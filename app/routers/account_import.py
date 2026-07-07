"""Bulk Telegram account import router (Phase 21 — IMPT-01).

Step 1 of the two-step flow (D-08a): ``POST /api/v1/accounts/import/preview``.

The client uploads ONE ZIP of ``<phone>.json`` + ``<phone>.session`` vendor pairs.
This endpoint unzips + pairs + validates SYNCHRONOUSLY (no Telegram connect — cheap
enough for one HTTP request), stages the raw ZIP bytes in ``account_import_stagings``
with a 30-minute TTL (mirrors the ``csv_imports`` BYTEA pattern), and returns a
recognized-set summary so the client can see what was recognized before committing to
the async import (Step 2 = 21-05).

Security (D-07 / RESEARCH Pitfall 9): the response and logs carry ONLY counts, bare
basenames, and boolean flags (``has_2fa`` / ``has_proxy``). The raw ``twoFA`` value and
the ``.session`` bytes are NEVER placed in any response field or log line — the session
bytes live only in the staged ``zip_data`` BYTEA, which the confirm step re-reads.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import AccountImportStaging
from app.services.account_import import ImportZipError, unpack_and_pair
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/accounts", tags=["account-import"])

# Staging TTL between the preview (step 1) and the async confirm (step 2, 21-05).
_STAGING_TTL_MINUTES = 30


# ─── Response models (co-located, like knowledge_bases.py) ──────────────────────


class PreviewMatchedItem(BaseModel):
    """A recognized .json↔.session pair. Carries flags only — never the twoFA value
    nor the session bytes."""

    basename: str
    phone: str
    has_2fa: bool
    has_proxy: bool


class PreviewUnpairedItem(BaseModel):
    """A file present without its partner (json without session, or vice versa)."""

    basename: str
    filename: str


class PreviewMalformedItem(BaseModel):
    """A .json that failed to parse or failed schema validation."""

    basename: str
    filename: str
    reason: str


class AccountImportPreviewResponse(BaseModel):
    import_id: UUID
    matched: list[PreviewMatchedItem]
    unpaired: list[PreviewUnpairedItem]
    malformed: list[PreviewMalformedItem]


# ─── POST /api/v1/accounts/import/preview (multipart ZIP) ───────────────────────


@router.post("/import/preview", response_model=AccountImportPreviewResponse)
async def import_preview(
    file: UploadFile = File(...),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Unzip + pair + validate a bulk-import ZIP synchronously; stage it with a TTL.

    Returns ``import_id`` + matched/unpaired/malformed. NO Telegram connect happens.
    """
    raw = await file.read()

    # Compressed-size fast guard (the uncompressed guard lives in unpack_and_pair).
    if len(raw) > settings.max_import_uncompressed_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": f"Max {settings.max_import_uncompressed_bytes} bytes",
            },
        )

    try:
        result = unpack_and_pair(raw)
    except ImportZipError as exc:
        # ZIP-bomb / traversal / oversized batch / undecodable ZIP → structured 4xx,
        # never a 500. code + http_status carried on the exception subclass.
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": str(exc)},
        )

    # Build a secrets-free view: only bare basenames, flags, and reasons. The twoFA
    # value and the .session bytes stay out of the response AND the stored summary.
    matched = [
        PreviewMatchedItem(
            basename=m["basename"],
            phone=m["basename"],  # vendor basename IS the phone (e.g. +18646884306)
            has_2fa=bool(m["json"].get("twoFA")),
            has_proxy=bool(m["json"].get("proxy")),
        )
        for m in result["matched"]
    ]
    unpaired = [
        PreviewUnpairedItem(basename=u["basename"], filename=u["filename"])
        for u in result["unpaired"]
    ]
    malformed = [
        PreviewMalformedItem(
            basename=m["basename"], filename=m["filename"], reason=m["reason"]
        )
        for m in result["malformed"]
    ]

    summary = {
        "counts": {
            "matched": len(matched),
            "unpaired": len(unpaired),
            "malformed": len(malformed),
        },
        "matched": [m.model_dump() for m in matched],
        "unpaired": [u.model_dump() for u in unpaired],
        "malformed": [m.model_dump() for m in malformed],
    }

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_STAGING_TTL_MINUTES)
    staging = AccountImportStaging(
        workspace_id=ctx.workspace_id,
        zip_data=raw,
        summary=summary,
        expires_at=expires_at,
    )
    db.add(staging)
    await db.flush()
    await db.commit()

    logger.info(
        "[account-import] preview workspace=%s import_id=%s "
        "matched=%d unpaired=%d malformed=%d expires_at=%s",
        ctx.workspace_id,
        staging.id,
        len(matched),
        len(unpaired),
        len(malformed),
        expires_at,
    )

    return AccountImportPreviewResponse(
        import_id=staging.id,
        matched=matched,
        unpaired=unpaired,
        malformed=malformed,
    )
