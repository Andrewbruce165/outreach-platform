"""Unit tests for sender write-restriction (migration 028).

Covers the four pieces of the spam-limit / freeze feature:
1. _derive_status precedence (error > frozen > limited > lifecycle).
2. telegram.is_frozen_error() FROZEN_* matching.
3. listener._restriction_reconcile_tick() — clear / extend / ban on SpamBot verdict.
4. Source-shape guards that queue.py writes restriction on PEER_FLOOD / ACCOUNT_FROZEN
   and skips restricted senders pre-send.

These avoid a live Telegram/DB by mocking AsyncSessionLocal + check_spambot, mirroring
test_listener_reconcile.py's approach.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# asyncio_mode=auto (pyproject) runs async tests without an explicit marker.


# ─── 1. _derive_status precedence ────────────────────────────────────────────


def _sender(auth="ok", restriction="none", lifecycle="active"):
    return SimpleNamespace(
        auth_status=auth, restriction_status=restriction, lifecycle_status=lifecycle
    )


def test_derive_status_matrix():
    from app.routers.senders import _derive_status

    # auth error wins over everything.
    assert _derive_status(_sender(auth="banned", restriction="frozen")) == "error"
    assert _derive_status(_sender(auth="session_expired")) == "error"
    # frozen beats limited beats lifecycle.
    assert _derive_status(_sender(restriction="frozen")) == "frozen"
    assert _derive_status(_sender(restriction="spam_limited")) == "limited"
    assert _derive_status(_sender(restriction="frozen", lifecycle="paused")) == "frozen"
    # no restriction → passthrough lifecycle.
    assert _derive_status(_sender(lifecycle="active")) == "active"
    assert _derive_status(_sender(lifecycle="warmup")) == "warmup"
    assert _derive_status(_sender(lifecycle="paused")) == "paused"


# ─── 2. is_frozen_error ──────────────────────────────────────────────────────


def test_is_frozen_error():
    from app.services.telegram import is_frozen_error

    assert is_frozen_error(Exception("FROZEN_METHOD_INVALID"))
    assert is_frozen_error(Exception("rpc error 420: FROZEN_PARTICIPANT_MISSING"))
    assert is_frozen_error(Exception("frozen_method_invalid"))  # case-insensitive
    assert not is_frozen_error(Exception("PEER_FLOOD"))
    assert not is_frozen_error(Exception("FLOOD_WAIT_42"))
    assert not is_frozen_error(Exception("network unreachable"))


# ─── 3. restriction reconcile tick ───────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Records every execute() and serves SELECT rows for the restriction query."""

    def __init__(self, select_rows):
        self._select_rows = select_rows
        self.executed = []  # list of (sql_str, params)

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params))
        if "FROM senders" in sql and "restriction_status <> 'none'" in sql:
            return _FakeResult(self._select_rows)
        return _FakeResult([])

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _setup_listener(monkeypatch, select_rows, verdict):
    from app.services import listener as listener_mod

    session = _FakeSession(select_rows)
    monkeypatch.setattr(listener_mod, "AsyncSessionLocal", MagicMock(return_value=session))
    monkeypatch.setattr(
        listener_mod.telegram_service,
        "check_spambot",
        AsyncMock(return_value={"status": verdict}),
    )

    listener = listener_mod.TelegramListener()
    listener.clients["s1"] = MagicMock()  # connected → tick will check it
    return listener, session


async def test_restriction_tick_clears_on_free(monkeypatch):
    rows = [("sid-1", "s1", "spam_limited")]
    listener, session = _setup_listener(monkeypatch, rows, "free")

    summary = await listener._restriction_reconcile_tick()

    assert summary == {"checked": 1, "cleared": 1, "extended": 0, "banned": 0, "skipped": 0}
    sqls = " ".join(s for s, _ in session.executed)
    assert "restriction_status = 'none'" in sqls
    # free also un-pauses the sender's paused queue items.
    assert "UPDATE message_queue" in sqls and "scheduled_at = NOW()" in sqls


async def test_restriction_tick_extends_on_limited(monkeypatch):
    rows = [("sid-1", "s1", "spam_limited")]
    listener, session = _setup_listener(monkeypatch, rows, "limited")

    summary = await listener._restriction_reconcile_tick()

    assert summary["extended"] == 1 and summary["cleared"] == 0
    sqls = " ".join(s for s, _ in session.executed)
    assert "restricted_until = :next" in sqls
    assert "restriction_status = 'none'" not in sqls  # NOT cleared


async def test_restriction_tick_bans_on_suspended(monkeypatch):
    rows = [("sid-1", "s1", "frozen")]
    listener, session = _setup_listener(monkeypatch, rows, "suspended")

    summary = await listener._restriction_reconcile_tick()

    assert summary["banned"] == 1
    sqls = " ".join(s for s, _ in session.executed)
    assert "auth_status = 'banned'" in sqls


async def test_restriction_tick_skips_disconnected(monkeypatch):
    rows = [("sid-1", "s1", "spam_limited")]
    listener, session = _setup_listener(monkeypatch, rows, "free")
    listener.clients.clear()  # not connected this tick

    summary = await listener._restriction_reconcile_tick()

    assert summary == {"checked": 0, "cleared": 0, "extended": 0, "banned": 0, "skipped": 1}


# ─── 4. queue.py source-shape guards ─────────────────────────────────────────


def test_queue_flags_restriction_on_send_errors():
    from app.services.queue import QueueWorker

    src = inspect.getsource(QueueWorker)  # send-error branches live in __send_item_inner
    # PEER_FLOOD writes spam_limited; new ACCOUNT_FROZEN branch writes frozen.
    assert "restriction_status = 'spam_limited'" in src
    assert 'error_code == "ACCOUNT_FROZEN"' in src
    assert "restriction_status = 'frozen'" in src


def test_queue_pre_send_skips_restricted():
    from app.services.queue import QueueWorker

    src = inspect.getsource(QueueWorker._check_rate_limits)
    assert 'restriction_status != "none"' in src
