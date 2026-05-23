"""Phase 5 analytics router — 4 read-only endpoints (workspace / campaigns / agents / senders).

Per D-13: real-time COUNT() per request. NO background workers, NO materialized
views, NO pre-aggregated counters. All 4 endpoints return identical
``AnalyticsCards`` schema (D-16).

Per D-14: all-time only. No ``?from=&to=`` query params.

Sources resolved (Phase 5 RESEARCH):
- C-01: «Отправлено» source = ``messages JOIN conversations`` (covers queue
  worker, listener self-checks, и UI manager-send D-04; ``messages_log`` и
  ``message_queue`` пропускают manager-send).
- D-15: «Отвечено» = две цифры в одном SELECT
  (``COUNT(DISTINCT m.conversation_id)`` + ``COUNT(*)``).
- Pitfall 8: ВСЕ counts исключают ``c.status != 'bot_ignored'``.
- Pitfall 9: ``leads`` и ``finishes`` — mutually exclusive
  (``status='lead'`` НЕ включает ``finished``).
"""

import logging
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AIContext, Campaign, Sender
from app.schemas import (
    AnalyticsCards,
    AnalyticsReplied,
    FunnelResponse,
    LLMAggregatesResponse,
)
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


# === Workspace-scope prechecks (T-05-02-WS-ISOLATION) ========================
#
# Per-resource endpoints (/campaigns/{id}, /agents/{id}, /senders/{id}) выполняют
# preprolance SELECT WHERE id=:rid AND workspace_id=:wid — 404 на cross-workspace
# ДО _compute_cards. Внутри _compute_cards ВСЕ 4 raw-SQL COUNT'а имеют
# WHERE c.workspace_id=:wid (defence-in-depth — workspace boundary даже если
# scope_id из чужого workspace utterly прошёл бы prequery).


async def _ensure_campaign_in_workspace(
    db: AsyncSession, ctx: AuthCtx, campaign_id: UUID
) -> None:
    row = (await db.execute(select(Campaign.id).where(
        Campaign.id == campaign_id,
        Campaign.workspace_id == ctx.workspace_id,
        # TODO(v2-rls): replaced by RLS policy app.workspace_id
    ))).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CAMPAIGN_NOT_FOUND",
                    "message": "Campaign not found in your workspace"},
        )


async def _ensure_agent_in_workspace(
    db: AsyncSession, ctx: AuthCtx, agent_id: UUID
) -> None:
    row = (await db.execute(select(AIContext.id).where(
        AIContext.id == agent_id,
        AIContext.workspace_id == ctx.workspace_id,
        # TODO(v2-rls): replaced by RLS policy app.workspace_id
    ))).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_NOT_FOUND",
                    "message": "Agent not found in your workspace"},
        )


async def _ensure_sender_in_workspace(
    db: AsyncSession, ctx: AuthCtx, sender_id: UUID
) -> None:
    row = (await db.execute(select(Sender.id).where(
        Sender.id == sender_id,
        Sender.workspace_id == ctx.workspace_id,
        # TODO(v2-rls): replaced by RLS policy app.workspace_id
    ))).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SENDER_NOT_FOUND",
                    "message": "Sender not found in your workspace"},
        )


# === Core helper: 4 COUNT'ов одной shape per scope ===========================

# T-05-02-COUNT-EXFIL mitigation: scope column whitelist для безопасной композиции
# scope_clause через f-string. Никакого dynamic SQL composition по другим колонкам
# (workspace_id всегда — первый WHERE; scope column — только из этого set'а).
_ALLOWED_SCOPE_COLUMNS = {"campaign_id", "ai_context_id", "sender_id"}


# Phase 05.1 (UI-DASH-01 + UI-CAMPD-01): scope-column whitelists для новых
# /funnel и /llm endpoint'ов. Тот же pattern что _ALLOWED_SCOPE_COLUMNS выше —
# scope query-param mapping → раз-валидированная SQL колонка через f-string,
# scope_val привязывается параметром :scope_val (никогда не интерполируется).
_FUNNEL_SCOPE_COLUMNS = {
    "campaign": "c.campaign_id",
    "agent": "c.ai_context_id",
    "sender": "c.sender_id",
}
_LLM_SCOPE_COLUMNS = {
    "campaign": "lc.campaign_id",
}
_SINCE_DAYS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
# scope→tableName для cross-workspace 404 prequery в /funnel.
_FUNNEL_SCOPE_TABLE = {
    "campaign": "campaigns",
    "agent": "ai_contexts",
    "sender": "senders",
}


async def _compute_cards(
    db: AsyncSession,
    workspace_id: UUID,
    scope: Optional[tuple[str, UUID]] = None,
) -> AnalyticsCards:
    """Run 4 raw-SQL COUNT'ов для одного scope.

    ``scope=None``       → workspace-only (no extra column filter).
    ``scope=(col, val)`` → дополнительный ``AND c.{col} = :scope_val`` per
    whitelist ``_ALLOWED_SCOPE_COLUMNS``.

    Per D-13: real-time COUNT() per request. Per D-16: identical AnalyticsCards
    shape per scope. Per Pitfall 8: ``c.status != 'bot_ignored'`` исключает
    bot dialogs из всех counts (sent + replied — где bot dialogs физически могут
    появиться; для leads/finishes условие избыточно но сохраняется для единства).
    Per D-15: replied — один SELECT с двумя агрегатами.

    Composite indexes из migration 017 покрывают workspace+scope+status фильтры:
    - idx_conversations_workspace_campaign_status
    - idx_conversations_workspace_agent_status
    - idx_conversations_workspace_sender_status
    """
    scope_clause = ""
    params: dict = {"wid": str(workspace_id)}
    if scope is not None:
        col, val = scope
        if col not in _ALLOWED_SCOPE_COLUMNS:
            raise ValueError(f"Invalid scope column: {col}")
        scope_clause = f" AND c.{col} = :scope_val"
        params["scope_val"] = str(val)

    # 1. Sent — source = messages (C-01: единственный источник, содержащий
    # outbound от queue worker + listener self-checks + UI manager-send D-04).
    sent = (await db.execute(text(f"""
        SELECT COUNT(*)
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.workspace_id = :wid
          AND c.status != 'bot_ignored'
          {scope_clause}
          AND m.direction = 'outbound'
    """), params)).scalar() or 0

    # 2. Replied — D-15 two figures в одном SELECT (один проход по индексу).
    replied_row = (await db.execute(text(f"""
        SELECT
            COUNT(DISTINCT m.conversation_id) AS conv_count,
            COUNT(*)                          AS msg_count
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.workspace_id = :wid
          AND c.status != 'bot_ignored'
          {scope_clause}
          AND m.direction = 'inbound'
          AND m.sent_by = 'contact'
    """), params)).first()

    # 3. Leads — Pitfall 9: status='lead' strict EQ (НЕ включает 'finished').
    # UI Lovable рендерит label «Активные лиды (ещё не финишировали)».
    leads = (await db.execute(text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE c.workspace_id = :wid
          AND c.status = 'lead'
          AND c.status != 'bot_ignored'
          {scope_clause}
    """), params)).scalar() or 0

    # 4. Finishes — status='finished' strict EQ.
    finishes = (await db.execute(text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE c.workspace_id = :wid
          AND c.status = 'finished'
          AND c.status != 'bot_ignored'
          {scope_clause}
    """), params)).scalar() or 0

    conv_count = (replied_row.conv_count if replied_row else 0) or 0
    msg_count = (replied_row.msg_count if replied_row else 0) or 0

    return AnalyticsCards(
        sent=sent,
        replied=AnalyticsReplied(
            conversation_count=conv_count,
            message_count=msg_count,
        ),
        leads=leads,
        finishes=finishes,
    )


# === 4 endpoints (identical AnalyticsCards shape per D-16) ====================


@router.get("/workspace", response_model=AnalyticsCards)
async def workspace_analytics(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCards:
    """ANLX-01 — метрики workspace юзера (all-time, real-time)."""
    return await _compute_cards(db, ctx.workspace_id, scope=None)


@router.get("/campaigns/{campaign_id}", response_model=AnalyticsCards)
async def campaign_analytics(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCards:
    """ANLX-02 — метрики одной кампании. 404 на cross-workspace campaign."""
    await _ensure_campaign_in_workspace(db, ctx, campaign_id)
    return await _compute_cards(
        db, ctx.workspace_id, scope=("campaign_id", campaign_id)
    )


@router.get("/agents/{agent_id}", response_model=AnalyticsCards)
async def agent_analytics(
    agent_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCards:
    """ANLX-04 — метрики одного агента. 404 на cross-workspace agent.

    Per D-16: ``agent.campaign_count`` лежит в /api/v1/agents (Phase 3) — НЕ здесь.
    """
    await _ensure_agent_in_workspace(db, ctx, agent_id)
    return await _compute_cards(
        db, ctx.workspace_id, scope=("ai_context_id", agent_id)
    )


@router.get("/senders/{sender_id}", response_model=AnalyticsCards)
async def sender_analytics(
    sender_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCards:
    """ANLX-03 — метрики одного sender'а. 404 на cross-workspace sender.

    Per D-16: sender errors (FloodWait/Failed/auth) лежат на странице sender
    (Phase 2 SNDR-03) — НЕ здесь.
    """
    await _ensure_sender_in_workspace(db, ctx, sender_id)
    return await _compute_cards(
        db, ctx.workspace_id, scope=("sender_id", sender_id)
    )


# === Phase 05.1: UI-DASH-01 Sankey funnel + UI-CAMPD-01 LLM aggregates ========


@router.get("/funnel", response_model=FunnelResponse)
async def funnel(
    scope: Literal["workspace", "campaign", "agent", "sender"] = "workspace",
    id: Optional[UUID] = None,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> FunnelResponse:
    """UI-SPEC §5.3 dashboard Sankey funnel — 5 stage counts.

    Stages: Sent → Replied → Engaged → Lead → Handoff.

    'engaged' definition LOCKED per RESEARCH Pitfall 5:
        COUNT(DISTINCT conversation_id) where >= 2 inbound contact messages AND
        status NOT IN ('lead','handoff','finished','bot_ignored').

    bot_ignored conversations are excluded from EVERY count (Phase 5 Pitfall 8
    carry-over). Counts are monotonically non-increasing under typical seeded
    data but the SQL does not enforce monotonicity — callers must not assume
    sent ≥ replied at the row level (e.g. a manual "human" message can land
    before any AI outbound).

    Per Pitfall 8 / Pitfall 9 (Phase 5): scope column whitelisted via
    `_FUNNEL_SCOPE_COLUMNS`; scope value bound as `:scope_val` (no f-string
    interpolation of user input into SQL).
    """
    if scope != "workspace":
        if id is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "ID_REQUIRED",
                        "message": f"id query-param required for scope={scope}"},
            )
        scope_col = _FUNNEL_SCOPE_COLUMNS[scope]
        scope_clause = f" AND {scope_col} = :scope_val"
        params: dict = {"wid": str(ctx.workspace_id), "scope_val": str(id)}

        # Cross-workspace 404 — silent (do not leak existence of other-ws ids).
        owns_table = _FUNNEL_SCOPE_TABLE[scope]
        owns = (await db.execute(text(f"""
            SELECT 1 FROM {owns_table}
            WHERE id = :scope_val AND workspace_id = :wid LIMIT 1
        """), params)).first()
        if owns is None:
            raise HTTPException(
                status_code=404,
                detail={"code": f"{scope.upper()}_NOT_FOUND",
                        "message": f"{scope} not found in your workspace"},
            )
    else:
        scope_clause = ""
        params = {"wid": str(ctx.workspace_id)}

    # 1. sent — outbound messages joined to conversations
    # (Phase 5 C-01: messages is the source-of-truth — covers queue worker +
    # listener self-checks + UI manager-send D-04).
    sent = (await db.execute(text(f"""
        SELECT COUNT(*) FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.workspace_id = :wid
          AND c.status != 'bot_ignored'
          {scope_clause}
          AND m.direction = 'outbound'
    """), params)).scalar() or 0

    # 2. replied — distinct conversations with >= 1 inbound contact message.
    replied = (await db.execute(text(f"""
        SELECT COUNT(DISTINCT m.conversation_id) FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.workspace_id = :wid
          AND c.status != 'bot_ignored'
          {scope_clause}
          AND m.direction = 'inbound'
          AND m.sent_by = 'contact'
    """), params)).scalar() or 0

    # 3. engaged — >= 2 inbound contact messages AND status NOT IN terminal/bot.
    # Definition locked per RESEARCH.md Pitfall 5 — DO NOT drift this clause.
    engaged = (await db.execute(text(f"""
        SELECT COUNT(*) FROM (
            SELECT m.conversation_id
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE c.workspace_id = :wid
              AND c.status NOT IN ('lead','handoff','finished','bot_ignored')
              {scope_clause}
              AND m.direction = 'inbound'
              AND m.sent_by = 'contact'
            GROUP BY m.conversation_id
            HAVING COUNT(*) >= 2
        ) e
    """), params)).scalar() or 0

    # 4. lead — conversations with status='lead' (strict EQ per Phase 5
    # Pitfall 9; 'lead' is mutually exclusive with 'finished').
    lead = (await db.execute(text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE c.workspace_id = :wid
          AND c.status = 'lead'
          {scope_clause}
    """), params)).scalar() or 0

    # 5. handoff — conversations with status='handoff' (AI-triggered manager
    # transfer). UI-SPEC §8.3 also maps 'manual' to "Manager" mode but that is
    # a human-driven takeover — not a funnel stage. Funnel locks to 'handoff'.
    handoff = (await db.execute(text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE c.workspace_id = :wid
          AND c.status = 'handoff'
          {scope_clause}
    """), params)).scalar() or 0

    return FunnelResponse(
        sent=sent, replied=replied, engaged=engaged, lead=lead, handoff=handoff,
    )


@router.get("/llm", response_model=LLMAggregatesResponse)
async def llm_aggregates(
    scope: Literal["workspace", "campaign"] = "workspace",
    id: Optional[UUID] = None,
    since: Literal["1d", "7d", "30d", "90d"] = "7d",
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> LLMAggregatesResponse:
    """UI-SPEC §5.6 LLM trace tab — top-of-tab aggregates over since-window.

    Returns total_calls / avg_latency_ms / prompt_tokens / completion_tokens /
    total_tokens / spend_usd_cents. spend_usd_cents is 0 in v1 — per-model
    pricing is deferred to v2 (RESEARCH §"Backend Gap Map" note).

    scope=workspace counts every LLM call in the workspace. scope=campaign
    filters by `llm_calls.campaign_id = :scope_val` (404 if cross-workspace).

    avg_latency_ms is None when no rows match — Pydantic Optional handles this.
    """
    days = _SINCE_DAYS[since]
    if scope == "campaign":
        if id is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "ID_REQUIRED",
                        "message": "id query-param required for scope=campaign"},
            )
        owns = (await db.execute(text("""
            SELECT 1 FROM campaigns
            WHERE id = :scope_val AND workspace_id = :wid LIMIT 1
        """), {"wid": str(ctx.workspace_id), "scope_val": str(id)})).first()
        if owns is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "CAMPAIGN_NOT_FOUND",
                        "message": "Campaign not found in your workspace"},
            )
        scope_clause = " AND lc.campaign_id = :scope_val"
        params = {"wid": str(ctx.workspace_id), "scope_val": str(id), "days": str(days)}
    else:
        scope_clause = ""
        params = {"wid": str(ctx.workspace_id), "days": str(days)}

    row = (await db.execute(text(f"""
        SELECT
            COUNT(*)                                    AS total_calls,
            CAST(ROUND(AVG(lc.latency_ms)) AS INT)      AS avg_latency_ms,
            COALESCE(SUM(lc.prompt_tokens), 0)::INT     AS prompt_tokens,
            COALESCE(SUM(lc.completion_tokens), 0)::INT AS completion_tokens,
            COALESCE(SUM(lc.total_tokens), 0)::INT      AS total_tokens
        FROM llm_calls lc
        WHERE lc.workspace_id = :wid
          AND lc.created_at >= NOW() - (:days || ' days')::INTERVAL
          {scope_clause}
    """), params)).first()

    return LLMAggregatesResponse(
        total_calls=(row.total_calls if row else 0) or 0,
        avg_latency_ms=row.avg_latency_ms if row else None,
        prompt_tokens=(row.prompt_tokens if row else 0) or 0,
        completion_tokens=(row.completion_tokens if row else 0) or 0,
        total_tokens=(row.total_tokens if row else 0) or 0,
        spend_usd_cents=0,  # v1 stub — RESEARCH defers per-model pricing
    )
