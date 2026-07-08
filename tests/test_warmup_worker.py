"""Phase 15 — Warmup worker RED guards (WARM-06 / WARM-10 / WARM-14).

These tests are intentionally RED at the end of Plan 15-01. They assert worker
behaviours that Plan 03 implements in ``app/services/warmup.py``:

- test_disabled_workspace_skipped (WARM-06): a workspace whose
  ``warmup_settings.enabled = false`` (or has no settings row) must contribute
  ZERO active-pool members to a worker tick — the enabled gate (D-06) drops
  disabled workspaces. RED until ``_get_active_pool`` honours the flag.
- test_content_defaults_when_empty (WARM-10): a workspace with no
  ``warmup_settings`` row (or empty topics / NULL system_prompt) must resolve to
  the 24 RU ``WARMUP_TOPICS`` + ``WARMUP_SYSTEM_PROMPT`` via a new content
  resolver helper. RED until the helper exists.
- test_restricted_sender_excluded (WARM-14): a sender with
  ``restriction_status='spam_limited'`` (or a future ``restricted_until``) must
  be excluded from ``_get_active_pool`` selection (RESV-05 model). RED until the
  restriction clause is added.

RED rationale: the enabled gate, the content resolver, and the restriction
clause do not exist yet, so the behavioural assertions fail (or AttributeError
on the missing helper) for the right reason. Imports of not-yet-existing symbols
are deferred into the test bodies so ``pytest --collect-only`` stays clean.
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── Helpers (raw INSERT to avoid coupling to ORM defaults) ───────────────────


async def _enroll_active_sender(db, wid: str, slug: str, **sender_overrides) -> str:
    """Create a role='sender', active+ok account and enroll it in warmup_pool."""
    sid = str(_uuid.uuid4())
    cols = dict(
        restriction_status="none",
        restricted_until=None,
    )
    cols.update(sender_overrides)
    await db.execute(text("""
        INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                             role, auth_status, lifecycle_status,
                             restriction_status, restricted_until,
                             rate_per_min, rate_per_hour, rate_per_day)
        VALUES (:id, :wid, :slug, :name, :phone, 'stub',
                'sender', 'ok', 'active',
                :restriction_status, :restricted_until, 4, 20, 150)
    """), {"id": sid, "wid": wid, "slug": slug, "name": slug,
           "phone": f"+790{abs(hash(sid)) % 10_000_000:07d}", **cols})
    await db.execute(text("""
        INSERT INTO warmup_pool (id, workspace_id, sender_id, is_active, enrolled_at)
        VALUES (gen_random_uuid(), :wid, :sid, true, NOW() - INTERVAL '3 days')
        ON CONFLICT (sender_id) DO NOTHING
    """), {"wid": wid, "sid": sid})
    await db.commit()
    return sid


async def _make_workspace(db) -> str:
    wid = str(_uuid.uuid4())
    await db.execute(text("INSERT INTO workspaces (id, name) VALUES (:id, :n)"),
                     {"id": wid, "n": f"WS {wid[:8]}"})
    await db.commit()
    return wid


# ── WARM-06: disabled workspace produces no active-pool members ──────────────


async def test_disabled_workspace_skipped(async_db_session):
    """A workspace with no warmup_settings row (enabled defaults OFF) must
    contribute no members to _get_active_pool — the worker skips it (D-06)."""
    db = async_db_session
    wid = await _make_workspace(db)
    await _enroll_active_sender(db, wid, f"disabled-{_uuid.uuid4().hex[:6]}")
    # No warmup_settings row → enabled is effectively FALSE.

    from app.services.warmup import warmup_worker

    pool = await warmup_worker._get_active_pool(db)
    members = [p for p in pool if p["workspace_id"] == wid]
    assert members == [], (
        "disabled workspace (no enabled warmup_settings) must yield zero "
        "active-pool members — enabled gate not implemented yet (WARM-06)"
    )


# ── WARM-10: empty settings → code-default topics + prompt ───────────────────


async def test_content_defaults_when_empty(async_db_session):
    """A workspace with no warmup_settings row resolves to the 24 RU topics +
    default system prompt via the content resolver helper (D-10)."""
    db = async_db_session
    wid = await _make_workspace(db)

    from app.services.warmup import (
        warmup_worker,
        WARMUP_TOPICS,
        WARMUP_SYSTEM_PROMPT,
    )

    resolver = getattr(warmup_worker, "_get_warmup_content", None)
    assert resolver is not None, (
        "WarmupWorker._get_warmup_content missing — content resolver not "
        "implemented yet (WARM-10)"
    )

    topics, prompt = await resolver(db, wid)
    assert topics == WARMUP_TOPICS, (
        "empty settings must resolve to the 24 hard-coded RU WARMUP_TOPICS"
    )
    assert prompt == WARMUP_SYSTEM_PROMPT, (
        "empty settings must resolve to the hard-coded WARMUP_SYSTEM_PROMPT"
    )


# ── WARM-14: restricted sender excluded from selection ───────────────────────


async def test_restricted_sender_excluded(async_db_session):
    """A sender with restriction_status='spam_limited' must not appear in
    _get_active_pool (RESV-05 model, D-14)."""
    db = async_db_session
    wid = await _make_workspace(db)
    # Enable warmup so the only thing keeping it out is the restriction clause.
    await db.execute(text("""
        INSERT INTO warmup_settings (workspace_id, enabled)
        VALUES (:wid, true)
        ON CONFLICT (workspace_id) DO UPDATE SET enabled = true
    """), {"wid": wid})
    await db.commit()

    restricted_slug = f"restricted-{_uuid.uuid4().hex[:6]}"
    await _enroll_active_sender(
        db, wid, restricted_slug, restriction_status="spam_limited",
    )

    from app.services.warmup import warmup_worker

    pool = await warmup_worker._get_active_pool(db)
    slugs = [p["slug"] for p in pool]
    assert restricted_slug not in slugs, (
        "spam_limited sender must be excluded from warmup pool selection — "
        "restriction clause not added yet (WARM-14)"
    )


# ── Head-of-line guard: ineligible session must advance next_message_at ──────


async def test_ineligible_session_reschedules_next_message_at(async_db_session):
    """A due session whose peer is ineligible (e.g. auth_status='session_expired')
    must have next_message_at pushed into the FUTURE, not left in the past.

    Regression for the 2026-07-02 warmup stall: _process_session used to `return`
    on an ineligible peer WITHOUT advancing next_message_at, so the session stayed
    at the head of the LIMIT-10 due-queue forever and starved every healthy pair.
    """
    db = async_db_session
    wid = await _make_workspace(db)

    # from = eligible active/ok sender; to = session_expired (ineligible).
    from_id = await _enroll_active_sender(db, wid, f"warm-from-{_uuid.uuid4().hex[:6]}")
    to_id = await _enroll_active_sender(
        db, wid, f"warm-dead-{_uuid.uuid4().hex[:6]}", auth_status="session_expired",
    )

    session_id = str(_uuid.uuid4())
    await db.execute(text("""
        INSERT INTO warmup_sessions
            (id, workspace_id, sender_a_id, sender_b_id, topic,
             status, messages_sent, target_messages, next_message_at)
        VALUES (:id, :wid, :a, :b, 'тест',
                'active', 0, 6, NOW() - INTERVAL '2 hours')
    """), {"id": session_id, "wid": wid, "a": from_id, "b": to_id})
    await db.commit()

    from app.services.warmup import warmup_worker

    await warmup_worker._process_session(db, {
        "id": session_id,
        "sender_a_id": from_id,
        "sender_b_id": to_id,
        "topic": "тест",
        "messages_sent": 0,
        "target_messages": 6,
        "last_sender_id": None,
    })

    next_at = (await db.execute(
        text("SELECT next_message_at FROM warmup_sessions WHERE id = :id"),
        {"id": session_id},
    )).scalar()
    from datetime import datetime, timezone
    assert next_at > datetime.now(timezone.utc), (
        "ineligible session must advance next_message_at into the future so it "
        "leaves the head of the due-queue (head-of-line guard)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 22 — shared new-chat budget for warmup pairing (D-08 / D-09 / D-03)
#
# -k "pair"    — new-pair charge + registry insert, known/backfilled pair free,
#                initiator = older account ordered as sender_a.
# -k "reserve" — outreach-priority reserve on the trailing-24h shared budget:
#                pending cold openers + trailing-24h sends starve warmup, then
#                free it once the reserve drops.
# ─────────────────────────────────────────────────────────────────────────────


async def _enable_warmup(db, wid: str) -> None:
    """Turn on warmup for a workspace so _get_active_pool returns its members."""
    await db.execute(text("""
        INSERT INTO warmup_settings (workspace_id, enabled)
        VALUES (:wid, true)
        ON CONFLICT (workspace_id) DO UPDATE SET enabled = true
    """), {"wid": wid})
    await db.commit()


async def _set_enrolled_days_ago(db, sender_id: str, days: int) -> None:
    """Override warmup_pool.enrolled_at so tests can control the initiator choice
    (older / more-warmed = greater enrolled_days = earlier enrolled_at)."""
    await db.execute(text("""
        UPDATE warmup_pool SET enrolled_at = NOW() - make_interval(days => :d)
        WHERE sender_id = :sid
    """), {"d": days, "sid": sender_id})
    await db.commit()


async def _make_campaign(db, wid: str) -> str:
    cid = str(_uuid.uuid4())
    await db.execute(
        text("INSERT INTO campaigns (id, workspace_id, name) VALUES (:id, :wid, :n)"),
        {"id": cid, "wid": wid, "n": f"camp {cid[:8]}"},
    )
    await db.commit()
    return cid


async def _queue_row(db, wid: str, sender_id: str, campaign_id, status: str,
                     phone: str, finished_ago_hours=None) -> None:
    """Insert a message_queue row for budget accounting.

    finished_ago_hours: when status='sent', how long ago finished_at was (to
    place it inside / outside the trailing-24h window). None → NULL finished_at.
    """
    finished = None
    if finished_ago_hours is not None:
        finished = (
            "NOW() - make_interval(hours => :fh)" if finished_ago_hours else "NOW()"
        )
    sql = f"""
        INSERT INTO message_queue
            (id, workspace_id, sender_id, campaign_id, item_type, status,
             recipient_phone, message_text, finished_at)
        VALUES (:id, :wid, :sid, :cid, 'message', :st, :phone, 'hi',
                {finished if finished else 'NULL'})
    """
    params = {"id": str(_uuid.uuid4()), "wid": wid, "sid": sender_id,
              "cid": campaign_id, "st": status, "phone": phone}
    if finished_ago_hours:
        params["fh"] = finished_ago_hours
    await db.execute(text(sql), params)
    await db.commit()


async def _active_session_pair(db, sender_a: str, sender_b: str):
    """Return the (sender_a_id, sender_b_id) of the newest active warmup_session
    covering this unordered pair, or None if none created."""
    row = (await db.execute(text("""
        SELECT sender_a_id, sender_b_id FROM warmup_sessions
        WHERE status = 'active'
          AND ((sender_a_id = :a AND sender_b_id = :b)
            OR (sender_a_id = :b AND sender_b_id = :a))
        ORDER BY created_at DESC LIMIT 1
    """), {"a": sender_a, "b": sender_b})).fetchone()
    return (str(row[0]), str(row[1])) if row else None


async def _registry_has(db, a: str, b: str) -> bool:
    row = (await db.execute(text("""
        SELECT 1 FROM sender_first_contacts
        WHERE sender_a_id = LEAST(CAST(:a AS uuid), CAST(:b AS uuid))
          AND sender_b_id = GREATEST(CAST(:a AS uuid), CAST(:b AS uuid))
    """), {"a": a, "b": b})).fetchone()
    return row is not None


# ── -k pair ──────────────────────────────────────────────────────────────────


async def test_new_pair_charges_and_records_initiator_as_sender_a(async_db_session):
    """A NEW pair (absent from sender_first_contacts) with budget available:
    session created, registry row inserted, and the initiator (older account =
    earlier enrolled_at) is sender_a so it writes first (D-08)."""
    db = async_db_session
    wid = await _make_workspace(db)
    await _enable_warmup(db, wid)

    older = await _enroll_active_sender(db, wid, f"pair-old-{_uuid.uuid4().hex[:6]}")
    younger = await _enroll_active_sender(db, wid, f"pair-new-{_uuid.uuid4().hex[:6]}")
    await _set_enrolled_days_ago(db, older, 10)    # older / more-warmed
    await _set_enrolled_days_ago(db, younger, 2)

    from app.services.warmup import warmup_worker

    await warmup_worker._create_new_sessions(db)

    pair = await _active_session_pair(db, older, younger)
    assert pair is not None, "NEW pair with budget must create a warmup session"
    assert pair[0] == older, (
        "initiator (older account) must be sender_a so it writes first (D-08)"
    )
    assert await _registry_has(db, older, younger), (
        "creating a NEW pair must record it in sender_first_contacts (D-08)"
    )


async def test_known_pair_is_free_no_new_registry_row(async_db_session):
    """A KNOWN pair (already in the registry) still warms but consumes no budget
    and adds no second registry row (D-08)."""
    db = async_db_session
    wid = await _make_workspace(db)
    await _enable_warmup(db, wid)

    a = await _enroll_active_sender(db, wid, f"known-a-{_uuid.uuid4().hex[:6]}")
    b = await _enroll_active_sender(db, wid, f"known-b-{_uuid.uuid4().hex[:6]}")
    # Pre-record the pair canonically → classified KNOWN.
    await db.execute(text("""
        INSERT INTO sender_first_contacts (sender_a_id, sender_b_id)
        VALUES (LEAST(CAST(:a AS uuid), CAST(:b AS uuid)),
                GREATEST(CAST(:a AS uuid), CAST(:b AS uuid)))
        ON CONFLICT DO NOTHING
    """), {"a": a, "b": b})
    await db.commit()

    # Starve the shared budget entirely: a known pair must ignore the reserve.
    cid = await _make_campaign(db, wid)
    for i in range(9):
        await _queue_row(db, wid, a, cid, "pending", f"+79990000{i:03d}")

    from app.services.warmup import warmup_worker

    await warmup_worker._create_new_sessions(db)

    assert await _active_session_pair(db, a, b) is not None, (
        "a KNOWN pair must still warm even with zero remaining budget"
    )
    count = (await db.execute(text("""
        SELECT COUNT(*) FROM sender_first_contacts
        WHERE sender_a_id = LEAST(CAST(:a AS uuid), CAST(:b AS uuid))
          AND sender_b_id = GREATEST(CAST(:a AS uuid), CAST(:b AS uuid))
    """), {"a": a, "b": b})).scalar()
    assert count == 1, "a KNOWN pair must not insert a second registry row"


async def test_backfilled_pair_classified_known(async_db_session):
    """A pair that already warmed before this phase (backfilled from
    warmup_sessions into sender_first_contacts by migration 057) is classified
    KNOWN — not charged as new (D-08 backfill idempotency)."""
    db = async_db_session
    wid = await _make_workspace(db)
    await _enable_warmup(db, wid)

    a = await _enroll_active_sender(db, wid, f"bf-a-{_uuid.uuid4().hex[:6]}")
    b = await _enroll_active_sender(db, wid, f"bf-b-{_uuid.uuid4().hex[:6]}")

    # Simulate prior warmup activity: a COMPLETED session between the pair.
    await db.execute(text("""
        INSERT INTO warmup_sessions
            (id, workspace_id, sender_a_id, sender_b_id, topic,
             status, messages_sent, target_messages, created_at)
        VALUES (:id, :wid, :a, :b, 'история',
                'completed', 6, 6, NOW() - INTERVAL '5 days')
    """), {"id": str(_uuid.uuid4()), "wid": wid, "a": a, "b": b})
    await db.commit()

    # Re-run migration 057's canonical backfill from warmup_sessions.
    await db.execute(text("""
        INSERT INTO sender_first_contacts (sender_a_id, sender_b_id, first_contact_at)
        SELECT LEAST(sender_a_id, sender_b_id),
               GREATEST(sender_a_id, sender_b_id),
               MIN(created_at)
          FROM warmup_sessions
         WHERE sender_a_id IS NOT NULL AND sender_b_id IS NOT NULL
           AND sender_a_id <> sender_b_id
         GROUP BY LEAST(sender_a_id, sender_b_id), GREATEST(sender_a_id, sender_b_id)
        ON CONFLICT DO NOTHING
    """))
    await db.commit()

    assert await _registry_has(db, a, b), "backfill must record the warmed pair"

    from app.services.warmup import warmup_worker

    await warmup_worker._create_new_sessions(db)

    # Only the backfilled row exists — no NEW-pair insert happened.
    count = (await db.execute(text("""
        SELECT COUNT(*) FROM sender_first_contacts
        WHERE sender_a_id = LEAST(CAST(:a AS uuid), CAST(:b AS uuid))
          AND sender_b_id = GREATEST(CAST(:a AS uuid), CAST(:b AS uuid))
    """), {"a": a, "b": b})).scalar()
    assert count == 1, "a backfilled (KNOWN) pair must not be re-charged as new"


# ── -k reserve ───────────────────────────────────────────────────────────────


async def test_reserve_starves_new_pair(async_db_session):
    """Outreach-priority reserve (D-09): when pending cold openers reserve the
    whole account budget (level-1 = 5), warmup opens NO new pair for the
    initiator, on the trailing-24h shared window (D-03)."""
    db = async_db_session
    wid = await _make_workspace(db)
    await _enable_warmup(db, wid)

    a = await _enroll_active_sender(db, wid, f"resv-a-{_uuid.uuid4().hex[:6]}")
    b = await _enroll_active_sender(db, wid, f"resv-b-{_uuid.uuid4().hex[:6]}")
    await _set_enrolled_days_ago(db, a, 20)   # a = older → initiator
    await _set_enrolled_days_ago(db, b, 1)

    # 5 distinct pending cold openers for the initiator (level-1 budget = 5).
    cid = await _make_campaign(db, wid)
    for i in range(5):
        await _queue_row(db, wid, a, cid, "pending", f"+79995550{i:03d}")

    from app.services.warmup import warmup_worker

    await warmup_worker._create_new_sessions(db)

    assert await _active_session_pair(db, a, b) is None, (
        "reserve = full budget → warmup must NOT open a new pair"
    )
    assert not await _registry_has(db, a, b), (
        "a starved new pair must not be recorded in the registry"
    )


async def test_reserve_frees_new_pair_when_backlog_drops(async_db_session):
    """When outreach reserve leaves >= 1 of the shared budget, warmup opens the
    new pair and records it (D-09)."""
    db = async_db_session
    wid = await _make_workspace(db)
    await _enable_warmup(db, wid)

    a = await _enroll_active_sender(db, wid, f"free-a-{_uuid.uuid4().hex[:6]}")
    b = await _enroll_active_sender(db, wid, f"free-b-{_uuid.uuid4().hex[:6]}")
    await _set_enrolled_days_ago(db, a, 20)   # a = older → initiator
    await _set_enrolled_days_ago(db, b, 1)

    # Only 4 pending openers (budget 5) → remaining 1 → warmup may open.
    cid = await _make_campaign(db, wid)
    for i in range(4):
        await _queue_row(db, wid, a, cid, "pending", f"+79994440{i:03d}")

    from app.services.warmup import warmup_worker

    await warmup_worker._create_new_sessions(db)

    pair = await _active_session_pair(db, a, b)
    assert pair is not None and pair[0] == a, (
        "remaining budget >= 1 → warmup opens the new pair, initiator = sender_a"
    )
    assert await _registry_has(db, a, b), "freed new pair must be recorded"


async def test_reserve_trailing_24h_spent_window(async_db_session):
    """The shared 'spent' count uses the trailing-24h window, not calendar-day:
    a send finished 30h ago does NOT count against budget; one finished 2h ago
    does (D-03, Pitfall 4)."""
    db = async_db_session
    wid = await _make_workspace(db)
    await _enable_warmup(db, wid)

    a = await _enroll_active_sender(db, wid, f"win-a-{_uuid.uuid4().hex[:6]}")
    b = await _enroll_active_sender(db, wid, f"win-b-{_uuid.uuid4().hex[:6]}")

    from app.services.warmup import warmup_worker

    # Budget 5, no queue rows → remaining 5.
    base = await warmup_worker._remaining_new_chat_budget(db, a, wid, 1)
    assert base == 5, f"level-1 code-default budget must be 5, got {base}"

    cid = await _make_campaign(db, wid)
    # A send that finished 30h ago is OUTSIDE the trailing-24h window → ignored.
    await _queue_row(db, wid, a, cid, "sent", "+79993330001", finished_ago_hours=30)
    stale = await warmup_worker._remaining_new_chat_budget(db, a, wid, 1)
    assert stale == 5, "a send >24h ago must not reduce the shared budget"

    # A send that finished 2h ago IS inside the window → spends 1.
    await _queue_row(db, wid, a, cid, "sent", "+79993330002", finished_ago_hours=2)
    fresh = await warmup_worker._remaining_new_chat_budget(db, a, wid, 1)
    assert fresh == 4, "a send within trailing-24h must reduce the budget by 1"
