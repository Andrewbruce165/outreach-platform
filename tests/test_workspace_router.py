"""
Integration tests для workspace endpoints (AUTH-01 bootstrap, AUTH-04 refresh, TENT-04).

POST /api/v1/auth/me
GET  /api/v1/workspace
PATCH /api/v1/workspace
"""

import pytest


# ─── POST /auth/me (TENT-02 + AUTH-01 bootstrap UX) ──────────────────────────

async def test_auth_me_no_auth_returns_401(async_client):
    """Без заголовков → 401."""
    response = await async_client.post("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


async def test_auth_me_bootstrap_creates_workspace(async_client, valid_supabase_jwt):
    """Первый вызов с валидным JWT → создаётся workspace, возвращается id."""
    token = valid_supabase_jwt(sub="me-test-user-1", email="me@example.com")
    response = await async_client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "workspace_id" in body
    assert body["user_id"] == "me-test-user-1"
    assert body["source"] == "jwt"
    assert body["role"] == "owner"
    assert body["workspace_name"] == "me@example.com"  # D-09


async def test_auth_me_idempotent(async_client, valid_supabase_jwt):
    """Повторный вызов с тем же sub → тот же workspace_id."""
    token = valid_supabase_jwt(sub="me-test-user-2", email="me2@example.com")
    r1 = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    r2 = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["workspace_id"] == r2.json()["workspace_id"]


async def test_auth_me_rejects_api_key(async_client):
    """auth/me JWT-only (D-10): попытка с X-Workspace-Key → 401 (нет валидного ключа без bootstrap)."""
    response = await async_client.post(
        "/api/v1/auth/me",
        headers={"X-Workspace-Key": "wsk_random_invalid"},
    )
    assert response.status_code == 401


# ─── GET /workspace ──────────────────────────────────────────────────────────

async def test_get_workspace_with_jwt(async_client, valid_supabase_jwt):
    """JWT даёт доступ к GET /workspace."""
    token = valid_supabase_jwt(sub="get-user-1", email="get1@example.com")
    response = await async_client.get(
        "/api/v1/workspace",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert body["name"] == "get1@example.com"


async def test_get_workspace_no_auth_401(async_client):
    """Без заголовков → 401."""
    response = await async_client.get("/api/v1/workspace")
    assert response.status_code == 401


# ─── PATCH /workspace (rename) ───────────────────────────────────────────────

async def test_patch_workspace_renames(async_client, valid_supabase_jwt):
    """JWT → rename работает."""
    token = valid_supabase_jwt(sub="patch-user-1", email="patch1@example.com")
    # Bootstrap
    await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    # Rename
    response = await async_client.patch(
        "/api/v1/workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Renamed Workspace"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "My Renamed Workspace"


async def test_patch_workspace_empty_name_400(async_client, valid_supabase_jwt):
    """Empty name → 400 INVALID_NAME."""
    token = valid_supabase_jwt(sub="patch-user-2", email="patch2@example.com")
    response = await async_client.patch(
        "/api/v1/workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "   "},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_NAME"
