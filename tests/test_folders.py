"""Integration tests для Folders router (FLDR-01, FLDR-02).

Покрывают: CRUD + 409 FOLDER_NOT_EMPTY + ?force=true cascade +
cross-tenant isolation + get_or_create_by_name helper (FLDR-03 prep).
"""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


async def test_list_folders_no_auth_returns_401(async_client):
    response = await async_client.get("/api/v1/folders")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


async def test_create_folder_returns_201_with_contact_count_0(
    async_client, valid_supabase_jwt
):
    token = valid_supabase_jwt(
        sub=f"folders-test-1-{uuid4()}", email=f"a-{uuid4()}@x.com"
    )
    response = await async_client.post(
        "/api/v1/folders",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Leads"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Leads"
    assert body["contact_count"] == 0
    assert "id" in body


async def test_list_folders_returns_workspace_folders(
    async_client, valid_supabase_jwt
):
    token = valid_supabase_jwt(
        sub=f"folders-test-2-{uuid4()}", email=f"b-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    await async_client.post("/api/v1/folders", headers=headers, json={"name": "F1"})
    await async_client.post("/api/v1/folders", headers=headers, json={"name": "F2"})
    response = await async_client.get("/api/v1/folders", headers=headers)
    assert response.status_code == 200
    names = [f["name"] for f in response.json()]
    assert "F1" in names and "F2" in names


async def test_create_duplicate_name_returns_409(
    async_client, valid_supabase_jwt
):
    token = valid_supabase_jwt(
        sub=f"folders-test-3-{uuid4()}", email=f"c-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    await async_client.post("/api/v1/folders", headers=headers, json={"name": "Dup"})
    response = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "Dup"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FOLDER_NAME_DUPLICATE"


async def test_patch_folder_renames(async_client, valid_supabase_jwt):
    token = valid_supabase_jwt(
        sub=f"folders-test-4-{uuid4()}", email=f"d-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    create = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "Old"}
    )
    fid = create.json()["id"]
    response = await async_client.patch(
        f"/api/v1/folders/{fid}", headers=headers, json={"name": "New"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New"


async def test_delete_empty_folder_returns_204(
    async_client, valid_supabase_jwt
):
    token = valid_supabase_jwt(
        sub=f"folders-test-5-{uuid4()}", email=f"e-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    create = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "ToDel"}
    )
    fid = create.json()["id"]
    response = await async_client.delete(f"/api/v1/folders/{fid}", headers=headers)
    assert response.status_code == 204
    get_response = await async_client.get(f"/api/v1/folders/{fid}", headers=headers)
    assert get_response.status_code == 404


async def test_delete_folder_with_contacts_returns_409(
    async_client, valid_supabase_jwt, async_db_session
):
    """Используем raw SQL для добавления контактов в обход API (плана 02-04 нет)."""
    from sqlalchemy import text

    token = valid_supabase_jwt(
        sub=f"folders-test-6-{uuid4()}", email=f"f-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    create = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "Full"}
    )
    fid = create.json()["id"]
    ws = await async_client.get("/api/v1/workspace", headers=headers)
    wid = ws.json()["id"]
    await async_db_session.execute(
        text(
            """
            INSERT INTO contacts (workspace_id, folder_id, phone, tg_status)
            VALUES (:wid, :fid, :p1, 'pending'), (:wid, :fid, :p2, 'pending')
            """
        ),
        {
            "wid": wid,
            "fid": fid,
            "p1": "+79001110001",
            "p2": "+79001110002",
        },
    )
    await async_db_session.commit()

    response = await async_client.delete(f"/api/v1/folders/{fid}", headers=headers)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "FOLDER_NOT_EMPTY"
    assert detail["contact_count"] == 2
    assert detail["active_campaigns"] == []


async def test_delete_folder_force_cascades_contacts(
    async_client, valid_supabase_jwt, async_db_session
):
    from sqlalchemy import text

    token = valid_supabase_jwt(
        sub=f"folders-test-7-{uuid4()}", email=f"g-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    create = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "ForceDel"}
    )
    fid = create.json()["id"]
    ws = await async_client.get("/api/v1/workspace", headers=headers)
    wid = ws.json()["id"]
    await async_db_session.execute(
        text(
            """
            INSERT INTO contacts (workspace_id, folder_id, phone, tg_status)
            VALUES (:wid, :fid, :p, 'pending')
            """
        ),
        {"wid": wid, "fid": fid, "p": "+79002220001"},
    )
    await async_db_session.commit()

    response = await async_client.delete(
        f"/api/v1/folders/{fid}?force=true", headers=headers
    )
    assert response.status_code == 204

    # Контакты тоже удалены каскадом через FK ondelete=CASCADE.
    rows = await async_db_session.execute(
        text("SELECT COUNT(*) FROM contacts WHERE folder_id = :fid"),
        {"fid": fid},
    )
    assert rows.scalar() == 0


async def test_cross_tenant_folder_returns_404(async_client, valid_supabase_jwt):
    # Workspace A creates a folder
    token_a = valid_supabase_jwt(
        sub=f"folders-tenant-a-{uuid4()}", email=f"a-{uuid4()}@x.com"
    )
    ha = {"Authorization": f"Bearer {token_a}"}
    create = await async_client.post(
        "/api/v1/folders", headers=ha, json={"name": "PrivateA"}
    )
    fid = create.json()["id"]

    # Workspace B tries to read it → 404 (security: hide cross-tenant existence)
    token_b = valid_supabase_jwt(
        sub=f"folders-tenant-b-{uuid4()}", email=f"b-{uuid4()}@x.com"
    )
    hb = {"Authorization": f"Bearer {token_b}"}
    response = await async_client.get(f"/api/v1/folders/{fid}", headers=hb)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "FOLDER_NOT_FOUND"


async def test_get_or_create_by_name_helper(async_db_session, test_workspace):
    """Helper для FLDR-03 (auto-create) — будет использован в plan 02-04."""
    from app.routers.folders import get_or_create_by_name

    # Первый вызов — создаёт
    fid1 = await get_or_create_by_name(
        async_db_session, test_workspace.id, "AutoFolder"
    )
    await async_db_session.commit()

    # Второй вызов — возвращает тот же ID (idempotent через ON CONFLICT)
    fid2 = await get_or_create_by_name(
        async_db_session, test_workspace.id, "AutoFolder"
    )
    await async_db_session.commit()

    assert fid1 == fid2
