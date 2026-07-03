"""Phase 19 (NORP-04/06/09/10/11/12) — FollowUpWorker: the timer-driven state
machine for No Reply Follow-Up and Auto-Finish.

Pattern: ``CampaignEnqueueWorker`` (Phase 4) — module-level singleton,
``start()`` / ``stop()`` registered in the FastAPI lifespan, a broad-except
``_run`` loop that never dies, ``asyncio.sleep(poll_interval)`` between ticks.

Tick algorithm (one ``AsyncSessionLocal`` session):
    1. SELECT eligible conversations JOINed to their campaign (and owning
       sender), gated on ``campaigns.status='running' AND follow_up_enabled=true``
       and ``conversations.status IN ('active','no_reply')`` — D-06/D-16.
       ``FOR UPDATE OF c SKIP LOCKED`` so concurrent ticks never double-process.
    2. Derive ``last_outbound_at = MAX(messages.created_at WHERE direction=
       'outbound')`` lazily per conversation (RESEARCH Pattern 2 recommendation —
       no stored column). This is the silence / auto-finish anchor (D-04/D-10).
       NULL (no outbound yet) → skip.
    3. AUTO-FINISH FIRST (D-08 "whichever comes first"): if
       ``NOW()-last_outbound_at >= auto_finish_hours`` OR
       ``pings_sent >= follow_up_max_pings`` → flip ``status='finished'``, cancel
       pending pings, fire the finish webhook with ``reason='no_reply'`` (D-09).
       Do NOT also ping. D-15 (toggle-enable of a campaign whose dialogs are
       already past ``auto_finish_hours``) is satisfied by this branch firing on
       the very first tick.
    4. PING (else) (D-02/D-04): if ``NOW()-last_outbound_at >=
       follow_up_interval_hours`` AND ``pings_sent < follow_up_max_pings`` →
       if the owning sender is restricted skip this tick (D-14); else flip an
       ``active`` conversation to ``no_reply`` (D-02, only from active), generate
       the ping via ``ai_engine.generate_followup_ping`` (Plan 19-02), enqueue it
       as a follow-up item to the owning sender (D-13/D-14), and increment
       ``pings_sent``.

Note (D-16 / RESEARCH Open Question 1): pause semantics use wall-clock — a
campaign paused mid-silence does NOT get its silence clock reset on resume for
v1 (elapsed-during-pause still counts). No pause-duration compensation.

Done-side ping cancel is handled elsewhere: the campaign finish/stop endpoint
runs ``_cancel_pending_queue`` (campaigns.py) — this tick only adds the
``running AND follow_up_enabled`` gate (D-16).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services.ai_engine import ai_engine
from app.services.queue import enqueue_message
from app.services.webhook_notify import notify_signal

logger = logging.getLogger(__name__)

# Sane per-tick cap — interval bounds are in hours, so a modest batch drains
# comfortably within the tick window.
BATCH_LIMIT = 200


class FollowUpWorker:
    """Background worker driving the no_reply → ping → auto-finish state machine."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.poll_interval = get_settings().follow_up_tick_seconds

    def start(self):
        """Start background task. Idempotent."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="follow-up-worker")
            logger.info("🔔 FollowUpWorker started (poll=%ss)", self.poll_interval)

    async def stop(self):
        """Stop background task gracefully (cancel + await)."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🔔 FollowUpWorker stopped")

    async def _run(self):
        """Main loop — sleep after tick so startup is not blocked."""
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — worker must not die
                logger.error("❌ FollowUpWorker tick error: %s", exc, exc_info=True)
            await asyncio.sleep(self.poll_interval)

    async def tick(self) -> int:
        """One tick — sweep eligible conversations. Returns count of actions taken
        (auto-finishes + pings enqueued)."""
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(text("""
                SELECT c.id, c.status, c.pings_sent, c.workspace_id, c.sender_id,
                       c.contact_phone, c.contact_name, c.contact_telegram_id,
                       c.campaign_id,
                       cp.follow_up_interval_hours, cp.follow_up_max_pings,
                       cp.auto_finish_hours, cp.name AS campaign_name,
                       cp.finish_webhook_url, cp.webhook_url,
                       s.slug AS sender_slug,
                       s.restriction_status AS sender_restriction_status,
                       lo.last_outbound_at
                FROM conversations c
                JOIN campaigns cp ON cp.id = c.campaign_id
                JOIN senders s ON s.id = c.sender_id
                LEFT JOIN LATERAL (
                    SELECT MAX(m.created_at) AS last_outbound_at
                    FROM messages m
                    WHERE m.conversation_id = c.id
                      AND m.direction = 'outbound'
                ) lo ON true
                WHERE cp.status = 'running'
                  AND cp.follow_up_enabled = true
                  AND c.status IN ('active', 'no_reply')
                FOR UPDATE OF c SKIP LOCKED
                LIMIT :lim
            """), {"lim": BATCH_LIMIT})).fetchall()

            actions = 0
            for r in rows:
                try:
                    if await self._process_conversation(db, r):
                        actions += 1
                except Exception as exc:  # noqa: BLE001 — one bad row never kills the tick
                    logger.error(
                        "FollowUpWorker: error processing conversation %s: %s",
                        r.id, exc, exc_info=True,
                    )
                    await db.rollback()

            if actions:
                logger.info("🔔 FollowUpWorker tick: %s actions across %s eligible", actions, len(rows))
            return actions

    async def _process_conversation(self, db: AsyncSession, r) -> bool:
        """Apply the auto-finish-first / ping-else rule to one conversation.

        Returns True if an action (auto-finish or ping enqueue) was taken.
        """
        # D-04/D-10: the timer is anchored to the last OUTBOUND message. No
        # outbound yet → the opener hasn't gone out; nothing to time against.
        if r.last_outbound_at is None:
            return False

        now = datetime.now(timezone.utc)
        elapsed = now - r.last_outbound_at

        # ── AUTO-FINISH FIRST (D-08 "whichever comes first") ──────────────────
        auto_finish = (
            elapsed >= timedelta(hours=r.auto_finish_hours)
            or r.pings_sent >= r.follow_up_max_pings
        )
        if auto_finish:
            await self._auto_finish(db, r)
            return True

        # ── PING (else) (D-02/D-04) ───────────────────────────────────────────
        if elapsed >= timedelta(hours=r.follow_up_interval_hours) and r.pings_sent < r.follow_up_max_pings:
            return await self._ping(db, r)

        return False

    async def _auto_finish(self, db: AsyncSession, r) -> None:
        """D-09: flip to finished, cancel pending pings, fire finish webhook
        reason='no_reply'."""
        # Guard the UPDATE against a status that changed since the SELECT
        # (e.g. a reply reverted it) — only finish an active/no_reply dialog.
        await db.execute(text("""
            UPDATE conversations
            SET status = 'finished', updated_at = NOW()
            WHERE id = :cid AND status IN ('active', 'no_reply')
        """), {"cid": str(r.id)})

        # Cancel any pending follow-up pings for this conversation's contact on
        # the owning sender (they'd fire into a now-finished dialog).
        await db.execute(text("""
            UPDATE message_queue
            SET status = 'cancelled',
                error_message = 'conversation auto-finished (no_reply)'
            WHERE campaign_id = :cid AND recipient_phone = :phone
              AND sender_id = :sid AND status = 'pending'
        """), {"cid": str(r.campaign_id), "phone": r.contact_phone, "sid": str(r.sender_id)})

        await db.commit()

        # Fire-and-forget finish webhook with the no_reply marker (D-09).
        campaign = {
            "id": r.campaign_id,
            "name": r.campaign_name,
            "workspace_id": r.workspace_id,
            "finish_webhook_url": r.finish_webhook_url,
            "webhook_url": r.webhook_url,
        }
        contact = {
            "phone": r.contact_phone,
            "telegram_id": r.contact_telegram_id,
            "name": r.contact_name,
        }
        await notify_signal(
            event_type="finish",
            campaign=campaign,
            conversation_id=r.id,
            contact=contact,
            reason="no_reply",
            db=db,
        )
        logger.info("🔔 Auto-finished conversation %s (no_reply, pings_sent=%s)", r.id, r.pings_sent)

    async def _ping(self, db: AsyncSession, r) -> bool:
        """D-02/D-13/D-14: enqueue a single follow-up ping to the owning sender.

        Returns True if a ping was enqueued.
        """
        # D-14: skip if the owning sender is restricted — retry next tick. The
        # auto-finish clock still runs, so a durably-restricted dialog closes on
        # the time threshold above regardless.
        if r.sender_restriction_status and r.sender_restriction_status != "none":
            logger.info(
                "🔔 Skip ping for conversation %s — sender %s restricted (%s)",
                r.id, r.sender_slug, r.sender_restriction_status,
            )
            return False

        # Guard double-enqueue: a pending ping for this contact already sits in
        # the queue — don't stack another.
        pending = (await db.execute(text("""
            SELECT 1 FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = :phone
              AND sender_id = :sid AND status = 'pending'
            LIMIT 1
        """), {"cid": str(r.campaign_id), "phone": r.contact_phone, "sid": str(r.sender_id)})).first()
        if pending is not None:
            return False

        # Generate the ping text (Plan 19-02). None → no agent context / provider
        # failure → skip this tick and retry next (no status/counter change).
        ping_text = await ai_engine.generate_followup_ping(db, str(r.id))
        if not ping_text or not ping_text.strip():
            logger.info("🔔 No ping text for conversation %s — skip this tick", r.id)
            return False

        # D-02: an active conversation flips to no_reply on the first ping; a
        # no_reply conversation stays no_reply. Guard on current status.
        if r.status == "active":
            await db.execute(text("""
                UPDATE conversations SET status = 'no_reply', updated_at = NOW()
                WHERE id = :cid AND status = 'active'
            """), {"cid": str(r.id)})

        # Increment the ping counter (D-08 ceiling driver). Pending with the
        # UPDATE above; enqueue_message's commit flushes both + the queue insert
        # together.
        await db.execute(text("""
            UPDATE conversations SET pings_sent = pings_sent + 1, updated_at = NOW()
            WHERE id = :cid
        """), {"cid": str(r.id)})

        # D-13: enqueue as a message_queue follow-up item tagged kind='followup'.
        # A prior 'sent' opener makes this a follow-up by construction, so the
        # queue bypasses the new-dialog cap/pacing but obeys rate limits +
        # working hours (no changes to queue throttling here). D-14: owning
        # sender only.
        await enqueue_message(
            db,
            workspace_id=r.workspace_id,
            sender_id=r.sender_id,
            sender_slug=r.sender_slug,
            recipient_phone=r.contact_phone,
            recipient_name=r.contact_name,
            message_text=ping_text,
            metadata={"kind": "followup"},
            campaign_id=r.campaign_id,
        )
        logger.info("🔔 Enqueued follow-up ping for conversation %s (sender %s)", r.id, r.sender_slug)
        return True


# Module-level singleton — registered in app/main.py lifespan.
follow_up_worker = FollowUpWorker()
