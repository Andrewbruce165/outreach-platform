"""Phase 21 — AccountImportWorker tick + status reporting (Wave 0 RED).

Covers IMPT-02: the confirm endpoint creates a job + per-item rows and an async worker
drives each item pending → processing → ok/failed, while the job row reports
processed/total (the surface the status poll reads).

Mirrors tests/test_kb_ingest_worker.py: seed rows directly, monkeypatch the per-account
import routine (``app.services.account_import.import_one_account`` — a MODULE attribute, so
the worker must call it as ``account_import.import_one_account(...)`` for the patch to bite)
to a deterministic stub, run one ``_tick`` per pending item, then assert terminal state.

RED until Plan 21-05 lands ``app.services.account_import_worker.AccountImportWorker`` (and
21-04 lands ``app.services.account_import``): the deferred in-body imports raise ImportError.
No real Telegram network — the import routine is fully stubbed.
"""

import uuid

import pytest
from sqlalchemy import text as _t

pytestmark = pytest.mark.asyncio


async def _seed_job_with_items(db, ws_id, basenames):
    """Insert a running job + N pending items; return (job_id, {basename: item_id})."""
    job_id = str(uuid.uuid4())
    await db.execute(_t("""
        INSERT INTO account_import_jobs (id, workspace_id, role, status, total)
        VALUES (:id, :ws, 'sender', 'running', :total)
    """), {"id": job_id, "ws": str(ws_id), "total": len(basenames)})
    ids = {}
    for basename in basenames:
        item_id = str(uuid.uuid4())
        await db.execute(_t("""
            INSERT INTO account_import_items
                (id, job_id, workspace_id, basename, session_blob, vendor_json, status)
            VALUES (:id, :job, :ws, :bn, :blob, '{}'::jsonb, 'pending')
        """), {"id": item_id, "job": job_id, "ws": str(ws_id),
               "bn": basename, "blob": b"\x00sqlite"})
        ids[basename] = item_id
    await db.commit()
    return job_id, ids


# ─── IMPT-02: worker drives items pending→processing→ok/failed + job progress ───

async def test_worker_drives_items_and_status(async_db_session, test_workspace, monkeypatch):
    """Seed a job + 2 pending items; a deterministic stubbed import routine marks one 'ok'
    and one 'failed'. One ``_tick`` per item drives them to terminal state and the job row
    reports processed==total and flips status → 'done'."""
    import app.services.account_import as ai_mod  # RED until 21-04
    from app.services.account_import_worker import AccountImportWorker  # RED until 21-05

    job_id, ids = await _seed_job_with_items(
        async_db_session, test_workspace.id, ["+15550000001", "+15550000002bad"]
    )

    async def _fake_import(db, item):
        """Deterministic per-item outcome by basename. Mutates the worker's item row +
        returns the result; the worker owns commit + processed bump + job completion."""
        basename = item["basename"] if isinstance(item, dict) else item.basename
        if basename.endswith("bad"):
            result = "auth_failed"
        else:
            result = "imported"
        return result

    from unittest.mock import AsyncMock
    monkeypatch.setattr(ai_mod, "import_one_account", AsyncMock(side_effect=_fake_import))

    worker = AccountImportWorker()
    # One tick per pending item (worker claims one FOR UPDATE SKIP LOCKED per tick).
    await worker._tick()
    await worker._tick()

    rows = (await async_db_session.execute(_t("""
        SELECT basename, status, result FROM account_import_items WHERE job_id = :job
    """), {"job": job_id})).mappings().all()
    by_name = {r["basename"]: r for r in rows}

    assert by_name["+15550000001"]["status"] == "ok"
    assert by_name["+15550000002bad"]["status"] == "failed"
    for r in rows:
        assert r["status"] in ("ok", "failed"), f"{r['basename']} still {r['status']}"

    job = (await async_db_session.execute(_t("""
        SELECT status, total, processed FROM account_import_jobs WHERE id = :id
    """), {"id": job_id})).mappings().first()
    assert int(job["processed"]) == int(job["total"]) == 2
    assert job["status"] == "done"
