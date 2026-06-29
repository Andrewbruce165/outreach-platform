"""Contacts router (Phase 2 — CONT-01, CONT-02, CONT-03, CONT-05, FLDR-03).

Endpoints:
  GET    /api/v1/contacts?folder_id=&tg_status=&limit=&offset=  — list
  POST   /api/v1/contacts                                       — push (single/batch) для n8n
  POST   /api/v1/contacts/import/preview multipart              — CSV preview
  POST   /api/v1/contacts/import                                — apply CSV import
  POST   /api/v1/contacts/{id}/move                             — move to folder
  POST   /api/v1/contacts/move                                  — batch move
  DELETE /api/v1/contacts/{id}                                  — delete single

Все endpoint'ы — workspace-scoped через Depends(auth_dep).
Push (POST /contacts) работает и с JWT, и с X-Workspace-Key (D-10).

D-19 async pipeline: контакты импортятся с tg_status='pending'. ContactCheckWorker
из плана 02-05 поднимет их и проверит через checker.
D-20 has_checker fallback: если в workspace нет sender'а с role='checker' — tg_status='unchecked'.

См. .planning/phases/02-tg-accounts-contacts/02-04-PLAN.md
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sql_func
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Contact, CsvImport, Folder, Sender
from app.routers.folders import get_or_create_by_name
from app.schemas import (
    ContactBatchPush,
    ContactCreate,
    ContactImportPreviewResponse,
    ContactImportRequest,
    ContactImportSummary,
    ContactResponse,
    DeleteContactBatchRequest,
    MoveContactBatchRequest,
    MoveContactRequest,
)
from app.services.csv_import import apply_import, parse_preview, suggest_mapping
from app.utils.auth import AuthCtx, auth_dep
from app.utils.names import normalize_full_name
from app.utils.phone import normalize_to_e164

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/contacts", tags=["contacts"])

MAX_CSV_BYTES = 5 * 1024 * 1024  # 5 MB hard limit (RESEARCH §CSV Import Pitfalls)


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _resolve_folder_id(
    db: AsyncSession,
    workspace_id: UUID,
    folder_id: Optional[UUID],
    folder_name: Optional[str],
) -> UUID:
    """Возвращает folder_id для INSERT'а.

    - folder_id передан → проверяем что он workspace'ный, иначе 404.
    - folder_name → get_or_create_by_name (FLDR-03 — переиспользование helper'а из 02-03).
    - оба None → 400.
    """
    if folder_id:
        result = await db.execute(
            select(Folder.id).where(
                Folder.id == folder_id,
                Folder.workspace_id == workspace_id,
                # TODO(v2-rls): replaced by RLS policy
            )
        )
        row = result.scalar()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
            )
        return row
    if folder_name:
        return await get_or_create_by_name(db, workspace_id, folder_name)
    raise HTTPException(
        status_code=400,
        detail={
            "code": "FOLDER_REQUIRED",
            "message": "Either folder_id or folder_name required",
        },
    )


async def _has_checker(db: AsyncSession, workspace_id: UUID) -> bool:
    """D-20: проверка наличия checker'а с auth_status='ok' в workspace.

    Влияет на default tg_status новых контактов: 'pending' если checker есть,
    'unchecked' иначе (ContactCheckWorker пропускает unchecked — нет чем проверять).
    """
    result = await db.execute(
        select(sql_func.count(Sender.id)).where(
            Sender.workspace_id == workspace_id,
            Sender.role == "checker",
            Sender.auth_status == "ok",
        )
    )
    return (result.scalar() or 0) > 0


async def _insert_contacts_with_dedup(
    db: AsyncSession,
    workspace_id: UUID,
    folder_id: UUID,
    records: list[dict],
    has_checker: bool,
) -> dict:
    """Атомарный INSERT с dedup через partial UNIQUE на (workspace_id, phone)
    и (workspace_id, username). Использует ON CONFLICT DO NOTHING.

    D-19: tg_status='pending' если checker есть, иначе 'unchecked' (D-20).

    Username-shortcut: если у записи есть username — считаем контакт заведомо
    зарегистрированным и ставим tg_status='registered' сразу (независимо от
    наличия телефона и checker'а). По username мы можем написать напрямую, а
    checker (phone-resolve) для таких контактов лишний и только жжёт лимиты;
    ContactCheckWorker выбирает только tg_status='pending', так что
    registered-контакты автоматически минуют чекер.

    Returns {imported, skipped_duplicates, skipped_phones}.
    """
    default_tg_status = "pending" if has_checker else "unchecked"
    imported = 0
    skipped_duplicates = 0
    skipped_phones: list[str] = []

    for rec in records:
        tg_status = "registered" if rec.get("username") else default_tg_status
        stmt = (
            pg_insert(Contact)
            .values(
                workspace_id=workspace_id,
                folder_id=folder_id,
                phone=rec.get("phone"),
                username=rec.get("username"),
                full_name=rec.get("full_name"),
                source=rec.get("source"),
                custom=rec.get("custom") or {},
                tg_status=tg_status,
            )
            .on_conflict_do_nothing()
            .returning(Contact.id)
        )
        result = await db.execute(stmt)
        row = result.scalar()
        if row is None:
            skipped_duplicates += 1
            if rec.get("phone"):
                skipped_phones.append(rec["phone"])
        else:
            imported += 1

    await db.commit()
    return {
        "imported": imported,
        "skipped_duplicates": skipped_duplicates,
        "skipped_phones": skipped_phones,
    }


# ─── GET /api/v1/contacts ────────────────────────────────────────────────────


@router.get("", response_model=List[ContactResponse])
async def list_contacts(
    folder_id: Optional[UUID] = Query(None),
    tg_status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """List contacts in workspace. Optional filters: folder_id, tg_status."""
    q = select(Contact).where(
        Contact.workspace_id == ctx.workspace_id
        # TODO(v2-rls): replaced by RLS policy
    )
    if folder_id is not None:
        q = q.where(Contact.folder_id == folder_id)
    if tg_status is not None:
        q = q.where(Contact.tg_status == tg_status)
    q = q.order_by(Contact.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return [ContactResponse.model_validate(c) for c in result.scalars().all()]


# ─── POST /api/v1/contacts (push — D-10) ─────────────────────────────────────


@router.post("", response_model=ContactImportSummary)
async def push_contacts(
    payload: dict,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Push single или batch (до 1000). Auth: JWT или X-Workspace-Key.

    Single:  {phone | username, full_name?, source?, custom?, folder_id|folder_name}
    Batch:   {contacts: [{...}, ...]}

    Все контакты идут в одну папку (берём folder из первого элемента).
    """
    # Разбираем payload как single или batch через Pydantic.
    if "contacts" in payload:
        try:
            batch = ContactBatchPush(contacts=payload["contacts"])
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail={"code": "VALIDATION_ERROR", "message": str(e)},
            )
        raw_records = [c.model_dump() for c in batch.contacts]
    else:
        try:
            single = ContactCreate(**payload)
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail={"code": "VALIDATION_ERROR", "message": str(e)},
            )
        raw_records = [single.model_dump()]

    # Все контакты — одна папка (folder из первого элемента).
    first = raw_records[0]
    folder_uuid = first.get("folder_id")
    if folder_uuid and not isinstance(folder_uuid, UUID):
        try:
            folder_uuid = UUID(str(folder_uuid))
        except (ValueError, TypeError):
            folder_uuid = None
    folder_id = await _resolve_folder_id(
        db, ctx.workspace_id, folder_uuid, first.get("folder_name")
    )

    has_checker = await _has_checker(db, ctx.workspace_id)

    # Нормализация phone + сбор skipped_invalid.
    clean_records: list[dict] = []
    skipped_invalid = 0
    skipped_phones: list[str] = []
    for rec in raw_records:
        if rec.get("phone"):
            normalized = normalize_to_e164(rec["phone"])
            if not normalized:
                skipped_invalid += 1
                skipped_phones.append(rec["phone"])
                continue
            rec["phone"] = normalized
        if rec.get("username"):
            rec["username"] = rec["username"].lstrip("@")
        # Title-case full_name at the import boundary (single + batch).
        if rec.get("full_name"):
            rec["full_name"] = normalize_full_name(rec["full_name"])
        if not rec.get("phone") and not rec.get("username"):
            skipped_invalid += 1
            continue
        # Folder-поля уже разрешены — удаляем из record.
        rec.pop("folder_id", None)
        rec.pop("folder_name", None)
        clean_records.append(rec)

    dedup_summary = await _insert_contacts_with_dedup(
        db, ctx.workspace_id, folder_id, clean_records, has_checker
    )

    logger.info(
        f"[contacts] push workspace={ctx.workspace_id} folder={folder_id} "
        f"source={ctx.source} total={len(raw_records)} "
        f"imported={dedup_summary['imported']} "
        f"dup={dedup_summary['skipped_duplicates']} invalid={skipped_invalid}"
    )

    return ContactImportSummary(
        total=len(raw_records),
        imported=dedup_summary["imported"],
        skipped_duplicates=dedup_summary["skipped_duplicates"],
        skipped_invalid=skipped_invalid,
        skipped_phones=skipped_phones + dedup_summary["skipped_phones"],
    )


# ─── POST /api/v1/contacts/import/preview (multipart) ────────────────────────


@router.post("/import/preview", response_model=ContactImportPreviewResponse)
async def import_preview(
    file: UploadFile = File(...),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Загружает CSV (multipart), парсит первые ~50 строк, сохраняет blob в
    csv_imports с TTL=30 мин (DB default INTERVAL '30 minutes').
    Возвращает import_id, columns, sample_rows, suggested_mapping.
    """
    raw = await file.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": f"Max {MAX_CSV_BYTES} bytes",
            },
        )
    try:
        preview = parse_preview(raw)
    except ValueError as e:
        raise HTTPException(
            status_code=422, detail={"code": str(e), "message": str(e)}
        )

    mapping = suggest_mapping(preview["columns"])

    # Сохраняем в csv_imports BYTEA (RESEARCH C-02 Option B). expires_at
    # выставляем Python-side (NOW + 30 минут) т.к. ORM-колонка NOT NULL
    # без server_default. Используем ORM напрямую — раньше тут был raw SQL
    # с `:cols::jsonb` cast'ом, но SQLAlchemy text() + asyncpg не умеют
    # парсить named-param сразу перед `::` (Postgres cast-оператор) и
    # падали с PostgresSyntaxError → endpoint возвращал 500 без CORS.
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    csv_row = CsvImport(
        workspace_id=ctx.workspace_id,
        file_data=raw,
        columns=preview["columns"],
        suggested_mapping=mapping,
        encoding=preview["encoding"],
        delimiter=preview["delimiter"],
        expires_at=expires_at,
    )
    db.add(csv_row)
    await db.flush()
    await db.commit()
    import_id = csv_row.id

    logger.info(
        f"[contacts] import-preview workspace={ctx.workspace_id} "
        f"import_id={import_id} cols={len(preview['columns'])} "
        f"sample_rows={len(preview['sample_rows'])} encoding={preview['encoding']} "
        f"expires_at={expires_at}"
    )

    return ContactImportPreviewResponse(
        import_id=import_id,
        columns=preview["columns"],
        sample_rows=preview["sample_rows"],
        suggested_mapping=mapping,
        encoding=preview["encoding"],
        delimiter=preview["delimiter"],
        looks_like_no_header=preview["looks_like_no_header"],
    )


# ─── POST /api/v1/contacts/import ────────────────────────────────────────────


@router.post("/import", response_model=ContactImportSummary, status_code=202)
async def import_contacts(
    payload: ContactImportRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Применяет mapping к csv_imports blob → INSERT contacts с dedup.

    202 Accepted: контакты сохраняются с tg_status='pending'. ContactCheckWorker
    (plan 02-05) подберёт и проверит. UI поллит GET /contacts чтобы видеть прогресс.
    """
    result = await db.execute(
        select(CsvImport).where(
            CsvImport.id == payload.import_id,
            CsvImport.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    imp = result.scalars().first()
    if imp is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "IMPORT_NOT_FOUND",
                "message": "Import session not found or expired",
            },
        )
    # Postgres хранит как timezone-aware; сравним с UTC now.
    expires_at = imp.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=410,
            detail={"code": "IMPORT_EXPIRED", "message": "Import session expired"},
        )

    folder_id = await _resolve_folder_id(
        db, ctx.workspace_id, payload.folder_id, payload.folder_name
    )

    try:
        applied = apply_import(
            imp.file_data,
            mapping=payload.mapping,
            delimiter=imp.delimiter or ",",
            encoding=imp.encoding or "utf-8-sig",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422, detail={"code": "MAPPING_INVALID", "message": str(e)}
        )

    has_checker = await _has_checker(db, ctx.workspace_id)
    dedup_summary = await _insert_contacts_with_dedup(
        db, ctx.workspace_id, folder_id, applied["rows_to_insert"], has_checker
    )

    # Удаляем CsvImport row — больше не нужен.
    await db.execute(sa_delete(CsvImport).where(CsvImport.id == payload.import_id))
    await db.commit()

    logger.info(
        f"[contacts] import workspace={ctx.workspace_id} folder={folder_id} "
        f"total={applied['total']} imported={dedup_summary['imported']} "
        f"dup={dedup_summary['skipped_duplicates']} invalid={applied['skipped_invalid']}"
    )

    return ContactImportSummary(
        total=applied["total"],
        imported=dedup_summary["imported"],
        skipped_duplicates=dedup_summary["skipped_duplicates"],
        skipped_invalid=applied["skipped_invalid"],
        skipped_phones=dedup_summary["skipped_phones"],
    )


# ─── POST /api/v1/contacts/{id}/move (D-04) ──────────────────────────────────


@router.post("/{contact_id}/move", response_model=ContactResponse)
async def move_contact(
    contact_id: UUID,
    payload: MoveContactRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Move one contact to a different folder (D-04: контакт в одной папке)."""
    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    contact = result.scalars().first()
    if contact is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONTACT_NOT_FOUND", "message": "Contact not found"},
        )
    folder_result = await db.execute(
        select(Folder.id).where(
            Folder.id == payload.folder_id,
            Folder.workspace_id == ctx.workspace_id,
        )
    )
    if folder_result.scalar() is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
        )
    contact.folder_id = payload.folder_id
    await db.commit()
    await db.refresh(contact)
    return ContactResponse.model_validate(contact)


@router.post("/move", response_model=dict)
async def move_contacts_batch(
    payload: MoveContactBatchRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Batch move (D-04). Возвращает {moved: N}.

    Используем ORM update через explicit fetch+update — это и закрывает
    workspace-isolation contract (Contact.workspace_id фильтр), и
    позволяет SQLAlchemy управлять updated_at через onupdate.
    """
    folder_result = await db.execute(
        select(Folder.id).where(
            Folder.id == payload.folder_id,
            Folder.workspace_id == ctx.workspace_id,
        )
    )
    if folder_result.scalar() is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
        )

    # Workspace-scoped fetch (cross-tenant ids — пропускаются).
    fetch_result = await db.execute(
        select(Contact).where(
            Contact.id.in_(payload.contact_ids),
            Contact.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    contacts_to_move = fetch_result.scalars().all()
    for contact in contacts_to_move:
        contact.folder_id = payload.folder_id
    await db.commit()
    moved = len(contacts_to_move)
    logger.info(
        f"[contacts] batch-move workspace={ctx.workspace_id} "
        f"folder={payload.folder_id} moved={moved}"
    )
    return {"moved": moved}


# ─── DELETE /api/v1/contacts ─────────────────────────────────────────────────


@router.post("/delete", response_model=dict)
async def delete_contacts_batch(
    payload: DeleteContactBatchRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Batch hard-delete. Возвращает {deleted: N}.

    Зеркало move_contacts_batch: workspace-scoped fetch+delete, cross-tenant ids
    молча пропускаются (не светим существование чужих контактов через 404).
    """
    fetch_result = await db.execute(
        select(Contact).where(
            Contact.id.in_(payload.contact_ids),
            Contact.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    contacts_to_delete = fetch_result.scalars().all()
    for contact in contacts_to_delete:
        await db.delete(contact)
    await db.commit()
    deleted = len(contacts_to_delete)
    logger.info(
        f"[contacts] batch-delete workspace={ctx.workspace_id} deleted={deleted}"
    )
    return {"deleted": deleted}


# ─── DELETE /api/v1/contacts/{id} ────────────────────────────────────────────


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Hard delete контакта (workspace-scoped)."""
    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    contact = result.scalars().first()
    if contact is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONTACT_NOT_FOUND", "message": "Contact not found"},
        )
    await db.delete(contact)
    await db.commit()
    return None
