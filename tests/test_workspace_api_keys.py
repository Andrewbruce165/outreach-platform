"""
Integration tests для workspace API-keys (TENT-03).

POST /workspace/api-keys — plaintext возвращается ОДИН раз
GET  /workspace/api-keys — без plaintext
DELETE /workspace/api-keys/{id} — soft-revoke
Cross-tenant: ключ workspace A невидим для workspace B.
"""

import pytest


async def test_create_api_key_returns_plaintext_once(async_client, valid_supabase_jwt):
    """POST возвращает plaintext token (начинается с wsk_)."""
    token = valid_supabase_jwt(sub="apikey-user-1", email="api1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "n8n-integration"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("wsk_")
    assert len(body["prefix"]) == 12  # wsk_ + 8 chars
    assert body["prefix"] == body["token"][:12]
    assert body["name"] == "n8n-integration"


async def test_list_api_keys_excludes_plaintext(async_client, valid_supabase_jwt):
    """GET НЕ должен возвращать plaintext token (только prefix)."""
    token = valid_supabase_jwt(sub="apikey-user-2", email="api2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Создаём ключ
    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "first-key"},
    )
    assert create.status_code == 201

    # Запрашиваем список
    list_response = await async_client.get(
        "/api/v1/workspace/api-keys", headers=headers
    )
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["total"] >= 1
    for item in body["api_keys"]:
        # Никаких полей с plaintext
        assert "token" not in item
        assert "bcrypt_hash" not in item
        assert item["prefix"].startswith("wsk_")


async def test_revoke_api_key(async_client, valid_supabase_jwt):
    """DELETE → revoked_at заполнено; GET показывает revoked_at."""
    token = valid_supabase_jwt(sub="apikey-user-3", email="api3@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "to-revoke"},
    )
    key_id = create.json()["id"]

    revoke = await async_client.delete(
        f"/api/v1/workspace/api-keys/{key_id}", headers=headers
    )
    assert revoke.status_code == 204

    list_response = await async_client.get(
        "/api/v1/workspace/api-keys", headers=headers
    )
    revoked_key = next(
        k for k in list_response.json()["api_keys"] if k["id"] == key_id
    )
    assert revoked_key["revoked_at"] is not None


async def test_revoked_key_cannot_authenticate(async_client, valid_supabase_jwt):
    """Revoked ключ → 401 при попытке использовать."""
    token = valid_supabase_jwt(sub="apikey-user-4", email="api4@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "will-be-revoked"},
    )
    full_token = create.json()["token"]
    key_id = create.json()["id"]

    # Revoke
    await async_client.delete(
        f"/api/v1/workspace/api-keys/{key_id}", headers=headers
    )

    # Используем revoked ключ
    attempt = await async_client.get(
        "/api/v1/workspace",
        headers={"X-Workspace-Key": full_token},
    )
    assert attempt.status_code == 401
    assert attempt.json()["detail"]["code"] == "API_KEY_INVALID"


async def test_api_key_grants_access_to_workspace_endpoint(
    async_client, valid_supabase_jwt
):
    """Валидный wsk_ ключ даёт доступ к GET /workspace (TENT-04)."""
    token = valid_supabase_jwt(sub="apikey-user-5", email="api5@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "n8n-prod"},
    )
    full_token = create.json()["token"]

    # Используем ключ для GET /workspace
    response = await async_client.get(
        "/api/v1/workspace",
        headers={"X-Workspace-Key": full_token},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "api5@example.com"


async def test_cross_tenant_isolation(async_client, valid_supabase_jwt):
    """Ключ workspace A нельзя удалить через JWT workspace B (404, не 403 — security)."""
    token_a = valid_supabase_jwt(sub="apikey-iso-A", email="iso-a@example.com")
    token_b = valid_supabase_jwt(sub="apikey-iso-B", email="iso-b@example.com")

    # A создаёт ключ
    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "a-only"},
    )
    a_key_id = create.json()["id"]

    # B пробует удалить ключ A
    delete = await async_client.delete(
        f"/api/v1/workspace/api-keys/{a_key_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert delete.status_code == 404  # not found, NOT 403 (security: hide existence)

    # A видит свой ключ нетронутым
    list_a = await async_client.get(
        "/api/v1/workspace/api-keys",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    a_key = next(k for k in list_a.json()["api_keys"] if k["id"] == a_key_id)
    assert a_key["revoked_at"] is None
