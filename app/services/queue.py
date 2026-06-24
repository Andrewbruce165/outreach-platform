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
from uuid import UUID

import httpx
from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import MessageQueue, QueueItemStatus, QueueItemType, Sender, MessageLog, MessageType
from app.services.telegram import telegram_service, SessionAuthError
from app.services.recontact import protected_conversation_sql
from app.services.restriction_audit import record_restriction_event
from telethon.errors import FloodWaitError
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── Rate-limit config ──────────────────────────────────────────────────────────
# Randomised interval between sends (human-like behaviour, avoids fixed-pattern detection)
MIN_SEND_INTERVAL = 20            # seconds — minimum pause between two sends
MAX_SEND_INTERVAL = 55            # seconds — maximum pause between two sends
# Fatigue factor: as msgs_last_hour approaches sender.rate_per_hour, interval grows by up to 50%
SEND_INTERVAL_FATIGUE = 0.5

# Per-sender rate limits live on senders.rate_per_min/hour/day columns (Phase 2 D-13).
# The same empirically-tuned 4/20/150 "green corridor" remains as DB server_default.
# NB: Other rate constants (MIN_SEND_INTERVAL, LONG_PAUSE_*, FLOOD_HARD_THRESHOLD)
#     — NOT TOUCHED per CLAUDE.md. Working-hours globals MOSCOW_TZ /
#     WORK_HOUR_START / WORK_HOUR_END were removed in Phase 4 Plan 04-03 —
#     scheduling is now per-campaign (see `_campaign_in_working_window` below).
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

# ── Queue tick batch ───────────────────────────────────────────────────────────
# Maximum pending items to inspect per tick (per-sender pick happens later).
QUEUE_TICK_BATCH = 500
# ──────────────────────────────────────────────────────────────────────────────


def _campaign_in_working_window(
    *,
    campaign_tz: str,
    work_hour_start: int,
    work_hour_end: int,
    work_days_mask: int,
    now: Optional[datetime] = None,
) -> bool:
    """Per-campaign working-hours check (Phase 4 D-08, D-09, D-10).

    Args:
        campaign_tz: IANA timezone name (e.g. ``'Europe/Moscow'``).
        work_hour_start: 0-23, inclusive (start of daily send window).
        work_hour_end: 1-24, exclusive (end of daily send window).
        work_days_mask: bitmask Mo=1, Tu=2, We=4, Th=8, Fr=16, Sa=32, Su=64.
        now: datetime to test, defaults to ``datetime.now(timezone.utc)``.

    Returns True iff ``now`` (after conversion to ``campaign_tz``) falls into
    the half-open hour interval and the weekday bit is set in the mask.
    Invalid timezone → returns False (logged at WARNING).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        tz = zoneinfo.ZoneInfo(campaign_tz)
    except zoneinfo.ZoneInfoNotFoundError as exc:
        logger.warning(f"Invalid campaign timezone '{campaign_tz}': {exc}")
        return False
    except Exception as exc:  # safety net — any unexpected tz error
        logger.warning(f"Failed to resolve campaign timezone '{campaign_tz}': {exc}")
        return False

    local_now = now.astimezone(tz)
    weekday_bit = 1 << local_now.weekday()  # Mo=0 → 1, Tu=1 → 2, …, Su=6 → 64
    if (work_days_mask & weekday_bit) == 0:
        return False
    if not (work_hour_start <= local_now.hour < work_hour_end):
        return False
    return True


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

    async def _tick(self):
        """Pick one ready item per eligible sender and process it.

        Phase 4 (D-08..D-11, D-15): JOIN to ``campaigns`` and filter on the
        campaign side instead of the old global MSK working-hours check.

        WHERE conditions evaluated in SQL:
          * ``mq.status = 'pending'``
          * ``mq.scheduled_at <= NOW()``
          * ``mq.campaign_id IS NOT NULL`` — defence-in-depth (INNER JOIN
            already excludes NULL, kept explicit per H4 revision)
          * ``c.status = 'running'``  — paused/done/draft campaigns skipped
          * ``c.start_date IS NULL OR NOW() >= c.start_date``

        Python-side post-filter (``zoneinfo`` is awkward in SQL):
          * ``stop_date`` past → mark item as failed (D-11 soft skip).
          * working-hours window per campaign timezone + work_days_mask.
        """
        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                text("""
                    SELECT
                        mq.id AS item_id,
                        mq.sender_id AS sender_id,
                        c.id AS c_id,
                        c.timezone AS c_tz,
                        c.work_hour_start AS c_whs,
                        c.work_hour_end AS c_whe,
                        c.work_days_mask AS c_wdm,
                        c.stop_date AS c_stop
                    FROM message_queue mq
                    JOIN campaigns c ON c.id = mq.campaign_id
                    WHERE mq.status = 'pending'
                      AND mq.scheduled_at <= NOW()
                      AND mq.campaign_id IS NOT NULL
                      AND c.status = 'running'
                      AND (c.start_date IS NULL OR NOW() >= c.start_date)
                    ORDER BY mq.scheduled_at ASC
                    LIMIT :batch
                """),
                {"batch": QUEUE_TICK_BATCH},
            )
            fetched = rows.fetchall()

            now_utc = datetime.now(timezone.utc)
            items_to_fail: list = []
            eligible_sender_ids: list = []
            seen_senders: set = set()

            for r in fetched:
                # D-11 (soft skip) — past stop_date → mark failed.
                if r.c_stop is not None and now_utc >= r.c_stop:
                    items_to_fail.append(r.item_id)
                    continue
                in_window = _campaign_in_working_window(
                    campaign_tz=r.c_tz,
                    work_hour_start=r.c_whs,
                    work_hour_end=r.c_whe,
                    work_days_mask=r.c_wdm,
                    now=now_utc,
                )
                if not in_window:
                    # SKIP — item stays pending, next tick will retry.
                    continue
                if r.sender_id not in seen_senders:
                    seen_senders.add(r.sender_id)
                    eligible_sender_ids.append(r.sender_id)

            if items_to_fail:
                await db.execute(
                    text("""
                        UPDATE message_queue
                        SET status = 'failed',
                            error_message = 'past_stop_date',
                            finished_at = NOW()
                        WHERE id = ANY(:ids)
                    """),
                    {"ids": [str(i) for i in items_to_fail]},
                )
                await db.commit()
                logger.info(
                    f"Marked {len(items_to_fail)} queue items as failed "
                    f"(past campaign.stop_date)"
                )

        for sender_id in eligible_sender_ids:
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
            # Pick the next pending item (SKIP LOCKED prevents double-processing).
            # Phase 4 (D-08..D-11, D-15): per-campaign JOIN + working-window
            # re-check at pick time — `_tick` may have decided this sender is
            # eligible based on one campaign, but the actual SELECT picks the
            # next item by (priority, created_at); we must re-verify the
            # campaign of THAT item is still running/in-window.
            rows = await db.execute(
                text("""
                    SELECT
                        mq.id AS item_id,
                        c.timezone AS c_tz,
                        c.work_hour_start AS c_whs,
                        c.work_hour_end AS c_whe,
                        c.work_days_mask AS c_wdm,
                        c.stop_date AS c_stop
                    FROM message_queue mq
                    JOIN campaigns c ON c.id = mq.campaign_id
                    WHERE mq.sender_id = :sid
                      AND mq.status = 'pending'
                      AND mq.scheduled_at <= NOW()
                      AND mq.campaign_id IS NOT NULL
                      AND c.status = 'running'
                      AND (c.start_date IS NULL OR NOW() >= c.start_date)
                    ORDER BY mq.priority DESC, mq.created_at ASC
                    LIMIT 8
                    FOR UPDATE OF mq SKIP LOCKED
                """),
                {"sid": str(sender_id)}
            )

            now_utc = datetime.now(timezone.utc)
            item_id = None
            stop_date_failed_ids: list = []
            for r in rows.fetchall():
                if r.c_stop is not None and now_utc >= r.c_stop:
                    stop_date_failed_ids.append(r.item_id)
                    continue
                if _campaign_in_working_window(
                    campaign_tz=r.c_tz,
                    work_hour_start=r.c_whs,
                    work_hour_end=r.c_whe,
                    work_days_mask=r.c_wdm,
                    now=now_utc,
                ):
                    item_id = r.item_id
                    break

            if stop_date_failed_ids:
                await db.execute(
                    text("""
                        UPDATE message_queue
                        SET status = 'failed',
                            error_message = 'past_stop_date',
                            finished_at = NOW()
                        WHERE id = ANY(:ids)
                    """),
                    {"ids": [str(i) for i in stop_date_failed_ids]},
                )

            if item_id is None:
                await db.commit()
                return

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
        """Return False if the sender has hit any rate limit.

        Phase 2 (D-13): rate limits живут per-sender в senders.rate_per_min/hour/day.
        Sender row читаем один раз в начале tick'а, глобальные константы выпилены.
        """
        now = datetime.now(timezone.utc)
        one_minute_ago = now - timedelta(minutes=1)
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(hours=24)

        # Phase 2 D-13: read per-sender rate limits from DB (once per tick).
        # Если sender удалён concurrently — пропускаем тик (вернёт False).
        sender_row = (await db.execute(
            text("""
                SELECT rate_per_min, rate_per_hour, rate_per_day,
                       lifecycle_status, auth_status,
                       restriction_status, restricted_until
                FROM senders WHERE id = :sid
            """),
            {"sid": str(sender_id)},
        )).fetchone()

        if not sender_row:
            logger.warning(f"Sender {sender_id}: row missing — skipping tick")
            return False

        # Phase 2 D-11/D-12: derived 'error' / paused — пропускаем.
        if sender_row.lifecycle_status != "active" or sender_row.auth_status != "ok":
            logger.debug(
                f"Sender {sender_id}: not eligible "
                f"(lifecycle={sender_row.lifecycle_status} auth={sender_row.auth_status})"
            )
            return False

        # Migration 028: don't burn sends on a restricted (spam_limited/frozen)
        # account. The listener reconcile sweep clears the flag once SpamBot says
        # the account is free again. While restricted_until is in the future we skip;
        # once it elapses we let the sweep (not the worker) re-check.
        if sender_row.restriction_status != "none":
            logger.debug(
                f"Sender {sender_id}: restricted "
                f"({sender_row.restriction_status}, until={sender_row.restricted_until}) — skipping tick"
            )
            return False

        max_per_min = sender_row.rate_per_min
        max_per_hour = sender_row.rate_per_hour
        max_per_day = sender_row.rate_per_day

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
        if msgs_last_minute >= max_per_min:
            logger.info(f"Sender {sender_id}: per-minute limit reached ({msgs_last_minute}/{max_per_min}), pausing")
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
        if msgs_last_hour >= max_per_hour:
            logger.warning(
                f"Sender {sender_id}: per-hour limit reached ({msgs_last_hour}/{max_per_hour}), "
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
        if msgs_today >= max_per_day:
            logger.warning(
                f"Sender {sender_id}: daily limit reached ({msgs_today}/{max_per_day}), "
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
            fatigue = 1.0 + (msgs_last_hour / max_per_hour) * SEND_INTERVAL_FATIGUE
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
            # Phase 2 D-11/D-12: derived 'error' / paused — fail the item.
            if not sender:
                await self._fail_item(db, item, "Sender not found")
                return
            if sender.lifecycle_status != "active" or sender.auth_status != "ok":
                await self._fail_item(
                    db, item,
                    f"Sender not eligible (lifecycle={sender.lifecycle_status} "
                    f"auth={sender.auth_status})"
                )
                return

            # === Phase 5 D-04 / Pitfall 6: Pre-send guard against race condition ===
            # Менеджер мог нажать POST /conversations/{id}/send одновременно с
            # очередью worker. /send уже UPDATE'нул conversation.ai_enabled=false
            # и cancel-queue для pending, но этот item уже был помечен 'processing'
            # к этому моменту — попал бы в Telegram несмотря на ручник.
            #
            # Один SELECT по conversations: если ai_enabled=false → SKIP send.
            # CLAUDE.md guard: НЕ трогаем эмпирические интервалы rate-limit /
            # debounce / long-pause / flood-threshold — только один SELECT и
            # одно UPDATE на queue item.
            #
            # Re-contact (migration 026): под allow_recontact закрытый/протухший
            # диалог НЕ продолжается — _upsert_conversation откроет новую строку
            # (чистый старт). Значит guard должен смотреть ai_enabled только у
            # ЗАЩИЩЁННОГО (живого и свежего) диалога — иначе старый finished-диалог
            # с ai_enabled=false (закрытый/перехваченный ранее) ложно срубит
            # легитимный cold opener. Предикат общий с _upsert_conversation и
            # campaign_enqueue через recontact.protected_conversation_sql.
            allow_recontact = False
            recontact_age = 30
            if item.campaign_id is not None:
                camp_row = (await db.execute(text("""
                    SELECT allow_recontact, recontact_min_age_days
                    FROM campaigns WHERE id = :cid
                """), {"cid": str(item.campaign_id)})).fetchone()
                if camp_row is not None:
                    allow_recontact = bool(camp_row.allow_recontact)
                    recontact_age = int(camp_row.recontact_min_age_days)

            guard_params = {
                "wid": str(item.workspace_id),
                "sid": str(item.sender_id),
                "phone": item.recipient_phone,
            }
            if allow_recontact:
                guard_sql = f"""
                    SELECT ai_enabled FROM conversations
                    WHERE workspace_id = :wid
                      AND sender_id = :sid
                      AND contact_phone = :phone
                      AND {protected_conversation_sql("age_days")}
                    ORDER BY updated_at DESC LIMIT 1
                """
                guard_params["age_days"] = recontact_age
            else:
                guard_sql = """
                    SELECT ai_enabled FROM conversations
                    WHERE workspace_id = :wid
                      AND sender_id = :sid
                      AND contact_phone = :phone
                    LIMIT 1
                """
            guard_row = (await db.execute(text(guard_sql), guard_params)).first()

            if guard_row is not None and guard_row.ai_enabled is False:
                logger.info(
                    "⏭️  Pre-send guard: skipping queue item %s — "
                    "conversation taken over manually",
                    str(item.id)[:8],
                )
                await db.execute(
                    update(MessageQueue)
                    .where(MessageQueue.id == item.id)
                    .values(
                        status=QueueItemStatus.failed,
                        error_message="Conversation taken over manually",
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
                return
            # === End Phase 5 pre-send guard ===

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
                        sender_id=str(sender.id),
                        workspace_id=str(item.workspace_id),
                    )
                else:
                    result = await telegram_service.send_message(
                        client=client,
                        phone=item.recipient_phone,
                        recipient_name=item.recipient_name,
                        message=item.message_text,
                        as_draft=item.as_draft,
                        sender_id=str(sender.id),
                        workspace_id=str(item.workspace_id),
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
                    # Phase 02.1 CR-01: workspace_id NOT NULL after migration 012 —
                    # propagate from sender to avoid NotNullViolation on first send.
                    log_entry = MessageLog(
                        workspace_id=sender.workspace_id,
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
                                # Phase 10 (HLTH-01 / OQ#1): informational flood_wait event.
                                # This path only PAUSES the queue — it does NOT change
                                # senders.restriction_status, so pool_health is unaffected.
                                await record_restriction_event(
                                    sender.id, "flood_wait", "queue_error",
                                    reschedule_at, error_msg, db=db2,
                                )
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
                        # Spam restriction — worse than FloodWait, pause all tasks 24h.
                        # Migration 028: also flag the sender as spam_limited so the UI
                        # stops showing 'active' and the listener reconcile sweep re-checks
                        # via SpamBot once restricted_until elapses (recheck interval, not
                        # the 24h queue pause — the empirical pause is left untouched).
                        pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
                        recheck_at = datetime.now(timezone.utc) + timedelta(
                            seconds=get_settings().restriction_recheck_interval_seconds
                        )
                        async with AsyncSessionLocal() as db2:
                            await db2.execute(text("""
                                UPDATE message_queue SET scheduled_at = :pause_until
                                WHERE sender_id = :sid AND status = 'pending'
                            """), {"pause_until": pause_until, "sid": str(sender.id)})
                            await db2.execute(text("""
                                UPDATE senders
                                SET restriction_status = 'spam_limited',
                                    restricted_until = :recheck_at
                                WHERE id = :sid
                            """), {"recheck_at": recheck_at, "sid": str(sender.id)})
                            # Phase 10 (HLTH-01): durable restriction event in the SAME
                            # TX as the status UPDATE (audit + state never diverge).
                            await record_restriction_event(sender.id, "spam_limited", "queue_error", recheck_at, error_msg, db=db2)
                            await db2.commit()
                        logger.critical(
                            f"PEER_FLOOD for sender {sender.slug} — all tasks paused 24h "
                            f"until {pause_until.strftime('%Y-%m-%d %H:%M UTC')}. "
                            f"Sender flagged spam_limited (recheck "
                            f"{recheck_at.strftime('%Y-%m-%d %H:%M UTC')}). "
                            f"Manual account review required before resuming!"
                            # TODO: add external alert (webhook/email) when monitoring infrastructure is available
                        )
                        # Phase 9 (FAIL-02): the sender is now flagged spam_limited
                        # (committed above), so the healthy-pool candidate filter
                        # excludes it. Move its cold-pending backlog onto healthy
                        # senders so cold contacts don't stall 24h. db=None → the
                        # helper owns + commits its own session.
                        from app.services.failover import failover_cold_backlog
                        await failover_cold_backlog(sender.id)
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

                    elif error_code == "ACCOUNT_FROZEN":
                        # Migration 028: Telegram froze the account (FROZEN_*). All writes
                        # are blocked until appeal — pause pending and flag the sender frozen.
                        # The reconcile sweep re-checks via SpamBot and lifts on its own.
                        pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
                        recheck_at = datetime.now(timezone.utc) + timedelta(
                            seconds=get_settings().restriction_recheck_interval_seconds
                        )
                        async with AsyncSessionLocal() as db2:
                            await db2.execute(text("""
                                UPDATE message_queue SET scheduled_at = :pause_until
                                WHERE sender_id = :sid AND status = 'pending'
                            """), {"pause_until": pause_until, "sid": str(sender.id)})
                            await db2.execute(text("""
                                UPDATE senders
                                SET restriction_status = 'frozen',
                                    restricted_until = :recheck_at
                                WHERE id = :sid
                            """), {"recheck_at": recheck_at, "sid": str(sender.id)})
                            # Phase 10 (HLTH-01): durable frozen event, same TX.
                            await record_restriction_event(
                                sender.id, "frozen", "queue_error",
                                recheck_at, error_msg, db=db2,
                            )
                            await db2.commit()
                        logger.critical(
                            f"ACCOUNT_FROZEN for sender {sender.slug} — flagged frozen, "
                            f"pending paused until {pause_until.strftime('%Y-%m-%d %H:%M UTC')}. "
                            f"Telegram appeal required."
                        )
                        # Phase 9 (FAIL-02): sender flagged frozen (committed above)
                        # → excluded from the healthy pool. Move its cold-pending
                        # backlog onto healthy senders. db=None → own committed session.
                        from app.services.failover import failover_cold_backlog
                        await failover_cold_backlog(sender.id)
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

                    elif error_code == "PRIVACY_RESTRICTED":
                        # Phase 10 (D-03): recipient-level privacy error
                        # (UserNotMutualContactError). The ACCOUNT is healthy — this
                        # is the recipient's privacy setting, NOT a restriction on us.
                        # Log it in the separate 'recipient_privacy' category on the
                        # EXISTING send-loop session (db) so restriction analytics can
                        # filter it out, and NEVER touch senders.restriction_status.
                        await record_restriction_event(
                            sender.id, "privacy_restricted", "queue_error",
                            None, error_msg, category="recipient_privacy", db=db,
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
                    # Phase 2 D-11/D-12: больше не пишем is_active=false.
                    # auth_status уже выставлен (listener / SessionAuthError).
                    # Derived status='error' computed at read-time из auth_status.
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
            # Phase 02.1 CR-01: workspace_id NOT NULL — derive from queue item
            # (which was tagged with workspace_id at enqueue time).
            log_entry = MessageLog(
                workspace_id=item.workspace_id,
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
        """Mirror the conversation/message bookkeeping from send.py.

        Phase 4 D-05: INSERT extended with ``campaign_id`` from
        ``item.campaign_id`` (the new NULLable FK on message_queue per AUDIT Q1).
        ``ai_context_id`` is now derived from ``campaigns.agent_id`` via JOIN
        when item.campaign_id is set; falls back to ``extra_data["ai_context_id"]``
        for legacy queue items (campaign_id NULL).
        """
        try:
            recipient_tg_id = result.get("recipient", {}).get("telegram_id")
            recipient_name = (
                result.get("recipient", {}).get("name")
                or item.recipient_name
                or item.recipient_phone
            )

            # Campaign re-contact policy (migration 026). Fetched once; drives
            # both ai_context derivation (Phase 4 D-05) and whether a stale/closed
            # dialog may be reused. Defaults (no campaign / legacy item): strict.
            #  - campaign_id from item.campaign_id (Phase 4 column).
            #  - ai_context_id from campaigns.agent_id, else item.extra_data.
            campaign_id_str: Optional[str] = (
                str(item.campaign_id) if item.campaign_id else None
            )
            ai_ctx_id: Optional[str] = None
            allow_recontact = False
            recontact_age = 30
            if campaign_id_str is not None:
                camp_row = (await db.execute(
                    text("""
                        SELECT agent_id, allow_recontact, recontact_min_age_days
                        FROM campaigns WHERE id = :cid
                    """),
                    {"cid": campaign_id_str},
                )).fetchone()
                if camp_row is not None:
                    if camp_row.agent_id is not None:
                        ai_ctx_id = str(camp_row.agent_id)
                    allow_recontact = bool(camp_row.allow_recontact)
                    recontact_age = int(camp_row.recontact_min_age_days)
            if ai_ctx_id is None:
                # Legacy fallback for queue items without campaign_id.
                ai_ctx_id = (item.extra_data or {}).get("ai_context_id")

            # Find an existing conversation for this (sender, peer). Under
            # allow_recontact only a PROTECTED (live & fresh) dialog is reused —
            # a closed/stale one falls through to a new row (empty AI history =
            # real fresh start), sharing the predicate with campaign_enqueue via
            # recontact.py. Strict mode reuses any match. Both ORDER BY ... LIMIT
            # 1 for determinism when duplicate rows exist (newest wins).
            if allow_recontact:
                lookup_sql = f"""
                    SELECT id FROM conversations
                    WHERE sender_id = :sid AND contact_telegram_id = :tg_id
                      AND {protected_conversation_sql("age_days")}
                    ORDER BY updated_at DESC LIMIT 1
                """
                lookup_params = {
                    "sid": str(sender.id),
                    "tg_id": recipient_tg_id,
                    "age_days": recontact_age,
                }
            else:
                lookup_sql = """
                    SELECT id FROM conversations
                    WHERE sender_id = :sid AND contact_telegram_id = :tg_id
                    ORDER BY created_at DESC LIMIT 1
                """
                lookup_params = {"sid": str(sender.id), "tg_id": recipient_tg_id}

            conv_row = (await db.execute(text(lookup_sql), lookup_params)).fetchone()

            if conv_row:
                conversation_id = str(conv_row[0])
            else:
                # Phase 02.1 CR-01: workspace_id NOT NULL on conversations after
                # migration 012 — derive from sender (single source of truth).
                r2 = await db.execute(
                    text("""
                        INSERT INTO conversations
                            (workspace_id, sender_id, contact_phone, contact_name,
                             contact_telegram_id, ai_enabled, ai_context_id, campaign_id)
                        VALUES (:wid, :sid, :phone, :name, :tg_id, true, :ai_ctx, :cid)
                        RETURNING id
                    """),
                    {
                        "wid": str(sender.workspace_id),
                        "sid": str(sender.id),
                        "phone": item.recipient_phone,
                        "name": recipient_name,
                        "tg_id": recipient_tg_id,
                        "ai_ctx": ai_ctx_id,
                        "cid": campaign_id_str,  # Phase 4 D-05: propagate campaign_id.
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
    workspace_id,
    sender_id,
    sender_slug: str,
    recipient_phone: str,
    recipient_name: Optional[str],
    message_text: str,
    as_draft: bool = False,
    metadata: Optional[dict] = None,
    priority: int = 0,
    callback_url: Optional[str] = None,
    ai_context_id: Optional[UUID] = None,
    campaign_id: Optional[UUID] = None,
) -> dict:
    """Add a message to the queue. Returns queue info dict.

    Phase 02.1 CR-01: workspace_id is required (message_queue.workspace_id
    NOT NULL after migration 012). Callers must pass the workspace_id from
    AuthCtx or the sender's workspace_id.

    Phase 3 D-06: ai_context_id was an explicit parameter for the Phase 3
    send-flow. Phase 4 D-16 deprecates this in favour of ``campaign_id``;
    ``ai_context_id`` is now retained ONLY for legacy callers and is overridden
    by the campaign agent_id JOIN at _upsert_conversation time.

    Phase 4 D-16: ``campaign_id`` is the canonical parameter. ``message_queue
    .campaign_id`` is NULLable per AUDIT Q1 (legacy items support).
    """
    extra_data = dict(metadata or {})
    if ai_context_id is not None:
        extra_data["ai_context_id"] = str(ai_context_id)

    item = MessageQueue(
        workspace_id=workspace_id,
        sender_id=sender_id,
        item_type=QueueItemType.message,
        recipient_phone=recipient_phone,
        recipient_name=recipient_name,
        message_text=message_text,
        as_draft=as_draft,
        extra_data=extra_data,
        priority=priority,
        callback_url=callback_url,
        campaign_id=campaign_id,
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
    workspace_id,
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
    campaign_id: Optional[UUID] = None,
) -> dict:
    """Add a file send to the queue. Returns queue info dict.

    Phase 02.1 CR-01: workspace_id is required (message_queue.workspace_id
    NOT NULL after migration 012).

    Phase 4 D-16 (B1 revision): ``campaign_id`` propagated through the
    function signature, mirroring ``enqueue_message``. ``message_queue
    .campaign_id`` is NULLable per AUDIT Q1 — legacy callers (no campaign)
    continue to work.
    """
    item = MessageQueue(
        workspace_id=workspace_id,
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
        campaign_id=campaign_id,
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
