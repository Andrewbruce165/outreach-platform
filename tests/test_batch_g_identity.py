"""Batch G (quick-260704-buc) — WR-14 cross-workspace identity keying.

WR-14: ``senders.slug`` is unique only per-workspace (migration 014), so the SAME
slug can legitimately exist in two workspaces once the same Telegram account is
onboarded into two tenants. Keying auth_status updates or per-account asyncio
locks on ``slug`` is therefore unsafe:

- ``TelegramService._set_auth_status`` previously did ``scalar_one_or_none()`` on
  ``WHERE slug = :slug`` → ``MultipleResultsFound`` inside the auth-error handler,
  replacing ``SessionAuthError`` with an unrelated crash so the queue worker
  burned attempts forever and never flipped ``auth_status``.
- ``TelegramService._locks`` / ``CheckerService._locks`` keyed on slug would
  serialize two different accounts that happen to share a slug.

The fix keys both on the sender/checker ``id`` (primary key). These tests seed
two rows sharing a slug across two workspaces and assert id-scoped behaviour.
"""

import uuid

import pytest
from sqlalchemy import text

from app.models import Sender, Workspace
from app.services.telegram import (
    SessionAuthError,
    TelegramService,
    _set_auth_status,
)

pytestmark = pytest.mark.asyncio


async def _mk_workspace(db, name: str) -> Workspace:
    ws = Workspace(name=name)
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return ws


async def _mk_sender(db, workspace_id, slug: str, **overrides) -> Sender:
    tag = uuid.uuid4().hex[:8]
    defaults = dict(
        workspace_id=workspace_id,
        slug=slug,
        name=f"Sender {slug} {tag}",
        phone=f"+7900{uuid.uuid4().int % 10_000_000:07d}",
        session_string="encrypted_stub",
        role="sender",
        auth_status="ok",
        lifecycle_status="active",
        rate_per_min=4,
        rate_per_hour=20,
        rate_per_day=150,
    )
    defaults.update(overrides)
    s = Sender(**defaults)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


# ─── WR-14 (Task 2): TelegramService ─────────────────────────────────────────


async def test_set_auth_status_updates_by_id_not_slug(async_db_session):
    """Two senders sharing a slug across workspaces — flipping auth_status by id
    updates ONLY that row and never raises MultipleResultsFound."""
    ws_a = await _mk_workspace(async_db_session, "WR14 set-auth WS A")
    ws_b = await _mk_workspace(async_db_session, "WR14 set-auth WS B")
    s_a = await _mk_sender(async_db_session, ws_a.id, "dup-slug-wr14")
    s_b = await _mk_sender(async_db_session, ws_b.id, "dup-slug-wr14")

    # Must NOT raise (old slug-keyed scalar_one_or_none → MultipleResultsFound).
    await _set_auth_status(str(s_a.id), "session_expired")

    a_status = (await async_db_session.execute(
        text("SELECT auth_status FROM senders WHERE id = :id"), {"id": str(s_a.id)}
    )).scalar()
    b_status = (await async_db_session.execute(
        text("SELECT auth_status FROM senders WHERE id = :id"), {"id": str(s_b.id)}
    )).scalar()
    assert a_status == "session_expired"
    assert b_status == "ok", "the same-slug sender in the OTHER workspace must be untouched"


async def test_get_client_keys_lock_and_auth_by_id(async_db_session, monkeypatch):
    """get_client on a dead/unauthorized session flips the CORRECT sender's
    auth_status BY ID, raises SessionAuthError, and keys _locks by id (so two
    same-slug senders in different workspaces get distinct locks)."""
    ws_a = await _mk_workspace(async_db_session, "WR14 get-client WS A")
    ws_b = await _mk_workspace(async_db_session, "WR14 get-client WS B")
    s_a = await _mk_sender(async_db_session, ws_a.id, "gc-dup-wr14")
    s_b = await _mk_sender(async_db_session, ws_b.id, "gc-dup-wr14")

    # Mock the Telethon seam: empty session string (valid StringSession), a client
    # that connects fine but reports NOT authorized → session_expired path. No net.
    class _Client:
        async def connect(self):
            return None

        async def is_user_authorized(self):
            return False

        async def disconnect(self):
            return None

    monkeypatch.setattr("app.services.telegram.decrypt_session", lambda _s: "")
    monkeypatch.setattr(
        "app.services.telegram.make_telegram_client", lambda *a, **k: _Client()
    )

    svc = TelegramService()

    with pytest.raises(SessionAuthError) as ei_a:
        await svc.get_client("gc-dup-wr14", str(s_a.id), "enc")
    assert ei_a.value.auth_status == "session_expired"

    # Lock keyed by id, NOT slug.
    assert str(s_a.id) in svc._locks
    assert "gc-dup-wr14" not in svc._locks

    # Only sender A flipped (by id); the same-slug sender B is untouched.
    a_status = (await async_db_session.execute(
        text("SELECT auth_status FROM senders WHERE id = :id"), {"id": str(s_a.id)}
    )).scalar()
    b_status = (await async_db_session.execute(
        text("SELECT auth_status FROM senders WHERE id = :id"), {"id": str(s_b.id)}
    )).scalar()
    assert a_status == "session_expired"
    assert b_status == "ok"

    # Second same-slug sender → a DISTINCT lock keyed by its own id.
    with pytest.raises(SessionAuthError):
        await svc.get_client("gc-dup-wr14", str(s_b.id), "enc")
    assert svc._locks[str(s_a.id)] is not svc._locks[str(s_b.id)]
    # Same id → same lock (stable per account).
    assert svc._locks[str(s_a.id)] is svc._locks[str(s_a.id)]


async def test_get_client_signature_takes_sender_id_second():
    """get_client(self, sender_slug, sender_id, encrypted_session, proxy=None)."""
    import inspect

    params = list(inspect.signature(TelegramService.get_client).parameters.keys())
    # ['self', 'sender_slug', 'sender_id', 'encrypted_session', 'proxy']
    assert params[1] == "sender_slug"
    assert params[2] == "sender_id"
    assert "encrypted_session" in params


# ─── WR-14 (Task 3): CheckerService ──────────────────────────────────────────


async def test_checker_locks_keyed_by_id():
    """Two same-slug checker_ids produce DISTINCT locks; the same id → same lock."""
    from app.services.checker import CheckerService

    svc = CheckerService()
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())

    assert svc._get_lock(id_a) is not svc._get_lock(id_b)
    assert svc._get_lock(id_a) is svc._get_lock(id_a)


async def test_probe_control_locks_on_checker_id(monkeypatch):
    """probe_control(checker_id=...) acquires the lock keyed by id, not by the
    (non-unique) slug."""
    from app.services.checker import CheckerService

    svc = CheckerService()

    class _FakeClient:
        def is_connected(self):
            return False

    async def _fake_get_client(*args, **kwargs):
        return _FakeClient()

    # Shadow the bound method on the instance (plain function → no self binding).
    svc._get_client = _fake_get_client

    cid = str(uuid.uuid4())
    slug = "dup-checker-wr14"
    # phones=[] → the lock is taken and the resolve loop is skipped (no network).
    summary = await svc.probe_control(
        checker_slug=slug, encrypted_session="enc", phones=[], checker_id=cid
    )
    assert summary["checked"] == 0
    assert cid in svc._locks, "probe_control must key its lock on checker_id"
    assert slug not in svc._locks, "probe_control must NOT key its lock on slug"


async def test_probe_control_falls_back_to_slug_without_id(monkeypatch):
    """Omitting checker_id preserves the old slug-keyed behaviour (compat)."""
    from app.services.checker import CheckerService

    svc = CheckerService()

    class _FakeClient:
        def is_connected(self):
            return False

    async def _fake_get_client(*args, **kwargs):
        return _FakeClient()

    svc._get_client = _fake_get_client

    slug = "legacy-slug-wr14"
    await svc.probe_control(checker_slug=slug, encrypted_session="enc", phones=[])
    assert slug in svc._locks
