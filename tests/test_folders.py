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


async def test_rename_folder_with_contacts_is_never_blocked(
    async_client, valid_supabase_jwt, async_db_session
):
    """Regression (debug folder-delete-blocked-contacts): renaming a NON-EMPTY folder
    must succeed. Contained contacts gate DELETE only — never PATCH.
    """
    from sqlalchemy import text

    token = valid_supabase_jwt(
        sub=f"folders-rename-full-{uuid4()}", email=f"rf-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    create = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "RenameMeFull"}
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
        {"wid": wid, "fid": fid, "p1": "+79004440001", "p2": "+79004440002"},
    )
    await async_db_session.commit()

    response = await async_client.patch(
        f"/api/v1/folders/{fid}", headers=headers, json={"name": "RenamedFull"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "RenamedFull"
    # Rename must not touch the contacts.
    assert body["contact_count"] == 2


async def test_delete_folder_used_by_draft_campaign_returns_409_not_500(
    async_client, valid_supabase_jwt, async_db_session
):
    """Regression: campaigns.folder_id is FK ON DELETE RESTRICT, so ANY referencing
    campaign blocks the delete. The guard used to filter status='running' only, so a
    draft campaign slipped past it and raised an unhandled 500 IntegrityError.
    force=true must NOT override this (contacts cascade; campaigns restrict).
    """
    from sqlalchemy import text

    token = valid_supabase_jwt(
        sub=f"folders-draft-camp-{uuid4()}", email=f"dc-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    create = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "UsedByDraft"}
    )
    fid = create.json()["id"]
    ws = await async_client.get("/api/v1/workspace", headers=headers)
    wid = ws.json()["id"]
    await async_db_session.execute(
        text(
            """
            INSERT INTO campaigns (workspace_id, folder_id, name, status)
            VALUES (:wid, :fid, 'DraftCamp', 'draft')
            """
        ),
        {"wid": wid, "fid": fid},
    )
    await async_db_session.commit()

    response = await async_client.delete(
        f"/api/v1/folders/{fid}?force=true", headers=headers
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "FOLDER_USED_BY_RUNNING_CAMPAIGN"
    assert [c["name"] for c in detail["campaigns"]] == ["DraftCamp"]
    assert detail["campaigns"][0]["status"] == "draft"


async def test_delete_folder_campaign_guard_is_workspace_scoped(
    async_client, valid_supabase_jwt, async_db_session
):
    """A campaign in ANOTHER workspace must never appear in the guard's payload
    (multi-tenant leak check on the new SQL).
    """
    from sqlalchemy import text

    token_a = valid_supabase_jwt(
        sub=f"folders-scope-a-{uuid4()}", email=f"pa-{uuid4()}@x.com"
    )
    ha = {"Authorization": f"Bearer {token_a}"}
    create = await async_client.post(
        "/api/v1/folders", headers=ha, json={"name": "ScopedFolder"}
    )
    fid = create.json()["id"]

    # Workspace B exists but owns no campaign on this folder.
    token_b = valid_supabase_jwt(
        sub=f"folders-scope-b-{uuid4()}", email=f"pb-{uuid4()}@x.com"
    )
    hb = {"Authorization": f"Bearer {token_b}"}
    ws_b = await async_client.get("/api/v1/workspace", headers=hb)
    wid_b = ws_b.json()["id"]

    # Workspace B's campaign points at its OWN folder, not A's.
    create_b = await async_client.post(
        "/api/v1/folders", headers=hb, json={"name": "BFolder"}
    )
    fid_b = create_b.json()["id"]
    await async_db_session.execute(
        text(
            """
            INSERT INTO campaigns (workspace_id, folder_id, name, status)
            VALUES (:wid, :fid, 'BCamp', 'running')
            """
        ),
        {"wid": wid_b, "fid": fid_b},
    )
    await async_db_session.commit()

    # A's folder has no campaigns of its own → delete proceeds.
    response = await async_client.delete(f"/api/v1/folders/{fid}", headers=ha)
    assert response.status_code == 204


async def test_folder_stats_breakdown(
    async_client, valid_supabase_jwt, async_db_session
):
    """Server-side stat breakdown buckets tg_status into in_telegram/checking/not_found.

    Fixes the /contacts flash-then-correct bug: cards now read a single GROUP BY
    aggregate instead of deriving counts from the first paginated page.
    """
    from sqlalchemy import text

    token = valid_supabase_jwt(
        sub=f"folders-stats-{uuid4()}", email=f"st-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    create = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "StatsFolder"}
    )
    fid = create.json()["id"]
    ws = await async_client.get("/api/v1/workspace", headers=headers)
    wid = ws.json()["id"]

    # 2 registered (in_telegram), 3 pending (checking), 1 not_registered (not_found)
    await async_db_session.execute(
        text(
            """
            INSERT INTO contacts (workspace_id, folder_id, phone, tg_status) VALUES
              (:wid, :fid, :p1, 'registered'),
              (:wid, :fid, :p2, 'registered'),
              (:wid, :fid, :p3, 'pending'),
              (:wid, :fid, :p4, 'pending'),
              (:wid, :fid, :p5, 'pending'),
              (:wid, :fid, :p6, 'not_registered')
            """
        ),
        {
            "wid": wid,
            "fid": fid,
            "p1": "+79003330001",
            "p2": "+79003330002",
            "p3": "+79003330003",
            "p4": "+79003330004",
            "p5": "+79003330005",
            "p6": "+79003330006",
        },
    )
    await async_db_session.commit()

    response = await async_client.get(
        f"/api/v1/folders/{fid}/stats", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 6
    assert body["in_telegram"] == 2
    assert body["checking"] == 3
    assert body["not_found"] == 1
    # Buckets partition the total exactly.
    assert body["in_telegram"] + body["checking"] + body["not_found"] == body["total"]


async def test_folder_stats_empty_folder_all_zero(async_client, valid_supabase_jwt):
    token = valid_supabase_jwt(
        sub=f"folders-stats-empty-{uuid4()}", email=f"se-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    create = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "EmptyStats"}
    )
    fid = create.json()["id"]
    response = await async_client.get(
        f"/api/v1/folders/{fid}/stats", headers=headers
    )
    assert response.status_code == 200
    assert response.json() == {
        "total": 0,
        "in_telegram": 0,
        "checking": 0,
        "not_found": 0,
    }


async def test_folder_stats_cross_tenant_returns_404(
    async_client, valid_supabase_jwt
):
    token_a = valid_supabase_jwt(
        sub=f"folders-stats-a-{uuid4()}", email=f"sa-{uuid4()}@x.com"
    )
    ha = {"Authorization": f"Bearer {token_a}"}
    create = await async_client.post(
        "/api/v1/folders", headers=ha, json={"name": "PrivateStats"}
    )
    fid = create.json()["id"]

    token_b = valid_supabase_jwt(
        sub=f"folders-stats-b-{uuid4()}", email=f"sb-{uuid4()}@x.com"
    )
    hb = {"Authorization": f"Bearer {token_b}"}
    response = await async_client.get(f"/api/v1/folders/{fid}/stats", headers=hb)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "FOLDER_NOT_FOUND"


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
