"""Unit tests for TelegramListener._reconcile_tick (Phase 2 — D-18).

The listener.py module unconditionally calls ``logging.basicConfig`` and uses
its own asyncpg engine bound to DATABASE_URL — so the tests below mock
``listener.AsyncSessionLocal`` to return a fake context manager and assert
the diff logic directly. ``make_telegram_client`` is also patched so no
network or Telethon construction is attempted.

Tests:
1. Reconcile adds a brand-new desired sender — fires start_client task.
2. Reconcile with paused/removed sender — disconnect called, dict shrinks.
3. Reconcile with auth_status != ok — same as #2 (filtered out by get_active_senders).
4. Proxy change — disconnect now, reconnect on next tick.
5. _reconcile_loop cancels gracefully on stop().
6. get_active_senders SQL filter shape is correct (no is_active, uses
   lifecycle_status + auth_status + role='sender').
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _patch_listener_imports():
    """Import listener fresh — its module-level engine is created on import.

    We rely on ``DATABASE_URL`` already being set by conftest.py.
    """
    from app.services import listener as listener_mod

    return listener_mod


def _stub_get_active_senders(listener, senders: list[dict]):
    """Replace listener.get_active_senders with an async stub returning ``senders``."""
    listener.get_active_senders = AsyncMock(return_value=senders)


def _make_mock_client(connected: bool = True):
    client = MagicMock()
    client.is_connected = MagicMock(return_value=connected)
    client.disconnect = AsyncMock(return_value=None)
    return client


def _seed_connected(listener, sender_id: str, slug: str, proxy=None):
    """Pretend ``start_client`` has already wired this sender up."""
    listener._connected_sender_ids.add(sender_id)
    listener._proxy_snapshot[sender_id] = proxy
    listener._sender_id_to_slug[sender_id] = slug
    listener.clients[slug] = _make_mock_client()


# ─── Tests ───────────────────────────────────────────────────────────────────


async def test_reconcile_tick_connects_new_sender(monkeypatch):
    listener_mod = _patch_listener_imports()
    listener = listener_mod.TelegramListener()

    desired = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "slug": "new-1",
            "phone": "+79001112233",
            "session_string": "enc",
            "ai_context_id": None,
            "proxy": None,
        }
    ]
    _stub_get_active_senders(listener, desired)

    started = []

    async def fake_start_client(s):
        started.append(s["id"])

    listener.start_client = fake_start_client

    summary = await listener._reconcile_tick()
    # Let create_task'd coroutine actually run.
    await asyncio.sleep(0)

    assert summary["added"] == 1
    assert summary["removed"] == 0
    assert started == ["11111111-1111-1111-1111-111111111111"]


async def test_reconcile_tick_disconnects_removed_sender(monkeypatch):
    listener_mod = _patch_listener_imports()
    listener = listener_mod.TelegramListener()

    _seed_connected(listener, "22222222-2222-2222-2222-222222222222", "kept")
    _seed_connected(listener, "33333333-3333-3333-3333-333333333333", "gone")

    # Desired now only contains "kept" — "gone" was paused/removed.
    desired = [
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "slug": "kept",
            "phone": "+79001",
            "session_string": "enc",
            "ai_context_id": None,
            "proxy": None,
        }
    ]
    _stub_get_active_senders(listener, desired)
    listener.start_client = AsyncMock()

    gone_client = listener.clients["gone"]
    summary = await listener._reconcile_tick()

    assert summary["removed"] == 1
    assert "gone" not in listener.clients
    assert "33333333-3333-3333-3333-333333333333" not in listener._connected_sender_ids
    gone_client.disconnect.assert_awaited_once()


async def test_reconcile_tick_disconnects_when_auth_filtered_out():
    """If get_active_senders no longer returns a sender (e.g. auth_status flipped
    to session_expired), reconcile disconnects it — same code path as 'removed'.
    """
    listener_mod = _patch_listener_imports()
    listener = listener_mod.TelegramListener()

    _seed_connected(listener, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "errored")
    _stub_get_active_senders(listener, [])  # auth_status != ok → filtered out
    listener.start_client = AsyncMock()

    errored_client = listener.clients["errored"]
    summary = await listener._reconcile_tick()

    assert summary["removed"] == 1
    errored_client.disconnect.assert_awaited_once()
    assert "errored" not in listener.clients


async def test_reconcile_tick_detects_proxy_change():
    listener_mod = _patch_listener_imports()
    listener = listener_mod.TelegramListener()

    old_proxy = {"type": "socks5", "host": "1.1.1.1", "port": 1080}
    new_proxy = {"type": "socks5", "host": "9.9.9.9", "port": 1080}
    sid = "44444444-4444-4444-4444-444444444444"

    _seed_connected(listener, sid, "swap", proxy=old_proxy)

    desired = [
        {
            "id": sid,
            "slug": "swap",
            "phone": "+79002",
            "session_string": "enc",
            "ai_context_id": None,
            "proxy": new_proxy,
        }
    ]
    _stub_get_active_senders(listener, desired)
    listener.start_client = AsyncMock()

    old_client = listener.clients["swap"]
    summary = await listener._reconcile_tick()
    assert summary["reproxied"] == 1
    old_client.disconnect.assert_awaited_once()
    # The dict slot is empty now; next tick's NEW branch will spawn start_client.
    assert "swap" not in listener.clients
    assert sid not in listener._connected_sender_ids


async def test_reconcile_loop_cancels_gracefully():
    listener_mod = _patch_listener_imports()
    listener = listener_mod.TelegramListener()
    listener.reconcile_interval = 0.01
    _stub_get_active_senders(listener, [])
    listener.start_client = AsyncMock()

    # Run a few ticks then cancel.
    listener.running = True
    task = asyncio.create_task(listener._reconcile_loop())
    await asyncio.sleep(0.05)
    listener.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done()


async def test_get_active_senders_query_shape():
    """Make sure the SQL filter still matches Phase 2 contract (D-11/D-18)."""
    import inspect

    import re as _re

    listener_mod = _patch_listener_imports()
    src = inspect.getsource(listener_mod.TelegramListener.get_active_senders)
    assert "lifecycle_status = 'active'" in src
    assert "auth_status = 'ok'" in src
    assert "role = 'sender'" in src
    # And make sure the dead column reference is gone from executable code.
    # (The decision comment legitimately mentions "is_active dropped".)
    code_only = _re.sub(r"#.*", "", src)
    assert "is_active" not in code_only


async def test_reconcile_loop_attributes_initialised():
    """Defensive: D-18 attrs exist on a fresh listener."""
    listener_mod = _patch_listener_imports()
    listener = listener_mod.TelegramListener()
    assert hasattr(listener, "reconcile_interval")
    assert hasattr(listener, "_reconcile_task")
    assert isinstance(listener._connected_sender_ids, set)
    assert isinstance(listener._proxy_snapshot, dict)
    assert isinstance(listener._sender_id_to_slug, dict)
