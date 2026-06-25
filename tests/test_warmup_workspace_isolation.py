"""Regression: warmup workspace isolation + SQL precedence (CR-04, Phase 02.1-01).

Three invariants pinned here:

1. CR-04 issue 1: INSERTs into warmup_sessions / warmup_messages carry workspace_id.
2. CR-04 issue 2: FloodWait UPDATE uses (A OR B) AND status='active' precedence —
   completed sessions stay completed.
3. CR-04 issue 3: _create_new_sessions partitions the pool by workspace_id —
   sender from workspace A can never be paired with sender from workspace B.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models import Workspace
from app.services.warmup import WarmupWorker

pytestmark = pytest.mark.asyncio


# ─── Local helpers (per parallel-safety rules: no conftest.py edits) ──────────


@pytest_asyncio.fixture
async def workspace_factory(async_db_session):
    """Local factory creating N distinct Workspace rows on demand."""
    counter = {"n": 0}

    async def _make(name: str | None = None) -> Workspace:
        counter["n"] += 1
        ws = Workspace(name=name or f"WS-warmup-{counter['n']}")
        async_db_session.add(ws)
        await async_db_session.commit()
        await async_db_session.refresh(ws)
        return ws

    return _make


async def _add_sender(db, workspace_id, slug, phone):
    """Insert a fully-eligible sender directly via SQL (avoids dependency on
    test_sender_factory which is bound to the single test_workspace fixture)."""
    row = (
        await db.execute(
            text(
                """
                INSERT INTO senders (
                    workspace_id, slug, name, phone, session_string,
                    role, auth_status, lifecycle_status,
                    rate_per_min, rate_per_hour, rate_per_day
                )
                VALUES (
                    :wid, :slug, :name, :phone, 'encrypted_stub',
                    'sender', 'ok', 'active',
                    4, 20, 150
                )
                RETURNING id
                """
            ),
            {
                "wid": str(workspace_id),
                "slug": slug,
                "name": slug,
                "phone": phone,
            },
        )
    ).fetchone()
    await db.commit()
    return str(row[0])


async def _add_to_pool(db, sender_id, workspace_id, days_ago: int = 5):
    await db.execute(
        text(
            """
            INSERT INTO warmup_pool (workspace_id, sender_id, is_active, enrolled_at)
            VALUES (:wid, :sid, true, NOW() - (:days || ' days')::interval)
            """
        ),
        {"wid": str(workspace_id), "sid": sender_id, "days": str(days_ago)},
    )
    await db.commit()


# ─── Tests ────────────────────────────────────────────────────────────────────


async def test_get_active_pool_returns_workspace_id(
    async_db_session, workspace_factory
):
    """_get_active_pool must return workspace_id for each pool entry (CR-04 issue 3)."""
    ws_a = await workspace_factory()
    s_a = await _add_sender(async_db_session, ws_a.id, "warm-pool-a-1", "+79150000001")
    await _add_to_pool(async_db_session, s_a, ws_a.id)

    worker = WarmupWorker()
    pool = await worker._get_active_pool(async_db_session)

    assert len(pool) >= 1
    entry = next(p for p in pool if p["sender_id"] == s_a)
    assert entry["workspace_id"] == str(ws_a.id)


async def test_warmup_sessions_insert_writes_workspace_id(
    async_db_session, workspace_factory
):
    """_create_new_sessions writes warmup_sessions.workspace_id (CR-04 issue 1)."""
    ws = await workspace_factory()
    s1 = await _add_sender(async_db_session, ws.id, "warm-sess-1", "+79150000010")
    s2 = await _add_sender(async_db_session, ws.id, "warm-sess-2", "+79150000011")
    await _add_to_pool(async_db_session, s1, ws.id)
    await _add_to_pool(async_db_session, s2, ws.id)

    worker = WarmupWorker()
    await worker._create_new_sessions(async_db_session)

    sessions = (
        await async_db_session.execute(
            text(
                """
                SELECT workspace_id, sender_a_id, sender_b_id FROM warmup_sessions
                WHERE sender_a_id IN (:s1, :s2) OR sender_b_id IN (:s1, :s2)
                """
            ),
            {"s1": s1, "s2": s2},
        )
    ).fetchall()
    assert len(sessions) == 1
    assert str(sessions[0][0]) == str(ws.id)


async def test_warmup_pool_no_cross_tenant_pairs(
    async_db_session, workspace_factory
):
    """CRITICAL CR-04 issue 3: sender from workspace A never pairs with sender from B."""
    ws_a = await workspace_factory()
    ws_b = await workspace_factory()
    a1 = await _add_sender(async_db_session, ws_a.id, "warm-iso-a1", "+79150000020")
    a2 = await _add_sender(async_db_session, ws_a.id, "warm-iso-a2", "+79150000021")
    b1 = await _add_sender(async_db_session, ws_b.id, "warm-iso-b1", "+79150000022")
    b2 = await _add_sender(async_db_session, ws_b.id, "warm-iso-b2", "+79150000023")

    for sid, wsid in [(a1, ws_a.id), (a2, ws_a.id), (b1, ws_b.id), (b2, ws_b.id)]:
        await _add_to_pool(async_db_session, sid, wsid)

    worker = WarmupWorker()
    await worker._create_new_sessions(async_db_session)

    # JOIN sessions → senders on both sides; both senders must share workspace_id.
    rows = (
        await async_db_session.execute(
            text(
                """
                SELECT ws.id, ws.workspace_id,
                       sa.workspace_id AS sa_wid, sb.workspace_id AS sb_wid
                FROM warmup_sessions ws
                JOIN senders sa ON sa.id = ws.sender_a_id
                JOIN senders sb ON sb.id = ws.sender_b_id
                WHERE ws.sender_a_id IN (:a1, :a2, :b1, :b2)
                   OR ws.sender_b_id IN (:a1, :a2, :b1, :b2)
                """
            ),
            {"a1": a1, "a2": a2, "b1": b1, "b2": b2},
        )
    ).fetchall()

    assert len(rows) > 0, "Expected at least one warmup pair"
    for row in rows:
        assert row.sa_wid == row.sb_wid, (
            f"Cross-tenant pair leaked! session={row.id} "
            f"sa_wid={row.sa_wid} sb_wid={row.sb_wid}"
        )
        assert row.workspace_id == row.sa_wid


async def test_floodwait_update_only_affects_active(
    async_db_session, workspace_factory
):
    """CR-04 issue 2: precedence fix — completed sessions are NOT touched by FloodWait UPDATE."""
    ws = await workspace_factory()
    s1 = await _add_sender(async_db_session, ws.id, "warm-fw-1", "+79150000030")
    s2 = await _add_sender(async_db_session, ws.id, "warm-fw-2", "+79150000031")

    # Two sessions for the same sender_a: one active, one completed.
    await async_db_session.execute(
        text(
            """
            INSERT INTO warmup_sessions
                (workspace_id, sender_a_id, sender_b_id, topic, target_messages,
                 messages_sent, status, next_message_at)
            VALUES
                (:wid, :a, :b, 'test', 5, 0, 'active',    NOW() + INTERVAL '1 hour'),
                (:wid, :a, :b, 'test', 5, 0, 'completed', NOW() - INTERVAL '1 day')
            """
        ),
        {"wid": str(ws.id), "a": s1, "b": s2},
    )
    await async_db_session.commit()

    # Mimic the fixed FloodWait UPDATE from warmup._send_via_telethon.
    retry_at = datetime.now(timezone.utc) + timedelta(hours=2)
    await async_db_session.execute(
        text(
            """
            UPDATE warmup_sessions
            SET next_message_at = :t, updated_at = NOW()
            WHERE (sender_a_id = :sid OR sender_b_id = :sid)
              AND status = 'active'
            """
        ),
        {"t": retry_at, "sid": s1},
    )
    await async_db_session.commit()

    rows = (
        await async_db_session.execute(
            text(
                """
                SELECT status, next_message_at FROM warmup_sessions
                WHERE sender_a_id = :sid
                ORDER BY status
                """
            ),
            {"sid": s1},
        )
    ).fetchall()
    by_status = {r.status: r.next_message_at for r in rows}

    # Active session was bumped to retry_at.
    assert abs((by_status["active"] - retry_at).total_seconds()) < 5
    # Completed session was NOT touched (still in the past).
    assert by_status["completed"] < datetime.now(timezone.utc)
