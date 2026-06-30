"""Phase 16 — KB ingest upload endpoint (Wave 0 RED).

Asserts the documented upload/paste behaviour of the KB document endpoint that
Plan 16-03 will implement: POSTing a small file/text creates a `kb_documents`
row with status='pending' and size_bytes set, returning 202 (the worker does the
extract/chunk/embed asynchronously — Pitfall: never parse in the request handler).

Until the router lands the endpoint does not exist, so the test FAILS RED (the
endpoint 404s and the kb_documents row is never created). Expected Wave-0 state.

Test → requirement map:
- test_upload_creates_pending_doc → KB-02
"""

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_kb_state(async_db_session):
    """The KnowledgeIngestWorker claims ANY workspace's pending docs globally
    (``ORDER BY created_at ASC ... FOR UPDATE SKIP LOCKED``). The upload endpoint
    commits a real ``pending`` kb_documents row, so purge committed KB rows after
    each test — otherwise a leftover pending doc leaks into a later worker test
    (test_kb_ingest_worker.py) and gets claimed instead of that test's own doc.
    Mirrors the autouse cleanup already present in test_kb_ingest_worker.py."""
    yield
    await async_db_session.execute(text("DELETE FROM kb_chunks"))
    await async_db_session.execute(text("DELETE FROM kb_documents"))
    await async_db_session.execute(text("DELETE FROM agent_knowledge_bases"))
    await async_db_session.execute(text("DELETE FROM knowledge_bases"))
    await async_db_session.commit()


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ─── KB-02 ───────────────────────────────────────────────────────────────────

async def test_upload_creates_pending_doc(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """KB-02: POST a small text/file to a KB → kb_documents row status='pending',
    size_bytes set, 202 Accepted (worker indexes asynchronously).
    """
    await _bind(async_db_session, test_workspace.id, "kb-upload")

    create = await async_client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Upload KB"},
        headers=_auth_headers(valid_supabase_jwt, "kb-upload"),
    )
    assert create.status_code in (200, 201), create.text
    kb_id = create.json()["id"]

    # Upload a small TXT file (multipart — mirrors contacts.py UploadFile pattern).
    blob = b"Hello knowledge base. This is a small test document body."
    upload = await async_client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        files={"file": ("note.txt", blob, "text/plain")},
        headers=_auth_headers(valid_supabase_jwt, "kb-upload"),
    )
    assert upload.status_code == 202, upload.text
    doc = upload.json()
    doc_id = doc["id"]

    # A kb_documents row exists, pending, with size_bytes set to the blob length.
    row = (await async_db_session.execute(text("""
        SELECT status, size_bytes, source_kind, kb_id
        FROM kb_documents WHERE id = :id
    """), {"id": doc_id})).first()
    assert row is not None, "upload did not create a kb_documents row"
    status, size_bytes, source_kind, row_kb_id = row
    assert status == "pending"
    assert int(size_bytes) == len(blob)
    assert str(row_kb_id) == kb_id
    assert source_kind in ("txt", "text")
