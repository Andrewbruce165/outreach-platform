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

D-01 (event only on state-change / forward-shift): the GATE that suppresses
no-shift reconcile ticks lives at the listener call-site (it compares old_until
read intra-transaction against the new release date) — NOT here. This helper
just writes whatever event the caller decided to record.

D-05 (snapshot at write time): the activity_slice is computed here, inline, in
the caller's transaction — never reconstructed later from ephemeral sources.

PII discipline (CLAUDE.md): raw_text carries only the human-facing Telegram
error / @SpamBot reply text; the proxy snapshot lives in its own structured
``proxy`` JSONB column. No API_KEY, session strings, or proxy credentials are
ever logged into raw_text.
"""

import json
import logging
from datetime import datetime
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
    s = (await db.execute(text("""
        SELECT workspace_id, proxy, rate_per_min, rate_per_hour, rate_per_day
        FROM senders WHERE id = :sid
    """), {"sid": str(sender_id)})).one()

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
