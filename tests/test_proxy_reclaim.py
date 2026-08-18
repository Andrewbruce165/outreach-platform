"""Proxy-port reclaim sweep — free ports held by dead senders, spare live ones.

Runs the worker's exact set-based statement (`_RECLAIM_SQL`, imported from
app.services.proxy_reclaim) against the isolated test session so seed → sweep →
assertions live in one rolled-back transaction.

Covered behaviours:
- A `banned` sender's pool port is freed (assigned_to_sender_id → NULL) and its
  `proxy` JSON is cleared.
- A `session_expired` sender's port is likewise reclaimed.
- A healthy `ok` sender keeps both its port assignment and its proxy JSON.
- An inline-only proxy on a dead sender that holds NO pool row is left untouched
  (the sweep reclaims POOL ports only).
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

from app.services.proxy_reclaim import _RECLAIM_SQL

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _make_workspace(db) -> str:
    wid = str(_uuid.uuid4())
    await db.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:id, :n)"),
        {"id": wid, "n": f"WS {wid[:8]}"},
    )
    return wid


async def _make_sender(db, wid: str, slug: str, auth_status: str, with_proxy: bool) -> str:
    sid = str(_uuid.uuid4())
    proxy = (
        '{"type": "socks5", "host": "h", "port": 1080, "username": "u", "password": "p"}'
        if with_proxy
        else None
    )
    await db.execute(
        text(
            """
            INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                                 role, auth_status, lifecycle_status,
                                 rate_per_min, rate_per_hour, proxy)
            VALUES (:id, :wid, :slug, :name, :phone, 'stub',
                    'sender', :auth, 'active', 4, 20, CAST(:proxy AS jsonb))
            """
        ),
        {
            "id": sid, "wid": wid, "slug": slug, "name": slug,
            "phone": f"+790{abs(hash(sid)) % 10_000_000:07d}",
            "auth": auth_status, "proxy": proxy,
        },
    )
    return sid


async def _make_port(db, wid: str, port: int, sender_id: str | None) -> str:
    pid = str(_uuid.uuid4())
    await db.execute(
        text(
            """
            INSERT INTO proxy_pool (id, workspace_id, host, port, username, password,
                                    assigned_to_sender_id)
            VALUES (:id, :wid, 'h', :port, 'u', 'p', :sid)
            """
        ),
        {"id": pid, "wid": wid, "port": port, "sid": sender_id},
    )
    return pid


async def _port_owner(db, pid: str):
    return (
        await db.execute(
            text("SELECT assigned_to_sender_id FROM proxy_pool WHERE id = :id"),
            {"id": pid},
        )
    ).scalar_one()


async def _sender_proxy(db, sid: str):
    return (
        await db.execute(
            text("SELECT proxy FROM senders WHERE id = :id"), {"id": sid}
        )
    ).scalar_one()


# ── Reclaim: dead senders ────────────────────────────────────────────────────


async def test_banned_sender_port_reclaimed(async_db_session):
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "banned-1", "banned", with_proxy=True)
    pid = await _make_port(db, wid, 1001, sid)

    await db.execute(_RECLAIM_SQL, {"qdays": 7})

    assert await _port_owner(db, pid) is None, "banned sender's port must be freed"
    assert await _sender_proxy(db, sid) is None, "banned sender's proxy must be cleared"
    quarantined = (await db.execute(
        text("SELECT quarantined_until FROM proxy_pool WHERE id = :id"), {"id": pid}
    )).scalar_one()
    assert quarantined is not None, "reclaimed port must be quarantined (H4)"


async def test_session_expired_sender_port_reclaimed(async_db_session):
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "expired-1", "session_expired", with_proxy=True)
    pid = await _make_port(db, wid, 1002, sid)

    await db.execute(_RECLAIM_SQL, {"qdays": 7})

    assert await _port_owner(db, pid) is None, "expired sender's port must be freed"
    assert await _sender_proxy(db, sid) is None


# ── Spare: live senders ──────────────────────────────────────────────────────


async def test_healthy_sender_port_kept(async_db_session):
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "ok-1", "ok", with_proxy=True)
    pid = await _make_port(db, wid, 1003, sid)

    await db.execute(_RECLAIM_SQL, {"qdays": 7})

    assert await _port_owner(db, pid) == _uuid.UUID(sid), "healthy sender keeps its port"
    assert await _sender_proxy(db, sid) is not None, "healthy sender keeps its proxy"


async def test_dead_sender_without_pool_row_untouched(async_db_session):
    """An inline-only proxy on a dead sender that holds no pool row is not cleared."""
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "banned-inline", "banned", with_proxy=True)
    # No _make_port for this sender.

    reclaimed = (await db.execute(_RECLAIM_SQL, {"qdays": 7})).rowcount

    assert await _sender_proxy(db, sid) is not None, "no pool row → proxy left as-is"
    assert reclaimed == 0
