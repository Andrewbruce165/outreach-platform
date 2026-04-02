"""
CheckerService — batch phone number verification via dedicated checker accounts.

Checker accounts are "disposable" Telegram accounts whose sole job is to call
ResolvePhoneRequest at scale and cache the is_registered result.  Main sender
accounts then consult the cache and skip ResolvePhoneRequest entirely for
numbers known to be unregistered — protecting them from Telegram's spam
detection.

IMPORTANT: access_hash is account-specific in Telegram.  Checker accounts
cache only is_registered + telegram_id.  Main senders still need their own
ResolvePhoneRequest to obtain their own access_hash (but only for confirmed-
registered numbers, which is the whole point).
"""
import asyncio
import logging
import random
import time
from typing import Optional

from sqlalchemy import text
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, PhoneNumberInvalidError, PhoneNotOccupiedError

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services.encryption import decrypt_session
from app.services.telegram import make_telegram_client

logger = logging.getLogger(__name__)
settings = get_settings()


class CheckerService:
    """Service for bulk phone-number verification using checker Telegram accounts."""

    def __init__(self):
        # One Lock per checker_slug — prevents concurrent API calls on the same account.
        # Single uvicorn worker → asyncio.Lock is sufficient (no distributed lock needed).
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, checker_slug: str) -> asyncio.Lock:
        if checker_slug not in self._locks:
            self._locks[checker_slug] = asyncio.Lock()
        return self._locks[checker_slug]

    async def _get_client(self, encrypted_session: str, proxy: dict | None = None) -> TelegramClient:
        """Create a connected Telethon client for the checker account."""
        session_string = decrypt_session(encrypted_session)
        client = make_telegram_client(
            StringSession(session_string),
            proxy=proxy,
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise Exception("Checker session is not authorized. Re-authenticate.")
        return client

    async def _lookup_cache(self, phone: str) -> Optional[dict]:
        """Check contacts_cache for any existing record for this phone (cross-sender)."""
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                text("""
                    SELECT is_registered, telegram_id
                    FROM contacts_cache
                    WHERE phone = :phone
                      AND updated_at > NOW() - INTERVAL '7 days'
                    ORDER BY updated_at DESC
                    LIMIT 1
                """),
                {"phone": phone},
            )).fetchone()

        if row is None:
            return None
        return {
            "is_registered": row[0],
            "telegram_id": row[1],
            "from_cache": True,
        }

    async def _save_cache(self, checker_id: str, phone: str, is_registered: bool, telegram_id: Optional[int]):
        """Persist check result to contacts_cache under the checker's sender_id."""
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("""
                        INSERT INTO contacts_cache
                            (sender_id, phone, telegram_id, is_registered)
                        VALUES (:sender_id, :phone, :telegram_id, :is_registered)
                        ON CONFLICT (sender_id, phone) DO UPDATE SET
                            telegram_id = EXCLUDED.telegram_id,
                            is_registered = EXCLUDED.is_registered,
                            updated_at = NOW()
                    """),
                    {
                        "sender_id": checker_id,
                        "phone": phone,
                        "telegram_id": telegram_id,
                        "is_registered": is_registered,
                    },
                )
                await db.commit()
        except Exception as exc:
            logger.warning(f"CheckerService: failed to save cache for {phone}: {exc}")

    async def check_phones(
        self,
        checker_id: str,
        checker_slug: str,
        encrypted_session: str,
        phones: list[str],
        proxy: dict | None = None,
    ) -> dict:
        """
        Check each phone via Telegram and cache the result.

        Returns a summary dict:
        {
            "checked": int,
            "registered": int,
            "not_registered": int,
            "flood_wait_hit": bool,
            "results": [{"phone": str, "is_registered": bool, "telegram_id": int|None, "from_cache": bool}, ...]
        }
        """
        async with self._get_lock(checker_slug):
            return await self._check_phones_locked(checker_id, checker_slug, encrypted_session, phones, proxy)

    async def _check_phones_locked(
        self,
        checker_id: str,
        checker_slug: str,
        encrypted_session: str,
        phones: list[str],
        proxy: dict | None = None,
    ) -> dict:
        results: list[dict] = []
        flood_wait_hit = False

        start_ts = time.monotonic()
        logger.info(f"[checker:{checker_slug}] Starting batch check for {len(phones)} phones")

        client: Optional[TelegramClient] = None
        try:
            client = await self._get_client(encrypted_session, proxy=proxy)

            for i, phone in enumerate(phones):
                # 1. Try cache first (cross-sender — any cached record for this phone)
                cached = await self._lookup_cache(phone)
                if cached is not None:
                    results.append({
                        "phone": phone,
                        "is_registered": cached["is_registered"],
                        "telegram_id": cached.get("telegram_id"),
                        "from_cache": True,
                    })
                    logger.debug(f"[checker:{checker_slug}] {phone} → from cache (registered={cached['is_registered']})")
                    continue

                # 2. Cache miss — call Telegram
                try:
                    from telethon.tl.functions.contacts import ResolvePhoneRequest
                    result = await client(ResolvePhoneRequest(phone=phone))

                    if result and result.users:
                        user = result.users[0]
                        is_registered = True
                        telegram_id = user.id
                    else:
                        is_registered = False
                        telegram_id = None

                except FloodWaitError:
                    raise  # propagate to outer except FloodWaitError handler
                except PhoneNumberInvalidError:
                    is_registered = False
                    telegram_id = None
                except PhoneNotOccupiedError:
                    is_registered = False
                    telegram_id = None
                except Exception as exc:
                    err = str(exc)
                    if "PHONE_NOT_OCCUPIED" in err or "phone_not_occupied" in err.lower():
                        is_registered = False
                        telegram_id = None
                    else:
                        # Unexpected error (frozen account, network, etc.) — do NOT mask as
                        # "not registered"; re-raise so the batch stops and logs the real cause
                        logger.error(f"[checker:{checker_slug}] Unexpected ResolvePhone error for {phone}: {exc}", exc_info=True)
                        raise

                # 3. Save to cache
                await self._save_cache(checker_id, phone, is_registered, telegram_id)

                results.append({
                    "phone": phone,
                    "is_registered": is_registered,
                    "telegram_id": telegram_id,
                    "from_cache": False,
                })
                logger.debug(f"[checker:{checker_slug}] {phone} → registered={is_registered}")

                # 4. Polite delay between Telegram requests (skip after last item)
                if i < len(phones) - 1:
                    delay = random.uniform(2.0, 3.5)
                    await asyncio.sleep(delay)

        except FloodWaitError as exc:
            flood_wait_hit = True
            wait_sec = exc.seconds
            logger.warning(
                f"[checker:{checker_slug}] FloodWait hit after {len(results)}/{len(phones)} phones "
                f"— sleeping {wait_sec}s then stopping batch"
            )
            await asyncio.sleep(wait_sec)
            # Partial result — remaining phones are not checked
        finally:
            if client:
                try:
                    if client.is_connected():
                        await client.disconnect()
                except Exception as exc:
                    logger.warning(f"[checker:{checker_slug}] Error disconnecting: {exc}")

        elapsed = time.monotonic() - start_ts
        registered_count = sum(1 for r in results if r["is_registered"])
        not_registered_count = len(results) - registered_count

        logger.info(
            f"[checker:{checker_slug}] Batch done in {elapsed:.1f}s: "
            f"checked={len(results)}, registered={registered_count}, "
            f"not_registered={not_registered_count}, flood_wait={flood_wait_hit}"
        )

        return {
            "checked": len(results),
            "registered": registered_count,
            "not_registered": not_registered_count,
            "flood_wait_hit": flood_wait_hit,
            "results": results,
        }


# Global singleton
checker_service = CheckerService()
