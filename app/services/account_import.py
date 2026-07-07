"""Bulk Telegram account import — preview / unzip / pair (Phase 21, Plan 21-03).

Step 1 of the two-step flow (D-08a): a fast, synchronous preview that unzips the
uploaded archive in memory, pairs ``<base>.json`` ↔ ``<base>.session`` by basename,
validates each vendor JSON against :class:`VendorAccountJson`, and returns a
``{matched, unpaired, malformed}`` summary. The preview / unzip / pair path performs
NO Telegram connect and opens no socket.

Plan 21-04 adds the per-account import routine to this same module:
  * :func:`sqlite_to_string_session` — OFFLINE SQLite ``.session`` → ``StringSession``
    (Telethon loads the file locally; no network);
  * :func:`encrypt_twofa` — Fernet-encrypt the vendor 2FA password at rest (D-05);
  * :func:`resolve_import_proxy` — ALWAYS a free ProxyPool row; the vendor JSON proxy is
    ignored (dead/exhausted in practice). Returns the row id so the caller can mark it
    taken after the sender is created;
  * :func:`import_one_account` — convert → connect under the account's own fingerprint
    → ``get_me`` → dedup → create exactly one ``active`` sender, or skip+report — never
    raising into the batch (D-10). This is the only path here that opens a socket, and
    only via the shared :func:`make_telegram_client` seam (stubbed out in tests).

Security (RESEARCH Pitfall 7 — ZIP bomb / path traversal / huge batch):
  * every member name is reduced to ``os.path.basename`` — the archived path is never
    trusted, and any member with an absolute path or a ``..`` component is rejected
    BEFORE any bytes are read;
  * the total *uncompressed* size (summed from ``ZipInfo.file_size``) is capped by
    ``settings.max_import_uncompressed_bytes`` (default 50 MB);
  * the number of distinct account basenames is capped by
    ``settings.max_import_accounts`` (default 500).
All three raise a subclass of :class:`ImportZipError` that the router maps to a
structured 413/422 (never a 500).
"""

import io
import json
import logging
import os
import sqlite3
import tempfile
import zipfile
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from telethon.crypto import AuthKey
from telethon.errors import UserDeactivatedBanError
from telethon.sessions import StringSession

from app.config import get_settings
from app.models import AccountImportJob, ProxyPool, Sender
from app.services.encryption import encrypt_session
from app.services.telegram import AUTH_ERRORS, make_telegram_client

logger = logging.getLogger(__name__)
settings = get_settings()


# ─── Structured ZIP-safety errors (router maps .code / .http_status) ────────────


class ImportZipError(ValueError):
    """Base for a rejected/undecodable archive — mapped to a structured 4xx."""

    code = "BAD_ZIP"
    http_status = 422


class ZipTooLargeError(ImportZipError):
    """Uncompressed contents exceed the configured cap (ZIP-bomb guard)."""

    code = "ZIP_TOO_LARGE"
    http_status = 413


class TooManyAccountsError(ImportZipError):
    """More distinct account basenames than the per-batch cap allows."""

    code = "TOO_MANY_ACCOUNTS"
    http_status = 422


# ─── Vendor JSON schema (verified against the real +18646884306.json sample) ────


class VendorAccountJson(BaseModel):
    """The subset of the vendor account JSON the import routine actually needs.

    ``extra="ignore"`` drops everything else the vendor ships (``app_id``/``app_hash``
    are intentionally NOT declared → ignored per D-03; ``id``/``phone``/``username``
    come from ``get_me()`` per D-11). ``session_file`` is REQUIRED — it is the shared
    basename and the authoritative pairing key. When a real record omits it,
    :func:`unpack_and_pair` injects the archived filename basename before validating.
    """

    model_config = ConfigDict(extra="ignore")

    session_file: str  # REQUIRED — shared basename, the pairing key
    device: str | None = None  # -> device_model
    sdk: str | None = None  # -> system_version
    app_version: str | None = None  # -> app_version
    lang_code: str | None = None
    system_lang_code: str | None = None
    twoFA: str | None = None  # -> Fernet-encrypt into twofa_password_enc (D-05)
    # Accept ANY proxy shape (canonical dict, PySocks-style list, "host:port[:u:p]"
    # string) — normalized later by _normalize_vendor_proxy. Kept as ``Any`` so a
    # never-before-seen vendor shape can NEVER fail validation and silently drop the
    # account; an unusable shape just falls back to the pool (D-15).
    proxy: Any = None  # -> senders.proxy if set, else pool (D-15)
    phone: str | None = None


def build_fingerprint(v: VendorAccountJson) -> dict:
    """Map a validated vendor JSON to the Telethon client fingerprint (D-01).

    NOTE: ``lang_pack`` is deliberately NOT included — ``make_telegram_client`` always
    forces ``'tdesktop'`` (D-04), and ``api_id``/``api_hash`` stay global (D-03).

    Keys whose vendor value is None are OMITTED (not emitted as None) so the client factory
    keeps its global default for that field — some vendor JSONs ship only ``lang_pack`` and
    leave ``lang_code``/``system_lang_code`` unset, and a None there breaks Telethon's
    ``initConnection`` serialization.
    """
    fields = {
        "device_model": v.device,
        "system_version": v.sdk,
        "app_version": v.app_version,
        "lang_code": v.lang_code,
        "system_lang_code": v.system_lang_code,
    }
    return {k: val for k, val in fields.items() if val is not None}


# ─── Unzip + pair by basename (no Telegram connect) ─────────────────────────────


def _safe_basename(member_name: str) -> str:
    """Reduce an archive member name to a trusted basename or reject it.

    Rejects absolute paths and any name containing a ``..`` path component before a
    single byte is read (RESEARCH Pitfall 7). Returns the plain basename otherwise.
    """
    normalized = member_name.replace("\\", "/")
    if os.path.isabs(normalized) or normalized.startswith("/"):
        raise ImportZipError(f"rejected absolute path in archive: {member_name!r}")
    parts = normalized.split("/")
    if ".." in parts:
        raise ImportZipError(f"rejected path traversal in archive: {member_name!r}")
    return os.path.basename(normalized)


def unpack_and_pair(zip_bytes: bytes) -> dict:
    """Unzip in memory and pair ``<base>.json`` ↔ ``<base>.session`` by basename.

    Returns ``{"matched": [...], "unpaired": [...], "malformed": [...]}`` where each
    entry is a dict carrying a bare ``basename`` (extension stripped) plus:
      * matched  → ``{"basename", "json": <parsed dict>, "session_bytes": <bytes>}``
      * unpaired → ``{"basename", "filename"}`` (json without session, or vice versa)
      * malformed→ ``{"basename", "filename", "reason"}`` (bad JSON / schema-invalid)

    Never connects to Telegram. Raises a subclass of :class:`ImportZipError` for an
    undecodable / oversized / over-count archive (the router maps it to a 4xx).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ImportZipError(f"not a valid ZIP archive: {exc}") from exc

    with zf:
        infos = zf.infolist()

        # 1) ZIP-bomb guard — sum uncompressed sizes BEFORE reading any content.
        total_uncompressed = sum(zi.file_size for zi in infos)
        if total_uncompressed > settings.max_import_uncompressed_bytes:
            raise ZipTooLargeError(
                f"uncompressed contents {total_uncompressed} bytes exceed the "
                f"{settings.max_import_uncompressed_bytes}-byte limit"
            )

        # 2) Group members by trusted basename (path-traversal rejected here).
        jsons: dict[str, zipfile.ZipInfo] = {}
        sessions: dict[str, zipfile.ZipInfo] = {}
        for zi in infos:
            if zi.is_dir():
                continue
            base_with_ext = _safe_basename(zi.filename)
            if not base_with_ext:
                continue
            stem, ext = os.path.splitext(base_with_ext)
            ext = ext.lower()
            if ext == ".json":
                jsons[stem] = zi
            elif ext == ".session":
                sessions[stem] = zi
            # any other extension is ignored (not part of a pair)

        # 3) Max-accounts-per-batch guard on the distinct basename count.
        distinct = set(jsons) | set(sessions)
        if len(distinct) > settings.max_import_accounts:
            raise TooManyAccountsError(
                f"{len(distinct)} accounts exceed the "
                f"{settings.max_import_accounts}-account per-batch limit"
            )

        matched: list[dict] = []
        unpaired: list[dict] = []
        malformed: list[dict] = []

        for stem in sorted(distinct):
            json_info = jsons.get(stem)
            session_info = sessions.get(stem)

            if json_info is not None:
                json_name = os.path.basename(json_info.filename)
                raw = zf.read(json_info)
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                    malformed.append(
                        {"basename": stem, "filename": json_name, "reason": "invalid JSON"}
                    )
                    continue
                if not isinstance(data, dict):
                    malformed.append(
                        {"basename": stem, "filename": json_name, "reason": "JSON is not an object"}
                    )
                    continue
                # session_file is authoritative when present; fall back to the
                # archived filename basename so a record omitting it still validates.
                data.setdefault("session_file", stem)
                try:
                    vendor = VendorAccountJson.model_validate(data)
                except ValidationError as exc:
                    malformed.append(
                        {
                            "basename": stem,
                            "filename": json_name,
                            "reason": f"schema validation failed: {exc.error_count()} error(s)",
                        }
                    )
                    continue

                if session_info is not None:
                    matched.append(
                        {
                            "basename": vendor.session_file or stem,
                            "json": data,
                            "session_bytes": zf.read(session_info),
                        }
                    )
                else:
                    unpaired.append({"basename": stem, "filename": json_name})
            else:
                # A .session with no matching .json.
                session_name = os.path.basename(session_info.filename)
                unpaired.append({"basename": stem, "filename": session_name})

    return {"matched": matched, "unpaired": unpaired, "malformed": malformed}


# ─── Per-account import routine (Plan 21-04) ────────────────────────────────────
#
# Security invariant (RESEARCH Pitfall 9): the raw vendor ``.session`` bytes, the
# decrypted StringSession, the 2FA plaintext and the account auth_key are NEVER
# logged anywhere in this module — only a masked phone prefix + slug/tg_id + result.


def _mask_phone(value: str | None) -> str:
    """Return a log-safe phone prefix (``+1864***``) — never the full number."""
    s = str(value or "")
    return (s[:6] + "***") if s else "***"


def sqlite_to_string_session(session_bytes: bytes) -> str:
    """Convert vendor SQLite ``.session`` bytes to a ``StringSession`` string, OFFLINE.

    Reads ``dc_id / server_address / port / auth_key`` from the ``sessions`` table by
    EXPLICIT column name and rebuilds a :class:`~telethon.sessions.StringSession` in
    memory — no ``connect()``, no socket. We deliberately do NOT hand the file to
    Telethon's own ``SQLiteSession``: that class runs ``SELECT * FROM sessions`` and
    unpacks exactly 5 values, so any vendor that ships an extra column (seen in the
    wild: a 6th ``tmp_auth_key`` column written by a patched/forked Telethon) makes it
    raise ``too many values to unpack`` even though the auth_key is perfectly valid.
    Naming the columns is tolerant of such schema variants.

    The bytes are written to a unique temp path ENDING in ``.session``, ``chmod 0600``.
    Raises :class:`ValueError` (``empty_or_invalid_session``) when the file is empty /
    not a database / lacks a ``sessions`` row with a usable auth_key + DC — the caller
    fails that one item, never the batch. The temp file (and any SQLite journal/WAL
    side files) is deleted in a ``finally`` so vendor session bytes never linger on
    disk (Pitfall 9).
    """
    fd, path = tempfile.mkstemp(suffix=".session")
    side_files = [path, path + "-journal", path + "-wal", path + "-shm"]
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(session_bytes or b"")
        os.chmod(path, 0o600)

        try:
            con = sqlite3.connect(path)
            try:
                # Explicit columns → tolerant of extra vendor columns (tmp_auth_key etc.).
                row = con.execute(
                    "SELECT dc_id, server_address, port, auth_key "
                    "FROM sessions WHERE auth_key IS NOT NULL LIMIT 1"
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error as exc:
            # "file is not a database" / no sessions table / non-Telethon schema.
            raise ValueError("empty_or_invalid_session") from exc

        # Empty but valid sqlite (fresh DB), or a row without a usable auth_key + DC →
        # no live account behind it.
        if not row:
            raise ValueError("empty_or_invalid_session")
        dc_id, server_address, port, auth_key = row
        if not auth_key or not server_address or not port:
            raise ValueError("empty_or_invalid_session")

        sess = StringSession()
        sess.set_dc(dc_id, server_address, port)
        sess.auth_key = AuthKey(data=bytes(auth_key))
        return sess.save()
    finally:
        for p in side_files:
            try:
                os.remove(p)
            except OSError:
                pass


def encrypt_twofa(twofa: str | None) -> str | None:
    """Fernet-encrypt a vendor 2FA password for storage (D-05). ``None`` → ``None``.

    Reuses the shared session Fernet path (``app.services.encryption``) — one key, one
    code path. The ciphertext is stored in ``senders.twofa_password_enc`` and is never
    logged or returned as plaintext (D-07).
    """
    if not twofa:
        return None
    return encrypt_session(twofa)


# Map a PySocks-style integer proxy type (as shipped in a vendor list) to our string.
_VENDOR_PROXY_INT_TYPE = {1: "socks4", 2: "socks5", 3: "http"}


def _normalize_vendor_proxy(raw: Any) -> dict | None:
    """Coerce a vendor ``proxy`` value to our canonical ``{type,host,port,...}`` dict.

    Vendors ship the proxy in several shapes — a canonical dict, a PySocks-style list
    ``[type, host, port, rdns?, user?, pass?]`` (``type`` an int code or a string), or a
    ``"host:port[:user:pass]"`` string. Returns the canonical dict, or ``None`` for an
    empty / unrecognized shape (the account still imports — it just falls back to the
    workspace proxy pool per D-15; a proxy is never a reason to drop an account).
    """
    if not raw:
        return None

    if isinstance(raw, dict):
        return raw if raw.get("host") else None

    if isinstance(raw, (list, tuple)):
        if len(raw) < 3:
            return None
        ptype, host = raw[0], raw[1]
        ptype = ptype.lower() if isinstance(ptype, str) else _VENDOR_PROXY_INT_TYPE.get(ptype)
        if ptype not in ("socks5", "socks4", "http") or not host:
            return None
        try:
            port = int(raw[2])
        except (TypeError, ValueError):
            return None
        out: dict = {"type": ptype, "host": str(host), "port": port}
        username = raw[4] if len(raw) > 4 else None
        if username:
            out["username"] = username
            out["password"] = (raw[5] if len(raw) > 5 else "") or ""
        return out

    if isinstance(raw, str):
        parts = raw.strip().split(":")
        if len(parts) < 2:
            return None
        try:
            port = int(parts[1])
        except (TypeError, ValueError):
            return None
        out = {"type": "socks5", "host": parts[0], "port": port}
        if len(parts) >= 4:
            out["username"] = parts[2]
            out["password"] = parts[3]
        return out

    return None


async def resolve_import_proxy(db, workspace_id, json_proxy: Any = None):
    """Resolve the proxy for an imported account → ``(proxy_dict | None, pool_row_id | None)``.

    **Policy (2026-07-07 — overrides D-15):** the vendor-supplied ``json_proxy`` is IGNORED.
    Imported accounts ALWAYS route through a workspace-owned :class:`ProxyPool` row. The
    residential proxies shipped inside the vendor archives (e.g. ``proxyshard``) proved to
    be subscription-exhausted / dead (SOCKS ``402: user reached limit`` → hard timeout), so
    honouring them only produced ``connect_failed`` and stranded the accounts. ``json_proxy``
    is still accepted for signature compatibility, but no longer selected.

    * Take one FREE :class:`ProxyPool` row for the workspace
      (``assigned_to_sender_id IS NULL``) → ``(socks5-dict, row.id)``. The row id lets the
      caller mark that EXACT pool row ``assigned_to_sender_id`` AFTER the sender exists
      (Warning-1 contract gap fix) — this function only READS, never writes.
    * Empty pool → ``(None, None)`` (proxy is optional — do NOT hard-fail; logged so an
      exhausted pool is visible in the account-import logs).
    """
    row = (
        await db.execute(
            select(ProxyPool)
            .where(
                ProxyPool.workspace_id == workspace_id,
                ProxyPool.assigned_to_sender_id.is_(None),
            )
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        logger.warning(
            "[account-import] proxy pool empty for workspace %s — account will connect "
            "WITHOUT a proxy (direct from server IP)",
            workspace_id,
        )
        return None, None
    return (
        {
            "type": "socks5",
            "host": row.host,
            "port": row.port,
            "username": row.username,
            "password": row.password,
        },
        row.id,
    )


def _as_vendor_dict(value) -> dict:
    """Coerce a stored ``vendor_json`` value (dict / JSON str / bytes / None) to a dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


async def _job_role(db, job_id) -> str:
    """The batch role ('sender' | 'checker') for an item's job; 'sender' if unknown."""
    if job_id is None:
        return "sender"
    role = (
        await db.execute(select(AccountImportJob.role).where(AccountImportJob.id == job_id))
    ).scalar_one_or_none()
    return role or "sender"


async def import_one_account(db, item) -> str:
    """Import ONE file pair (an ``account_import_items`` row dict) → a result-code string.

    Returns one of ``imported`` | ``already_connected`` | ``malformed_json`` |
    ``convert_failed`` | ``not_authorized`` | ``auth_failed`` | ``banned`` |
    ``connect_failed`` | ``failed`` and NEVER raises for a per-account failure (D-10) —
    the worker (21-05) persists the returned code onto the item row. ``item`` carries its
    own ``session_blob`` (bytes) + ``vendor_json`` so no re-unzip is needed.

    Flow:
      1. Re-validate the vendor JSON (``session_file`` injected from ``basename``) —
         malformed → ``malformed_json``.
      2. Dedup BEFORE loading the session (D-14 — never overwrite a live account): a
         sender already exists in the workspace with this phone → ``already_connected``,
         its ``session_string`` untouched (and a garbage session is never even loaded).
      3. Offline ``sqlite_to_string_session`` — a broken/empty session fails THIS item
         (``convert_failed``), not the batch.
      4. Connect under the account's OWN fingerprint (21-02 seam), ``get_me``, disconnect
         in a ``finally`` (Pitfall 5). Dead session → ``auth_failed``/``banned``/
         ``not_authorized``.
      5. Authoritative dedup by ``telegram_id`` (IMPT-06) — same account under a
         different filename is still a duplicate → ``already_connected``.
      6. Create exactly one ``active`` sender (fingerprint / Fernet-2FA / proxy / none),
         mark the exact free pool row taken (only when a pool proxy was used), and
         recover an INSERT race as ``already_connected`` (Pitfall 8). No @SpamBot probe —
         ``restriction_status`` defaults to ``'none'`` via server_default (D-11).
    """
    workspace_id = item.get("workspace_id")
    basename = item.get("basename") or ""
    session_bytes = item.get("session_blob")
    masked = _mask_phone(basename)

    try:
        vendor_dict = _as_vendor_dict(item.get("vendor_json"))
        # session_file is REQUIRED on the schema; the item stores the vendor JSON as-is,
        # so inject the archived basename as the authoritative fallback before validating.
        vendor_dict.setdefault("session_file", basename)
        try:
            vendor = VendorAccountJson.model_validate(vendor_dict)
        except ValidationError:
            logger.warning("[account-import] malformed vendor JSON for %s", masked)
            return "malformed_json"

        # (2) Pre-connect dedup by phone (D-14): skip a known account WITHOUT loading its
        # session — never overwrite the existing (live) session_string.
        existing = (
            await db.execute(
                select(Sender).where(
                    Sender.workspace_id == workspace_id, Sender.phone == basename
                )
            )
        ).scalars().first()
        if existing is not None:
            logger.info("[account-import] skip %s -> already_connected (phone match)", masked)
            return "already_connected"

        # (3) Offline SQLite -> StringSession — fails this item only.
        try:
            string = sqlite_to_string_session(session_bytes or b"")
        except Exception as exc:  # noqa: BLE001 — ValueError + corrupt-sqlite variants
            logger.warning("[account-import] convert_failed for %s: %s", masked, exc)
            return "convert_failed"

        role = await _job_role(db, item.get("job_id"))
        proxy, proxy_pool_id = await resolve_import_proxy(db, workspace_id, vendor.proxy)

        # (4) Connect under the account's OWN fingerprint (21-02 seam) — the stub replaces
        # make_telegram_client in tests, so no socket is opened there.
        client = make_telegram_client(
            StringSession(string), proxy=proxy, fingerprint=build_fingerprint(vendor)
        )
        me = None
        try:
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    logger.warning("[account-import] not_authorized for %s", masked)
                    return "not_authorized"
                me = await client.get_me()
            except AUTH_ERRORS as exc:
                logger.warning("[account-import] auth_failed for %s: %s", masked, exc)
                return "auth_failed"
            except UserDeactivatedBanError as exc:
                logger.warning("[account-import] banned for %s: %s", masked, exc)
                return "banned"
            except Exception as exc:  # noqa: BLE001 — never raise into the batch
                logger.warning("[account-import] connect_failed for %s: %s", masked, exc)
                return "connect_failed"
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 — best-effort cleanup (Pitfall 5)
                pass

        if me is None:
            logger.warning("[account-import] get_me returned nothing for %s", masked)
            return "connect_failed"

        tg_id = getattr(me, "id", None)
        tg_username = getattr(me, "username", None)
        tg_premium = bool(getattr(me, "premium", False))  # mig 052: Premium badge
        first_name = getattr(me, "first_name", "") or ""
        slug = f"sender-{tg_id}" if tg_id is not None else f"sender-{basename}"

        # (5) Authoritative dedup by telegram_id (IMPT-06) — same account, any filename.
        if tg_id is not None:
            dup = (
                await db.execute(
                    select(Sender).where(
                        Sender.workspace_id == workspace_id, Sender.telegram_id == tg_id
                    )
                )
            ).scalars().first()
            if dup is not None:
                logger.info(
                    "[account-import] skip %s -> already_connected (tg_id match)", masked
                )
                return "already_connected"

        # (6) Create exactly one active sender. restriction_status defaults to 'none' via
        # server_default (D-11) — imported accounts start active/none, no @SpamBot probe.
        sender = Sender(
            workspace_id=workspace_id,
            slug=slug,
            name=first_name or slug,
            phone=basename,
            telegram_id=tg_id,
            session_string=encrypt_session(string),
            role=role,
            proxy=proxy,
            client_fingerprint=build_fingerprint(vendor),
            twofa_password_enc=encrypt_twofa(vendor.twoFA),
            auth_status="ok",
            lifecycle_status="active",
            tg_username=tg_username,
            tg_premium=tg_premium,
        )
        db.add(sender)
        try:
            await db.flush()
        except IntegrityError:
            # Race: a concurrent import created (workspace_id, slug) between the dedup
            # SELECT and this flush (Pitfall 8) → recover as already_connected, never 500.
            await db.rollback()
            raced = (
                await db.execute(
                    select(Sender).where(
                        Sender.workspace_id == workspace_id, Sender.slug == slug
                    )
                )
            ).scalars().first()
            if raced is None:
                return "failed"
            logger.warning("[account-import] INSERT raced on slug=%s -> already_connected", slug)
            return "already_connected"

        # Mark the EXACT free pool row taken — only when a pool proxy was used. A
        # JSON-supplied proxy (proxy_pool_id is None) touches no pool row.
        if proxy_pool_id is not None:
            await db.execute(
                update(ProxyPool)
                .where(ProxyPool.id == proxy_pool_id)
                .values(assigned_to_sender_id=sender.id)
            )

        await db.commit()
        logger.info("[account-import] imported %s slug=%s tg_id=%s", masked, slug, tg_id)
        return "imported"
    except Exception as exc:  # noqa: BLE001 — a per-account failure must never break the batch
        logger.warning("[account-import] unexpected failure for %s: %s", masked, exc)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return "failed"
