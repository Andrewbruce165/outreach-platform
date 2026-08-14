import logging
import asyncio
import re
import tempfile
import os
import shutil
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
from telethon.tl.types import InputPhoneContact, InputPeerUser, DocumentAttributeFilename
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    UserNotMutualContactError,
    UserIsBlockedError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    AuthKeyError,
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    AuthKeyPermEmptyError,
    UserDeactivatedBanError,
    MessageNotModifiedError,
    MessageEditTimeExpiredError,
    MessageAuthorRequiredError,
    MessageIdInvalidError,
)
from sqlalchemy import text

from app.config import get_settings
from app.services.encryption import decrypt_session
from app.database import AsyncSessionLocal
from app.utils.phone import is_username_key, sanitize_username, username_from_key

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
_SPAMBOT_FREE_PHRASES = ("good news", "no limits", "нет ограничений", "всё хорошо", "free as a bird", "свободен от", "свободна от")
# Telegram's 2025 read-only "freeze" is a REVERSIBLE, session-intact state (distinct
# from a permanent ban). SpamBot reports it with freeze/read-only wording. These must
# be classified BEFORE _SPAMBOT_SUSPENDED_PHRASES because a freeze reply can also carry
# the generic "blocked"/"заблокирован" keyword — the freeze signal has to win so we
# don't escalate a reversible freeze to a permanent 'suspended' verdict.
_SPAMBOT_FROZEN_PHRASES = (
    "frozen", "freeze", "read-only", "read only",
    "заморож", "только для чтения", "только чтение",
)
_SPAMBOT_LIMITED_PHRASES = ("limited", "restrict", "ограничен")
_SPAMBOT_SUSPENDED_PHRASES = (
    "suspended", "blocked", "banned",
    "заблокирован", "приостановлен", "забанен",
)


def classify_spambot_text(text: str) -> str:
    """Classify a @SpamBot reply body → 'free' | 'frozen' | 'limited' | 'suspended' | 'unknown'.

    'free' is checked FIRST: a "good news, no limits" reply must win even though it
    can incidentally contain a restriction keyword in boilerplate. 'frozen' is checked
    BEFORE 'limited'/'suspended' — Telegram's read-only freeze is reversible and
    session-intact, and its reply can carry a generic "blocked" keyword, so the freeze
    signal must win over 'suspended' (see .planning/debug/frozen-spambot-check-error.md).
    'unknown' (no recognised phrase) is NOT a restriction — callers must only act
    restrictively on 'frozen'/'limited'/'suspended'.
    """
    text_lower = (text or "").lower()
    if any(p in text_lower for p in _SPAMBOT_FREE_PHRASES):
        return "free"
    if any(p in text_lower for p in _SPAMBOT_FROZEN_PHRASES):
        return "frozen"
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


class ProfileChangeRejectedError(Exception):
    """Telegram accepted ``UpdateProfileRequest`` WITHOUT raising, yet a post-write
    re-read shows the requested value was NOT applied.

    This is Telegram's undocumented anti-abuse throttle on profile-name/bio churn:
    the RPC succeeds (no FloodWaitError) but the change is silently no-op'd —
    especially on freshly-created accounts. Trusting "the RPC returned" as success
    reports a false success to the user and desyncs the local cache. The router maps
    this to a clear 409 PROFILE_CHANGE_REJECTED so the UI tells the user to retry
    later instead of showing "Профиль обновлён".
    """
    def __init__(self, fields: list[str]):
        self.fields = fields
        super().__init__(
            "PROFILE_CHANGE_REJECTED: Telegram did not apply "
            f"{', '.join(fields)} (silent rate limit)"
        )


async def _set_auth_status(sender_id: str, auth_status: str):
    """Update sender auth_status BY PRIMARY KEY.

    WR-14: since migration 014, ``senders.slug`` is unique only per-workspace, so
    the same slug can exist in two workspaces. Keying this UPDATE on ``slug`` made
    ``scalar_one_or_none()`` raise ``MultipleResultsFound`` inside get_client's
    auth-error handler — replacing ``SessionAuthError`` with an unrelated crash so
    the queue worker never flipped ``auth_status``. Mirror
    ``CheckerService._flag_checker_auth`` (checker.py) and update by ``id``.
    """
    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(
                text("UPDATE senders SET auth_status = :st WHERE id = :sid"),
                {"st": auth_status, "sid": sender_id},
            )
    logger.warning("auth_status for %s -> %s", sender_id, auth_status)

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
    fingerprint: dict | None = None,
) -> TelegramClient:
    """Create a TelegramClient with official-client fingerprint.

    Patches _init_request.lang_pack before connect() so Telegram identifies
    the session as a known platform client (tdesktop). Without this patch,
    Telethon sends an empty lang_pack which Telegram uses to mark the session
    as third-party and terminates it when the user logs out from mobile.

    Per-account fingerprint override (Phase 21, IMPT-04): pass ``fingerprint``
    (a dict of device_model/system_version/app_version/lang_code/system_lang_code)
    to reconnect an *imported* account with the exact fingerprint that created its
    session. STRICT NULL fallback (D-02): ``fingerprint=None`` resolves to EXACTLY
    the global ``_CLIENT_FINGERPRINT`` — byte-identical to pre-Phase-21 behaviour,
    so the phone-onboarded senders (all with NULL fingerprint) are unaffected. Any
    keys the override omits fall back to the global. ``api_id``/``api_hash`` stay the
    global settings values — never per-account (D-03). ``lang_pack`` is forced to
    'tdesktop' UNCONDITIONALLY even when overriding (D-04 — it is the field that
    terminates sessions when empty).

    Pass client_class=ResilientTelegramClient for the listener subprocess.
    Must be called before client.connect().
    """
    # Merge the per-account override over the global default, but treat a None value as
    # "not provided" so it does NOT clobber the default — some vendor JSONs omit
    # lang_code/system_lang_code (only lang_pack), which would otherwise send None into
    # Telethon's initConnection → "bytes or str expected, not NoneType" on the first request.
    fp = {**_CLIENT_FINGERPRINT, **{k: v for k, v in (fingerprint or {}).items() if v is not None}}
    client = client_class(
        session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        flood_sleep_threshold=flood_sleep_threshold,
        proxy=build_proxy_tuple(proxy),
        **fp,
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
        sender_id: str,
        encrypted_session: str,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> TelegramClient:
        """Create a temporary Telegram client for a single operation.

        IMPORTANT: Caller MUST disconnect the client after use via disconnect_client(),
        otherwise the persistent connection will steal updates from the listener.

        Raises SessionAuthError if the session is dead (expired/revoked/banned).

        WR-14: the per-account lock and the auth_status update are keyed on
        ``sender_id`` (primary key), NOT ``sender_slug`` — since migration 014 the
        slug is unique only per-workspace, so two workspaces can share a slug. The
        human-readable ``sender_slug`` is still used only for the SessionAuthError
        message.
        """
        if sender_id not in self._locks:
            self._locks[sender_id] = asyncio.Lock()

        async with self._locks[sender_id]:
            session_string = decrypt_session(encrypted_session)
            client = make_telegram_client(
                StringSession(session_string),
                proxy=proxy,
                fingerprint=fingerprint,
            )

            try:
                await client.connect()
            except AUTH_ERRORS as e:
                await _set_auth_status(sender_id, "session_expired")
                raise SessionAuthError(sender_slug, "session_expired", str(e))
            except UserDeactivatedBanError as e:
                await _set_auth_status(sender_id, "banned")
                raise SessionAuthError(sender_slug, "banned", str(e))

            try:
                if not await client.is_user_authorized():
                    await client.disconnect()
                    await _set_auth_status(sender_id, "session_expired")
                    raise SessionAuthError(sender_slug, "session_expired", "Session is not authorized")
            except SessionAuthError:
                raise
            except AUTH_ERRORS as e:
                await client.disconnect()
                await _set_auth_status(sender_id, "session_expired")
                raise SessionAuthError(sender_slug, "session_expired", str(e))
            except UserDeactivatedBanError as e:
                await client.disconnect()
                await _set_auth_status(sender_id, "banned")
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
            status: 'free' | 'frozen' | 'limited' | 'suspended' | 'unknown'
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
            # 1. Check contacts_cache.
            # `newer_than_verdict` (2026-07-27 poisoned-cache incident): a cached
            # false is only trusted when it is NOT older than the newest checker
            # verdict for this phone — a later healthy re-check supersedes any
            # earlier false written by a throttle-degraded account. No contacts
            # row / NULL tg_checked_at → '-infinity' → the guard is permissive.
            row = (await db.execute(
                text("""
                    SELECT telegram_id, first_name, last_name, username, is_registered, access_hash,
                           updated_at >= COALESCE(
                               (SELECT MAX(c.tg_checked_at) FROM contacts c
                                 WHERE c.workspace_id = :workspace_id AND c.phone = :phone),
                               '-infinity'::timestamptz) AS newer_than_verdict
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

            # D-12: a cached `is_registered=false` is only trusted when NO matching
            # contacts row in the workspace is SUSPECT. The Igor cross-contamination
            # showed a throttled checker poisons the cache with false negatives; if the
            # contact's probe is suspect (or its confidence isn't 'high'), we must NOT
            # serve the blind false — let resolve_contact fall through to a LIVE resolve.
            # NULL confidence counts as not-trusted (IS DISTINCT FROM is NULL-safe).
            # Shared suspect predicate with 17-02 (_lookup_cache). The cache is NEVER
            # deleted — we only suppress the READ.
            suspect = (await db.execute(
                text("""
                    SELECT 1 FROM contacts
                     WHERE workspace_id = :workspace_id AND phone = :phone
                       AND (tg_probe_state = 'suspect' OR tg_confidence IS DISTINCT FROM 'high')
                     LIMIT 1
                """),
                {"workspace_id": workspace_id, "phone": phone}
            )).fetchone()

            if row and row[4] is False:  # known unregistered (per-sender)
                if suspect:
                    logger.debug(f"Contact {phone}: per-sender false is suspect — forcing live resolve (D-12)")
                elif not row[6]:
                    # 2026-07-27 incident: the false predates a newer checker
                    # verdict — a stale poison row, never serve it.
                    logger.debug(f"Contact {phone}: per-sender false is older than checker verdict — forcing live resolve")
                else:
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
            # подтвердил что номер не зарегистрирован, пропускаем live resolve.
            # Для зарегистрированных использовать не можем — нужен per-sender access_hash.
            #
            # 2026-07-27 poisoned-cache incident: take the NEWEST row for the
            # (workspace, phone) — not "any false row" — and only rows at least as
            # recent as the newest checker verdict count. Four throttle-degraded
            # checkers wrote 30 false rows each; a later healthy re-check stamped
            # the contacts row registered/high/clean, which CLOSED the D-12 suspect
            # gate and made the stale falses authoritative for every sender. With
            # newest-row + verdict-recency semantics the 07:04 true supersedes the
            # 06:48–06:56 falses and the short-circuit is impossible.
            cross_row = (await db.execute(
                text("""
                    SELECT is_registered FROM contacts_cache
                    WHERE workspace_id = :workspace_id
                      AND phone = :phone
                      AND updated_at > NOW() - INTERVAL '7 days'
                      AND updated_at >= COALESCE(
                          (SELECT MAX(c.tg_checked_at) FROM contacts c
                            WHERE c.workspace_id = :workspace_id AND c.phone = :phone),
                          '-infinity'::timestamptz)
                    ORDER BY updated_at DESC
                    LIMIT 1
                """),
                {"workspace_id": workspace_id, "phone": phone}
            )).fetchone()

            # D-12: same suspect gate on the cross-sender false. A suspect-source false
            # (e.g. a throttled checker on another sender) must NOT short-circuit — the
            # sender does a LIVE resolve via the ladder instead.
            if cross_row and cross_row[0] is False and not suspect:
                logger.debug(f"Contact {phone} found unregistered in cross-sender cache — skipping live resolve")
                return {"is_registered": False, "from_cache": True}

        return None

    async def _load_contact_verdict(self, workspace_id: str, phone: str) -> dict:
        """Load the checker verdict + @usernames for a phone (tier-2/tier-3 inputs).

        Reads the existing Phase-14/17 columns on `contacts`:
          - `tg_status` — the checker verdict ('registered' | 'not_registered' | 'pending' | …),
            the gate for the tier-3 ImportContacts (only 'registered' triggers an import, D-03/D-11).
          - `tg_username_resolved` — the public, transferable @handle the checker captured
            (17-02), the input for the sender's tier-2 ResolveUsername (D-07).
          - `username` — the @handle the OPERATOR declared at import time. The CSV import
            stamps username-bearing contacts as tg_status='registered' so they bypass the
            ContactCheckWorker (routers/contacts.py), and the worker is the only writer of
            `tg_username_resolved` — so for exactly those contacts the captured handle is
            NULL forever. Without the declared handle tier-2 could never fire for them and
            privacy-hidden numbers surfaced a false "not registered".

        A phone may map to multiple contacts; the ORDER BY prefers a `registered` row
        (conservative — favours reachability) then the most recently updated one. Returns
        all-None when there is no contacts row (e.g. a '@handle' identity-key contact or an
        ad-hoc send) — callers treat a None verdict permissively per the existing send-path
        semantics, but ONLY 'registered' ever triggers an import.
        """
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                text("""
                    SELECT tg_status, tg_username_resolved, username
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
            "declared_username": row[2] if row else None,
        }

    async def _persist_resolved_handle(
        self, workspace_id: str, phone: str, username: str, telegram_id: Optional[int]
    ) -> None:
        """Promote a live-verified declared handle into `contacts.tg_username_resolved`.

        Only called after a SUCCESSFUL tier-2 ResolveUsername on an operator-declared
        handle, so the value is proven live. Makes the next send cheap (the captured
        handle is read first) and gives the operator visible provenance.

        Deliberately does NOT touch tg_status / tg_confidence / tg_resolved_by /
        tg_probe_state — those are checker-provenance fields (D-09) and a sender-side
        resolve is not a checker verdict. Failures are swallowed: persistence is an
        optimisation, never a reason to fail a send.
        """
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("""
                        UPDATE contacts
                           SET tg_username_resolved = :uname,
                               tg_telegram_id = COALESCE(:tg_id, tg_telegram_id),
                               updated_at = NOW()
                         WHERE workspace_id = :workspace_id AND phone = :phone
                    """),
                    {
                        "workspace_id": workspace_id,
                        "phone": phone,
                        "uname": username[:50],
                        "tg_id": telegram_id,
                    },
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to persist resolved handle for {phone}: {e}")

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

        # Tier-2: ResolveUsername on COALESCE(captured, declared) @username (D-07).
        # The cheapest, safest transferable resolve — a public handle resolves
        # identically on any account.
        #   1. `tg_username_resolved` — captured live by the checker (preferred).
        #   2. `contacts.username` — declared by the operator at import time. The
        #      import shortcut routes username-bearing contacts around the checker, so
        #      the captured handle stays NULL for them; without this fallback tier-2 was
        #      skipped and privacy-hidden numbers died in tier-3 with a false
        #      "not registered" (bug send-ignores-import-username, 2026-08-14).
        # Both are sanitized: a garbage/placeholder handle (the literal "None" a CSV
        # export can emit) is SKIPPED, not resolved — skipping tier-2 never finalizes
        # not_registered. A stale handle likewise falls through to tier-3 (D-09).
        captured = sanitize_username(verdict.get("captured_username"))
        declared = sanitize_username(verdict.get("declared_username"))
        handle = captured or declared
        if handle:
            res = await self._resolve_username(
                client, workspace_id, sender_id, "@" + handle
            )
            if res.get("is_registered"):
                # Cache the access_hash under the PHONE key so follow-up sends are
                # phone-cache hits (the @handle key is also cached by _resolve_username).
                await self._save_contact_cache(workspace_id, sender_id, phone, res)
                if not captured:
                    # Declared handle proven live → promote it so the next send reads
                    # it from the captured column and skips this branch.
                    await self._persist_resolved_handle(
                        workspace_id, phone,
                        sanitize_username(res.get("username")) or handle,
                        res.get("telegram_id"),
                    )
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

        except (UsernameNotOccupiedError, UsernameInvalidError):
            # D-09: a stale captured @username (the handle was renamed/freed) does NOT
            # mean the contact is unregistered — it means THIS handle is gone. NEVER
            # cache/finalize False here; signal the caller to FALL THROUGH to the
            # import tier (resolve_contact tier-3). For a '@handle' identity-key
            # contact (no phone), resolve_contact maps this to not_registered.
            logger.info(f"Contact {key}: captured username is stale → fall through to import")
            return {"stale_username": True}
        except Exception as e:
            err = str(e)
            low = err.lower()
            # Defence-in-depth: Telethon occasionally surfaces these as generic RPC
            # errors. Treat the string match the same way — stale, fall through (D-09).
            if "username_not_occupied" in low or "username_invalid" in low:
                logger.info(f"Contact {key}: captured username is stale → fall through to import")
                return {"stale_username": True}
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
                error = {
                    "code": "RECIPIENT_NOT_IN_TELEGRAM",
                    "message": f"{target} не зарегистрирован в Telegram"
                }
                # 2026-07-27 incident: a cache-sourced verdict is a workspace-level
                # DB read — rotating to another sender cannot change it. The queue
                # uses this flag to SKIP _reroute_resolve_fail (no pool churn).
                if contact_info.get("from_cache"):
                    error["from_cache"] = True
                return {"success": False, "error": error}

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
        except UserIsBlockedError:
            # SRLD-08 (D-15): the recipient has blocked THIS sender. This is the
            # dominant cold-outreach account-killer proxy (blocks/reports → PeerFlood
            # → freeze). Surface a distinct code so the queue can durably record the
            # block and fail ONLY this item — it is NOT an account restriction (D-16).
            return {
                "success": False,
                "error": {
                    "code": "USER_IS_BLOCKED",
                    "message": "Получатель заблокировал отправителя"
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
            # Defence-in-depth (mirrors is_frozen_error): Telethon may surface a
            # block as a generic RPC error. Match the string the same way (D-15).
            if "USER_IS_BLOCKED" in str(e):
                return {
                    "success": False,
                    "error": {
                        "code": "USER_IS_BLOCKED",
                        "message": "Получатель заблокировал отправителя"
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
        file_url: Optional[str] = None,
        file_name: Optional[str] = None,
        caption: Optional[str] = None,
        sender_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        force_document: bool = True,
        attachments: Optional[list[dict]] = None,
    ) -> dict:
        """Send a file to a recipient. Client is disconnected after the operation.

        Source (D-08): pass ``file_bytes`` for a DB-blob source (written straight to
        a temp file, no network) OR ``file_url`` to download the file first. Exactly
        one is expected; ``file_bytes`` wins when both are given.

        Album (260709-dbl): pass ``attachments`` — a list of dicts
        ``{"file_bytes": bytes, "file_name": str, "content_type": Optional[str]}`` —
        to deliver SEVERAL files as one grouped Telegram album (caption on the first).
        Each file is written into its own temp subdir so Telethon derives the EXACT
        original filename from the basename (no DocumentAttributeFilename needed, no
        cross-file name collisions). Telegram may split mixed media types (photo+doc)
        into more than one grouped message; Telethon handles that internally —
        acceptable for v1. When ``attachments`` is falsy the single-file path below is
        byte-for-byte unchanged.

        Media type (D-06): ``force_document=True`` (default) preserves today's
        behaviour — every file arrives as a document. ``force_document=False`` lets
        Telethon auto-detect media (photo->photo, video->video, else document); this
        works because the temp file keeps the ORIGINAL filename extension.

        Caption overflow (D-07): a caption longer than 1024 chars is sent as a
        separate follow-up text message so nothing is lost.
        """
        tmp_path = None
        album_dir = None
        try:
            # D-08 guard: need at least one source (blob, URL or album) before touching Telegram.
            if not attachments and file_bytes is None and not file_url:
                return {
                    "success": False,
                    "error": {
                        "code": "SEND_FAILED",
                        "message": "no file source"
                    }
                }

            # Resolve contact (cache first, ImportContacts only for new contacts)
            if sender_id and workspace_id:
                contact_info = await self.resolve_contact(client, workspace_id, sender_id, phone, recipient_name)
            else:
                contact_info = await self.check_contact(client, phone)

            if not contact_info.get("is_registered"):
                target = phone if is_username_key(phone) else f"Номер {phone}"
                error = {
                    "code": "RECIPIENT_NOT_IN_TELEGRAM",
                    "message": f"{target} не зарегистрирован в Telegram"
                }
                # 2026-07-27 incident: cache verdicts are workspace-level — the
                # queue skips _reroute_resolve_fail on this flag (see send_message).
                if contact_info.get("from_cache"):
                    error["from_cache"] = True
                return {"success": False, "error": error}

            telegram_id = contact_info["telegram_id"]
            access_hash = contact_info.get("access_hash")

            if access_hash is not None:
                peer = InputPeerUser(user_id=telegram_id, access_hash=access_hash)
            else:
                peer = telegram_id  # fallback for cached contacts without access_hash

            # Telegram caption limit for media is 1024 chars. If the caption exceeds
            # the limit, send the file(s) without caption and follow up with a separate
            # text message so the full text is delivered (D-07). Shared by both paths.
            CAPTION_LIMIT = 1024
            file_caption = None
            overflow_text = None
            if caption:
                if len(caption) <= CAPTION_LIMIT:
                    file_caption = caption
                else:
                    overflow_text = caption

            if attachments:
                # 260709-dbl album path: write each file into its OWN temp subdir so
                # Telethon derives the EXACT original filename from the basename (no
                # DocumentAttributeFilename needed, no cross-file name collisions), then
                # send the whole list as one grouped message (caption lands on the first).
                # Telegram may split mixed media types (photo+doc) into >1 grouped
                # message; Telethon handles that internally — acceptable for v1.
                album_dir = tempfile.mkdtemp()
                media_paths = []
                for i, att in enumerate(attachments):
                    att_name = os.path.basename(att.get("file_name") or "") or "file"
                    sub = os.path.join(album_dir, str(i))
                    os.makedirs(sub, exist_ok=True)
                    path = os.path.join(sub, att_name)
                    with open(path, "wb") as f:
                        f.write(att["file_bytes"])
                    media_paths.append(path)
                sent = await client.send_file(
                    peer,
                    media_paths,
                    caption=file_caption,
                    force_document=force_document,
                )
            else:
                # Obtain the file bytes: blob source (D-08) skips the network entirely;
                # otherwise fall back to the existing URL download.
                if file_bytes is not None:
                    # The attachment always supplies a file_name; default defensively.
                    if not file_name:
                        file_name = "file"
                    file_data = file_bytes
                else:
                    if not file_name:
                        parsed = urlparse(file_url)
                        file_name = os.path.basename(parsed.path) or "file"

                    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
                        resp = await http.get(file_url)
                        resp.raise_for_status()
                        file_data = resp.content

                # Save to temp file. The suffix is load-bearing: Telethon uses the file
                # extension to decide auto-media type when force_document=False (D-06).
                suffix = os.path.splitext(file_name)[1] or ""
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                    f.write(file_data)
                    tmp_path = f.name

                # Send file via Telethon.
                # Telethon's send_file has no `file_name` kwarg — it silently swallows
                # unknown kwargs into **kwargs and does nothing with them. The real
                # filename comes from a DocumentAttributeFilename attribute, which
                # overrides the default (derived from tmp_path's random basename) only
                # when explicitly passed here (mirrors send_file_by_telegram_id).
                sent = await client.send_file(
                    peer,
                    tmp_path,
                    caption=file_caption,
                    attributes=[DocumentAttributeFilename(file_name)] if file_name else None,
                    force_document=force_document
                )

            # An album returns a LIST of messages; take the first message's id.
            if isinstance(sent, (list, tuple)):
                message_id = str(sent[0].id) if sent else None
            else:
                message_id = str(sent.id)

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
                "message_id": message_id,
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
        except UserIsBlockedError:
            # SRLD-08 (D-15): recipient blocked THIS sender — parity with send_message.
            return {
                "success": False,
                "error": {
                    "code": "USER_IS_BLOCKED",
                    "message": "Получатель заблокировал отправителя"
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
            if "USER_IS_BLOCKED" in str(e):
                return {
                    "success": False,
                    "error": {
                        "code": "USER_IS_BLOCKED",
                        "message": "Получатель заблокировал отправителя"
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
            # 260709-dbl: remove the album's parent temp dir (and its per-file subdirs).
            if album_dir:
                shutil.rmtree(album_dir, ignore_errors=True)

    async def send_message_by_telegram_id(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        telegram_id: int,
        message: str,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
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
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)

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

    # ─── Account profile (Phase 20 — PROF-02/03) ────────────────────────────
    # All three follow the send_message_by_telegram_id client-per-op skeleton:
    # create via get_client, do the op, ALWAYS disconnect_client in finally.
    # SessionAuthError (dead session) is NOT caught here — it propagates so the
    # router maps it to 403 (same contract as the spambot-check handler). Telethon
    # profile errors (UsernameOccupiedError, AboutTooLongError, FloodWaitError, ...)
    # also propagate; the router owns the error→HTTP mapping.

    async def update_profile(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        request,
        *,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Dispatch a pre-built ``account.UpdateProfileRequest`` via a per-op client.

        The router builds the TL request from only the fields the user actually
        changed (``None`` leaves a field untouched — RESEARCH anti-pattern) and
        passes it here; this method owns the client lifecycle only.
        """
        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            await client(request)
            # Post-write verification (D-07 hardening): Telegram can accept
            # UpdateProfileRequest WITHOUT raising FloodWaitError yet silently NOT
            # apply a first/last-name or bio change (anti-abuse throttle on profile
            # churn, most aggressive on fresh accounts). Re-read the live profile and
            # diff against what was requested; a confirmed mismatch → the change was
            # rejected. The READ itself is best-effort: if it fails we degrade to the
            # old "assume success" behaviour rather than falsely reporting rejection.
            rejected = await self._verify_profile_applied(client, request)
            if rejected:
                raise ProfileChangeRejectedError(rejected)
            return {"success": True}
        finally:
            if client:
                await self.disconnect_client(client)

    @staticmethod
    async def _verify_profile_applied(client, request) -> list[str]:
        """Return the list of requested fields Telegram did NOT actually apply.

        Only fields the caller actually set (non-``None`` on the request) are checked.
        Comparison is whitespace-tolerant. Any failure to READ the live profile
        returns ``[]`` (no false rejection) — verification is a safety net, never a
        new failure mode of its own.
        """
        req_first = getattr(request, "first_name", None)
        req_last = getattr(request, "last_name", None)
        req_about = getattr(request, "about", None)

        def _norm(v) -> str:
            return (v or "").strip()

        mismatched: list[str] = []
        try:
            if req_first is not None or req_last is not None:
                me = await client.get_me()
                if req_first is not None and _norm(getattr(me, "first_name", None)) != _norm(req_first):
                    mismatched.append("first_name")
                if req_last is not None and _norm(getattr(me, "last_name", None)) != _norm(req_last):
                    mismatched.append("last_name")
            if req_about is not None:
                from telethon.tl.functions.users import GetFullUserRequest
                full = await client(GetFullUserRequest("me"))
                live_about = getattr(getattr(full, "full_user", None), "about", None)
                if _norm(live_about) != _norm(req_about):
                    mismatched.append("about")
        except Exception as e:  # noqa: BLE001 — verification read is best-effort
            logger.info(f"[telegram] profile post-write verify skipped (read failed): {e}")
            return []
        return mismatched

    async def check_username(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        username: str,
        *,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Live username availability pre-check (``account.CheckUsernameRequest``).

        Returns ``{"available": bool, "reason": 'taken'|'invalid'|None}``. Raises on
        session/connection failure — the router treats an unreachable session as a
        best-effort fall-through to the format-only verdict.
        """
        from telethon.tl.functions.account import CheckUsernameRequest
        from telethon.errors import UsernameInvalidError
        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            try:
                available = await client(CheckUsernameRequest(username))
            except UsernameInvalidError:
                return {"available": False, "reason": "invalid"}
            return {"available": bool(available), "reason": None if available else "taken"}
        finally:
            if client:
                await self.disconnect_client(client)

    async def set_username(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        username: str,
        *,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Set (or clear via ``username=""``) the account username via
        ``account.UpdateUsernameRequest``.

        Re-submitting the account's current username raises ``UsernameNotModifiedError``
        which is treated as a success no-op (Pitfall 4). Occupied / invalid / paid-handle
        errors PROPAGATE — the router maps them to structured HTTP responses.
        """
        from telethon.tl.functions.account import UpdateUsernameRequest
        from telethon.errors import UsernameNotModifiedError
        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            try:
                await client(UpdateUsernameRequest(username))
            except UsernameNotModifiedError:
                return {"success": True}  # current username re-submitted = no-op success
            return {"success": True}
        finally:
            if client:
                await self.disconnect_client(client)

    async def update_username(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        username: str,
        *,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Router-facing alias for :meth:`set_username` (kept as the router's call
        target; delegates to the canonical per-op implementation above)."""
        return await self.set_username(
            sender_slug, sender_id, encrypted_session, username, proxy=proxy, fingerprint=fingerprint
        )

    # ─── Account profile photo + resync (Phase 20 — PROF-04/06/07, D-11/D-12) ──
    # Same client-per-op skeleton as the identity methods above: create via
    # get_client, do the op, ALWAYS disconnect_client in finally. SessionAuthError
    # + Telethon errors (PhotoCropSizeSmallError, PhotoExtInvalidError, FloodWaitError)
    # PROPAGATE — the router owns the error→HTTP mapping (_raise_profile_telegram_error).

    async def set_profile_photo(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        raw_bytes: bytes,
        *,
        file_name: str = "avatar.jpg",
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Upload a new profile photo, then re-download Telegram's OWN normalized
        avatar (OQ3: already square-ish / re-encoded) to cache instead of the raw
        upload. Returns ``{"success": True, "photo": <bytes>, "photo_mime": "image/jpeg"}``.
        Raises on failure (SessionAuthError + Telethon photo errors propagate)."""
        import io
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            input_file = await client.upload_file(io.BytesIO(raw_bytes), file_name=file_name)
            await client(UploadProfilePhotoRequest(file=input_file))
            # OQ3: cache Telegram's own normalized small avatar, not the raw upload.
            norm = await client.download_profile_photo('me', file=bytes)
            return {"success": True, "photo": norm, "photo_mime": "image/jpeg"}
        finally:
            if client:
                await self.disconnect_client(client)

    async def upload_profile_photo(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        raw_bytes: bytes,
        *,
        file_name: str = "avatar.jpg",
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Router-facing alias for :meth:`set_profile_photo` (the name the upload
        endpoint calls; delegates to the canonical per-op implementation above)."""
        return await self.set_profile_photo(
            sender_slug, sender_id, encrypted_session, raw_bytes,
            file_name=file_name, proxy=proxy, fingerprint=fingerprint,
        )

    async def delete_profile_photo(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Remove the current profile photo. Fetches a FRESH photo object first
        (``get_profile_photos('me', limit=1)``) so DeletePhotosRequest carries a
        valid, non-expired file_reference (Pitfall 6). No photo present = no-op success."""
        from telethon.tl.functions.photos import DeletePhotosRequest
        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            photos = await client.get_profile_photos('me', limit=1)   # fresh file_reference (Pitfall 6)
            if photos:
                await client(DeletePhotosRequest(id=[photos[0]]))
            return {"success": True}
        finally:
            if client:
                await self.disconnect_client(client)

    async def delete_profile_photos(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Router-facing alias for :meth:`delete_profile_photo` (the name the delete
        endpoint calls; delegates to the canonical per-op implementation above)."""
        return await self.delete_profile_photo(
            sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint
        )

    async def resync_profile(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """D-12: re-fetch the LIVE username / bio / photo from Telegram into the cache.

        Returns ``{"success": True, "username": ..., "bio": ..., "photo": <bytes|None>,
        "photo_mime": ..., "has_photo": bool}``. GetFullUser is wrapped so a bio-fetch
        failure degrades to ``bio=None`` without failing the whole resync."""
        from telethon.tl.functions.users import GetFullUserRequest
        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            me = await client.get_me()
            try:
                full = await client(GetFullUserRequest('me'))
                bio = getattr(full.full_user, "about", None)
            except Exception:  # noqa: BLE001 — bio is best-effort; username/photo still resync
                bio = None
            photo_bytes = await client.download_profile_photo('me', file=bytes)   # bytes | None
            return {
                "success": True,
                "username": getattr(me, "username", None),
                # PROF-06 gap-fix: surface the LIVE display name too so a resync can
                # refresh the cached name after the user renames on Telegram (there is
                # no separate first/last column — the router composes them into `name`).
                "first_name": getattr(me, "first_name", None),
                "last_name": getattr(me, "last_name", None),
                "premium": bool(getattr(me, "premium", False)),
                "bio": bio,
                "photo": photo_bytes,
                "photo_mime": "image/jpeg" if photo_bytes else None,
                "has_photo": photo_bytes is not None,
            }
        finally:
            if client:
                await self.disconnect_client(client)

    async def fetch_profile(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Router-facing alias for :meth:`resync_profile` (the name the resync
        endpoint calls; delegates to the canonical per-op implementation above)."""
        return await self.resync_profile(
            sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint
        )

    # ─── Account 2FA + recovery email (Phase 20 — PROF-05, D-03/D-04) ──────────
    # Same client-per-op skeleton as the identity/photo methods: create via
    # get_client, do the op, ALWAYS disconnect_client in finally. SessionAuthError
    # + Telethon errors (PasswordHashInvalidError, EmailInvalidError,
    # PasswordTooFreshError/SessionTooFreshError, FloodWaitError) PROPAGATE — the
    # router owns the error→HTTP mapping (_raise_profile_telegram_error).
    #
    # SECURITY (D-03): the 2FA password is a transient request-field only. It is
    # never returned, never logged, never persisted anywhere.

    async def change_2fa_password(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        current_password: str | None = None,
        new_password: str,
        hint: str = "",
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Set (current_password=None) or change the account's 2FA password via the
        high-level ``client.edit_2fa`` in ONE stateless request.

        No ``email=`` kwarg is passed → no ``email_code_callback`` is required →
        edit_2fa completes synchronously (RESEARCH §Pitfall 2, CRITICAL). Recovery
        email is a separate two-request flow (:meth:`start_recovery_email`).

        Errors PROPAGATE (PasswordHashInvalidError = wrong current password,
        PasswordTooFreshError/SessionTooFreshError, FloodWaitError) — the router maps
        them. The password is never logged.
        """
        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            # No email here → no email_code_callback → completes synchronously (Pitfall 2).
            await client.edit_2fa(
                current_password=current_password,
                new_password=new_password,
                hint=hint or "",
            )
            return {"success": True}
        finally:
            if client:
                await self.disconnect_client(client)

    async def edit_2fa(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        current_password: str | None = None,
        new_password: str,
        hint: str = "",
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Router-facing alias for :meth:`change_2fa_password` (the name the 2FA
        endpoint calls; delegates to the canonical per-op implementation above)."""
        return await self.change_2fa_password(
            sender_slug,
            sender_id,
            encrypted_session,
            current_password=current_password,
            new_password=new_password,
            hint=hint,
            proxy=proxy,
            fingerprint=fingerprint,
        )

    async def start_recovery_email(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        current_password: str | None = None,
        email: str,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Step 1 of the recovery-email change — the TWO-request raw flow.

        ``edit_2fa(email=...)`` needs a synchronous ``email_code_callback`` that a
        per-op disconnect-between-requests client cannot provide (RESEARCH §Pitfall 2),
        so we drop to the raw functions: ``GetPasswordRequest`` → ``compute_check`` →
        ``UpdatePasswordSettingsRequest``. Telegram sends the confirmation code and
        raises ``EmailUnconfirmedError`` — we pivot on it and return the code length so
        the UI can prompt. The pending-email state now lives account-side on Telegram,
        so step 2 (:meth:`confirm_recovery_email`) can use a FRESH per-op client.

        Returns ``{"code_length": n}``. Errors PROPAGATE (EmailInvalidError,
        PasswordHashInvalidError, PasswordTooFreshError/SessionTooFreshError,
        FloodWaitError). The password is never logged.
        """
        import os as _os
        from telethon.tl.functions.account import (
            GetPasswordRequest,
            UpdatePasswordSettingsRequest,
        )
        from telethon.tl.types.account import PasswordInputSettings
        from telethon.password import compute_check, compute_digest
        from telethon.errors import EmailUnconfirmedError

        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            pwd = await client(GetPasswordRequest())
            # A recovery email is Telegram's mechanism to RESET the cloud (2FA)
            # password — it cannot exist without one. For a password-less account
            # GetPasswordRequest returns has_password=False / current_algo=None, and
            # feeding that to compute_check raises the raw telethon
            # "unsupported password algorithm NoneType" ValueError. Gate it here and
            # surface a clear, actionable error instead (router maps NO_2FA_PASSWORD).
            if not getattr(pwd, "has_password", False):
                raise ValueError("NO_2FA_PASSWORD")
            # Telegram only starts the email-confirmation flow when the request
            # re-submits the (re-hashed) cloud password in ``new_settings``. An
            # email-ONLY PasswordInputSettings is accepted as a no-op: no
            # EmailUnconfirmedError, no code emailed — the old code then silently
            # returned success and the UI falsely claimed a code was sent (bug).
            # We must re-hash the SAME password, so current_password is required;
            # without it we'd hash "" and REMOVE 2FA. Mirror telethon's own
            # edit_2fa: randomise ``new_algo.salt1`` BEFORE hashing, then include
            # new_algo + new_password_hash + hint + email.
            if not current_password:
                raise ValueError("CURRENT_PASSWORD_REQUIRED")
            srp = compute_check(pwd, current_password)
            pwd.new_algo.salt1 += _os.urandom(32)
            new_password_hash = compute_digest(pwd.new_algo, current_password)
            try:
                await client(
                    UpdatePasswordSettingsRequest(
                        password=srp,
                        new_settings=PasswordInputSettings(
                            new_algo=pwd.new_algo,
                            new_password_hash=new_password_hash,
                            hint=getattr(pwd, "hint", "") or "",
                            email=email,
                            new_secure_settings=None,
                        ),
                    )
                )
            except EmailUnconfirmedError as e:
                # Confirmation code sent by Telegram → pivot to step 2 (Code Example 5).
                return {"code_length": getattr(e, "code_length", None)}
            # No EmailUnconfirmedError means Telegram accepted the settings without
            # emailing a code. With a real email change this should not happen; the
            # only benign case is the email already being the confirmed recovery
            # address. Signal it distinctly so the router does NOT tell the UI to
            # prompt for a code that will never arrive.
            return {"code_length": None, "already_confirmed": True}
        finally:
            if client:
                await self.disconnect_client(client)

    async def set_recovery_email(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        current_password: str | None = None,
        email: str,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Router-facing alias for :meth:`start_recovery_email` (the name the
        recovery-email endpoint calls; delegates to the canonical per-op impl above)."""
        return await self.start_recovery_email(
            sender_slug,
            sender_id,
            encrypted_session,
            current_password=current_password,
            email=email,
            proxy=proxy,
            fingerprint=fingerprint,
        )

    async def confirm_recovery_email(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        *,
        code: str,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Step 2 of the recovery-email change — submit the emailed code via
        ``ConfirmPasswordEmailRequest`` on a FRESH per-op client (the pending-email
        state lives account-side after step 1). Errors PROPAGATE (CodeInvalidError =
        wrong/expired code, FloodWaitError) — the router maps them."""
        from telethon.tl.functions.account import ConfirmPasswordEmailRequest

        client = None
        try:
            client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
            await client(ConfirmPasswordEmailRequest(code=str(code)))
            return {"success": True}
        finally:
            if client:
                await self.disconnect_client(client)

    # ─── Inbox mutation methods (Phase 23 — INBM-01/02/03/05) ────────────────
    # Each clones the send_message_by_telegram_id client-per-op skeleton: create a
    # client via get_client, resolve the peer via _resolve_peer_by_telegram_id, do
    # the op, ALWAYS disconnect_client in finally. Each returns a structured dict so
    # the router logic (gates, ordering, DB writes) is fully testable against mocks.

    async def _resolve_peer_by_telegram_id(self, client: TelegramClient, telegram_id: int):
        """Resolve an input peer by telegram_id: cache → get_dialogs(200) → retry.

        Extracted from the send_message_by_telegram_id cold-cache fix: a bare
        telegram_id may miss Telethon's session entity cache (no access_hash), so
        on ValueError we warm the cache by enumerating recent dialogs and retry.
        Shared by every inbox mutation method in this class.
        """
        try:
            return await client.get_input_entity(telegram_id)
        except ValueError:
            await client.get_dialogs(limit=200)
            return await client.get_input_entity(telegram_id)

    async def edit_message_by_telegram_id(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        telegram_id: int,
        telegram_message_id: int,
        new_text: str,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Edit a previously-sent message (edit-for-everyone).

        The edit window is server-controlled (D-05): we do NOT pre-gate it — we
        attempt the edit and map Telethon's error classes (Pitfall 1). Re-editing
        to identical text raises MessageNotModifiedError → treated as an idempotent
        success no-op, mirroring how set_username treats UsernameNotModifiedError.
        """
        client = None
        try:
            client = await self.get_client(
                sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint
            )
            peer = await self._resolve_peer_by_telegram_id(client, telegram_id)
            await client.edit_message(peer, telegram_message_id, new_text)
            return {"success": True}
        except MessageNotModifiedError:
            # Identical text — idempotent no-op; the edit still "succeeds".
            return {"success": True, "no_op": True}
        except MessageEditTimeExpiredError:
            return {
                "success": False,
                "error": {
                    "code": "MESSAGE_EDIT_TOO_OLD",
                    "message": "Сообщение слишком старое для редактирования"
                }
            }
        except (MessageAuthorRequiredError, MessageIdInvalidError):
            return {
                "success": False,
                "error": {
                    "code": "MESSAGE_NOT_EDITABLE",
                    "message": "Это сообщение нельзя изменить"
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
        except Exception as e:
            if is_frozen_error(e):
                logger.critical(f"Account frozen while editing message: {e}")
                return {
                    "success": False,
                    "error": {
                        "code": "ACCOUNT_FROZEN",
                        "message": "Аккаунт заморожен Telegram (FROZEN_*). Требуется аппеляция."
                    }
                }
            logger.error(f"Error editing message by telegram_id: {e}")
            return {
                "success": False,
                "error": {
                    "code": "MESSAGE_NOT_EDITABLE",
                    "message": str(e)
                }
            }
        finally:
            if client:
                await self.disconnect_client(client)

    async def delete_message_by_telegram_id(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        telegram_id: int,
        telegram_message_id: int,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Delete a message for everyone (revoke=True).

        Deleting an already-gone / own message in a private chat is a SILENT no-op
        in Telegram — delete_messages never raises for it (Pitfall 4). So reaching
        completion IS success; the DB row is the source of truth. We reserve failure
        for connection / flood / frozen / session errors.
        """
        client = None
        try:
            client = await self.get_client(
                sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint
            )
            peer = await self._resolve_peer_by_telegram_id(client, telegram_id)
            await client.delete_messages(peer, [telegram_message_id], revoke=True)
            return {"success": True}
        except FloodWaitError as e:
            return {
                "success": False,
                "error": {
                    "code": "FLOOD_WAIT",
                    "message": f"Rate limited. Retry after {e.seconds} seconds",
                    "retry_after": e.seconds
                }
            }
        except Exception as e:
            if is_frozen_error(e):
                logger.critical(f"Account frozen while deleting message: {e}")
                return {
                    "success": False,
                    "error": {
                        "code": "ACCOUNT_FROZEN",
                        "message": "Аккаунт заморожен Telegram (FROZEN_*). Требуется аппеляция."
                    }
                }
            logger.error(f"Error deleting message by telegram_id: {e}")
            return {
                "success": False,
                "error": {
                    "code": "DELETE_FAILED",
                    "message": str(e)
                }
            }
        finally:
            if client:
                await self.disconnect_client(client)

    async def click_message_button_by_telegram_id(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        telegram_id: int,
        telegram_message_id: int,
        row: int,
        col: int,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Click a button on a message using THIS sender's own Telethon session
        (quick task 260713-jmp).

        `message.click(row, col)` transparently handles BOTH inline callback-query
        buttons (fires the callback) AND custom reply-keyboard text-buttons (sends
        the button's label as a normal message) — we do NOT branch on button type.
        Whatever @SpamBot sends back arrives through the UNCHANGED listener antispam
        path exactly like any other inbound message; there is no special response
        handling here.
        """
        client = None
        try:
            client = await self.get_client(
                sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint
            )
            peer = await self._resolve_peer_by_telegram_id(client, telegram_id)
            msg = await client.get_messages(peer, ids=telegram_message_id)
            if msg is None:
                return {
                    "success": False,
                    "error": {
                        "code": "MESSAGE_NOT_FOUND",
                        "message": "Сообщение не найдено в Telegram"
                    }
                }
            await msg.click(row, col)
            return {"success": True}
        except FloodWaitError as e:
            return {
                "success": False,
                "error": {
                    "code": "FLOOD_WAIT",
                    "message": f"Rate limited. Retry after {e.seconds} seconds",
                    "retry_after": e.seconds
                }
            }
        except Exception as e:
            if is_frozen_error(e):
                logger.critical(f"Account frozen while clicking button: {e}")
                return {
                    "success": False,
                    "error": {
                        "code": "ACCOUNT_FROZEN",
                        "message": "Аккаунт заморожен Telegram (FROZEN_*). Требуется аппеляция."
                    }
                }
            logger.error(f"Error clicking message button by telegram_id: {e}")
            return {
                "success": False,
                "error": {
                    "code": "TELEGRAM_OP_FAILED",
                    "message": str(e)
                }
            }
        finally:
            if client:
                await self.disconnect_client(client)

    async def send_file_by_telegram_id(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        telegram_id: int,
        tmp_path: str,
        file_name: str | None = None,
        caption: str | None = None,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Send a file from the inbox by telegram_id (multipart→temp-file path).

        Unlike the queue ``send_file`` this is the temp-file path: the router owns
        the temp file lifecycle (D-14) — we do NOT unlink ``tmp_path`` here and we do
        NOT download from a URL. Media type is auto-detected (``force_document=False``,
        D-11): photos arrive as photos, not documents (the temp file keeps the
        original extension). A caption over 1024 chars is sent as a separate
        follow-up text message so nothing is lost.
        """
        client = None
        try:
            client = await self.get_client(
                sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint
            )
            peer = await self._resolve_peer_by_telegram_id(client, telegram_id)

            # Telegram caption limit for media is 1024 chars. If the caption
            # exceeds it, send the file without a caption and follow up with a
            # separate text message so the full text is delivered.
            CAPTION_LIMIT = 1024
            file_caption = None
            overflow_text = None
            if caption:
                if len(caption) <= CAPTION_LIMIT:
                    file_caption = caption
                else:
                    overflow_text = caption

            sent = await client.send_file(
                peer,
                tmp_path,
                caption=file_caption,
                # Telethon's send_file has no `file_name` kwarg — it silently
                # swallows unknown kwargs into **kwargs and does nothing with
                # them. The real filename comes from a DocumentAttributeFilename
                # attribute, which overrides the default (derived from tmp_path's
                # basename) only when explicitly passed here.
                attributes=[DocumentAttributeFilename(file_name)] if file_name else None,
                force_document=False,   # D-11 auto-media
            )

            if overflow_text:
                try:
                    await client.send_message(peer, overflow_text)
                except Exception as e:
                    logger.warning(f"File sent but overflow text failed: {e}")

            return {"success": True, "telegram_message_id": sent.id}

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
        except UserIsBlockedError:
            # SRLD-08 (D-15): recipient blocked THIS sender — parity with send_message.
            return {
                "success": False,
                "error": {
                    "code": "USER_IS_BLOCKED",
                    "message": "Получатель заблокировал отправителя"
                }
            }
        except ValueError as e:
            # Peer never resolved even after the get_dialogs retry — the recipient
            # is unreachable from this sender (cold entity).
            logger.error(f"Error sending file by telegram_id (unresolved peer): {e}")
            return {
                "success": False,
                "error": {
                    "code": "RECIPIENT_NOT_IN_TELEGRAM",
                    "message": "Получатель недоступен в Telegram"
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
            if "USER_IS_BLOCKED" in str(e):
                return {
                    "success": False,
                    "error": {
                        "code": "USER_IS_BLOCKED",
                        "message": "Получатель заблокировал отправителя"
                    }
                }
            logger.error(f"Error sending file by telegram_id: {e}")
            return {
                "success": False,
                "error": {
                    "code": "SEND_FAILED",
                    "message": str(e)
                }
            }
        finally:
            if client:
                await self.disconnect_client(client)

    async def download_media_by_telegram_id(
        self,
        sender_slug: str,
        sender_id: str,
        encrypted_session: str,
        telegram_id: int,
        telegram_message_id: int,
        proxy: dict | None = None,
        fingerprint: dict | None = None,
    ) -> dict:
        """Lazily download a message's media by (peer, message_id).

        NEVER uses the deprecated bot-API file identifier (unreliable for user
        accounts — Pitfall 6). The reliable key is the stored ``telegram_message_id``
        + the peer. If the message is gone (contact deleted it) or carries no media,
        the contact is no longer downloadable → MEDIA_UNAVAILABLE.
        """
        client = None
        try:
            client = await self.get_client(
                sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint
            )
            peer = await self._resolve_peer_by_telegram_id(client, telegram_id)

            msg = await client.get_messages(peer, ids=telegram_message_id)
            if not msg or not msg.media:
                return {
                    "success": False,
                    "error": {
                        "code": "MEDIA_UNAVAILABLE",
                        "message": "Файл больше недоступен в Telegram"
                    }
                }

            data = await msg.download_media(file=bytes)
            return {
                "success": True,
                "data": data,
                "mime": (msg.file.mime_type if msg.file else "application/octet-stream"),
                "name": (msg.file.name if msg.file else "file"),
            }

        except FloodWaitError as e:
            return {
                "success": False,
                "error": {
                    "code": "DOWNLOAD_FAILED",
                    "message": f"Rate limited. Retry after {e.seconds} seconds",
                    "retry_after": e.seconds
                }
            }
        except Exception as e:
            if is_frozen_error(e):
                logger.critical(f"Account frozen while downloading media: {e}")
                return {
                    "success": False,
                    "error": {
                        "code": "ACCOUNT_FROZEN",
                        "message": "Аккаунт заморожен Telegram (FROZEN_*). Требуется аппеляция."
                    }
                }
            logger.error(f"Error downloading media by telegram_id: {e}")
            return {
                "success": False,
                "error": {
                    "code": "DOWNLOAD_FAILED",
                    "message": str(e)
                }
            }
        finally:
            if client:
                await self.disconnect_client(client)




# Global instance
telegram_service = TelegramService()
