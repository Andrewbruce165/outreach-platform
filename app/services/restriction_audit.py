"""Phase 10 — Durable append-only restriction event-log writer (HLTH-01/02).

Why this exists: ``senders.restriction_status`` holds only the CURRENT state;
``message_queue.error_message`` is overwritten on reschedule; telemetry_events
never records restriction changes; and container logs live only ~18h (see
.planning/notes/account-restriction-audit-gap.md). So the history of "what this
account was doing → what restriction it got" is unrecoverable today. This module
writes one durable, append-only row per restriction state-change into
``sender_restriction_events`` (migration 030), and for restriction-category
events captures a snapshot of the sender's preceding activity (D-05).

Session ownership (same-TX guarantee, Pitfall 2): the event row MUST land in the
SAME transaction as the ``senders.restriction_status`` UPDATE so audit and state
can never diverge on crash. Hence the dual-mode signature (mirrors
``failover_cold_backlog``): ``db=None`` → open AND commit our own session;
``db`` passed → transaction-neutral, the CALLER commits.

D-01 (event only on state-change / forward-shift): for ``event_type ==
'extension'`` the helper compares the passed ``restricted_until`` against the
sender's CURRENT ``restricted_until`` (read in the same transaction) and writes
NOTHING unless the release date moved meaningfully forward (> 1 minute). A pure
recheck-interval bump on a still-limited tick therefore produces no row — this
is the gate that kills the 37/day reconcile noise. All other event types are
always recorded (they ARE state-changes).

D-05 (snapshot at write time): the activity_slice is computed here, inline, in
the caller's transaction — never reconstructed later from ephemeral sources.

PII discipline (CLAUDE.md): raw_text carries only the human-facing Telegram
error / @SpamBot reply text; the proxy snapshot lives in its own structured
``proxy`` JSONB column. No API_KEY, session strings, or proxy credentials are
ever logged into raw_text.
"""

import json
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def record_restriction_event(
    sender_id: UUID,
    event_type: str,
    source: str,
    restricted_until: datetime | None,
    raw_text: str | None,
    category: str = "restriction",
    db: AsyncSession | None = None,
) -> None:
    """Write one append-only restriction event for a sender.

    Args:
        sender_id: the sender the event is about (its workspace_id, proxy and
            configured rate limits are read from the sender row).
        event_type: 'spam_limited'|'frozen'|'flood_wait'|'cleared'|'banned'|
            'extension' (restriction) | 'privacy_restricted' (recipient_privacy).
        source: 'queue_error'|'spambot_reconcile'|'antispam_signal' (free-form).
        restricted_until: the release/recheck time at the moment of the event;
            NULL for cleared / recipient-privacy rows.
        raw_text: the raw Telegram send-error message or @SpamBot reply.
        category: 'restriction' (account-level, carries a slice) or
            'recipient_privacy' (recipient-level, account healthy, slice NULL).
        db: when ``None`` the helper opens AND commits its own session; when an
            ``AsyncSession`` is passed it is transaction-neutral and the CALLER
            commits (so the event lands atomically with the status UPDATE).
    """
    if db is None:
        async with AsyncSessionLocal() as own_db:
            await _record(
                own_db, sender_id, event_type, source,
                restricted_until, raw_text, category,
            )
            await own_db.commit()
        return
    # Transaction-neutral: caller owns the commit.
    await _record(
        db, sender_id, event_type, source,
        restricted_until, raw_text, category,
    )


async def _record(
    db: AsyncSession,
    sender_id: UUID,
    event_type: str,
    source: str,
    restricted_until: datetime | None,
    raw_text: str | None,
    category: str,
) -> None:
    """Core write over a live session (no commit — caller decides).

    Reads the sender's workspace_id + proxy + configured limits, computes the
    activity_slice for restriction-category events from messages_log, and INSERTs
    the event row. NEVER UPDATEs senders — restriction-status changes belong to
    the call-sites; this helper only appends the audit row.
    """
    # WR-03 (Phase 10): the senders FK is ON DELETE CASCADE and reconcile/queue
    # ticks run on stale sender_ids read from a prior batch SELECT. If the sender
    # was deleted between the restriction event and this write, `.one()` would
    # raise NoResultFound and — in the same-TX call-sites — abort the caller's
    # legitimate transaction (rolling back the restriction_status UPDATE / queue
    # resume the audit row documents). An audit write must never roll back the
    # state change it records: use .one_or_none() and skip the write if gone.
    s = (await db.execute(text("""
        SELECT workspace_id, proxy, rate_per_min, rate_per_hour, rate_per_day,
               restricted_until
        FROM senders WHERE id = :sid
    """), {"sid": str(sender_id)})).one_or_none()
    if s is None:
        logger.warning("restriction event for missing sender %s skipped", sender_id)
        return

    # D-01 gate: an 'extension' event is recorded ONLY on a genuine forward shift
    # of the release date (> old + 1 minute). A still-limited reconcile tick that
    # re-checks to the SAME (or earlier) release date writes NO row — this is what
    # suppresses the 37/day reconcile noise the audit log exists to avoid.
    if event_type == "extension":
        old_until = s.restricted_until
        if (
            old_until is not None
            and restricted_until is not None
            and restricted_until <= old_until + timedelta(minutes=1)
        ):
            return

    slice_ = None
    if category == "restriction":
        # D-05/D-06: snapshot the preceding activity from the durable send log.
        # Pitfall 3: count only message_type = 'sent'; window by created_at.
        counts = (await db.execute(text("""
            SELECT
              COUNT(*) FILTER (
                WHERE created_at >= now() - interval '1 hour')  AS s1,
              COUNT(*) FILTER (
                WHERE created_at >= now() - interval '24 hours') AS s24,
              COUNT(DISTINCT recipient_phone) FILTER (
                WHERE created_at >= now() - interval '1 hour')  AS u1,
              COUNT(DISTINCT recipient_phone) FILTER (
                WHERE created_at >= now() - interval '24 hours') AS u24
            FROM messages_log
            WHERE sender_id = :sid AND message_type = 'sent'
        """), {"sid": str(sender_id)})).one()
        slice_ = {
            "sends_1h": counts.s1,
            "sends_24h": counts.s24,
            "unique_contacts_1h": counts.u1,
            "unique_contacts_24h": counts.u24,
            "rate": {
                "configured_per_min": s.rate_per_min,
                "configured_per_hour": s.rate_per_hour,
                "configured_per_day": s.rate_per_day,
                "actual_per_hour": counts.s1,
                "actual_per_day": counts.s24,
            },
        }

    await db.execute(text("""
        INSERT INTO sender_restriction_events
          (workspace_id, sender_id, category, event_type, source,
           restricted_until, raw_text, activity_slice, proxy)
        VALUES (:wid, :sid, :cat, :etype, :src, :ru, :raw,
                CAST(:slice AS JSONB), CAST(:proxy AS JSONB))
    """), {
        "wid": str(s.workspace_id),
        "sid": str(sender_id),
        "cat": category,
        "etype": event_type,
        "src": source,
        "ru": restricted_until,
        "raw": raw_text,
        "slice": json.dumps(slice_) if slice_ is not None else None,
        "proxy": json.dumps(s.proxy) if s.proxy else None,
    })
