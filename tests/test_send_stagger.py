"""B4 — start/resume burst desync (`senders.send_stagger_until`).

Covers, in the order the feature is built:

1. Schema + config (Task 1)
   - `senders.send_stagger_until` exists, is `timestamp with time zone`, NULLABLE.
   - The `Sender` ORM exposes it and a fresh sender has it as `None`.
   - `send_stagger_window_seconds` defaults to 3600 (int).

2. Layout service wired into /start + /resume (Task 2)
   - start lays out DISTINCT markers on all eligible attached senders, inside
     `[NOW(), NOW()+W]`, one per `W/N` slot (even split with jitter, D-3).
   - resume RE-lays them (D-2: every transition to running).
   - ineligible attached senders keep `NULL` and consume no slot.
   - `send_stagger_window_seconds = 0` (kill switch, D-1) writes nothing.
   - N == 1 (D-6) writes nothing.

3. New-dialog-only gate in the send worker (Task 3)
   - baseline: NULL marker → a pending new-dialog item IS picked;
   - unexpired marker → NOT picked;
   - unexpired marker + prior sent row (follow-up) → IS picked (D-5, load-bearing);
   - expired marker → picked again;
   - kill switch → picked (gate bypassed by the `:stagger_on` bind);
   - PROTECTED regression: base interval 20-55s, fatigue, PACE_JITTER, the
     4/min-20/hour-150/day limits and `MAX_NEW_CONTACTS_PER_HOUR` unchanged, and
     the gate lives ONLY in `_process_next_for_sender` (not `_tick`, not
     `_check_rate_limits` — both would starve follow-ups).
"""

import inspect
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

import app.config as config_module
from app.config import get_settings
from app.models import Sender
from app.services import queue as queue_module
from app.services.queue import (
    MAX_NEW_CONTACTS_PER_HOUR,
    MAX_SEND_INTERVAL,
    MIN_SEND_INTERVAL,
    PACE_JITTER_HIGH,
    PACE_JITTER_LOW,
    SEND_INTERVAL_FATIGUE,
    QueueWorker,
)

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────────


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    """Bind a supabase user to the test workspace (mirrors test_campaign_router)."""
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _make_campaign(client, jwt, uid, agent_id, folder_id, sender_ids=None, name="B4"):
    payload = {
        "name": name,
        "agent_id": str(agent_id),
        "folder_id": str(folder_id),
        "sender_ids": [str(s) for s in (sender_ids or [])],
        "message_template": "Hi {{name}}",
    }
    r = await client.post("/api/v1/campaigns", json=payload,
                          headers={"Authorization": f"Bearer {jwt(sub=uid)}"})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["campaign"] if "campaign" in body and "warnings" in body else body


async def _stagger_offsets(db, sender_ids) -> dict:
    """{sender_id: offset_seconds_from_NOW or None} for the given senders."""
    rows = (await db.execute(text("""
        SELECT id,
               send_stagger_until,
               EXTRACT(EPOCH FROM (send_stagger_until - NOW())) AS offset_s
        FROM senders WHERE id = ANY(:ids)
    """), {"ids": [str(s) for s in sender_ids]})).fetchall()
    return {
        str(r.id): (None if r.send_stagger_until is None else float(r.offset_s))
        for r in rows
    }


@contextmanager
def _window_seconds(value: int):
    """Temporarily override `send_stagger_window_seconds` (Task 2 test 8 / Task 3
    test 14 — the D-1 kill switch).

    Mutates the ONE lru_cached Settings instance in place, because every caller
    reads the knob through `get_settings()` at call time. Deliberately does NOT
    call `get_settings.cache_clear()`: clearing would rebuild Settings from the
    environment and silently drop the override. The original value is restored in
    `finally`, so no other test can inherit the zeroed knob.
    """
    settings = config_module.get_settings()
    original = settings.send_stagger_window_seconds
    object.__setattr__(settings, "send_stagger_window_seconds", value)
    try:
        yield
    finally:
        object.__setattr__(settings, "send_stagger_window_seconds", original)


@contextmanager
def _pin_pacing(*, window_start_utc, frac, jitter=1.0):
    """Pin the two non-deterministic pacing inputs so the pace predicate is never
    the binding constraint in the gate tests (redefined locally on purpose — the
    original lives in tests/test_queue_even_pacing.py and is not imported across
    test modules)."""
    def _fixed(**_kwargs):
        return window_start_utc, frac

    with patch.object(queue_module, "_window_elapsed_fraction", _fixed), \
         patch.object(queue_module.random, "uniform", lambda _lo, _hi: jitter):
        yield


async def _insert_pending_item(
    db, *, workspace_id, sender_id, campaign_id, recipient_phone: str,
) -> str:
    """Insert a pending message_queue item with scheduled_at <= NOW()."""
    qid = str(uuid.uuid4())
    scheduled_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text, scheduled_at
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'pending', :rp, 'hello', :sa
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id), "rp": recipient_phone, "sa": scheduled_at,
    })
    await db.commit()
    return qid


async def _seed_sent_dialog(
    db, *, workspace_id, sender_id, campaign_id, recipient_phone: str,
) -> str:
    """Seed a status='sent' row with an explicit non-NULL finished_at, so the
    follow-up EXISTS branch (and the 24h counters) actually see it."""
    qid = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text,
            scheduled_at, finished_at
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'sent', :rp, 'prior', NOW(), NOW()
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id), "rp": recipient_phone,
    })
    await db.commit()
    return qid


async def _set_budget(db, *, campaign_id, cap: int = 100):
    """Raise the workspace level-1 new-chat budget high enough that the account
    budget cap never binds in the gate tests."""
    wid = (await db.execute(text(
        "SELECT workspace_id FROM campaigns WHERE id = :cid"
    ), {"cid": str(campaign_id)})).scalar()
    await db.execute(text("""
        INSERT INTO sender_grade_settings (workspace_id, level1_chats_per_day)
        VALUES (:wid, :cap)
        ON CONFLICT (workspace_id)
        DO UPDATE SET level1_chats_per_day = EXCLUDED.level1_chats_per_day
    """), {"wid": str(wid), "cap": cap})
    await db.commit()


async def _set_stagger(db, sender_id, sql_expr: str):
    await db.execute(text(
        f"UPDATE senders SET send_stagger_until = {sql_expr} WHERE id = :sid"
    ), {"sid": str(sender_id)})
    await db.commit()


async def _item_status(db, qid: str) -> str | None:
    row = (await db.execute(text(
        "SELECT status FROM message_queue WHERE id = :qid"
    ), {"qid": qid})).first()
    return row[0] if row else None


def _run_worker_capturing_picked(worker: QueueWorker):
    """Exercise the real candidate SELECT but never touch Telegram: rate limits →
    True, long-pause → None, _send_item → capture (mirrors
    tests/test_queue_new_dialog_limit.py)."""
    captured: dict = {"picked": []}

    async def _fake_send(item_id):
        captured["picked"].append(str(item_id))

    cm_rate = patch.object(worker, "_check_rate_limits", new=AsyncMock(return_value=True))
    cm_pause = patch.object(worker, "_get_long_pause_seconds", new=AsyncMock(return_value=None))
    cm_send = patch.object(worker, "_send_item", side_effect=_fake_send)
    return captured, cm_rate, cm_pause, cm_send


# ── Task 1: schema + config knob ─────────────────────────────────────────────


async def test_send_stagger_until_column_is_nullable_timestamptz(async_db_session):
    """Test 1 — migration 066 / ORM column shape."""
    row = (await async_db_session.execute(text("""
        SELECT data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'senders' AND column_name = 'send_stagger_until'
    """))).first()
    assert row is not None, "senders.send_stagger_until is missing"
    assert row.data_type == "timestamp with time zone"
    assert row.is_nullable == "YES"
    # No server_default and no python-side default (project-orm-default-vs-server-default-drift):
    # a nullable-no-default column cannot drift between create_all and the migration.
    assert row.column_default is None


async def test_sender_orm_exposes_send_stagger_until_defaulting_to_none(
    async_db_session, test_sender_factory
):
    """Test 2 — the ORM attribute exists and a fresh sender has no marker."""
    assert hasattr(Sender, "send_stagger_until")
    sender = await test_sender_factory()
    assert sender.send_stagger_until is None


async def test_send_stagger_window_seconds_default():
    """Test 3 — the config knob (D-1: the only kill switch)."""
    value = get_settings().send_stagger_window_seconds
    assert isinstance(value, int)
    assert value == 3600
