"""Phase 16 — Knowledge Base CRUD / isolation / aggregate / attach (Wave 0 RED).

These tests fully ASSERT the documented behaviour of the KB endpoints + helpers
that Plans 16-02/16-03 will implement. Until those plans land the production
modules (`app.routers.knowledge_bases`, `app.services.kb_search`/aggregate) do
not exist, so the deferred in-body imports raise ImportError and every test here
FAILS RED — the expected Wave-0 state per 16-VALIDATION.md.

The deferred imports (inside each test body, NOT at module top) keep
`pytest --collect-only` clean so the full suite still collects with 0 errors.

Test → requirement map (names consumed by later verify commands):
- test_create_kb_workspace_isolated → KB-01 (KB scoped to workspace; other ws can't see)
- test_kb_detail_aggregate          → KB-03 (DOCUMENTS/INDEXED/PROCESSING/FAILED/STORAGE)
- test_attach_detach_agent          → KB-04 (M:N attach/detach + reverse list)
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_kb_state(async_db_session):
    """Purge committed KB rows after each test so a stray pending doc never leaks
    into the global-claim KnowledgeIngestWorker test (test_kb_ingest_worker.py).
    The aggregate test seeds committed kb_documents rows; mirror the autouse
    cleanup already present in test_kb_ingest_worker.py."""
    yield
    await async_db_session.execute(text("DELETE FROM kb_chunks"))
    await async_db_session.execute(text("DELETE FROM kb_documents"))
    await async_db_session.execute(text("DELETE FROM agent_knowledge_bases"))
    await async_db_session.execute(text("DELETE FROM knowledge_bases"))
    await async_db_session.commit()


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    """Bind a Supabase user (JWT sub) to a workspace as owner.

    user_workspaces has DB-level UNIQUE(supabase_user_id) (migration 023) and the
    schema is built once per session, so each test uses a DISTINCT sub bound to
    its own workspace (mirrors test_pool_endpoints.py convention).
    """
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ─── KB-01 ───────────────────────────────────────────────────────────────────

async def test_create_kb_workspace_isolated(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """KB-01: a KB created in workspace A is NOT visible to workspace B.

    Create a KB scoped to workspace A, then list/fetch it scoped to a different
    workspace B and assert it does not appear (empty list / 404).
    """
    await _bind(async_db_session, test_workspace.id, "kb-iso-a")

    # Create a KB in workspace A.
    create = await async_client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Workspace A KB", "description": "private to A"},
        headers=_auth_headers(valid_supabase_jwt, "kb-iso-a"),
    )
    assert create.status_code in (200, 201), create.text
    kb_id = create.json()["id"]

    # A second workspace + its owner.
    ws_b_id = uuid.uuid4()
    await async_db_session.execute(text(
        "INSERT INTO workspaces (id, name) VALUES (:id, 'Workspace B')"
    ), {"id": str(ws_b_id)})
    await _bind(async_db_session, ws_b_id, "kb-iso-b")

    # Workspace B cannot list workspace A's KB.
    list_b = await async_client.get(
        "/api/v1/knowledge-bases",
        headers=_auth_headers(valid_supabase_jwt, "kb-iso-b"),
    )
    assert list_b.status_code == 200, list_b.text
    b_ids = [kb["id"] for kb in list_b.json()]
    assert kb_id not in b_ids

    # Workspace B cannot fetch workspace A's KB by id.
    detail_b = await async_client.get(
        f"/api/v1/knowledge-bases/{kb_id}",
        headers=_auth_headers(valid_supabase_jwt, "kb-iso-b"),
    )
    assert detail_b.status_code == 404, detail_b.text


# ─── KB-03 (aggregate) ─────────────────────────────────────────────────────────

async def test_kb_detail_aggregate(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """KB-03: KB detail header aggregates DOCUMENTS / INDEXED / PROCESSING /
    FAILED counts + STORAGE = SUM(kb_documents.size_bytes).

    Seed documents in mixed statuses with known sizes and assert the aggregate.
    """
    await _bind(async_db_session, test_workspace.id, "kb-agg")

    create = await async_client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Aggregate KB"},
        headers=_auth_headers(valid_supabase_jwt, "kb-agg"),
    )
    assert create.status_code in (200, 201), create.text
    kb_id = create.json()["id"]

    # Seed 4 docs directly: 2 indexed, 1 processing, 1 failed; known sizes.
    docs = [
        ("d-indexed-1", "indexed", 100),
        ("d-indexed-2", "indexed", 250),
        ("d-processing", "processing", 50),
        ("d-failed", "failed", 25),
    ]
    for name, status, size in docs:
        await async_db_session.execute(text("""
            INSERT INTO kb_documents
                (workspace_id, kb_id, name, source_kind, size_bytes, status)
            VALUES (:ws, :kb, :name, 'txt', :size, :status)
        """), {
            "ws": str(test_workspace.id), "kb": kb_id,
            "name": name, "size": size, "status": status,
        })
    await async_db_session.commit()

    detail = await async_client.get(
        f"/api/v1/knowledge-bases/{kb_id}",
        headers=_auth_headers(valid_supabase_jwt, "kb-agg"),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    # Aggregate counters (key names per 16-UI-SPEC D-09; accept counts dict or flat).
    counts = body.get("counts", body)
    assert int(counts["documents"]) == 4
    assert int(counts["indexed"]) == 2
    assert int(counts["processing"]) == 1
    assert int(counts["failed"]) == 1
    assert int(counts["storage_bytes"]) == 425  # 100 + 250 + 50 + 25


# ─── KB-04 ───────────────────────────────────────────────────────────────────

async def test_attach_detach_agent(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory,
):
    """KB-04: attach a KB to an agent (M:N), assert the row + reverse list,
    then detach and assert removed.
    """
    await _bind(async_db_session, test_workspace.id, "kb-attach")
    agent = await test_agent_factory(name="KB Sales Agent")

    create = await async_client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Attachable KB"},
        headers=_auth_headers(valid_supabase_jwt, "kb-attach"),
    )
    assert create.status_code in (200, 201), create.text
    kb_id = create.json()["id"]

    # Attach the KB to the agent.
    attach = await async_client.post(
        f"/api/v1/knowledge-bases/{kb_id}/agents",
        json={"agent_id": str(agent.id)},
        headers=_auth_headers(valid_supabase_jwt, "kb-attach"),
    )
    assert attach.status_code in (200, 201), attach.text

    # M:N row exists in agent_knowledge_bases.
    row_count = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM agent_knowledge_bases
        WHERE agent_id = :aid AND kb_id = :kid
    """), {"aid": str(agent.id), "kid": kb_id})).scalar_one()
    assert int(row_count) == 1

    # Reverse list — agents-for-kb (Agents tab, D-11) includes this agent.
    agents_for_kb = await async_client.get(
        f"/api/v1/knowledge-bases/{kb_id}/agents",
        headers=_auth_headers(valid_supabase_jwt, "kb-attach"),
    )
    assert agents_for_kb.status_code == 200, agents_for_kb.text
    attached_agent_ids = [a["id"] for a in agents_for_kb.json()]
    assert str(agent.id) in attached_agent_ids

    # Detach.
    detach = await async_client.delete(
        f"/api/v1/knowledge-bases/{kb_id}/agents/{agent.id}",
        headers=_auth_headers(valid_supabase_jwt, "kb-attach"),
    )
    assert detach.status_code in (200, 204), detach.text

    row_count_after = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM agent_knowledge_bases
        WHERE agent_id = :aid AND kb_id = :kid
    """), {"aid": str(agent.id), "kid": kb_id})).scalar_one()
    assert int(row_count_after) == 0
