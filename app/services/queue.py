"""
Queue worker for rate-limited Telegram outbound messages.

Per-sender limits (safe values to avoid account freezes):
  - Delay between sends:  20–55 seconds randomised (with fatigue factor)
  - Max messages per minute: 4
  - Max messages per hour:  20
  - Max messages per day:   150
  - Max new contacts per hour: 15
  - Long pause every 12–25 messages: 3–10 minutes

Worker runs as an asyncio background task inside the API process.
No Redis or Celery needed — the queue lives in Postgres.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
import zoneinfo
from typing import Optional

import httpx
from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import MessageQueue, QueueItemStatus, QueueItemType, Sender, MessageLog, MessageType
from app.services.telegram import telegram_service, SessionAuthError
from telethon.errors import FloodWaitError
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── Rate-limit config ──────────────────────────────────────────────────────────
# Randomised interval between sends (human-like behaviour, avoids fixed-pattern detection)
MIN_SEND_INTERVAL = 20            # seconds — minimum pause between two sends
MAX_SEND_INTERVAL = 55            # seconds — maximum pause between two sends
# Fatigue factor: as msgs_last_hour approaches MAX_MSGS_PER_HOUR, interval grows by up to 50%
SEND_INTERVAL_FATIGUE = 0.5

MAX_MSGS_PER_MINUTE = 4
MAX_MSGS_PER_HOUR = 20            # conservative: Telegram bans at ~30/h to new contacts
MAX_MSGS_PER_DAY = 150            # daily ceiling across the 24-hour rolling window
MAX_NEW_CONTACTS_PER_HOUR = 15
MAX_ATTEMPTS = 3                  # retry failed items up to N times
RETRY_DELAY_SECONDS = 60          # wait before retrying a failed item

# ── Long-pause config (imitate human behaviour) ────────────────────────────────
# Every LONG_PAUSE_EVERY_MIN..MAX successfully sent messages take a longer break.
LONG_PAUSE_EVERY_MIN = 12         # randomised lower bound
LONG_PAUSE_EVERY_MAX = 25         # randomised upper bound
LONG_PAUSE_MIN_SECS = 180         # 3 minutes
LONG_PAUSE_MAX_SECS = 600         # 10 minutes

# ── FloodWait thresholds ───────────────────────────────────────────────────────
# At FLOOD_HARD_THRESHOLD seconds ALL pending tasks for the sender are rescheduled.
FLOOD_HARD_THRESHOLD = 300        # seconds

# ── Working hours (Moscow time) ────────────────────────────────────────────────
MOSCOW_TZ = zoneinfo.ZoneInfo("Europe/Moscow")
WORK_HOUR_START = 9   # 09:00 МСК
WORK_HOUR_END = 20    # до 20:00 МСК (последняя отправка в 19:59)
# ──────────────────────────────────────────────────────────────────────────────


class QueueWorker:
    """Background asyncio task that drains the message_queue table."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._idle_event: Optional[asyncio.Event] = None

    def start(self):
        if self._task is None or self._task.done():
            self._running = True
            self._idle_event = asyncio.Event()
            self._idle_event.set()   # initially idle
            self._task = asyncio.create_task(self._run(), name="queue-worker")
            logger.info("Queue worker started")

    async def stop(self):
        self._running = False
        # Wait for the current send to finish gracefully (up to 60s)
        if self._idle_event:
            try:
                await asyncio.wait_for(self._idle_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("Graceful shutdown: timeout after 60s, forcing stop")
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Queue worker stopped")

    # ── Main loop ──────────────────────────────────────────────────────────────

    async def _run(self):
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.error(f"Queue worker error: {exc}", exc_info=True)
            await asyncio.sleep(3)   # poll interval

    @staticmethod
    def _is_working_hours() -> bool:
        """Return True if current Moscow time is within working hours."""
        now_msk = datetime.now(MOSCOW_TZ)
        return WORK_HOUR_START <= now_msk.hour < WORK_HOUR_END

    @staticmethod
    def _next_working_window() -> datetime:
        """Return the next 09:00 MSK as UTC datetime."""
        now_msk = datetime.now(MOSCOW_TZ)
        next_open = now_msk.replace(hour=WORK_HOUR_START, minute=0, second=0, microsecond=0)
        if now_msk.hour >= WORK_HOUR_END:
            # After hours today — next window is tomorrow
            next_open += timedelta(days=1)
        return next_open.astimezone(timezone.utc)

    async def _tick(self):
        """Pick one ready item per sender and process it."""
        if not self._is_working_hours():
            next_open = self._next_working_window()
            logger.debug(
                f"Outside working hours (MSK {WORK_HOUR_START}:00–{WORK_HOUR_END}:00), "
                f"next window at {next_open.strftime('%H:%M UTC')}"
            )
            return

        async with AsyncSessionLocal() as db:
            # Get all senders that have pending work
            rows = await db.execute(
                text("""
                    SELECT DISTINCT sender_id
                    FROM message_queue
                    WHERE status = 'pending'
                      AND scheduled_at <= NOW()
                """)
            )
            sender_ids = [r[0] for r in rows.fetchall()]

        for sender_id in sender_ids:
            await self._process_next_for_sender(sender_id)
            # Small pause between different senders so we don't hammer PG
            await asyncio.sleep(0.5)

    # ── Per-sender processing ──────────────────────────────────────────────────

    async def _get_long_pause_seconds(self, sender_id) -> Optional[int]:
        """Return pause duration in seconds if a periodic long pause is due, else None.

        Every LONG_PAUSE_EVERY_MIN..MAX sent messages the worker takes a human-like
        break of LONG_PAUSE_MIN_SECS..MAX_SECS to avoid machine-pattern detection.
        The threshold is randomised so the pattern itself is unpredictable.
        """
        pause_every = random.randint(LONG_PAUSE_EVERY_MIN, LONG_PAUSE_EVERY_MAX)
        async with AsyncSessionLocal() as db:
            # Count messages sent in the last 30 minutes (rolling activity window)
            r = await db.execute(
                text("""
                    SELECT COUNT(*) FROM message_queue
                    WHERE sender_id = :sid
                      AND status = 'sent'
                      AND finished_at >= NOW() - INTERVAL '30 minutes'
                """),
                {"sid": str(sender_id)}
            )
            recent_count = r.scalar() or 0

        if recent_count > 0 and recent_count % pause_every == 0:
            return random.randint(LONG_PAUSE_MIN_SECS, LONG_PAUSE_MAX_SECS)
        return None

    async def _process_next_for_sender(self, sender_id):
        async with AsyncSessionLocal() as db:
            # Check per-sender rate limits before picking an item
            if not await self._check_rate_limits(db, sender_id):
                return

        # Check if a long human-like pause is due (outside the DB transaction)
        long_pause = await self._get_long_pause_seconds(sender_id)
        if long_pause:
            logger.info(
                f"Sender {sender_id}: long pause {long_pause}s "
                f"(human-behaviour pattern break)"
            )
            await asyncio.sleep(long_pause)

        async with AsyncSessionLocal() as db:
            # Pick the next pending item (SKIP LOCKED prevents double-processing)
            row = await db.execute(
                text("""
                    SELECT id FROM message_queue
                    WHERE sender_id = :sid
                      AND status = 'pending'
                      AND scheduled_at <= NOW()
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                """),
                {"sid": str(sender_id)}
            )
            item_row = row.fetchone()
            if not item_row:
                return

            item_id = item_row[0]

            # Mark as processing
            await db.execute(
                update(MessageQueue)
                .where(MessageQueue.id == item_id)
                .values(status=QueueItemStatus.processing, started_at=datetime.now(timezone.utc))
            )
            await db.commit()

        # Process outside the lock so we don't hold a TX while hitting Telegram
        await self._send_item(item_id)

    async def _check_rate_limits(self, db: AsyncSession, sender_id) -> bool:
        """Return False if the sender has hit any rate limit."""
        now = datetime.now(timezone.utc)
        one_minute_ago = now - timedelta(minutes=1)
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(hours=24)

        # Messages sent in last minute
        r = await db.execute(
            text("""
                SELECT COUNT(*) FROM message_queue
                WHERE sender_id = :sid
                  AND status = 'sent'
                  AND finished_at >= :since
            """),
            {"sid": str(sender_id), "since": one_minute_ago}
        )
        msgs_last_minute = r.scalar()
        if msgs_last_minute >= MAX_MSGS_PER_MINUTE:
            logger.info(f"Sender {sender_id}: per-minute limit reached ({msgs_last_minute}/{MAX_MSGS_PER_MINUTE}), pausing")
            return False

        # Messages sent in last hour
        r = await db.execute(
            text("""
                SELECT COUNT(*) FROM message_queue
                WHERE sender_id = :sid
                  AND status = 'sent'
                  AND finished_at >= :since
            """),
            {"sid": str(sender_id), "since": one_hour_ago}
        )
        msgs_last_hour = r.scalar()
        if msgs_last_hour >= MAX_MSGS_PER_HOUR:
            logger.warning(
                f"Sender {sender_id}: per-hour limit reached ({msgs_last_hour}/{MAX_MSGS_PER_HOUR}), "
                f"pausing until hour window slides"
            )
            return False

        # Unique contacts reached in the last hour
        r = await db.execute(
            text("""
                SELECT COUNT(DISTINCT recipient_phone) FROM message_queue
                WHERE sender_id = :sid
                  AND status = 'sent'
                  AND finished_at >= :since
            """),
            {"sid": str(sender_id), "since": one_hour_ago}
        )
        new_contacts_last_hour = r.scalar()
        if new_contacts_last_hour >= MAX_NEW_CONTACTS_PER_HOUR:
            logger.warning(
                f"Sender {sender_id}: unique contacts per-hour limit reached "
                f"({new_contacts_last_hour}/{MAX_NEW_CONTACTS_PER_HOUR}), pausing"
            )
            return False

        # Messages sent in last 24 hours (daily cap)
        r = await db.execute(
            text("""
                SELECT COUNT(*) FROM message_queue
                WHERE sender_id = :sid
                  AND status = 'sent'
                  AND finished_at >= :since
            """),
            {"sid": str(sender_id), "since": one_day_ago}
        )
        msgs_today = r.scalar()
        if msgs_today >= MAX_MSGS_PER_DAY:
            logger.warning(
                f"Sender {sender_id}: daily limit reached ({msgs_today}/{MAX_MSGS_PER_DAY}), "
                f"pausing until 24h window slides"
            )
            return False

        # Time since last send — randomised interval with fatigue factor
        r = await db.execute(
            text("""
                SELECT finished_at FROM message_queue
                WHERE sender_id = :sid
                  AND status = 'sent'
                ORDER BY finished_at DESC
                LIMIT 1
            """),
            {"sid": str(sender_id)}
        )
        last_row = r.fetchone()
        if last_row and last_row[0]:
            elapsed = (now - last_row[0]).total_seconds()
            # Fatigue: interval grows as we approach the hourly limit
            fatigue = 1.0 + (msgs_last_hour / MAX_MSGS_PER_HOUR) * SEND_INTERVAL_FATIGUE
            required_interval = random.uniform(MIN_SEND_INTERVAL, MAX_SEND_INTERVAL) * fatigue
            if elapsed < required_interval:
                logger.debug(
                    f"Sender {sender_id}: interval not elapsed "
                    f"({elapsed:.1f}s < {required_interval:.1f}s, fatigue={fatigue:.2f})"
                )
                return False

        return True

    # ── Actual send ────────────────────────────────────────────────────────────

    async def _send_item(self, item_id):
        if self._idle_event:
            self._idle_event.clear()   # mark as busy
        try:
            await self.__send_item_inner(item_id)
        finally:
            if self._idle_event:
                self._idle_event.set()   # mark as idle again

    async def __send_item_inner(self, item_id):
        async with AsyncSessionLocal() as db:
            # Load item + sender
            r = await db.execute(
                select(MessageQueue).where(MessageQueue.id == item_id)
            )
            item: MessageQueue = r.scalar_one_or_none()
            if not item:
                return

            r2 = await db.execute(select(Sender).where(Sender.id == item.sender_id))
            sender: Sender = r2.scalar_one_or_none()
            if not sender or not sender.is_active:
                await self._fail_item(db, item, "Sender not found or inactive")
                return

            client = None
            try:
                client = await telegram_service.get_client(sender.slug, sender.session_string, proxy=sender.proxy)

                if item.item_type == QueueItemType.file:
                    result = await telegram_service.send_file(
                        client=client,
                        phone=item.recipient_phone,
                        recipient_name=item.recipient_name,
                        file_url=item.file_url,
                        file_name=item.file_name,
                        caption=item.caption,
                        sender_id=str(sender.id)
                    )
                else:
                    result = await telegram_service.send_message(
                        client=client,
                        phone=item.recipient_phone,
                        recipient_name=item.recipient_name,
                        message=item.message_text,
                        as_draft=item.as_draft,
                        sender_id=str(sender.id)
                    )

                if result["success"]:
                    recipient = result.get("recipient", {})
                    await db.execute(
                        update(MessageQueue)
                        .where(MessageQueue.id == item_id)
                        .values(
                            status=QueueItemStatus.sent,
                            finished_at=datetime.now(timezone.utc),
                            result_message_id=result.get("message_id"),
                            result_recipient_telegram_id=recipient.get("telegram_id"),
                            result_recipient_name=recipient.get("name"),
                            result_recipient_username=recipient.get("username"),
                        )
                    )

                    # Write to messages_log
                    log_entry = MessageLog(
                        sender_id=sender.id,
                        recipient_phone=item.recipient_phone,
                        recipient_name=item.recipient_name,
                        recipient_telegram_id=recipient.get("telegram_id"),
                        message_text=item.message_text or f"[file: {item.file_url}]",
                        message_type=MessageType.sent,
                        extra_data=item.extra_data or {}
                    )
                    db.add(log_entry)

                    # Create/update conversation
                    await self._upsert_conversation(db, sender, item, result)

                    await db.commit()
                    logger.info(
                        f"Sent queued item {str(item_id)[:8]} "
                        f"to {item.recipient_phone} via {sender.slug}"
                    )

                    # Fire callback webhook (fire-and-forget)
                    if item.callback_url:
                        asyncio.create_task(self._fire_callback(
                            url=item.callback_url,
                            queue_id=str(item.id),
                            status="sent",
                            sender_slug=sender.slug,
                            recipient_phone=item.recipient_phone,
                            recipient_name=recipient.get("name"),
                            recipient_telegram_id=recipient.get("telegram_id"),
                            recipient_username=recipient.get("username"),
                            message_id=result.get("message_id"),
                            extra_data=item.extra_data,
                        ))
                else:
                    error = result.get("error", {})
                    error_code = error.get("code", "")
                    error_msg = error.get("message", "Unknown error")

                    # FloodWait: reschedule exactly as Telegram instructs, don't count as attempt
                    if error_code == "FLOOD_WAIT":
                        retry_after = error.get("retry_after", 300)
                        reschedule_at = datetime.now(timezone.utc) + timedelta(seconds=retry_after)

                        if retry_after >= FLOOD_HARD_THRESHOLD:
                            # Hard FloodWait — pause ALL pending tasks for this sender
                            logger.critical(
                                f"HARD FloodWait {retry_after}s for sender {sender.slug} — "
                                f"pausing all pending tasks until {reschedule_at.strftime('%H:%M:%S UTC')}"
                            )
                            async with AsyncSessionLocal() as db2:
                                await db2.execute(text("""
                                    UPDATE message_queue SET scheduled_at = :reschedule
                                    WHERE sender_id = :sid AND status = 'pending'
                                """), {"reschedule": reschedule_at, "sid": str(sender.id)})
                                await db2.commit()

                        await db.execute(
                            update(MessageQueue)
                            .where(MessageQueue.id == item.id)
                            .values(
                                status=QueueItemStatus.pending,
                                scheduled_at=reschedule_at,
                                error_message=error_msg,
                            )
                        )
                        await db.commit()
                        logger.warning(
                            f"Queue item {str(item.id)[:8]} hit FloodWait {retry_after}s — "
                            f"rescheduled until {reschedule_at.strftime('%H:%M:%S UTC')}"
                        )
                        return

                    elif error_code == "PEER_FLOOD":
                        # Spam restriction — worse than FloodWait, pause all tasks 24h
                        pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
                        async with AsyncSessionLocal() as db2:
                            await db2.execute(text("""
                                UPDATE message_queue SET scheduled_at = :pause_until
                                WHERE sender_id = :sid AND status = 'pending'
                            """), {"pause_until": pause_until, "sid": str(sender.id)})
                            await db2.commit()
                        logger.critical(
                            f"PEER_FLOOD for sender {sender.slug} — all tasks paused 24h "
                            f"until {pause_until.strftime('%Y-%m-%d %H:%M UTC')}. "
                            f"Manual account review required before resuming!"
                            # TODO: add external alert (webhook/email) when monitoring infrastructure is available
                        )
                        if item.callback_url:
                            asyncio.create_task(self._fire_callback(
                                url=item.callback_url,
                                queue_id=str(item.id),
                                status="failed",
                                sender_slug=sender.slug,
                                recipient_phone=item.recipient_phone,
                                error=error_msg,
                                extra_data=item.extra_data,
                            ))
                        await self._fail_item(db, item, error_msg)
                        return

                    # Fire failure callback before failing the item
                    if item.callback_url:
                        asyncio.create_task(self._fire_callback(
                            url=item.callback_url,
                            queue_id=str(item.id),
                            status="failed",
                            sender_slug=sender.slug,
                            recipient_phone=item.recipient_phone,
                            error=error_msg,
                            extra_data=item.extra_data,
                        ))
                    await self._fail_item(db, item, error_msg)

            except FloodWaitError as exc:
                # Telegram told us explicitly how long to wait — honour it
                retry_after = exc.seconds
                reschedule_at = datetime.now(timezone.utc) + timedelta(seconds=retry_after)

                if retry_after >= FLOOD_HARD_THRESHOLD:
                    logger.critical(
                        f"HARD FloodWait {retry_after}s for sender {sender.slug} — "
                        f"pausing all pending tasks until {reschedule_at.strftime('%H:%M:%S UTC')}"
                    )
                    async with AsyncSessionLocal() as db2:
                        await db2.execute(text("""
                            UPDATE message_queue SET scheduled_at = :reschedule
                            WHERE sender_id = :sid AND status = 'pending'
                        """), {"reschedule": reschedule_at, "sid": str(sender.id)})
                        await db2.commit()

                async with AsyncSessionLocal() as db2:
                    await db2.execute(
                        update(MessageQueue)
                        .where(MessageQueue.id == item.id)
                        .values(
                            status=QueueItemStatus.pending,
                            scheduled_at=reschedule_at,
                            error_message=f"FloodWait: retry after {retry_after}s",
                        )
                    )
                    await db2.commit()
                logger.warning(
                    f"Queue item {str(item.id)[:8]} hit FloodWait {retry_after}s (exception) — "
                    f"rescheduled until {reschedule_at.strftime('%H:%M:%S UTC')}"
                )

            except SessionAuthError as exc:
                # Session is dead — deactivate sender and fail all pending tasks
                logger.critical(
                    f"Auth error for sender {sender.slug}: {exc.auth_status} — "
                    f"deactivating sender and failing all pending tasks"
                )
                async with AsyncSessionLocal() as db2:
                    await db2.execute(text("""
                        UPDATE senders SET is_active = false
                        WHERE slug = :slug
                    """), {"slug": sender.slug})
                    await db2.execute(text("""
                        UPDATE message_queue
                        SET status = 'failed', error_message = :err, finished_at = NOW()
                        WHERE sender_id = :sid AND status IN ('pending', 'processing')
                    """), {"err": f"Sender auth failed: {exc.auth_status}", "sid": str(sender.id)})
                    await db2.commit()
                if item.callback_url:
                    asyncio.create_task(self._fire_callback(
                        url=item.callback_url,
                        queue_id=str(item.id),
                        status="failed",
                        sender_slug=sender.slug,
                        recipient_phone=item.recipient_phone,
                        error=f"Sender auth failed: {exc.auth_status}",
                        extra_data=item.extra_data,
                    ))

            except Exception as exc:
                logger.error(f"Queue item {str(item_id)[:8]} failed: {exc}", exc_info=True)
                if item.callback_url:
                    asyncio.create_task(self._fire_callback(
                        url=item.callback_url,
                        queue_id=str(item.id),
                        status="failed",
                        sender_slug=sender.slug,
                        recipient_phone=item.recipient_phone,
                        error=str(exc),
                        extra_data=item.extra_data,
                    ))
                await self._fail_item(db, item, str(exc))

    async def _fail_item(self, db: AsyncSession, item: MessageQueue, error: str):
        attempts = (item.attempts or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            new_status = QueueItemStatus.failed
            reschedule = None
            logger.warning(
                f"Queue item {str(item.id)[:8]} permanently failed after "
                f"{attempts} attempts: {error}"
            )
        else:
            new_status = QueueItemStatus.pending
            reschedule = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_SECONDS * attempts)
            logger.info(
                f"Queue item {str(item.id)[:8]} will retry "
                f"(attempt {attempts}/{MAX_ATTEMPTS}) at {reschedule}"
            )

        await db.execute(
            update(MessageQueue)
            .where(MessageQueue.id == item.id)
            .values(
                status=new_status,
                attempts=attempts,
                error_message=error,
                finished_at=datetime.now(timezone.utc) if new_status == QueueItemStatus.failed else None,
                scheduled_at=reschedule or item.scheduled_at,
            )
        )

        if new_status == QueueItemStatus.failed:
            log_entry = MessageLog(
                sender_id=item.sender_id,
                recipient_phone=item.recipient_phone,
                recipient_name=item.recipient_name,
                message_text=item.message_text or f"[file: {item.file_url}]",
                message_type=MessageType.failed,
                error_message=error,
                extra_data=item.extra_data or {}
            )
            db.add(log_entry)

        await db.commit()

    async def _upsert_conversation(self, db: AsyncSession, sender: Sender, item: MessageQueue, result: dict):
        """Mirror the conversation/message bookkeeping from send.py."""
        try:
            recipient_tg_id = result.get("recipient", {}).get("telegram_id")
            recipient_name = (
                result.get("recipient", {}).get("name")
                or item.recipient_name
                or item.recipient_phone
            )

            r = await db.execute(
                text("SELECT id FROM conversations WHERE sender_id = :sid AND contact_telegram_id = :tg_id"),
                {"sid": str(sender.id), "tg_id": recipient_tg_id}
            )
            conv_row = r.fetchone()

            if conv_row:
                conversation_id = str(conv_row[0])
            else:
                r2 = await db.execute(
                    text("""
                        INSERT INTO conversations
                            (sender_id, contact_phone, contact_name, contact_telegram_id, ai_enabled, ai_context_id)
                        VALUES (:sid, :phone, :name, :tg_id, true, :ai_ctx)
                        RETURNING id
                    """),
                    {
                        "sid": str(sender.id),
                        "phone": item.recipient_phone,
                        "name": recipient_name,
                        "tg_id": recipient_tg_id,
                        "ai_ctx": str(sender.ai_context_id) if sender.ai_context_id else None,
                    }
                )
                conversation_id = str(r2.fetchone()[0])

            message_id = result.get("message_id")
            if message_id and not item.as_draft:
                await db.execute(
                    text("""
                        INSERT INTO messages (conversation_id, direction, message_text, sent_by, telegram_message_id)
                        VALUES (:cid, 'outbound', :txt, 'human', :mid)
                        ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
                    """),
                    {
                        "cid": conversation_id,
                        "txt": item.message_text or f"[file: {item.file_url}]",
                        "mid": int(message_id),
                    }
                )
        except Exception as exc:
            logger.error(f"Failed to upsert conversation for queue item: {exc}")

    async def _fire_callback(
        self,
        url: str,
        queue_id: str,
        status: str,
        sender_slug: str,
        recipient_phone: str,
        recipient_name: Optional[str] = None,
        recipient_telegram_id: Optional[int] = None,
        recipient_username: Optional[str] = None,
        message_id: Optional[str] = None,
        error: Optional[str] = None,
        extra_data: Optional[dict] = None,
    ):
        """POST result to caller's callback_url. Fire-and-forget, never raises."""
        payload = {
            "queue_id": queue_id,
            "status": status,
            "sender_slug": sender_slug,
            "recipient_phone": recipient_phone,
            "recipient_name": recipient_name,
            "recipient_telegram_id": recipient_telegram_id,
            "recipient_username": recipient_username,
            "message_id": message_id,
            "error": error,
            "extra_data": extra_data or {},
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                logger.info(
                    f"Callback {url} → {resp.status_code} "
                    f"(queue_id={queue_id[:8]}, status={status})"
                )
        except Exception as exc:
            logger.warning(f"Callback failed for queue_id={queue_id[:8]}: {exc}")


# Singleton used by main.py and routers
queue_worker = QueueWorker()


# ── Helper functions used by the send router ──────────────────────────────────

async def enqueue_message(
    db: AsyncSession,
    sender_id,
    sender_slug: str,
    recipient_phone: str,
    recipient_name: Optional[str],
    message_text: str,
    as_draft: bool = False,
    metadata: Optional[dict] = None,
    priority: int = 0,
    callback_url: Optional[str] = None,
) -> dict:
    """Add a message to the queue. Returns queue info dict."""
    item = MessageQueue(
        sender_id=sender_id,
        item_type=QueueItemType.message,
        recipient_phone=recipient_phone,
        recipient_name=recipient_name,
        message_text=message_text,
        as_draft=as_draft,
        extra_data=metadata or {},
        priority=priority,
        callback_url=callback_url,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)

    position = await _queue_position(db, sender_id, item.id)
    estimated = _estimate_send_time(position)

    return {
        "queue_id": str(item.id),
        "queue_position": position,
        "estimated_send_at": estimated,
    }


async def enqueue_file(
    db: AsyncSession,
    sender_id,
    sender_slug: str,
    recipient_phone: str,
    recipient_name: Optional[str],
    file_url: str,
    file_name: Optional[str],
    caption: Optional[str],
    metadata: Optional[dict] = None,
    priority: int = 0,
    callback_url: Optional[str] = None,
) -> dict:
    """Add a file send to the queue. Returns queue info dict."""
    item = MessageQueue(
        sender_id=sender_id,
        item_type=QueueItemType.file,
        recipient_phone=recipient_phone,
        recipient_name=recipient_name,
        file_url=file_url,
        file_name=file_name,
        caption=caption,
        extra_data=metadata or {},
        priority=priority,
        callback_url=callback_url,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)

    position = await _queue_position(db, sender_id, item.id)
    estimated = _estimate_send_time(position)

    return {
        "queue_id": str(item.id),
        "queue_position": position,
        "estimated_send_at": estimated,
    }


async def _queue_position(db: AsyncSession, sender_id, item_id) -> int:
    """How many pending items are ahead of this one for the same sender."""
    r = await db.execute(
        text("""
            SELECT COUNT(*) FROM message_queue
            WHERE sender_id = :sid
              AND status = 'pending'
              AND (priority, created_at) > (
                  SELECT priority, created_at FROM message_queue WHERE id = :iid
              )
        """),
        {"sid": str(sender_id), "iid": str(item_id)}
    )
    return (r.scalar() or 0) + 1  # 1-based


def _estimate_send_time(position: int) -> datetime:
    """Rough ETA based on queue position and average configured interval."""
    avg_interval = (MIN_SEND_INTERVAL + MAX_SEND_INTERVAL) / 2
    return datetime.now(timezone.utc) + timedelta(seconds=avg_interval * position)


async def recover_stuck_jobs() -> int:
    """Recover jobs stuck in 'processing' state after a container restart.

    Any item that has been 'processing' for more than 10 minutes is considered
    orphaned (the worker that picked it up died mid-send). These are returned to
    'pending' so the queue worker can retry them on the next tick.

    10-minute threshold accounts for:
      - flood_sleep_threshold auto-sleep up to 60s
      - network timeouts on Telegram connect/send (~30s)
      - ResolvePhoneRequest latency
    Returns the number of recovered items.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                UPDATE message_queue
                SET status = 'pending',
                    scheduled_at = NOW(),
                    started_at = NULL
                WHERE status = 'processing'
                  AND started_at < NOW() - INTERVAL '10 minutes'
                RETURNING id
            """)
        )
        await db.commit()
        count = len(result.fetchall())
        if count:
            logger.warning(f"Startup recovery: restored {count} stuck job(s) from 'processing' to 'pending'")
        else:
            logger.info("Startup recovery: no stuck jobs found")
        return count
