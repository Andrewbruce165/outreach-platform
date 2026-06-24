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


# ─── 2b. parse_spambot_limit_until ───────────────────────────────────────────


def test_parse_spambot_limit_until():
    from datetime import datetime, timezone

    from app.services.telegram import parse_spambot_limit_until

    # Real SpamBot wording (both "limited until" and "released on" appear).
    text = (
        "your account is now limited until 20 Jun 2026, 11:49 UTC. "
        "Your account will be automatically released on 20 Jun 2026, 11:49 UTC."
    )
    assert parse_spambot_limit_until(text) == datetime(2026, 6, 20, 11, 49, tzinfo=timezone.utc)
    # No-comma variant still parses.
    assert parse_spambot_limit_until("released on 1 Jan 2027 09:05 UTC") == datetime(
        2027, 1, 1, 9, 5, tzinfo=timezone.utc
    )
    # RU / unknown format → None (caller falls back to fixed interval).
    assert parse_spambot_limit_until("ваш аккаунт ограничен до 20 июн 2026") is None
    assert parse_spambot_limit_until("good news, no limits") is None
    assert parse_spambot_limit_until("") is None


# ─── 3. restriction reconcile tick ───────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def one(self):
        return self._rows[0]


# Phase 10: the restriction-audit helper (record_restriction_event) selects the
# sender row (workspace_id, proxy, rate_*, restricted_until) then INSERTs an event.
# The fake serves a minimal sender row so the helper runs inside the mocked tick.
_FAKE_SENDER_ROW = SimpleNamespace(
    workspace_id="ws-1", proxy=None,
    rate_per_min=4, rate_per_hour=20, rate_per_day=150,
    restricted_until=None,
)


class _FakeSession:
    """Records every execute() and serves SELECT rows for the restriction query."""

    def __init__(self, select_rows):
        self._select_rows = select_rows
        self.executed = []  # list of (sql_str, params)

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params))
        if "FROM senders" in sql and "restriction_status <> 'none'" in sql:
            # The batch reconcile SELECT (decides which senders to recheck).
            return _FakeResult(self._select_rows)
        if "SELECT restricted_until FROM senders" in sql:
            # Phase 10 B-1: per-sender intra-tx old_until read (D-01 gate).
            return _FakeResult([], scalar=None)
        if "FROM senders" in sql:
            # Phase 10: the helper's sender-row read (workspace_id/proxy/rate/...).
            return _FakeResult([_FAKE_SENDER_ROW])
        if "FROM messages_log" in sql:
            # Phase 10: the activity-slice counts.
            return _FakeResult([SimpleNamespace(s1=0, s24=0, u1=0, u24=0)])
        return _FakeResult([])

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _setup_listener(monkeypatch, select_rows, verdict, extra=None):
    from app.services import listener as listener_mod

    session = _FakeSession(select_rows)
    spambot_result = {"status": verdict, **(extra or {})}
    monkeypatch.setattr(listener_mod, "AsyncSessionLocal", MagicMock(return_value=session))
    monkeypatch.setattr(
        listener_mod.telegram_service,
        "check_spambot",
        AsyncMock(return_value=spambot_result),
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


async def test_restriction_tick_uses_spambot_release_date(monkeypatch):
    from datetime import datetime, timedelta, timezone

    # SpamBot quotes a release far in the future → sweep schedules recheck just after it.
    release = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    rows = [("sid-1", "s1", "spam_limited")]
    listener, session = _setup_listener(
        monkeypatch, rows, "limited", extra={"limit_until": release.isoformat()}
    )

    summary = await listener._restriction_reconcile_tick()

    assert summary["extended"] == 1
    # Find the UPDATE ... restricted_until = :next and check the param.
    next_params = [p for s, p in session.executed if p and "next" in p]
    assert next_params, "expected an UPDATE with :next param"
    assert next_params[0]["next"] == release + timedelta(minutes=5)


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
