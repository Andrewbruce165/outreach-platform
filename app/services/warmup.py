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

from openai import AsyncOpenAI, APIError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import FloodWaitError, UserIsBlockedError, RPCError

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services.telegram import telegram_service

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
        self._openai = AsyncOpenAI()  # api_key читается из OPENAI_API_KEY

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
        result = await db.execute(text("""
            SELECT wp.sender_id, wp.workspace_id, wp.enrolled_at,
                   s.slug, s.phone, s.session_string
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
        """))
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
                "enrolled_days": max(0, (now - r[2]).days),
            }
            for r in rows
        ]

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
                       workspace_id, restriction_status, restricted_until
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
            logger.warning(
                f"🔥 Warmup {session['id'][:8]}: один из аккаунтов не eligible "
                f"(from lifecycle={from_sender['lifecycle_status']} auth={from_sender['auth_status']}, "
                f"to lifecycle={to_sender['lifecycle_status']} auth={to_sender['auth_status']})"
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
        )
        if not message_text:
            logger.warning(f"🔥 GPT вернул None для сессии {session['id'][:8]}, пропускаем")
            return

        # Пишем в БД ДО отправки (listener проверяет по кэшу телефонов, это дополнительная страховка)
        # Phase 02.1 (CR-04 issue 1): warmup_messages.workspace_id NOT NULL после миграции 012.
        await db.execute(
            text("""
                INSERT INTO warmup_messages (workspace_id, session_id, from_sender_id, to_sender_id, message_text)
                VALUES (:wid, :session_id, :from_id, :to_id, :text)
            """),
            {
                "wid":        from_sender["workspace_id"],
                "session_id": session["id"],
                "from_id":    from_id,
                "to_id":      to_id,
                "text":       message_text,
            }
        )

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
        success = await self._send_via_telethon(from_sender, to_sender["phone"], message_text)

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
                f"запись в БД сохранена"
            )
            # При ошибке отправки откладываем сессию на 15 минут
            retry_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            async with AsyncSessionLocal() as db2:
                await db2.execute(
                    text("UPDATE warmup_sessions SET next_message_at = :t WHERE id = :sid"),
                    {"t": retry_at, "sid": session["id"]}
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

        for sender_a, sender_b, ws_topics in pairs:
            # Защита-в-глубину: после partitioning оба sender'а из одного workspace.
            assert sender_a["workspace_id"] == sender_b["workspace_id"], \
                "Cross-tenant warmup pair attempted — partitioning bug!"

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
            logger.info(
                f"🔥 Новая warmup сессия (workspace={sender_a['workspace_id'][:8]}): "
                f"{sender_a['slug']} ↔ {sender_b['slug']} "
                f"(тема: «{topic}», {target} сообщений)"
            )

        await db.commit()

    # ─── AI generation ────────────────────────────────────────────────────────

    async def _generate_message(
        self,
        topic: str,
        history: list[dict],
        from_sender_id: str,
        system_prompt: Optional[str] = None,
    ) -> Optional[str]:
        """Сгенерировать следующее warmup-сообщение через GPT.

        Phase 15 (D-10): system_prompt передаётся per-workspace (резолвится в
        _process_session через _get_warmup_content). При отсутствии — дефолт
        WARMUP_SYSTEM_PROMPT, поведение без настроек неизменно.
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

            response = await self._openai.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
            )
            return response.choices[0].message.content.strip()

        except APIError as e:
            logger.error(f"🔥 OpenAI ошибка при генерации warmup: {e}")
            return None
        except Exception as e:
            logger.error(f"🔥 Неожиданная ошибка при генерации warmup: {e}", exc_info=True)
            return None

    # ─── Telethon send ────────────────────────────────────────────────────────

    async def _send_via_telethon(
        self,
        from_sender: dict,
        to_phone: str,
        message_text: str,
    ) -> bool:
        """Отправить warmup-сообщение через Telethon.

        Переиспользует telegram_service.get_client() с локами — не конфликтует
        с queue_worker, который использует ту же инфраструктуру.
        """
        from telethon.tl.types import InputPeerUser

        client = None
        try:
            client = await telegram_service.get_client(
                from_sender["slug"],
                from_sender["session_string"]
            )

            # Резолвим получателя (кэш + ResolvePhoneRequest)
            contact = await telegram_service.resolve_contact(
                client,
                from_sender["workspace_id"],
                from_sender["id"],
                to_phone
            )

            if not contact.get("is_registered"):
                logger.error(f"🔥 Warmup: {to_phone} не зарегистрирован в Telegram")
                return False

            tg_id       = contact["telegram_id"]
            access_hash = contact.get("access_hash")
            peer = InputPeerUser(tg_id, access_hash) if access_hash is not None else tg_id

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
