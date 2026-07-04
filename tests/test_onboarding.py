"""Integration tests for onboarding router (Phase 2 — ONBD-01..05).

Telethon is mocked via ``monkeypatch.setattr`` on
``app.routers.onboarding.make_telegram_client`` so we never touch the network.

Covers:
* AUTH_REQUIRED on missing auth.
* /start happy path (row appears in onboarding_sessions, status='code_sent').
* /verify-code success → sender created in correct workspace.
* /verify-code PhoneCodeInvalidError → 400 PHONE_CODE_INVALID.
* /verify-code SessionPasswordNeededError → 200 2fa_required + status updated.
* /verify-2fa happy path + PasswordHashInvalidError → 400.
* /verify-code with invalid session_id → 404 SESSION_NOT_FOUND.
* /verify-code cross-workspace → 404 (workspace isolation).
* /qr-start returns QR image + session_id.
* DELETE /cancel/{id} clears state idempotently.
* Recovery: emptied in-process dict still works (decrypt_session path).
* Role override (checker) at verify-code time.
"""

import uuid
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

import app.routers.onboarding as onboarding_router
from app.models import OnboardingSession, Sender

pytestmark = pytest.mark.asyncio


def _make_valid_string_session() -> str:
    """A real (empty-auth-key) Telethon StringSession blob.

    The /verify-code recovery path persists the session_string and later rebuilds
    a real ``StringSession(session_string)``; that constructor raises ValueError on
    anything that isn't a genuine Telethon session, so the mock must return one.
    """
    from telethon.crypto import AuthKey
    from telethon.sessions import StringSession

    s = StringSession()
    s.set_dc(2, "149.154.167.40", 443)
    s.auth_key = AuthKey(b"\x00" * 256)
    return s.save()


_VALID_STRING_SESSION = _make_valid_string_session()


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_client(
    sign_in_effect=None,
    phone_code_hash: str = "hash-default",
    me_id: int = 12345,
    me_first_name: str = "Tester",
    me_username: str | None = None,
):
    """Build a MagicMock that quacks like a Telethon TelegramClient."""
    client = MagicMock(name="MockTelethonClient")
    client.connect = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)
    client.is_connected = MagicMock(return_value=True)

    sent_code = MagicMock()
    sent_code.phone_code_hash = phone_code_hash
    client.send_code_request = AsyncMock(return_value=sent_code)

    if sign_in_effect is None:
        client.sign_in = AsyncMock(return_value=None)
    else:
        client.sign_in = AsyncMock(side_effect=sign_in_effect)

    session = MagicMock()
    # A VALID (empty-auth-key) Telethon StringSession blob — the /verify-code
    # recovery path rebuilds a real StringSession(session_string) from the
    # persisted value, which raises ValueError on a non-Telethon string.
    session.save = MagicMock(return_value=_VALID_STRING_SESSION)
    client.session = session

    me = MagicMock()
    me.id = me_id
    me.first_name = me_first_name
    me.username = me_username   # Phase 20 (PROF-08): finalize caches this onto sender.tg_username
    client.get_me = AsyncMock(return_value=me)

    qr_login = MagicMock()
    qr_login.url = "tg://login?token=fake"
    qr_login.wait = AsyncMock(return_value=None)
    client.qr_login = AsyncMock(return_value=qr_login)
    return client


def _patch_factory(monkeypatch, client):
    """Make ``make_telegram_client(...)`` always return ``client``."""

    def _factory(session, proxy=None, **kwargs):
        return client

    monkeypatch.setattr(onboarding_router, "make_telegram_client", _factory)


async def _bootstrap_workspace(async_client, valid_supabase_jwt, sub_suffix: str = ""):
    """POST /auth/me to create a workspace; return (token, workspace_id)."""
    sub = f"onb-{sub_suffix}-{uuid4()}"
    token = valid_supabase_jwt(sub=sub, email=f"{sub}@x.com")
    r = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    return token, r.json()["workspace_id"]


def _clear_in_process_clients():
    onboarding_router._in_process_clients.clear()


# ─── Tests ───────────────────────────────────────────────────────────────────


async def test_start_no_auth_returns_401(async_client):
    r = await async_client.post("/api/v1/onboarding/start", json={"phone": "+79001234567"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "AUTH_REQUIRED"


async def test_start_happy_path_creates_session_row(
    async_client, async_db_session, valid_supabase_jwt, monkeypatch
):
    _clear_in_process_clients()
    token, ws_id = await _bootstrap_workspace(async_client, valid_supabase_jwt, "start-ok")
    _patch_factory(monkeypatch, _make_mock_client(phone_code_hash="hh-1"))

    r = await async_client.post(
        "/api/v1/onboarding/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"phone": "+79001234567"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "code_sent"
    assert body["phone"] == "+79001234567"
    session_id = body["session_id"]

    row = (
        await async_db_session.execute(
            select(OnboardingSession).where(OnboardingSession.id == uuid.UUID(session_id))
        )
    ).scalars().first()
    assert row is not None
    assert row.workspace_id == uuid.UUID(ws_id)
    assert row.phone == "+79001234567"
    assert row.status == "code_sent"
    assert row.phone_code_hash == "hh-1"


async def test_verify_code_creates_sender_in_workspace(
    async_client, async_db_session, valid_supabase_jwt, monkeypatch
):
    _clear_in_process_clients()
    token, ws_id = await _bootstrap_workspace(
        async_client, valid_supabase_jwt, "verify-ok"
    )
    headers = {"Authorization": f"Bearer {token}"}

    client = _make_mock_client(me_id=99001, me_first_name="Alice")
    _patch_factory(monkeypatch, client)

    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79002223344"}
    )
    sid = start.json()["session_id"]

    r = await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers=headers,
        json={"session_id": sid, "code": "12345"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["slug"] == "sender-99001"

    sender_row = (
        await async_db_session.execute(
            select(Sender).where(Sender.id == uuid.UUID(body["sender_id"]))
        )
    ).scalars().first()
    assert sender_row is not None
    assert sender_row.workspace_id == uuid.UUID(ws_id)
    assert sender_row.role == "sender"
    assert sender_row.lifecycle_status == "active"
    assert sender_row.auth_status == "ok"

    # onboarding row should be deleted on success.
    onb_row = (
        await async_db_session.execute(
            select(OnboardingSession).where(OnboardingSession.id == uuid.UUID(sid))
        )
    ).scalars().first()
    assert onb_row is None


async def test_verify_code_invalid_code_returns_400(
    async_client, valid_supabase_jwt, monkeypatch
):
    _clear_in_process_clients()
    token, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "code-bad")
    headers = {"Authorization": f"Bearer {token}"}

    client = _make_mock_client(sign_in_effect=PhoneCodeInvalidError(None))
    _patch_factory(monkeypatch, client)

    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79003334455"}
    )
    sid = start.json()["session_id"]

    r = await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers=headers,
        json={"session_id": sid, "code": "00000"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "PHONE_CODE_INVALID"


async def test_verify_code_2fa_required_marks_state(
    async_client, async_db_session, valid_supabase_jwt, monkeypatch
):
    _clear_in_process_clients()
    token, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "code-2fa")
    headers = {"Authorization": f"Bearer {token}"}

    client = _make_mock_client(sign_in_effect=SessionPasswordNeededError(None))
    _patch_factory(monkeypatch, client)

    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79004445566"}
    )
    sid = start.json()["session_id"]

    r = await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers=headers,
        json={"session_id": sid, "code": "12345"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "2fa_required"
    assert body["session_id"] == sid

    row = (
        await async_db_session.execute(
            select(OnboardingSession).where(OnboardingSession.id == uuid.UUID(sid))
        )
    ).scalars().first()
    assert row.status == "awaiting_2fa"


async def test_verify_2fa_success_creates_sender(
    async_client, async_db_session, valid_supabase_jwt, monkeypatch
):
    _clear_in_process_clients()
    token, ws_id = await _bootstrap_workspace(
        async_client, valid_supabase_jwt, "2fa-ok"
    )
    headers = {"Authorization": f"Bearer {token}"}

    # sign_in: first call (verify-code) raises 2FA, second call (verify-2fa) succeeds
    client = _make_mock_client(me_id=88001, me_first_name="Bob")
    client.sign_in = AsyncMock(
        side_effect=[SessionPasswordNeededError(None), None]
    )
    _patch_factory(monkeypatch, client)

    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79005556677"}
    )
    sid = start.json()["session_id"]

    await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers=headers,
        json={"session_id": sid, "code": "12345"},
    )

    r = await async_client.post(
        "/api/v1/onboarding/verify-2fa",
        headers=headers,
        json={"session_id": sid, "password": "secret"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["slug"] == "sender-88001"

    sender_row = (
        await async_db_session.execute(
            select(Sender).where(Sender.id == uuid.UUID(body["sender_id"]))
        )
    ).scalars().first()
    assert sender_row.workspace_id == uuid.UUID(ws_id)


async def test_verify_2fa_invalid_password_returns_400(
    async_client, valid_supabase_jwt, monkeypatch
):
    _clear_in_process_clients()
    token, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "2fa-bad")
    headers = {"Authorization": f"Bearer {token}"}

    client = _make_mock_client()
    client.sign_in = AsyncMock(
        side_effect=[SessionPasswordNeededError(None), PasswordHashInvalidError(None)]
    )
    _patch_factory(monkeypatch, client)

    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79006667788"}
    )
    sid = start.json()["session_id"]

    await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers=headers,
        json={"session_id": sid, "code": "12345"},
    )
    r = await async_client.post(
        "/api/v1/onboarding/verify-2fa",
        headers=headers,
        json={"session_id": sid, "password": "wrong"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "PASSWORD_INVALID"


async def test_verify_code_unknown_session_returns_404(
    async_client, valid_supabase_jwt
):
    token, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "unknown")
    bogus = str(uuid4())
    r = await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers={"Authorization": f"Bearer {token}"},
        json={"session_id": bogus, "code": "12345"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SESSION_NOT_FOUND"


async def test_verify_code_cross_workspace_returns_404(
    async_client, valid_supabase_jwt, monkeypatch
):
    """Session belongs to workspace A; B sends verify-code → 404."""
    _clear_in_process_clients()
    token_a, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "x-tenant-a")
    token_b, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "x-tenant-b")

    _patch_factory(monkeypatch, _make_mock_client())
    start = await async_client.post(
        "/api/v1/onboarding/start",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"phone": "+79007778899"},
    )
    sid = start.json()["session_id"]

    r = await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"session_id": sid, "code": "12345"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SESSION_NOT_FOUND"


async def test_qr_start_returns_image_and_session(
    async_client, valid_supabase_jwt, monkeypatch
):
    _clear_in_process_clients()
    token, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "qr")

    client = _make_mock_client()
    # Prevent the background _wait_for_qr coroutine from running to completion
    # during the test (it would try to create a Sender on get_me success).
    import asyncio as _asyncio

    async def _never(timeout=None):
        await _asyncio.sleep(3600)

    client.qr_login.return_value.wait = AsyncMock(side_effect=_never)
    _patch_factory(monkeypatch, client)

    r = await async_client.post(
        "/api/v1/onboarding/qr-start",
        headers={"Authorization": f"Bearer {token}"},
        json={"role": "sender"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "session_id" in body
    assert body["status"] == "pending"
    assert body["qr_image"].startswith("data:image/png;base64,")


async def test_cancel_idempotent(async_client, valid_supabase_jwt, monkeypatch):
    _clear_in_process_clients()
    token, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "cancel")
    headers = {"Authorization": f"Bearer {token}"}

    _patch_factory(monkeypatch, _make_mock_client())
    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79008889900"}
    )
    sid = start.json()["session_id"]

    r1 = await async_client.delete(f"/api/v1/onboarding/cancel/{sid}", headers=headers)
    assert r1.status_code == 204
    # Idempotent — second cancel on the same id is still 204.
    r2 = await async_client.delete(f"/api/v1/onboarding/cancel/{sid}", headers=headers)
    assert r2.status_code == 204


async def test_verify_code_recovers_when_in_process_dict_empty(
    async_client, async_db_session, valid_supabase_jwt, monkeypatch
):
    """Simulate api-container restart: clear _in_process_clients between
    /start and /verify-code. The router must rebuild a client from
    decrypted session_string and continue.
    """
    _clear_in_process_clients()
    token, _ = await _bootstrap_workspace(
        async_client, valid_supabase_jwt, "recovery"
    )
    headers = {"Authorization": f"Bearer {token}"}

    client = _make_mock_client(me_id=77001)
    _patch_factory(monkeypatch, client)

    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79001112233"}
    )
    sid = start.json()["session_id"]

    # Simulate restart: in-process dict cleared, decrypt path must rebuild.
    onboarding_router._in_process_clients.clear()

    r = await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers=headers,
        json={"session_id": sid, "code": "12345"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"


async def test_role_override_in_verify_code(
    async_client, async_db_session, valid_supabase_jwt, monkeypatch
):
    """User picks 'checker' in the UI at verify-code time; sender row reflects it."""
    _clear_in_process_clients()
    token, _ = await _bootstrap_workspace(async_client, valid_supabase_jwt, "checker")
    headers = {"Authorization": f"Bearer {token}"}

    _patch_factory(monkeypatch, _make_mock_client(me_id=66001))
    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79002224466"}
    )
    sid = start.json()["session_id"]

    r = await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers=headers,
        json={"session_id": sid, "code": "12345", "role": "checker"},
    )
    assert r.status_code == 200
    sender_id = r.json()["sender_id"]
    sender_row = (
        await async_db_session.execute(
            select(Sender).where(Sender.id == uuid.UUID(sender_id))
        )
    ).scalars().first()
    assert sender_row.role == "checker"


async def test_no_subprocess_or_legacy_dict_in_module():
    """Static check: legacy anti-patterns are gone from the rewritten router.

    The module docstring / comments legitimately MENTION the removed patterns
    (documenting that they are gone), so strip comments and string/docstring
    literals before asserting the tokens are absent from executable code.
    """
    import inspect
    import io
    import tokenize

    src = inspect.getsource(onboarding_router)

    # Drop comments and string literals (incl. docstrings) via tokenize so the
    # decision documentation that references the dead patterns is ignored.
    code_tokens = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        code_tokens.append(tok.string)
    code_only = " ".join(code_tokens)

    assert "subprocess" not in code_only, "subprocess.run anti-pattern must be gone (D-18)"
    assert "_onboarding_sessions" not in code_only, "legacy dict must be gone (D-16)"
    assert "verify_api_key" not in code_only, "legacy verify_api_key import must be gone"


# ─── Phase 20 (PROF-08): finalize caches the Telegram profile ─────────────────


async def test_finalize_caches_profile(
    async_client, async_db_session, valid_supabase_jwt, monkeypatch
):
    """RED (PROF-08): onboarding finalize (verify-code success) caches the account's
    @username from get_me() onto the new sender row (sender.tg_username). RED until
    the downstream Wave plan wires the profile cache into the finalize path."""
    _clear_in_process_clients()
    token, ws_id = await _bootstrap_workspace(
        async_client, valid_supabase_jwt, "finalize-profile"
    )
    headers = {"Authorization": f"Bearer {token}"}

    client = _make_mock_client(
        me_id=770077, me_first_name="Profiled", me_username="cachedhandle"
    )
    _patch_factory(monkeypatch, client)

    start = await async_client.post(
        "/api/v1/onboarding/start", headers=headers, json={"phone": "+79007770077"}
    )
    sid = start.json()["session_id"]

    r = await async_client.post(
        "/api/v1/onboarding/verify-code",
        headers=headers,
        json={"session_id": sid, "code": "12345"},
    )
    assert r.status_code == 200, r.text
    sender_id = r.json()["sender_id"]

    sender_row = (
        await async_db_session.execute(
            select(Sender).where(Sender.id == uuid.UUID(sender_id))
        )
    ).scalars().first()
    assert sender_row is not None
    assert sender_row.tg_username == "cachedhandle"
