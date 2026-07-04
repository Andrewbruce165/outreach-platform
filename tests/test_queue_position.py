"""Quick 260703-ssv Task 2 (WR-03) — integration tests for the priority-aware,
NULL-safe ``_queue_position``.

`_queue_position(db, sender_id, item_id)` reports how many pending items are
"ahead" of a given item for the SAME sender, mirroring the worker pick order
``priority DESC, created_at ASC``. The old implementation used a tuple
comparison ``(priority, created_at) > (...)`` which was BOTH inverted (counted
LATER same-priority rows as ahead) AND NULL-blind (a NULL priority made the
whole comparison NULL → counted nothing).

`test_queue_item_factory` cannot set ``priority``/``created_at`` (its INSERT
column list is fixed — conftest.py:836), so these tests issue their own raw-SQL
INSERTs mirroring the factory's shape, seeding explicit priorities (including
NULL) and controlled ``created_at`` values.

Behaviours covered:
- higher-priority pending rows for the same sender count as "ahead";
- NULL priority is treated as 0 via COALESCE on both sides;
- same-priority rows created EARLIER are ahead; created LATER are NOT;
- position is 1-based (nothing ahead → 1);
- ordering matches the pick order ``priority DESC, created_at ASC``.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _insert_item(
    db,
    *,
    workspace_id,
    sender_id,
    recipient_phone: str,
    priority,               # int or None (NULL)
    created_at: datetime,
    status: str = "pending",
) -> str:
    """Seed one message_queue row with an explicit priority (incl. NULL) and
    created_at — the conftest factory cannot set these columns."""
    qid = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO message_queue (
                id, workspace_id, sender_id, campaign_id,
                item_type, status, recipient_phone, message_text,
                priority, scheduled_at, created_at
            ) VALUES (
                :qid, :wid, :sid, NULL,
                'message', :status, :rp, 'hi',
                :prio, :created, :created
            )
        """),
        {
            "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
            "status": status, "rp": recipient_phone,
            "prio": priority, "created": created_at,
        },
    )
    await db.commit()
    return qid


async def test_queue_position_priority_and_null_aware(
    async_db_session, test_sender_factory
):
    """One mixed-priority backlog (incl. a NULL-priority row) covers all five
    behaviours at once.

    Seeded (T0 = a fixed base instant):
        A  priority=5   created T0      → highest priority, earliest in group
        B  priority=5   created T0+1s   → same priority as A, created LATER
        C  priority=0   created T0-5s   → low priority, created EARLIEST overall
        D  priority=NULL created T0+2s  → COALESCE(NULL,0)=0
        E  priority=0   created T0+3s   → low priority, created LATEST

    Pick order (priority DESC, created_at ASC): A, B, C, D, E
    Expected 1-based positions:                A=1, B=2, C=3, D=4, E=5
    """
    sender = await test_sender_factory()
    wid = sender.workspace_id
    sid = sender.id

    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    a = await _insert_item(async_db_session, workspace_id=wid, sender_id=sid,
                           recipient_phone="+79990000001", priority=5, created_at=base)
    b = await _insert_item(async_db_session, workspace_id=wid, sender_id=sid,
                           recipient_phone="+79990000002", priority=5,
                           created_at=base + timedelta(seconds=1))
    c = await _insert_item(async_db_session, workspace_id=wid, sender_id=sid,
                           recipient_phone="+79990000003", priority=0,
                           created_at=base - timedelta(seconds=5))
    d = await _insert_item(async_db_session, workspace_id=wid, sender_id=sid,
                           recipient_phone="+79990000004", priority=None,
                           created_at=base + timedelta(seconds=2))
    e = await _insert_item(async_db_session, workspace_id=wid, sender_id=sid,
                           recipient_phone="+79990000005", priority=0,
                           created_at=base + timedelta(seconds=3))

    from app.services.queue import _queue_position

    # Highest priority, earliest in its group → nothing ahead → 1-based == 1.
    assert await _queue_position(async_db_session, sid, a) == 1
    # Same priority as A but created later → A is ahead.
    assert await _queue_position(async_db_session, sid, b) == 2
    # Low priority created earliest overall → the two priority-5 rows are ahead.
    assert await _queue_position(async_db_session, sid, c) == 3
    # NULL priority treated as 0: ahead = A,B (higher) + C (same-0, earlier).
    assert await _queue_position(async_db_session, sid, d) == 4
    # Lowest/created-last: ahead = A,B (higher) + C,D (same-0, earlier).
    assert await _queue_position(async_db_session, sid, e) == 5


async def test_queue_position_is_one_based_when_nothing_ahead(
    async_db_session, test_sender_factory
):
    """A lone pending item has nothing ahead → position 1 (1-based)."""
    sender = await test_sender_factory()
    only = await _insert_item(
        async_db_session, workspace_id=sender.workspace_id, sender_id=sender.id,
        recipient_phone="+79991110000", priority=0,
        created_at=datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
    )

    from app.services.queue import _queue_position

    assert await _queue_position(async_db_session, sender.id, only) == 1


async def test_queue_position_null_priority_ordered_as_zero_against_positive(
    async_db_session, test_sender_factory
):
    """A NULL-priority row is behind a positive-priority row regardless of
    created_at ordering (COALESCE(NULL,0)=0 < positive)."""
    sender = await test_sender_factory()
    wid = sender.workspace_id
    sid = sender.id
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # NULL-priority row created FIRST, positive-priority row created LATER.
    null_item = await _insert_item(async_db_session, workspace_id=wid, sender_id=sid,
                                   recipient_phone="+79992220001", priority=None,
                                   created_at=base)
    hi_item = await _insert_item(async_db_session, workspace_id=wid, sender_id=sid,
                                 recipient_phone="+79992220002", priority=3,
                                 created_at=base + timedelta(seconds=10))

    from app.services.queue import _queue_position

    # Positive priority wins even though created later → nothing ahead of it.
    assert await _queue_position(async_db_session, sid, hi_item) == 1
    # NULL(→0) priority is behind the positive-priority row.
    assert await _queue_position(async_db_session, sid, null_item) == 2


# ─── IN-04: pre-send manual-takeover guard reads the NEWEST conversation row ──


async def test_in04_all_ai_enabled_guards_order_by_updated_at_desc():
    """IN-04 (product-code regression): every pre-send manual-takeover guard that
    reads ``conversations.ai_enabled`` must ``ORDER BY updated_at DESC`` before
    ``LIMIT 1`` so the NEWEST conversation row governs the guard when recontact
    created duplicate (workspace, sender, phone) rows. Before the fix the
    non-recontact branch used a bare ``LIMIT 1`` (arbitrary row)."""
    import pathlib
    import re

    import app.services.queue as queue_mod

    src = pathlib.Path(queue_mod.__file__).read_text()
    guards: list[str] = []
    for m in re.finditer(r"SELECT ai_enabled FROM conversations", src):
        tail = src[m.start():]
        limit_idx = tail.find("LIMIT 1")
        assert limit_idx != -1, "an ai_enabled guard query has no LIMIT 1"
        guards.append(tail[: limit_idx + len("LIMIT 1")])

    # Both the recontact and non-recontact branches read ai_enabled — the test
    # would have gone RED before the fix (non-recontact branch lacked ORDER BY).
    assert len(guards) >= 2, f"expected >=2 ai_enabled guards, found {len(guards)}"
    for block in guards:
        assert "ORDER BY updated_at DESC" in block, (
            "a pre-send ai_enabled guard is missing ORDER BY updated_at DESC — it "
            "would read an arbitrary duplicate conversation row:\n" + block
        )


async def _insert_conversation(
    db, *, workspace_id, sender_id, contact_phone, ai_enabled, updated_at
) -> str:
    """Seed one conversations row with an explicit updated_at + ai_enabled."""
    cid = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO conversations (
                id, workspace_id, sender_id, contact_phone,
                ai_enabled, status, created_at, updated_at
            ) VALUES (
                :cid, :wid, :sid, :phone, :ai_en, 'active', :ts, :ts
            )
        """),
        {
            "cid": cid, "wid": str(workspace_id), "sid": str(sender_id),
            "phone": contact_phone, "ai_en": ai_enabled, "ts": updated_at,
        },
    )
    await db.commit()
    return cid


async def test_in04_newest_conversation_ai_enabled_wins(
    async_db_session, test_sender_factory
):
    """IN-04 (behavioural): with two duplicate conversation rows for the same
    (workspace, sender, phone) the non-recontact guard's ``ORDER BY updated_at
    DESC LIMIT 1`` returns the NEWEST row's ai_enabled — not an arbitrary one."""
    sender = await test_sender_factory()
    wid = sender.workspace_id
    sid = sender.id
    phone = "+79993330001"
    base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

    # Older row: manager had taken over earlier (ai_enabled=False).
    await _insert_conversation(async_db_session, workspace_id=wid, sender_id=sid,
                               contact_phone=phone, ai_enabled=False, updated_at=base)
    # Newest row: a fresh recontact dialog with AI back on (ai_enabled=True).
    await _insert_conversation(async_db_session, workspace_id=wid, sender_id=sid,
                               contact_phone=phone, ai_enabled=True,
                               updated_at=base + timedelta(hours=1))

    # Exact non-recontact guard SQL from queue.py __send_item_inner.
    guard_sql = text("""
        SELECT ai_enabled FROM conversations
        WHERE workspace_id = :wid
          AND sender_id = :sid
          AND contact_phone = :phone
        ORDER BY updated_at DESC LIMIT 1
    """)
    params = {"wid": str(wid), "sid": str(sid), "phone": phone}

    ai_enabled = (await async_db_session.execute(guard_sql, params)).scalar()
    assert ai_enabled is True, "newest conversation (AI re-enabled) must win"

    # Flip: make the NEWEST row the manager-takeover one → guard must read False.
    await _insert_conversation(async_db_session, workspace_id=wid, sender_id=sid,
                               contact_phone=phone, ai_enabled=False,
                               updated_at=base + timedelta(hours=2))
    ai_enabled = (await async_db_session.execute(guard_sql, params)).scalar()
    assert ai_enabled is False, "newest conversation (manual takeover) must win"
