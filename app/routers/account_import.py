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
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import (
    AccountImportItem,
    AccountImportJob,
    AccountImportStaging,
)
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


# ─── Step 2 (D-09): confirm → async job + status poll (Plan 21-05) ──────────────
#
# ``POST /import/{import_id}/confirm`` re-reads the staged ZIP, fans the matched pairs
# into ``account_import_items`` (one row per pair, carrying its own session bytes +
# parsed JSON), creates ONE ``account_import_jobs`` row, and returns ``job_id`` (202)
# immediately — the AccountImportWorker drains the items in the background. The role is
# chosen ONCE for the whole batch (D-16). ``GET /import/{job_id}/status`` polls progress.
#
# Security (D-07 / RESEARCH Pitfall 9): the status response carries ONLY bare basenames +
# status/result/reason — never the ``session_blob``, the ``vendor_json`` (which holds the
# twoFA value), or any other secret.


class ImportConfirmRequest(BaseModel):
    """Body for confirm. Role is chosen once for the whole batch (D-16); an invalid
    value is rejected by the ``Literal`` type (structured 422)."""

    role: Literal['sender', 'checker']


class ImportConfirmResponse(BaseModel):
    job_id: UUID
    total: int


class ImportStatusItem(BaseModel):
    """A single per-file outcome — NEVER carries session bytes or the twoFA value."""

    basename: str
    status: str
    result: Optional[str] = None
    reason: Optional[str] = None


class ImportStatusResponse(BaseModel):
    job_id: UUID
    status: str
    total: int
    processed: int
    items: list[ImportStatusItem]


@router.post(
    "/import/{import_id}/confirm",
    response_model=ImportConfirmResponse,
    status_code=202,
)
async def import_confirm(
    import_id: UUID,
    payload: ImportConfirmRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Turn a staged preview into a background import job (returns ``job_id``, 202).

    Re-reads the staged ZIP, pairs it again, and creates ONE job + N pending items (one
    per matched pair). NO Telegram connect happens here — the AccountImportWorker does the
    per-account work. Unknown staging → 404 ``IMPORT_NOT_FOUND``; expired → 410
    ``IMPORT_EXPIRED``. Double-submit is allowed (a fresh job each time — the worker dedups
    per phone/telegram_id, so a re-confirm never creates duplicate senders; D-14/IMPT-06).
    """
    staging = (
        await db.execute(
            select(AccountImportStaging).where(
                AccountImportStaging.id == import_id,
                AccountImportStaging.workspace_id == ctx.workspace_id,
                # TODO(v2-rls): replaced by RLS policy
            )
        )
    ).scalars().first()
    if staging is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "IMPORT_NOT_FOUND", "message": "Import session not found"},
        )

    # Postgres stores tz-aware; a defensive naive→UTC coercion mirrors contacts.import.
    expires_at = staging.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=410,
            detail={"code": "IMPORT_EXPIRED", "message": "Import session expired"},
        )

    # Re-pair the staged ZIP. It was validated at preview, but a defensive re-read keeps a
    # corrupt/oversized blob a structured 4xx (never a 500).
    try:
        result = unpack_and_pair(staging.zip_data)
    except ImportZipError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": str(exc)},
        )

    matched = result["matched"]

    job = AccountImportJob(
        workspace_id=ctx.workspace_id,
        staging_id=import_id,
        role=payload.role,
        status="running",
        total=len(matched),
        processed=0,
    )
    db.add(job)
    await db.flush()  # populate job.id for the FK below

    # One pending item per matched pair — each carries its own session bytes + parsed JSON
    # so the worker never re-unzips. Unpaired/malformed were reported at preview and are
    # intentionally NOT imported, so total == number of items actually created.
    for m in matched:
        db.add(
            AccountImportItem(
                job_id=job.id,
                workspace_id=ctx.workspace_id,
                basename=m["basename"],
                session_blob=m["session_bytes"],
                vendor_json=m["json"],
                status="pending",
            )
        )

    await db.commit()

    logger.info(
        "[account-import] confirm workspace=%s import_id=%s job_id=%s role=%s total=%d",
        ctx.workspace_id,
        import_id,
        job.id,
        payload.role,
        len(matched),
    )

    return ImportConfirmResponse(job_id=job.id, total=job.total)


@router.get("/import/{job_id}/status", response_model=ImportStatusResponse)
async def import_status(
    job_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Poll an import job's progress: ``processed``/``total`` + a per-file result list.

    The item payload is secrets-free by construction — only basename/status/result/reason.
    ``processed`` is the worker-maintained counter; when it reaches ``total`` the worker has
    flipped ``status`` → ``done`` (whatever the row currently says is returned).
    """
    job = (
        await db.execute(
            select(AccountImportJob).where(
                AccountImportJob.id == job_id,
                AccountImportJob.workspace_id == ctx.workspace_id,
                # TODO(v2-rls): replaced by RLS policy
            )
        )
    ).scalars().first()
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": "Import job not found"},
        )

    items = (
        await db.execute(
            select(AccountImportItem)
            .where(
                AccountImportItem.job_id == job_id,
                AccountImportItem.workspace_id == ctx.workspace_id,
            )
            .order_by(AccountImportItem.created_at.asc())
        )
    ).scalars().all()

    return ImportStatusResponse(
        job_id=job.id,
        status=job.status,
        total=job.total,
        processed=job.processed,
        items=[
            ImportStatusItem(
                basename=i.basename,
                status=i.status,
                result=i.result,
                reason=i.reason,
            )
            for i in items
        ],
    )
