"""Agents router (Phase 3 — AGNT-01..04).

Workspace-scoped CRUD для AI-агентов (шаблонов промпта/правил/FAQ).

API-resource = «agent» (Pydantic schemas, OpenAPI tag), DB-table = `ai_contexts`
(D-02 — переиспользуем существующую таблицу без переименования).

Endpoints:
    GET    /api/v1/agents             — list workspace agents (с campaign_count=0)
    POST   /api/v1/agents             — create (409 на дубль (workspace_id, name))
    GET    /api/v1/agents/{id}        — single agent
    PATCH  /api/v1/agents/{id}        — partial update
    DELETE /api/v1/agents/{id}        — hard delete (FK cascades)
    POST   /api/v1/agents/{id}/duplicate — copy → "(copy)" / "(copy N)"

All endpoints под Depends(auth_dep) + .where(AIContext.workspace_id == ctx.workspace_id).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AIContext
from app.schemas import (
    AgentCreate,
    AgentListResponse,
    AgentResponse,
    AgentUpdate,
    FaqItem,
)
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _agent_to_response(agent: AIContext, campaign_count: int = 0) -> AgentResponse:
    """Build AgentResponse. campaign_count must be computed by caller (Phase 4 D-10 closure).

    Phase 4 close: campaign_count теперь реальный SELECT COUNT(*) FROM campaigns WHERE agent_id=...
    (см. _campaign_counts_for_agents helper). Default 0 для безопасности (если caller забыл передать).
    """
    faq_data = agent.faq if agent.faq else []
    # If FAQ stored as legacy dict form — coerce to empty list for safety
    if not isinstance(faq_data, list):
        faq_data = []
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        system_prompt=agent.system_prompt,
        rules=agent.rules,
        tone_of_voice=agent.tone_of_voice,
        faq=[FaqItem(**item) for item in faq_data],
        company_info=agent.company_info,
        product_info=agent.product_info,
        campaign_count=campaign_count,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


async def _campaign_counts_for_agents(
    db: AsyncSession, workspace_id: UUID
) -> dict[UUID, int]:
    """Phase 4 close: SELECT COUNT(*) FROM campaigns WHERE agent_id=... per agent.

    Returns dict {agent_id: count} for ALL agents in workspace. Filters by
    workspace as defence-in-depth (campaigns FK-scoped already but explicit filter is safer).
    """
    rows = (await db.execute(text("""
        SELECT a.id, COUNT(c.id)
        FROM ai_contexts a
        LEFT JOIN campaigns c
               ON c.agent_id = a.id
              AND c.workspace_id = a.workspace_id
        WHERE a.workspace_id = :wid
        GROUP BY a.id
    """), {"wid": str(workspace_id)})).fetchall()
    return {row[0]: row[1] for row in rows}


async def _count_campaigns_for_agent(
    db: AsyncSession, workspace_id: UUID, agent_id: UUID
) -> int:
    """Single-agent count for GET /{id} endpoint."""
    row = (await db.execute(text("""
        SELECT COUNT(*) FROM campaigns
        WHERE agent_id = :aid AND workspace_id = :wid
    """), {"aid": str(agent_id), "wid": str(workspace_id)})).first()
    return int(row[0]) if row else 0


async def _load_agent(db: AsyncSession, ctx: AuthCtx, agent_id: UUID) -> AIContext:
    """Workspace-scoped SELECT by id. 404 если cross-tenant или не существует."""
    result = await db.execute(
        select(AIContext).where(
            AIContext.id == agent_id,
            AIContext.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
    )
    agent = result.scalars().first()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_NOT_FOUND", "message": "Agent not found"},
        )
    return agent


async def _generate_duplicate_name(
    db: AsyncSession, workspace_id: UUID, base_name: str
) -> str:
    """Generate '{name} (copy)' or '{name} (copy N)' for next free N.

    Pattern 4 (RESEARCH): pre-fetch conflicts via LIKE — first free index wins.
    Race protection: caller wraps INSERT in retry-on-IntegrityError loop (Pitfall 2).
    """
    pattern_no_n = f"{base_name} (copy)"
    pattern_with_n = f"{base_name} (copy %)"
    result = await db.execute(
        text("""
            SELECT name FROM ai_contexts
            WHERE workspace_id = :wid
              AND (name = :exact OR name LIKE :pattern)
        """),
        {"wid": str(workspace_id), "exact": pattern_no_n, "pattern": pattern_with_n},
    )
    existing = {row[0] for row in result.fetchall()}
    if pattern_no_n not in existing:
        return pattern_no_n
    n = 2
    while f"{base_name} (copy {n})" in existing:
        n += 1
    return f"{base_name} (copy {n})"


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get("", response_model=AgentListResponse)
async def list_agents(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """List all agents in current workspace. Phase 4: campaign_count is real SELECT COUNT."""
    result = await db.execute(
        select(AIContext)
        .where(AIContext.workspace_id == ctx.workspace_id)
        # TODO(v2-rls): replaced by RLS policy
        .order_by(AIContext.created_at.desc())
    )
    agents = result.scalars().all()
    counts = await _campaign_counts_for_agents(db, ctx.workspace_id)
    return AgentListResponse(
        agents=[_agent_to_response(a, campaign_count=counts.get(a.id, 0)) for a in agents],
        total=len(agents),
    )


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    payload: AgentCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Create new agent. 409 на дубль (workspace_id, name) (Pattern 2)."""
    name = payload.name.strip()
    existing = await db.execute(
        select(AIContext).where(
            AIContext.workspace_id == ctx.workspace_id,
            AIContext.name == name,
        )
        # TODO(v2-rls): replaced by RLS policy
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AGENT_NAME_DUPLICATE",
                "message": f"Agent '{name}' already exists",
            },
        )
    agent = AIContext(
        workspace_id=ctx.workspace_id,
        name=name,
        system_prompt=payload.system_prompt,
        rules=payload.rules,
        tone_of_voice=payload.tone_of_voice,
        faq=[item.model_dump() for item in payload.faq],
        company_info=payload.company_info,
        product_info=payload.product_info,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    logger.info(
        f"[agents] created workspace={ctx.workspace_id} name='{name}' id={agent.id}"
    )
    # New agent → 0 campaigns yet.
    return _agent_to_response(agent, campaign_count=0)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Get agent by id (workspace-scoped). 404 если cross-tenant."""
    agent = await _load_agent(db, ctx, agent_id)
    cnt = await _count_campaigns_for_agent(db, ctx.workspace_id, agent.id)
    return _agent_to_response(agent, campaign_count=cnt)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: UUID,
    payload: AgentUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Partial PATCH (Phase 2 convention). FAQ = full replacement (Pitfall 7)."""
    agent = await _load_agent(db, ctx, agent_id)

    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name != agent.name:
            # Duplicate check для rename (Pattern 2)
            dup = await db.execute(
                select(AIContext).where(
                    AIContext.workspace_id == ctx.workspace_id,
                    AIContext.name == new_name,
                )
            )
            if dup.scalars().first():
                raise HTTPException(
                    status_code=409,
                    detail={"code": "AGENT_NAME_DUPLICATE",
                            "message": f"Agent '{new_name}' already exists"},
                )
        agent.name = new_name
    if payload.system_prompt is not None:
        agent.system_prompt = payload.system_prompt
    if payload.rules is not None:
        agent.rules = payload.rules
    if payload.tone_of_voice is not None:
        agent.tone_of_voice = payload.tone_of_voice
    if payload.faq is not None:
        # Pitfall 7: full replacement (not merge)
        agent.faq = [item.model_dump() for item in payload.faq]
    if payload.company_info is not None:
        agent.company_info = payload.company_info
    if payload.product_info is not None:
        agent.product_info = payload.product_info

    await db.commit()
    await db.refresh(agent)
    logger.info(f"[agents] updated workspace={ctx.workspace_id} id={agent_id}")
    cnt = await _count_campaigns_for_agent(db, ctx.workspace_id, agent.id)
    return _agent_to_response(agent, campaign_count=cnt)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-08: hard delete. Phase 4 close (D-09): 409 если есть running campaign на этом agent."""
    agent = await _load_agent(db, ctx, agent_id)

    # Phase 4 close D-09: block DELETE if active (running) campaign references this agent.
    # FK ON DELETE RESTRICT also enforces this at DB level for any non-deleted campaign,
    # but explicit 409 with detail{campaigns: [...]} is friendlier UX.
    active = (await db.execute(text("""
        SELECT id, name FROM campaigns
        WHERE agent_id = :aid AND workspace_id = :wid AND status = 'running'
        ORDER BY name
    """), {"aid": str(agent_id), "wid": str(ctx.workspace_id)})).fetchall()
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AGENT_USED_BY_RUNNING_CAMPAIGN",
                "message": "Cannot delete agent — used by running campaign(s)",
                "campaigns": [{"id": str(r[0]), "name": r[1]} for r in active],
            },
        )

    await db.delete(agent)
    await db.commit()
    logger.info(f"[agents] deleted workspace={ctx.workspace_id} id={agent_id}")
    return None


@router.post("/{agent_id}/duplicate", response_model=AgentResponse, status_code=201)
async def duplicate_agent(
    agent_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-07: POST /{id}/duplicate без body. Auto-name '(copy)' / '(copy N)'.

    Pitfall 2: retry-on-IntegrityError loop (max 5 retries) защищает от parallel POST race.
    """
    original = await _load_agent(db, ctx, agent_id)

    for attempt in range(5):
        new_name = await _generate_duplicate_name(db, ctx.workspace_id, original.name)
        new_agent = AIContext(
            workspace_id=ctx.workspace_id,
            name=new_name,
            system_prompt=original.system_prompt,
            rules=original.rules,
            tone_of_voice=original.tone_of_voice,
            faq=original.faq,
            company_info=original.company_info,
            product_info=original.product_info,
        )
        db.add(new_agent)
        try:
            await db.commit()
            await db.refresh(new_agent)
            logger.info(
                f"[agents] duplicated workspace={ctx.workspace_id} "
                f"src={agent_id} dst={new_agent.id} name='{new_name}'"
            )
            return _agent_to_response(new_agent, campaign_count=0)
        except IntegrityError:
            await db.rollback()
            continue

    raise HTTPException(
        status_code=409,
        detail={"code": "DUPLICATE_RACE",
                "message": "Failed to allocate unique name after 5 retries"},
    )
