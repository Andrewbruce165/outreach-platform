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

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

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


# ─── IMPT-02: confirm endpoint creates a job + N pending items (returns 202) ─────


def _pair_zip(secret_2fa: str) -> bytes:
    """A ZIP with 2 matched .json↔.session pairs + one orphan .json (unpaired)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("+15550000001.json", json.dumps({"twoFA": secret_2fa, "app_version": "6.8.2"}))
        zf.writestr("+15550000001.session", b"\x00fake-sqlite-a")
        zf.writestr("+15550000002.json", json.dumps({"proxy": {"host": "1.2.3.4"}}))
        zf.writestr("+15550000002.session", b"\x00fake-sqlite-b")
        zf.writestr("+15559999999.json", json.dumps({}))  # orphan — not imported
    return buf.getvalue()


async def test_confirm_endpoint_creates_job_and_items(
    async_client, async_db_session, valid_supabase_jwt
):
    """``POST /import/{import_id}/confirm`` re-reads the staged ZIP, creates ONE running job
    (with the batch role) + one pending item per matched pair, and returns ``job_id`` + total
    (202). Unpaired entries are NOT imported and the twoFA value never leaks into the response.
    """
    token = valid_supabase_jwt(sub="imp-confirm", email="imp-confirm@test.com")
    r = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text

    secret_2fa = "confirm-secret-2fa"
    prev = await async_client.post(
        "/api/v1/accounts/import/preview",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("accounts.zip", _pair_zip(secret_2fa), "application/zip")},
    )
    assert prev.status_code == 200, prev.text
    import_id = prev.json()["import_id"]

    resp = await async_client.post(
        f"/api/v1/accounts/import/{import_id}/confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"role": "checker"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    job_id = body["job_id"]
    assert job_id
    assert body["total"] == 2  # only the 2 matched pairs, not the orphan
    assert secret_2fa not in resp.text

    job = (await async_db_session.execute(_t("""
        SELECT role, status, total, processed FROM account_import_jobs WHERE id = :id
    """), {"id": job_id})).mappings().first()
    assert job is not None
    assert job["role"] == "checker"
    assert job["status"] == "running"
    assert int(job["total"]) == 2
    assert int(job["processed"]) == 0

    items = (await async_db_session.execute(_t("""
        SELECT basename, status, octet_length(session_blob) AS blen
        FROM account_import_items WHERE job_id = :job ORDER BY basename
    """), {"job": job_id})).mappings().all()
    assert [i["basename"] for i in items] == ["+15550000001", "+15550000002"]
    assert all(i["status"] == "pending" for i in items)
    # Each item carries its own session bytes so the worker never re-unzips.
    assert all(i["blen"] and i["blen"] > 0 for i in items)


async def test_confirm_expired_staging_returns_410(
    async_client, async_db_session, valid_supabase_jwt
):
    """An expired staging row → 410 ``IMPORT_EXPIRED`` (not a 500), and an unknown id → 404."""
    token = valid_supabase_jwt(sub="imp-expired", email="imp-expired@test.com")
    me = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200, me.text
    ws_id = me.json()["workspace_id"]

    staging_id = str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO account_import_stagings (id, workspace_id, zip_data, summary, expires_at)
        VALUES (:id, :ws, :zip, '{}'::jsonb, :exp)
    """), {
        "id": staging_id, "ws": ws_id, "zip": _pair_zip("x"),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    })
    await async_db_session.commit()

    resp = await async_client.post(
        f"/api/v1/accounts/import/{staging_id}/confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"role": "sender"},
    )
    assert resp.status_code == 410, resp.text
    assert resp.json()["detail"]["code"] == "IMPORT_EXPIRED"

    unknown = await async_client.post(
        f"/api/v1/accounts/import/{uuid.uuid4()}/confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"role": "sender"},
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"]["code"] == "IMPORT_NOT_FOUND"


async def test_status_endpoint_reports_progress(
    async_client, async_db_session, valid_supabase_jwt
):
    """``GET /import/{job_id}/status`` returns processed/total + a secrets-free per-item list.

    Seeds a job with a terminal + a pending item; the status payload exposes only
    basename/status/result/reason (never session bytes or the twoFA value) and an unknown
    job → 404.
    """
    token = valid_supabase_jwt(sub="imp-status", email="imp-status@test.com")
    me = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200, me.text
    ws_id = me.json()["workspace_id"]

    job_id = str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO account_import_jobs (id, workspace_id, role, status, total, processed)
        VALUES (:id, :ws, 'sender', 'running', 2, 1)
    """), {"id": job_id, "ws": ws_id})
    await async_db_session.execute(_t("""
        INSERT INTO account_import_items
            (id, job_id, workspace_id, basename, session_blob, vendor_json, status, result)
        VALUES (:id, :job, :ws, '+15550000001', NULL, '{}'::jsonb, 'ok', 'imported')
    """), {"id": str(uuid.uuid4()), "job": job_id, "ws": ws_id})
    await async_db_session.execute(_t("""
        INSERT INTO account_import_items
            (id, job_id, workspace_id, basename, session_blob, vendor_json, status)
        VALUES (:id, :job, :ws, '+15550000002', :blob, '{"twoFA": "leak-me"}'::jsonb, 'pending')
    """), {"id": str(uuid.uuid4()), "job": job_id, "ws": ws_id, "blob": b"\x00secret-bytes"})
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/accounts/import/{job_id}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "running"
    assert body["total"] == 2
    assert body["processed"] == 1
    by_name = {i["basename"]: i for i in body["items"]}
    assert by_name["+15550000001"]["status"] == "ok"
    assert by_name["+15550000001"]["result"] == "imported"
    assert by_name["+15550000002"]["status"] == "pending"
    # No secret ever appears in the status payload.
    assert "leak-me" not in resp.text
    assert "session_blob" not in resp.text
    assert "vendor_json" not in resp.text

    unknown = await async_client.get(
        f"/api/v1/accounts/import/{uuid.uuid4()}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"]["code"] == "JOB_NOT_FOUND"
