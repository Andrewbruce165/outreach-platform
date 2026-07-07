"""Bulk Telegram account import — preview / unzip / pair (Phase 21, Plan 21-03).

Step 1 of the two-step flow (D-08a): a fast, synchronous preview that unzips the
uploaded archive in memory, pairs ``<base>.json`` ↔ ``<base>.session`` by basename,
validates each vendor JSON against :class:`VendorAccountJson`, and returns a
``{matched, unpaired, malformed}`` summary. NO Telegram connect happens here — this
module never imports Telethon and never opens a socket.

Later plans extend this same module with the per-account import routine
(``sqlite_to_string_session`` / ``encrypt_twofa`` / ``import_one_account`` — 21-04/05);
those symbols are intentionally NOT defined here yet.

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
import zipfile

from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import get_settings

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
    proxy: dict | None = None  # -> senders.proxy if set, else pool (D-15)
    phone: str | None = None


def build_fingerprint(v: VendorAccountJson) -> dict:
    """Map a validated vendor JSON to the Telethon client fingerprint (D-01).

    NOTE: ``lang_pack`` is deliberately NOT included — ``make_telegram_client`` always
    forces ``'tdesktop'`` (D-04), and ``api_id``/``api_hash`` stay global (D-03).
    """
    return {
        "device_model": v.device,
        "system_version": v.sdk,
        "app_version": v.app_version,
        "lang_code": v.lang_code,
        "system_lang_code": v.system_lang_code,
    }


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
