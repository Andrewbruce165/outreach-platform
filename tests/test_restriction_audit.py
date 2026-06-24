"""Phase 10 — Restriction-audit event-log tests (Wave 0 RED stubs).

These tests fully ASSERT the behaviour of the restriction-audit machinery that
Plan 10-02 (helper + migration 030 + write-point wiring) and Plan 10-03 (history
endpoint) will implement. None of it exists yet, so the production import is done
INSIDE each test body — that keeps `pytest --collect-only` clean (no top-level
ImportError collection error) while the tests still ERROR/FAIL RED at run time.
This is the expected Wave-0 state per 10-VALIDATION.md (pattern lifted from
tests/test_failover.py:1 / tests/test_rebalance.py:51).

Helper under test (signature fixed here, implemented in 10-02 — see 10-RESEARCH.md
§Proposed Table & Schema and the <interfaces> block of 10-01-PLAN.md):

    async def record_restriction_event(
        sender_id, event_type, source, restricted_until, raw_text,
        category="restriction", db=None,
    ) -> None:
        # Writes one row in sender_restriction_events (append-only) and, for
        # restriction-category events, computes activity_slice + proxy snapshot
        # in the SAME transaction. db passed → transaction-neutral, caller commits.

    event_type ∈ {spam_limited, frozen, flood_wait, cleared, banned, extension,
                  privacy_restricted}
    source     ∈ {queue_error, spambot_reconcile, antispam_signal}
    category   ∈ {restriction, recipient_privacy}

Table (migration 030): sender_restriction_events
    (id, workspace_id, sender_id, category, event_type, source,
     restricted_until, raw_text, activity_slice JSONB, proxy JSONB, created_at)

activity_slice JSONB shape (10-RESEARCH.md:217):
    {sends_1h, sends_24h, unique_contacts_1h, unique_contacts_24h,
     rate: {configured_per_min/hour/day, actual_per_hour, actual_per_day}}

Read endpoint (10-03): GET /senders/{slug}/restriction-events → newest-first,
workspace-scoped.

Test → requirement map (contract — names consumed by later verify commands):
- test_peer_flood_writes_event              → HLTH-01a (PEER_FLOOD → spam_limited/queue_error)
- test_reconcile_cleared_writes_event       → HLTH-01b (reconcile free → cleared)
- test_reconcile_no_shift_no_event          → HLTH-01c / D-01 (no forward shift → NO event)
- test_reconcile_shift_writes_extension     → HLTH-01d / D-01 (forward shift → ONE extension)
- test_events_append_only                   → HLTH-01e (history not overwritten)
- test_event_carries_activity_slice         → HLTH-02a (slice computed at write time)
- test_event_carries_proxy_snapshot         → HLTH-02b (proxy snapshot from senders.proxy)
- test_slice_windows_sent_only              → HLTH-02c (sent-only, windowed correctly)
- test_recipient_privacy_separate_category  → D-03 (recipient_privacy category, no status flip)
- test_history_endpoint                     → HLTH-03 (workspace-scoped, newest-first)
"""

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ─── Local freeze-state helper (mirrors tests/test_failover.py:62-70) ─────────

async def _freeze_sender(db, sender_id, status: str = "spam_limited", until=None):
    """Flag a sender restricted exactly as the freeze paths do (queue.py / listener.py).

    `until` defaults to NOW()+24h when not given; pass an explicit timestamp to
    drive the reconcile date-shift assertions (D-01).
    """
    if until is None:
        await db.execute(text("""
            UPDATE senders
            SET restriction_status = :st,
                restricted_until = NOW() + INTERVAL '24 hours'
            WHERE id = :sid
        """), {"st": status, "sid": str(sender_id)})
    else:
        await db.execute(text("""
            UPDATE senders
            SET restriction_status = :st, restricted_until = :until
            WHERE id = :sid
        """), {"st": status, "until": until, "sid": str(sender_id)})
    await db.commit()


async def _event_rows(db, sender_id, **filters):
    """All sender_restriction_events rows for a sender, optional equality filters."""
    where = ["sender_id = :sid"]
    params = {"sid": str(sender_id)}
    for k, v in filters.items():
        where.append(f"{k} = :{k}")
        params[k] = v
    rows = (await db.execute(text(
        "SELECT id, workspace_id, sender_id, category, event_type, source, "
        "restricted_until, raw_text, activity_slice, proxy, created_at "
        "FROM sender_restriction_events WHERE " + " AND ".join(where) +
        " ORDER BY created_at"
    ), params)).all()
    return [dict(r._mapping) for r in rows]


async def _restriction_status(db, sender_id) -> str:
    return (await db.execute(text(
        "SELECT restriction_status FROM senders WHERE id = :sid"
    ), {"sid": str(sender_id)})).scalar_one()


async def _seed_sent(db, workspace_id, sender_id, phone, *, minutes_ago: int,
                     message_type: str = "sent"):
    """Insert a messages_log row at NOW()-minutes_ago for slice windowing tests."""
    await db.execute(text("""
        INSERT INTO messages_log (
            id, workspace_id, sender_id, recipient_phone, message_text,
            message_type, created_at
        ) VALUES (
            :id, :wid, :sid, :phone, 'slice seed', :mtype,
            NOW() - (:mins || ' minutes')::interval
        )
    """), {
        "id": str(uuid.uuid4()), "wid": str(workspace_id), "sid": str(sender_id),
        "phone": phone, "mtype": message_type, "mins": str(minutes_ago),
    })
    await db.commit()


# ─── HLTH-01a ─────────────────────────────────────────────────────────────────

async def test_peer_flood_writes_event(
    async_db_session, test_running_campaign_factory,
):
    """HLTH-01a: a PEER_FLOOD restriction writes exactly one append-only event row
    with category='restriction', event_type='spam_limited', source='queue_error'."""
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]

    await record_restriction_event(
        sender_id=sender.id,
        event_type="spam_limited",
        source="queue_error",
        restricted_until=None,
        raw_text="PEER_FLOOD",
        db=async_db_session,
    )
    await async_db_session.commit()

    rows = await _event_rows(async_db_session, sender.id)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["category"] == "restriction"
    assert row["event_type"] == "spam_limited"
    assert row["source"] == "queue_error"
    assert row["raw_text"] == "PEER_FLOOD"


# ─── HLTH-01b ─────────────────────────────────────────────────────────────────

async def test_reconcile_cleared_writes_event(
    async_db_session, test_running_campaign_factory,
):
    """HLTH-01b: the @SpamBot-reconcile 'free' branch writes a 'cleared' event with
    source='spambot_reconcile' and restricted_until IS NULL."""
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]

    await record_restriction_event(
        sender_id=sender.id,
        event_type="cleared",
        source="spambot_reconcile",
        restricted_until=None,
        raw_text="Good news, no limits are currently applied to your account.",
        db=async_db_session,
    )
    await async_db_session.commit()

    rows = await _event_rows(async_db_session, sender.id, event_type="cleared")
    assert len(rows) == 1, rows
    assert rows[0]["source"] == "spambot_reconcile"
    assert rows[0]["restricted_until"] is None


# ─── HLTH-01c (D-01) ──────────────────────────────────────────────────────────

async def test_reconcile_no_shift_no_event(
    async_db_session, test_running_campaign_factory,
):
    """HLTH-01c / D-01: a still-limited reconcile tick that does NOT move
    restricted_until forward writes NO 'extension' event (suppress 37/day noise).

    The helper compares old vs new restricted_until; a same-value (or earlier)
    recheck must not emit. After the no-shift call, COUNT(extension) == 0.
    """
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]

    # Sender is already spam_limited until a fixed release date.
    same_until = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '12 hours'")
    )).scalar_one()
    await _freeze_sender(async_db_session, sender.id, "spam_limited", until=same_until)

    # Reconcile re-checks and finds the SAME release date → no forward shift.
    await record_restriction_event(
        sender_id=sender.id,
        event_type="extension",
        source="spambot_reconcile",
        restricted_until=same_until,
        raw_text="still limited",
        db=async_db_session,
    )
    await async_db_session.commit()

    rows = await _event_rows(async_db_session, sender.id, event_type="extension")
    assert len(rows) == 0, f"no-shift reconcile must not write an extension event: {rows}"


# ─── HLTH-01d (D-01) ──────────────────────────────────────────────────────────

async def test_reconcile_shift_writes_extension(
    async_db_session, test_running_campaign_factory,
):
    """HLTH-01d / D-01: when the reconcile finds a release date moved meaningfully
    forward (> old + 1 minute), exactly ONE 'extension' event is written."""
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]

    old_until = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '2 hours'")
    )).scalar_one()
    await _freeze_sender(async_db_session, sender.id, "spam_limited", until=old_until)

    # @SpamBot now quotes a release date far in the future → real forward shift.
    new_until = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '48 hours'")
    )).scalar_one()
    await record_restriction_event(
        sender_id=sender.id,
        event_type="extension",
        source="spambot_reconcile",
        restricted_until=new_until,
        raw_text="limited until later",
        db=async_db_session,
    )
    await async_db_session.commit()

    rows = await _event_rows(async_db_session, sender.id, event_type="extension")
    assert len(rows) == 1, f"forward shift must write exactly one extension event: {rows}"


# ─── HLTH-01e ─────────────────────────────────────────────────────────────────

async def test_events_append_only(
    async_db_session, test_running_campaign_factory,
):
    """HLTH-01e: writing spam_limited then cleared for the same sender leaves BOTH
    rows in place — history is append-only, not overwritten like queue.error_message."""
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]

    await record_restriction_event(
        sender_id=sender.id, event_type="spam_limited", source="queue_error",
        restricted_until=None, raw_text="PEER_FLOOD", db=async_db_session,
    )
    await async_db_session.commit()
    await record_restriction_event(
        sender_id=sender.id, event_type="cleared", source="spambot_reconcile",
        restricted_until=None, raw_text="no limits applied", db=async_db_session,
    )
    await async_db_session.commit()

    rows = await _event_rows(async_db_session, sender.id)
    assert len(rows) == 2, f"append-only history must keep both events: {rows}"
    types = {r["event_type"] for r in rows}
    assert types == {"spam_limited", "cleared"}


# ─── HLTH-02a ─────────────────────────────────────────────────────────────────

async def test_event_carries_activity_slice(
    async_db_session, test_running_campaign_factory,
):
    """HLTH-02a: after seeding N 'sent' messages_log rows in the last hour, the event
    row's activity_slice JSONB carries sends_1h == N and a rate object echoing the
    sender's configured per-min/hour/day limits."""
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]

    n = 3
    for i in range(n):
        await _seed_sent(
            async_db_session, sender.workspace_id, sender.id,
            f"+79991110{i:03d}", minutes_ago=5 + i,
        )

    await record_restriction_event(
        sender_id=sender.id, event_type="spam_limited", source="queue_error",
        restricted_until=None, raw_text="PEER_FLOOD", db=async_db_session,
    )
    await async_db_session.commit()

    rows = await _event_rows(async_db_session, sender.id)
    assert len(rows) == 1, rows
    slice_ = rows[0]["activity_slice"]
    assert slice_ is not None, "restriction event must carry an activity_slice"
    assert slice_["sends_1h"] == n, slice_
    rate = slice_["rate"]
    assert rate["configured_per_min"] == 4
    assert rate["configured_per_hour"] == 20
    assert rate["configured_per_day"] == 150


# ─── HLTH-02b ─────────────────────────────────────────────────────────────────

async def test_event_carries_proxy_snapshot(
    async_db_session, test_running_campaign_factory,
):
    """HLTH-02b: with senders.proxy set, the event row's proxy JSONB column equals
    that proxy snapshot (taken at event time, own column per D-06.3)."""
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]

    proxy = {"type": "socks5", "host": "10.0.0.1", "port": 1080}
    await async_db_session.execute(
        text("UPDATE senders SET proxy = CAST(:p AS jsonb) WHERE id = :sid"),
        {"p": __import__("json").dumps(proxy), "sid": str(sender.id)},
    )
    await async_db_session.commit()

    await record_restriction_event(
        sender_id=sender.id, event_type="spam_limited", source="queue_error",
        restricted_until=None, raw_text="PEER_FLOOD", db=async_db_session,
    )
    await async_db_session.commit()

    rows = await _event_rows(async_db_session, sender.id)
    assert len(rows) == 1, rows
    assert rows[0]["proxy"] == proxy


# ─── HLTH-02c ─────────────────────────────────────────────────────────────────

async def test_slice_windows_sent_only(
    async_db_session, test_running_campaign_factory,
):
    """HLTH-02c: the slice counts only message_type='sent' and windows by created_at:
    a 'failed' row is excluded; a row older than 1h is excluded from sends_1h but
    still counted in sends_24h."""
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]
    wid = sender.workspace_id

    # 2 sent within the last hour.
    await _seed_sent(async_db_session, wid, sender.id, "+79992220001", minutes_ago=10)
    await _seed_sent(async_db_session, wid, sender.id, "+79992220002", minutes_ago=20)
    # 1 sent older than an hour (in 24h window only).
    await _seed_sent(async_db_session, wid, sender.id, "+79992220003", minutes_ago=120)
    # 1 failed within the hour — must be ignored by sends_* counts.
    await _seed_sent(async_db_session, wid, sender.id, "+79992220004",
                     minutes_ago=15, message_type="failed")

    await record_restriction_event(
        sender_id=sender.id, event_type="spam_limited", source="queue_error",
        restricted_until=None, raw_text="PEER_FLOOD", db=async_db_session,
    )
    await async_db_session.commit()

    slice_ = (await _event_rows(async_db_session, sender.id))[0]["activity_slice"]
    assert slice_ is not None
    assert slice_["sends_1h"] == 2, slice_   # 2 recent sent only
    assert slice_["sends_24h"] == 3, slice_  # 3 sent within 24h, failed excluded


# ─── D-03 ─────────────────────────────────────────────────────────────────────

async def test_recipient_privacy_separate_category(
    async_db_session, test_running_campaign_factory,
):
    """D-03: a recipient-privacy error is logged with category='recipient_privacy',
    NEVER flips the sender's restriction_status, and is excluded by a
    WHERE category='restriction' filter."""
    from app.services.restriction_audit import record_restriction_event

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]
    assert await _restriction_status(async_db_session, sender.id) == "none"

    await record_restriction_event(
        sender_id=sender.id,
        event_type="privacy_restricted",
        source="queue_error",
        restricted_until=None,
        raw_text="UserNotMutualContactError",
        category="recipient_privacy",
        db=async_db_session,
    )
    await async_db_session.commit()

    rows = await _event_rows(async_db_session, sender.id)
    assert len(rows) == 1, rows
    assert rows[0]["category"] == "recipient_privacy"
    # The account itself stays healthy — recipient-level error, not account restriction.
    assert await _restriction_status(async_db_session, sender.id) == "none"
    # The restriction-analytics filter excludes it.
    restriction_rows = await _event_rows(
        async_db_session, sender.id, category="restriction"
    )
    assert restriction_rows == []


# ─── HLTH-03 ──────────────────────────────────────────────────────────────────

async def test_history_endpoint(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_running_campaign_factory,
):
    """HLTH-03: GET /senders/{slug}/restriction-events returns the workspace's events
    newest-first; a sender from another workspace is NOT visible (workspace-scoped —
    cross-tenant-leak guard)."""
    from app.models import Workspace, Sender
    from app.services.restriction_audit import record_restriction_event

    # Bind the JWT sub to our test workspace as owner (mirror test_pool_endpoints _bind).
    await async_db_session.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": "hist-user", "wid": str(test_workspace.id)})
    await async_db_session.commit()

    _camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]

    # Two events for our sender: spam_limited first, then cleared (newer).
    await record_restriction_event(
        sender_id=sender.id, event_type="spam_limited", source="queue_error",
        restricted_until=None, raw_text="PEER_FLOOD", db=async_db_session,
    )
    await async_db_session.commit()
    await async_db_session.execute(
        text("UPDATE sender_restriction_events SET created_at = NOW() - INTERVAL '1 hour' "
             "WHERE sender_id = :sid AND event_type = 'spam_limited'"),
        {"sid": str(sender.id)},
    )
    await record_restriction_event(
        sender_id=sender.id, event_type="cleared", source="spambot_reconcile",
        restricted_until=None, raw_text="no limits applied", db=async_db_session,
    )
    await async_db_session.commit()

    # A sender in a DIFFERENT workspace with its own event — must NOT leak.
    other = Workspace(name="ForeignHistWS")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    foreign = Sender(
        workspace_id=other.id, slug="foreign-hist-sender", name="Foreign Hist",
        phone="+79995551234", session_string="enc", role="sender",
        auth_status="ok", lifecycle_status="active",
        rate_per_min=4, rate_per_hour=20, rate_per_day=150,
    )
    async_db_session.add(foreign)
    await async_db_session.commit()
    await async_db_session.refresh(foreign)
    await record_restriction_event(
        sender_id=foreign.id, event_type="banned", source="spambot_reconcile",
        restricted_until=None, raw_text="account suspended", db=async_db_session,
    )
    await async_db_session.commit()

    headers = {"Authorization": f"Bearer {valid_supabase_jwt(sub='hist-user')}"}

    r = await async_client.get(
        f"/api/v1/senders/{sender.slug}/restriction-events", headers=headers,
    )
    assert r.status_code == 200, r.text
    events = r.json()
    assert isinstance(events, list) and len(events) == 2, events
    # Newest-first.
    assert events[0]["event_type"] == "cleared"
    assert events[1]["event_type"] == "spam_limited"

    # Cross-tenant guard: the foreign sender's slug is not reachable from our workspace.
    r2 = await async_client.get(
        f"/api/v1/senders/{foreign.slug}/restriction-events", headers=headers,
    )
    assert r2.status_code == 404, r2.text
    assert "account suspended" not in r2.text
