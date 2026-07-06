"""Quick 260706-e8s (T2) — reactive send-path suspect-resolve rollback.

Why this exists: the 2026-07-06 07:31 incident — sender-7867638054 returned
"+79185782285 не зарегистрирован в Telegram" (a resolve false-negative) and 12s
later caught PEER_FLOOD. The number was ALIVE — a healthy account reached it at
08:19. On the dedicated CHECKER path Phase 14/17 already treat a false-negative
from an account sliding into a throttle as SUSPECT (rollback + cache purge). This
mirrors that guarantee onto the message-SEND path so a false-negative from a sick
sender is never finalized silently.

When a sender is flagged spam_limited/frozen (the inline PEER_FLOOD /
ACCOUNT_FROZEN block in queue.py), its NOT_REGISTERED / PRIVACY refusals from the
last ``SUSPECT_RESOLVE_WINDOW_MINUTES`` are treated as likely false-negatives from
the pre-restriction throttle ramp — NOT final. ``rollback_suspect_resolve_fails``:

  1. reroutes those ``message_queue`` failed rows onto a healthy UNTRIED pool
     sender (``status='pending'``, ``scheduled_at=NOW()``), keeping the sticky
     ``campaign_contact_assignments`` in lock-step;
  2. purges the ``is_registered=false`` ``contacts_cache`` rows the flagged sender
     wrote in the window (mirrors Phase 17 suspect cache-poison handling);
  3. defensively rolls the flagged sender's fresh ``not_registered`` ``contacts``
     verdicts back to pending/suspect (forward-safe no-op today — the send path
     does NOT set ``contacts.tg_resolved_by`` to a sender, unlike the checker).

Bounded (WR-15): a row is only rerouted to a sender NOT already present in
``extra_data.nr_tried_senders``. Once every healthy sender has tried, the row
stays failed (genuinely unregistered) — no infinite loop.

Best-effort (mirrors failover.py FAIL-07 / D-13): a campaign with no healthy
receiver leaves its rows failed — nothing is lost, the reactive path just can't
reroute (the reconcile-resume loop lifts the flagged sender's own backlog later).

Never resurrect (WR-17): only rows on a ``running`` campaign are touched; a
paused/done campaign's failed rows are left alone.

Session ownership (mirrors failover.py): ``db=None`` → open + commit own session
(queue.py PEER_FLOOD / ACCOUNT_FROZEN callers); ``db`` passed → transaction-
neutral, the CALLER commits (so pause + flag + failover + rollback land in one
atomic commit).

PII discipline (CLAUDE.md): logs COUNT + sender/campaign UUIDs ONLY — never
``recipient_phone`` or payloads.

NB: this touches NO rate-limit / interval constant (MIN/MAX_SEND_INTERVAL,
LONG_PAUSE_*, FLOOD_HARD_THRESHOLD, the 24h pause, per-sender rate_per_*). The
15-min window is a suspect-attribution window, not a rate control — the CLAUDE.md
"не менять без обсуждения" carve-out does not apply.
"""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.services.rotation import _pick_least_loaded

logger = logging.getLogger(__name__)

# See PLAN objective for rationale; NOT a rate-limit constant. The send interval
# is 20-55s so 15 min ≈ the last ~15-40 sends — the pre-restriction throttle ramp
# during which a sliding-into-spam-limit account emits false negatives before
# PEER_FLOOD fires — while short enough not to claw back genuinely old (healthy-
# period) NOT_REGISTERED verdicts.
SUSPECT_RESOLVE_WINDOW_MINUTES = 15

# Resolve-fail marker codes stamped by queue.py into message_queue.extra_data.
_RESOLVE_FAIL_CODES = ("RECIPIENT_NOT_IN_TELEGRAM", "PRIVACY_RESTRICTED")

# Healthy-pool query copied VERBATIM from failover.py: restriction_status='none'
# excludes the just-flagged sender; the campaigns JOIN + workspace_id scope close
# the cross-workspace surface.
_HEALTHY_POOL_SQL = """
    SELECT s.id AS sid
    FROM campaign_senders cs
    JOIN senders s ON s.id = cs.sender_id
    JOIN campaigns c ON c.id = cs.campaign_id
    WHERE cs.campaign_id = :cid
      AND s.lifecycle_status = 'active'
      AND s.auth_status = 'ok'
      AND s.role = 'sender'
      AND s.restriction_status = 'none'
      AND s.workspace_id = c.workspace_id
"""


async def rollback_suspect_resolve_fails(
    flagged_sender_id: UUID,
    db: AsyncSession | None = None,
) -> int:
    """Claw back a just-flagged sender's send-path resolve false-negatives.

    Mirrors the Phase 14/17 checker suspect-rollback on the SEND path: when a
    sender is flagged spam_limited/frozen, its NOT_REGISTERED / PRIVACY refusals
    in the last SUSPECT_RESOLVE_WINDOW_MINUTES are treated as SUSPECT (likely
    false negatives from throttle onset), not final. db=None → own session +
    commit (queue.py callers); db passed → transaction-neutral (caller commits).
    Returns the number of message_queue rows rerouted.
    """
    if db is None:
        async with AsyncSessionLocal() as own_db:
            moved = await _rollback(flagged_sender_id, own_db)
            await own_db.commit()
            return moved
    # Transaction-neutral: caller owns the commit.
    return await _rollback(flagged_sender_id, db)


async def _rollback(flagged_sender_id: UUID, db: AsyncSession) -> int:
    """Core rollback over a live session (no commit — caller decides)."""
    sid = str(flagged_sender_id)
    win = SUSPECT_RESOLVE_WINDOW_MINUTES

    # Step 1: the flagged sender's SUSPECT resolve-fail rows in the window, on a
    # RUNNING campaign only (WR-17 — never resurrect on paused/done). NULL
    # extra_data → ->>'…' yields NULL → excluded by the IN filter (safe re IN-14).
    claimed = (await db.execute(
        text("""
            SELECT mq.id AS id, mq.campaign_id AS campaign_id,
                   mq.recipient_phone AS phone, mq.extra_data AS extra_data
            FROM message_queue mq
            JOIN campaigns c ON c.id = mq.campaign_id
            WHERE mq.sender_id::text = :sid
              AND mq.status = 'failed'
              AND mq.campaign_id IS NOT NULL
              AND c.status = 'running'
              AND mq.finished_at >= NOW() - make_interval(mins => :win)
              AND mq.extra_data->>'resolve_fail_code'
                    IN ('RECIPIENT_NOT_IN_TELEGRAM', 'PRIVACY_RESTRICTED')
              AND mq.extra_data->>'resolve_fail_sender' = :sid
            FOR UPDATE OF mq SKIP LOCKED
        """),
        {"sid": sid, "win": win},
    )).fetchall()

    # Resolve the healthy pool once per campaign (cache across rows).
    pool_cache: dict[str, list[str]] = {}
    total_moved = 0

    for row in claimed:
        cid = str(row.campaign_id)
        if cid not in pool_cache:
            pool_rows = (await db.execute(text(_HEALTHY_POOL_SQL), {"cid": cid})).fetchall()
            pool_cache[cid] = [str(r.sid) for r in pool_rows]
        pool = pool_cache[cid]
        if not pool:
            # Best-effort (FAIL-07 / D-13): no healthy receiver — leave failed.
            logger.info(
                "send_suspect: no healthy receiver to reroute suspect resolve-fail "
                "off sender %s in campaign %s",
                sid, cid,
            )
            continue

        # Bounded (WR-15 / Test F): candidate receivers = healthy pool minus the
        # senders already tried for this row minus the flagged sender (the pool
        # query already excludes the flagged sender via restriction_status='none').
        ed = row.extra_data or {}
        tried = set(ed.get("nr_tried_senders") or [])
        candidates = [c for c in pool if c not in tried and c != sid]
        if not candidates:
            continue

        new_sid = str(await _pick_least_loaded(db, candidates))
        # Move queue + sticky CCA in lock-step and reset scheduled_at = NOW() so
        # the healthy receiver can send immediately (shed the +24h freeze pause).
        await db.execute(text("""
            UPDATE message_queue
            SET sender_id = :new, status = 'pending', scheduled_at = NOW(),
                attempts = 0, error_message = NULL, started_at = NULL,
                finished_at = NULL
            WHERE id = :rid
        """), {"new": new_sid, "rid": str(row.id)})
        await db.execute(text("""
            UPDATE campaign_contact_assignments
            SET sender_id = :new
            WHERE campaign_id = :cid AND contact_phone = :phone
        """), {"new": new_sid, "cid": cid, "phone": row.phone})
        total_moved += 1

    # Step 4: purge the poisoned cache the flagged sender wrote in the window
    # (mirrors Phase 17 suspect cache-poison handling). Runs UNCONDITIONALLY —
    # a false-negative can poison the resolve cache even when it never enqueued a
    # reroutable row — so the next resolve is a live lookup, not a stale false.
    await db.execute(text("""
        DELETE FROM contacts_cache
        WHERE sender_id = :sid
          AND is_registered = false
          AND updated_at >= NOW() - make_interval(mins => :win)
    """), {"sid": sid, "win": win})

    # Step 5: defensive contacts rollback. Forward-safe: the SEND path does NOT set
    # contacts.tg_resolved_by to a sender today (only the Phase 14/17 checker does),
    # so this is a no-op that keeps parity with the acceptance wording "…/contacts.
    # tg_status записи от этого сендера…" should the send path ever write it.
    await db.execute(text("""
        UPDATE contacts
        SET tg_status = 'pending', tg_checked_at = NULL, tg_probe_state = 'suspect',
            tg_confidence = NULL, updated_at = NOW()
        WHERE tg_resolved_by = :sid
          AND tg_status = 'not_registered'
          AND tg_checked_at >= NOW() - make_interval(mins => :win)
    """), {"sid": sid, "win": win})

    if total_moved:
        # PII discipline: COUNT + flagged-sender UUID ONLY — never a phone.
        logger.info(
            "send_suspect: rerouted %d suspect resolve-fail row(s) off flagged "
            "sender %s to healthy untried pool sender(s)",
            total_moved, sid,
        )
    return total_moved
