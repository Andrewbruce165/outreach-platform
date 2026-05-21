"""Integration tests для Contacts router (CONT-01, CONT-02, CONT-03, CONT-05, FLDR-03).

Покрывают:
- list / get (workspace-scoped)
- push single + batch (D-10, с auth через JWT и X-Workspace-Key)
- dedup через ON CONFLICT (D-02, D-03)
- folder_name auto-create (FLDR-03)
- CSV preview + import 2-step flow (D-07)
- D-20: tg_status='unchecked' если нет checker'а, 'pending' если есть
- move single + batch (D-04)
- cross-tenant isolation (404 без раскрытия "not yours")
- batch >1000 → 422
"""

import io
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


async def _setup_workspace(async_client, valid_supabase_jwt, suffix: str):
    """Создаёт unique workspace через JWT и возвращает (headers, workspace_id)."""
    token = valid_supabase_jwt(
        sub=f"contacts-{suffix}-{uuid4()}", email=f"{suffix}-{uuid4()}@x.com"
    )
    headers = {"Authorization": f"Bearer {token}"}
    ws = await async_client.get("/api/v1/workspace", headers=headers)
    return headers, ws.json()["id"]


async def _create_folder(async_client, headers, name: str) -> str:
    """Создаёт папку, возвращает folder_id."""
    response = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": name}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ─── Push (D-10) ─────────────────────────────────────────────────────────────


async def test_push_single_with_folder_name_returns_summary(
    async_client, valid_supabase_jwt
):
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "push-single")
    response = await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={
            "phone": "+79001234567",
            "full_name": "Test Person",
            "folder_name": "Pushed",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["imported"] == 1
    assert body["skipped_duplicates"] == 0
    assert body["skipped_invalid"] == 0


async def test_push_batch_returns_summary(async_client, valid_supabase_jwt):
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "push-batch")
    response = await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={
            "contacts": [
                {"phone": "+79001110001", "folder_name": "Batch"},
                {"phone": "+79001110002", "folder_name": "Batch"},
                {"phone": "+79001110003", "folder_name": "Batch"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["imported"] == 3


async def test_push_dedup_within_batch(async_client, valid_supabase_jwt):
    """D-02/D-03: два одинаковых phone в batch → second skipped через ON CONFLICT."""
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "push-dedup")
    response = await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={
            "contacts": [
                {"phone": "+79009990001", "folder_name": "Dedup"},
                {"phone": "+79009990001", "folder_name": "Dedup"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 1
    assert body["skipped_duplicates"] == 1


async def test_push_folder_name_auto_create(async_client, valid_supabase_jwt):
    """FLDR-03: folder_name без folder_id → папка создаётся auto."""
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "push-auto-folder"
    )
    response = await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"phone": "+79007770001", "folder_name": "NewFolder"},
    )
    assert response.status_code == 200, response.text
    # Папка появилась в списке.
    folders = await async_client.get("/api/v1/folders", headers=headers)
    names = [f["name"] for f in folders.json()]
    assert "NewFolder" in names


async def test_push_batch_over_1000_returns_422(async_client, valid_supabase_jwt):
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "push-1001")
    contacts = [
        {"phone": f"+790{i:09d}", "folder_name": "Big"} for i in range(1001)
    ]
    response = await async_client.post(
        "/api/v1/contacts", headers=headers, json={"contacts": contacts}
    )
    assert response.status_code == 422


async def test_push_without_phone_or_username_returns_422(
    async_client, valid_supabase_jwt
):
    """Pydantic model_validator → 422 (контакт без phone и без username невалиден)."""
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "push-no-id"
    )
    response = await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"full_name": "X", "folder_name": "Test"},
    )
    assert response.status_code == 422


async def test_push_via_workspace_api_key(async_client, valid_supabase_jwt):
    """D-10: push через X-Workspace-Key (без JWT) — n8n flow."""
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "push-wsk")
    # Создаём API key
    create_key = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "n8n-key"},
    )
    assert create_key.status_code == 201, create_key.text
    wsk = create_key.json()["token"]

    # Push через API key
    response = await async_client.post(
        "/api/v1/contacts",
        headers={"X-Workspace-Key": wsk},
        json={"phone": "+79006661111", "folder_name": "FromN8n"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1


# ─── CSV import (D-07) ────────────────────────────────────────────────────────


async def test_csv_preview_returns_columns_and_import_id(
    async_client, valid_supabase_jwt
):
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "csv-preview")
    csv_body = b"phone,name\n+79001234567,John\n+79001234568,Jane\n"
    files = {"file": ("test.csv", io.BytesIO(csv_body), "text/csv")}
    response = await async_client.post(
        "/api/v1/contacts/import/preview", headers=headers, files=files
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == ["phone", "name"]
    assert len(body["sample_rows"]) == 2
    assert body["suggested_mapping"]["0"] == "phone"
    assert "import_id" in body


async def test_csv_import_applies_mapping(async_client, valid_supabase_jwt):
    """D-07 full flow: preview → import → contacts inserted."""
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "csv-import")
    csv_body = b"phone,name\n+79002220001,John\n89002220002,Jane\n"  # 8 → +7
    files = {"file": ("test.csv", io.BytesIO(csv_body), "text/csv")}
    preview = await async_client.post(
        "/api/v1/contacts/import/preview", headers=headers, files=files
    )
    assert preview.status_code == 200, preview.text
    import_id = preview.json()["import_id"]

    response = await async_client.post(
        "/api/v1/contacts/import",
        headers=headers,
        json={
            "import_id": import_id,
            "folder_name": "CsvBatch",
            "mapping": {"0": "phone", "1": "full_name"},
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["imported"] == 2

    # Контакты появились в списке.
    contacts = await async_client.get("/api/v1/contacts", headers=headers)
    phones = [c["phone"] for c in contacts.json()]
    assert "+79002220001" in phones
    assert "+79002220002" in phones  # leading-8 нормализован в +7


async def test_csv_import_mapping_without_phone_or_username_returns_422(
    async_client, valid_supabase_jwt
):
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "csv-bad-mapping"
    )
    csv_body = b"name,city\nJohn,Moscow\n"
    files = {"file": ("test.csv", io.BytesIO(csv_body), "text/csv")}
    preview = await async_client.post(
        "/api/v1/contacts/import/preview", headers=headers, files=files
    )
    import_id = preview.json()["import_id"]

    response = await async_client.post(
        "/api/v1/contacts/import",
        headers=headers,
        json={
            "import_id": import_id,
            "folder_name": "Bad",
            "mapping": {"0": "full_name", "1": "custom.city"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MAPPING_INVALID"


async def test_csv_import_unknown_import_id_returns_404(
    async_client, valid_supabase_jwt
):
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "csv-not-found"
    )
    response = await async_client.post(
        "/api/v1/contacts/import",
        headers=headers,
        json={
            "import_id": str(uuid4()),
            "folder_name": "X",
            "mapping": {"0": "phone"},
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "IMPORT_NOT_FOUND"


# ─── List + filters ──────────────────────────────────────────────────────────


async def test_list_contacts_filter_by_folder(async_client, valid_supabase_jwt):
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "list-folder"
    )
    f1 = await _create_folder(async_client, headers, "F1")
    f2 = await _create_folder(async_client, headers, "F2")
    await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"phone": "+79008880001", "folder_id": f1},
    )
    await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"phone": "+79008880002", "folder_id": f2},
    )
    response = await async_client.get(
        f"/api/v1/contacts?folder_id={f1}", headers=headers
    )
    assert response.status_code == 200
    phones = [c["phone"] for c in response.json()]
    assert "+79008880001" in phones
    assert "+79008880002" not in phones


async def test_list_contacts_filter_by_tg_status(async_client, valid_supabase_jwt):
    """D-20: без checker'а → tg_status='unchecked'."""
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "list-status"
    )
    await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"phone": "+79005550001", "folder_name": "S"},
    )
    response = await async_client.get(
        "/api/v1/contacts?tg_status=unchecked", headers=headers
    )
    assert response.status_code == 200
    assert any(c["phone"] == "+79005550001" for c in response.json())


# ─── D-20 has_checker ────────────────────────────────────────────────────────


async def test_import_without_checker_sets_unchecked(
    async_client, valid_supabase_jwt
):
    """D-20: если в workspace нет checker'а — контакты получают tg_status='unchecked'."""
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "no-checker"
    )
    await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"phone": "+79004440001", "folder_name": "NoChecker"},
    )
    contacts = await async_client.get("/api/v1/contacts", headers=headers)
    found = [c for c in contacts.json() if c["phone"] == "+79004440001"]
    assert len(found) == 1
    assert found[0]["tg_status"] == "unchecked"


async def test_import_with_checker_sets_pending(
    async_client, valid_supabase_jwt, async_db_session
):
    """D-20 vs D-19: если есть checker — контакты в 'pending' (worker подберёт)."""
    from sqlalchemy import text

    headers, ws_id = await _setup_workspace(
        async_client, valid_supabase_jwt, "with-checker"
    )
    # Создаём checker через raw SQL (онбординг отдельный поток в этом workspace).
    checker_slug = f"chk-{uuid4().hex[:8]}"
    await async_db_session.execute(
        text(
            """
            INSERT INTO senders
                (workspace_id, slug, name, phone, session_string, role,
                 auth_status, lifecycle_status, rate_per_min, rate_per_hour, rate_per_day)
            VALUES (:wid, :slug, 'Chk', '+79009998877', 'enc_stub', 'checker',
                    'ok', 'active', 4, 20, 150)
            """
        ),
        {"wid": ws_id, "slug": checker_slug},
    )
    await async_db_session.commit()

    await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"phone": "+79003330001", "folder_name": "WithChecker"},
    )
    contacts = await async_client.get("/api/v1/contacts", headers=headers)
    found = [c for c in contacts.json() if c["phone"] == "+79003330001"]
    assert len(found) == 1
    assert found[0]["tg_status"] == "pending"


# ─── Move (D-04) ─────────────────────────────────────────────────────────────


async def test_move_single_contact(async_client, valid_supabase_jwt):
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "move-single"
    )
    f1 = await _create_folder(async_client, headers, "From")
    f2 = await _create_folder(async_client, headers, "To")
    push = await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"phone": "+79002001000", "folder_id": f1},
    )
    assert push.status_code == 200, push.text
    contacts = await async_client.get(
        f"/api/v1/contacts?folder_id={f1}", headers=headers
    )
    cid = contacts.json()[0]["id"]

    response = await async_client.post(
        f"/api/v1/contacts/{cid}/move", headers=headers, json={"folder_id": f2}
    )
    assert response.status_code == 200, response.text
    assert response.json()["folder_id"] == f2


async def test_move_batch(async_client, valid_supabase_jwt):
    headers, _ = await _setup_workspace(async_client, valid_supabase_jwt, "move-batch")
    f1 = await _create_folder(async_client, headers, "F1")
    f2 = await _create_folder(async_client, headers, "F2")
    await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={
            "contacts": [
                {"phone": "+79002201001", "folder_id": f1},
                {"phone": "+79002201002", "folder_id": f1},
            ]
        },
    )
    contacts = await async_client.get(
        f"/api/v1/contacts?folder_id={f1}", headers=headers
    )
    ids = [c["id"] for c in contacts.json()]
    response = await async_client.post(
        "/api/v1/contacts/move",
        headers=headers,
        json={"contact_ids": ids, "folder_id": f2},
    )
    assert response.status_code == 200, response.text
    assert response.json()["moved"] == 2


# ─── Cross-tenant isolation ──────────────────────────────────────────────────


async def test_cross_tenant_delete_returns_404(async_client, valid_supabase_jwt):
    headers_a, _ = await _setup_workspace(async_client, valid_supabase_jwt, "tenant-a")
    push = await async_client.post(
        "/api/v1/contacts",
        headers=headers_a,
        json={"phone": "+79001112222", "folder_name": "PrivateA"},
    )
    assert push.status_code == 200, push.text
    contacts = await async_client.get("/api/v1/contacts", headers=headers_a)
    cid = contacts.json()[0]["id"]

    headers_b, _ = await _setup_workspace(async_client, valid_supabase_jwt, "tenant-b")
    response = await async_client.delete(f"/api/v1/contacts/{cid}", headers=headers_b)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CONTACT_NOT_FOUND"


async def test_delete_own_contact(async_client, valid_supabase_jwt):
    headers, _ = await _setup_workspace(
        async_client, valid_supabase_jwt, "delete-own"
    )
    await async_client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"phone": "+79009990099", "folder_name": "ToDel"},
    )
    contacts = await async_client.get("/api/v1/contacts", headers=headers)
    cid = contacts.json()[0]["id"]
    response = await async_client.delete(f"/api/v1/contacts/{cid}", headers=headers)
    assert response.status_code == 204
