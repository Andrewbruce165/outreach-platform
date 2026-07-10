"""Campaigns router (Phase 4 — CAMP-01..04, CAMP-07, CAMP-08, CAMP-14).

Workspace-scoped CRUD + lifecycle endpoints для кампаний.

Endpoints:
    GET    /api/v1/campaigns                       — list workspace campaigns
    POST   /api/v1/campaigns                       — create draft campaign
    GET    /api/v1/campaigns/{id}                  — single campaign (с is_exhausted + attached_senders)
    PATCH  /api/v1/campaigns/{id}                  — partial update
    DELETE /api/v1/campaigns/{id}                  — hard delete (409 на running)
    POST   /api/v1/campaigns/{id}/start            — draft|paused → running (sender lock check)
    POST   /api/v1/campaigns/{id}/pause            — running → paused
    POST   /api/v1/campaigns/{id}/resume           — paused → running (sender lock re-check)
    POST   /api/v1/campaigns/{id}/finish           — running|paused → done (terminal)
    POST   /api/v1/campaigns/{id}/duplicate        — copy row only (empty sender pool), status='draft'

Все endpoint'ы под Depends(auth_dep) + .where(Campaign.workspace_id == ctx.workspace_id).
"""

import logging
import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func as sql_func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    AIContext,
    Campaign,
    CampaignAttachment,
    CampaignSender,
    Folder,
    Sender,
)
from app.schemas import (
    CampaignCreate,
    CampaignEvent,
    CampaignEventsResponse,
    CampaignListResponse,
    CampaignResponse,
    CampaignSenderAttach,
    CampaignSenderAttachRequest,
    CampaignUpdate,
    CampaignWriteResponse,
    EtaShortfall,
    PoolHealth,
    SenderAttachWarning,
    WarningItem,
)
from app.services import grade_ladder
from app.services.rebalance import rebalance_on_attach
from app.services.campaign_enqueue import rerender_pending_queue
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])

# Phase 24 D-03: hard ceiling for the campaign first-message attachment blob.
# Bytes go straight to the BYTEA column (D-02) — no temp file. Over this → 413.
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024   # 50 MB
# 260709-dbl: cap the number of attachments per campaign (album). Over → 400.
MAX_ATTACHMENTS = 10

# Phase 22 (D-07): the per-campaign daily new-dialog cap and its validation helper
# were removed — the daily throttle is now the account-level grade budget resolved
# from the workspace ladder. Campaign create/update no longer emit cap warnings here.


# ── Helpers ──────────────────────────────────────────────────────────────────


def _validate_timezone(tz: str) -> None:
    """Raise 422 INVALID_TIMEZONE if tz not a valid IANA zone."""
    try:
        zoneinfo.ZoneInfo(tz)
    except Exception:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_TIMEZONE",
                    "message": f"Unknown IANA timezone '{tz}'"},
        )


async def _load_campaign(db: AsyncSession, ctx: AuthCtx, campaign_id: UUID) -> Campaign:
    """Workspace-scoped fetch by id; 404 if not found or wrong workspace."""
    res = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
    )
    c = res.scalars().first()
    if c is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CAMPAIGN_NOT_FOUND", "message": "Campaign not found"},
        )
    return c


async def _cancel_pending_queue(db: AsyncSession, campaign_id: UUID, reason: str) -> None:
    """Cancel still-pending queue items of a campaign on terminal transition/delete.

    The queue dispatcher already refuses to send items whose campaign is not
    'running' (queue.py INNER JOIN + c.status='running'), so this is not a
    send-leak guard — it prevents zombie 'pending' rows lingering forever after
    finish/stop/delete (delete also nulls campaign_id via FK SET NULL).
    Pause is intentionally excluded: paused items resume when the campaign does.
    """
    await db.execute(
        text("""
            UPDATE message_queue
            SET status = 'cancelled', finished_at = NOW(), error_message = :reason
            WHERE campaign_id = :cid AND status = 'pending'
        """),
        {"cid": str(campaign_id), "reason": reason},
    )


async def _validate_workspace_owns_agent(
    db: AsyncSession, ctx: AuthCtx, agent_id: UUID
) -> None:
    row = (await db.execute(
        select(AIContext.id).where(
            AIContext.id == agent_id,
            AIContext.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_NOT_FOUND",
                    "message": "Agent not in your workspace"},
        )


async def _validate_workspace_owns_folder(
    db: AsyncSession, ctx: AuthCtx, folder_id: UUID
) -> None:
    row = (await db.execute(
        select(Folder.id).where(
            Folder.id == folder_id,
            Folder.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "FOLDER_NOT_FOUND",
                    "message": "Folder not in your workspace"},
        )


async def _validate_workspace_owns_senders(
    db: AsyncSession, ctx: AuthCtx, sender_ids: list[UUID]
) -> None:
    """Defence-in-depth per Q4 — все sender_ids должны быть в ctx.workspace_id."""
    if not sender_ids:
        return
    rows = (await db.execute(
        select(Sender.id).where(
            Sender.id.in_(sender_ids),
            Sender.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )).scalars().all()
    found = set(rows)
    missing = [sid for sid in sender_ids if sid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail={"code": "SENDER_NOT_FOUND",
                    "message": "One or more senders not in your workspace",
                    "missing_sender_ids": [str(s) for s in missing]},
        )


async def _compute_is_exhausted(
    db: AsyncSession, campaign_id: UUID, folder_id: UUID
) -> bool:
    """is_exhausted = (no registered contacts unassigned) AND (no pending/processing queue).

    M4: 'registered' value is part of contacts.tg_status CHECK constraint (migration 013).
    """
    unassigned_count = (await db.execute(text("""
        SELECT COUNT(*)
        FROM contacts c
        WHERE c.folder_id = :fid
          AND c.tg_status = 'registered'
          AND (c.phone IS NOT NULL OR c.username IS NOT NULL)
          AND NOT EXISTS (
              SELECT 1 FROM campaign_contact_assignments cca
              WHERE cca.campaign_id = :cid
                -- identity key: phone wins, else '@username' (migration 025)
                AND cca.contact_phone = COALESCE(c.phone, '@' || c.username)
          )
    """), {"fid": str(folder_id), "cid": str(campaign_id)})).scalar() or 0

    pending_count = (await db.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND status IN ('pending', 'processing')
    """), {"cid": str(campaign_id)})).scalar() or 0

    return unassigned_count == 0 and pending_count == 0


def _count_work_days(
    *, campaign_tz: str, work_days_mask: int, stop_date: datetime,
    now: Optional[datetime] = None,
) -> int:
    """Variant 1 (deadline-mass-fail fix): count campaign work-days remaining
    between now and stop_date (today inclusive) in the campaign's timezone,
    filtered by work_days_mask (same Mo=1..Su=64 bit mapping as
    ``queue._campaign_in_working_window``). Used ONLY by the ETA forecast below
    — NOT by the dispatch hot path in queue.py.

    Approximation: counts a day as "available" regardless of how much of
    work_hour_start..work_hour_end is left in it — fine for a warning, not for
    precise scheduling. Returns 0 if stop_date is already in the past (mirrors
    the D-11 v2 pause trigger: no work days left to send) or on an invalid
    timezone (conservative).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        tz = zoneinfo.ZoneInfo(campaign_tz)
    except Exception as exc:  # noqa: BLE001 — any tz resolution failure
        logger.warning(f"Invalid campaign timezone '{campaign_tz}' in ETA forecast: {exc}")
        return 0

    local_now = now.astimezone(tz)
    local_stop = stop_date.astimezone(tz)
    if local_stop <= local_now:
        return 0

    day = local_now.date()
    stop_day = local_stop.date()
    count = 0
    guard = 0
    while day <= stop_day and guard < 400:  # guard: defensive cap, not expected in practice
        if work_days_mask & (1 << day.weekday()):
            count += 1
        day += timedelta(days=1)
        guard += 1
    return count


async def _compute_eta_shortfall(
    db: AsyncSession, campaign: Campaign
) -> Optional[EtaShortfall]:
    """Variant 1 (deadline-mass-fail fix): forecast contacts-left vs. deadline
    capacity, so the UI can warn BEFORE the D-11 v2 auto-pause fires.

    None when there's no stop_date (nothing to miss) or no folder (nothing to
    count remaining contacts against) — mirrors the is_exhausted short-circuit.

    remaining = unassigned registered contacts in the folder + pending/processing
    queue rows (same counting as ``_compute_is_exhausted``, unified here).
    daily_capacity = sum of the Phase 22 grade-ladder budget over the pool's
    CURRENTLY eligible senders (restriction_status='none' AND auth_status='ok'
    AND lifecycle_status='active' — same predicate as POOLV-01's `active`), so
    it reflects the live pool, not a hardcoded per-account number (the
    per-campaign daily cap column was dropped in migration 059).
    """
    if campaign.stop_date is None or campaign.folder_id is None:
        return None

    unassigned_count = (await db.execute(text("""
        SELECT COUNT(*)
        FROM contacts c
        WHERE c.folder_id = :fid
          AND c.tg_status = 'registered'
          AND (c.phone IS NOT NULL OR c.username IS NOT NULL)
          AND NOT EXISTS (
              SELECT 1 FROM campaign_contact_assignments cca
              WHERE cca.campaign_id = :cid
                AND cca.contact_phone = COALESCE(c.phone, '@' || c.username)
          )
    """), {"fid": str(campaign.folder_id), "cid": str(campaign.id)})).scalar() or 0

    pending_count = (await db.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND status IN ('pending', 'processing')
    """), {"cid": str(campaign.id)})).scalar() or 0
    remaining = unassigned_count + pending_count

    ladder = await grade_ladder.load_ladder(db, campaign.workspace_id)
    level_rows = (await db.execute(text("""
        SELECT s.current_level
        FROM campaign_senders cs
        JOIN senders s ON s.id = cs.sender_id
        WHERE cs.campaign_id = :cid
          AND s.restriction_status = 'none'
          AND s.auth_status = 'ok'
          AND s.lifecycle_status = 'active'
    """), {"cid": str(campaign.id)})).fetchall()
    daily_capacity = sum(
        grade_ladder.budget_for_level(ladder, r.current_level) for r in level_rows
    )

    work_days_left = _count_work_days(
        campaign_tz=campaign.timezone,
        work_days_mask=campaign.work_days_mask,
        stop_date=campaign.stop_date,
    )

    shortfall = max(0, remaining - daily_capacity * work_days_left)
    return EtaShortfall(
        remaining_contacts=remaining,
        daily_capacity=daily_capacity,
        work_days_left=work_days_left,
        shortfall_contacts=shortfall,
        on_track=shortfall == 0,
    )


async def _build_attached_senders(
    db: AsyncSession, ctx: AuthCtx, campaign_id: UUID
) -> list[CampaignSenderAttach]:
    """attached_senders with locked_by_campaign_id when sender in OTHER running camp."""
    rows = await db.execute(text("""
        SELECT cs.sender_id,
               (SELECT c.id FROM campaign_senders cs2
                  JOIN campaigns c ON c.id = cs2.campaign_id
                  WHERE cs2.sender_id = cs.sender_id
                    AND c.status = 'running'
                    AND c.id != :cid
                    AND c.workspace_id = :wid
                  LIMIT 1) AS locked_by_id,
               (SELECT c.name FROM campaign_senders cs2
                  JOIN campaigns c ON c.id = cs2.campaign_id
                  WHERE cs2.sender_id = cs.sender_id
                    AND c.status = 'running'
                    AND c.id != :cid
                    AND c.workspace_id = :wid
                  LIMIT 1) AS locked_by_name,
               s.restriction_status,
               s.restricted_until
        FROM campaign_senders cs
        JOIN senders s ON s.id = cs.sender_id
        WHERE cs.campaign_id = :cid
        ORDER BY cs.added_at
    """), {"cid": str(campaign_id), "wid": str(ctx.workspace_id)})
    return [
        CampaignSenderAttach(
            sender_id=row[0],
            locked_by_campaign_id=row[1],
            locked_by_campaign_name=row[2],
            restriction_status=row[3],
            restricted_until=row[4],
        )
        for row in rows.fetchall()
    ]


async def _compute_pool_health(
    db: AsyncSession, campaign_id: UUID
) -> PoolHealth:
    """POOLV-01: 3-state pool-health aggregate in one SELECT over the attached pool.

    total = COUNT(*), active = truly-sendable (IN-10: restriction_status='none'
    AND auth_status='ok' AND lifecycle_status='active'), paused = everything
    restricted, earliest_resume_at = MIN(restricted_until) among the restricted
    senders (OQ#4 recheck horizon). Empty pool → all zeros / None.
    """
    row = (await db.execute(text("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (
                WHERE s.restriction_status = 'none'
                  AND s.auth_status = 'ok'
                  AND s.lifecycle_status = 'active'
            ) AS active,
            COUNT(*) FILTER (WHERE s.restriction_status <> 'none') AS paused,
            MIN(s.restricted_until)
                FILTER (WHERE s.restriction_status <> 'none') AS earliest_resume_at
        FROM campaign_senders cs
        JOIN senders s ON s.id = cs.sender_id
        WHERE cs.campaign_id = :cid
    """), {"cid": str(campaign_id)})).one()
    active = row.active or 0
    return PoolHealth(
        total=row.total or 0,
        active=active,
        paused=row.paused or 0,
        earliest_resume_at=row.earliest_resume_at,
        # quick-260706-c1p SOFT advisory: >=2 sendable senders means a single
        # freeze still leaves a backup. Derived from the active count already
        # computed above — no extra query. Advisory only; no attach/detach/start
        # behaviour changes (locked decision 2026-07-06).
        has_backup=active >= 2,
    )


async def _campaign_to_response(
    db: AsyncSession, ctx: AuthCtx, campaign: Campaign
) -> CampaignResponse:
    attached = await _build_attached_senders(db, ctx, campaign.id)
    pool_health = await _compute_pool_health(db, campaign.id)
    # 024: draft без папки не может быть «исчерпан» — рассылать некуда. Short-circuit
    # вместо вызова _compute_is_exhausted с folder_id=None (даст бессмысленный SQL).
    if campaign.folder_id is None:
        is_exhausted = False
    else:
        is_exhausted = await _compute_is_exhausted(db, campaign.id, campaign.folder_id)
    # WR-12b: read-time count of failed queue rows (COUNT(*), not a stored column).
    failed_count = (await db.execute(text(
        "SELECT COUNT(*) FROM message_queue WHERE campaign_id = :cid AND status = 'failed'"
    ), {"cid": str(campaign.id)})).scalar() or 0
    # Phase 24 D-13/D-19 + 260709-dbl: COUNT on campaign_attachments — the 50MB blobs
    # stay off every SELECT campaigns (Pitfall 7). attachment_count/has_attachment are
    # computed, not columns. has_attachment = attachment_count > 0.
    attachment_count = (await db.execute(text(
        "SELECT COUNT(*) FROM campaign_attachments WHERE campaign_id = :cid"
    ), {"cid": str(campaign.id)})).scalar() or 0
    has_attachment = attachment_count > 0
    # Variant 1 (deadline-mass-fail fix): live ETA-vs-deadline forecast.
    eta_shortfall = await _compute_eta_shortfall(db, campaign)
    return CampaignResponse(
        id=campaign.id,
        workspace_id=campaign.workspace_id,
        name=campaign.name,
        description=campaign.description,
        agent_id=campaign.agent_id,
        folder_id=campaign.folder_id,
        status=campaign.status,
        timezone=campaign.timezone,
        work_hour_start=campaign.work_hour_start,
        work_hour_end=campaign.work_hour_end,
        work_days_mask=campaign.work_days_mask,
        start_date=campaign.start_date,
        stop_date=campaign.stop_date,
        message_template=campaign.message_template,
        lead_webhook_url=campaign.lead_webhook_url,
        handoff_webhook_url=campaign.handoff_webhook_url,
        finish_webhook_url=campaign.finish_webhook_url,
        lead_trigger_hint=campaign.lead_trigger_hint,
        handoff_trigger_hint=campaign.handoff_trigger_hint,
        finish_trigger_hint=campaign.finish_trigger_hint,
        tools=campaign.tools or [],
        # 05.1 v2 fields (UI-SPEC §5.5 step 2 + step 6 — UI-CAMPB-01 / UI-CAMPL-01).
        audience_hints=campaign.audience_hints,
        primary_goal=campaign.primary_goal,
        # NB: success_criteria removed (Phase 11 D-13) — merged into lead_trigger_hint.
        webhook_url=campaign.webhook_url,
        # 026: per-campaign re-contact policy.
        allow_recontact=campaign.allow_recontact,
        recontact_min_age_days=campaign.recontact_min_age_days,
        # Phase 19 NORP-02/NORP-05: follow-up + auto-finish (D-08/D-12).
        follow_up_enabled=campaign.follow_up_enabled,
        follow_up_interval_hours=campaign.follow_up_interval_hours,
        follow_up_max_pings=campaign.follow_up_max_pings,
        auto_finish_hours=campaign.auto_finish_hours,
        # Phase 11 campaign fields (D-04/D-12/D-14).
        dialogue_flow=campaign.dialogue_flow or [],
        arguments_facts=campaign.arguments_facts,
        campaign_rules=campaign.campaign_rules,
        # Prompt template v2 (migration 037): preset-driven core_directive.
        objective_preset=campaign.objective_preset,
        disclosure_preset=campaign.disclosure_preset,
        authority_preset=campaign.authority_preset,
        style_examples=campaign.style_examples,
        # 029: auto-pause visibility.
        pause_reason=campaign.pause_reason,
        paused_at=campaign.paused_at,
        attached_senders=attached,
        is_exhausted=is_exhausted,
        failed_count=failed_count,
        pool_health=pool_health,
        eta_shortfall=eta_shortfall,
        # Phase 24 D-13/D-19 + 260709-dbl: variation toggle + computed attachment
        # presence/count.
        variation_enabled=campaign.variation_enabled,
        has_attachment=has_attachment,
        attachment_count=attachment_count,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


async def _check_sender_lock(
    db: AsyncSession, ctx: AuthCtx, campaign_id: UUID,
    only_sender_id: Optional[UUID] = None,
) -> list[dict]:
    """Return list of {sender_id, campaign_id, campaign_name} conflicts.

    Conflict = another running campaign in same workspace shares ≥1 sender with this one.
    Empty list = lock OK.

    IN-05: when ``only_sender_id`` is supplied (the attach flow), the scan is
    restricted to that single sender so attaching a conflict-free sender to a
    campaign that already contains a DIFFERENT sender locked in another running
    campaign does NOT falsely 409 — only the newly-attached sender is checked.
    start/resume pass no ``only_sender_id`` and keep checking the full pool.
    """
    params = {"cid": str(campaign_id), "wid": str(ctx.workspace_id)}
    only_filter = ""
    if only_sender_id is not None:
        only_filter = "AND cs.sender_id = :only_sender_id"
        params["only_sender_id"] = str(only_sender_id)
    rows = await db.execute(text(f"""
        SELECT cs.sender_id, c.id, c.name
        FROM campaign_senders cs
        JOIN campaigns c ON c.id = cs.campaign_id
        WHERE cs.sender_id IN (
                SELECT sender_id FROM campaign_senders WHERE campaign_id = :cid
              )
          AND c.status = 'running'
          AND c.id != :cid
          AND c.workspace_id = :wid
          {only_filter}
        ORDER BY c.name
    """), params)
    return [
        {"sender_id": str(r[0]), "campaign_id": str(r[1]), "campaign_name": r[2]}
        for r in rows.fetchall()
    ]


# ── Endpoints: CRUD ──────────────────────────────────────────────────────────


@router.post("", response_model=CampaignWriteResponse, status_code=201)
async def create_campaign(
    payload: CampaignCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """CAMP-01: create campaign in draft state.

    Validation chain:
    1. timezone IANA → 422
    2. agent_id workspace → 404
    3. folder_id workspace → 404
    4. sender_ids[] workspace (Q4) → 404
    5. Duplicate name → 409
    """
    _validate_timezone(payload.timezone)
    # Phase 22 (D-07): the per-campaign dialog-cap validation was removed; the daily
    # throttle is now the account grade budget. No cap warnings on create.
    warnings: List[WarningItem] = []
    # 024: agent/folder опциональны для draft — валидируем принадлежность workspace
    # только когда значение передано (None = ещё не заполнено в визарде).
    if payload.agent_id is not None:
        await _validate_workspace_owns_agent(db, ctx, payload.agent_id)
    if payload.folder_id is not None:
        await _validate_workspace_owns_folder(db, ctx, payload.folder_id)
    if payload.sender_ids:
        await _validate_workspace_owns_senders(db, ctx, payload.sender_ids)

    name = payload.name.strip()
    existing = (await db.execute(
        select(Campaign).where(
            Campaign.workspace_id == ctx.workspace_id,
            Campaign.name == name,
        )
    )).scalars().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"code": "CAMPAIGN_NAME_DUPLICATE",
                    "message": f"Campaign '{name}' already exists"},
        )

    camp = Campaign(
        workspace_id=ctx.workspace_id,
        name=name,
        description=payload.description,
        agent_id=payload.agent_id,
        folder_id=payload.folder_id,
        # message_template nullable=False server_default="" — None превращаем в "" для draft.
        message_template=payload.message_template or "",
        timezone=payload.timezone,
        work_hour_start=payload.work_hour_start,
        work_hour_end=payload.work_hour_end,
        work_days_mask=payload.work_days_mask,
        start_date=payload.start_date,
        stop_date=payload.stop_date,
        lead_webhook_url=str(payload.lead_webhook_url) if payload.lead_webhook_url else None,
        handoff_webhook_url=str(payload.handoff_webhook_url) if payload.handoff_webhook_url else None,
        finish_webhook_url=str(payload.finish_webhook_url) if payload.finish_webhook_url else None,
        lead_trigger_hint=payload.lead_trigger_hint,
        handoff_trigger_hint=payload.handoff_trigger_hint,
        finish_trigger_hint=payload.finish_trigger_hint,
        tools=[t.model_dump(mode="json") for t in payload.tools],
        # 05.1 v2 fields (UI-SPEC §5.5 step 2 + step 6 — UI-CAMPB-01).
        audience_hints=payload.audience_hints,
        primary_goal=payload.primary_goal,
        # NB: success_criteria removed (Phase 11 D-13) — merged into lead_trigger_hint.
        webhook_url=str(payload.webhook_url) if payload.webhook_url else None,
        # 026: per-campaign re-contact policy.
        allow_recontact=payload.allow_recontact,
        recontact_min_age_days=payload.recontact_min_age_days,
        # Phase 24 D-13: invisible anti-spam text-variation toggle (default ON).
        variation_enabled=payload.variation_enabled,
        # Phase 19 NORP-02/NORP-05: follow-up + auto-finish (D-08/D-12).
        follow_up_enabled=payload.follow_up_enabled,
        follow_up_interval_hours=payload.follow_up_interval_hours,
        follow_up_max_pings=payload.follow_up_max_pings,
        auto_finish_hours=payload.auto_finish_hours,
        # Phase 11 campaign fields (D-04/D-12/D-14).
        dialogue_flow=[s.model_dump() for s in payload.dialogue_flow] if payload.dialogue_flow else [],
        arguments_facts=payload.arguments_facts,
        campaign_rules=payload.campaign_rules,
        # Prompt template v2 (migration 037): preset-driven core_directive.
        objective_preset=payload.objective_preset,
        disclosure_preset=payload.disclosure_preset,
        authority_preset=payload.authority_preset,
        style_examples=payload.style_examples,
        status="draft",
    )
    db.add(camp)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        if "idx_campaigns_workspace_name" in str(e.orig).lower() or "duplicate" in str(e.orig).lower():
            raise HTTPException(
                status_code=409,
                detail={"code": "CAMPAIGN_NAME_DUPLICATE",
                        "message": f"Campaign '{name}' already exists"},
            )
        raise

    for sid in payload.sender_ids:
        db.add(CampaignSender(
            campaign_id=camp.id,
            sender_id=sid,
            workspace_id=ctx.workspace_id,
        ))

    await db.commit()
    await db.refresh(camp)
    logger.info(
        f"[campaigns] created workspace={ctx.workspace_id} name='{name}' "
        f"id={camp.id} senders={len(payload.sender_ids)}"
    )
    resp = await _campaign_to_response(db, ctx, camp)
    return CampaignWriteResponse(campaign=resp, warnings=warnings)


# ── Endpoint: AI co-pilot auto-fill (UI-SPEC §5.5 — v1 stub) ─────────────────


class _AutoFillRequest(BaseModel):
    """UI-SPEC §5.5 AI co-pilot button payload — free-form brief text the user pasted.

    Phase 05.1 v1: brief is currently ignored; the endpoint returns canned defaults.
    """
    brief: Optional[str] = None


class _AutoFillResponse(BaseModel):
    """Canned defaults so the UI button works without an LLM call (v1 stub).

    Phase 11 D-13: success_criteria removed; auto-fill stub returns lead_trigger_hint instead.
    """
    name: str
    audience_hints: str
    primary_goal: str
    lead_trigger_hint: str
    tools: list


@router.post("/auto-fill", response_model=_AutoFillResponse)
async def auto_fill_campaign(
    _body: _AutoFillRequest,
    ctx: AuthCtx = Depends(auth_dep),
):
    """UI-SPEC §5.5 AI co-pilot — v1 stub returns canned defaults.

    RESEARCH §"Backend Gap Map" Campaigns explicitly defers LLM-driven auto-fill to v2.
    This endpoint exists so the UI button stops looking broken; the real implementation
    lands when /telemetry/core-value KPI proves users want the feature.

    Body shape is the same for v1 and v2 — clients can begin sending `brief` text now
    so the wire-format doesn't break when the LLM call lands.
    """
    return _AutoFillResponse(
        name="Untitled campaign",
        audience_hints="",
        primary_goal="book_meeting",
        lead_trigger_hint="",
        tools=[],
    )


@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """List campaigns in current workspace, optional filter by status."""
    q = select(Campaign).where(
        Campaign.workspace_id == ctx.workspace_id,
        # TODO(v2-rls): replaced by RLS policy
    )
    if status_filter:
        q = q.where(Campaign.status == status_filter)
    rows = (await db.execute(q.order_by(Campaign.created_at.desc()))).scalars().all()
    items = [await _campaign_to_response(db, ctx, c) for c in rows]
    return CampaignListResponse(items=items, total=len(items))


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    c = await _load_campaign(db, ctx, campaign_id)
    return await _campaign_to_response(db, ctx, c)


@router.patch("/{campaign_id}", response_model=CampaignWriteResponse)
async def patch_campaign(
    campaign_id: UUID,
    payload: CampaignUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Partial PATCH. On running campaign: agent_id / folder_id immutable."""
    c = await _load_campaign(db, ctx, campaign_id)
    update_data = payload.model_dump(exclude_unset=True)

    # Phase 22 (D-07): the per-campaign dialog-cap validation was removed; the daily
    # throttle is now the account grade budget. PATCH emits no cap warnings.
    warnings: List[WarningItem] = []

    if c.status == "running":
        forbidden = {"agent_id", "folder_id"}
        present = forbidden & set(update_data.keys())
        if present:
            raise HTTPException(
                status_code=409,
                detail={"code": "CAMPAIGN_RUNNING_IMMUTABLE_FIELDS",
                        "fields": sorted(present),
                        "message": "Cannot change agent_id / folder_id on running campaign"},
            )

    if "timezone" in update_data and update_data["timezone"] is not None:
        _validate_timezone(update_data["timezone"])

    if "agent_id" in update_data and update_data["agent_id"] is not None:
        await _validate_workspace_owns_agent(db, ctx, update_data["agent_id"])
    if "folder_id" in update_data and update_data["folder_id"] is not None:
        await _validate_workspace_owns_folder(db, ctx, update_data["folder_id"])

    if "name" in update_data and update_data["name"] is not None:
        new_name = update_data["name"].strip()
        if new_name != c.name:
            dup = (await db.execute(
                select(Campaign).where(
                    Campaign.workspace_id == ctx.workspace_id,
                    Campaign.name == new_name,
                )
            )).scalars().first()
            if dup is not None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "CAMPAIGN_NAME_DUPLICATE",
                            "message": f"Campaign '{new_name}' already exists"},
                )
        update_data["name"] = new_name

    for k in ("lead_webhook_url", "handoff_webhook_url", "finish_webhook_url", "webhook_url"):
        if k in update_data and update_data[k] is not None:
            update_data[k] = str(update_data[k])

    if "tools" in update_data and update_data["tools"] is not None:
        update_data["tools"] = [
            t.model_dump(mode="json") if hasattr(t, "model_dump") else t
            for t in update_data["tools"]
        ]

    # Phase 11 D-04: dialogue_flow PATCH = full replacement (mirrors tools/qa_pairs Pitfall 7).
    # DialogueStage objects from Pydantic must be serialised to dicts before writing to JSONB.
    if "dialogue_flow" in update_data and update_data["dialogue_flow"] is not None:
        update_data["dialogue_flow"] = [
            s.model_dump() if hasattr(s, "model_dump") else s
            for s in update_data["dialogue_flow"]
        ]

    # Detect a real message_template change BEFORE setattr overwrites the old value.
    # Editing the template must propagate to already-pending queue rows (they snapshot
    # the rendered opener at enqueue time — see project memory). Compared against the
    # stored value so an unchanged or unrelated PATCH does no queue work.
    template_changed = (
        "message_template" in update_data
        and update_data["message_template"] is not None
        and update_data["message_template"] != c.message_template
    )

    for k, v in update_data.items():
        setattr(c, k, v)

    # Re-render pending queue items in the SAME transaction as the template change,
    # so persistence is atomic. Reads c.message_template (already the new value).
    if template_changed:
        rerendered = await rerender_pending_queue(db, c)
        if rerendered:
            logger.info(
                "[campaigns] template changed id=%s — re-rendered %d pending queue item(s)",
                campaign_id, rerendered,
            )

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        msg = str(e.orig).lower()
        if "idx_campaigns_workspace_name" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=409,
                detail={"code": "CAMPAIGN_NAME_DUPLICATE"},
            )
        raise
    await db.refresh(c)
    resp = await _campaign_to_response(db, ctx, c)
    return CampaignWriteResponse(campaign=resp, warnings=warnings)


class _RerenderResponse(BaseModel):
    """POST /campaigns/{id}/rerender-pending result."""
    rerendered: int


class _RequeueFailedResponse(BaseModel):
    """POST /campaigns/{id}/requeue-failed result."""
    requeued_count: int


@router.post("/{campaign_id}/requeue-failed", response_model=_RequeueFailedResponse)
async def requeue_failed(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """WR-12b: re-pend all status='failed' queue rows for this campaign.

    A transient outage / restriction batch can leave items terminally failed;
    this re-pends them (status='pending', attempts=0, error_message/finished_at
    cleared, scheduled_at=NOW()) so the dispatcher retries them without a full
    re-enqueue from the folder. Workspace-scoped (404 for another workspace's
    campaign). Returns {"requeued_count": N}.
    """
    c = await _load_campaign(db, ctx, campaign_id)
    result = await db.execute(text("""
        UPDATE message_queue
        SET status='pending', attempts=0, error_message=NULL,
            finished_at=NULL, scheduled_at=NOW()
        WHERE campaign_id = :cid AND status = 'failed'
    """), {"cid": str(c.id)})
    await db.commit()
    count = result.rowcount or 0
    logger.info(
        "[campaigns] requeue-failed workspace=%s id=%s — %d item(s)",
        ctx.workspace_id, campaign_id, count,
    )
    return _RequeueFailedResponse(requeued_count=count)


@router.post("/{campaign_id}/rerender-pending", response_model=_RerenderResponse)
async def rerender_pending(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Re-render the message_text of all pending queue items for this campaign with
    the current message_template. On-demand counterpart to the automatic re-render
    in PATCH — lets the UI add a "refresh queue" button or recover after a template
    edit made through another path. Already-sent rows are not touched.
    """
    c = await _load_campaign(db, ctx, campaign_id)
    rerendered = await rerender_pending_queue(db, c)
    await db.commit()
    logger.info(
        "[campaigns] rerender-pending workspace=%s id=%s — %d item(s)",
        ctx.workspace_id, campaign_id, rerendered,
    )
    return _RerenderResponse(rerendered=rerendered)


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-07: hard delete. 409 на running. draft/paused/done → 204.

    FK semantics:
      - campaign_senders / campaign_contact_assignments: CASCADE
      - conversations.campaign_id / message_queue.campaign_id: SET NULL (Q1)
    """
    c = await _load_campaign(db, ctx, campaign_id)
    if c.status == "running":
        raise HTTPException(
            status_code=409,
            detail={"code": "CAMPAIGN_RUNNING",
                    "message": "Stop campaign before deleting"},
        )
    await _cancel_pending_queue(db, campaign_id, "campaign deleted")
    await db.delete(c)
    await db.commit()
    logger.info(f"[campaigns] deleted workspace={ctx.workspace_id} id={campaign_id}")
    return None


# ── Endpoints: Lifecycle ─────────────────────────────────────────────────────


@router.post("/{campaign_id}/start", response_model=CampaignResponse)
async def start_campaign(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """CAMP-08: draft|paused → running. Requires ≥1 sender + sender lock check (CAMP-04)."""
    c = await _load_campaign(db, ctx, campaign_id)
    if c.status not in ("draft", "paused"):
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_TRANSITION",
                    "from": c.status, "to": "running"},
        )

    # 024: readiness-guard — draft мог быть сохранён незавершённым; перед запуском
    # обязательны agent/folder/template. NO_SENDERS_ATTACHED проверяется ниже.
    missing = []
    if c.agent_id is None:
        missing.append("agent_id")
    if c.folder_id is None:
        missing.append("folder_id")
    if not (c.message_template or "").strip():
        missing.append("message_template")
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"code": "CAMPAIGN_INCOMPLETE",
                    "message": "Campaign is missing required fields before start",
                    "missing": missing},
        )

    sender_count = (await db.execute(
        select(sql_func.count()).select_from(CampaignSender)
        .where(CampaignSender.campaign_id == c.id)
    )).scalar()
    if sender_count == 0:
        raise HTTPException(
            status_code=422,
            detail={"code": "NO_SENDERS_ATTACHED",
                    "message": "Attach at least one sender before starting"},
        )

    conflicts = await _check_sender_lock(db, ctx, c.id)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={"code": "SENDER_LOCK_CONFLICT",
                    "conflicts": conflicts},
        )

    c.status = "running"
    c.pause_reason = None  # 029: clear any stale auto-pause reason on (re)start
    c.paused_at = None
    await db.commit()
    await db.refresh(c)
    logger.info(f"[campaigns] started id={campaign_id}")
    return await _campaign_to_response(db, ctx, c)


@router.post("/{campaign_id}/pause", response_model=CampaignResponse)
async def pause_campaign(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """running → paused."""
    c = await _load_campaign(db, ctx, campaign_id)
    if c.status != "running":
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_TRANSITION",
                    "from": c.status, "to": "paused"},
        )
    c.status = "paused"
    await db.commit()
    await db.refresh(c)
    logger.info(f"[campaigns] paused id={campaign_id}")
    return await _campaign_to_response(db, ctx, c)


@router.post("/{campaign_id}/resume", response_model=CampaignResponse)
async def resume_campaign(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """paused → running. D-04: re-check sender lock (другая кампания могла занять)."""
    c = await _load_campaign(db, ctx, campaign_id)
    if c.status != "paused":
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_TRANSITION",
                    "from": c.status, "to": "running"},
        )
    conflicts = await _check_sender_lock(db, ctx, c.id)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={"code": "SENDER_LOCK_CONFLICT",
                    "conflicts": conflicts},
        )
    c.status = "running"
    c.pause_reason = None  # 029: clear auto-pause reason — user is taking it back live
    c.paused_at = None
    await db.commit()
    await db.refresh(c)
    logger.info(f"[campaigns] resumed id={campaign_id}")
    return await _campaign_to_response(db, ctx, c)


@router.post("/{campaign_id}/finish", response_model=CampaignResponse)
async def finish_campaign(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """running|paused → done. Terminal."""
    c = await _load_campaign(db, ctx, campaign_id)
    if c.status not in ("running", "paused"):
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_TRANSITION",
                    "from": c.status, "to": "done"},
        )
    c.status = "done"
    await _cancel_pending_queue(db, campaign_id, "campaign finished")
    await db.commit()
    await db.refresh(c)
    logger.info(f"[campaigns] finished id={campaign_id}")
    return await _campaign_to_response(db, ctx, c)


@router.post("/{campaign_id}/stop", response_model=CampaignResponse)
async def stop_campaign(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """UI-SPEC §5.4/§5.6 alias — UI labels say "Stop", backend has historically been
    'finish'. Identical effect (running|paused → done, terminal).

    Phase 05.1 (UI-CAMPL-01): added as alias rather than rename to preserve Phase 4 tests
    against POST /{id}/finish.
    """
    return await finish_campaign(campaign_id, ctx, db)


# ── Endpoints: Duplicate ─────────────────────────────────────────────────────


@router.post("/{campaign_id}/duplicate", response_model=CampaignResponse, status_code=201)
async def duplicate_campaign(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Q2: copy campaigns row only. status='draft'.

    NOT copied: campaign_senders (the sender pool), message_queue items,
    campaign_contact_assignments. The duplicate is created with an EMPTY sender
    pool — the user attaches accounts explicitly. Carrying senders over caused
    accounts to appear locked inside the duplicate (held by the source's running
    campaign) and undeletable from its card.

    Name: '{name} (copy)' or '{name} (copy N)' if conflict.
    """
    src = await _load_campaign(db, ctx, campaign_id)

    base = f"{src.name} (copy)"
    candidate = base
    i = 1
    while True:
        existing = (await db.execute(
            select(Campaign).where(
                Campaign.workspace_id == ctx.workspace_id,
                Campaign.name == candidate,
            )
        )).scalars().first()
        if existing is None:
            break
        i += 1
        candidate = f"{src.name} (copy {i})"

    new_c = Campaign(
        workspace_id=ctx.workspace_id,
        name=candidate,
        description=src.description,
        agent_id=src.agent_id,
        folder_id=src.folder_id,
        message_template=src.message_template,
        timezone=src.timezone,
        work_hour_start=src.work_hour_start,
        work_hour_end=src.work_hour_end,
        work_days_mask=src.work_days_mask,
        start_date=src.start_date,
        stop_date=src.stop_date,
        lead_webhook_url=src.lead_webhook_url,
        handoff_webhook_url=src.handoff_webhook_url,
        finish_webhook_url=src.finish_webhook_url,
        lead_trigger_hint=src.lead_trigger_hint,
        handoff_trigger_hint=src.handoff_trigger_hint,
        finish_trigger_hint=src.finish_trigger_hint,
        tools=src.tools,
        # 05.1 v2 fields — duplicate should copy them for parity with src.
        audience_hints=src.audience_hints,
        primary_goal=src.primary_goal,
        # NB: success_criteria removed (Phase 11 D-13) — merged into lead_trigger_hint.
        webhook_url=src.webhook_url,
        # 026: per-campaign re-contact policy — copy for parity with src.
        allow_recontact=src.allow_recontact,
        recontact_min_age_days=src.recontact_min_age_days,
        # Phase 24 D-20: copy the variation flag so the duplicate is send-ready.
        variation_enabled=src.variation_enabled,
        # Phase 19 NORP-02/NORP-05: follow-up + auto-finish — copy for parity with src.
        follow_up_enabled=src.follow_up_enabled,
        follow_up_interval_hours=src.follow_up_interval_hours,
        follow_up_max_pings=src.follow_up_max_pings,
        auto_finish_hours=src.auto_finish_hours,
        # Phase 11 campaign fields (D-04/D-12/D-14) — copy for parity with src.
        dialogue_flow=src.dialogue_flow or [],
        arguments_facts=src.arguments_facts,
        campaign_rules=src.campaign_rules,
        # Prompt template v2 (migration 037) — copy for parity with src.
        objective_preset=src.objective_preset,
        disclosure_preset=src.disclosure_preset,
        authority_preset=src.authority_preset,
        style_examples=src.style_examples,
        status="draft",
    )
    db.add(new_c)
    # IN-06: the TOCTOU name-pick loop above can still lose a race to a concurrent
    # create/duplicate — the unique index is the race-safe backstop. Translate its
    # IntegrityError into 409 (was surfacing as a raw 500).
    try:
        await db.flush()
        # Phase 24 D-20 + 260709-dbl: copy ALL first-message attachment rows into NEW
        # rows for the duplicate (own campaign_attachments rows, order preserved) so the
        # copy is send-ready. Done in the SAME transaction as new_c — either all land or
        # none.
        atts = (await db.execute(text(
            "SELECT file_data, file_name, content_type, size_bytes, position "
            "FROM campaign_attachments WHERE campaign_id = :cid ORDER BY position, created_at"
        ), {"cid": str(src.id)})).all()
        for att in atts:
            db.add(CampaignAttachment(
                campaign_id=new_c.id,
                workspace_id=ctx.workspace_id,
                file_data=att.file_data,
                file_name=att.file_name,
                content_type=att.content_type,
                size_bytes=att.size_bytes,
                position=att.position,
            ))
        # Sender pool is NOT copied: the duplicate starts empty so accounts held by
        # the source's running campaign don't appear locked/undeletable in the copy.
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        msg = str(e.orig).lower()
        if "idx_campaigns_workspace_name" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=409,
                detail={"code": "CAMPAIGN_NAME_DUPLICATE",
                        "message": f"Campaign '{candidate}' already exists"},
            )
        raise
    await db.refresh(new_c)
    logger.info(
        f"[campaigns] duplicated workspace={ctx.workspace_id} "
        f"src={campaign_id} dst={new_c.id} name='{candidate}'"
    )
    return await _campaign_to_response(db, ctx, new_c)


# ── Endpoints: first-message attachment (Phase 24 — D-01/D-03/D-19) ──────────


@router.post("/{campaign_id}/attachment")
async def upload_attachment(
    campaign_id: UUID,
    files: list[UploadFile] = File(default=None),
    attachments: list[UploadFile] = File(default=None),
    file: UploadFile = File(default=None),
    attachment: UploadFile = File(default=None),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-19 + 260709-dbl: attach one OR MORE first-message files to a campaign.

    Accepts a `files` (or `attachments`) list for the multi-file album, and stays
    alias-tolerant to the legacy single-field callers (`file`/`attachment`) so the
    current Lovable frontend keeps working unchanged. Bytes stream straight to the
    BYTEA column (D-02 — no temp file).

    Validation (before any write): D-03 per-file MAX_ATTACHMENT_BYTES → 413
    FILE_TOO_LARGE; over MAX_ATTACHMENTS files → 400 TOO_MANY_ATTACHMENTS.

    Replace-all upsert: every upload REPLACES the campaign's whole attachment set
    (delete-then-insert all, ordered by position). Workspace-scoped via
    _load_campaign (cross-workspace → 404).
    """
    # Build one ordered upload list. Prefer the list fields; fall back to the legacy
    # single fields only if the lists are empty (avoid double-counting the same file).
    uploads: list[UploadFile] = [u for u in (files or []) if u is not None]
    uploads += [u for u in (attachments or []) if u is not None]
    if not uploads:
        single = file or attachment
        if single is not None:
            uploads = [single]

    if not uploads:
        raise HTTPException(
            status_code=422,
            detail={"code": "FILE_REQUIRED",
                    "message": "no file field (files|attachments|file|attachment)"},
        )

    if len(uploads) > MAX_ATTACHMENTS:
        raise HTTPException(
            status_code=400,
            detail={"code": "TOO_MANY_ATTACHMENTS",
                    "message": f"Max {MAX_ATTACHMENTS} files"},
        )

    # Read every blob and enforce the per-file size ceiling BEFORE writing any row.
    prepared: list[dict] = []
    for upload in uploads:
        raw = await upload.read()
        if len(raw) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"code": "FILE_TOO_LARGE",
                        "message": f"Max {MAX_ATTACHMENT_BYTES} bytes"},
            )
        prepared.append({
            "file_data": raw,
            "file_name": upload.filename or "file",
            "content_type": upload.content_type,
            "size_bytes": len(raw),
        })

    c = await _load_campaign(db, ctx, campaign_id)

    # Replace-all upsert: clear the existing set, then insert all uploads with position.
    await db.execute(
        delete(CampaignAttachment).where(CampaignAttachment.campaign_id == c.id)
    )
    for idx, p in enumerate(prepared):
        db.add(CampaignAttachment(
            campaign_id=c.id,
            workspace_id=ctx.workspace_id,
            file_data=p["file_data"],
            file_name=p["file_name"],
            content_type=p["content_type"],
            size_bytes=p["size_bytes"],
            position=idx,
        ))
    await db.commit()
    logger.info(
        "[campaigns] attachments stored workspace=%s campaign=%s count=%d bytes=%d",
        ctx.workspace_id, c.id, len(prepared), sum(p["size_bytes"] for p in prepared),
    )
    first = prepared[0]
    return {
        "campaign_id": str(c.id),
        "count": len(prepared),
        "attachments": [
            {
                "file_name": p["file_name"],
                "size_bytes": p["size_bytes"],
                "content_type": p["content_type"],
                "position": idx,
            }
            for idx, p in enumerate(prepared)
        ],
        # Back-compat: echo the FIRST file at top level so existing single-file
        # assertions and the current Lovable client keep reading the old shape.
        "file_name": first["file_name"],
        "size_bytes": first["size_bytes"],
        "content_type": first["content_type"],
    }


@router.delete("/{campaign_id}/attachment", status_code=204)
async def delete_attachment(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-19: remove the campaign's first-message attachment (idempotent → 204)."""
    c = await _load_campaign(db, ctx, campaign_id)
    await db.execute(
        delete(CampaignAttachment).where(CampaignAttachment.campaign_id == c.id)
    )
    await db.commit()
    return None


# ── Endpoints: Pool management (Phase 8 — POOL-01..06b) ──────────────────────


async def _recent_restriction_warnings(
    db: AsyncSession, ctx: AuthCtx, sender_id: UUID
) -> List[SenderAttachWarning]:
    """PFH-01: pre-flight "зелёный коридор" check for a pool attach.

    Returns a single advisory RECENT_RESTRICTION warning if the sender hit a
    non-'cleared' restriction event in the last 7 days ('cleared' is a RECOVERY
    event and is excluded). Empty list otherwise. Query mirrors the 7-day window
    pattern used by get_block_rate; workspace-scoped, newest in-window event.
    """
    row = (await db.execute(text("""
        SELECT event_type, restricted_until, created_at
          FROM sender_restriction_events
         WHERE sender_id = :sid
           AND workspace_id = :wid
           AND event_type <> 'cleared'
           AND created_at > now() - interval '7 days'
         ORDER BY created_at DESC
         LIMIT 1
    """), {"sid": str(sender_id), "wid": str(ctx.workspace_id)})).first()
    if row is None:
        return []
    event_type, restricted_until, created_at = row
    return [SenderAttachWarning(
        code="RECENT_RESTRICTION",
        sender_id=sender_id,
        message=(
            f"Account had a restriction event ({event_type}) in the last 7 days — "
            "attaching it may re-trigger anti-spam. Verify via @SpamBot before sending."
        ),
        event_type=event_type,
        restricted_until=restricted_until,
        last_event_at=created_at,
    )]


@router.post("/{campaign_id}/senders", response_model=CampaignResponse)
async def attach_sender(
    campaign_id: UUID,
    payload: CampaignSenderAttachRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """POOL-01/02/03: attach a sender to a draft/paused/running campaign pool.

    D-01: allowed on draft/paused/running — no status-transition guard, only the
    _load_campaign 404 scopes the campaign to the workspace.
    D-02: reuses _validate_workspace_owns_senders (404 SENDER_NOT_FOUND) and
    _check_sender_lock (409 SENDER_LOCK_CONFLICT — byte-identical to /start).
    D-08: triggers rebalance_on_attach only when the campaign is running.
    PFH-01: on success, attach_warnings[] carries a RECENT_RESTRICTION advisory if
    the sender hit a restriction event in the last 7 days (warning, NOT a block).
    PFH-02: attaching a role='checker' account requires force=true, else 409
    CHECKER_ROLE_CONFLICT — a checker consumed for sending can PEER_FLOOD out of
    both roles (restriction-gated selection excludes restricted checkers).
    """
    c = await _load_campaign(db, ctx, campaign_id)
    await _validate_workspace_owns_senders(db, ctx, [payload.sender_id])

    # PFH-02: role guard. Capture the role now (before commit) — the incoming
    # sender is already workspace-validated, so a plain fetch is enough.
    sender = (await db.execute(
        select(Sender).where(
            Sender.id == payload.sender_id,
            Sender.workspace_id == ctx.workspace_id,
        )
    )).scalars().first()
    sender_is_checker = sender is not None and sender.role == "checker"
    if sender_is_checker and not payload.force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHECKER_ROLE_CONFLICT",
                "message": (
                    "This account is in the checker pool (role='checker'); attaching "
                    "it as a campaign sender will consume it for contact-checking and "
                    "can PEER_FLOOD it out of both roles. Pass force=true to override."
                ),
                "sender_id": str(payload.sender_id),
            },
        )

    # Idempotency: PK is (campaign_id, sender_id) — no-op if already attached.
    existing = (await db.execute(
        select(CampaignSender).where(
            CampaignSender.campaign_id == c.id,
            CampaignSender.sender_id == payload.sender_id,
        )
    )).scalars().first()
    if existing is not None:
        return await _campaign_to_response(db, ctx, c)

    db.add(CampaignSender(
        campaign_id=c.id,
        sender_id=payload.sender_id,
        workspace_id=ctx.workspace_id,
    ))
    await db.flush()

    # D-02: insert-then-check-then-rollback so the incoming sender is in scope.
    # IN-05: only the newly-attached sender is checked — a pre-existing sender in
    # this pool that happens to be locked elsewhere must not block the attach.
    conflicts = await _check_sender_lock(db, ctx, c.id, only_sender_id=payload.sender_id)
    if conflicts:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "SENDER_LOCK_CONFLICT",
                    "conflicts": conflicts},
        )

    # D-08: back-fill the new sender from overloaded ones only on a running pool.
    if c.status == "running":
        await rebalance_on_attach(c.id, payload.sender_id, db)

    await db.commit()
    await db.refresh(c)
    logger.info(
        f"[campaigns] attached sender={payload.sender_id} "
        f"campaign={campaign_id} status={c.status}"
    )

    # PFH-01/PFH-02: advisory (non-blocking) warnings on the successful attach.
    warnings = await _recent_restriction_warnings(db, ctx, payload.sender_id)
    if sender_is_checker:  # reached only with force=true
        warnings.append(SenderAttachWarning(
            code="CHECKER_FORCE_ATTACHED",
            sender_id=payload.sender_id,
            message=(
                "Checker account force-attached as a campaign sender — it will be "
                "pulled out of the contact-check pool once it sends."
            ),
        ))
    resp = await _campaign_to_response(db, ctx, c)
    resp.attach_warnings = warnings
    return resp


@router.delete("/{campaign_id}/senders/{sender_id}", response_model=CampaignResponse)
async def detach_sender(
    campaign_id: UUID,
    sender_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """POOL-04/05/06/06b: detach a sender from a campaign pool.

    D-03 (min-pool): cannot remove the last sender of a running campaign → 409.
    D-04 (cold-pending): un-sent cold pending rows would be silently orphaned → 409.
    D-05 (engaged): dialogs with an open conversation never block detach.
    D-06: no auto-reassign of the cold backlog here — deferred to Phase 9.
    """
    c = await _load_campaign(db, ctx, campaign_id)

    # D-03 min-pool guard — only running campaigns must keep ≥1 sender.
    cnt = (await db.execute(
        select(sql_func.count()).select_from(CampaignSender)
        .where(CampaignSender.campaign_id == c.id)
    )).scalar()
    if c.status == "running" and cnt <= 1:
        raise HTTPException(
            status_code=409,
            detail={"code": "MIN_POOL_GUARD",
                    "message": "Cannot detach the last sender of a running "
                               "campaign. Pause it first."},
        )

    # D-04/D-05 cold-pending guard, scoped to the detached sender. The
    # NOT EXISTS conversations clause excludes engaged dialogs so they never
    # block detach (D-05/POOL-06b).
    has_cold = (await db.execute(text("""
        SELECT EXISTS (
          SELECT 1 FROM message_queue mq
          WHERE mq.campaign_id = :cid AND mq.sender_id = :sid AND mq.status = 'pending'
            AND NOT EXISTS (SELECT 1 FROM message_queue s
                            WHERE s.campaign_id = mq.campaign_id
                              AND s.recipient_phone = mq.recipient_phone
                              AND s.status = 'sent')
            AND NOT EXISTS (SELECT 1 FROM conversations cv
                            WHERE cv.workspace_id = mq.workspace_id
                              AND cv.contact_phone = mq.recipient_phone)
        )
    """), {"cid": str(c.id), "sid": str(sender_id)})).scalar()
    if has_cold:
        raise HTTPException(
            status_code=409,
            detail={"code": "DETACH_BLOCKED_PENDING",
                    "message": "This sender still has un-sent contacts in the "
                               "campaign. Pause the campaign or wait for the "
                               "queue to drain, then detach."},
        )

    await db.execute(delete(CampaignSender).where(
        CampaignSender.campaign_id == c.id,
        CampaignSender.sender_id == sender_id,
    ))
    await db.commit()
    await db.refresh(c)
    logger.info(
        f"[campaigns] detached sender={sender_id} campaign={campaign_id}"
    )
    return await _campaign_to_response(db, ctx, c)


# ── Campaign event log (quick 260710-cge) ────────────────────────────────────

# llm_calls.tool_calls element name → event type (built-in tools only,
# app/services/ai_engine.py::BUILT_IN_TOOL_NAMES).
_TOOL_EVENT_TYPES = {
    "mark_as_lead": "lead",
    "transfer_to_manager": "handoff",
    "finish_conversation": "dialog_finished",
}


@router.get("/{campaign_id}/events", response_model=CampaignEventsResponse)
async def campaign_events(
    campaign_id: UUID,
    before: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=100),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Read-only, newest-first campaign event log ("Лог кампании").

    Merges three existing sources — no dedicated events table, no migrations:
    - message_queue (status sent/failed) → message_sent / message_failed
    - llm_calls.tool_calls built-in tool invocations → lead / handoff /
      dialog_finished. tool_calls is the audit source of truth here —
      conversation.status gets overwritten, the log does not.
    - campaigns.paused_at/pause_reason (D-11 v2, deadline-mass-fail fix) →
      campaign_paused (current pause state only, not history).

    Cursor pagination: ``before`` (ISO datetime, defaults to now) + ``limit``.
    NOTE: the strict ``< :before`` cursor can theoretically skip events that
    share the exact same microsecond timestamp across a page boundary —
    accepted for MVP.
    """
    await _load_campaign(db, ctx, campaign_id)  # workspace scope, 404 otherwise

    if before is None:
        before = datetime.now(timezone.utc)
    params = {
        "cid": str(campaign_id),
        "wid": str(ctx.workspace_id),
        "before": before,
        "lim": limit + 1,
    }

    # Source 1 — message events. Only sent/failed rows are events; pending/
    # processing/cancelled are queue state, not history.
    queue_rows = (await db.execute(text("""
        SELECT COALESCE(q.finished_at, q.created_at) AS at,
               q.status::text                        AS status,
               COALESCE(q.result_recipient_name, q.recipient_name) AS contact_name,
               q.result_recipient_username           AS contact_username,
               q.recipient_phone                     AS contact_phone,
               q.error_message                       AS error_message,
               s.slug                                AS sender_slug,
               q.id::text                            AS row_id
        FROM message_queue q
        JOIN senders s ON s.id = q.sender_id
        WHERE q.campaign_id = :cid
          AND q.workspace_id = :wid
          AND q.status IN ('sent', 'failed')
          AND COALESCE(q.finished_at, q.created_at) < :before
        ORDER BY COALESCE(q.finished_at, q.created_at) DESC
        LIMIT :lim
    """), params)).all()

    # Source 2 — AI signal events from the llm_calls tool-call audit log.
    # jsonb_typeof(...) = 'array' in WHERE is MANDATORY: some rows store JSON
    # null (jsonb 'null', not SQL NULL) and jsonb_array_elements over a scalar
    # raises — this bug bit the campaign-detail redesign before.
    llm_rows = (await db.execute(text("""
        SELECT lc.created_at        AS at,
               tc.value->>'name'      AS tool_name,
               tc.value->>'arguments' AS arguments,
               conv.contact_name    AS contact_name,
               conv.contact_phone   AS contact_phone,
               s.slug               AS sender_slug,
               lc.id::text          AS row_id
        FROM llm_calls lc
        JOIN conversations conv ON conv.id = lc.conversation_id
        LEFT JOIN senders s ON s.id = lc.sender_id
        CROSS JOIN LATERAL jsonb_array_elements(lc.tool_calls) AS tc
        WHERE lc.campaign_id = :cid
          AND lc.workspace_id = :wid
          AND jsonb_typeof(lc.tool_calls) = 'array'
          AND tc.value->>'name' IN ('mark_as_lead', 'transfer_to_manager',
                                    'finish_conversation')
          AND lc.created_at < :before
        ORDER BY lc.created_at DESC
        LIMIT :lim
    """), params)).all()

    # Source 3 — D-11 v2 deadline auto-pause (deadline-mass-fail fix). At most
    # one row: reflects the CURRENT pause state, not a history of every past
    # pause (paused_at/pause_reason are overwritten on each pause/resume — same
    # limitation as the source columns, accepted for MVP like the `before`
    # cursor edge case above).
    pause_row = (await db.execute(text("""
        SELECT paused_at AS at, pause_reason AS detail
        FROM campaigns
        WHERE id = :cid AND workspace_id = :wid
          AND status = 'paused' AND pause_reason = 'past_stop_date'
          AND paused_at IS NOT NULL AND paused_at < :before
    """), params)).first()

    events: list[tuple] = []  # (at, tie_break, CampaignEvent)
    for r in queue_rows:
        etype = "message_sent" if r.status == "sent" else "message_failed"
        events.append((r.at, f"{etype}:{r.row_id}", CampaignEvent(
            type=etype,
            at=r.at,
            contact_name=r.contact_name,
            contact_username=r.contact_username,
            contact_phone=r.contact_phone,
            sender_slug=r.sender_slug,
            detail=r.error_message if etype == "message_failed" else None,
        )))
    for r in llm_rows:
        args = (r.arguments or "").strip()
        events.append((r.at, f"{r.tool_name}:{r.row_id}", CampaignEvent(
            type=_TOOL_EVENT_TYPES[r.tool_name],
            at=r.at,
            contact_name=r.contact_name,
            contact_username=None,
            contact_phone=r.contact_phone,
            sender_slug=r.sender_slug,
            detail=args[:200] if args else None,
        )))
    if pause_row is not None:
        events.append((pause_row.at, f"campaign_paused:{campaign_id}", CampaignEvent(
            type="campaign_paused",
            at=pause_row.at,
            contact_name=None,
            contact_username=None,
            contact_phone=None,
            sender_slug=None,
            detail=pause_row.detail,
        )))

    # Newest first; the tie-break string keeps ordering deterministic for
    # events sharing the same timestamp.
    events.sort(key=lambda e: (e[0], e[1]), reverse=True)
    has_more = len(events) > limit
    page = [e[2] for e in events[:limit]]
    return CampaignEventsResponse(
        events=page,
        next_before=page[-1].at if page else None,
        has_more=has_more,
    )
