---
phase: 03-agents-ai-templates
plan: 02
type: execute
wave: 1
depends_on: ["03-01"]
files_modified:
  - app/routers/agents.py
  - app/routers/send.py
  - app/routers/contexts.py
  - app/schemas/__init__.py
  - app/main.py
  - tests/test_agents.py
  - tests/test_send.py
autonomous: true
requirements: [AGNT-01, AGNT-02, AGNT-03, AGNT-04]
requirements_addressed: [AGNT-01, AGNT-02, AGNT-03, AGNT-04]

must_haves:
  truths:
    - "Пользователь POST'ит /api/v1/agents и получает 201 + AgentResponse с id, name, system_prompt, rules, tone_of_voice, faq[], company_info, product_info, campaign_count=0"
    - "Дубль имени в одном workspace возвращает 409 AGENT_NAME_DUPLICATE (Pattern 2)"
    - "Cross-workspace доступ запрещён — agent другого workspace отдаёт 404"
    - "PATCH partial: переданы только некоторые поля → обновляется только они (Phase 2 convention)"
    - "FAQ PATCH — full replacement (не merge), per Pitfall 7"
    - "DELETE — hard delete; conversations.ai_context_id → NULL (FK SET NULL); context_contact_assignments удаляются (FK CASCADE)"
    - "POST /api/v1/agents/{id}/duplicate без body создаёт нового агента с авто-именем «{name} (copy)» / «{name} (copy 2)» (Pattern 4 + retry-on-IntegrityError per Pitfall 2)"
    - "POST /api/v1/send требует ai_context_id в body — workspace-scoped 404 если agent в другом workspace"
    - "Один и тот же agent_id валиден в нескольких POST /api/v1/send запросах (с разными sender'ами) — доказывает AGNT-03 reusability"
    - "Lovable получает campaign_count=0 хардкодом — поле всегда в response (D-10)"
  artifacts:
    - path: "app/routers/agents.py"
      provides: "6 endpoints под /api/v1/agents (list/create/get/patch/delete/duplicate)"
      contains: "router = APIRouter(prefix=\"/api/v1/agents\""
    - path: "app/routers/send.py"
      provides: "POST /api/v1/send под AuthDep + explicit ai_context_id"
      contains: "Depends(auth_dep)"
    - path: "app/schemas/__init__.py"
      provides: "AgentCreate / AgentUpdate / AgentResponse / AgentListResponse / FaqItem"
      contains: "class AgentResponse"
    - path: "app/main.py"
      provides: "app.include_router(agents.router) + app.include_router(send.router)"
      contains: "app.include_router(agents.router)"
    - path: "tests/test_agents.py"
      provides: "13 тестов AGNT-01..04 (CRUD + duplicate + delete + cascade)"
      contains: "test_create_agent_returns_201"
    - path: "tests/test_send.py"
      provides: "Phase 3 send rewrite tests (explicit ai_context_id, cross-workspace 404)"
      contains: "test_send_requires_ai_context_id"
  key_links:
    - from: "app/main.py"
      to: "agents.router и send.router"
      via: "app.include_router(...)"
      pattern: "app\\.include_router\\((agents|send)\\.router\\)"
    - from: "POST /api/v1/agents/{id}/duplicate"
      to: "AIContext name auto-increment"
      via: "_generate_duplicate_name LIKE-based"
      pattern: "_generate_duplicate_name"
    - from: "POST /api/v1/send"
      to: "AIContext.workspace_id check"
      via: ".where(AIContext.id == request.ai_context_id, AIContext.workspace_id == ctx.workspace_id)"
      pattern: "AIContext.workspace_id == ctx.workspace_id"
    - from: "DELETE /api/v1/agents/{id}"
      to: "context_contact_assignments каскад"
      via: "FK ON DELETE CASCADE"
      pattern: "TODO\\(phase-4\\): also block on active campaign attachment"
---

<objective>
Phase 3 Plan 2: новый workspace-scoped роутер `agents` под `/api/v1/agents` (полный рерайт legacy `contexts.py` под AuthDep + workspace filter + drop deprecated полей + hard delete + duplicate endpoint), workspace-scoped рерайт `send.py` (AuthDep + explicit ai_context_id в body), регистрация обоих в `main.py`.

Purpose: Закрыть AGNT-01..04 (CRUD агентов) + восстановить отправку сообщений через /api/v1/send. После Phase 3 продукт снова отвечает на основной endpoint отправки + n8n может пушить сообщения с явным ai_context_id.

Output:
  - app/routers/agents.py (новый файл с 6 endpoints)
  - app/routers/send.py (полный рерайт под AuthDep)
  - app/routers/contexts.py (старый файл — git-delete, чтобы не было дубликатов prefix'ов; либо оставить как `.bak` — планировщик решит, но НЕ регистрировать в main.py)
  - app/schemas/__init__.py (добавлены AgentCreate/Update/Response/ListResponse/FaqItem + SendMessageRequest рефакторинг под required ai_context_id)
  - app/main.py (зарегистрированы agents.router + send.router)
  - tests/test_agents.py (13 тестов)
  - tests/test_send.py (2 теста: explicit ai_context_id required + cross-workspace 404)

NB: Plan 03-02 зависит от 03-01 (миграция 015 применена в conftest, ORM очищена, senders router очищен от ai_context_id). Без 03-01 тесты этого плана упадут на схемах БД.
</objective>

<execution_context>
@/Users/andrewbruce/Documents/outreach-platform/.claude/get-shit-done/workflows/execute-plan.md
@/Users/andrewbruce/Documents/outreach-platform/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/03-agents-ai-templates/03-CONTEXT.md
@.planning/phases/03-agents-ai-templates/03-RESEARCH.md
@.planning/phases/03-agents-ai-templates/03-VALIDATION.md
@.planning/phases/01-workspace-foundation/01-CONTEXT.md
@.planning/phases/02-tg-accounts-contacts/02-CONTEXT.md
@.planning/phases/03-agents-ai-templates/03-01-agent-model-decoupling-PLAN.md
@CLAUDE.md

# Existing code (reference patterns)
@app/routers/folders.py
@app/routers/contacts.py
@app/routers/senders.py
@app/routers/contexts.py
@app/routers/send.py
@app/main.py
@app/schemas/__init__.py
@app/utils/auth.py
@app/services/queue.py
@app/services/rotation.py
@tests/conftest.py

<interfaces>
<!-- Key types and contracts the executor needs. Extracted from codebase. -->

From app/utils/auth.py (AuthCtx — Phase 1 D-12):
```python
class AuthCtx(BaseModel):
    workspace_id: UUID
    user_id: Optional[str]
    source: Literal["jwt", "api_key"]
    role: Optional[str]

async def auth_dep(authorization, x_workspace_key, db) -> AuthCtx: ...
```

From app/models (after Plan 03-01 cleanup):
```python
class AIContext(Base):
    id: UUID  # primary key
    workspace_id: UUID  # NOT NULL FK CASCADE
    name: str  # VARCHAR(100), UNIQUE per workspace via idx_ai_contexts_workspace_name
    system_prompt: Optional[str]  # TEXT
    tone_of_voice: Optional[str]  # TEXT
    rules: Optional[str]  # TEXT
    company_info: Optional[str]  # TEXT
    product_info: Optional[str]  # TEXT
    faq: dict  # JSONB, default {}
    created_at: datetime
    updated_at: datetime
```

From app/routers/folders.py (Phase 2 pattern — copy this skeleton):
```python
@router.post("", response_model=FolderResponse, status_code=201)
async def create_folder(
    payload: FolderCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    # 1. Duplicate check via SELECT
    # 2. If existing → raise HTTPException(409, detail={"code": "FOLDER_NAME_DUPLICATE", ...})
    # 3. INSERT via ORM Folder(...)
    # 4. commit + refresh
    # 5. Return _folder_to_response(db, folder)
```

From app/services/queue.py (after Plan 03-01 adaptation):
```python
async def enqueue_message(
    db: AsyncSession,
    workspace_id: UUID,
    sender_id: UUID,
    sender_slug: str,
    recipient_phone: str,
    recipient_name: Optional[str] = None,
    message_text: str = "",
    as_draft: bool = False,
    metadata: Optional[dict] = None,
    priority: int = 0,
    callback_url: Optional[str] = None,
    ai_context_id: Optional[UUID] = None,  # Phase 3 explicit
) -> dict:
    """Returns {queue_id, queue_position, estimated_send_at}"""
```

From app/services/rotation.py:
```python
async def get_or_assign_sender(
    db: AsyncSession,
    context_id: UUID,
    contact_phone: str,
    workspace_id: UUID,
) -> Sender:
    """Picks/persists sender. Raises ValueError if no eligible sender."""
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Wave 0 — создать test scaffolds для test_agents.py и test_send.py (полные тесты, не skeleton)</name>
  <files>
    tests/test_agents.py
    tests/test_send.py
  </files>
  <read_first>
    - .planning/phases/03-agents-ai-templates/03-VALIDATION.md §"Per-Task Verification Map" — точные имена 15 тестов (3-02-01..3-02-15).
    - tests/conftest.py — фикстуры async_client, test_workspace, test_sender_factory, test_agent_factory (создана в plan 03-01).
    - tests/test_senders.py (Phase 2) — паттерн async_client + valid_supabase_jwt для интеграционных тестов с auth.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-01..D-10" — точные decisions для тестов.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Pattern 2" — Duplicate-name 409 pattern; §"Pattern 4" — duplicate-name auto-increment; §"Pitfall 2" — race condition retry; §"Pitfall 7" — FAQ PATCH = full replacement.
  </read_first>
  <behavior>
    - 13 тестов в test_agents.py покрывают: create (201/409/cross-workspace), persist fields, FAQ shape, FAQ PATCH replacement, list with campaign_count, partial PATCH, delete cascade (conversations.ai_context_id NULL, context_contact_assignments CASCADE), duplicate auto-name (copy)/(copy 2)/(copy 3), duplicate race protection.
    - 2 теста в test_send.py покрывают: ai_context_id required (422 если отсутствует), cross-workspace 404.
    - Все тесты сначала FAIL (RED) — endpoints ещё не существуют. Эти тесты — driving spec для Tasks 2-6.
  </behavior>
  <action>
    Создать `tests/test_agents.py` с 13 async-тестами (Wave 0 — все стартово красные, naming строго из VALIDATION.md):

    ```python
    """Phase 3 — Agent CRUD API tests (AGNT-01..04).

    Wave 0 RED — endpoints created in Task 2-5 of plan 03-02.
    """
    import pytest
    from sqlalchemy import text
    from uuid import uuid4

    pytestmark = pytest.mark.asyncio


    # ─── Auth helper ───────────────────────────────────────────────────────────
    async def _link_user_to_workspace(db, user_sub, workspace_id):
        """Create user_workspaces link so JWT auth resolves to existing workspace."""
        from app.models import UserWorkspace
        uw = UserWorkspace(supabase_user_id=user_sub, workspace_id=workspace_id, role="owner")
        db.add(uw)
        await db.commit()


    # ─── AGNT-01: create ────────────────────────────────────────────────────────
    async def test_create_agent_returns_201(async_client, async_db_session, valid_supabase_jwt, test_workspace):
        user_sub = f"user-create-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        token = valid_supabase_jwt(sub=user_sub)
        resp = await async_client.post(
            "/api/v1/agents",
            json={"name": "Sales Agent", "system_prompt": "be helpful"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Sales Agent"
        assert body["system_prompt"] == "be helpful"
        assert body["campaign_count"] == 0
        assert "id" in body


    async def test_create_agent_workspace_scoped(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory):
        """Agent в другом workspace отдаёт 404 (security: no cross-tenant leak)."""
        # Create agent in test_workspace
        other_agent = await test_agent_factory(name="Other WS Agent")

        # Create a separate workspace + user, try to GET other_agent → must be 404
        from app.models import Workspace
        ws2 = Workspace(name="Workspace 2")
        async_db_session.add(ws2)
        await async_db_session.commit()
        user2 = f"user-cross-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user2, ws2.id)

        token = valid_supabase_jwt(sub=user2)
        resp = await async_client.get(
            f"/api/v1/agents/{other_agent.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, resp.text


    async def test_create_agent_duplicate_name_409(async_client, async_db_session, valid_supabase_jwt, test_workspace):
        user_sub = f"user-dup-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        token = valid_supabase_jwt(sub=user_sub)
        # First create
        r1 = await async_client.post(
            "/api/v1/agents",
            json={"name": "Dup Agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 201
        # Second create with same name → 409
        r2 = await async_client.post(
            "/api/v1/agents",
            json={"name": "Dup Agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 409
        assert r2.json()["detail"]["code"] == "AGENT_NAME_DUPLICATE"


    # ─── AGNT-02: fields ────────────────────────────────────────────────────────
    async def test_create_agent_persists_all_fields(async_client, async_db_session, valid_supabase_jwt, test_workspace):
        user_sub = f"user-fields-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        token = valid_supabase_jwt(sub=user_sub)
        resp = await async_client.post(
            "/api/v1/agents",
            json={
                "name": "Full Agent",
                "system_prompt": "prompt",
                "rules": "be polite",
                "tone_of_voice": "friendly",
                "faq": [{"question": "Q1", "answer": "A1"}],
                "company_info": "Co",
                "product_info": "Prod",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["system_prompt"] == "prompt"
        assert body["rules"] == "be polite"
        assert body["tone_of_voice"] == "friendly"
        assert body["faq"] == [{"question": "Q1", "answer": "A1"}]
        assert body["company_info"] == "Co"
        assert body["product_info"] == "Prod"


    async def test_faq_shape_validation(async_client, async_db_session, valid_supabase_jwt, test_workspace):
        """FAQ shape: list[{question, answer}] — wrong shape → 422."""
        user_sub = f"user-faq-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        token = valid_supabase_jwt(sub=user_sub)
        # Wrong shape: dict instead of list
        resp = await async_client.post(
            "/api/v1/agents",
            json={"name": "Bad FAQ", "faq": {"Q1": "A1"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422


    async def test_patch_faq_replaces_not_merges(async_client, async_db_session, valid_supabase_jwt, test_workspace):
        """Pitfall 7: PATCH faq = full replacement, not concat/merge."""
        user_sub = f"user-faq-replace-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        token = valid_supabase_jwt(sub=user_sub)
        r1 = await async_client.post(
            "/api/v1/agents",
            json={"name": "FAQ Test", "faq": [{"question": "Q1", "answer": "A1"}, {"question": "Q2", "answer": "A2"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = r1.json()["id"]
        # PATCH with new array of length 1 — must REPLACE, not merge
        r2 = await async_client.patch(
            f"/api/v1/agents/{agent_id}",
            json={"faq": [{"question": "NewQ", "answer": "NewA"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["faq"] == [{"question": "NewQ", "answer": "NewA"}], \
            f"FAQ must be replaced, got: {body['faq']}"


    # ─── AGNT-04: list / patch / delete ─────────────────────────────────────────
    async def test_list_agents_with_campaign_count(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory):
        user_sub = f"user-list-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        await test_agent_factory(name="L1")
        await test_agent_factory(name="L2")
        token = valid_supabase_jwt(sub=user_sub)
        resp = await async_client.get("/api/v1/agents", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        # AgentListResponse shape: {agents: [...], total: N}
        assert "agents" in body
        assert body["total"] >= 2
        for a in body["agents"]:
            assert a["campaign_count"] == 0  # D-10 hardcoded


    async def test_patch_agent_partial(async_client, async_db_session, valid_supabase_jwt, test_workspace):
        user_sub = f"user-patch-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        token = valid_supabase_jwt(sub=user_sub)
        r1 = await async_client.post(
            "/api/v1/agents",
            json={"name": "Patch Agent", "system_prompt": "old", "rules": "old rules"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = r1.json()["id"]
        # Partial PATCH only system_prompt
        r2 = await async_client.patch(
            f"/api/v1/agents/{agent_id}",
            json={"system_prompt": "new"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["system_prompt"] == "new"
        assert body["rules"] == "old rules"  # preserved


    async def test_delete_agent_sets_conversation_to_null(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory, test_sender_factory):
        """D-08: DELETE hard; FK conversations.ai_context_id → NULL."""
        user_sub = f"user-del1-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        agent = await test_agent_factory(name="Delete Me")
        sender = await test_sender_factory(slug="del-test-sender")
        # Create conversation pointing to this agent
        await async_db_session.execute(
            text("""
                INSERT INTO conversations (workspace_id, sender_id, contact_phone, ai_context_id, ai_enabled)
                VALUES (:wid, :sid, '+79999999999', :aid, true)
            """),
            {"wid": str(test_workspace.id), "sid": str(sender.id), "aid": str(agent.id)},
        )
        await async_db_session.commit()

        token = valid_supabase_jwt(sub=user_sub)
        resp = await async_client.delete(f"/api/v1/agents/{agent.id}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 204

        # conversation должна остаться, но ai_context_id = NULL
        row = await async_db_session.execute(
            text("SELECT ai_context_id FROM conversations WHERE workspace_id = :wid"),
            {"wid": str(test_workspace.id)},
        )
        result = row.fetchone()
        assert result is not None
        assert result[0] is None, "conversation.ai_context_id must be NULL after agent delete (FK SET NULL)"


    async def test_delete_agent_cascades_assignments(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory, test_sender_factory):
        """D-08: DELETE cascades context_contact_assignments rows."""
        user_sub = f"user-del2-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        agent = await test_agent_factory(name="Delete Cascade")
        sender = await test_sender_factory(slug="del-cascade-sender")

        await async_db_session.execute(
            text("""
                INSERT INTO context_contact_assignments (workspace_id, context_id, contact_phone, sender_id)
                VALUES (:wid, :ctx_id, '+79991234567', :sid)
            """),
            {"wid": str(test_workspace.id), "ctx_id": str(agent.id), "sid": str(sender.id)},
        )
        await async_db_session.commit()

        token = valid_supabase_jwt(sub=user_sub)
        resp = await async_client.delete(f"/api/v1/agents/{agent.id}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 204

        # context_contact_assignments row должна быть удалена каскадом
        row = await async_db_session.execute(
            text("SELECT COUNT(*) FROM context_contact_assignments WHERE context_id = :ctx_id"),
            {"ctx_id": str(agent.id)},
        )
        assert row.scalar() == 0, "context_contact_assignments must cascade-delete with agent"


    # ─── AGNT-04: duplicate ─────────────────────────────────────────────────────
    async def test_duplicate_agent_auto_name(async_client, async_db_session, valid_supabase_jwt, test_workspace):
        """D-07: POST /agents/{id}/duplicate без body. Auto-name (copy) → (copy 2) → (copy 3)."""
        user_sub = f"user-dup-name-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        token = valid_supabase_jwt(sub=user_sub)
        r1 = await async_client.post(
            "/api/v1/agents",
            json={"name": "Original", "system_prompt": "p"},
            headers={"Authorization": f"Bearer {token}"},
        )
        orig_id = r1.json()["id"]
        # 1st duplicate → "Original (copy)"
        r2 = await async_client.post(f"/api/v1/agents/{orig_id}/duplicate", headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 201
        assert r2.json()["name"] == "Original (copy)"
        # 2nd duplicate of original → "Original (copy 2)"
        r3 = await async_client.post(f"/api/v1/agents/{orig_id}/duplicate", headers={"Authorization": f"Bearer {token}"})
        assert r3.status_code == 201
        assert r3.json()["name"] == "Original (copy 2)"
        # 3rd → "Original (copy 3)"
        r4 = await async_client.post(f"/api/v1/agents/{orig_id}/duplicate", headers={"Authorization": f"Bearer {token}"})
        assert r4.status_code == 201
        assert r4.json()["name"] == "Original (copy 3)"


    async def test_duplicate_race_handling(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory):
        """Pitfall 2: retry on IntegrityError when 2 parallel duplicate calls race."""
        # Simplified single-call sanity check (true race requires parallel runner) —
        # Just verify the endpoint doesn't 500 on a normal call.
        user_sub = f"user-dup-race-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        agent = await test_agent_factory(name="Race Source")
        token = valid_supabase_jwt(sub=user_sub)
        resp = await async_client.post(f"/api/v1/agents/{agent.id}/duplicate", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 201
    ```

    Создать `tests/test_send.py` с 2 тестами:
    ```python
    """Phase 3 — POST /api/v1/send (rewrite under AuthDep + explicit ai_context_id).

    Wave 0 RED — endpoint rewritten in Task 5.
    """
    import pytest
    from uuid import uuid4

    pytestmark = pytest.mark.asyncio


    async def _link_user_to_workspace(db, user_sub, workspace_id):
        from app.models import UserWorkspace
        uw = UserWorkspace(supabase_user_id=user_sub, workspace_id=workspace_id, role="owner")
        db.add(uw)
        await db.commit()


    async def test_send_requires_ai_context_id(async_client, async_db_session, valid_supabase_jwt, test_workspace):
        """Phase 3 D-06: POST /send без ai_context_id в body → 422."""
        user_sub = f"user-send-1-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        token = valid_supabase_jwt(sub=user_sub)
        resp = await async_client.post(
            "/api/v1/send",
            json={"recipient_phone": "+79991234567", "message": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, f"expected 422 for missing ai_context_id, got {resp.status_code}: {resp.text}"


    async def test_send_cross_workspace_agent_404(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory):
        """Phase 3 D-06: POST /send с ai_context_id из другого workspace → 404."""
        # agent в test_workspace
        agent = await test_agent_factory(name="Cross WS Agent")

        # user в другом workspace
        from app.models import Workspace
        ws2 = Workspace(name="Other WS for send test")
        async_db_session.add(ws2)
        await async_db_session.commit()
        user_sub = f"user-send-cross-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, ws2.id)

        token = valid_supabase_jwt(sub=user_sub)
        resp = await async_client.post(
            "/api/v1/send",
            json={
                "ai_context_id": str(agent.id),  # принадлежит ws1, не ws2
                "recipient_phone": "+79991234567",
                "message": "hi",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, f"expected 404 for cross-ws agent, got {resp.status_code}: {resp.text}"
    ```

    Также добавить в test_send.py:
    ```python
    async def test_same_agent_id_works_for_multiple_senders(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory, test_sender_factory):
        """AGNT-03: один и тот же agent_id успешно используется с разными sender'ами."""
        user_sub = f"user-multi-send-{uuid4()}"
        await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
        agent = await test_agent_factory(name="Multi Sender Agent")
        sender_a = await test_sender_factory(slug="sender-a-multi", lifecycle_status="active", auth_status="ok")
        sender_b = await test_sender_factory(slug="sender-b-multi", lifecycle_status="active", auth_status="ok")

        token = valid_supabase_jwt(sub=user_sub)
        # send via sender_a
        r1 = await async_client.post(
            "/api/v1/send",
            json={
                "ai_context_id": str(agent.id),
                "sender_slug": sender_a.slug,
                "recipient_phone": "+79991111111",
                "message": "msg1",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 200, r1.text
        # send via sender_b — SAME agent_id reused
        r2 = await async_client.post(
            "/api/v1/send",
            json={
                "ai_context_id": str(agent.id),
                "sender_slug": sender_b.slug,
                "recipient_phone": "+79992222222",
                "message": "msg2",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200, r2.text
    ```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_agents.py tests/test_send.py --collect-only -q</automated>
  </verify>
  <acceptance_criteria>
    - File `tests/test_agents.py` exists with exactly the 13 test function names from VALIDATION.md (3-02-01..3-02-13): test_create_agent_returns_201, test_create_agent_workspace_scoped, test_create_agent_duplicate_name_409, test_create_agent_persists_all_fields, test_faq_shape_validation, test_patch_faq_replaces_not_merges, test_list_agents_with_campaign_count, test_patch_agent_partial, test_delete_agent_sets_conversation_to_null, test_delete_agent_cascades_assignments, test_duplicate_agent_auto_name, test_duplicate_race_handling.
    - File `tests/test_send.py` exists with test functions: test_send_requires_ai_context_id, test_send_cross_workspace_agent_404, test_same_agent_id_works_for_multiple_senders.
    - `pytest tests/test_agents.py tests/test_send.py --collect-only -q` succeeds — pytest discovers all tests without ImportError.
    - At this stage tests are expected to FAIL when run (endpoints not implemented yet) — but collection must succeed.
  </acceptance_criteria>
  <done>15 Wave-0 тестов написаны, pytest успешно их собирает (collect-only), все стартово RED (endpoints в Tasks 2-5 ещё не существуют) — драйвят имплементацию.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Pydantic схемы — AgentCreate/Update/Response/ListResponse + FaqItem; рерайт SendMessageRequest под required ai_context_id</name>
  <files>
    app/schemas/__init__.py
  </files>
  <read_first>
    - app/schemas/__init__.py — текущие схемы (FolderResponse, ContactResponse — Phase 2 паттерны), SendMessageRequest (текущая структура, lines 16-31).
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Example 2" (lines 487-540) — готовый Pydantic шаблон AgentCreate/Update/Response/ListResponse + FaqItem.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-02" — финальные поля; §"D-10" — campaign_count хардкод.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"C-01" — FAQ shape = массив объектов.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"C-03" — partial PATCH с Optional.
  </read_first>
  <behavior>
    - `AgentCreate(name="x")` валиден (минимум только name).
    - `AgentCreate(name="x", faq=[{"question": "Q", "answer": "A"}])` валиден.
    - `AgentCreate(name="x", faq={"Q": "A"})` падает с ValidationError (FAQ dict shape запрещён — массив объектов).
    - `AgentResponse.model_dump()` всегда содержит `campaign_count: int = 0`.
    - `SendMessageRequest(ai_context_id=UUID, recipient_phone="...", message="...")` валиден без `sender`.
    - `SendMessageRequest(recipient_phone="...", message="...")` падает с ValidationError (ai_context_id required).
  </behavior>
  <action>
    В `app/schemas/__init__.py`:

    1. Добавить в конец файла (или в логически подходящем месте, после Folder/Contact схем) блок:

    ```python
    # === Agents (Phase 3 — AGNT-01..04) ===

    class FaqItem(BaseModel):
        """Single FAQ Q&A pair. C-01 resolution: array of objects (over dict)."""
        question: str = Field(..., max_length=500)
        answer: str = Field(..., max_length=2000)


    class AgentCreate(BaseModel):
        """POST /api/v1/agents body (D-02)."""
        name: str = Field(..., min_length=1, max_length=100)
        system_prompt: Optional[str] = None
        rules: Optional[str] = None
        tone_of_voice: Optional[str] = None
        faq: List[FaqItem] = Field(default_factory=list)
        company_info: Optional[str] = None
        product_info: Optional[str] = None


    class AgentUpdate(BaseModel):
        """PATCH /api/v1/agents/{id} body. Partial PATCH (C-03 Phase 2 convention)."""
        name: Optional[str] = Field(None, min_length=1, max_length=100)
        system_prompt: Optional[str] = None
        rules: Optional[str] = None
        tone_of_voice: Optional[str] = None
        # None = leave unchanged; [] = clear FAQ; [...] = full replace (Pitfall 7)
        faq: Optional[List[FaqItem]] = None
        company_info: Optional[str] = None
        product_info: Optional[str] = None


    class AgentResponse(BaseModel):
        """GET / POST / PATCH response body. D-10: campaign_count hardcoded 0 в Phase 3."""
        model_config = ConfigDict(from_attributes=True)
        id: UUID
        name: str
        system_prompt: Optional[str]
        rules: Optional[str]
        tone_of_voice: Optional[str]
        faq: List[FaqItem] = []
        company_info: Optional[str]
        product_info: Optional[str]
        campaign_count: int = 0
        created_at: datetime
        updated_at: datetime


    class AgentListResponse(BaseModel):
        agents: List[AgentResponse]
        total: int
    ```

    2. **Полностью переписать `SendMessageRequest`** (lines 16-31) — Phase 3 D-06: ai_context_id обязательный:
    ```python
    class SendMessageRequest(BaseModel):
        """Phase 3 rewrite (D-06): ai_context_id REQUIRED (no derive from sender)."""
        ai_context_id: UUID = Field(..., description="Agent ID (workspace-scoped validation)")
        sender_slug: Optional[str] = Field(None, description="Explicit sender; if None, rotation picks one")
        recipient_phone: str = Field(..., description="Номер получателя с кодом страны")
        recipient_name: Optional[str] = Field(None, description="Имя получателя")
        message: str = Field(..., max_length=4096, description="Текст сообщения")
        as_draft: bool = Field(False, description="Сохранить как черновик")
        metadata: Optional[dict] = Field(default_factory=dict, description="Дополнительные данные")
        callback_url: Optional[str] = Field(None, description="Webhook-уведомление после отправки")
    ```

    **Удалить** `@model_validator(mode="after") def sender_or_context_required` (lines 27-31) — больше не нужен, ai_context_id required напрямую.

    3. **НЕ ТРОГАТЬ** `SendFileRequest` (lines 52-67) — он используется в legacy send-file endpoint, который в Phase 3 НЕ переписывается (С-04 — только `/send` основной endpoint).

    4. Обеспечить `from typing import List` импорт (если ещё нет — добавить).
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && python -c "from app.schemas import AgentCreate, AgentUpdate, AgentResponse, AgentListResponse, FaqItem, SendMessageRequest; from uuid import uuid4; a = AgentCreate(name='x'); print(a.model_dump()); s = SendMessageRequest(ai_context_id=uuid4(), recipient_phone='+79991234567', message='hi'); print(s.model_dump())"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "class AgentCreate" app/schemas/__init__.py` matches.
    - `grep -n "class AgentUpdate" app/schemas/__init__.py` matches.
    - `grep -n "class AgentResponse" app/schemas/__init__.py` matches AND the class contains `campaign_count: int = 0`.
    - `grep -n "class AgentListResponse" app/schemas/__init__.py` matches.
    - `grep -n "class FaqItem" app/schemas/__init__.py` matches.
    - `SendMessageRequest` definition in `app/schemas/__init__.py` contains `ai_context_id: UUID = Field(...,` (no Optional, no None default).
    - `SendMessageRequest` does NOT contain `model_validator` with `sender_or_context_required`.
    - Python smoke check exits 0: `python -c "from app.schemas import AgentCreate, AgentResponse, SendMessageRequest; AgentCreate(name='x')"`.
    - `python -c "from app.schemas import SendMessageRequest; SendMessageRequest(recipient_phone='+79991234567', message='hi')"` raises pydantic ValidationError (ai_context_id required).
  </acceptance_criteria>
  <done>Pydantic-схемы готовы; SendMessageRequest требует ai_context_id; AgentResponse всегда содержит campaign_count=0 (D-10).</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Создать app/routers/agents.py — 6 endpoints под /api/v1/agents (CRUD + duplicate)</name>
  <files>
    app/routers/agents.py
  </files>
  <read_first>
    - app/routers/folders.py — drop-in pattern для всех 6 endpoints (workspace-scope, 409 duplicate, partial PATCH, 404 not found).
    - app/routers/contexts.py — старый файл, читать для понимания legacy logic, но НЕ копировать (использует verify_api_key и дропнутые поля).
    - app/models/__init__.py — AIContext после Plan 03-01 cleanup.
    - app/utils/auth.py — AuthCtx + auth_dep.
    - app/schemas/__init__.py — добавленные в Task 2 AgentCreate/Update/Response/ListResponse + FaqItem.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Example 3" (создание), §"Example 4" (duplicate), §"Pattern 5" (delete).
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Pitfall 2" — retry on IntegrityError для duplicate race.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Pitfall 7" — FAQ PATCH = full replacement.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-06..D-10" — все endpoints локированные decisions.
  </read_first>
  <behavior>
    - 6 endpoints: GET list, POST create, GET by id, PATCH partial, DELETE hard, POST duplicate. Все workspace-scoped через `Depends(auth_dep)`.
    - GET list → 200 + AgentListResponse {agents: [...], total: N}.
    - POST create → 201 + AgentResponse; 409 на дубль имени; campaign_count всегда 0.
    - GET by id → 200 + AgentResponse; 404 если cross-workspace или не существует.
    - PATCH → 200 + AgentResponse; partial update (только переданные Optional поля); FAQ — full replacement.
    - DELETE → 204; hard delete; FK конкаскадно (conversations.ai_context_id → NULL; context_contact_assignments → CASCADE).
    - POST duplicate без body → 201 + AgentResponse с новым именем «{name} (copy)», «{name} (copy 2)», etc.; retry-on-IntegrityError max 5 раз.
  </behavior>
  <action>
    Создать `app/routers/agents.py` с полной имплементацией:

    ```python
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
    from typing import List
    from uuid import UUID

    from fastapi import APIRouter, Depends, HTTPException
    from sqlalchemy import func as sql_func, select, text
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


    def _agent_to_response(agent: AIContext) -> AgentResponse:
        """Build AgentResponse with hardcoded campaign_count=0 (D-10).

        TODO(phase-4): campaign_count via SELECT COUNT(*) FROM campaigns WHERE agent_id = ai_contexts.id.
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
            campaign_count=0,  # D-10
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )


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
        """List all agents in current workspace (D-10 campaign_count=0)."""
        result = await db.execute(
            select(AIContext)
            .where(AIContext.workspace_id == ctx.workspace_id)
            # TODO(v2-rls): replaced by RLS policy
            .order_by(AIContext.created_at.desc())
        )
        agents = result.scalars().all()
        return AgentListResponse(
            agents=[_agent_to_response(a) for a in agents],
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
        return _agent_to_response(agent)


    @router.get("/{agent_id}", response_model=AgentResponse)
    async def get_agent(
        agent_id: UUID,
        ctx: AuthCtx = Depends(auth_dep),
        db: AsyncSession = Depends(get_db),
    ):
        """Get agent by id (workspace-scoped). 404 если cross-tenant."""
        agent = await _load_agent(db, ctx, agent_id)
        return _agent_to_response(agent)


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
        return _agent_to_response(agent)


    @router.delete("/{agent_id}", status_code=204)
    async def delete_agent(
        agent_id: UUID,
        ctx: AuthCtx = Depends(auth_dep),
        db: AsyncSession = Depends(get_db),
    ):
        """D-08: hard delete. FK SET NULL у conversations, CASCADE у context_contact_assignments."""
        agent = await _load_agent(db, ctx, agent_id)

        # TODO(phase-4): also block on active campaign attachment (D-09)
        # — заглушка до появления Campaign модели в Phase 4.

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
                return _agent_to_response(new_agent)
            except IntegrityError:
                await db.rollback()
                continue

        raise HTTPException(
            status_code=409,
            detail={"code": "DUPLICATE_RACE",
                    "message": "Failed to allocate unique name after 5 retries"},
        )
    ```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && python -c "from app.routers.agents import router; print('imports ok'); routes = [r.path for r in router.routes]; assert '/api/v1/agents' in routes; assert '/api/v1/agents/{agent_id}' in routes; assert '/api/v1/agents/{agent_id}/duplicate' in routes; print(routes)"</automated>
  </verify>
  <acceptance_criteria>
    - File `app/routers/agents.py` exists.
    - File contains `router = APIRouter(prefix="/api/v1/agents", tags=["agents"])`.
    - File defines all 6 async endpoint functions: `list_agents`, `create_agent`, `get_agent`, `update_agent`, `delete_agent`, `duplicate_agent`.
    - File contains `.where(AIContext.workspace_id == ctx.workspace_id)` in EVERY endpoint (verify via grep — should match at least 6 times across endpoints + helpers).
    - File contains `from app.utils.auth import AuthCtx, auth_dep` AND every endpoint signature contains `ctx: AuthCtx = Depends(auth_dep)`.
    - File contains `_generate_duplicate_name` helper function.
    - File contains `for attempt in range(5)` AND `except IntegrityError` in `duplicate_agent` (Pitfall 2 retry loop).
    - File contains `# TODO(phase-4): also block on active campaign attachment` comment in `delete_agent`.
    - Python smoke check exits 0: `python -c "from app.routers.agents import router"`.
    - All endpoint paths match expected URLs (smoke command above verifies).
  </acceptance_criteria>
  <done>app/routers/agents.py готов; 6 endpoints определены; workspace-scoped через AuthDep; duplicate-retry pattern implemented; campaign_count=0 hardcoded.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 4: Полный рерайт app/routers/send.py под AuthDep + explicit ai_context_id + удалить legacy contexts.py</name>
  <files>
    app/routers/send.py
    app/routers/contexts.py
  </files>
  <read_first>
    - app/routers/send.py (текущий) — legacy с verify_api_key + sender.is_active + AIContext.is_active (всё это уже не существует).
    - app/services/rotation.py — `get_or_assign_sender(db, context_id, contact_phone, workspace_id)` — сигнатура.
    - app/services/queue.py — `enqueue_message(...)` после Plan 03-01 правки (новый параметр ai_context_id).
    - app/routers/senders.py — паттерн _load_sender_by_slug (workspace-scoped).
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Example 5" (lines 654-738) — готовый template для send.py rewrite.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-06" — explicit ai_context_id в body, workspace validation.
  </read_first>
  <behavior>
    - POST `/api/v1/send` принимает body с обязательным `ai_context_id: UUID`. Если отсутствует — 422.
    - Если ai_context_id принадлежит другому workspace — 404 AGENT_NOT_FOUND.
    - Если `sender_slug` указан → найти sender в workspace + проверить eligibility (lifecycle_status='active' AND auth_status='ok'). 404 SENDER_NOT_FOUND / 409 SENDER_NOT_READY.
    - Если `sender_slug` отсутствует → `get_or_assign_sender(db, ai_context_id, recipient_phone, workspace_id)`. ValueError → 409 NO_ACTIVE_SENDER.
    - Вызвать `enqueue_message(db, workspace_id, sender_id, sender_slug, recipient_phone, ..., ai_context_id=request.ai_context_id)`.
    - Вернуть EnqueueResponse 200 + queue_id, queue_position, sender_slug, estimated_send_at, timestamp.
    - `send-file` и `send-batch` endpoints — НЕ переписывать в Phase 3 (С-04, оставить как legacy либо удалить). Планировщик решает: удалить целиком (минимум кода) или оставить с TODO(phase-4). Рекомендация: удалить /send-file и /send-batch роуты — Phase 3 фокус на основной /send.
  </behavior>
  <action>
    1. **Полностью переписать `app/routers/send.py`** на:

    ```python
    """Send router (Phase 3 rewrite — D-06).

    POST /api/v1/send — единственный endpoint отправки, под Depends(auth_dep).
    Принимает explicit ai_context_id в body. Валидирует workspace-принадлежность агента.
    """

    import logging
    from datetime import datetime, timezone

    from fastapi import APIRouter, Depends, HTTPException
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import get_db
    from app.models import AIContext, Sender
    from app.schemas import SendMessageRequest, EnqueueResponse
    from app.services.queue import enqueue_message
    from app.services.rotation import get_or_assign_sender
    from app.utils.auth import AuthCtx, auth_dep

    logger = logging.getLogger(__name__)

    router = APIRouter(prefix="/api/v1", tags=["send"])


    @router.post("/send", response_model=EnqueueResponse)
    async def send_message(
        request: SendMessageRequest,
        ctx: AuthCtx = Depends(auth_dep),
        db: AsyncSession = Depends(get_db),
    ):
        """Enqueue a Telegram message (workspace-scoped, agent-explicit).

        Body требует: ai_context_id (UUID), recipient_phone (str), message (str).
        Optional: sender_slug (если None — rotation), recipient_name, as_draft, metadata, callback_url.
        """
        # 1. Validate agent exists in caller's workspace (D-06)
        agent_result = await db.execute(
            select(AIContext).where(
                AIContext.id == request.ai_context_id,
                AIContext.workspace_id == ctx.workspace_id,
                # TODO(v2-rls): replaced by RLS policy
            )
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "AGENT_NOT_FOUND",
                    "message": f"Agent {request.ai_context_id} not found in workspace",
                },
            )

        # 2. Resolve sender — explicit slug OR rotation
        if request.sender_slug:
            sender_result = await db.execute(
                select(Sender).where(
                    Sender.slug == request.sender_slug,
                    Sender.workspace_id == ctx.workspace_id,
                )
            )
            sender = sender_result.scalar_one_or_none()
            if sender is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "SENDER_NOT_FOUND",
                            "message": f"Sender '{request.sender_slug}' not found"},
                )
            if sender.lifecycle_status != "active" or sender.auth_status != "ok":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SENDER_NOT_READY",
                        "lifecycle_status": sender.lifecycle_status,
                        "auth_status": sender.auth_status,
                        "message": f"Sender '{request.sender_slug}' is not ready (lifecycle={sender.lifecycle_status}, auth={sender.auth_status})",
                    },
                )
        else:
            # Rotation pick (workspace-only since Phase 3 D-04)
            try:
                sender = await get_or_assign_sender(
                    db=db,
                    context_id=request.ai_context_id,
                    contact_phone=request.recipient_phone,
                    workspace_id=ctx.workspace_id,
                )
            except ValueError as e:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "NO_ACTIVE_SENDER", "message": str(e)},
                )

        # 3. Enqueue with explicit ai_context_id (Plan 03-01 added param)
        try:
            info = await enqueue_message(
                db=db,
                workspace_id=ctx.workspace_id,
                sender_id=sender.id,
                sender_slug=sender.slug,
                recipient_phone=request.recipient_phone,
                recipient_name=request.recipient_name,
                message_text=request.message,
                as_draft=request.as_draft,
                metadata=request.metadata,
                callback_url=request.callback_url,
                ai_context_id=request.ai_context_id,
            )
        except Exception as e:
            logger.error(f"[send] enqueue failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"code": "ENQUEUE_FAILED", "message": str(e)},
            )

        logger.info(
            f"[send] workspace={ctx.workspace_id} agent={request.ai_context_id} "
            f"sender={sender.slug} to={request.recipient_phone} queue={info['queue_id'][:8]}"
        )
        return EnqueueResponse(
            success=True,
            queued=True,
            queue_id=info["queue_id"],
            queue_position=info["queue_position"],
            sender_slug=sender.slug,
            estimated_send_at=info["estimated_send_at"],
            timestamp=datetime.now(timezone.utc),
        )
    ```

    Удалить `send-file` и `send-batch` endpoints из `send.py` — они legacy, не имеют отношения к Phase 3 success criteria (С-04 + RESEARCH рекомендация).

    2. **Удалить старый `app/routers/contexts.py`** — он использует выпиленный verify_api_key и дропнутые поля (is_active, webhook_functions, max_message_length). Pythonически `git rm app/routers/contexts.py`. Если планировщик предпочитает не удалять — пометить файл `.bak` (переименовать), но НЕ оставлять `contexts.py` в `app/routers/` чтобы избежать случайного `app.include_router(contexts.router)` в будущем.

    Принять рекомендацию: УДАЛИТЬ файл (минимум кода). Если новые имена не совпадают — нет коллизий импортов.
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && python -c "from app.routers.send import router; print('imports ok'); print([r.path for r in router.routes])"</automated>
  </verify>
  <acceptance_criteria>
    - File `app/routers/send.py` contains `from app.utils.auth import AuthCtx, auth_dep`.
    - `grep -n "from app.routers.auth import verify_api_key" app/routers/send.py` returns NO matches (legacy auth removed).
    - File contains `@router.post("/send", response_model=EnqueueResponse)` AND function signature with `ctx: AuthCtx = Depends(auth_dep)`.
    - File contains check `.where(AIContext.id == request.ai_context_id, AIContext.workspace_id == ctx.workspace_id)` — exactly that condition for agent workspace validation.
    - File contains call `enqueue_message(..., ai_context_id=request.ai_context_id)` (explicit param).
    - `grep -n "send-file\|send-batch" app/routers/send.py` returns NO matches — legacy endpoints removed.
    - `grep -n "AIContext.is_active" app/routers/send.py` returns NO matches.
    - File `app/routers/contexts.py` is deleted (`ls app/routers/contexts.py` returns "No such file") OR renamed with `.bak` suffix.
    - Python smoke check exits 0: `python -c "from app.routers.send import router"`.
  </acceptance_criteria>
  <done>send.py переписан под Phase 3 D-06 — workspace-scoped, agent explicit в body. Legacy contexts.py удалён.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 5: Зарегистрировать agents.router + send.router в app/main.py</name>
  <files>
    app/main.py
  </files>
  <read_first>
    - app/main.py — текущий список registered routers (lines 13-21, 81-87).
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-06" — обязательная регистрация в main.py.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Open Question 4" — Yes, регистрируем send.router сразу.
  </read_first>
  <behavior>
    - После запуска FastAPI приложения, `GET /api/v1/agents` отвечает (был бы 401 без auth, но не 404).
    - `POST /api/v1/send` отвечает (тот же auth check).
    - Существующие health/workspace/senders/folders/contacts/check_contacts/onboarding endpoints продолжают работать.
  </behavior>
  <action>
    В `app/main.py`:

    1. **Lines 13-21** — расширить импорт роутеров:
    ```python
    from app.routers import (
        agents,
        check_contacts,
        contacts,
        folders,
        health,
        onboarding,
        send,
        senders,
        workspace,
    )
    ```

    2. **Lines 81-87** — добавить `include_router` для agents и send (после `onboarding.router`):
    ```python
    # Include routers.
    #   Phase 1: health, workspace (D-14 lockdown)
    #   Phase 2: senders, folders (workspace-scoped, replaces legacy routers)
    #   Phase 3: agents (CRUD AI templates), send (rewrite under AuthDep)
    app.include_router(health.router)
    app.include_router(workspace.router)
    app.include_router(senders.router)
    app.include_router(folders.router)
    app.include_router(contacts.router)
    app.include_router(check_contacts.router)
    app.include_router(onboarding.router)
    app.include_router(agents.router)
    app.include_router(send.router)
    ```

    3. **Lines 90-103** — обновить root endpoint description:
    ```python
    @app.get("/")
    async def root():
        return {
            "service": "Outreach Platform API",
            "version": "2.0.0-phase3",
            "docs": "/docs",
            "health": "/api/v1/health",
            "endpoints": {
                "auth_me": "POST /api/v1/auth/me",
                "workspace": "/api/v1/workspace",
                "api_keys": "/api/v1/workspace/api-keys",
                "agents": "/api/v1/agents",  # Phase 3
                "send": "POST /api/v1/send",  # Phase 3
            }
        }
    ```

    4. Также **обновить** FastAPI title/version (line 62-66):
    ```python
    app = FastAPI(
        title="Outreach Platform API",
        description="Multi-tenant Telegram outreach SaaS (Phase 3: agents + send)",
        version="2.0.0-phase3",
        lifespan=lifespan
    )
    ```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && python -c "from app.main import app; routes = [r.path for r in app.routes]; assert '/api/v1/agents' in routes, routes; assert '/api/v1/agents/{agent_id}' in routes; assert '/api/v1/send' in routes; print('routes ok:', sorted(r for r in routes if r.startswith('/api/v1/')))"    <automated>python -c "from app.main import app; routes = [r.path for r in app.routes]; assert '/api/v1/agents' in routes, routes; assert '/api/v1/send' in routes; print('routes ok')"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "from app.routers import" app/main.py` shows the import block includes both `agents` and `send` names.
    - `grep -n "app.include_router(agents.router)" app/main.py` matches.
    - `grep -n "app.include_router(send.router)" app/main.py` matches.
    - `grep -n "app.include_router(contexts.router)" app/main.py` returns NO matches (legacy NOT re-registered).
    - `app/main.py` FastAPI() constructor contains `version="2.0.0-phase3"`.
    - Python smoke check exits 0: `python -c "from app.main import app; routes = [r.path for r in app.routes]; assert '/api/v1/agents' in routes; assert '/api/v1/send' in routes"`.
  </acceptance_criteria>
  <done>main.py регистрирует agents.router и send.router; legacy contexts.router НЕ зарегистрирован; продукт снова отвечает на основной endpoint отправки (D-06).</done>
</task>

<task type="auto" tdd="true">
  <name>Task 6: Запустить полный suite Wave 0 — все 15 тестов должны быть зелёными</name>
  <files>
    tests/test_agents.py
    tests/test_send.py
  </files>
  <read_first>
    - Все ранее созданные файлы (Tasks 1-5) — это финальный gate plan'а.
    - .planning/phases/03-agents-ai-templates/03-VALIDATION.md §"Per-Task Verification Map" — точные имена 15 тестов.
  </read_first>
  <behavior>
    - 13 тестов test_agents.py + 2-3 теста test_send.py — все зелёные.
    - Существующие Phase 1+2 тесты не сломаны.
  </behavior>
  <action>
    Запустить полный suite Phase 3:
    ```bash
    pytest tests/test_agents.py tests/test_send.py -x -v
    ```

    Если падают тесты — fix-pass по каждому failing test:
    1. Прочитать output теста.
    2. Найти соответствующий endpoint/schema в Tasks 2-5.
    3. Поправить — НЕ менять test (test это спецификация).
    4. Перезапустить.

    Дополнительно запустить regression check (Phase 1+2 не сломались):
    ```bash
    pytest tests/ -x -v --ignore=tests/test_agents.py --ignore=tests/test_send.py
    ```

    Если какой-то Phase 2 тест упал — почти наверняка из-за adapter правок plan 03-01 (senders.py cleanup) — fix surgically.

    Конечная проверка — full suite зелёный:
    ```bash
    pytest tests/ -x -v
    ```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_agents.py tests/test_send.py -x -v</automated>
  </verify>
  <acceptance_criteria>
    - `pytest tests/test_agents.py -x -v` reports 13 tests, all PASS.
    - `pytest tests/test_send.py -x -v` reports 3 tests (test_send_requires_ai_context_id, test_send_cross_workspace_agent_404, test_same_agent_id_works_for_multiple_senders), all PASS.
    - `pytest tests/ -x -v` (full suite) reports 0 failures.
    - All 12 plan 03-01 tests (migration_015 + ai_engine + listener + rotation + queue_enqueue + senders) still pass.
    - No regressions in Phase 1+2 tests (folders, contacts, senders existing tests, workspace, auth).
  </acceptance_criteria>
  <done>Все 15 Wave-0 тестов Phase 3 зелёные; полный suite зелёный; продукт готов к деплою (api снова отвечает на /agents + /send).</done>
</task>

</tasks>

<verification>
**Per-task automated commands** (run after each task):
- Task 1: `pytest tests/test_agents.py tests/test_send.py --collect-only -q` (15 tests discovered, no ImportError)
- Task 2: `python -c "from app.schemas import AgentCreate, AgentResponse, SendMessageRequest; AgentCreate(name='x')"` (no errors)
- Task 3: `python -c "from app.routers.agents import router"` + endpoint paths check
- Task 4: `python -c "from app.routers.send import router"` + no verify_api_key import
- Task 5: `python -c "from app.main import app; assert '/api/v1/agents' in [r.path for r in app.routes]"`
- Task 6: Full Phase 3 suite + regression

**Plan-level verification** (run at end):
```bash
pytest tests/test_agents.py tests/test_send.py tests/test_migration_015.py tests/test_ai_engine.py tests/test_listener.py tests/test_rotation.py tests/test_queue_enqueue.py tests/test_senders.py -x -v
```
Expected: 27 tests pass (15 Phase 3 plan-02 + 12 Phase 3 plan-01).

**Goal-backward truths verification:**
- `curl -X POST http://localhost:8000/api/v1/agents -H "Authorization: Bearer $TOKEN" -d '{"name":"X"}'` → 201 + AgentResponse with `campaign_count: 0`.
- `curl -X POST http://localhost:8000/api/v1/agents -H "Authorization: Bearer $TOKEN" -d '{"name":"X"}'` (повтор) → 409 AGENT_NAME_DUPLICATE.
- `curl -X POST http://localhost:8000/api/v1/agents/$ID/duplicate -H "Authorization: Bearer $TOKEN"` → 201 + новый агент с именем "X (copy)".
- `curl -X POST http://localhost:8000/api/v1/send -H "Authorization: Bearer $TOKEN" -d '{"recipient_phone":"+7...","message":"hi"}'` → 422 (отсутствует ai_context_id).
- `curl -X POST http://localhost:8000/api/v1/send -H "Authorization: Bearer $TOKEN" -d '{"ai_context_id":"<other-ws-id>","recipient_phone":"+7...","message":"hi"}'` → 404 AGENT_NOT_FOUND.

**Manual UX checks (deferred to /gsd:verify-work):**
- Lovable рендерит /agents список с campaign_count=0 без ошибок.
- End-to-end smoke: POST /agents → POST /send → AI отвечает на входящее через debounce.
</verification>

<success_criteria>
- [ ] app/routers/agents.py существует, 6 endpoints workspace-scoped через AuthDep
- [ ] app/routers/send.py переписан под AuthDep, ai_context_id обязателен в body, agent workspace validation
- [ ] app/routers/contexts.py удалён (или переименован в .bak)
- [ ] app/schemas/__init__.py содержит AgentCreate, AgentUpdate, AgentResponse (с campaign_count=0), AgentListResponse, FaqItem; SendMessageRequest требует ai_context_id
- [ ] app/main.py регистрирует agents.router + send.router
- [ ] tests/test_agents.py: 13 тестов AGNT-01..04 (create/dup/list/patch/delete/duplicate/cascade) — все зелёные
- [ ] tests/test_send.py: 3 теста (required ai_context_id, cross-workspace 404, same agent multi-sender) — все зелёные
- [ ] Полный pytest suite зелёный (0 failures)
- [ ] AGNT-01 (create with name) ✓
- [ ] AGNT-02 (context/задача/тон/FAQ persist) ✓
- [ ] AGNT-03 (один agent в нескольких senders/send-запросах) ✓
- [ ] AGNT-04 (CRUD + duplicate + list with campaign_count) ✓
- [ ] CLAUDE.md respected: общение на русском, async everywhere, rate-limit/debounce НЕ тронуты
- [ ] TODO(phase-4) markers оставлены для DELETE block, document_webhook, listener `_send_to_ai`
</success_criteria>

<output>
After completion, create `.planning/phases/03-agents-ai-templates/03-02-SUMMARY.md` per template, with sections:
- What was built (agents router, send rewrite, schemas, main.py registration)
- Files modified (full list with line counts)
- Test coverage (15 Wave-0 tests passing; 27 total Phase 3 tests passing)
- Manual verification deferred to /gsd:verify-work (Lovable UI smoke + Telethon E2E)
- TODOs for Phase 4 (campaign_count real query, active campaign block on DELETE, listener pull ai_context_id from campaign, send-file/send-batch rewrite)
- Carry-overs into Phase 4 (Campaign model должна добавить `campaigns.agent_id FK ai_contexts.id`; ROADMAP-update placeholder в conversations.ai_context_id)
</output>
