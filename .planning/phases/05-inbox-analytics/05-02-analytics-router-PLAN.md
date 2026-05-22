---
phase: 05-inbox-analytics
plan: 02
type: execute
wave: 2
depends_on: ["05-01"]
files_modified:
  - app/routers/analytics.py
  - app/schemas/__init__.py
  - app/main.py
  - tests/test_phase5_analytics.py
  - tests/test_phase5_analytics_correctness.py
autonomous: true
requirements: [ANLX-01, ANLX-02, ANLX-03, ANLX-04]
requirements_addressed: [ANLX-01, ANLX-02, ANLX-03, ANLX-04]
gap_closure: false

must_haves:
  truths:
    - "GET /api/v1/analytics/workspace возвращает AnalyticsCards для workspace юзера: {sent: int, replied: {conversation_count: int, message_count: int}, leads: int, finishes: int}; 401 без credentials; workspace-scoped (никаких чужих counts в выдаче)"
    - "GET /api/v1/analytics/campaigns/{id} возвращает identical AnalyticsCards schema со scope WHERE c.campaign_id=:cid; 404 на cross-workspace campaign"
    - "GET /api/v1/analytics/agents/{id} возвращает identical schema со scope WHERE c.ai_context_id=:aid; 404 на cross-workspace agent"
    - "GET /api/v1/analytics/senders/{id} возвращает identical schema со scope WHERE c.sender_id=:sid; 404 на cross-workspace sender"
    - "Все 4 endpoints используют один helper _compute_cards(db, workspace_id, scope=None|tuple) — 4 raw-text COUNT'а в одной функции: sent (messages.direction='outbound' JOIN conversations.workspace_id), replied (two figures из D-15: COUNT(DISTINCT conversation_id) + COUNT(*) на messages.direction='inbound' AND sent_by='contact'), leads (conversations.status='lead'), finishes (conversations.status='finished'). Все 4 query'а исключают status='bot_ignored' через WHERE c.status != 'bot_ignored' (Pitfall 8)"
    - "Источник «Отправлено» — messages JOIN conversations (C-01: НЕ messages_log, НЕ message_queue — единственный источник содержащий outbound от queue worker, listener self-checks И UI manager send D-04)"
    - "D-13 real-time COUNT() — никаких background workers, никаких materialized views, никаких pre-aggregated counters. Нет changes в lifespan main.py (background workers list остаётся 5)"
    - "D-14 all-time — endpoints НЕ принимают ?from=&to= параметры. Counts с момента создания entity"
    - "D-16 одинаковые карточки на всех 4 уровнях. AnalyticsCards Pydantic schema используется всеми 4 endpoints без вариаций per-level"
    - "Composite indexes на conversations (созданные в Task 1 migration 017) используются для real-time COUNT'ов: idx_conversations_workspace_campaign_status, idx_conversations_workspace_agent_status, idx_conversations_workspace_sender_status"
    - "Pitfall 9 — leads/finishes mutually exclusive (per CONTEXT.md D-16 verbatim): leads=COUNT WHERE status='lead' (не включает finished). UI label: «Активные лиды (ещё не финишировали)». Документировано в docstring endpoint'ов"
  artifacts:
    - path: "app/routers/analytics.py"
      provides: "4 endpoints workspace/campaigns/agents/senders + _compute_cards helper + 3 _ensure_*_in_workspace prechecks"
      contains: "@router.get(\"/workspace\"), @router.get(\"/campaigns/{campaign_id}\"), @router.get(\"/agents/{agent_id}\"), @router.get(\"/senders/{sender_id}\"), async def _compute_cards"
    - path: "app/schemas/__init__.py"
      provides: "Phase 5 analytics Pydantic schemas: AnalyticsReplied, AnalyticsCards"
      contains: "class AnalyticsReplied, class AnalyticsCards"
    - path: "app/main.py"
      provides: "app.include_router(analytics.router) — новый роутер"
      contains: "include_router(analytics.router)"
  key_links:
    - from: "app/main.py"
      to: "app/routers/analytics.py"
      via: "app.include_router(analytics.router)"
      pattern: "include_router\\(analytics\\.router\\)"
    - from: "app/routers/analytics.py _compute_cards"
      to: "conversations table + messages table"
      via: "JOIN messages m ON c.id = m.conversation_id WHERE c.workspace_id=:wid AND c.status != 'bot_ignored' {scope_clause}"
      pattern: "JOIN conversations c ON c.id = m.conversation_id"
    - from: "app/routers/analytics.py /campaigns/{id}"
      to: "campaigns table workspace check"
      via: "SELECT id FROM campaigns WHERE id=:cid AND workspace_id=:wid → 404 на cross-workspace"
      pattern: "Campaign.workspace_id == ctx.workspace_id"
    - from: "app/routers/analytics.py /agents/{id}"
      to: "ai_contexts table workspace check"
      via: "SELECT id FROM ai_contexts WHERE id=:aid AND workspace_id=:wid"
      pattern: "AIContext.workspace_id == ctx.workspace_id"
    - from: "app/routers/analytics.py /senders/{id}"
      to: "senders table workspace check"
      via: "SELECT id FROM senders WHERE id=:sid AND workspace_id=:wid"
      pattern: "Sender.workspace_id == ctx.workspace_id"

threat_model:
  - id: T-05-02-WS-ISOLATION
    threat: "Cross-workspace analytics leak — user workspace A видит counts из workspace B через подмену campaign_id/agent_id/sender_id в URL"
    mitigation: "Каждый из 4 endpoints под Depends(auth_dep). Per-resource endpoints (/campaigns/{id} и т.д.) делают _ensure_*_in_workspace prequery (SELECT id WHERE id=:rid AND workspace_id=:wid) — если row не найден → 404 ДО _compute_cards. Внутри _compute_cards ВСЕ 4 COUNT'а имеют WHERE c.workspace_id=:wid (workspace boundary даже если scope_id из чужого workspace utterly бы прошёл prequery)."
    verification: "pytest tests/test_phase5_analytics.py::test_cross_workspace_404_on_all_4 -x — workspace A user → GET /api/v1/analytics/{level}/<workspace-B-resource-id> → 404 на 3 per-resource endpoints; /workspace всегда показывает только свои counts"
  - id: T-05-02-COUNT-EXFIL
    threat: "Even с 404, 4 SELECT'а в _compute_cards могут случайно вернуть данные из чужого workspace если scope_clause некорректен"
    mitigation: "scope_clause параметризованный через :scope_val placeholder; workspace_id всегда первым WHERE clause; никакого dynamic SQL composition (только статичный f-string с safe scope column whitelist {campaign_id, ai_context_id, sender_id})"
    verification: "pytest tests/test_phase5_analytics_correctness.py::test_workspace_isolation_in_all_4_counts -x — seed workspace A counts + workspace B counts → workspace A user видит только свои"
  - id: T-05-02-BOT-INFLATE
    threat: "Conversations со status='bot_ignored' содержат inbound bot messages — incl. в 'replied' COUNT'ах создаёт inflated metrics (Pitfall 8)"
    mitigation: "ВСЕ 4 raw-text COUNT'а имеют WHERE c.status != 'bot_ignored'. Bot dialogs исключены из sent + replied + leads + finishes."
    verification: "pytest tests/test_phase5_analytics_correctness.py::test_bot_ignored_excluded_from_replied -x — seed 1 bot conv с 5 inbound messages + 1 real conv с 3 inbound → replied.conversation_count=1, message_count=3 (не 6)"

---

<objective>
Создать `app/routers/analytics.py` — новый read-only роутер с 4 endpoints (workspace / campaigns / agents / senders) под Depends(auth_dep), все возвращающие identical AnalyticsCards Pydantic schema через единый _compute_cards helper с raw-text COUNT'ами. Добавить AnalyticsReplied + AnalyticsCards в app/schemas/__init__.py. Зарегистрировать роутер в app/main.py.

Purpose: Закрывает 4 из 11 требований Phase 5 (ANLX-01..04). Использует composite indexes созданные в Plan 05-01 migration 017. Без background workers (D-13) — всё request-time. UI Lovable рендерит 4 одинаковые карточки на 4 уровнях dashboard'а.

Output: 1 новый роутер с 4 endpoints, расширение схем (2 класса), регистрация в main.py, 2 файла тестов (smoke isolation + correctness с seeded fixtures).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@.planning/phases/05-inbox-analytics/05-CONTEXT.md
@.planning/phases/05-inbox-analytics/05-RESEARCH.md
@.planning/phases/05-inbox-analytics/05-PATTERNS.md
@.planning/phases/05-inbox-analytics/05-VALIDATION.md
@.planning/phases/05-inbox-analytics/05-01-migration-inbox-manager-bot-filter-PLAN.md
@.planning/codebase/ARCHITECTURE.md
@CLAUDE.md
@migrations/017_phase5.sql
@app/models/__init__.py
@app/schemas/__init__.py
@app/routers/campaigns.py
@app/routers/agents.py
@app/utils/auth.py
@app/main.py
@tests/conftest.py
@tests/test_campaign_router.py

<interfaces>
<!-- Phase 5 schemas added in Plan 05-01 (Task 1) — reuse here -->

From app/utils/auth.py:
```python
class AuthCtx(BaseModel):
    workspace_id: UUID
    user_id: Optional[str]
    source: Literal["jwt", "api_key"]
    role: Optional[str]

async def auth_dep(...) -> AuthCtx: ...
```

From app/routers/campaigns.py (workspace-scope precheck pattern to copy):
```python
async def _load_campaign(db: AsyncSession, ctx: AuthCtx, campaign_id: UUID) -> Campaign:
    res = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == ctx.workspace_id,
        )
    )
    c = res.scalars().first()
    if c is None:
        raise HTTPException(404, detail={"code": "X_NOT_FOUND", ...})
    return c
```

From migrations/017_phase5.sql (composite indexes used by these queries):
```sql
CREATE INDEX idx_conversations_workspace_campaign_status ON conversations(workspace_id, campaign_id, status) WHERE campaign_id IS NOT NULL;
CREATE INDEX idx_conversations_workspace_agent_status ON conversations(workspace_id, ai_context_id, status) WHERE ai_context_id IS NOT NULL;
CREATE INDEX idx_conversations_workspace_sender_status ON conversations(workspace_id, sender_id, status);
```

From app/models (existing — referenced by SELECT'ы):
```python
class Campaign(Base):  # workspace_id, status, agent_id, ...
class AIContext(Base): # workspace_id, name, ... (FK target ai_contexts.id from conversations.ai_context_id)
class Sender(Base):    # workspace_id, lifecycle_status, auth_status
class Conversation(Base): # workspace_id, sender_id, ai_context_id (nullable), campaign_id (nullable), status (7 values incl bot_ignored)
```

Conversation status values (after Plan 05-01 migration 017):
- 'active', 'manual', 'paused', 'lead', 'handoff', 'finished', 'bot_ignored'

Phase 5 D-16 metric definitions:
- sent  = COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE c.workspace_id=:wid AND c.status != 'bot_ignored' AND m.direction='outbound' {scope}
- replied.conversation_count = COUNT(DISTINCT m.conversation_id) — same JOIN — WHERE m.direction='inbound' AND m.sent_by='contact'
- replied.message_count      = COUNT(*) — same JOIN/WHERE
- leads    = COUNT(*) FROM conversations c WHERE c.workspace_id=:wid AND c.status='lead' {scope}
- finishes = COUNT(*) FROM conversations c WHERE c.workspace_id=:wid AND c.status='finished' {scope}

Scope column whitelist (security — no dynamic SQL injection):
- ('campaign_id', UUID)        — for /campaigns/{id}
- ('ai_context_id', UUID)      — for /agents/{id}  
- ('sender_id', UUID)          — for /senders/{id}
- None                          — for /workspace (no extra scope)
</interfaces>
</context>

<tasks>

<task type="execute" tdd="true">
  <name>Task 1: AnalyticsReplied + AnalyticsCards Pydantic schemas</name>
  <files>
    app/schemas/__init__.py,
    tests/test_phase5_analytics.py
  </files>
  <wave>1</wave>
  <depends_on>[]</depends_on>
  <read_first>
    - app/schemas/__init__.py (полностью — особенно CampaignResponse и nested models pattern для AnalyticsReplied внутри AnalyticsCards; verify импорты BaseModel, ConfigDict уже есть)
    - .planning/phases/05-inbox-analytics/05-CONTEXT.md §D-15, §D-16 (точная shape AnalyticsCards)
    - .planning/phases/05-inbox-analytics/05-RESEARCH.md §"C-03: Pydantic Schemas Recommended Shape" (строки 1334-1342 — AnalyticsReplied, AnalyticsCards)
    - .planning/phases/05-inbox-analytics/05-PATTERNS.md §"`app/schemas/__init__.py`" (строки 504-541 — pattern для nested response models)
  </read_first>
  <behavior>
    - Test 1: AnalyticsCards с valid {sent:10, replied:{conversation_count:3, message_count:5}, leads:2, finishes:1} проходит validation
    - Test 2: AnalyticsCards без какого-либо required field падает с ValidationError
    - Test 3: AnalyticsReplied с conversation_count=0, message_count=0 валиден (граничный случай — нет ответов)
    - Test 4: AnalyticsCards.model_dump() возвращает dict pattern для JSON serialization
  </behavior>
  <action>
Добавить в `app/schemas/__init__.py` (вставить после Phase 5 inbox schemas из Plan 05-01 Task 1):

```python
# === Phase 5 analytics schemas ===

class AnalyticsReplied(BaseModel):
    """Per D-15: «Отвечено» = две цифры — conversation_count + message_count."""
    conversation_count: int
    message_count: int


class AnalyticsCards(BaseModel):
    """Per D-16: identical schema across all 4 levels (workspace / campaign / agent / sender)."""
    sent: int
    replied: AnalyticsReplied
    leads: int
    finishes: int
```

Если `BaseModel` или `ConfigDict` не импортированы — добавить. Если уже — не дублировать.

Создать (или extend если уже создан в Plan 05-01) `tests/test_phase5_analytics.py` с базовыми schema-тестами:

```python
import pytest
from pydantic import ValidationError
from app.schemas import AnalyticsCards, AnalyticsReplied


def test_analytics_cards_valid():
    obj = AnalyticsCards(
        sent=10,
        replied=AnalyticsReplied(conversation_count=3, message_count=5),
        leads=2,
        finishes=1,
    )
    assert obj.sent == 10
    assert obj.replied.conversation_count == 3
    assert obj.replied.message_count == 5
    assert obj.leads == 2
    assert obj.finishes == 1


def test_analytics_cards_missing_required():
    with pytest.raises(ValidationError):
        AnalyticsCards(sent=10, leads=2, finishes=1)  # missing replied


def test_analytics_replied_zeros():
    obj = AnalyticsReplied(conversation_count=0, message_count=0)
    assert obj.conversation_count == 0


def test_analytics_cards_dump_to_dict():
    obj = AnalyticsCards(
        sent=10,
        replied=AnalyticsReplied(conversation_count=3, message_count=5),
        leads=2, finishes=1,
    )
    d = obj.model_dump()
    assert d["sent"] == 10
    assert d["replied"]["conversation_count"] == 3
    assert d["replied"]["message_count"] == 5
```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_phase5_analytics.py::test_analytics_cards_valid tests/test_phase5_analytics.py::test_analytics_cards_missing_required tests/test_phase5_analytics.py::test_analytics_replied_zeros tests/test_phase5_analytics.py::test_analytics_cards_dump_to_dict -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "class AnalyticsReplied" app/schemas/__init__.py` returns 0
    - `grep -q "class AnalyticsCards" app/schemas/__init__.py` returns 0
    - `grep -q "conversation_count" app/schemas/__init__.py` returns 0
    - `grep -q "message_count" app/schemas/__init__.py` returns 0
    - `pytest tests/test_phase5_analytics.py -k "schema or cards_valid or cards_missing" -x -q` — 4 tests pass
  </acceptance_criteria>
  <done>
    AnalyticsReplied + AnalyticsCards добавлены в app/schemas/__init__.py со shape из D-15/D-16. 4 schema-теста зелёные.
  </done>
</task>

<task type="execute" tdd="true">
  <name>Task 2: Analytics router (4 endpoints + _compute_cards helper + 3 workspace prechecks)</name>
  <files>
    app/routers/analytics.py,
    app/main.py,
    tests/test_phase5_analytics.py,
    tests/test_phase5_analytics_correctness.py
  </files>
  <wave>1</wave>
  <depends_on>["Task 1"]</depends_on>
  <read_first>
    - app/routers/campaigns.py (полностью — pattern для workspace-scope precheck helper строки 67-82; pattern для multi-COUNT raw text строки 145-202)
    - app/routers/agents.py (паттерн router structure; campaign_count COUNT helper)
    - app/main.py (полностью — pattern app.include_router и import block; добавляем analytics после conversations.router)
    - .planning/phases/05-inbox-analytics/05-CONTEXT.md §D-13, §D-14, §D-15, §D-16 (определение метрик)
    - .planning/phases/05-inbox-analytics/05-RESEARCH.md §"Example 3: Analytics workspace endpoint" (строки 826-913 — FULL КОД pattern для всех 4 endpoints и _compute_cards), §"Pattern 6: Analytics queries — выбор источника «Отправлено»" (строки 352-374 — C-01 recommendation = messages), §"Pattern 7: Two-figure «Отвечено»" (строки 376-393), §"Pitfall 8: Bot filter creates conversation БЕЗ campaign_id" (строки 617-631 — exclusion logic)
    - .planning/phases/05-inbox-analytics/05-PATTERNS.md §"`app/routers/analytics.py`" (строки 171-236 — exact analog references)
    - migrations/017_phase5.sql (composite indexes использованные query'ями)
    - tests/test_campaign_router.py (полностью — pattern для cross-workspace 404 tests + seeded fixtures)
    - tests/conftest.py (новые `test_conversation_factory`, `test_message_factory` из Plan 05-01 Task 1 — используются для seeding)
  </read_first>
  <behavior>
    - Test 1 (ANLX-01 auth): GET /api/v1/analytics/workspace без credentials → 401
    - Test 2 (ANLX-01): GET /workspace для workspace юзера возвращает {sent, replied:{...}, leads, finishes} — все 4 поля присутствуют
    - Test 3 (ANLX-01 isolation): user workspace A видит counts ONLY из workspace A; counts из workspace B не попадают
    - Test 4 (ANLX-02): GET /api/v1/analytics/campaigns/{id} 404 на cross-workspace campaign
    - Test 5 (ANLX-02): GET /campaigns/{my-campaign} возвращает counts со scope `c.campaign_id=:cid`
    - Test 6 (ANLX-03): GET /api/v1/analytics/senders/{id} 404 на cross-workspace sender; happy path scope `c.sender_id=:sid`
    - Test 7 (ANLX-04): GET /api/v1/analytics/agents/{id} 404 на cross-workspace agent; scope `c.ai_context_id=:aid`
    - Test 8 (D-16 schema parity): все 4 endpoints возвращают exactly same AnalyticsCards JSON shape (top-level keys)
    - Test 9 (Correctness): seed workspace со: 10 outbound messages в 3 conversations / 5 inbound messages from contact в 2 conversations / 1 lead status / 1 finished status → /workspace возвращает {sent:10, replied:{conversation_count:2, message_count:5}, leads:1, finishes:1}
    - Test 10 (Pitfall 8): seed 1 conversation status='bot_ignored' с 5 inbound messages + 1 real conv status='active' с 3 inbound → replied.conversation_count=1, message_count=3 (bot_ignored excluded)
    - Test 11 (Pitfall 9 — leads mutually exclusive): seed 1 conv status='lead' + 1 conv status='finished' (что был раньше lead) → /workspace leads=1, finishes=1 (НЕ leads=2)
    - Test 12 (D-15 two figures): seed 2 conversations со 3 inbound each (2 conv, 6 msgs) → replied.conversation_count=2, replied.message_count=6
    - Test 13 (D-15 same SELECT): assert /workspace replied query is one SELECT (не два) — это integration smoke + EXPLAIN check
    - Test 14 (Scope campaign): seed conv с campaign_id=X со 5 outbound + conv без campaign_id со 3 outbound → /campaigns/X sent=5; /workspace sent=8 (workspace-level включает оба)
    - Test 15 (Scope agent): аналогично для ai_context_id
    - Test 16 (Scope sender): аналогично для sender_id
  </behavior>
  <action>
Создать `app/routers/analytics.py` с нуля:

```python
"""Phase 5 analytics router — 4 read-only endpoints (workspace / campaigns / agents / senders).

Per D-13: real-time COUNT() per request. NO background workers, NO materialized views,
NO pre-aggregated counters. All 4 endpoints return identical AnalyticsCards schema (D-16).
Per D-14: all-time only. No ?from=&to= query params.
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AIContext, Campaign, Sender
from app.schemas import AnalyticsCards, AnalyticsReplied
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


# === Workspace-scope precheck helpers (404 на cross-workspace) ===

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
        # TODO(v2-rls)
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
        # TODO(v2-rls)
    ))).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SENDER_NOT_FOUND",
                    "message": "Sender not found in your workspace"},
        )


# === Core helper: 4 COUNT'ов одной shape per scope ===

# Scope column whitelist — security: NO dynamic SQL injection through scope col name
_ALLOWED_SCOPE_COLUMNS = {"campaign_id", "ai_context_id", "sender_id"}


async def _compute_cards(
    db: AsyncSession,
    workspace_id: UUID,
    scope: Optional[tuple[str, UUID]] = None,
) -> AnalyticsCards:
    """Run 4 COUNT'ов для одного scope. scope=None → workspace-only.

    Per D-13: real-time. Per D-16: identical shape per level. Per Pitfall 8:
    bot_ignored conversations excluded из всех 4 counts.
    """
    scope_clause = ""
    params: dict = {"wid": str(workspace_id)}
    if scope is not None:
        col, val = scope
        if col not in _ALLOWED_SCOPE_COLUMNS:
            raise ValueError(f"Invalid scope column: {col}")
        scope_clause = f" AND c.{col} = :scope_val"
        params["scope_val"] = str(val)

    # 1. Sent — source = messages (C-01 recommendation; covers queue worker / listener self / UI manager send)
    sent = (await db.execute(text(f"""
        SELECT COUNT(*)
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.workspace_id = :wid
          AND c.status != 'bot_ignored'
          {scope_clause}
          AND m.direction = 'outbound'
    """), params)).scalar() or 0

    # 2. Replied — two figures in ONE SELECT (D-15)
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

    # 3. Leads — mutually exclusive (Pitfall 9 — per D-16 verbatim)
    leads = (await db.execute(text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE c.workspace_id = :wid
          AND c.status = 'lead'
          {scope_clause}
    """), params)).scalar() or 0

    # 4. Finishes
    finishes = (await db.execute(text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE c.workspace_id = :wid
          AND c.status = 'finished'
          {scope_clause}
    """), params)).scalar() or 0

    return AnalyticsCards(
        sent=sent,
        replied=AnalyticsReplied(
            conversation_count=(replied_row.conv_count if replied_row else 0) or 0,
            message_count=(replied_row.msg_count if replied_row else 0) or 0,
        ),
        leads=leads,
        finishes=finishes,
    )


# === 4 endpoints (identical AnalyticsCards shape per D-16) ===

@router.get("/workspace", response_model=AnalyticsCards)
async def workspace_analytics(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCards:
    """ANLX-01 — метрики workspace юзера."""
    return await _compute_cards(db, ctx.workspace_id, scope=None)


@router.get("/campaigns/{campaign_id}", response_model=AnalyticsCards)
async def campaign_analytics(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCards:
    """ANLX-02 — метрики одной кампании. 404 на cross-workspace."""
    await _ensure_campaign_in_workspace(db, ctx, campaign_id)
    return await _compute_cards(db, ctx.workspace_id, scope=("campaign_id", campaign_id))


@router.get("/agents/{agent_id}", response_model=AnalyticsCards)
async def agent_analytics(
    agent_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCards:
    """ANLX-04 — метрики одного агента. 404 на cross-workspace.

    Per D-16: agent_campaign_count лежит в /api/v1/agents (Phase 3) — НЕ здесь.
    """
    await _ensure_agent_in_workspace(db, ctx, agent_id)
    return await _compute_cards(db, ctx.workspace_id, scope=("ai_context_id", agent_id))


@router.get("/senders/{sender_id}", response_model=AnalyticsCards)
async def sender_analytics(
    sender_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCards:
    """ANLX-03 — метрики одного sender'а. 404 на cross-workspace.

    Per D-16: sender errors (FloodWait/Failed/auth) лежат на странице sender (Phase 2 SNDR-03) — НЕ здесь.
    """
    await _ensure_sender_in_workspace(db, ctx, sender_id)
    return await _compute_cards(db, ctx.workspace_id, scope=("sender_id", sender_id))
```

Зарегистрировать в `app/main.py`:

```python
from app.routers import (
    agents,
    analytics,                  # Phase 5 NEW
    campaigns,
    check_contacts,
    contacts,
    conversations,
    folders,
    ...
)
# ...
app.include_router(conversations.router)  # Plan 05-01
app.include_router(analytics.router)      # Plan 05-02 — Phase 5 NEW
```

Никаких изменений в lifespan (D-13 — нет новых workers).

Расширить `tests/test_phase5_analytics.py` (созданный в Task 1) с smoke-isolation тестами:
- test_workspace_endpoint_returns_4_metrics
- test_workspace_endpoint_401_without_auth
- test_workspace_endpoint_workspace_isolation
- test_campaign_endpoint_404_cross_workspace
- test_agent_endpoint_404_cross_workspace
- test_sender_endpoint_404_cross_workspace
- test_all_4_endpoints_same_schema (assert keys)

Создать `tests/test_phase5_analytics_correctness.py` (8+ тестов с seeded fixtures):

```python
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_workspace_4_metrics_correct(
    async_client, auth_headers, test_conversation_factory, test_message_factory,
):
    """ANLX-01 correctness — seeded fixtures map to expected counts."""
    # Conv 1: 10 outbound
    conv1 = await test_conversation_factory(status='active')
    await test_message_factory(conv1["id"], count=10, direction='outbound', sent_by='ai')
    # Conv 2: 3 inbound from contact
    conv2 = await test_conversation_factory(status='active')
    await test_message_factory(conv2["id"], count=3, direction='inbound', sent_by='contact')
    # Conv 3: 2 inbound from contact
    conv3 = await test_conversation_factory(status='active')
    await test_message_factory(conv3["id"], count=2, direction='inbound', sent_by='contact')
    # 1 lead
    await test_conversation_factory(status='lead')
    # 1 finished
    await test_conversation_factory(status='finished')

    resp = await async_client.get("/api/v1/analytics/workspace", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] == 10
    assert data["replied"]["conversation_count"] == 2
    assert data["replied"]["message_count"] == 5
    assert data["leads"] == 1
    assert data["finishes"] == 1


async def test_bot_ignored_excluded(
    async_client, auth_headers, test_conversation_factory, test_message_factory,
):
    """Pitfall 8 — bot conversations excluded from replied count."""
    # Bot conv: 5 inbound (should NOT be counted)
    bot_conv = await test_conversation_factory(status='bot_ignored', ai_enabled=False)
    await test_message_factory(bot_conv["id"], count=5, direction='inbound', sent_by='contact')
    # Real conv: 3 inbound (should be counted)
    real_conv = await test_conversation_factory(status='active')
    await test_message_factory(real_conv["id"], count=3, direction='inbound', sent_by='contact')

    resp = await async_client.get("/api/v1/analytics/workspace", headers=auth_headers)
    data = resp.json()
    assert data["replied"]["conversation_count"] == 1
    assert data["replied"]["message_count"] == 3


async def test_leads_mutually_exclusive_with_finished(
    async_client, auth_headers, test_conversation_factory,
):
    """Pitfall 9 — leads strict status='lead', не включает 'finished'."""
    await test_conversation_factory(status='lead')
    await test_conversation_factory(status='finished')
    resp = await async_client.get("/api/v1/analytics/workspace", headers=auth_headers)
    data = resp.json()
    assert data["leads"] == 1  # not 2
    assert data["finishes"] == 1


async def test_campaign_scope_filters(
    async_client, auth_headers, test_campaign_factory,
    test_conversation_factory, test_message_factory,
):
    """ANLX-02 — campaign scope correctly filters."""
    camp = await test_campaign_factory()
    conv_in_camp = await test_conversation_factory(campaign_id=camp["id"], status='active')
    await test_message_factory(conv_in_camp["id"], count=5, direction='outbound')
    conv_no_camp = await test_conversation_factory(status='active')
    await test_message_factory(conv_no_camp["id"], count=3, direction='outbound')

    # Campaign endpoint sees only conv_in_camp
    resp = await async_client.get(f"/api/v1/analytics/campaigns/{camp['id']}", headers=auth_headers)
    assert resp.json()["sent"] == 5

    # Workspace endpoint sees both
    resp_ws = await async_client.get("/api/v1/analytics/workspace", headers=auth_headers)
    assert resp_ws.json()["sent"] == 8


# ... similar tests for agent_scope_filters, sender_scope_filters,
# two_figure_replied_single_select (D-15), and all_4_levels_same_shape
```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_phase5_analytics.py tests/test_phase5_analytics_correctness.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "@router.get" app/routers/analytics.py` == 4 (workspace / campaigns / agents / senders)
    - `grep -c "Depends(auth_dep)" app/routers/analytics.py` >= 4 (every endpoint scoped)
    - `grep -q "async def _compute_cards" app/routers/analytics.py` returns 0
    - `grep -q "async def _ensure_campaign_in_workspace" app/routers/analytics.py` returns 0
    - `grep -q "async def _ensure_agent_in_workspace" app/routers/analytics.py` returns 0
    - `grep -q "async def _ensure_sender_in_workspace" app/routers/analytics.py` returns 0
    - `grep -q "_ALLOWED_SCOPE_COLUMNS" app/routers/analytics.py` returns 0 (whitelist для безопасной композиции scope_clause)
    - `grep -c "c.status != 'bot_ignored'" app/routers/analytics.py` >= 4 (Pitfall 8 — exclusion in all 4 query types: sent, replied, leads, finishes — но leads/finishes уже фильтруют по c.status='lead'/'finished' так что exclusion может быть в 2 query'ях — фактически проверим что в sent и replied query'ях есть)
    - In `_compute_cards` — replied query is ONE SELECT (not two): `grep -A 12 "Replied" app/routers/analytics.py | grep -c "SELECT"` == 1
    - `grep -q "COUNT(DISTINCT m.conversation_id)" app/routers/analytics.py` returns 0 (D-15 conversation_count formula)
    - `grep -q "m.direction = 'outbound'" app/routers/analytics.py` returns 0 (sent source = messages.outbound per C-01)
    - `grep -q "include_router(analytics.router)" app/main.py` returns 0
    - `pytest tests/test_phase5_analytics.py -x -q` — все isolation + schema tests pass
    - `pytest tests/test_phase5_analytics_correctness.py -x -q` — все correctness tests pass (ANLX-01..04 covered)
  </acceptance_criteria>
  <done>
    app/routers/analytics.py содержит 4 endpoints (workspace / campaigns / agents / senders) под Depends(auth_dep) с workspace-scope prechecks, единый _compute_cards helper выполняет 4 raw-text COUNT'а (sent + replied two-figure single SELECT + leads + finishes) с исключением bot_ignored. AnalyticsCards schema (одинаковая per D-16) возвращается всеми 4 endpoints. main.py регистрирует роутер. Тесты на cross-workspace isolation (404) и correctness (seeded → expected) зелёные.
  </done>
</task>

</tasks>

<verification>
**Plan-level checks:**

1. **Endpoint smoke:**
   - `uvicorn app.main:app --reload` startup без ошибок
   - GET /docs показывает 4 endpoints под "analytics" tag (workspace / campaigns / agents / senders)

2. **Schema parity (D-16):**
   - All 4 endpoints — same response shape `{sent: int, replied: {conversation_count, message_count}, leads: int, finishes: int}`
   - `pytest tests/test_phase5_analytics.py -x -q` зелёный

3. **Correctness (seeded → expected):**
   - `pytest tests/test_phase5_analytics_correctness.py -x -q` зелёный — все ANLX-01..04 покрыты
   - Manual: seed workspace, hit 4 endpoints, assert counts make sense

4. **Workspace isolation (T-05-02-WS-ISOLATION):**
   - cross-workspace campaign/agent/sender ID → 404
   - workspace user A видит только свои counts

5. **Bot exclusion (Pitfall 8):**
   - seed 1 bot conv с inbound messages + 1 real conv с inbound — replied count = только real

6. **No background workers (D-13):**
   - `grep -c "lifespan\|asyncio.create_task" app/routers/analytics.py` == 0
   - lifespan list main.py не изменился (5 background workers до и после)

7. **Composite indexes used:**
   - `EXPLAIN ANALYZE SELECT ... WHERE workspace_id=:wid AND status='lead' AND campaign_id=:cid` использует idx_conversations_workspace_campaign_status (manual check; не grep)
</verification>

<success_criteria>
**Plan 05-02 complete when:**

- [ ] `app/routers/analytics.py` создан с 4 endpoints (/workspace, /campaigns/{id}, /agents/{id}, /senders/{id}) под Depends(auth_dep)
- [ ] `_compute_cards(db, workspace_id, scope=None|tuple)` helper выполняет 4 raw-text COUNT'а: sent (messages outbound JOIN conversations с exclusion bot_ignored), replied (single SELECT с COUNT(DISTINCT conv_id) + COUNT(*) per D-15), leads (status='lead'), finishes (status='finished')
- [ ] 3 workspace prechecks (`_ensure_{campaign,agent,sender}_in_workspace`) для 404 на cross-workspace
- [ ] `_ALLOWED_SCOPE_COLUMNS` whitelist для безопасной композиции scope_clause (no SQL injection через col name)
- [ ] AnalyticsReplied + AnalyticsCards добавлены в app/schemas/__init__.py с required fields
- [ ] `app/main.py` регистрирует analytics.router; lifespan НЕ изменён (5 workers до = 5 после, D-13)
- [ ] Все 4 endpoints возвращают identical AnalyticsCards JSON shape (D-16)
- [ ] tests/test_phase5_analytics.py (~7 isolation/schema tests) — все зелёные
- [ ] tests/test_phase5_analytics_correctness.py (~8+ correctness tests с seeded fixtures) — все зелёные, покрывая ANLX-01..04 + Pitfalls 8 + 9
- [ ] Regression: existing tests (test_campaign_router, test_agents, test_phase5_inbox*) зелёные
</success_criteria>

<output>
After completion, create `.planning/phases/05-inbox-analytics/05-02-SUMMARY.md` следуя `.claude/get-shit-done/templates/summary.md`.

Include:
- 4 analytics endpoints created
- _compute_cards helper architecture (single function, 4 COUNT'ов, scope-parameterised)
- Sources resolved: sent = messages.outbound JOIN conversations (C-01), replied = two-figure single SELECT (D-15)
- Bot exclusion in all relevant counts (Pitfall 8)
- Leads/finishes mutually exclusive per D-16 verbatim (Pitfall 9)
- Composite indexes from migration 017 (created in 05-01) leveraged for real-time COUNTs
- Test coverage: ~15 tests across 2 files
- No background workers added (D-13 confirmed)
</output>
