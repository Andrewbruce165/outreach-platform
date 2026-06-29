"""Folders router (Phase 2 — FLDR-01, FLDR-02).

Endpoints:
    GET    /api/v1/folders                          — list workspace folders + contact_count
    POST   /api/v1/folders                          — create folder
    GET    /api/v1/folders/{id}                     — single folder
    PATCH  /api/v1/folders/{id}                     — rename
    DELETE /api/v1/folders/{id}[?force=true]        — delete; 409 if not empty, force cascades

All endpoints workspace-scoped через Depends(auth_dep) + WHERE workspace_id == ctx.workspace_id.

FLDR-03 (auto-create by folder_name during CSV import / push) реализуется в plan 02-04
(contacts router/CSV service) через переиспользование helper'а get_or_create_by_name(...)
из этого файла.
"""

import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sql_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Contact, Folder
from app.schemas import (
    FolderCreate,
    FolderResponse,
    FolderStatsResponse,
    FolderUpdate,
)
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/folders", tags=["folders"])


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def get_or_create_by_name(
    db: AsyncSession, workspace_id: UUID, name: str
) -> UUID:
    """D-09 helper: auto-create folder by name (used in contacts router / CSV import).

    Idempotent through Postgres ON CONFLICT — safe under race conditions
    (RESEARCH Pitfall 4: two parallel CSV imports with the same folder_name).
    Returns the folder id (existing or newly created).
    """
    row = await db.execute(
        text(
            """
            INSERT INTO folders (workspace_id, name)
            VALUES (:wid, :name)
            ON CONFLICT (workspace_id, name) DO UPDATE SET updated_at = NOW()
            RETURNING id
            """
        ),
        {"wid": str(workspace_id), "name": name.strip()},
    )
    return row.scalar()


async def _folder_to_response(db: AsyncSession, folder: Folder) -> FolderResponse:
    """Build FolderResponse including computed contact_count."""
    count_result = await db.execute(
        select(sql_func.count(Contact.id)).where(Contact.folder_id == folder.id)
    )
    contact_count = count_result.scalar() or 0
    return FolderResponse(
        id=folder.id,
        name=folder.name,
        contact_count=contact_count,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get("", response_model=List[FolderResponse])
async def list_folders(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """List all folders in the current workspace (with contact_count for each)."""
    result = await db.execute(
        select(Folder)
        .where(Folder.workspace_id == ctx.workspace_id)
        # TODO(v2-rls): replaced by RLS policy app.workspace_id
        .order_by(Folder.created_at.desc())
    )
    folders = result.scalars().all()
    return [await _folder_to_response(db, f) for f in folders]


@router.post("", response_model=FolderResponse, status_code=201)
async def create_folder(
    payload: FolderCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Create a new folder in current workspace. Name must be unique within workspace."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_NAME", "message": "Folder name cannot be empty"},
        )
    # Friendlier duplicate detection than letting IntegrityError bubble up
    existing = await db.execute(
        select(Folder).where(
            Folder.workspace_id == ctx.workspace_id,
            Folder.name == name,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FOLDER_NAME_DUPLICATE",
                "message": f"Folder '{name}' already exists",
            },
        )
    folder = Folder(workspace_id=ctx.workspace_id, name=name)
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    logger.info(
        f"[folders] created workspace={ctx.workspace_id} "
        f"name='{name}' id={folder.id}"
    )
    return await _folder_to_response(db, folder)


@router.get("/{folder_id}", response_model=FolderResponse)
async def get_folder(
    folder_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Get a single folder by id (workspace-scoped). 404 if cross-tenant."""
    result = await db.execute(
        select(Folder).where(
            Folder.id == folder_id,
            Folder.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    folder = result.scalars().first()
    if folder is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
        )
    return await _folder_to_response(db, folder)


# tg_status → stat-card bucket. Mirrors the frontend classifiers in contacts.tsx.
_IN_TELEGRAM_STATUSES = ("registered", "ok", "found", "in_telegram")
_CHECKING_STATUSES = ("pending", "checking", "unknown", "unchecked", "")
_NOT_FOUND_STATUSES = ("not_registered", "not_found", "privacy", "missing", "error")


@router.get("/{folder_id}/stats", response_model=FolderStatsResponse)
async def folder_stats(
    folder_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Folder-wide Telegram-status breakdown for the /contacts stat cards.

    Single GROUP BY over the folder — returns correct totals immediately, so the UI
    no longer derives counts from the first paginated page (the cause of the
    flash-then-correct bug). 404 if folder is cross-tenant / missing.
    """
    folder_row = await db.execute(
        select(Folder.id).where(
            Folder.id == folder_id,
            Folder.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    if folder_row.scalar() is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
        )

    rows = await db.execute(
        select(Contact.tg_status, sql_func.count(Contact.id))
        .where(Contact.folder_id == folder_id)
        .group_by(Contact.tg_status)
    )

    total = in_telegram = checking = not_found = 0
    for status, count in rows.all():
        normalized = (status or "").strip().lower()
        total += count
        if normalized in _IN_TELEGRAM_STATUSES:
            in_telegram += count
        elif normalized in _NOT_FOUND_STATUSES:
            not_found += count
        else:
            # Default bucket = "checking" (matches frontend: pending/unknown/'' + anything else)
            checking += count

    return FolderStatsResponse(
        total=total,
        in_telegram=in_telegram,
        checking=checking,
        not_found=not_found,
    )


@router.patch("/{folder_id}", response_model=FolderResponse)
async def rename_folder(
    folder_id: UUID,
    payload: FolderUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Rename folder. Duplicate name within workspace → 409."""
    result = await db.execute(
        select(Folder).where(
            Folder.id == folder_id,
            Folder.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    folder = result.scalars().first()
    if folder is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
        )
    new_name = payload.name.strip()
    if not new_name:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_NAME", "message": "Folder name cannot be empty"},
        )
    if new_name != folder.name:
        dup = await db.execute(
            select(Folder).where(
                Folder.workspace_id == ctx.workspace_id,
                Folder.name == new_name,
            )
        )
        if dup.scalars().first():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "FOLDER_NAME_DUPLICATE",
                    "message": f"Folder '{new_name}' already exists",
                },
            )
    folder.name = new_name
    await db.commit()
    await db.refresh(folder)
    logger.info(f"[folders] renamed id={folder.id} to '{new_name}'")
    return await _folder_to_response(db, folder)


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: UUID,
    force: bool = Query(False, description="Force cascade delete contacts in folder"),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Delete folder.

    D-06: 409 FOLDER_NOT_EMPTY if it contains contacts (unless ?force=true).
    With force=true, contacts are cascade-deleted via FK ondelete=CASCADE.
    """
    result = await db.execute(
        select(Folder).where(
            Folder.id == folder_id,
            Folder.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    folder = result.scalars().first()
    if folder is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
        )

    # Phase 4 close (D-06 carry-over): block delete если есть running campaign на этой папке.
    # FK ON DELETE RESTRICT enforces at DB level, but explicit 409 is friendlier UX.
    active_campaigns = (await db.execute(text("""
        SELECT id, name FROM campaigns
        WHERE folder_id = :fid AND workspace_id = :wid AND status = 'running'
        ORDER BY name
    """), {"fid": str(folder_id), "wid": str(ctx.workspace_id)})).fetchall()
    if active_campaigns:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FOLDER_USED_BY_RUNNING_CAMPAIGN",
                "message": "Cannot delete folder — used by running campaign(s)",
                "campaigns": [
                    {"id": str(r[0]), "name": r[1]} for r in active_campaigns
                ],
            },
        )

    # D-06: запрет удаления непустой папки (если force=false).
    count_result = await db.execute(
        select(sql_func.count(Contact.id)).where(Contact.folder_id == folder.id)
    )
    contact_count = count_result.scalar() or 0

    if contact_count > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FOLDER_NOT_EMPTY",
                "message": (
                    f"Folder contains {contact_count} contact(s). "
                    "Move them, delete them, or pass ?force=true."
                ),
                "contact_count": contact_count,
                # Running-campaign use is caught earlier (FOLDER_USED_BY_RUNNING_CAMPAIGN),
                # so by this point there are none — always [] here.
                "active_campaigns": [],
            },
        )

    # force=true OR empty → удаление; контакты каскадом через FK ondelete=CASCADE.
    await db.delete(folder)
    await db.commit()
    logger.info(
        f"[folders] deleted id={folder_id} force={force} "
        f"cascade_contacts={contact_count}"
    )
    return None
