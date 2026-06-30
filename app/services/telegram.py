import logging
import asyncio
import re
import tempfile
import os
import time
import socks
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import (
    ImportContactsRequest,
    GetContactsRequest,
    ResolveUsernameRequest,
)
from telethon.tl.types import InputPhoneContact, InputPeerUser
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    UserNotMutualContactError,
    AuthKeyError,
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    AuthKeyPermEmptyError,
    UserDeactivatedBanError,
)
from sqlalchemy import select, text

from app.config import get_settings
from app.services.encryption import decrypt_session
from app.database import AsyncSessionLocal
from app.utils.phone import is_username_key, username_from_key

logger = logging.getLogger(__name__)

# Auth errors that mean the session is dead and needs re-authorization
AUTH_ERRORS = (AuthKeyError, AuthKeyUnregisteredError, AuthKeyDuplicatedError, AuthKeyPermEmptyError)


def is_frozen_error(exc: Exception) -> bool:
    """True if the RPC error signals an account freeze (FROZEN_* family).

    Telegram only enforces a freeze on the WRITE path (sending, joining), raising
    an RPC error whose name starts with ``FROZEN_`` (e.g. FROZEN_METHOD_INVALID on
    send, FROZEN_PARTICIPANT_MISSING in the update loop — Telethon #4610). Telethon
    surfaces these unknown RPC errors generically, so we match on the string.
    """
    return "FROZEN" in str(exc).upper()


# SpamBot phrases its release time as e.g. "released on 20 Jun 2026, 11:49 UTC"
# (also "limited until 20 Jun 2026, 11:49 UTC"). English only — RU/other locales
# fall back to a fixed recheck interval at the call site.
_SPAMBOT_DATE_RE = re.compile(
    r"(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4}),?\s+(\d{1,2}):(\d{2})\s*UTC"
)


def parse_spambot_limit_until(text: str) -> Optional[datetime]:
    """Extract the absolute release time SpamBot quotes, as an aware UTC datetime.

    Returns None if no English-format date is found (caller then uses a fixed
    recheck interval instead).
    """
    m = _SPAMBOT_DATE_RE.search(text or "")
    if not m:
        return None
    day, mon, year, hh, mm = m.groups()
    try:
        dt = datetime.strptime(f"{day} {mon} {year} {hh}:{mm}", "%d %b %Y %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


# Phrase tables for classifying a @SpamBot reply body. Single source of truth shared
# by check_spambot (the solicited /start poll) AND the listener's unsolicited-SpamBot
# safety net, so both classify identically. A "free"/"unknown" body must NOT be
# treated as a restriction (the 2026-06-29 false-positive: a clean "Good news, no
# limits … free as a bird!" reply was flagged spam_limited for 6h — see
# .planning/debug/checker-false-spam-limited.md).
_SPAMBOT_FREE_PHRASES = ("good news", "no limits", "нет ограничений", "всё хорошо", "free as a bird")
_SPAMBOT_LIMITED_PHRASES = ("limited", "restrict", "ограничен")
_SPAMBOT_SUSPENDED_PHRASES = (
    "suspended", "blocked", "banned",
    "заблокирован", "приостановлен", "забанен",
)


def classify_spambot_text(text: str) -> str:
    """Classify a @SpamBot reply body → 'free' | 'limited' | 'suspended' | 'unknown'.

    'free' is checked FIRST: a "good news, no limits" reply must win even though it
    can incidentally contain a restriction keyword in boilerplate. 'unknown' (no
    recognised phrase) is NOT a restriction — callers must only act restrictively on
    'limited'/'suspended'.
    """
    text_lower = (text or "").lower()
    if any(p in text_lower for p in _SPAMBOT_FREE_PHRASES):
        return "free"
    if any(p in text_lower for p in _SPAMBOT_LIMITED_PHRASES):
        return "limited"
    if any(p in text_lower for p in _SPAMBOT_SUSPENDED_PHRASES):
        return "suspended"
    return "unknown"


class SessionAuthError(Exception):
    """Raised when Telegram session is invalid and needs re-authorization."""
    def __init__(self, slug: str, auth_status: str, detail: str):
        self.slug = slug
        self.auth_status = auth_status
        self.detail = detail
        super().__init__(f"[{slug}] {auth_status}: {detail}")


async def _set_auth_status(slug: str, auth_status: str):
    """Update sender auth_status in DB."""
    from app.models import Sender
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Sender).where(Sender.slug == slug))
        sender = result.scalar_one_or_none()
        if sender:
            sender.auth_status = auth_status
            await db.commit()
    logger.warning(f"auth_status for {slug} -> {auth_status}")

settings = get_settings()

# Device fingerprint mimicking Telegram Desktop.
# lang_pack is the key signal Telegram uses to identify official vs third-party clients.
# Sessions with empty lang_pack (Telethon default) are terminated when mobile logs out.
# Telethon doesn't expose lang_pack as a constructor parameter — we patch _init_request
# manually before connect() in make_telegram_client().
_CLIENT_FINGERPRINT = {
    "device_model": "Desktop",
    "system_version": "Windows 10",
    "app_version": "5.3.1",
    "lang_code": "ru",
    "system_lang_code": "ru-RU",
}

# Keep alias for imports in listener.py / checker.py / onboarding.py
DESKTOP_CLIENT_KWARGS = _CLIENT_FINGERPRINT

_PROXY_TYPE_MAP = {
    "socks5": socks.SOCKS5,
    "socks4": socks.SOCKS4,
    "http": socks.HTTP,
}


def build_proxy_tuple(proxy: dict | None) -> tuple | None:
    """Convert proxy config dict to Telethon-compatible proxy tuple.

    Returns None if proxy is None (client connects directly).
    Format: (type, host, port) or (type, host, port, True, username, password)
    """
    if not proxy:
        return None
    proxy_type = _PROXY_TYPE_MAP.get(proxy["type"].lower())
    if not proxy_type:
        logger.warning(f"Unknown proxy type: {proxy['type']}, connecting without proxy")
        return None
    host = proxy["host"]
    port = proxy["port"]
    username = proxy.get("username")
    if username:
        return (proxy_type, host, port, True, username, proxy.get("password", ""))
    return (proxy_type, host, port)


# ── Visual feedback helpers for AI reply flow ───────────────────────────────
# Both are best-effort (never raise into caller) so that AI generation /
# send_message paths remain unaffected if Telegram refuses the side-effect
# (peer blocked, FloodWait on the action endpoint, etc).


async def safe_read_ack(client, peer, max_id):
    """Mark inbound up to max_id as read on the given peer. Never raises."""
    if not (client and peer and max_id):
        return
    try:
        await client.send_read_acknowledge(peer, max_id=max_id)
    except Exception as e:
        logger.debug(f"safe_read_ack failed for peer={peer}: {e}")


@asynccontextmanager
async def safe_typing(client, peer):
    """Show "typing..." on the peer for the duration of the context.

    Telethon auto-renews the indicator every ~5s (Telegram clears stale state
    after 6s). Indicator clears automatically on context exit, including the
    exception path. Always yields exactly once — caller can use it even when
    client/peer is None (no-op).
    """
    cm = None
    if client and peer:
        try:
            cm = client.action(peer, 'typing')
            await cm.__aenter__()
        except Exception as e:
            logger.debug(f"safe_typing start failed for peer={peer}: {e}")
            cm = None
    try:
        yield
    finally:
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"safe_typing exit failed for peer={peer}: {e}")


def make_telegram_client(
    session: StringSession,
    proxy: dict | None = None,
    flood_sleep_threshold: int = 60,
    client_class: type = TelegramClient,
) -> TelegramClient:
    """Create a TelegramClient with official-client fingerprint.

    Patches _init_request.lang_pack before connect() so Telegram identifies
    the session as a known platform client (tdesktop). Without this patch,
    Telethon sends an empty lang_pack which Telegram uses to mark the session
    as third-party and terminates it when the user logs out from mobile.

    Pass client_class=ResilientTelegramClient for the listener subprocess.
    Must be called before client.connect().
    """
    client = client_class(
        session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        flood_sleep_threshold=flood_sleep_threshold,
        proxy=build_proxy_tuple(proxy),
        **_CLIENT_FINGERPRINT,
    )
    client._init_request.lang_pack = "tdesktop"
    return client


class TelegramService:
    """Service for Telegram operations via Telethon.

    IMPORTANT: Clients are created per-operation and disconnected after use.
    Persistent connections would steal Telegram updates from the listener container.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        # Self-check registry: slug -> monotonic expiry. While a slug is marked,
        # the listener's antispam handler treats an incoming SpamBot reply as
        # *solicited* (we pinged @SpamBot ourselves) and skips the auto-cancel.
        # In-memory and per-process: effective only within the process that set
        # it. The reconcile sweep runs in the listener process (same process as
        # the antispam handler) → fully covered. The manual /spambot-check
        # endpoint runs in the api process → NOT covered (documented limitation).
        self._spambot_selfcheck: dict[str, float] = {}

    def mark_spambot_selfcheck(self, slug: str, ttl: float = 30.0) -> None:
        """Mark `slug` as performing a solicited SpamBot check for the next `ttl` seconds."""
        self._spambot_selfcheck[slug] = time.monotonic() + ttl

    def is_spambot_selfcheck(self, slug: str) -> bool:
        """True if `slug` has an unexpired solicited-SpamBot-check marker. Prunes expired entries."""
        now = time.monotonic()
        # Prune expired markers so the dict can't grow unbounded.
        for s in [s for s, exp in self._spambot_selfcheck.items() if exp <= now]:
            self._spambot_selfcheck.pop(s, None)
        return self._spambot_selfcheck.get(slug, 0.0) > now

    async def get_client(
        self,
        sender_slug: str,
        encrypted_session: str,
        proxy: dict | None = None
    ) -> TelegramClient:
        """Create a temporary Telegram client for a single operation.

        IMPORTANT: Caller MUST disconnect the client after use via disconnect_client(),
        otherwise the persistent connection will steal updates from the listener.

        Raises SessionAuthError if the session is dead (expired/revoked/banned).
        """
        if sender_slug not in self._locks:
            self._locks[sender_slug] = asyncio.Lock()

        async with self._locks[sender_slug]:
            session_string = decrypt_session(encrypted_session)
            client = make_telegram_client(
                StringSession(session_string),
                proxy=proxy,
            )

            try:
                await client.connect()
            except AUTH_ERRORS as e:
                await _set_auth_status(sender_slug, "session_expired")
                raise SessionAuthError(sender_slug, "session_expired", str(e))
            except UserDeactivatedBanError as e:
                await _set_auth_status(sender_slug, "banned")
                raise SessionAuthError(sender_slug, "banned", str(e))

            try:
                if not await client.is_user_authorized():
                    await client.disconnect()
                    await _set_auth_status(sender_slug, "session_expired")
                    raise SessionAuthError(sender_slug, "session_expired", "Session is not authorized")
            except SessionAuthError:
                raise
            except AUTH_ERRORS as e:
                await client.disconnect()
                await _set_auth_status(sender_slug, "session_expired")
                raise SessionAuthError(sender_slug, "session_expired", str(e))
            except UserDeactivatedBanError as e:
                await client.disconnect()
                await _set_auth_status(sender_slug, "banned")
                raise SessionAuthError(sender_slug, "banned", str(e))

            return client

    async def disconnect_client(self, client: TelegramClient):
        """Disconnect client after operation to free the update stream for the listener."""
        try:
            if client and client.is_connected():
                await client.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting client: {e}")

    async def check_spambot(self, client: TelegramClient, selfcheck_key: str | None = None) -> dict:
        """Send /start to @SpamBot and parse the response.

        Args:
            selfcheck_key: if given (the sender slug), mark a solicited-self-check
                window before sending /start so the listener's antispam handler
                skips the auto-cancel when SpamBot's reply arrives. Only effective
                within this process (see TelegramService.is_spambot_selfcheck).

        Returns dict with:
            status: 'free' | 'limited' | 'suspended' | 'unknown'
            raw_text: full SpamBot response
            limit_until: optional date string if limited
        """
        # Mark BEFORE sending so the marker is live by the time the reply hits the
        # listener's update stream. Intentionally not cleared in finally — letting
        # it lapse via TTL avoids a race with the asynchronously-delivered reply.
        if selfcheck_key:
            self.mark_spambot_selfcheck(selfcheck_key)
        try:
            await client.send_message("SpamBot", "/start")
            await asyncio.sleep(2)

            messages = await client.get_messages("SpamBot", limit=1)
            if not messages:
                return {"status": "unknown", "raw_text": "No response from SpamBot"}

            text = messages[0].text or ""
            result = {"raw_text": text}

            status = classify_spambot_text(text)
            result["status"] = status
            if status == "limited":
                # Absolute release time SpamBot quotes (English only); None → caller
                # falls back to a fixed recheck interval.
                limit_until = parse_spambot_limit_until(text)
                if limit_until:
                    result["limit_until"] = limit_until.isoformat()

            return result

        except FloodWaitError as e:
            return {"status": "unknown", "raw_text": f"FloodWait: retry after {e.seconds}s"}
        except Exception as e:
            logger.error(f"SpamBot check failed: {e}")
            return {"status": "unknown", "raw_text": f"Error: {str(e)}"}
    
    async def _get_cached_contact(self, workspace_id: str, sender_id: str, phone: str) -> Optional[dict]:
        """Look up contact in DB cache (contacts_cache + conversations).

        Workspace-isolated by D-03 (Phase 1 multi-tenant): all SELECT'ы фильтруют
        по workspace_id чтобы исключить cross-tenant data leak через resolve cache.
        """
        async with AsyncSessionLocal() as db:
            # 1. Check contacts_cache
            row = (await db.execute(
                text("""
                    SELECT telegram_id, first_name, last_name, username, is_registered, access_hash
                    FROM contacts_cache
                    WHERE workspace_id = :workspace_id
                      AND sender_id = :sender_id
                      AND phone = :phone
                      AND updated_at > NOW() - INTERVAL '7 days'
                """),
                {"workspace_id": workspace_id, "sender_id": sender_id, "phone": phone}
            )).fetchone()

            if row and row[0] and row[5] is not None:  # telegram_id + access_hash exist
                return {
                    "is_registered": True,
                    "telegram_id": row[0],
                    "access_hash": row[5],
                    "first_name": row[1],
                    "last_name": row[2],
                    "username": row[3],
                    "from_cache": True
                }
            if row and row[4] is False:  # known unregistered
                return {"is_registered": False, "from_cache": True}

            # 2. Check conversations table as fallback
            row = (await db.execute(
                text("""
                    SELECT contact_telegram_id, contact_name
                    FROM conversations
                    WHERE workspace_id = :workspace_id
                      AND sender_id = :sender_id
                      AND contact_phone = :phone
                      AND contact_telegram_id IS NOT NULL
                    LIMIT 1
                """),
                {"workspace_id": workspace_id, "sender_id": sender_id, "phone": phone}
            )).fetchone()

            # conversations table has no access_hash — don't return from cache,
            # let the live resolve ladder (ResolveUsername / ImportContacts) fetch a
            # valid per-sender access_hash.
            pass

            # 3. Cross-sender lookup (внутри того же workspace) — ONLY для
            # is_registered=false. Если другой чекер этого workspace'а уже
            # подтвердил что номер не зарегистрирован, пропускаем ResolvePhone.
            # Для зарегистрированных используем не можем — нужен per-sender access_hash.
            cross_row = (await db.execute(
                text("""
                    SELECT is_registered FROM contacts_cache
                    WHERE workspace_id = :workspace_id
                      AND phone = :phone
                      AND is_registered = false
                      AND updated_at > NOW() - INTERVAL '7 days'
                    LIMIT 1
                """),
                {"workspace_id": workspace_id, "phone": phone}
            )).fetchone()

            if cross_row:
                logger.debug(f"Contact {phone} found unregistered in cross-sender cache — skipping ResolvePhone")
                return {"is_registered": False, "from_cache": True}

        return None

    async def _load_contact_verdict(self, workspace_id: str, phone: str) -> dict:
        """Load the checker verdict + captured @username for a phone (tier-2/tier-3 inputs).

        Reads the existing Phase-14/17 columns on `contacts`:
          - `tg_status` — the checker verdict ('registered' | 'not_registered' | 'pending' | …),
            the gate for the tier-3 ImportContacts (only 'registered' triggers an import, D-03/D-11).
          - `tg_username_resolved` — the public, transferable @handle the checker captured
            (17-02), the input for the sender's tier-2 ResolveUsername (D-07).

        A phone may map to multiple contacts; the ORDER BY prefers a `registered` row
        (conservative — favours reachability) then the most recently updated one. Returns
        `{"tg_status": None, "captured_username": None}` when there is no contacts row
        (e.g. a '@handle' identity-key contact or an ad-hoc send) — callers treat a None
        verdict permissively per the existing send-path semantics, but ONLY 'registered'
        ever triggers an import.
        """
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                text("""
                    SELECT tg_status, tg_username_resolved
                      FROM contacts
                     WHERE workspace_id = :workspace_id AND phone = :phone
                     ORDER BY (tg_status = 'registered') DESC, updated_at DESC
                     LIMIT 1
                """),
                {"workspace_id": workspace_id, "phone": phone}
            )).fetchone()

        return {
            "tg_status": row[0] if row else None,
            "captured_username": row[1] if row else None,
        }

    async def _save_contact_cache(self, workspace_id: str, sender_id: str, phone: str, contact_info: dict):
        """Save contact lookup result to DB cache."""
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("""
                        INSERT INTO contacts_cache
                            (workspace_id, sender_id, phone, telegram_id, access_hash, first_name, last_name, username, is_registered)
                        VALUES (:workspace_id, :sender_id, :phone, :tg_id, :access_hash, :first_name, :last_name, :username, :is_reg)
                        ON CONFLICT (sender_id, phone) DO UPDATE SET
                            telegram_id = EXCLUDED.telegram_id,
                            access_hash = EXCLUDED.access_hash,
                            first_name = EXCLUDED.first_name,
                            last_name = EXCLUDED.last_name,
                            username = EXCLUDED.username,
                            is_registered = EXCLUDED.is_registered,
                            updated_at = NOW()
                    """),
                    {
                        "workspace_id": workspace_id,
                        "sender_id": sender_id,
                        "phone": phone,
                        "tg_id": contact_info.get("telegram_id"),
                        "access_hash": contact_info.get("access_hash"),
                        "first_name": contact_info.get("first_name"),
                        "last_name": contact_info.get("last_name"),
                        "username": contact_info.get("username"),
                        "is_reg": contact_info.get("is_registered", False)
                    }
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to save contact cache: {e}")

    async def resolve_contact(
        self,
        client: TelegramClient,
        workspace_id: str,
        sender_id: str,
        phone: str,
        recipient_name: Optional[str] = None
    ) -> dict:
        """Resolve phone to telegram_id using cache first, then ImportContacts as fallback.

        This minimizes ImportContactsRequest calls to avoid Telegram spam detection.
        Workspace-isolated: cache lookups + writes scoped to the caller's workspace.
        """
        # 1. Try cache
        cached = await self._get_cached_contact(workspace_id, sender_id, phone)
        if cached:
            logger.debug(f"Contact {phone} resolved from cache: tg_id={cached.get('telegram_id')}")
            return cached

        # 2a. Username identity key ('@handle') — resolve via ResolveUsername.
        # No phone to import; cache is keyed on the same '@handle' string.
        if is_username_key(phone):
            res = await self._resolve_username(client, workspace_id, sender_id, phone)
            # A '@handle' contact has no phone to import; a stale signal here is
            # genuinely unregistered (the handle was the only identity we had).
            if res.get("stale_username"):
                return {"is_registered": False}
            return res

        # 2b. PHONE key — the 3-tier sender resolve ladder (D-01/D-02). The sender's
        # OWN ResolvePhone is GONE (D-01): it gave the false negatives in the
        # Barter-ВЭД incident (throttle/privacy on a per-recipient lookup). A
        # checker's resolve (and its access_hash) can never be reused on a sender
        # (per-account, Telethon — verified), so the sender does its own lookup via
        # the transferable top tier (captured @username) and an import fallback.
        verdict = await self._load_contact_verdict(workspace_id, phone)

        # Tier-2: ResolveUsername on the captured @username (D-07). The cheapest,
        # safest transferable resolve — a public handle resolves identically on any
        # account. A stale handle (Task 3) falls through to the import tier (D-09),
        # it is NEVER finalized as not_registered.
        captured = verdict.get("captured_username")
        if captured:
            res = await self._resolve_username(
                client, workspace_id, sender_id, "@" + captured.lstrip("@")
            )
            if res.get("is_registered"):
                # Cache the access_hash under the PHONE key so follow-up sends are
                # phone-cache hits (the @handle key is also cached by _resolve_username).
                await self._save_contact_cache(workspace_id, sender_id, phone, res)
                return res
            # res.get("stale_username") → fall through to tier-3 import (D-09).
            # (any FloodWait/frozen would have propagated out of _resolve_username)

        # Tier-3: ImportContacts, GATED on the checker verdict 'registered'
        # (D-03/D-11). ImportContacts surfaces registered-but-privacy-hidden numbers
        # that ResolvePhone misses; we only spend a (risky) import when the checker
        # has already confirmed the number is registered. A 'not_registered' (or any
        # non-'registered') verdict → skip the import entirely (D-03).
        if verdict.get("tg_status") == "registered":
            logger.info(f"Contact {phone}: tier-3 ImportContacts (registered, no live username)")
            try:
                result = await client(ImportContactsRequest(
                    contacts=[InputPhoneContact(
                        client_id=0,
                        phone=phone,
                        first_name=recipient_name or "",
                        last_name="",
                    )]
                ))
                # D-04: the SENDER KEEPS the imported contact (hot entity-cache for
                # follow-ups) — NO DeleteContactsRequest here (unlike the checker).
                if result and result.users:
                    user = result.users[0]
                    contact_info = {
                        "is_registered": True,
                        "telegram_id": user.id,
                        "access_hash": user.access_hash,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "username": user.username,
                    }
                    await self._save_contact_cache(workspace_id, sender_id, phone, contact_info)
                    return contact_info
                # Import returned no users — do NOT cache/finalize False here; leave
                # finalization to the checker (D-09 semantics). The number was tagged
                # registered, so an empty import is more likely privacy/transient.
                return {"is_registered": False}
            except PhoneNumberInvalidError:
                return {"is_registered": False, "error": "Invalid phone number"}
            # FloodWait / frozen / network errors propagate (do NOT mask) so the
            # queue worker records the real error and retries.

        # Verdict is 'not_registered' (or 'pending'/None with no captured username):
        # skip the import (D-03) and report not-registered WITHOUT caching False —
        # finalization is the checker's job (D-09 semantics).
        return {"is_registered": False}

    async def _resolve_username(
        self,
        client: TelegramClient,
        workspace_id: str,
        sender_id: str,
        key: str,
    ) -> dict:
        """Resolve a '@username' identity key to telegram_id + access_hash.

        Mirrors the ResolvePhone path but uses ResolveUsernameRequest. The result
        is cached in contacts_cache under the '@username' key (same column as
        phones), so subsequent sends are cache hits.
        """
        uname = username_from_key(key)
        logger.info(f"Contact {key} not in cache, calling ResolveUsernameRequest")
        try:
            result = await client(ResolveUsernameRequest(username=uname))

            if result and result.users:
                user = result.users[0]
                contact_info = {
                    "is_registered": True,
                    "telegram_id": user.id,
                    "access_hash": user.access_hash,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "username": user.username,
                }
            else:
                contact_info = {"is_registered": False}

            await self._save_contact_cache(workspace_id, sender_id, key, contact_info)
            return contact_info

        except Exception as e:
            # USERNAME_NOT_OCCUPIED / USERNAME_INVALID → not registered, cache it.
            err = str(e)
            low = err.lower()
            if "username_not_occupied" in low or "username_invalid" in low:
                contact_info = {"is_registered": False}
                await self._save_contact_cache(workspace_id, sender_id, key, contact_info)
                return {"is_registered": False}
            # Anything else (FloodWait, frozen account, network) must propagate so
            # the queue worker records the real error and retries.
            logger.error(f"Error checking contact via ResolveUsername: {e}")
            raise

    async def check_contact(
        self,
        client: TelegramClient,
        phone: str
    ) -> dict:
        """Check if phone number is registered in Telegram (legacy, no cache)."""
        try:
            result = await client(ImportContactsRequest(
                contacts=[InputPhoneContact(
                    client_id=0,
                    phone=phone,
                    first_name="Check",
                    last_name=""
                )]
            ))

            if result.users:
                user = result.users[0]
                return {
                    "is_registered": True,
                    "telegram_id": user.id,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "username": user.username
                }
            else:
                return {"is_registered": False}

        except PhoneNumberInvalidError:
            return {"is_registered": False, "error": "Invalid phone number"}
        except Exception as e:
            logger.error(f"Error checking contact: {e}")
            return {"is_registered": False, "error": str(e)}
    
    async def send_message(
        self,
        client: TelegramClient,
        phone: str,
        recipient_name: Optional[str],
        message: str,
        as_draft: bool = False,
        sender_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> dict:
        """Send message to phone number. Client is disconnected after the operation."""
        try:
            # Resolve contact (cache first, ImportContacts only for new contacts)
            if sender_id and workspace_id:
                contact_info = await self.resolve_contact(client, workspace_id, sender_id, phone, recipient_name)
            else:
                contact_info = await self.check_contact(client, phone)

            if not contact_info.get("is_registered"):
                target = phone if is_username_key(phone) else f"Номер {phone}"
                return {
                    "success": False,
                    "error": {
                        "code": "RECIPIENT_NOT_IN_TELEGRAM",
                        "message": f"{target} не зарегистрирован в Telegram"
                    }
                }

            telegram_id = contact_info["telegram_id"]
            access_hash = contact_info.get("access_hash")

            # Use InputPeerUser with access_hash to avoid "entity not found" errors.
            # The resolve ladder (ResolveUsername / ImportContacts) returns the user
            # object with access_hash but may not warm the session entity cache — so a
            # bare telegram_id could fail on send.
            if access_hash is not None:
                peer = InputPeerUser(user_id=telegram_id, access_hash=access_hash)
            else:
                peer = telegram_id  # fallback for cached contacts without access_hash

            if as_draft:
                return {
                    "success": True,
                    "action": "draft",
                    "recipient": {
                        "telegram_id": telegram_id,
                        "name": contact_info.get("first_name"),
                        "username": contact_info.get("username"),
                        "was_added_to_contacts": False
                    }
                }

            # Send message
            sent = await client.send_message(peer, message)

            return {
                "success": True,
                "action": "sent",
                "message_id": str(sent.id),
                "recipient": {
                    "telegram_id": telegram_id,
                    "name": contact_info.get("first_name"),
                    "username": contact_info.get("username"),
                    "was_added_to_contacts": not contact_info.get("from_cache", False)
                }
            }

        except FloodWaitError as e:
            return {
                "success": False,
                "error": {
                    "code": "FLOOD_WAIT",
                    "message": f"Rate limited. Retry after {e.seconds} seconds",
                    "retry_after": e.seconds
                }
            }
        except PeerFloodError:
            return {
                "success": False,
                "error": {
                    "code": "PEER_FLOOD",
                    "message": "Спам-ограничение аккаунта. Требуется пауза и ручная проверка."
                }
            }
        except UserNotMutualContactError:
            return {
                "success": False,
                "error": {
                    "code": "PRIVACY_RESTRICTED",
                    "message": "Пользователь ограничил приватность сообщений"
                }
            }
        except Exception as e:
            if is_frozen_error(e):
                logger.critical(f"Account frozen while sending message: {e}")
                return {
                    "success": False,
                    "error": {
                        "code": "ACCOUNT_FROZEN",
                        "message": "Аккаунт заморожен Telegram (FROZEN_*). Требуется аппеляция."
                    }
                }
            logger.error(f"Error sending message: {e}")
            return {
                "success": False,
                "error": {
                    "code": "SEND_FAILED",
                    "message": str(e)
                }
            }
        finally:
            await self.disconnect_client(client)

    async def send_file(
        self,
        client: TelegramClient,
        phone: str,
        recipient_name: Optional[str],
        file_url: str,
        file_name: Optional[str] = None,
        caption: Optional[str] = None,
        sender_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> dict:
        """Download file from URL and send to recipient. Client is disconnected after the operation."""
        tmp_path = None
        try:
            # Resolve contact (cache first, ImportContacts only for new contacts)
            if sender_id and workspace_id:
                contact_info = await self.resolve_contact(client, workspace_id, sender_id, phone, recipient_name)
            else:
                contact_info = await self.check_contact(client, phone)

            if not contact_info.get("is_registered"):
                target = phone if is_username_key(phone) else f"Номер {phone}"
                return {
                    "success": False,
                    "error": {
                        "code": "RECIPIENT_NOT_IN_TELEGRAM",
                        "message": f"{target} не зарегистрирован в Telegram"
                    }
                }

            telegram_id = contact_info["telegram_id"]
            access_hash = contact_info.get("access_hash")

            if access_hash is not None:
                peer = InputPeerUser(user_id=telegram_id, access_hash=access_hash)
            else:
                peer = telegram_id  # fallback for cached contacts without access_hash

            # Download file from URL
            if not file_name:
                parsed = urlparse(file_url)
                file_name = os.path.basename(parsed.path) or "file"

            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
                resp = await http.get(file_url)
                resp.raise_for_status()
                file_data = resp.content

            # Save to temp file
            suffix = os.path.splitext(file_name)[1] or ""
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(file_data)
                tmp_path = f.name

            # Telegram caption limit for media is 1024 chars.
            # If caption exceeds the limit, send file without caption and follow up
            # with a separate text message so the full text is delivered.
            CAPTION_LIMIT = 1024
            file_caption = None
            overflow_text = None
            if caption:
                if len(caption) <= CAPTION_LIMIT:
                    file_caption = caption
                else:
                    overflow_text = caption

            # Send file via Telethon
            sent = await client.send_file(
                peer,
                tmp_path,
                caption=file_caption,
                file_name=file_name,
                force_document=True
            )

            # Send overflow caption as a follow-up text message
            if overflow_text:
                try:
                    await client.send_message(peer, overflow_text)
                    logger.info(f"Caption overflow sent as separate message to {phone}")
                except Exception as e:
                    logger.warning(f"File sent but follow-up text message failed for {phone}: {e}")

            return {
                "success": True,
                "action": "file_sent",
                "message_id": str(sent.id),
                "recipient": {
                    "telegram_id": telegram_id,
                    "name": contact_info.get("first_name"),
                    "username": contact_info.get("username"),
                    "was_added_to_contacts": not contact_info.get("from_cache", False)
                }
            }

        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "error": {
                    "code": "FILE_DOWNLOAD_FAILED",
                    "message": f"Не удалось скачать файл: HTTP {e.response.status_code}"
                }
            }
        except httpx.RequestError as e:
            return {
                "success": False,
                "error": {
                    "code": "FILE_DOWNLOAD_FAILED",
                    "message": f"Ошибка скачивания файла: {e}"
                }
            }
        except FloodWaitError as e:
            return {
                "success": False,
                "error": {
                    "code": "FLOOD_WAIT",
                    "message": f"Rate limited. Retry after {e.seconds} seconds",
                    "retry_after": e.seconds
                }
            }
        except PeerFloodError:
            return {
                "success": False,
                "error": {
                    "code": "PEER_FLOOD",
                    "message": "Спам-ограничение аккаунта. Требуется пауза и ручная проверка."
                }
            }
        except UserNotMutualContactError:
            return {
                "success": False,
                "error": {
                    "code": "PRIVACY_RESTRICTED",
                    "message": "Пользователь ограничил приватность сообщений"
                }
            }
        except Exception as e:
            if is_frozen_error(e):
                logger.critical(f"Account frozen while sending file: {e}")
                return {
                    "success": False,
                    "error": {
                        "code": "ACCOUNT_FROZEN",
                        "message": "Аккаунт заморожен Telegram (FROZEN_*). Требуется аппеляция."
                    }
                }
            logger.error(f"Error sending file: {e}")
            return {
                "success": False,
                "error": {
                    "code": "SEND_FAILED",
                    "message": str(e)
                }
            }
        finally:
            await self.disconnect_client(client)
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def send_message_by_telegram_id(
        self,
        sender_slug: str,
        encrypted_session: str,
        telegram_id: int,
        message: str,
        proxy: dict | None = None
    ) -> dict:
        """Send message directly by Telegram ID (for existing conversations).

        Resolution strategy (2026-05-26 fix for /send 500 → "Could not find
        the input entity"):
          1. Try `get_input_entity(telegram_id)` — Telethon checks its session-
             local entity cache (the SQLite file holds access_hash for every
             peer the client has seen).
          2. If miss, refresh the cache by enumerating recent dialogs — for any
             user we have a conversation with, this populates access_hash.
          3. Retry `get_input_entity`. If it still fails, the contact has never
             been talked to from this sender → caller must use a different send
             path (cold-start outreach, which goes through the queue with the
             pre-resolved peer).
        """
        client = None
        try:
            client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)

            try:
                peer = await client.get_input_entity(telegram_id)
            except ValueError:
                # Cold cache — load recent dialogs so Telethon learns
                # access_hash for users in our chat list.
                logger.info(
                    f"send_by_id: entity {telegram_id} not in cache, "
                    f"refreshing dialogs for {sender_slug}"
                )
                await client.get_dialogs(limit=200)
                peer = await client.get_input_entity(telegram_id)

            sent = await client.send_message(peer, message)

            return {
                "success": True,
                "telegram_message_id": sent.id
            }

        except FloodWaitError as e:
            return {
                "success": False,
                "error": f"Rate limited. Retry after {e.seconds} seconds"
            }
        except Exception as e:
            logger.error(f"Error sending message by telegram_id: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            if client:
                await self.disconnect_client(client)


# Global instance
telegram_service = TelegramService()
