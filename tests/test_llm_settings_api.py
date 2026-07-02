"""Wave-0 RED scaffold — workspace LLM-settings API (LLMP-01/02/04/05).

Targets the settings router `/api/v1/workspace/llm-settings` (NOT yet built). Deferred
imports (none needed here — all through async_client) keep --collect-only clean; the
endpoints simply 404 now so the assertions FAIL for the right reason (behaviour missing).

Decisions covered:
  D-01 — workspace-level scope: workspace A cannot read workspace B's settings.
  D-02 — default-off: no row → provider default + api_key_status='unset', model NULL.
  D-04 — key stored Fernet-encrypted; only masked prefix ever returned (never the full key).
  D-05 — test-connection probe returns valid/invalid against a mocked provider client.

Auth pattern mirrors tests/test_warmup_router.py (valid_supabase_jwt + user_workspaces bind).
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

_LLM_SETTINGS_URL = "/api/v1/workspace/llm-settings"


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _make_workspace(db) -> str:
    wid = str(_uuid.uuid4())
    await db.execute(text("INSERT INTO workspaces (id, name) VALUES (:id, :n)"),
                     {"id": wid, "n": f"WS {wid[:8]}"})
    await db.commit()
    return wid


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def test_get_settings_default_off(async_client, valid_supabase_jwt, async_db_session):
    """No llm_settings row → GET returns the provider default, api_key_status='unset',
    model null (D-02 default-off, byte-identical to today's platform behaviour)."""
    db = async_db_session
    wid = await _make_workspace(db)
    uid = f"user-{_uuid.uuid4()}"
    await _bind(db, wid, uid)

    resp = await async_client.get(_LLM_SETTINGS_URL, headers=_auth_headers(valid_supabase_jwt, uid))
    assert resp.status_code == 200, (
        f"GET {_LLM_SETTINGS_URL} not available yet (got {resp.status_code}) — "
        "settings router must be built (18-03)"
    )
    body = resp.json()
    assert body["provider"] == "openai"          # default provider
    assert body["api_key_status"] == "unset"
    assert body["model"] is None


async def test_patch_stores_encrypted_and_masks(async_client, valid_supabase_jwt, async_db_session):
    """PATCH with {provider, model, api_key} → 200; response NEVER contains the full key
    (masked prefix only); DB read shows api_key_encrypted != plaintext (D-04)."""
    db = async_db_session
    wid = await _make_workspace(db)
    uid = f"user-{_uuid.uuid4()}"
    await _bind(db, wid, uid)

    plaintext_key = "sk-ant-secret-do-not-leak-1234567890abcdef"
    resp = await async_client.patch(
        _LLM_SETTINGS_URL,
        headers=_auth_headers(valid_supabase_jwt, uid),
        json={"provider": "anthropic", "model": "claude-sonnet-4-5", "api_key": plaintext_key},
    )
    assert resp.status_code == 200, (
        f"PATCH {_LLM_SETTINGS_URL} not available yet (got {resp.status_code})"
    )
    body = resp.json()
    # The full key must NEVER appear in the response body.
    import json as _json
    assert plaintext_key not in _json.dumps(body), "full API key leaked in response"

    # The stored ciphertext must differ from the plaintext.
    row = (await db.execute(text(
        "SELECT api_key_encrypted FROM llm_settings WHERE workspace_id = :wid"
    ), {"wid": wid})).first()
    assert row is not None, "PATCH did not persist an llm_settings row"
    assert row[0] is not None
    assert row[0] != plaintext_key, "API key stored in plaintext — must be Fernet-encrypted"


async def test_test_connection(async_client, valid_supabase_jwt, async_db_session, monkeypatch):
    """POST /test-connection returns {status:'valid'} for a good key and {status:'invalid'}
    for a key-level error, using a mocked provider client (D-05, no real network)."""
    from unittest.mock import AsyncMock
    import app.services.llm.resolve as resolve_mod

    db = async_db_session
    wid = await _make_workspace(db)
    uid = f"user-{_uuid.uuid4()}"
    await _bind(db, wid, uid)

    url = f"{_LLM_SETTINGS_URL}/test-connection"

    # Good key → the probe succeeds → status 'valid'.
    monkeypatch.setattr(resolve_mod, "probe_key", AsyncMock(return_value=True), raising=False)
    ok = await async_client.post(
        url, headers=_auth_headers(valid_supabase_jwt, uid),
        json={"provider": "anthropic", "api_key": "sk-ant-good"},
    )
    assert ok.status_code == 200, f"test-connection not available yet (got {ok.status_code})"
    assert ok.json()["status"] == "valid"

    # Bad key → the probe raises a key-level error → status 'invalid'.
    monkeypatch.setattr(
        resolve_mod, "probe_key",
        AsyncMock(side_effect=Exception("invalid api key")), raising=False,
    )
    bad = await async_client.post(
        url, headers=_auth_headers(valid_supabase_jwt, uid),
        json={"provider": "anthropic", "api_key": "sk-ant-bad"},
    )
    assert bad.status_code == 200
    assert bad.json()["status"] == "invalid"


async def test_workspace_isolation(async_client, valid_supabase_jwt, async_db_session):
    """Workspace A's settings are invisible to workspace B (D-01 workspace-level scope).

    B writes anthropic; A (a separate workspace with no row) still sees the default."""
    db = async_db_session
    wid_a = await _make_workspace(db)
    wid_b = await _make_workspace(db)
    uid_a = f"user-a-{_uuid.uuid4()}"
    uid_b = f"user-b-{_uuid.uuid4()}"
    await _bind(db, wid_a, uid_a)
    await _bind(db, wid_b, uid_b)

    # B configures anthropic.
    resp_b = await async_client.patch(
        _LLM_SETTINGS_URL,
        headers=_auth_headers(valid_supabase_jwt, uid_b),
        json={"provider": "anthropic", "model": "claude-sonnet-4-5", "api_key": "sk-ant-b"},
    )
    assert resp_b.status_code == 200, "PATCH not available yet"

    # A must NOT see B's config — A has no row, so it sees the default provider.
    resp_a = await async_client.get(_LLM_SETTINGS_URL, headers=_auth_headers(valid_supabase_jwt, uid_a))
    assert resp_a.status_code == 200
    assert resp_a.json()["provider"] == "openai", "workspace A leaked workspace B's settings"
