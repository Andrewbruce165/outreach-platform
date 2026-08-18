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


# ── Task 2: layout service wired into /start + /resume ───────────────────────


async def test_start_lays_out_distinct_markers_inside_window(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Test 4 — /start writes a marker on every eligible attached sender; the
    values are DISTINCT and all land inside [NOW(), NOW()+W]."""
    agent = await test_agent_factory()
    senders = [await test_sender_factory() for _ in range(4)]
    await _bind(async_db_session, test_workspace.id, "u-b4-start")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-b4-start",
                             agent.id, test_folder.id,
                             sender_ids=[s.id for s in senders], name="B4-start")

    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                                headers=_auth_headers(valid_supabase_jwt, "u-b4-start"))
    assert r.status_code == 200, r.text

    offsets = await _stagger_offsets(async_db_session, [s.id for s in senders])
    assert len(offsets) == 4
    assert all(v is not None for v in offsets.values()), offsets
    values = list(offsets.values())
    assert len(set(values)) == 4, f"markers collided: {values}"
    w = get_settings().send_stagger_window_seconds
    # Measured against a LATER NOW() than the UPDATE used, so allow a little slack
    # downward; the upper bound is structural (slot i < (i+1)*W/N <= W).
    assert all(-10.0 <= v <= w for v in values), values


async def test_start_layout_is_even_split_one_per_slot(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Test 5 — D-3 even split: the i-th smallest offset sits in its own
    [i*W/N, (i+1)*W/N] slot, i.e. the pool is spread, not clustered."""
    agent = await test_agent_factory()
    senders = [await test_sender_factory() for _ in range(4)]
    await _bind(async_db_session, test_workspace.id, "u-b4-slots")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-b4-slots",
                             agent.id, test_folder.id,
                             sender_ids=[s.id for s in senders], name="B4-slots")
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                                headers=_auth_headers(valid_supabase_jwt, "u-b4-slots"))
    assert r.status_code == 200, r.text

    offsets = sorted((await _stagger_offsets(async_db_session, [s.id for s in senders])).values())
    w = get_settings().send_stagger_window_seconds
    n = len(offsets)
    slot = w / n
    slack = 10.0
    for i, off in enumerate(offsets):
        assert i * slot - slack <= off <= (i + 1) * slot + slack, (
            f"offset #{i} = {off}s outside slot [{i * slot}, {(i + 1) * slot}]: {offsets}"
        )


async def test_resume_relays_the_stagger(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Test 6 — D-2: the stagger is re-laid on EVERY transition to running, so a
    resume (where the whole pool is due again) desyncs afresh."""
    agent = await test_agent_factory()
    senders = [await test_sender_factory() for _ in range(4)]
    sender_ids = [s.id for s in senders]
    await _bind(async_db_session, test_workspace.id, "u-b4-resume")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-b4-resume",
                             agent.id, test_folder.id,
                             sender_ids=sender_ids, name="B4-resume")
    h = _auth_headers(valid_supabase_jwt, "u-b4-resume")
    assert (await async_client.post(f"/api/v1/campaigns/{c['id']}/start", headers=h)).status_code == 200
    first = await _stagger_offsets(async_db_session, sender_ids)

    assert (await async_client.post(f"/api/v1/campaigns/{c['id']}/pause", headers=h)).status_code == 200
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/resume", headers=h)
    assert r.status_code == 200, r.text

    second = await _stagger_offsets(async_db_session, sender_ids)
    assert all(v is not None for v in second.values()), second
    assert len(set(second.values())) == 4, second
    w = get_settings().send_stagger_window_seconds
    assert all(-10.0 <= v <= w for v in second.values()), second
    assert any(second[k] != first[k] for k in second), (
        "resume did not re-lay the stagger", first, second
    )


async def test_ineligible_attached_sender_gets_no_marker_and_no_slot(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Test 7 — an attached-but-ineligible sender (spam_limited) keeps NULL and
    consumes no slot: the 3 remaining eligible senders still get a full split."""
    agent = await test_agent_factory()
    senders = [await test_sender_factory() for _ in range(4)]
    blocked = senders[0]
    eligible_ids = [s.id for s in senders[1:]]
    await _bind(async_db_session, test_workspace.id, "u-b4-inelig")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-b4-inelig",
                             agent.id, test_folder.id,
                             sender_ids=[s.id for s in senders], name="B4-inelig")
    await async_db_session.execute(text(
        "UPDATE senders SET restriction_status = 'spam_limited' WHERE id = :sid"
    ), {"sid": str(blocked.id)})
    await async_db_session.commit()

    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                                headers=_auth_headers(valid_supabase_jwt, "u-b4-inelig"))
    assert r.status_code == 200, r.text

    all_offsets = await _stagger_offsets(async_db_session, [s.id for s in senders])
    assert all_offsets[str(blocked.id)] is None, "ineligible sender was staggered"
    eligible_offsets = sorted(all_offsets[str(sid)] for sid in eligible_ids)
    assert all(v is not None for v in eligible_offsets), all_offsets
    assert len(set(eligible_offsets)) == 3, eligible_offsets

    # The split is over N=3 (the ineligible sender consumed no slot).
    w = get_settings().send_stagger_window_seconds
    slot = w / 3
    slack = 10.0
    for i, off in enumerate(eligible_offsets):
        assert i * slot - slack <= off <= (i + 1) * slot + slack, (i, off, eligible_offsets)


async def test_window_zero_disables_the_layout(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Test 8 — D-1 kill switch: W=0 writes nothing at all."""
    agent = await test_agent_factory()
    senders = [await test_sender_factory() for _ in range(3)]
    await _bind(async_db_session, test_workspace.id, "u-b4-off")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-b4-off",
                             agent.id, test_folder.id,
                             sender_ids=[s.id for s in senders], name="B4-off")
    with _window_seconds(0):
        r = await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                                    headers=_auth_headers(valid_supabase_jwt, "u-b4-off"))
        assert r.status_code == 200, r.text

    offsets = await _stagger_offsets(async_db_session, [s.id for s in senders])
    assert all(v is None for v in offsets.values()), offsets


async def test_single_eligible_sender_is_a_noop(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Test 9 — D-6: nothing to desync with N<2, and delaying a solo sender by up
    to an hour would just look like a broken start."""
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-b4-solo")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-b4-solo",
                             agent.id, test_folder.id,
                             sender_ids=[sender.id], name="B4-solo")
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                                headers=_auth_headers(valid_supabase_jwt, "u-b4-solo"))
    assert r.status_code == 200, r.text

    offsets = await _stagger_offsets(async_db_session, [sender.id])
    assert offsets[str(sender.id)] is None, offsets


async def test_stale_marker_cleared_when_sender_becomes_ineligible(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """A sender that carried a marker and has since gone ineligible must not keep
    a stale future timestamp — otherwise it would stay blocked for new dialogs
    after recovering."""
    agent = await test_agent_factory()
    senders = [await test_sender_factory() for _ in range(3)]
    await _bind(async_db_session, test_workspace.id, "u-b4-stale")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-b4-stale",
                             agent.id, test_folder.id,
                             sender_ids=[s.id for s in senders], name="B4-stale")
    h = _auth_headers(valid_supabase_jwt, "u-b4-stale")
    assert (await async_client.post(f"/api/v1/campaigns/{c['id']}/start", headers=h)).status_code == 200
    first = await _stagger_offsets(async_db_session, [s.id for s in senders])
    assert all(v is not None for v in first.values()), first

    await async_db_session.execute(text(
        "UPDATE senders SET restriction_status = 'spam_limited' WHERE id = :sid"
    ), {"sid": str(senders[0].id)})
    await async_db_session.commit()

    assert (await async_client.post(f"/api/v1/campaigns/{c['id']}/pause", headers=h)).status_code == 200
    assert (await async_client.post(f"/api/v1/campaigns/{c['id']}/resume", headers=h)).status_code == 200

    after = await _stagger_offsets(async_db_session, [s.id for s in senders])
    assert after[str(senders[0].id)] is None, ("stale marker survived", after)
