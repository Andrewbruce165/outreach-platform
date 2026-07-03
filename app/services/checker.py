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

CAVEAT — what is_registered=False actually means (privacy false-negatives):
    is_registered=False from ResolvePhoneRequest means "this number is NOT
    resolvable by phone by THIS (stranger) checker account" — it does NOT mean
    "no Telegram account exists for this number".  PhoneNotOccupiedError (and an
    empty ImportContacts result) also fires when the number's owner has set
    "Who can find me by my phone number" to Contacts / Nobody (a privacy
    setting).  In that case the account is registered but simply not discoverable
    by phone from an account that isn't in the owner's contacts — i.e. a false
    negative for a registered-but-private number.

    Proof this is privacy and not a broken checker (verified 2026-06-23):
    checker `sender-8428118140` threw PhoneNotOccupiedError on our OWN
    authorized senders' phone numbers (those accounts have restrictive
    find-by-phone privacy) while *simultaneously* having 83 numbers cached
    is_registered=True (most recent the prior day) — i.e. the checker was
    healthy, not broken.

    Consequence: the not_registered bucket contains an unknown share of false
    negatives (registered-but-private numbers).  This is operationally
    acceptable for cold phone-import outreach — you cannot DM a privacy-hidden
    number by phone anyway — but the field NAME `is_registered` is misleading
    and should not be read as "definitely no Telegram account".  Do not build
    analytics, dedup, or "dead number" logic on the false=="no account"
    assumption.

    The only API way to confirm a privacy-hidden account exists is via its
    @username (ResolveUsernameRequest) — see check_usernames in this file.
"""
import asyncio
import logging
import random
import time
from typing import Optional

from sqlalchemy import text
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneNotOccupiedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services.encryption import decrypt_session
from app.services.telegram import make_telegram_client

logger = logging.getLogger(__name__)
settings = get_settings()

# WR-05: Telethon auto-sleeps FloodWaits <= flood_sleep_threshold (60s); a RAISED
# FloodWait is longer than that. Cap the inline block so a multi-hour wait can't
# freeze the single-coroutine ContactCheckWorker — the checker is parked with a
# durable cooldown by the inline degrade path (_maybe_degrade_on_signal), which is
# where long waits belong. flood_wait_hit=True is still returned (partial batch).
_FLOOD_WAIT_INLINE_CAP = 60


async def resolve_phone_with_fallback(client: TelegramClient, phone: str) -> dict:
    """Resolve a single phone LIVE: ResolvePhone first, importContacts fallback.

    RESV-01/D-02. ``ResolvePhoneRequest`` is the primary resolve, but it returns
    nothing for a registered-but-private number (find-by-phone = Contacts/Nobody).
    When it comes back empty (or raises ``PhoneNotOccupiedError`` / ``PHONE_NOT_OCCUPIED``)
    we fall back to ``ImportContactsRequest`` — adding the number to the checker's
    address book forces Telegram to surface the user if one exists.

    CRITICAL (Pitfall 4 — this is how the original checker died): an imported
    contact MUST be removed from the address book immediately via
    ``DeleteContactsRequest``. Uncleaned imports leak the recipient's PII into the
    checker's contact list and shift its behavioural profile toward "mass contact
    importer", which accelerates the shadow-ban. Cleanup runs in a ``finally`` so a
    crash between import and delete still attempts removal.

    The import call's own failure never crashes the batch — it falls through to
    ``is_registered=False`` (the conservative, re-checkable verdict). No decrypted
    session string or full imported-contact PII is logged.

    Returns ``{"is_registered": bool, "telegram_id": int | None, "username": str | None}``.

    SRLD-01/D-06: the captured ``username`` is the public, *transferable* identity
    (unlike the per-account ``access_hash``, which can never be reused). When present
    it lets the sender do a cheap, safe tier-2 ``ResolveUsername`` instead of a
    phone-import. The key is ALWAYS present (``None`` when not registered / no handle)
    so the worker's ``res.get("username")`` never KeyErrors.
    """
    from telethon.tl.functions.contacts import (
        DeleteByPhonesRequest,
        DeleteContactsRequest,
        ImportContactsRequest,
        ResolvePhoneRequest,
    )
    from telethon.tl.types import InputPhoneContact

    # 1. Primary: ResolvePhoneRequest (live).
    resolve_empty = False
    try:
        result = await client(ResolvePhoneRequest(phone=phone))
        if result and result.users:
            user = result.users[0]
            return {
                "is_registered": True,
                "telegram_id": user.id,
                "username": getattr(user, "username", None),
            }
        resolve_empty = True
    except FloodWaitError:
        raise  # caller handles FloodWait — never mask it
    except PhoneNumberInvalidError:
        # IN-02: a syntactically invalid number is a HARD error (garbage input),
        # NOT a clean "not registered" verdict. Tag it so the batch producer skips
        # the cache write and the worker finalizes tg_status='error' (never
        # not_registered/high — which would silently swallow a bad-data row).
        return {"is_registered": False, "telegram_id": None, "username": None, "error": "invalid_phone"}
    except PhoneNotOccupiedError:
        resolve_empty = True
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        if "PHONE_NOT_OCCUPIED" in err or "phone_not_occupied" in err.lower():
            resolve_empty = True
        else:
            raise  # unexpected (frozen/network) — let the caller stop the batch

    if not resolve_empty:
        return {"is_registered": False, "telegram_id": None, "username": None}

    # 2. Fallback: importContacts — surfaces a private/registered user that
    #    ResolvePhone could not see. Its own failure is non-fatal (→ not registered).
    imported_user = None
    import_completed = False
    try:
        res = await client(ImportContactsRequest(contacts=[
            InputPhoneContact(client_id=0, phone=phone, first_name="Check", last_name="")
        ]))
        # WR-07: the import HAS completed (Telegram stored the phone as a saved
        # contact) the moment ImportContactsRequest returns — even when NO user
        # surfaced. Mark it BEFORE inspecting res.users so the finally cleans up
        # the saved phone in BOTH branches.
        import_completed = True
        if res and getattr(res, "users", None):
            imported_user = res.users[0]
    except FloodWaitError:
        raise
    except Exception as exc:  # noqa: BLE001 — import fallback must not crash the batch
        logger.warning("importContacts fallback failed for a phone: %s", exc)
        return {"is_registered": False, "telegram_id": None, "username": None}
    finally:
        # MANDATORY cleanup (D-02 / Pitfall 4) — runs in finally per the docstring contract.
        # WR-07: clean BOTH branches — DeleteContacts for a surfaced user, DeleteByPhones
        # for an empty import (Telegram still stored the phone as a saved contact even when
        # no user surfaced — the shadow-ban accelerator). Guard on import_completed so a
        # flood/failed import (nothing added) fires no extra contacts-API call.
        if import_completed:
            try:
                if imported_user is not None:
                    await client(DeleteContactsRequest(id=[imported_user]))
                else:
                    await client(DeleteByPhonesRequest(phones=[phone]))
            except Exception as exc:  # noqa: BLE001 — cleanup failure logged, not fatal
                logger.warning("import-fallback address-book cleanup failed for a phone: %s", exc)

    if imported_user is None:
        return {"is_registered": False, "telegram_id": None, "username": None}

    return {
        "is_registered": True,
        "telegram_id": imported_user.id,
        "username": getattr(imported_user, "username", None),
    }


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

    async def _flag_checker_auth(self, sender_id: str | None, auth_status: str) -> None:
        """WR-06: flag a dead checker's auth_status BY ID (not slug — WR-14 shows slug
        is not globally unique). No-op when sender_id is unknown (e.g. probe path)."""
        if not sender_id:
            return
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    text("UPDATE senders SET auth_status = :st WHERE id = :sid"),
                    {"st": auth_status, "sid": sender_id},
                )
        logger.warning("[checker] auth_status for %s -> %s", sender_id, auth_status)

    async def _get_client(
        self,
        encrypted_session: str,
        proxy: dict | None = None,
        sender_id: str | None = None,
        sender_slug: str | None = None,
    ) -> TelegramClient:
        """Create a connected Telethon client for the checker account.

        WR-06: classify auth failures exactly like ``TelegramService.get_client`` — a
        dead / unauthorized / banned session flips ``senders.auth_status`` BY ID (so
        the ``_tick`` JOIN-LATERAL gate ``auth_status='ok'`` excludes it on the next
        tick, closing the 5s hot loop that re-claimed the same contacts) and raises
        the typed ``SessionAuthError``. When ``sender_id`` is None (the
        ``probe_control`` call site) NO DB write happens — the probe swallows the
        error as a miss. Imports are inside the method to avoid an import cycle
        (mirrors how telegram.py imports Sender lazily)."""
        from telethon.errors import UserDeactivatedBanError

        from app.services.telegram import AUTH_ERRORS, SessionAuthError

        session_string = decrypt_session(encrypted_session)
        client = make_telegram_client(
            StringSession(session_string),
            proxy=proxy,
        )

        try:
            await client.connect()
        except AUTH_ERRORS as e:
            await self._flag_checker_auth(sender_id, "session_expired")
            raise SessionAuthError(sender_slug or "checker", "session_expired", str(e))
        except UserDeactivatedBanError as e:
            await self._flag_checker_auth(sender_id, "banned")
            raise SessionAuthError(sender_slug or "checker", "banned", str(e))

        try:
            if not await client.is_user_authorized():
                await client.disconnect()
                await self._flag_checker_auth(sender_id, "session_expired")
                raise SessionAuthError(
                    sender_slug or "checker", "session_expired", "Session is not authorized"
                )
        except SessionAuthError:
            raise
        except AUTH_ERRORS as e:
            await client.disconnect()
            await self._flag_checker_auth(sender_id, "session_expired")
            raise SessionAuthError(sender_slug or "checker", "session_expired", str(e))
        except UserDeactivatedBanError as e:
            await client.disconnect()
            await self._flag_checker_auth(sender_id, "banned")
            raise SessionAuthError(sender_slug or "checker", "banned", str(e))

        return client

    async def _lookup_cache(self, workspace_id: str, phone: str) -> Optional[dict]:
        """Check contacts_cache for any existing record for this phone within the workspace.

        Workspace-isolated by D-03 (Phase 1 multi-tenant): cache hits from another
        tenant's checker are not visible — prevents cross-tenant data leak via
        resolve cache.

        SRLD-07/D-12 — confidence-gated read of the negative bucket: a cached
        ``is_registered=false`` from a SUSPECT/low-confidence resolver is the Igor
        cross-contamination root cause — it short-circuits a re-check before
        Telegram is ever called, so the live re-resolve never happens. We therefore
        SUPPRESS a cached false (return ``None`` → force live re-resolve) whenever
        ANY matching ``contacts`` row in the same workspace is suspect or not
        high-confidence. Positive (``is_registered=true``) rows are served
        unchanged (a positive is not a contamination risk here). The cache row is
        NEVER deleted (ROADMAP "кэш не чистим").

        OQ#1 (Research): a phone may map to multiple contacts. The predicate is
        deliberately CONSERVATIVE — if ANY matching contact is suspect/low-confidence
        we fall through to a live resolve rather than trust a stale clean sibling.
        """
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                text("""
                    SELECT is_registered, telegram_id, username
                    FROM contacts_cache
                    WHERE workspace_id = :workspace_id
                      AND phone = :phone
                      AND updated_at > NOW() - INTERVAL '7 days'
                    ORDER BY updated_at DESC
                    LIMIT 1
                """),
                {"workspace_id": workspace_id, "phone": phone},
            )).fetchone()

            if row is None:
                return None

            # Confidence-gate ONLY the negative bucket (SRLD-07/D-12).
            if row[0] is False:
                suspect = (await db.execute(
                    text("""
                        SELECT 1
                        FROM contacts
                        WHERE workspace_id = :workspace_id
                          AND phone = :phone
                          AND (tg_probe_state = 'suspect'
                               OR tg_confidence IS DISTINCT FROM 'high')
                        LIMIT 1
                    """),
                    {"workspace_id": workspace_id, "phone": phone},
                )).fetchone()
                if suspect is not None:
                    # Poisoned/uncertified false → don't serve; force live re-resolve.
                    return None

        return {
            "is_registered": row[0],
            "telegram_id": row[1],
            "username": row[2],
            "from_cache": True,
        }

    async def _save_cache(self, workspace_id: str, checker_id: str, phone: str, is_registered: bool, telegram_id: Optional[int], username: Optional[str] = None):
        """Persist check result to contacts_cache under the checker's sender_id."""
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("""
                        INSERT INTO contacts_cache
                            (workspace_id, sender_id, phone, telegram_id, is_registered, username)
                        VALUES (:workspace_id, :sender_id, :phone, :telegram_id, :is_registered, :username)
                        ON CONFLICT (sender_id, phone) DO UPDATE SET
                            telegram_id = EXCLUDED.telegram_id,
                            is_registered = EXCLUDED.is_registered,
                            username = EXCLUDED.username,
                            updated_at = NOW()
                    """),
                    {
                        "workspace_id": workspace_id,
                        "sender_id": checker_id,
                        "phone": phone,
                        "telegram_id": telegram_id,
                        "is_registered": is_registered,
                        "username": username,
                    },
                )
                await db.commit()
        except Exception as exc:
            logger.warning(f"CheckerService: failed to save cache for {phone}: {exc}")

    async def check_phones(
        self,
        workspace_id: str,
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
            "results": [{"phone": str, "is_registered": bool, "telegram_id": int|None, "username": str|None, "from_cache": bool}, ...]
        }
        """
        async with self._get_lock(checker_slug):
            return await self._check_phones_locked(workspace_id, checker_id, checker_slug, encrypted_session, phones, proxy)

    async def probe_control(
        self,
        checker_slug: str,
        encrypted_session: str,
        phones: list[str],
        proxy: dict | None = None,
    ) -> dict:
        """LIVE-only control probe — resolve known-live numbers, bypassing cache.

        RESV-01/D-05 (Pitfall 1): the throttle-detector MUST hit Telegram on every
        control number. A probe that consults ``contacts_cache`` tests nothing — a
        silently-throttled checker would "pass" on stale cached hits. So this path
        deliberately:
          - NEVER reads ``_lookup_cache`` (live ``ResolvePhoneRequest`` every time),
          - NEVER writes ``_save_cache`` (the probe must not pollute the resolve cache),
          - NEVER mutates ``contacts`` rows (the control numbers are real ``registered``
            Barter rows — they must not be touched).

        Returns ``{"results": [{"phone", "is_registered"}...], "checked": int,
        "flood_wait_hit": bool}``. A control number that comes back
        ``is_registered=False`` is a MISS (the caller counts consecutive misses per
        checker); a truncated/short batch (fewer results than ``phones``) or
        ``flood_wait_hit=True`` is ALSO a miss (a flood-interrupted probe proves
        nothing about health). Does not log session strings.
        """
        async with self._get_lock(checker_slug):
            results: list[dict] = []
            flood_wait_hit = False
            client: Optional[TelegramClient] = None
            try:
                client = await self._get_client(encrypted_session, proxy=proxy)
                from telethon.tl.functions.contacts import ResolvePhoneRequest

                for i, phone in enumerate(phones):
                    try:
                        result = await client(ResolvePhoneRequest(phone=phone))
                        is_registered = bool(result and result.users)
                    except FloodWaitError:
                        raise
                    except (PhoneNumberInvalidError, PhoneNotOccupiedError):
                        is_registered = False
                    except Exception as exc:  # noqa: BLE001
                        err = str(exc)
                        if "PHONE_NOT_OCCUPIED" in err or "phone_not_occupied" in err.lower():
                            is_registered = False
                        else:
                            logger.error(
                                f"[checker:{checker_slug}] probe_control error for a control number: {exc}",
                                exc_info=True,
                            )
                            raise
                    results.append({"phone": phone, "is_registered": is_registered})
                    if i < len(phones) - 1:
                        await asyncio.sleep(random.uniform(
                            settings.contact_check_pace_low,
                            settings.contact_check_pace_high,
                        ))
            except FloodWaitError as exc:
                flood_wait_hit = True
                logger.warning(
                    f"[checker:{checker_slug}] probe_control FloodWait after "
                    f"{len(results)}/{len(phones)} — sleeping "
                    f"{min(exc.seconds, _FLOOD_WAIT_INLINE_CAP)}s (cap {_FLOOD_WAIT_INLINE_CAP}s, "
                    f"raised wait {exc.seconds}s)"
                )
                # WR-05: cap the inline block. Batch-A-safe — ONLY the sleep
                # duration changes; flood_wait_hit=True (a probe MISS) is unchanged.
                await asyncio.sleep(min(exc.seconds, _FLOOD_WAIT_INLINE_CAP))
            finally:
                if client:
                    try:
                        if client.is_connected():
                            await client.disconnect()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[checker:{checker_slug}] probe_control disconnect error: {exc}")

            return {"checked": len(results), "results": results, "flood_wait_hit": flood_wait_hit}

    async def _check_phones_locked(
        self,
        workspace_id: str,
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
            client = await self._get_client(
                encrypted_session, proxy=proxy,
                sender_id=checker_id, sender_slug=checker_slug,
            )

            for i, phone in enumerate(phones):
                # 1. Try cache first (workspace-isolated cross-sender lookup)
                cached = await self._lookup_cache(workspace_id, phone)
                if cached is not None:
                    results.append({
                        "phone": phone,
                        "is_registered": cached["is_registered"],
                        "telegram_id": cached.get("telegram_id"),
                        "username": cached.get("username"),
                        "from_cache": True,
                    })
                    logger.debug(f"[checker:{checker_slug}] {phone} → from cache (registered={cached['is_registered']})")
                    continue

                # 2. Cache miss — call Telegram. ResolvePhone first, then the
                #    importContacts fallback (with mandatory address-book cleanup,
                #    RESV-01/D-02) for registered-but-private numbers ResolvePhone
                #    cannot see.
                try:
                    resolved = await resolve_phone_with_fallback(client, phone)
                    is_registered = resolved["is_registered"]
                    telegram_id = resolved["telegram_id"]
                    username = resolved["username"]
                    error = resolved.get("error")
                except FloodWaitError:
                    raise  # propagate to outer except FloodWaitError handler
                except Exception as exc:
                    # Unexpected error (frozen account, network, etc.) — do NOT mask as
                    # "not registered"; re-raise so the batch stops and logs the real cause.
                    # (PHONE_NOT_OCCUPIED / invalid-number are handled inside the helper
                    # as a clean not-registered, not re-raised — see its docstring caveat.)
                    logger.error(f"[checker:{checker_slug}] Unexpected ResolvePhone error for {phone}: {exc}", exc_info=True)
                    raise

                # 3. Save to cache — but NOT for a hard error (IN-02). An invalid
                #    number is not a resolvable verdict; caching it as not_registered
                #    would poison the cache and let a campaign finalize a garbage row.
                if not error:
                    await self._save_cache(workspace_id, checker_id, phone, is_registered, telegram_id, username)

                entry = {
                    "phone": phone,
                    "is_registered": is_registered,
                    "telegram_id": telegram_id,
                    "username": username,
                    "from_cache": False,
                }
                # IN-02: propagate the error tag so _apply_results finalizes
                # tg_status='error' (the previously-unreachable error branch).
                if error:
                    entry["error"] = error
                results.append(entry)
                logger.debug(f"[checker:{checker_slug}] {phone} → registered={is_registered}")

                # 4. Polite delay between Telegram requests (skip after last item).
                # Pace is the authoritative knob (RESV-02/D-10); defaults 2.0/3.5
                # match the historical random.uniform so behaviour is unchanged.
                if i < len(phones) - 1:
                    delay = random.uniform(
                        settings.contact_check_pace_low,
                        settings.contact_check_pace_high,
                    )
                    await asyncio.sleep(delay)

        except FloodWaitError as exc:
            flood_wait_hit = True
            wait_sec = exc.seconds
            logger.warning(
                f"[checker:{checker_slug}] FloodWait hit after {len(results)}/{len(phones)} phones "
                f"— sleeping {min(wait_sec, _FLOOD_WAIT_INLINE_CAP)}s (cap {_FLOOD_WAIT_INLINE_CAP}s, "
                f"raised wait {wait_sec}s) then stopping batch"
            )
            # WR-05: cap the inline block — a multi-hour raised FloodWait must not
            # freeze the single-coroutine worker. flood_wait_hit=True is returned so
            # the caller degrades the checker with a durable cooldown.
            await asyncio.sleep(min(wait_sec, _FLOOD_WAIT_INLINE_CAP))
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

    async def check_usernames(
        self,
        workspace_id: str,
        checker_id: str,
        checker_slug: str,
        encrypted_session: str,
        usernames: list[str],
        proxy: dict | None = None,
    ) -> dict:
        """Resolve each Telegram username and cache the result.

        Mirrors :meth:`check_phones` but uses ResolveUsernameRequest. The cache
        key is the '@username' identity string (stored in contacts_cache.phone),
        matching the key used by the send/resolve path. ``usernames`` are bare
        handles (no leading '@').

        Returns the same summary shape as ``check_phones`` with one extra field
        per result: ``"username"`` (the bare handle, for matching by the worker).
        """
        async with self._get_lock(checker_slug):
            return await self._check_usernames_locked(
                workspace_id, checker_id, checker_slug, encrypted_session, usernames, proxy
            )

    async def _check_usernames_locked(
        self,
        workspace_id: str,
        checker_id: str,
        checker_slug: str,
        encrypted_session: str,
        usernames: list[str],
        proxy: dict | None = None,
    ) -> dict:
        from telethon.tl.functions.contacts import ResolveUsernameRequest

        results: list[dict] = []
        flood_wait_hit = False

        start_ts = time.monotonic()
        logger.info(f"[checker:{checker_slug}] Starting batch check for {len(usernames)} usernames")

        client: Optional[TelegramClient] = None
        try:
            client = await self._get_client(
                encrypted_session, proxy=proxy,
                sender_id=checker_id, sender_slug=checker_slug,
            )

            for i, uname in enumerate(usernames):
                bare = uname.lstrip("@")
                key = "@" + bare  # identity/cache key

                # 1. Cache first (workspace-isolated), keyed on '@username'.
                cached = await self._lookup_cache(workspace_id, key)
                if cached is not None:
                    results.append({
                        "username": bare,
                        "is_registered": cached["is_registered"],
                        "telegram_id": cached.get("telegram_id"),
                        "from_cache": True,
                    })
                    continue

                # 2. Cache miss — call Telegram.
                try:
                    result = await client(ResolveUsernameRequest(username=bare))
                    if result and result.users:
                        is_registered = True
                        telegram_id = result.users[0].id
                    else:
                        is_registered = False
                        telegram_id = None
                except FloodWaitError:
                    raise  # propagate to outer handler
                except (UsernameInvalidError, UsernameNotOccupiedError):
                    is_registered = False
                    telegram_id = None
                except Exception as exc:
                    low = str(exc).lower()
                    if "username_not_occupied" in low or "username_invalid" in low:
                        is_registered = False
                        telegram_id = None
                    else:
                        logger.error(
                            f"[checker:{checker_slug}] Unexpected ResolveUsername error for @{bare}: {exc}",
                            exc_info=True,
                        )
                        raise

                # 3. Cache under the '@username' key.
                await self._save_cache(workspace_id, checker_id, key, is_registered, telegram_id)

                results.append({
                    "username": bare,
                    "is_registered": is_registered,
                    "telegram_id": telegram_id,
                    "from_cache": False,
                })

                # 4. Polite delay between Telegram requests (skip after last).
                if i < len(usernames) - 1:
                    await asyncio.sleep(random.uniform(2.0, 3.5))

        except FloodWaitError as exc:
            flood_wait_hit = True
            logger.warning(
                f"[checker:{checker_slug}] FloodWait hit after {len(results)}/{len(usernames)} usernames "
                f"— sleeping {min(exc.seconds, _FLOOD_WAIT_INLINE_CAP)}s (cap {_FLOOD_WAIT_INLINE_CAP}s, "
                f"raised wait {exc.seconds}s) then stopping batch"
            )
            # WR-05: cap the inline block (see _check_phones_locked).
            await asyncio.sleep(min(exc.seconds, _FLOOD_WAIT_INLINE_CAP))
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
            f"[checker:{checker_slug}] Username batch done in {elapsed:.1f}s: "
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
