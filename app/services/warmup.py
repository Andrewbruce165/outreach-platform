"""
Warmup Worker Service
Автоматический прогрев Telegram-аккаунтов через AI-диалоги между своими аккаунтами.

Запускается как asyncio background task внутри API-процесса (аналогично QueueWorker).
Тикает каждые 30 секунд. Работает только в 09:00–20:00 МСК.
Уровень прогрева определяется автоматически по количеству дней с момента добавления в пул.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from openai import APIError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import FloodWaitError, UserIsBlockedError, RPCError

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services.telegram import telegram_service
# Phase 22 (D-03/D-08/D-09): shared new-chat grade ladder — the single source of
# truth for per-level new-chat budgets, shared with the queue rewrite (22-03),
# the settings API (22-02) and the sender API (22-04). Warmup spends this same
# budget for genuinely-new pairs, behind an outreach-priority reserve.
from app.services.grade_ladder import load_ladder, budget_for_level
# Phase 18 D-11: warmup routes through the SAME workspace-aware provider factory
# as the answerer (single tone everywhere). No standalone AsyncOpenAI here.
from app.services.llm import resolve_llm_config, get_provider, platform_fallback_config

logger = logging.getLogger(__name__)
settings = get_settings()

# ─── Константы ────────────────────────────────────────────────────────────────

MOSCOW_OFFSET = 3  # UTC+3

# Уровни: (min_day, max_day_exclusive, min_msgs_day, max_msgs_day)
LEVEL_CONFIG = [
    (0,  3,  5,  10),   # Уровень 1: дни 0–3
    (3,  7,  10, 25),   # Уровень 2: дни 3–7
    (7,  14, 25, 50),   # Уровень 3: дни 7–14
    (14, 21, 50, 80),   # Уровень 4: дни 14–21
    (21, 9999, 80, 120), # Уровень 5: дни 21+
]

WARMUP_TOPICS = [
    "планы на выходные",
    "любимые фильмы или сериалы",
    "спорт и активный отдых",
    "путешествия и куда хочется съездить",
    "еда и рецепты",
    "любимая музыка",
    "погода и время года",
    "книги и чтение",
    "хобби и увлечения",
    "новые гаджеты и технологии",
    "работа и усталость от неё",
    "домашние животные",
    "рестораны и кафе в городе",
    "видеоигры или настольные игры",
    "здоровый образ жизни",
    "природа и прогулки",
    "онлайн-шопинг и находки",
    "летние или зимние планы",
    "любимые места в городе",
    "готовка дома",
    "детские воспоминания",
    "сериалы которые сейчас смотришь",
    "планы на ближайший месяц",
    "новости которые удивили",
]

WARMUP_SYSTEM_PROMPT = """Ты участвуешь в обычной переписке в Telegram на русском языке.
Тема разговора: {topic}

Правила:
- Пиши как обычный человек, 1–3 коротких предложения
- Разговорный стиль, можно сокращения и редкие эмодзи
- Поддерживай тему, иногда задавай вопросы
- Никакого официального или делового тона
- Только одно сообщение, без подписей и пояснений"""


# ─── Worker ───────────────────────────────────────────────────────────────────

class WarmupWorker:
    """
    Воркер прогрева аккаунтов.

    Организует AI-диалоги между аккаунтами из warmup_pool по схеме full mesh:
    каждый аккаунт ведёт параллельные диалоги со всеми остальными аккаунтами
    своего workspace одновременно.
    Каждый тик: обрабатывает сессии с наступившим next_message_at,
    затем создаёт новые сессии для всех пар без активного диалога.
    """

    TICK_INTERVAL = 30        # секунд между тиками
    MIN_DELAY = 5 * 60        # минимальная пауза между сообщениями (5 мин)
    MAX_DELAY = 120 * 60      # максимальная пауза (2 часа)

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        """Запустить воркер как фоновый asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._run(), name="warmup_worker")
        logger.info("🔥 Warmup worker запущен")

    async def stop(self):
        """Graceful shutdown."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Warmup worker остановлен")

    # ─── Main loop ────────────────────────────────────────────────────────────

    async def _run(self):
        """Главный цикл воркера."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в warmup tick: {e}", exc_info=True)
            await asyncio.sleep(self.TICK_INTERVAL)

    async def _tick(self):
        """Один тик: обработать активные сессии + создать новые."""
        if not self._is_working_hours():
            return

        async with AsyncSessionLocal() as db:
            await self._process_due_sessions(db)
            await self._create_new_sessions(db)

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _is_working_hours(self) -> bool:
        """Рабочее время 09:00–20:00 МСК (UTC+3)."""
        moscow_hour = (datetime.now(timezone.utc).hour + MOSCOW_OFFSET) % 24
        return 9 <= moscow_hour < 20

    def _get_level(self, enrolled_days: int) -> int:
        """Уровень прогрева по количеству дней с начала."""
        for i, (start, end, *_) in enumerate(LEVEL_CONFIG, 1):
            if start <= enrolled_days < end:
                return i
        return 5

    def _get_daily_limit(self, enrolled_days: int) -> int:
        """Случайный дневной лимит для данного уровня."""
        for start, end, min_m, max_m in LEVEL_CONFIG:
            if start <= enrolled_days < end:
                return random.randint(min_m, max_m)
        return random.randint(80, 120)

    # ─── Pool ─────────────────────────────────────────────────────────────────

    async def _get_active_pool(self, db: AsyncSession) -> list[dict]:
        """Активные участники пула с данными sender'а.

        Phase 02.1 (CR-04 issue 3): workspace_id возвращается, чтобы
        _create_new_sessions мог партиционировать пары — sender'ы из разных
        workspace'ов НЕ должны парироваться (cross-tenant pair leak).

        JOIN дополнительно требует s.workspace_id = wp.workspace_id, чтобы
        даже при ручной правке БД (sender перенесён в другой workspace,
        warmup_pool остался) мы не отдавали неконсистентные строки.
        """
        # Phase 2 (D-11/D-12): senders.is_active dropped → lifecycle_status + auth_status.
        # warmup_pool.is_active — отдельная колонка (другая модель), остаётся.
        #
        # Phase 15 (D-06): enabled-gate — LEFT JOIN warmup_settings и требуем
        # COALESCE(ws.enabled, false) = true. «Нет строки» = прогрев ВЫКЛЮЧЕН
        # (default-OFF, explicit opt-in) → workspace выпадает из выборки.
        #
        # Phase 15 (D-14): restriction-skip (RESV-05 модель из contact_check_worker)
        # — аккаунт с restriction_status != 'none' ИЛИ будущим restricted_until
        # НЕ греется. lifecycle уже ограничен 'active' (исключает 'paused').
        #
        # Phase 22 (D-08/D-09): also SELECT s.current_level so the new-pair
        # budget check in _create_new_sessions knows the initiator's grade
        # level without an extra round-trip. current_level defaults to 1
        # (migration 056) so an unbackfilled account resolves to the level-1
        # budget — the safest (smallest) new-chat allowance.
        result = await db.execute(text("""
            SELECT wp.sender_id, wp.workspace_id, wp.enrolled_at,
                   s.slug, s.phone, s.session_string, s.current_level
            FROM warmup_pool wp
            JOIN senders s
              ON s.id = wp.sender_id
             AND s.workspace_id = wp.workspace_id
            LEFT JOIN warmup_settings ws
              ON ws.workspace_id = wp.workspace_id
            WHERE wp.is_active = true
              AND s.lifecycle_status = 'active'
              AND s.auth_status = 'ok'
              AND s.role = 'sender'
              AND COALESCE(ws.enabled, false) = true
              -- Warmup ВКЛЮЧАЕТ spam_limited: прогрев — это и есть восстановление
              -- доверия для аккаунта под спам-ограничением (мягкий full-mesh чат
              -- со знакомыми peer'ами). Исключаем только 'frozen' (Telegram
              -- блокирует все отправки). spam_limited греется и сквозь свой
              -- restricted_until (recheck-кулдаун); для 'none' кулдаун уважаем.
              AND s.restriction_status IN ('none', 'spam_limited')
              AND (
                  s.restriction_status = 'spam_limited'
                  OR s.restricted_until IS NULL
                  OR s.restricted_until <= NOW()
              )
              -- proxy-switch-listener-lag (mig 062): skip a sender whose proxy switch
              -- is still pending listener reconnect confirmation, so warmup never opens
              -- a connection on the NEW IP while the listener may still hold the OLD
              -- one (double-IP → auth_key kill). TTL fallback lifts a stale flag.
              AND (s.proxy_switch_pending_at IS NULL
                   OR s.proxy_switch_pending_at
                      < NOW() - make_interval(secs => :proxy_switch_ttl))
        """), {"proxy_switch_ttl": settings.proxy_switch_pending_ttl_seconds})
        rows = result.fetchall()
        now = datetime.now(timezone.utc)
        return [
            {
                "sender_id":     str(r[0]),
                "workspace_id":  str(r[1]),
                "enrolled_at":   r[2],
                "slug":          r[3],
                "phone":         r[4],
                "session_string": r[5],
                # Phase 22: grade level for the shared new-chat budget (D-08/D-09).
                "current_level": int(r[6]) if r[6] is not None else 1,
                "enrolled_days": max(0, (now - r[2]).days),
            }
            for r in rows
        ]

    # ─── Shared new-chat budget (Phase 22, D-03/D-08/D-09) ──────────────────────

    @staticmethod
    def _pick_initiator(a: dict, b: dict) -> tuple[dict, dict]:
        """Return (initiator, other) for a NEW warmup pair.

        The initiator is the OLDER / more-warmed account (D-08 discretion): it is
        charged the new-chat budget and, because it becomes ``sender_a``, it
        writes first (``_process_session``'s NEW-session ``else`` branch fires when
        ``last_sender_id IS NULL`` → sender_a sends). "Older" = greater warmup-pool
        tenure (``enrolled_days``); ties break to the EARLIER ``enrolled_at`` (the
        genuinely-older enrolment, since ``enrolled_days`` is a truncated day-count).
        """
        if a["enrolled_days"] != b["enrolled_days"]:
            return (a, b) if a["enrolled_days"] > b["enrolled_days"] else (b, a)
        # tie on truncated day-count → earlier enrolled_at is the older account
        return (a, b) if a["enrolled_at"] <= b["enrolled_at"] else (b, a)

    async def _remaining_new_chat_budget(
        self,
        db: AsyncSession,
        sender_id: str,
        workspace_id: str,
        current_level: int,
    ) -> int:
        """Initiator's remaining shared new-chat budget on the trailing-24h window.

        D-09 (outreach priority reserve): warmup may only open a NEW pair with the
        budget outreach does not need today. Remaining is computed on the SAME
        trailing-24h window the queue new-dialog cap uses (D-03, RESEARCH Pitfall
        4 — NOT CURRENT_DATE), so the two workers never disagree on "spent today":

            remaining = account_budget - spent_24h - reserved_pending_openers

        - ``account_budget`` = ``budget_for_level(ladder, current_level)`` — the
          workspace ladder (code-default 5/9/13 when unconfigured, D-16).
        - ``spent`` = DISTINCT recipients this sender already sent to in the
          trailing 24h (sender-wide, mirrors the queue cap window).
        - ``pending`` = DISTINCT cold openers still queued for this sender (a
          campaign message with no prior *sent* to that phone by this sender) —
          the outreach reserve. Bounded to ``account_budget`` so a large backlog
          can zero-out warmup but never drive the arithmetic negative.

        Bind params only. Returns an int >= 0.
        """
        ladder = await load_ladder(db, workspace_id)
        account_budget = budget_for_level(ladder, current_level)

        spent = (await db.execute(
            text("""
                SELECT COUNT(DISTINCT recipient_phone)
                FROM message_queue
                WHERE sender_id = :sid
                  AND status = 'sent'
                  AND finished_at >= NOW() - INTERVAL '24 hours'
            """),
            {"sid": sender_id},
        )).scalar() or 0

        pending = (await db.execute(
            text("""
                SELECT COUNT(DISTINCT mq.recipient_phone)
                FROM message_queue mq
                WHERE mq.sender_id = :sid
                  AND mq.status = 'pending'
                  AND mq.campaign_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM message_queue prior
                      WHERE prior.sender_id = mq.sender_id
                        AND prior.recipient_phone = mq.recipient_phone
                        AND prior.status = 'sent'
                  )
            """),
            {"sid": sender_id},
        )).scalar() or 0
        # Bound the reserve so a huge backlog zeroes warmup but never goes negative.
        pending = min(pending, account_budget)

        return max(0, account_budget - spent - pending)

    async def _count_sent_today(self, db: AsyncSession, sender_id: str) -> int:
        """Сколько warmup-сообщений отправлено сегодня этим аккаунтом."""
        result = await db.execute(
            text("""
                SELECT COUNT(*) FROM warmup_messages
                WHERE from_sender_id = :sid
                  AND sent_at >= CURRENT_DATE
            """),
            {"sid": sender_id}
        )
        return result.scalar() or 0

    async def _last_sent_at(self, db: AsyncSession, sender_id: str) -> Optional[datetime]:
        """Время последнего warmup-сообщения этого аккаунта (по всем сессиям).

        Full mesh: аккаунт участвует в N-1 диалогах одновременно, поэтому
        per-session MIN_DELAY больше не гарантирует паузу между сообщениями
        самого аккаунта. Этот метод используется как cross-session pacing-гард,
        чтобы между warmup-сообщениями одного аккаунта оставалось ≥ MIN_DELAY.
        """
        result = await db.execute(
            text("SELECT MAX(sent_at) FROM warmup_messages WHERE from_sender_id = :sid"),
            {"sid": sender_id}
        )
        return result.scalar()

    async def _get_warmup_content(
        self, db: AsyncSession, workspace_id: str
    ) -> tuple[list[str], str]:
        """Per-workspace warmup content with safe code-default fallback (D-10).

        Empty topics ([]) / NULL system_prompt / missing row all resolve to the
        24 RU WARMUP_TOPICS + WARMUP_SYSTEM_PROMPT — behaviour byte-identical to
        today when a workspace is unconfigured.
        """
        row = (await db.execute(
            text("SELECT topics, system_prompt FROM warmup_settings WHERE workspace_id = :wid"),
            {"wid": workspace_id},
        )).fetchone()
        topics = (row[0] if row and row[0] else None) or WARMUP_TOPICS
        prompt = (row[1] if row and row[1] else None) or WARMUP_SYSTEM_PROMPT
        return topics, prompt

    # ─── Session processing ───────────────────────────────────────────────────

    async def _process_due_sessions(self, db: AsyncSession):
        """Обработать сессии с наступившим next_message_at."""
        result = await db.execute(text("""
            SELECT id, sender_a_id, sender_b_id, topic,
                   messages_sent, target_messages, last_sender_id
            FROM warmup_sessions
            WHERE status = 'active'
              AND next_message_at <= NOW()
            ORDER BY next_message_at
            LIMIT 10
        """))
        sessions = result.fetchall()

        for row in sessions:
            session = {
                "id":              str(row[0]),
                "sender_a_id":     str(row[1]),
                "sender_b_id":     str(row[2]),
                "topic":           row[3],
                "messages_sent":   row[4],
                "target_messages": row[5],
                "last_sender_id":  str(row[6]) if row[6] else None,
            }
            try:
                await self._process_session(db, session)
            except Exception as e:
                logger.error(
                    f"❌ Ошибка обработки warmup сессии {session['id'][:8]}: {e}",
                    exc_info=True
                )

    async def _process_session(self, db: AsyncSession, session: dict):
        """Отправить следующее сообщение в сессии."""
        # Чередуем отправителя
        if session["last_sender_id"] == session["sender_a_id"]:
            from_id = session["sender_b_id"]
            to_id   = session["sender_a_id"]
        else:
            from_id = session["sender_a_id"]
            to_id   = session["sender_b_id"]

        # Загружаем данные обоих аккаунтов
        # Phase 2 (D-11/D-12): "eligible" = lifecycle_status='active' AND auth_status='ok'.
        # Phase 02.1 (CR-04 issue 1): workspace_id из senders для INSERT warmup_messages.
        # Phase 15 (D-14): also fetch restriction_status / restricted_until so an
        # account restricted MID-session stops too — is_eligible mirrors the
        # _get_active_pool clause (RESV-05): restriction_status='none' AND no
        # future restricted_until.
        now = datetime.now(timezone.utc)
        result = await db.execute(
            text("""
                SELECT id, slug, phone, session_string, lifecycle_status, auth_status,
                       workspace_id, restriction_status, restricted_until, client_fingerprint,
                       telegram_id
                FROM senders WHERE id = ANY(:ids)
            """),
            {"ids": [from_id, to_id]}
        )

        def _warmup_eligible(restriction_status, restricted_until) -> bool:
            # Зеркало _get_active_pool: spam_limited греется сквозь кулдаун
            # (восстановление доверия), 'frozen' исключён всегда, 'none'
            # уважает будущий restricted_until.
            if restriction_status == "frozen":
                return False
            if restriction_status == "spam_limited":
                return True
            if restriction_status != "none":
                return False
            if restricted_until is None:
                return True
            if restricted_until.tzinfo is None:
                restricted_until = restricted_until.replace(tzinfo=timezone.utc)
            return restricted_until <= now

        senders_map = {
            str(r[0]): {
                "id": str(r[0]), "slug": r[1],
                "phone": r[2], "session_string": r[3],
                "lifecycle_status": r[4], "auth_status": r[5],
                "workspace_id": str(r[6]),
                "restriction_status": r[7],
                "restricted_until": r[8],
                # Phase 21 IMPT-04: per-account fingerprint (NULL for phone-onboarded
                # senders → strict global fallback in make_telegram_client).
                "client_fingerprint": r[9],
                # Warmup peers are OUR OWN senders — telegram_id (migration 006)
                # is the resolve fallback when the phone ladder yields nothing
                # (warmup targets have no `contacts` row, so tier-3 never fires).
                "telegram_id": r[10],
                "is_eligible": (
                    r[4] == "active" and r[5] == "ok"
                    and _warmup_eligible(r[7], r[8])
                ),
            }
            for r in result.fetchall()
        }

        if from_id not in senders_map or to_id not in senders_map:
            logger.warning(f"🔥 Warmup сессия {session['id'][:8]}: sender недоступен, пропускаем")
            return

        from_sender = senders_map[from_id]
        to_sender   = senders_map[to_id]

        if not from_sender["is_eligible"] or not to_sender["is_eligible"]:
            # Head-of-line guard: сдвигаем next_message_at ВПЕРЁД перед выходом.
            # Иначе перманентно-неэлиджибл сессия (session_expired / paused peer)
            # навсегда остаётся в голове LIMIT-10 due-очереди _process_due_sessions
            # и морит голодом здоровые пары (весь warmup встаёт). Откладываем на
            # ~30–45 мин (с джиттером): когда аккаунт восстановится, сессия просто
            # продолжится на ближайшем due-тике. Зеркалит FloodWait/daily-limit
            # reschedule ниже.
            retry_at = datetime.now(timezone.utc) + timedelta(
                seconds=30 * 60 + random.randint(0, 15 * 60)
            )
            await db.execute(
                text("UPDATE warmup_sessions SET next_message_at = :t, updated_at = NOW() WHERE id = :sid"),
                {"t": retry_at, "sid": session["id"]}
            )
            await db.commit()
            logger.warning(
                f"🔥 Warmup {session['id'][:8]}: один из аккаунтов не eligible "
                f"(from lifecycle={from_sender['lifecycle_status']} auth={from_sender['auth_status']}, "
                f"to lifecycle={to_sender['lifecycle_status']} auth={to_sender['auth_status']}), "
                f"откладываем до {retry_at:%H:%M} UTC"
            )
            return

        # Cross-session pacing-гард (full mesh): аккаунт может быть due сразу в
        # нескольких сессиях. Не даём ему слать чаще, чем раз в MIN_DELAY —
        # иначе получаем burst из нескольких сообщений за секунды.
        last_sent = await self._last_sent_at(db, from_id)
        if last_sent is not None:
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_sent).total_seconds()
            if elapsed < self.MIN_DELAY:
                # Откладываем эту сессию до момента last_sent + MIN_DELAY + джиттер
                next_at = last_sent + timedelta(
                    seconds=self.MIN_DELAY + random.randint(0, 120)
                )
                await db.execute(
                    text("UPDATE warmup_sessions SET next_message_at = :t, updated_at = NOW() WHERE id = :sid"),
                    {"t": next_at, "sid": session["id"]}
                )
                await db.commit()
                return

        # Проверяем дневной лимит отправителя
        enrolled_result = await db.execute(
            text("SELECT EXTRACT(DAY FROM (NOW() - enrolled_at))::int FROM warmup_pool WHERE sender_id = :sid"),
            {"sid": from_id}
        )
        enrolled_days = enrolled_result.scalar() or 0
        sent_today    = await self._count_sent_today(db, from_id)
        daily_limit   = self._get_daily_limit(enrolled_days)

        if sent_today >= daily_limit:
            logger.info(
                f"🔥 {from_sender['slug']}: дневной лимит {daily_limit} исчерпан "
                f"(уровень {self._get_level(enrolled_days)}), откладываем до завтра"
            )
            # Откладываем на следующий день 09:00–09:30 МСК
            tomorrow_utc = (
                datetime.now(timezone.utc).replace(hour=6, second=0, microsecond=0)
                + timedelta(days=1, minutes=random.randint(0, 30))
            )
            await db.execute(
                text("UPDATE warmup_sessions SET next_message_at = :t, updated_at = NOW() WHERE id = :sid"),
                {"t": tomorrow_utc, "sid": session["id"]}
            )
            await db.commit()
            return

        # Загружаем историю сессии для контекста GPT
        hist_result = await db.execute(
            text("""
                SELECT from_sender_id, message_text FROM warmup_messages
                WHERE session_id = :sid ORDER BY sent_at LIMIT 20
            """),
            {"sid": session["id"]}
        )
        history = [{"from_id": str(r[0]), "text": r[1]} for r in hist_result.fetchall()]

        # Генерируем сообщение
        # Phase 15 (D-10): system-prompt берём из per-workspace warmup_settings
        # (код-дефолт WARMUP_SYSTEM_PROMPT). Резолвим по workspace отправителя.
        _ws_topics, ws_prompt = await self._get_warmup_content(
            db, from_sender["workspace_id"]
        )
        message_text = await self._generate_message(
            topic=session["topic"],
            history=history,
            from_sender_id=from_id,
            system_prompt=ws_prompt,
            db=db,
            workspace_id=from_sender["workspace_id"],  # D-11: same provider as the answerer
        )
        if not message_text:
            logger.warning(f"🔥 GPT вернул None для сессии {session['id'][:8]}, пропускаем")
            return

        # Пишем в БД ДО отправки (listener проверяет по кэшу телефонов, это дополнительная страховка)
        # Phase 02.1 (CR-04 issue 1): warmup_messages.workspace_id NOT NULL после миграции 012.
        msg_row_id = (await db.execute(
            text("""
                INSERT INTO warmup_messages (workspace_id, session_id, from_sender_id, to_sender_id, message_text)
                VALUES (:wid, :session_id, :from_id, :to_id, :text)
                RETURNING id
            """),
            {
                "wid":        from_sender["workspace_id"],
                "session_id": session["id"],
                "from_id":    from_id,
                "to_id":      to_id,
                "text":       message_text,
            }
        )).scalar_one()

        # Обновляем сессию
        new_sent   = session["messages_sent"] + 1
        is_done    = new_sent >= session["target_messages"]
        new_status = "completed" if is_done else "active"
        delay_sec  = random.randint(self.MIN_DELAY, self.MAX_DELAY)
        next_at    = datetime.now(timezone.utc) + timedelta(seconds=delay_sec)

        await db.execute(
            text("""
                UPDATE warmup_sessions
                SET messages_sent   = :msgs,
                    status          = :status,
                    next_message_at = :next_at,
                    last_sender_id  = :last_sender,
                    updated_at      = NOW()
                WHERE id = :sid
            """),
            {
                "msgs":        new_sent,
                "status":      new_status,
                "next_at":     next_at,
                "last_sender": from_id,
                "sid":         session["id"],
            }
        )
        await db.commit()

        # Отправляем через Telethon
        success = await self._send_via_telethon(from_sender, to_sender, message_text)

        if success:
            logger.info(
                f"🔥 Warmup [{from_sender['slug']} → {to_sender['slug']}] "
                f"({new_sent}/{session['target_messages']}): {message_text[:60]}..."
            )
            if is_done:
                logger.info(f"✅ Warmup сессия {session['id'][:8]} завершена")
        else:
            logger.warning(
                f"⚠️ Warmup: Telethon не смог отправить от {from_sender['slug']}, "
                f"откатываем запись и счётчик сессии"
            )
            # 2026-07-27 phantom-rows fix: раньше при фейле Telethon строка
            # warmup_messages и инкремент messages_sent ОСТАВАЛИСЬ — БД копила
            # ~100 фантомных «отправок»/час, пока доставка была мертва
            # (~с 2026-07-07). Откатываем строку + счётчик/статус/last_sender,
            # чтобы warmup-статистика отражала реальные доставки.
            retry_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            async with AsyncSessionLocal() as db2:
                await db2.execute(
                    text("DELETE FROM warmup_messages WHERE id = :mid"),
                    {"mid": msg_row_id}
                )
                await db2.execute(
                    text("""
                        UPDATE warmup_sessions
                        SET messages_sent   = GREATEST(messages_sent - 1, 0),
                            status          = 'active',
                            last_sender_id  = :prev_last,
                            -- GREATEST: не укорачиваем более длинный FloodWait-
                            -- reschedule, который _send_via_telethon мог уже выставить
                            next_message_at = GREATEST(COALESCE(next_message_at, :t), :t),
                            updated_at      = NOW()
                        WHERE id = :sid
                    """),
                    {
                        "prev_last": session["last_sender_id"],
                        "t": retry_at,
                        "sid": session["id"],
                    }
                )
                await db2.commit()

    # ─── Session creation ─────────────────────────────────────────────────────

    async def _create_new_sessions(self, db: AsyncSession):
        """Создать новые сессии — полный меш внутри каждого workspace.

        Каждый аккаунт ведёт параллельные диалоги со всеми остальными
        аккаунтами своего workspace одновременно: для любой пары, у которой
        ещё нет активной сессии, создаём новую. После завершения сессии пара
        снова станет «свободной» и получит свежий диалог на следующем тике —
        прогрев идёт непрерывно.

        Объём сообщений per-sender по-прежнему ограничен дневным лимитом, а
        темп — cross-session гардом в _process_session (≥ MIN_DELAY между
        сообщениями одного аккаунта).

        Phase 02.1 (CR-04 issue 3): пары формируются ВНУТРИ workspace_id —
        sender'ы из разных tenant'ов не должны общаться через warmup
        (cross-tenant Telegram leak).
        """
        from itertools import groupby, combinations

        pool = await self._get_active_pool(db)
        if len(pool) < 2:
            return

        # Пары, у которых уже есть активная сессия — не дублируем живой диалог
        # между теми же двумя аккаунтами (unordered pair).
        active_result = await db.execute(
            text("SELECT sender_a_id, sender_b_id FROM warmup_sessions WHERE status = 'active'")
        )
        active_pairs: set[frozenset] = {
            frozenset({str(row[0]), str(row[1])})
            for row in active_result.fetchall()
        }

        # ── Полный меш, партиционированный по workspace_id ────────────────────
        # groupby требует отсортированного входа.
        #
        # Phase 15 (D-10): темы берём из per-workspace warmup_settings (с
        # код-дефолтом WARMUP_TOPICS). Резолвим контент ОДИН раз на workspace-
        # группу и таскаем resolved-topics рядом с парой.
        pool_sorted = sorted(pool, key=lambda s: s["workspace_id"])
        pairs: list[tuple[dict, dict, list[str]]] = []
        for wsid, group_iter in groupby(pool_sorted, key=lambda s: s["workspace_id"]):
            ws_group = list(group_iter)
            if len(ws_group) < 2:
                continue
            ws_topics, _ws_prompt = await self._get_warmup_content(db, wsid)
            for sender_a, sender_b in combinations(ws_group, 2):
                pair = frozenset({sender_a["sender_id"], sender_b["sender_id"]})
                if pair in active_pairs:
                    continue
                pairs.append((sender_a, sender_b, ws_topics))

        if not pairs:
            return

        # Phase 22 (D-08): classify each candidate pair against the new-warmup-pair
        # registry. A pair already in sender_first_contacts warmed together before
        # → KNOWN → session is free (current behaviour). A pair NOT in the registry
        # → NEW → it may only open if the initiator has remaining shared new-chat
        # budget after the outreach reserve (D-09), and on creation the canonical
        # (LEAST,GREATEST) pair is recorded so future repeats are free.
        #
        # Load the registry ONCE per tick into a set[frozenset] (same shape as
        # active_pairs). Entries are same-workspace by construction (both senders
        # were partitioned into one workspace before insert), so this global read
        # cannot leak a cross-tenant pair into any workspace's candidate set.
        known_result = await db.execute(
            text("SELECT sender_a_id, sender_b_id FROM sender_first_contacts")
        )
        known_pairs: set[frozenset] = {
            frozenset({str(row[0]), str(row[1])})
            for row in known_result.fetchall()
        }

        for sender_a, sender_b, ws_topics in pairs:
            # Защита-в-глубину: после partitioning оба sender'а из одного workspace.
            assert sender_a["workspace_id"] == sender_b["workspace_id"], \
                "Cross-tenant warmup pair attempted — partitioning bug!"

            pair_fs = frozenset({sender_a["sender_id"], sender_b["sender_id"]})
            is_new_pair = pair_fs not in known_pairs

            if is_new_pair:
                # NEW pair: the initiator (older/more-warmed) becomes sender_a so it
                # writes first, and only if it has budget left after outreach (D-09).
                sender_a, sender_b = self._pick_initiator(sender_a, sender_b)
                remaining = await self._remaining_new_chat_budget(
                    db,
                    sender_a["sender_id"],
                    sender_a["workspace_id"],
                    sender_a["current_level"],
                )
                if remaining < 1:
                    # Outreach has reserved the whole account budget today — skip
                    # this new pair; it becomes eligible once outreach frees budget.
                    logger.info(
                        f"🔥 Warmup: пропускаем НОВУЮ пару "
                        f"{sender_a['slug']} → {sender_b['slug']} "
                        f"(инициатор {sender_a['slug']} без остатка нового-чат бюджета "
                        f"после резерва аутрича, level={sender_a['current_level']})"
                    )
                    continue

            topic  = random.choice(ws_topics)
            target = random.randint(4, 10)
            # Первое сообщение через 1–5 минут
            first_at = datetime.now(timezone.utc) + timedelta(minutes=random.randint(1, 5))

            # Phase 02.1 (CR-04 issue 1): warmup_sessions.workspace_id NOT NULL.
            await db.execute(
                text("""
                    INSERT INTO warmup_sessions
                        (workspace_id, sender_a_id, sender_b_id, topic, target_messages,
                         next_message_at, status, messages_sent)
                    VALUES (:wid, :a, :b, :topic, :target, :first_at, 'active', 0)
                """),
                {
                    "wid":      sender_a["workspace_id"],
                    "a":        sender_a["sender_id"],
                    "b":        sender_b["sender_id"],
                    "topic":    topic,
                    "target":   target,
                    "first_at": first_at,
                }
            )

            if is_new_pair:
                # Record the pair canonically (LEAST < GREATEST, matching migration
                # 057's PK invariant) so this pair is FREE on every future tick.
                # Mark it known in-memory too, so a duplicate candidate in the same
                # tick (should not happen, defence-in-depth) is not re-charged.
                await db.execute(
                    text("""
                        INSERT INTO sender_first_contacts
                            (sender_a_id, sender_b_id, first_contact_at)
                        VALUES (LEAST(CAST(:a AS uuid), CAST(:b AS uuid)),
                                GREATEST(CAST(:a AS uuid), CAST(:b AS uuid)),
                                NOW())
                        ON CONFLICT DO NOTHING
                    """),
                    {"a": sender_a["sender_id"], "b": sender_b["sender_id"]},
                )
                known_pairs.add(pair_fs)

            logger.info(
                f"🔥 Новая warmup сессия (workspace={sender_a['workspace_id'][:8]}): "
                f"{sender_a['slug']} ↔ {sender_b['slug']} "
                f"(тема: «{topic}», {target} сообщений, "
                f"{'НОВАЯ пара' if is_new_pair else 'знакомая пара'})"
            )

        await db.commit()

    # ─── AI generation ────────────────────────────────────────────────────────

    async def _generate_message(
        self,
        topic: str,
        history: list[dict],
        from_sender_id: str,
        system_prompt: Optional[str] = None,
        db: Optional[AsyncSession] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[str]:
        """Сгенерировать следующее warmup-сообщение через выбранный провайдер.

        Phase 15 (D-10): system_prompt передаётся per-workspace (резолвится в
        _process_session через _get_warmup_content). При отсутствии — дефолт
        WARMUP_SYSTEM_PROMPT, поведение без настроек неизменно.

        Phase 18 (D-11): маршрутизируется через ту же workspace-aware фабрику
        провайдеров, что и ответчик (единый тон везде). При отсутствии workspace_id
        — платформенный дефолт (OpenAI). Warmup НЕ получает D-06 fallback: ошибка
        ключа просто возвращает None для этого сообщения (best-effort). Warmup-вызовы
        НЕ логируются в llm_calls (D-09..D-12, поведение Phase 5 неизменно).
        Роль-чередование (подряд одинаковые роли из истории) обрабатывает
        AnthropicProvider._coalesce_roles прозрачно — extra merge не нужен.
        """
        prompt_template = system_prompt or WARMUP_SYSTEM_PROMPT
        try:
            messages = [
                {"role": "system", "content": prompt_template.format(topic=topic)}
            ]

            # Строим историю: с точки зрения from_sender_id
            # его сообщения — "assistant", чужие — "user"
            for msg in history:
                role = "assistant" if msg["from_id"] == from_sender_id else "user"
                messages.append({"role": role, "content": msg["text"]})

            # Первое сообщение в сессии
            if not history:
                messages.append({
                    "role": "user",
                    "content": f"Начни разговор на тему «{topic}». Напиши первое сообщение."
                })

            # Phase 18 D-11: resolve the workspace's chosen provider/model/knobs.
            if db is not None and workspace_id:
                cfg = await resolve_llm_config(db, workspace_id)
            else:
                cfg = platform_fallback_config(settings)
                cfg.key_source = "platform"
            provider = get_provider(cfg)

            result = await provider.complete(
                system=messages[0]["content"],
                messages=messages[1:],
                tools=None,
                max_tokens=cfg.max_tokens or 1024,
                temperature=cfg.temperature,
                reasoning_effort=cfg.reasoning_effort,
            )
            return result.text.strip() if result.text else None

        except APIError as e:
            logger.error(f"🔥 LLM ошибка при генерации warmup: {e}")
            return None
        except Exception as e:
            logger.error(f"🔥 Неожиданная ошибка при генерации warmup: {e}", exc_info=True)
            return None

    # ─── Telethon send ────────────────────────────────────────────────────────

    async def _send_via_telethon(
        self,
        from_sender: dict,
        to_sender: dict,
        message_text: str,
    ) -> bool:
        """Отправить warmup-сообщение через Telethon.

        Переиспользует telegram_service.get_client() с локами — не конфликтует
        с queue_worker, который использует ту же инфраструктуру.

        Резолв (2026-07-27 warmup-dead fix): warmup-цели — НАШИ ЖЕ sender'ы, у
        которых нет строки в `contacts`, поэтому resolve_contact после D-01/D-03
        (sender ResolvePhone удалён, tier-3 гейтится вердиктом чекера) для них
        детерминированно возвращает is_registered=False без единого Telegram-
        вызова — warmup-доставка была мертва с ~2026-07-07. Кеш пробуем первым
        (дёшево), затем фолбэк на peer по senders.telegram_id (migration 006) с
        прогревом entity-cache через get_dialogs (тот же приём, что в
        send_message_by_telegram_id).
        """
        from telethon.tl.types import InputPeerUser, PeerUser

        to_phone = to_sender["phone"]
        client = None
        try:
            client = await telegram_service.get_client(
                from_sender["slug"],
                str(from_sender["id"]),
                from_sender["session_string"],
                fingerprint=from_sender.get("client_fingerprint"),
            )

            # 1. Кеш-резолв (после первого удачного диалога — всегда хит)
            contact = await telegram_service.resolve_contact(
                client,
                from_sender["workspace_id"],
                from_sender["id"],
                to_phone
            )

            if contact.get("is_registered"):
                tg_id       = contact["telegram_id"]
                access_hash = contact.get("access_hash")
                peer = InputPeerUser(tg_id, access_hash) if access_hash is not None else tg_id
            else:
                # 2. Фолбэк: peer по known telegram_id нашего же аккаунта.
                to_tg_id = to_sender.get("telegram_id")
                if not to_tg_id:
                    logger.error(
                        f"🔥 Warmup: {to_sender['slug']} не резолвится (нет строки в "
                        f"contacts) и senders.telegram_id пуст — пропускаем"
                    )
                    return False
                try:
                    peer = await client.get_input_entity(PeerUser(int(to_tg_id)))
                except ValueError:
                    # Холодный entity-cache (StringSession не хранит peers) —
                    # get_dialogs заполняет access_hash для недавних диалогов.
                    await client.get_dialogs(limit=200)
                    try:
                        peer = await client.get_input_entity(PeerUser(int(to_tg_id)))
                    except ValueError:
                        logger.error(
                            f"🔥 Warmup: peer {to_sender['slug']} недоступен даже "
                            f"после get_dialogs — пропускаем"
                        )
                        return False
                # Кешируем резолв — следующие отправки идут через tier-1 кеш
                # без get_dialogs.
                if isinstance(peer, InputPeerUser):
                    await telegram_service._save_contact_cache(
                        from_sender["workspace_id"], str(from_sender["id"]), to_phone,
                        {
                            "is_registered": True,
                            "telegram_id": peer.user_id,
                            "access_hash": peer.access_hash,
                        },
                    )

            await client.send_message(peer, message_text)
            return True

        except FloodWaitError as e:
            logger.warning(f"⚠️ Warmup FloodWait {e.seconds}с для {from_sender['slug']}")
            # Откладываем сессию на время FloodWait + небольшой буфер
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=e.seconds + 60)
            async with AsyncSessionLocal() as db:
                # Phase 02.1 (CR-04 issue 2): скобки вокруг OR, чтобы AND status='active'
                # применялся к ОБОИМ условиям. Без скобок AND связывается только с
                # последним OR, и UPDATE задевал completed-сессии sender_b.
                await db.execute(
                    text("""
                        UPDATE warmup_sessions
                        SET next_message_at = :t, updated_at = NOW()
                        WHERE (sender_a_id = :sid OR sender_b_id = :sid)
                          AND status = 'active'
                    """),
                    {"t": retry_at, "sid": from_sender["id"]}
                )
                await db.commit()
            return False

        except UserIsBlockedError:
            logger.warning(f"⚠️ Warmup: блокировка для {from_sender['slug']}")
            return False

        except RPCError as e:
            logger.error(f"❌ Warmup RPC ошибка для {from_sender['slug']}: {e}")
            return False

        except Exception as e:
            logger.error(
                f"❌ Warmup Telethon ошибка для {from_sender['slug']}: {e}",
                exc_info=True
            )
            return False

        finally:
            if client:
                await telegram_service.disconnect_client(client)


# Глобальный экземпляр (импортируется в main.py)
warmup_worker = WarmupWorker()
