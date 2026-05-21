"""Integration-тесты POST /contacts/recheck + has_checker в GET /workspace.

Покрывают:
- recheck по contact_ids → 202 + marked_pending; контакты переходят в pending
- recheck по folder_id → 202 + marked_pending; все контакты папки → pending
- recheck с пустым payload → 422 (Pydantic model_validator)
- recheck cross-tenant: workspace B не может recheck контакты workspace A → marked_pending=0
- recheck с несуществующим folder_id → 404 FOLDER_NOT_FOUND
- GET /workspace без checker → has_checker=false
- GET /workspace с активным checker → has_checker=true
- GET /workspace с checker auth_status='session_expired' → has_checker=false
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _setup_workspace(async_client, valid_supabase_jwt, suffix: str):
    """Создаёт unique workspace через JWT и возвращает (headers, workspace_id)."""
    token = valid_supabase_jwt(
        sub=f"recheck-{suffix}-{uuid4()}", email=f"{suffix}-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    ws = await async_client.get("/api/v1/workspace", headers=headers)
    assert ws.status_code == 200, ws.text
    return headers, ws.json()["id"]


# ─── POST /api/v1/contacts/recheck — happy paths ─────────────────────────────


async def test_recheck_by_contact_ids_marks_them_pending(
    async_client, valid_supabase_jwt, async_db_session
):
    """recheck с contact_ids → 202 + marked_pending=2 + tg_status='pending' в БД."""
    headers, wid = await _setup_workspace(async_client, valid_supabase_jwt, "ids")
    # Создаём папку через API
    folder_resp = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": f"RecheckFolder-{uuid4()}"}
    )
    assert folder_resp.status_code == 201, folder_resp.text
    fid = folder_resp.json()["id"]

    # Insert два уже проверенных контакта (status='registered')
    await async_db_session.execute(
        text(
            """
            INSERT INTO contacts (workspace_id, folder_id, phone, tg_status, tg_telegram_id)
            VALUES (:wid, :fid, :p1, 'registered', 111),
                   (:wid, :fid, :p2, 'registered', 222)
            """
        ),
        {
            "wid": wid,
            "fid": fid,
            "p1": f"+7900{uuid4().int % 10000000:07d}",
            "p2": f"+7901{uuid4().int % 10000000:07d}",
        },
    )
    await async_db_session.commit()

    ids_result = await async_db_session.execute(
        text(
            "SELECT id::text FROM contacts WHERE folder_id = :fid ORDER BY phone"
        ),
        {"fid": fid},
    )
    contact_ids = [r[0] for r in ids_result.fetchall()]
    assert len(contact_ids) == 2

    response = await async_client.post(
        "/api/v1/contacts/recheck",
        headers=headers,
        json={"contact_ids": contact_ids},
    )
    assert response.status_code == 202, response.text
    assert response.json()["marked_pending"] == 2

    # Проверка БД — оба теперь pending, tg_error очищен.
    statuses = await async_db_session.execute(
        text(
            "SELECT tg_status, tg_error FROM contacts WHERE id::text = ANY(:ids)"
        ),
        {"ids": contact_ids},
    )
    rows = statuses.fetchall()
    assert all(r.tg_status == "pending" for r in rows)
    assert all(r.tg_error is None for r in rows)


async def test_recheck_by_folder_id_marks_all_folder_contacts_pending(
    async_client, valid_supabase_jwt, async_db_session
):
    """recheck с folder_id → все контакты папки → pending."""
    headers, wid = await _setup_workspace(async_client, valid_supabase_jwt, "fld")
    folder_resp = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": f"FldRe-{uuid4()}"}
    )
    fid = folder_resp.json()["id"]

    await async_db_session.execute(
        text(
            """
            INSERT INTO contacts (workspace_id, folder_id, phone, tg_status)
            VALUES (:wid, :fid, :p1, 'not_registered'),
                   (:wid, :fid, :p2, 'error')
            """
        ),
        {
            "wid": wid,
            "fid": fid,
            "p1": f"+7902{uuid4().int % 10000000:07d}",
            "p2": f"+7903{uuid4().int % 10000000:07d}",
        },
    )
    await async_db_session.commit()

    response = await async_client.post(
        "/api/v1/contacts/recheck",
        headers=headers,
        json={"folder_id": fid},
    )
    assert response.status_code == 202, response.text
    assert response.json()["marked_pending"] == 2

    statuses = await async_db_session.execute(
        text("SELECT tg_status FROM contacts WHERE folder_id = :fid"),
        {"fid": fid},
    )
    assert all(r.tg_status == "pending" for r in statuses.fetchall())


# ─── POST /api/v1/contacts/recheck — error paths ─────────────────────────────


async def test_recheck_missing_target_returns_422(
    async_client, valid_supabase_jwt
):
    """Empty payload → 422 (Pydantic model_validator)."""
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "miss")
    response = await async_client.post(
        "/api/v1/contacts/recheck",
        headers=headers,
        json={},
    )
    assert response.status_code == 422


async def test_recheck_nonexistent_folder_returns_404(
    async_client, valid_supabase_jwt
):
    """recheck с folder_id, не принадлежащим workspace, → 404 FOLDER_NOT_FOUND."""
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "no-fld")
    response = await async_client.post(
        "/api/v1/contacts/recheck",
        headers=headers,
        json={"folder_id": str(uuid4())},
    )
    assert response.status_code == 404
    body = response.json()
    # FastAPI оборачивает HTTPException(detail=...) в {"detail": ...}
    assert body["detail"]["code"] == "FOLDER_NOT_FOUND"


async def test_recheck_cross_tenant_marks_zero(
    async_client, valid_supabase_jwt, async_db_session
):
    """Workspace B шлёт contact_ids из workspace A → marked_pending=0; статус не меняется."""
    headers_a, wid_a = await _setup_workspace(
        async_client, valid_supabase_jwt, "a"
    )
    folder_a = await async_client.post(
        "/api/v1/folders", headers=headers_a, json={"name": f"TenantA-{uuid4()}"}
    )
    fid_a = folder_a.json()["id"]

    ins = await async_db_session.execute(
        text(
            """
            INSERT INTO contacts (workspace_id, folder_id, phone, tg_status)
            VALUES (:wid, :fid, :p, 'registered')
            RETURNING id::text
            """
        ),
        {
            "wid": wid_a,
            "fid": fid_a,
            "p": f"+7909{uuid4().int % 10000000:07d}",
        },
    )
    contact_id = ins.scalar()
    await async_db_session.commit()

    # Workspace B пытается recheck.
    headers_b, _ = await _setup_workspace(async_client, valid_supabase_jwt, "b")
    response = await async_client.post(
        "/api/v1/contacts/recheck",
        headers=headers_b,
        json={"contact_ids": [contact_id]},
    )
    assert response.status_code == 202
    assert response.json()["marked_pending"] == 0

    # Контакт в A остался 'registered'.
    status = await async_db_session.execute(
        text("SELECT tg_status FROM contacts WHERE id::text = :cid"),
        {"cid": contact_id},
    )
    assert status.scalar() == "registered"


# ─── GET /api/v1/workspace — has_checker exposure ────────────────────────────


async def test_workspace_has_checker_false_when_no_checker(
    async_client, valid_supabase_jwt
):
    """GET /workspace без checker'а → has_checker=false."""
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "hc-false"
    )
    response = await async_client.get("/api/v1/workspace", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert "has_checker" in body
    assert body["has_checker"] is False


async def test_workspace_has_checker_true_with_active_checker(
    async_client, valid_supabase_jwt, async_db_session
):
    """GET /workspace с sender role='checker' AND auth_status='ok' → has_checker=true."""
    headers, wid = await _setup_workspace(
        async_client, valid_supabase_jwt, "hc-true"
    )
    # Прямой INSERT — обходим API, проверяем только запрос has_checker.
    await async_db_session.execute(
        text(
            """
            INSERT INTO senders (workspace_id, slug, name, phone, session_string,
                                 role, auth_status, lifecycle_status,
                                 rate_per_min, rate_per_hour, rate_per_day)
            VALUES (:wid, :slug, 'Checker 1', '+79001234500', 'enc', 'checker',
                    'ok', 'active', 4, 20, 150)
            """
        ),
        {"wid": wid, "slug": f"ch-true-{uuid4()}"},
    )
    await async_db_session.commit()

    response = await async_client.get("/api/v1/workspace", headers=headers)
    assert response.status_code == 200
    assert response.json()["has_checker"] is True


async def test_workspace_has_checker_false_when_checker_auth_expired(
    async_client, valid_supabase_jwt, async_db_session
):
    """Checker с auth_status='session_expired' не считается активным."""
    headers, wid = await _setup_workspace(
        async_client, valid_supabase_jwt, "hc-exp"
    )
    await async_db_session.execute(
        text(
            """
            INSERT INTO senders (workspace_id, slug, name, phone, session_string,
                                 role, auth_status, lifecycle_status,
                                 rate_per_min, rate_per_hour, rate_per_day)
            VALUES (:wid, :slug, 'Broken Checker', '+79001234501', 'enc', 'checker',
                    'session_expired', 'active', 4, 20, 150)
            """
        ),
        {"wid": wid, "slug": f"ch-exp-{uuid4()}"},
    )
    await async_db_session.commit()

    response = await async_client.get("/api/v1/workspace", headers=headers)
    assert response.status_code == 200
    assert response.json()["has_checker"] is False
