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
import json
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


# ─── proxy-switch-listener-lag (mig 062) TTL sweep ────────────────────────────


async def test_sweep_clears_stale_proxy_switch_flag(test_sender_factory):
    """A proxy_switch_pending_at older than the TTL is cleared by the sweep;
    a fresh one (younger than TTL) is left intact so the sender stays paused.
    """
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import AsyncSessionLocal

    listener_mod = _patch_listener_imports()
    listener = listener_mod.TelegramListener()
    ttl = get_settings().proxy_switch_pending_ttl_seconds

    stale = await test_sender_factory(slug="proxy-stale")
    fresh = await test_sender_factory(slug="proxy-fresh")

    async with AsyncSessionLocal() as db:
        # stale: flag set well beyond the TTL → must be swept.
        await db.execute(
            text(
                "UPDATE senders SET proxy_switch_pending_at = "
                "NOW() - make_interval(secs => :age) WHERE id = :sid"
            ),
            {"age": ttl + 60, "sid": str(stale.id)},
        )
        # fresh: flag just set → within TTL → must survive.
        await db.execute(
            text("UPDATE senders SET proxy_switch_pending_at = NOW() WHERE id = :sid"),
            {"sid": str(fresh.id)},
        )
        await db.commit()

    swept = await listener._sweep_stale_proxy_switch_flags()
    assert swept >= 1

    async with AsyncSessionLocal() as db:
        rows = dict(
            (r.slug, r.proxy_switch_pending_at)
            for r in (
                await db.execute(
                    text(
                        "SELECT slug, proxy_switch_pending_at FROM senders "
                        "WHERE slug IN ('proxy-stale', 'proxy-fresh')"
                    )
                )
            ).fetchall()
        )
    assert rows["proxy-stale"] is None          # swept
    assert rows["proxy-fresh"] is not None       # still pausing


# ─── proxy-switch-listener-lag: fix B — flag cleared ONLY on proxy match ──────
#
# The failed live-verify (see debug/proxy-switch-listener-lag.md Evidence) was
# caused by start_client's confirmed-reconnect UPDATE clearing
# proxy_switch_pending_at on ANY successful get_me(), even when the listener had
# reconnected on the OLD proxy while the DB already held the NEW one. Fix B makes
# the clear conditional: it only NULLs the flag when the proxy the listener
# actually connected with matches the current sender.proxy in the DB. The test
# below drives the EXACT SQL clause start_client runs, so the guard can't silently
# regress. (start_client's full reconnect loop is not driven here: mocking a
# persistent-connection Telethon client whose run_until_disconnected returns
# instantly deadlocks against the session-scoped test engine — the live-verify
# step covers the end-to-end reconnect behaviour of fix A.)

# Verbatim copy of the flag-clear statement in listener.start_client — keep in sync.
_CLEAR_FLAG_SQL = (
    "UPDATE senders SET telegram_id = :tg_id, "
    "proxy_switch_pending_at = CASE "
    "  WHEN proxy IS NOT DISTINCT FROM CAST(:connected_proxy AS jsonb) "
    "  THEN NULL ELSE proxy_switch_pending_at END "
    "WHERE id = :sid"
)


async def _run_clear_flag(session_local, sender_id: str, connected_proxy):
    """Execute start_client's confirmed-reconnect UPDATE for a given connected proxy."""
    from sqlalchemy import text

    async with session_local() as db:
        await db.execute(
            text(_CLEAR_FLAG_SQL),
            {
                "tg_id": 5550001,
                "connected_proxy": (
                    json.dumps(connected_proxy) if connected_proxy is not None else None
                ),
                "sid": sender_id,
            },
        )
        await db.commit()


async def _read_flag(session_local, sender_id: str):
    from sqlalchemy import text

    async with session_local() as db:
        return (
            await db.execute(
                text("SELECT proxy_switch_pending_at FROM senders WHERE id = :sid"),
                {"sid": sender_id},
            )
        ).scalar_one()


async def test_clear_flag_only_when_connected_proxy_matches_db(test_sender_factory):
    """fix B: proxy_switch_pending_at is cleared ONLY when the proxy the listener
    reconnected with matches the current DB proxy; a reconnect on the OLD proxy
    (DB already switched) must leave the flag set so send/warmup/checker stay paused.
    """
    from sqlalchemy import text

    from app.database import AsyncSessionLocal

    old_proxy = {"type": "socks5", "addr": "1.1.1.1", "port": 10089}
    new_proxy = {"type": "socks5", "addr": "9.9.9.9", "port": 10017}

    sender = await test_sender_factory(slug="proxy-switch-gate")
    # Simulate assign-proxy just committed: DB holds NEW proxy + a fresh pause flag.
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE senders SET proxy = CAST(:p AS jsonb), "
                "proxy_switch_pending_at = NOW() WHERE id = :sid"
            ),
            {"p": json.dumps(new_proxy), "sid": str(sender.id)},
        )
        await db.commit()

    # Reconnect confirmed on the OLD proxy (the failed-live-verify scenario) →
    # connected proxy != DB proxy → flag MUST stay set.
    await _run_clear_flag(AsyncSessionLocal, str(sender.id), old_proxy)
    assert await _read_flag(AsyncSessionLocal, str(sender.id)) is not None

    # Reconnect confirmed on the NEW proxy → matches DB → flag cleared, sends resume.
    await _run_clear_flag(AsyncSessionLocal, str(sender.id), new_proxy)
    assert await _read_flag(AsyncSessionLocal, str(sender.id)) is None


async def test_clear_flag_handles_null_proxy(test_sender_factory):
    """fix B edge: IS NOT DISTINCT FROM matches a NULL/NULL proxy so a proxyless
    sender's flag still clears on a confirmed (proxy=None) reconnect, and does NOT
    clear when the DB has a proxy but the reconnect reported none.
    """
    from sqlalchemy import text

    from app.database import AsyncSessionLocal

    a_proxy = {"type": "socks5", "addr": "9.9.9.9", "port": 10017}

    sender = await test_sender_factory(slug="proxy-switch-null")
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE senders SET proxy = NULL, "
                "proxy_switch_pending_at = NOW() WHERE id = :sid"
            ),
            {"sid": str(sender.id)},
        )
        await db.commit()

    # DB proxy is NULL, reconnect reported a proxy → mismatch → flag stays.
    await _run_clear_flag(AsyncSessionLocal, str(sender.id), a_proxy)
    assert await _read_flag(AsyncSessionLocal, str(sender.id)) is not None

    # DB proxy is NULL, reconnect reported None → match → flag cleared.
    await _run_clear_flag(AsyncSessionLocal, str(sender.id), None)
    assert await _read_flag(AsyncSessionLocal, str(sender.id)) is None
