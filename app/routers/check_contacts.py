"""Check contacts router (Phase 2 — CONT-04 recheck endpoint).

Workspace-scoped rewrite of the legacy ``check_contacts.py`` (which imported
the removed ``app.routers.auth.verify_api_key`` shim and was unhookable).

Endpoints:
  POST /api/v1/contacts/recheck  — UPDATE tg_status='pending' batch →
                                   ContactCheckWorker подберёт на след. tick.

Payload (``RecheckRequest``):
  * ``contact_ids: list[UUID]`` — конкретные контакты этого workspace, либо
  * ``folder_id: UUID`` — все контакты папки этого workspace.

При наличии folder_id мы предварительно проверяем что folder принадлежит
workspace'у (cross-tenant guard выдаёт 404, не 403, чтобы не раскрывать
существование folder'а в другом тенанте).

При наличии только contact_ids — фильтр ``workspace_id = :wid`` в UPDATE сам
закрывает изоляцию: контакты другого workspace'а в счётчик не попадают
(``marked_pending`` будет меньше длины массива).

Возвращает ``202 Accepted`` + ``{marked_pending: N}`` — операция асинхронная,
сам resolve делает ``ContactCheckWorker`` (см. ``app/services/contact_check_worker.py``).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import RecheckRequest
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/contacts", tags=["contact-check"])


@router.post("/recheck", status_code=202)
async def recheck_contacts(
    payload: RecheckRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Помечает контакты как ``tg_status='pending'`` → ContactCheckWorker переоценит.

    Поддерживает либо ``contact_ids`` (subset), либо ``folder_id`` (вся папка).
    Pydantic ``model_validator`` гарантирует наличие одного из них (см.
    ``RecheckRequest.one_required``).
    """
    if payload.contact_ids:
        result = await db.execute(
            text(
                """
                UPDATE contacts
                SET tg_status = 'pending',
                    tg_error = NULL,
                    updated_at = NOW()
                WHERE id = ANY(:ids)
                  AND workspace_id = :wid
                  AND phone IS NOT NULL
                """
            ),
            {
                "ids": [str(cid) for cid in payload.contact_ids],
                "wid": str(ctx.workspace_id),
            },
        )
    elif payload.folder_id:
        # Workspace-scoped folder check — 404 не раскрывает cross-tenant.
        folder_check = await db.execute(
            text(
                """
                SELECT 1 FROM folders
                WHERE id = :fid AND workspace_id = :wid
                """
            ),
            {
                "fid": str(payload.folder_id),
                "wid": str(ctx.workspace_id),
            },
        )
        if folder_check.scalar() is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
            )
        result = await db.execute(
            text(
                """
                UPDATE contacts
                SET tg_status = 'pending',
                    tg_error = NULL,
                    updated_at = NOW()
                WHERE folder_id = :fid
                  AND workspace_id = :wid
                  AND phone IS NOT NULL
                """
            ),
            {
                "fid": str(payload.folder_id),
                "wid": str(ctx.workspace_id),
            },
        )
    else:
        # Не должно случаться — Pydantic model_validator уже отверг бы запрос.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MISSING_TARGET",
                "message": "Either contact_ids or folder_id required",
            },
        )

    marked = result.rowcount or 0
    await db.commit()
    logger.info(
        f"[recheck] workspace={ctx.workspace_id} source={ctx.source} "
        f"marked_pending={marked}"
    )
    return {"marked_pending": marked}
