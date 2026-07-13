"""
Telegram Listener Service
Слушает входящие сообщения, сохраняет в БД и отвечает через AI
"""

import asyncio
import json
import random
import logging
import uuid
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    UserIsBlockedError,
    ChatWriteForbiddenError,
    RPCError,
    AuthKeyError,
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    AuthKeyPermEmptyError,
    UserDeactivatedBanError,
)
from telethon.errors.common import TypeNotFoundError
from telethon.tl.functions.updates import GetDifferenceRequest, GetChannelDifferenceRequest
from app.services.telegram import make_telegram_client, safe_read_ack, safe_typing, telegram_service


class ResilientTelegramClient(TelegramClient):
    """TelegramClient, устойчивый к TypeNotFoundError при GetDifference.

    Telegram иногда присылает в ответе на GetDifference новые конструкторы,
    которых ещё нет в текущей версии Telethon. Стандартный клиент при этом
    отключается. Здесь мы конвертируем TypeNotFoundError в ValueError,
    который Telethon обрабатывает как временную проблему (end_difference + continue).
    """

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        try:
            return await super().__call__(request, ordered=ordered, flood_sleep_threshold=flood_sleep_threshold)
        except TypeNotFoundError as e:
            if isinstance(request, (GetDifferenceRequest, GetChannelDifferenceRequest)):
                logger.warning(f"⚠️ Неизвестный конструктор в GetDifference, пропускаем: {e}")
                raise ValueError(f"Unknown TL constructor in difference: {e}") from e
            raise
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import os
import signal
import tempfile
import httpx
import time
from dataclasses import dataclass
from typing import Optional

# Logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database setup
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace(
    "postgresql://", "postgresql+asyncpg://"
)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Auth errors that mean the session is dead and needs re-authorization
AUTH_ERRORS = (AuthKeyError, AuthKeyUnregisteredError, AuthKeyDuplicatedError, AuthKeyPermEmptyError)

# Telegram setup
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

# Decryption — use shared implementation to avoid divergence
from app.services.encryption import decrypt_session
from app.services.restriction_audit import record_restriction_event
from app.config import get_settings
import base64
import socks


_PROXY_TYPE_MAP = {
    "socks5": socks.SOCKS5,
    "socks4": socks.SOCKS4,
    "http": socks.HTTP,
}


def build_proxy_tuple(proxy: dict | None) -> tuple | None:
    """Convert proxy config dict to Telethon-compatible proxy tuple."""
    if not proxy:
        return None
    proxy_type = _PROXY_TYPE_MAP.get(proxy["type"].lower())
    if not proxy_type:
        return None
    host, port = proxy["host"], proxy["port"]
    username = proxy.get("username")
    if username:
        return (proxy_type, host, port, True, username, proxy.get("password", ""))
    return (proxy_type, host, port)


# AI Engine import
from app.services.ai_engine import ai_engine, get_context_for_conversation


# Phase 5 D-08 / Open Question #2: hardcoded antispam IDs that fall through
# to _handle_antispam_signal (safety net) instead of the regular bot filter
# in _handle_bot_message. This preserves the "pause sender lifecycle +
# cancel ALL queue items" behaviour for accounts at risk of being flagged
# by Telegram. Adjacent ANTISPAM_KEYWORDS check (по name) сохранён как
# backup if Telegram ever changes the bot IDs.
ANTISPAM_BOT_IDS = {178220800, 777000}  # SpamBot, Telegram service


@dataclass
class BufferedMessage:
    """Сообщение в буфере debounce"""
    text: str
    telegram_message_id: int
    is_voice: bool = False
    is_document: bool = False
    document_info: Optional[str] = None  # "📎 Документ: file.pdf"


# Typing-hold: имитация человеческой скорости набора после генерации LLM.
# Скорость рандомизируется per-message; floor — даже короткий ответ
# показывает пару секунд «печатает», ceiling — длинный ответ не стопорит
# conversation-task дольше ~40с.
TYPING_CPS_MIN = 3.0    # chars/sec, ≈180 chars/min
TYPING_CPS_MAX = 5.0    # chars/sec, ≈300 chars/min
TYPING_HOLD_MIN = 4.0   # сек — минимальная суммарная длительность typing
TYPING_HOLD_MAX = 40.0  # сек — потолок (600-char ответ не ждёт 2+ мин)


def compute_typing_hold(reply_len: int, elapsed: float, cps: float) -> float:
    """Сколько ещё держать «печатает…» после генерации ответа.

    target = clamp(reply_len / cps, TYPING_HOLD_MIN, TYPING_HOLD_MAX);
    время генерации LLM засчитывается в бюджет.
    """
    target = min(max(reply_len / cps, TYPING_HOLD_MIN), TYPING_HOLD_MAX)
    return max(0.0, target - elapsed)


def serialize_reply_markup(reply_markup) -> Optional[list]:
    """Flatten a Telethon reply_markup into a 2D array of ``{"text": str}``.

    Rows → cols mirrors Telethon's own row/col addressing (the same indices
    ``message.click(row, col)`` uses), so the click endpoint can address a button
    purely by ``(row, col)`` without re-parsing Telegram's raw markup. Returns
    ``None`` when the markup is falsy or carries no rows (plain-text message).
    NEVER raises — on any structural surprise it returns ``None`` and logs; the
    caller must not let a markup-parse failure crash the listener loop.
    """
    try:
        rows = getattr(reply_markup, "rows", None)
        if not rows:
            return None
        out: list[list[dict]] = []
        for row in rows:
            buttons = getattr(row, "buttons", None) or []
            out.append([{"text": getattr(b, "text", "") or ""} for b in buttons])
        return out or None
    except Exception as e:  # pragma: no cover - defensive
        logger.error("reply_markup serialize failed: %s", e, exc_info=True)
        return None


class TelegramListener:
    # Debounce настройки
    DEBOUNCE_MIN = 40.0    # минимум секунд ожидания после последнего сообщения
    DEBOUNCE_MAX = 120.0    # максимум секунд ожидания после последнего сообщения (2 минуты)
    MAX_BUFFER_TIME = 300.0  # максимум секунд от первого сообщения (5 минут)

    # TTL кэша телефонов warmup-аккаунтов (секунды)
    WARMUP_CACHE_TTL = 60.0

    def __init__(self):
        self.clients: dict[str, TelegramClient] = {}
        self.running = True
        # Debounce: буферы сообщений и tasks
        self.message_buffers: dict[str, list] = {}  # conversation_id -> list of messages
        self.debounce_tasks: dict[str, asyncio.Task] = {}  # conversation_id -> pending task
        self.buffer_start_time: dict[str, float] = {}  # conversation_id -> timestamp первого сообщения
        # Контекст для отложенной отправки AI
        self.pending_contexts: dict[str, dict] = {}  # conversation_id -> context data
        # Кэш warmup-аккаунтов (телефоны, telegram_id, sender uuid) — для фильтрации
        self._warmup_phones: set[str] = set()
        self._warmup_telegram_ids: set[int] = set()
        self._warmup_sender_ids: set[str] = set()
        self._warmup_cache_ts: float = 0.0
        # Phase 15 D-01/D-02: deterministic per-workspace internal-sender tg_id set.
        # «Свой со своим» — любой traffic между двумя senders ОДНОГО workspace
        # (по telegram_id ∈ senders) считается internal. НЕ зависит от phone
        # (закрывает leak при phone="unknown") и НЕ зависит от членства в warmup_pool
        # и НЕ restriction-gated (изоляция держится для restricted/non-enrolled).
        self._workspace_sender_tg_ids: dict[str, set[int]] = {}
        self._workspace_sender_tg_ids_ts: float = 0.0

        # Phase 2 (D-18): periodic reconcile loop replaces docker-restart.
        # Diff'аем desired senders в БД с currently_connected каждые N секунд.
        self.reconcile_interval = int(os.environ.get("LISTENER_RECONCILE_INTERVAL", "30"))
        self._reconcile_task: Optional[asyncio.Task] = None
        # Migration 028: restriction reconcile — re-check spam_limited/frozen senders
        # via SpamBot once restricted_until elapses, and lift/extend automatically.
        self.restriction_reconcile_interval = get_settings().restriction_reconcile_interval_seconds
        self._restriction_task: Optional[asyncio.Task] = None
        self._connected_sender_ids: set[str] = set()   # sender uuid str
        self._proxy_snapshot: dict[str, Optional[dict]] = {}
        self._sender_id_to_slug: dict[str, str] = {}
        self._stop_event: Optional[asyncio.Event] = None

    async def _set_auth_status(self, sender_id: str, slug: str, auth_status: str):
        """Update sender auth_status in DB.

        Phase 2 (D-11/D-12): is_active column dropped — derived status='error'
        is computed at read-time from auth_status (queue.py, senders router).
        """
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("UPDATE senders SET auth_status = :status WHERE id = :sid"),
                    {"status": auth_status, "sid": sender_id}
                )
                await db.commit()
            logger.warning(
                f"auth_status for {slug} -> {auth_status} (derived status: error)"
            )
        except Exception as e:
            logger.error(f"Failed to update auth_status for {slug}: {e}")

    def add_to_buffer(self, conversation_id: str, message: BufferedMessage):
        """Добавить сообщение в буфер"""
        if conversation_id not in self.message_buffers:
            self.message_buffers[conversation_id] = []
            self.buffer_start_time[conversation_id] = time.time()
        self.message_buffers[conversation_id].append(message)
        logger.debug(f"📝 Буфер {conversation_id[:8]}: {len(self.message_buffers[conversation_id])} сообщений")

    def get_buffer_age(self, conversation_id: str) -> float:
        """Получить возраст буфера в секундах"""
        if conversation_id in self.buffer_start_time:
            return time.time() - self.buffer_start_time[conversation_id]
        return 0.0

    def clear_buffer(self, conversation_id: str) -> list[BufferedMessage]:
        """Очистить буфер и вернуть сообщения"""
        messages = self.message_buffers.pop(conversation_id, [])
        self.buffer_start_time.pop(conversation_id, None)
        # НЕ удаляем pending_contexts здесь - это делается в process_buffered_messages
        return messages

    async def schedule_ai_response(self, conversation_id: str, context: dict):
        """Запланировать отправку на AI с debounce"""
        # Сохраняем контекст для отложенной обработки
        self.pending_contexts[conversation_id] = context

        # Отменяем предыдущий таймер если есть
        if conversation_id in self.debounce_tasks:
            self.debounce_tasks[conversation_id].cancel()
            try:
                await self.debounce_tasks[conversation_id]
            except asyncio.CancelledError:
                pass

        # Проверяем, не превышен ли максимальный таймаут
        buffer_age = self.get_buffer_age(conversation_id)
        if buffer_age >= self.MAX_BUFFER_TIME:
            logger.info(f"⏰ Буфер {conversation_id[:8]} достиг max timeout ({buffer_age:.1f}с), отправляем...")
            await self.process_buffered_messages(conversation_id)
            return

        # Phase 11 D-11 / RT-01: compute base delay from response_speed setting.
        # Modes: instant (~0-2s), human (default DEBOUNCE range), slow (3x range),
        #        manual (exact response_delay_seconds), missing/unknown → human.
        # The MAX_BUFFER_TIME - buffer_age cap is applied to EVERY mode (T3 threat).
        response_speed = context.get("response_speed") or "human"
        if response_speed == "instant":
            base_delay = random.uniform(0, 2.0)
        elif response_speed == "slow":
            base_delay = random.uniform(self.DEBOUNCE_MIN * 3, self.DEBOUNCE_MAX * 3)
        elif response_speed == "manual":
            try:
                base_delay = float(context.get("response_delay_seconds") or self.DEBOUNCE_MIN)
            except (TypeError, ValueError):
                base_delay = self.DEBOUNCE_MIN
        else:
            # "human" or any unknown value → existing empirical range (back-compat).
            base_delay = random.uniform(self.DEBOUNCE_MIN, self.DEBOUNCE_MAX)

        delay = min(base_delay, self.MAX_BUFFER_TIME - buffer_age)
        logger.info(f"⏱️ Debounce таймер для {conversation_id[:8]}: {delay:.1f}с (speed={response_speed})")
        self.debounce_tasks[conversation_id] = asyncio.create_task(
            self._debounce_timer(conversation_id, delay)
        )

    async def _debounce_timer(self, conversation_id: str, delay: float):
        """Таймер debounce"""
        try:
            logger.debug(f"⏱️ Debounce timer started for {conversation_id[:8]}, waiting {delay}s...")
            await asyncio.sleep(delay)
            logger.info(f"⏱️ Debounce таймер истёк для {conversation_id[:8]}, обрабатываем...")
            await self.process_buffered_messages(conversation_id)
        except asyncio.CancelledError:
            logger.debug(f"⏱️ Debounce timer cancelled for {conversation_id[:8]}")
        except Exception as e:
            logger.error(f"❌ Ошибка в debounce timer для {conversation_id[:8]}: {e}", exc_info=True)

    async def process_buffered_messages(self, conversation_id: str):
        """Обработать накопленные сообщения и отправить на AI"""
        messages = self.clear_buffer(conversation_id)
        context = self.pending_contexts.pop(conversation_id, None)

        if not messages or not context:
            return

        # Объединяем тексты сообщений
        combined_text = "\n".join(msg.text for msg in messages)
        # max telegram_message_id для read_ack — отметим все inbound в буфере
        # как прочитанные одним вызовом перед AI-генерацией.
        context["last_msg_id"] = max(m.telegram_message_id for m in messages)
        logger.info(f"📦 Обработка буфера {conversation_id[:8]}: {len(messages)} сообщений")

        # Отправляем на AI
        await self._send_to_ai(conversation_id, combined_text, context)

    async def _send_to_ai(self, conversation_id: str, message_text: str, context: dict):
        """Отправить сообщение на AI и ответить пользователю.

        Phase 4 D-12/D-14: agent + campaign-level tools/hints/webhook URLs
        теперь резолвятся в ai_engine.get_context_for_conversation(conversation.id)
        через JOIN conversations → campaigns → ai_contexts. Legacy
        conversation.ai_context_id путь сохранён для pre-Phase-4 conversations
        (M3 fallback).
        """
        contact_name = context.get("contact_name")
        client = context.get("client")
        recipient_id = context.get("recipient_id")
        sender_info = context.get("sender_info")
        # Legacy ai_context_id may still be present (set in handle_incoming_message);
        # we pass it through so generate_response's legacy fallback path works for
        # pre-Phase-4 conversations whose campaign_id is NULL.
        ai_context_id = context.get("ai_context_id")
        last_msg_id = context.get("last_msg_id")

        async with AsyncSessionLocal() as session:
            # Phase 4: resolve through campaign — verify context exists.
            resolved = await get_context_for_conversation(conversation_id, session)
            if resolved is None:
                logger.warning(
                    f"⚠️ listener._send_to_ai: no context for conversation {conversation_id[:8]} — skip"
                )
                return

            # UX feedback: mark inbound as read + show "typing..." for the
            # duration of AI generation. Both are best-effort (no-raise on
            # failure). read_ack first, then typing wraps generate_response —
            # natural visual cue for the contact: «прочитано → набирает → ответ».
            await safe_read_ack(client, recipient_id, last_msg_id)

            conversation_context = {
                "conversation_id": conversation_id,
                "contact_phone": context.get("contact_phone"),
                "contact_name": contact_name,
                "contact_telegram_id": recipient_id,
                "sender_id": sender_info["id"],
                "sender_slug": sender_info["slug"],
                "sender_name": sender_info.get("name", sender_info["slug"]),
                "ai_context_id": ai_context_id,
            }

            gen_start = time.monotonic()
            async with safe_typing(client, recipient_id):
                reply = await ai_engine.generate_response(
                    session=session,
                    conversation_id=conversation_id,
                    context_id=ai_context_id,
                    contact_name=contact_name,
                    new_message=message_text,
                    conversation_context=conversation_context
                )

        if reply and client:
            # Human-like typing: держим «печатает…» пропорционально длине ответа.
            # Сессия БД уже закрыта — sleep не удерживает соединение.
            cps = random.uniform(TYPING_CPS_MIN, TYPING_CPS_MAX)
            hold = compute_typing_hold(len(reply), time.monotonic() - gen_start, cps)
            if hold > 0:
                logger.info(
                    f"⌨️ Typing hold {hold:.1f}с для {conversation_id[:8]} "
                    f"({len(reply)} chars, cps={cps:.1f})"
                )
                async with safe_typing(client, recipient_id):
                    await asyncio.sleep(hold)
            try:
                sent_message = await client.send_message(recipient_id, reply)
                await self.save_message(
                    conversation_id=conversation_id,
                    direction="outbound",
                    message_text=reply,
                    sent_by="ai",
                    telegram_message_id=sent_message.id
                )
                logger.info(f"📤 AI ответил: {reply[:50]}...")
            except FloodWaitError as e:
                logger.error(f"❌ FloodWait: нужно подождать {e.seconds} секунд")
            except UserIsBlockedError:
                logger.warning(f"⚠️ Пользователь {contact_name} заблокировал бота")
            except ChatWriteForbiddenError:
                logger.warning(f"⚠️ Нет прав для отправки сообщений пользователю {contact_name}")
            except RPCError as e:
                logger.error(f"❌ RPC ошибка при отправке: {e}")

    async def send_document_to_webhook(
        self,
        file_path: str,
        file_name: str,
        file_type: str,
        conversation_id: str,
        contact_name: str,
        contact_telegram_id: int,
        webhook_url: str
    ):
        """Отправить документ на внешний webhook (fire-and-forget)"""
        try:
            with open(file_path, "rb") as f:
                file_content = base64.b64encode(f.read()).decode()

            payload = {
                "file_name": file_name,
                "file_type": file_type,
                "file_base64": file_content,
                "conversation_id": conversation_id,
                "contact_name": contact_name,
                "contact_telegram_id": contact_telegram_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with httpx.AsyncClient(timeout=30.0) as http_client:
                response = await http_client.post(webhook_url, json=payload)
                if response.status_code == 200:
                    logger.info(f"📤 Документ {file_name} отправлен на webhook")
                else:
                    logger.warning(f"⚠️ Webhook вернул {response.status_code} для {file_name}")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки документа на webhook: {e}")

    async def get_active_senders(self) -> list[dict]:
        """Получить всех активных отправителей из БД.

        Возвращает только аккаунты с role='sender'.
        Checker-аккаунты (role='checker') не должны слушать входящие сообщения.
        """
        async with AsyncSessionLocal() as session:
            # Phase 2 (D-11/D-12): is_active dropped — filter by lifecycle_status + auth_status.
            # Phase 3 D-04: senders.ai_context_id dropped — больше не SELECT'им.
            # Phase 4 D-14: agent_id derived per-conversation via
            # ai_engine.get_context_for_conversation() (JOIN through conversations.campaign_id);
            # senders no longer carry agent linkage — see _send_to_ai above.
            result = await session.execute(
                text("""
                    SELECT id, slug, phone, session_string, proxy, workspace_id, client_fingerprint
                    FROM senders
                    WHERE role = 'sender'
                      AND lifecycle_status = 'active'
                      AND auth_status = 'ok'
                """)
            )
            rows = result.fetchall()
            return [
                {
                    "id": str(r[0]),
                    "slug": r[1],
                    "phone": r[2],
                    "session_string": r[3],
                    # Phase 3 D-04: ai_context_id больше не на sender'е — agent_id придёт
                    # через conversation.campaign_id JOIN в Phase 4.
                    "proxy": r[4],
                    # Phase 15 D-01: workspace_id для детерминированного internal-short-circuit
                    # («свой со своим» по telegram_id ∈ senders этого workspace).
                    "workspace_id": str(r[5]),
                    # Phase 21 IMPT-04: per-account fingerprint for imported accounts;
                    # NULL for the 13 phone-onboarded senders → strict global fallback.
                    "client_fingerprint": r[6],
                }
                for r in rows
            ]
    
    async def get_or_create_conversation(
        self,
        session: AsyncSession,
        sender_id: str,
        contact_phone: str,
        contact_name: str,
        contact_telegram_id: int,
        ai_context_id: Optional[str] = None
    ) -> dict:
        """
        Получить или создать диалог, вернуть словарь с данными
        Raises: SQLAlchemyError если не удалось выполнить операцию
        """
        try:
            # Ищем существующий. ORDER BY created_at DESC LIMIT 1 — новейший
            # диалог выигрывает (migration 026): входящие роутятся в свежий
            # fresh-start диалог, а не в старый. Попутно чинит латентный
            # недетерминизм fetchone() когда на один peer >1 строки conversations.
            result = await session.execute(
                text("""
                    SELECT id, ai_enabled, ai_context_id, status
                    FROM conversations
                    WHERE sender_id = :sender_id AND contact_telegram_id = :tg_id
                    ORDER BY created_at DESC LIMIT 1
                """),
                {"sender_id": sender_id, "tg_id": contact_telegram_id}
            )
            row = result.fetchone()

            if row:
                conv_data = {
                    "id": str(row[0]),
                    "ai_enabled": row[1],
                    "ai_context_id": str(row[2]) if row[2] else None,
                    "status": row[3],
                    "is_new": False
                }
                
                # Если ai_context_id отсутствует в БД, но передан новый, обновляем
                if not row[2] and ai_context_id:
                    await session.execute(
                        text("UPDATE conversations SET ai_context_id = :ai_context_id WHERE id = :id"),
                        {"ai_context_id": ai_context_id, "id": str(row[0])}
                    )
                    await session.commit()
                    conv_data["ai_context_id"] = ai_context_id
                    logger.info(f"📝 Обновлён ai_context_id для существующего диалога {str(row[0])[:8]}...")
                
                return conv_data

            # Создаём новый
            # Phase 02.1 CR-02: conversations.workspace_id NOT NULL после миграции 012 —
            # подтягиваем workspace_id из senders (источник правды для tenant-привязки).
            ws_row = await session.execute(
                text("SELECT workspace_id FROM senders WHERE id = :sid"),
                {"sid": sender_id}
            )
            ws_result = ws_row.fetchone()
            if not ws_result:
                raise SQLAlchemyError(
                    f"Sender {sender_id} not found — cannot create conversation"
                )
            workspace_id = str(ws_result[0])

            result = await session.execute(
                text("""
                    INSERT INTO conversations (workspace_id, sender_id, contact_phone, contact_name, contact_telegram_id, ai_enabled, ai_context_id)
                    VALUES (:wid, :sender_id, :phone, :name, :tg_id, true, :ai_context_id)
                    RETURNING id
                """),
                {"wid": workspace_id, "sender_id": sender_id, "phone": contact_phone, "name": contact_name, "tg_id": contact_telegram_id, "ai_context_id": ai_context_id}
            )
            await session.commit()
            row = result.fetchone()
            return {
                "id": str(row[0]),
                "ai_enabled": True,
                "ai_context_id": ai_context_id,
                "status": "active",
                "is_new": True
            }

        except Exception as e:
            await session.rollback()
            logger.error(
                f"❌ Ошибка при получении/создании диалога для contact_telegram_id={contact_telegram_id}: {e}",
                exc_info=True
            )
            raise
    
    async def save_message(
        self,
        conversation_id: str,
        direction: str,
        message_text: str,
        sent_by: str,
        telegram_message_id: int,
        message_type: str = "text",
        file_name: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
    ) -> bool:
        """
        Сохранить сообщение в БД

        message_type/file_name/mime_type/size_bytes (Phase 23, mig 053) — media metadata
        for incoming file bubbles. Keyword-optional so every existing text call-site keeps
        working; the DB DEFAULT 'text' backfills message_type when not passed. message_text
        may be None for a file bubble без caption (column relaxed in mig 053).

        Returns: True if message was saved, False if it was a duplicate
        """
        async with AsyncSessionLocal() as session:
            try:
                await session.execute(
                    text("""
                        INSERT INTO messages (conversation_id, direction, message_text, sent_by,
                                              telegram_message_id, message_type, file_name, mime_type, size_bytes)
                        VALUES (:conv_id, :direction, :msg_text, :sent_by,
                                :msg_id, :message_type, :file_name, :mime_type, :size_bytes)
                    """),
                    {
                        "conv_id": conversation_id,
                        "direction": direction,
                        "msg_text": message_text,
                        "sent_by": sent_by,
                        "msg_id": telegram_message_id,
                        "message_type": message_type,
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "size_bytes": size_bytes,
                    }
                )
                await session.commit()
                return True
            except IntegrityError as e:
                await session.rollback()
                # Check if it's a duplicate constraint violation
                if "messages_conversation_telegram_unique" in str(e) or "duplicate key" in str(e).lower():
                    logger.debug(
                        f"Пропускаем дубликат: conversation={conversation_id[:8]}..., "
                        f"telegram_message_id={telegram_message_id}"
                    )
                    return False
                else:
                    # Re-raise if it's a different integrity error
                    logger.error(f"IntegrityError при сохранении сообщения: {e}")
                    raise
    
    async def _refresh_warmup_cache(self):
        """Обновить кэши warmup-аккаунтов если TTL истёк.

        Заполняет три множества: телефоны, telegram_id и sender uuid.
        Вызывается из _get_warmup_phones() и _get_warmup_telegram_ids().
        """
        now = time.time()
        if now - self._warmup_cache_ts <= self.WARMUP_CACHE_TTL:
            return
        try:
            async with AsyncSessionLocal() as session:
                # Phase 2: senders.is_active dropped → lifecycle_status + auth_status.
                # warmup_pool.is_active — отдельная колонка (другая модель), остаётся.
                result = await session.execute(text("""
                    SELECT s.phone, s.telegram_id, s.id
                    FROM warmup_pool wp
                    JOIN senders s ON s.id = wp.sender_id
                    WHERE wp.is_active = true
                      AND s.lifecycle_status = 'active'
                      AND s.auth_status = 'ok'
                      AND s.role = 'sender'
                """))
                rows = result.fetchall()
                self._warmup_phones       = {r[0] for r in rows if r[0]}
                self._warmup_telegram_ids = {r[1] for r in rows if r[1]}
                self._warmup_sender_ids   = {str(r[2]) for r in rows if r[2]}
                self._warmup_cache_ts = now
                logger.debug(
                    f"🔥 Warmup кэш: {len(rows)} аккаунтов, "
                    f"{len(self._warmup_telegram_ids)} telegram_id"
                )
        except Exception as e:
            logger.warning(f"⚠️ Не удалось обновить кэш warmup-аккаунтов: {e}")

    async def _get_warmup_phones(self) -> set[str]:
        """Кэшированные телефоны warmup-аккаунтов (для фильтрации входящих)."""
        await self._refresh_warmup_cache()
        return self._warmup_phones

    async def _get_warmup_telegram_ids(self) -> set[int]:
        """Кэшированные telegram_id warmup-аккаунтов (для фильтрации исходящих)."""
        await self._refresh_warmup_cache()
        return self._warmup_telegram_ids

    async def _get_workspace_sender_tg_ids(self, workspace_id: str) -> set[int]:
        """Phase 15 D-01: детерминированный internal-sender set для workspace.

        Возвращает множество telegram_id ВСЕХ senders данного workspace —
        источник истины для признака «свой со своим» (internal). Любой трафик,
        чей counterparty telegram_id попадает в это множество, считается internal
        и дропается листенером до AI и до любой записи в conversations/messages
        (D-02).

        Ключевые свойства (закрывают корневую причину pollution-инцидента
        2026-06-23/24, debug/dashboard-analytics-warmup-pollution.md):
          - НЕ joined к warmup_pool — изоляция держится для non-enrolled аккаунтов.
          - НЕ restriction-gated (нет фильтра по restriction_status/restricted_until)
            — изоляция держится для spam_limited/frozen аккаунтов.
          - НЕ зависит от phone — работает при phone="unknown".

        Кэш: TTL-словарь {str(workspace_id) -> set(telegram_id)}, перестраивается
        целиком одним запросом при истечении WARMUP_CACHE_TTL.

        Cache-miss tradeoff (research Pitfall 2): если внутри TTL-окна появился
        совсем новый sender (ещё не в кэше) и его tg_id запрошен как unknown для
        своего workspace_id, делаем single-row EXISTS fallback и, при нахождении,
        дописываем его в кэшированное множество. Это держит изоляцию строгой
        (новый sender не «протекает» как внешний контакт на время TTL) ценой
        максимум одного дешёвого индексного запроса на cold tg_id.
        """
        now = time.time()
        if now - self._workspace_sender_tg_ids_ts > self.WARMUP_CACHE_TTL:
            try:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(text("""
                        SELECT workspace_id, telegram_id
                        FROM senders
                        WHERE role = 'sender' AND telegram_id IS NOT NULL
                    """))
                    rebuilt: dict[str, set[int]] = {}
                    for r in result.fetchall():
                        rebuilt.setdefault(str(r[0]), set()).add(r[1])
                    self._workspace_sender_tg_ids = rebuilt
                    self._workspace_sender_tg_ids_ts = now
                    logger.debug(
                        f"🔥 Workspace internal-sender кэш: {len(rebuilt)} workspace(s)"
                    )
            except Exception as e:
                logger.warning(
                    f"⚠️ Не удалось обновить internal-sender кэш: {e}"
                )

        return self._workspace_sender_tg_ids.get(str(workspace_id), set())

    async def _is_internal_counterparty(
        self, workspace_id: str, counterparty_tg_id: int
    ) -> bool:
        """True если counterparty telegram_id принадлежит другому sender'у этого
        workspace (internal «свой со своим»). Включает single-row EXISTS fallback
        для cold tg_id внутри TTL-окна (Pitfall 2)."""
        internal_ids = await self._get_workspace_sender_tg_ids(workspace_id)
        if counterparty_tg_id in internal_ids:
            return True
        # Cache-miss fallback: brand-new sender may not be in the TTL snapshot yet.
        try:
            async with AsyncSessionLocal() as session:
                exists = (await session.execute(
                    text("""
                        SELECT EXISTS(
                            SELECT 1 FROM senders
                            WHERE workspace_id = :wid
                              AND telegram_id = :cid
                              AND role = 'sender'
                        )
                    """),
                    {"wid": str(workspace_id), "cid": counterparty_tg_id},
                )).scalar()
            if exists:
                # Patch the live cache so subsequent hits skip the fallback.
                self._workspace_sender_tg_ids.setdefault(
                    str(workspace_id), set()
                ).add(counterparty_tg_id)
                return True
        except Exception as e:
            logger.warning(f"⚠️ internal-sender EXISTS fallback failed: {e}")
        return False

    async def handle_incoming_message(self, event, sender_info: dict):
        """Обработка входящего сообщения с debounce и поддержкой медиа"""
        logger.debug(f"📨 Новое событие NewMessage incoming, chat_id={event.chat_id}, sender_id={event.sender_id}")
        logger.info(f"📨 Тип сообщения: text={bool(event.text)}, photo={bool(event.message.photo)}, video={bool(event.message.video)}, document={bool(event.message.document)}, voice={bool(event.message.voice)}")
        try:
            sender = await event.get_sender()
            logger.debug(f"📨 Sender получен: {sender}")

            if not sender:
                logger.warning(f"⚠️ Не удалось получить sender для события")
                return

            phone = getattr(sender, 'phone', None) or "unknown"
            name = (f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip()
                    or getattr(sender, 'title', None) or "Unknown")

            # Пропускаем сообщения от себя
            me = await event.client.get_me()
            if sender.id == me.id:
                logger.info(f"📨 Пропускаем своё сообщение от {name}")
                return

            # === Phase 15 D-01/D-02: детерминированный internal short-circuit ===
            # «Свой со своим» — если sender.id ∈ senders этого workspace, это
            # internal warmup-трафик. Дропаем ДО bot/antispam-веток, ДО AI и ДО
            # любой записи в conversations/messages. Признак НЕ зависит от phone
            # (закрывает leak при phone="unknown") и НЕ от членства в warmup_pool /
            # restriction-статуса (изоляция держится даже для остановленного
            # прогрева и ограниченных аккаунтов). Always-on — не gated warmup_enabled.
            internal_ids = await self._get_workspace_sender_tg_ids(
                sender_info["workspace_id"]
            )
            if sender.id in internal_ids or await self._is_internal_counterparty(
                sender_info["workspace_id"], sender.id
            ):
                logger.debug(f"🔥 internal warmup traffic dropped (tg_id={sender.id})")
                return

            # Сервисные сообщения Telegram (+42777, id=777000): login/auth-коды.
            # НЕ дропаем — сохраняем под status='telegram_service' для отдельной
            # вкладки в inbox. AI НЕ запускаем, antispam/bot-ветки не трогаем.
            TELEGRAM_SERVICE_PHONES = {"+42777", "42777"}
            if phone in TELEGRAM_SERVICE_PHONES or sender.id == 777000:
                await self._handle_telegram_service_message(
                    sender_info, sender, event, name, phone
                )
                return

            # Групповые чаты и каналы: только помечаем прочитанным, AI не запускаем
            if event.is_group or event.is_channel:
                try:
                    await event.client.send_read_acknowledge(event.chat_id, max_id=event.message.id)
                    logger.debug(f"📨 Прочитано сообщение в чате/канале {event.chat_id} от {name}")
                except Exception as e:
                    logger.debug(f"📨 Не удалось пометить прочитанным {event.chat_id}: {e}")
                return

            # === Phase 5 D-05/D-06: proactive bot filter ===
            # Если Telegram отдал event.sender.bot=True — это бот. AI не отвечаем.
            # Известные antispam id (178220800 SpamBot, 777000 Telegram service)
            # делегируем в существующий safety net `_handle_antispam_signal`
            # (Open Question #2 — D-08 sender lifecycle pause + cancel ВСЕХ
            # queue items сохраняется).
            if getattr(sender, 'bot', False) is True:
                if sender.id in ANTISPAM_BOT_IDS:
                    logger.warning(
                        "🚨 Antispam bot ID detected (%s) → delegating to safety net",
                        sender.id,
                    )
                    # 260713-hiw: persist @SpamBot (178220800) messages to a
                    # dedicated status='spambot' conversation for the account-page
                    # live chat — ADDITIVE, runs for ALL SpamBot replies (incl.
                    # "free"), BEFORE the unchanged restriction safety net below.
                    if sender.id == 178220800:
                        await self._persist_spambot_message(
                            sender_info, sender, event, name, phone
                        )
                    await self._handle_antispam_signal(
                        sender_info, name, sender.id, event.text or ""
                    )
                    return
                # Обычный бот — записываем сообщение и помечаем диалог bot_ignored.
                await self._handle_bot_message(
                    sender_info, sender, event, name, phone
                )
                return  # AI dispatch SKIPPED
            # === End Phase 5 D-06 ===

            # === Детект antispam bot — отключаем AI и останавливаем очередь ===
            # Backup path: keyword detection ловит ботов которые не выставляют
            # event.sender.bot=True (например если SpamBot когда-нибудь поменяет ID).
            ANTISPAM_KEYWORDS = ["spam", "антиспам", "spambot", "spam info"]
            is_antispam = (
                sender.id in ANTISPAM_BOT_IDS
                or any(kw in name.lower() for kw in ANTISPAM_KEYWORDS)
            )
            if is_antispam:
                logger.warning(
                    f"🚨 Сообщение от antispam бота ({name}, id={sender.id}) для аккаунта {sender_info['slug']}! "
                    f"Отключаем AI и ставим очередь на паузу."
                )
                # 260713-hiw: same additive persistence on the keyword-backup path.
                if sender.id == 178220800:
                    await self._persist_spambot_message(
                        sender_info, sender, event, name, phone
                    )
                await self._handle_antispam_signal(sender_info, name, sender.id, event.text or "")
                return

            # Phase 15 D-01: warmup/internal-трафик уже дропнут детерминированным
            # internal short-circuit выше (по telegram_id ∈ senders workspace).
            # Старый pool/phone-scoped фильтр удалён — он течёт при phone="unknown"
            # и при non-enrolled аккаунтах (корневая причина pollution-инцидента
            # 2026-06-23/24, debug/dashboard-analytics-warmup-pollution.md).
            # Детерминированный telegram_id-признак — единственный источник истины.

            logger.info(f"📨 Обрабатываем сообщение от {name} ({phone}), типы: photo={event.message.photo}, video={event.message.video}, document={event.message.document}, voice={event.message.voice}, text={bool(event.text)}")
            
            # Определяем тип и текст сообщения
            message_text = event.text
            is_voice = False
            is_document = False
            document_info = None

            # === Обработка голосовых сообщений ===
            voice_media = event.message.voice
            if voice_media:
                is_voice = True
                duration = getattr(voice_media, 'duration', None)
                duration_str = f", длительность: {duration}с" if duration else ""
                logger.info(f"🎤 Голосовое от {name} ({phone}){duration_str}")

                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
                        tmp_path = tmp_file.name

                    try:
                        await asyncio.wait_for(
                            event.message.download_media(file=tmp_path),
                            timeout=60.0
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"❌ Таймаут скачивания голосового от {name} (>60с)")
                        return
                    logger.info(f"📥 Голосовое скачано: {tmp_path}")

                    transcribed_text = await ai_engine.transcribe_audio(tmp_path)

                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

                    if transcribed_text:
                        message_text = f"[🎤 Голосовое]: {transcribed_text}"
                        logger.info(f"📝 Транскрипция: {transcribed_text[:50]}...")
                    else:
                        logger.warning(f"⚠️ Не удалось транскрибировать голосовое от {name}")
                        return

                except Exception as e:
                    logger.error(f"❌ Ошибка обработки голосового от {name}: {e}", exc_info=True)
                    if tmp_path:
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass
                    return

            # === Обработка документов, фото, видео ===
            elif event.message.photo or event.message.video or event.message.document:
                logger.info(f"📎 Обнаружен файл от {name} ({phone})")
                is_document = True
                media = event.message.media

                # Определяем тип и имя файла
                if event.message.photo:
                    file_name = f"photo_{event.id}.jpg"
                    file_type = "image/jpeg"
                    emoji = "📷"
                    media_type = "Фото"
                elif event.message.video:
                    file_name = getattr(event.message.video, 'file_name', None) or f"video_{event.id}.mp4"
                    file_type = event.message.video.mime_type or "video/mp4"
                    emoji = "🎥"
                    media_type = "Видео"
                else:
                    doc = event.message.document
                    file_name = None
                    # Ищем имя файла в атрибутах
                    for attr in doc.attributes:
                        if hasattr(attr, 'file_name'):
                            file_name = attr.file_name
                            break
                    file_name = file_name or f"document_{event.id}"
                    file_type = doc.mime_type or "application/octet-stream"
                    emoji = "📎"
                    media_type = "Документ"

                document_info = f"[{emoji} {media_type}: {file_name}]"
                logger.info(f"{emoji} {media_type} от {name} ({phone}): {file_name}")

                # Получаем conversation для webhook URL
                async with AsyncSessionLocal() as session:
                    conv = await self.get_or_create_conversation(
                        session=session,
                        sender_id=sender_info["id"],
                        contact_phone=phone,
                        contact_name=name,
                        contact_telegram_id=sender.id,
                        ai_context_id=sender_info.get("ai_context_id")
                    )

                conversation_id = conv["id"]
                ai_context_id = conv["ai_context_id"]

                # document_webhook_url not restored: dropped in Phase 3 migration 015,
                # moved to custom tools (campaigns.tools). If a client needs to receive
                # incoming files — define a custom tool with a file parameter in the
                # campaign config (deferred to custom tools per CONTEXT.md item 6).
                logger.info(
                    "ℹ️ document_webhook: legacy функция убрана в Phase 4. "
                    "Используйте campaigns.tools custom function с file param."
                )

                # Текст для сохранения — метка о документе + caption если есть
                caption = event.message.message or ""
                message_text = f"{document_info}\n{caption}".strip() if caption else document_info

            # === Обычное текстовое сообщение ===
            else:
                if event.text:
                    logger.info(f"📨 Текстовое сообщение от {name} ({phone}): {event.text[:50]}...")
                else:
                    logger.info(f"📨 Пустое или неподдерживаемый тип от {name} ({phone})")
                    return

            if not message_text:
                return

            # === Получаем или создаём диалог ===
            async with AsyncSessionLocal() as session:
                conv = await self.get_or_create_conversation(
                    session=session,
                    sender_id=sender_info["id"],
                    contact_phone=phone,
                    contact_name=name,
                    contact_telegram_id=sender.id,
                    ai_context_id=sender_info.get("ai_context_id")
                )

            conversation_id = conv["id"]
            ai_context_id = conv["ai_context_id"]

            # === Phase 23 (INBM-04 / D-15): classify concrete media type + pull
            # metadata from the telethon File wrapper WITHOUT downloading the bytes.
            # Lazy download happens later via the 23-05 endpoint. Never read the
            # deprecated/unreliable file id attribute (Pitfall 6) — only name/mime/size.
            _msg = event.message
            if _msg.photo:
                _mtype = "photo"
            elif _msg.video:
                _mtype = "video"
            elif _msg.voice:
                _mtype = "voice"
            elif _msg.document:
                _mtype = "document"
            else:
                _mtype = "text"
            _f = _msg.file  # telethon File wrapper; None for plain text
            _file_name = _f.name if _f else None
            _mime_type = _f.mime_type if _f else None
            _size_bytes = _f.size if _f else None

            # === Сохраняем в БД ===
            message_saved = await self.save_message(
                conversation_id=conversation_id,
                direction="inbound",
                message_text=message_text,
                sent_by="contact",
                telegram_message_id=event.id,
                message_type=_mtype,
                file_name=_file_name,
                mime_type=_mime_type,
                size_bytes=_size_bytes,
            )

            if not message_saved:
                return

            logger.info(f"✅ Сообщение сохранено в conversation {conversation_id[:8]}...")

            # === Phase 19 D-03 / D-17: genuine contact reply ===
            # A real inbound message reverts a no_reply conversation back to
            # 'active' and cancels this conversation's pending follow-up pings.
            # MUST run BEFORE the AI-dispatch check below: `conv` was read by
            # get_or_create_conversation before this revert (Pitfall 4), so we
            # also update the local dict so the normal answerer fires this turn.
            revert = await handle_no_reply_revert(conversation_id)
            if revert["reverted"]:
                conv["status"] = "active"

            # === Проверяем AI и добавляем в буфер ===
            ai_context_id = conv["ai_context_id"] or sender_info.get("ai_context_id")

            if conv["ai_enabled"] and conv["status"] == "active":
                if not ai_context_id:
                    logger.warning(f"⚠️ AI включён, но у sender {sender_info['slug']} нет контекста")
                else:
                    # Формируем текст для AI (без служебных меток для голосовых)
                    ai_text = message_text
                    if is_voice:
                        ai_text = message_text.replace("[🎤 Голосовое]: ", "")

                    # Добавляем в буфер debounce
                    buffered_msg = BufferedMessage(
                        text=ai_text,
                        telegram_message_id=event.id,
                        is_voice=is_voice,
                        is_document=is_document,
                        document_info=document_info
                    )
                    self.add_to_buffer(conversation_id, buffered_msg)

                    # Phase 11 D-11 / RT-01: pull response_speed + response_delay_seconds
                    # from the agent context (cached, TTL 60s) so schedule_ai_response
                    # can branch on the speed setting without a raw SELECT.
                    agent_ctx = None
                    try:
                        async with AsyncSessionLocal() as _spd_session:
                            agent_ctx = await ai_engine.get_context(_spd_session, ai_context_id)
                    except Exception as _spd_err:
                        logger.warning(
                            "⚠️ listener: failed to load agent context for response_speed "
                            "(ai_context_id=%s): %s — defaulting to 'human'",
                            ai_context_id, _spd_err
                        )

                    # Контекст для отложенной обработки
                    context = {
                        "ai_context_id": ai_context_id,
                        "contact_name": name,
                        "contact_phone": phone,
                        "client": event.client,
                        "recipient_id": sender.id,
                        "sender_info": sender_info,
                        # Phase 11 D-11: response_speed drives debounce delay branch.
                        # Default "human" = existing DEBOUNCE_MIN..MAX range (back-compat).
                        "response_speed": (agent_ctx or {}).get("response_speed") or "human",
                        "response_delay_seconds": (agent_ctx or {}).get("response_delay_seconds"),
                    }

                    logger.info(f"🤖 AI включён (context: {ai_context_id[:8]}...), добавлено в буфер...")

                    # Запускаем debounce таймер
                    await self.schedule_ai_response(conversation_id, context)
            else:
                logger.info(f"ℹ️ AI отключён для этого диалога")

        except FloodWaitError as e:
            logger.error(f"❌ FloodWait: нужно подождать {e.seconds} секунд")
        except RPCError as e:
            logger.error(f"❌ Telegram RPC ошибка: {e}")
        except SQLAlchemyError as e:
            logger.error(f"❌ Ошибка БД: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка: {e}", exc_info=True)

    async def _delayed_file_cleanup(self, file_path: str, delay: float):
        """Удалить файл после задержки"""
        await asyncio.sleep(delay)
        try:
            os.unlink(file_path)
            logger.debug(f"🗑️ Удалён временный файл: {file_path}")
        except Exception:
            pass

    async def _handle_antispam_signal(
        self,
        sender_info: dict,
        bot_name: str,
        bot_id: int,
        message_text: str
    ):
        """
        Реакция на сообщение от antispam-бота (unified freeze policy, Phase 07):
        1. Поставить pending-задачи очереди на паузу (scheduled_at +24h, status
           остаётся 'pending') — reconcile sweep сам их вернёт, когда лимит снят.
        2. Пометить sender restriction_status='spam_limited' + restricted_until
           (мягкое ограничение, зеркалит PEER_FLOOD-ветку в queue.py).
        3. AI НЕ трогаем — Telegram не блокирует ответы в установленных диалогах
           при soft spam-limit, поэтому реплаи продолжают идти (FRZ-03).
        """
        sender_id = sender_info["id"]
        sender_slug = sender_info["slug"]

        # Solicited SpamBot reply: we pinged @SpamBot ourselves (reconcile sweep or
        # manual check) — this is not a real antispam warning, so do NOT pause the
        # queue or flag the sender. Covers both detect branches (id + keyword) since
        # both funnel here. MUST stay the FIRST statement — otherwise the reconcile
        # sweep's own ping would re-flag the sender we are trying to clear (loop).
        if telegram_service.is_spambot_selfcheck(sender_slug):
            logger.info(
                f"🔕 [{sender_slug}] solicited SpamBot reply during self-check — skip auto-cancel"
            )
            return

        # Body classification (fix 2026-06-29): a SpamBot ID match alone is NOT proof
        # of a restriction — @SpamBot also sends CLEAN replies ("Good news, no limits …
        # free as a bird!"). Flagging spam_limited on a clean body was the false-positive
        # that pinned checker sender-8364639216 for 6h (see
        # .planning/debug/checker-false-spam-limited.md). Only a 'limited'/'suspended'
        # verdict is restrictive; a 'free' or 'unknown' body must never flag the sender.
        # Telegram-service messages (id 777000) carry no SpamBot verdict text → treated
        # as 'unknown' and likewise skipped here (they are not antispam warnings).
        from app.services.telegram import classify_spambot_text
        verdict = classify_spambot_text(message_text)
        if verdict not in ("frozen", "limited", "suspended"):
            logger.info(
                f"🔕 [{sender_slug}] SpamBot message classified '{verdict}' "
                f"(not a restriction) — skip auto-cancel/flag"
            )
            return
        # frozen-spambot-check-error.md: an unsolicited freeze notice (Telegram's
        # reversible read-only freeze) is flagged as 'frozen' — a hard restriction —
        # NOT the soft 'spam_limited' bucket. 'limited'/'suspended' remain the soft
        # antispam-signal bucket (this receive-path net never writes auth_status=banned;
        # a real ban surfaces on the auth path, so misclassified freeze text here can
        # only ever over-restrict softly, never demand reauth).
        target_status = "frozen" if verdict == "frozen" else "spam_limited"

        try:
            # Mirror of the PEER_FLOOD soft-restriction write (queue.py:739-754).
            # pause_until: empirical 24h queue pause — DO NOT change (CLAUDE.md hard rule).
            # recheck_at: when the reconcile sweep re-checks via SpamBot (default 6h).
            pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
            recheck_at = datetime.now(timezone.utc) + timedelta(
                seconds=get_settings().restriction_recheck_interval_seconds
            )
            async with AsyncSessionLocal() as session:
                # 1. Pause pending items (NOT fail) so the reconcile resume query
                #    (status='pending' AND scheduled_at > NOW()) can auto-resume them.
                #    Scope to 'pending' ONLY (drop 'processing') — matches PEER_FLOOD
                #    and avoids the in-flight lost-update race.
                paused = await session.execute(
                    text("""
                        UPDATE message_queue SET scheduled_at = :pause_until
                        WHERE sender_id = :sid AND status = 'pending'
                        RETURNING id
                    """),
                    {"pause_until": pause_until, "sid": str(sender_id)},
                )
                paused_count = len(paused.fetchall())

                # 2. Flag the sender (spam_limited for limited/suspended; frozen for a
                #    freeze notice). The '<> frozen' guard preserves frozen-precedence:
                #    a soft signal must not downgrade a hard freeze, and a 'frozen'
                #    verdict on an already-frozen sender is a no-op (no duplicate event).
                #    RETURNING id: only write the audit event when the row actually changed.
                flagged = await session.execute(
                    text("""
                        UPDATE senders
                        SET restriction_status = :status,
                            restricted_until = :recheck_at
                        WHERE id = :sid AND restriction_status <> 'frozen'
                        RETURNING id
                    """),
                    {"status": target_status, "recheck_at": recheck_at, "sid": str(sender_id)},
                )

                # 3. Phase 9 (FAIL-02): the spam_limited flag is now set on this
                #    session (Pitfall 3 — written BEFORE failover so the candidate
                #    filter sees restriction_status != 'none' and won't pick this
                #    sender). Move the cold-pending backlog onto healthy pool
                #    senders. Pass the session: transaction-neutral, so pause +
                #    flag + failover all land in the single commit below.
                from app.services.failover import failover_cold_backlog
                await failover_cold_backlog(sender_id, session)

                # Phase 10 (HLTH-01 / OQ#2): durable restriction event in the SAME
                # session as the pause+flag. Only when the UPDATE changed a row —
                # a frozen sender's no-op must not produce a false state-change event.
                if flagged.fetchone() is not None:
                    await record_restriction_event(
                        sender_id, target_status, "antispam_signal",
                        recheck_at, message_text, db=session,
                    )

                await session.commit()

            logger.warning(
                f"🚨 ANTISPAM [{sender_slug}] ({bot_name} id={bot_id}): "
                f"поставлено на паузу {paused_count} задач очереди (+24h), "
                f"sender помечен {target_status} (recheck "
                f"{recheck_at.strftime('%Y-%m-%d %H:%M UTC')}). AI оставлен включённым."
            )

        except Exception as e:
            logger.error(f"❌ Ошибка при обработке antispam-сигнала для {sender_slug}: {e}", exc_info=True)

    async def _handle_bot_message(
        self,
        sender_info: dict,
        sender,           # Telethon User object
        event,            # Telethon NewMessage event
        name: str,
        phone: str,
    ) -> None:
        """Phase 5 D-06 — store inbound bot message + flag conversation as bot_ignored.

        AI dispatch SKIPPED. UPDATE guard (Pitfall 3): only downgrades from
        status='active' to status='bot_ignored' — preserves lead/handoff/finished/manual.
        Isolated AsyncSessionLocal so a transient failure does not poison the
        listener's main event loop.
        """
        try:
            async with AsyncSessionLocal() as session:
                existing = (await session.execute(text("""
                    SELECT id, status FROM conversations
                    WHERE sender_id = :sid AND contact_telegram_id = :tid
                """), {
                    "sid": str(sender_info["id"]),
                    "tid": sender.id,
                })).fetchone()

                if existing is None:
                    conv_id = uuid.uuid4()
                    await session.execute(text("""
                        INSERT INTO conversations (
                            id, workspace_id, sender_id, contact_phone, contact_name,
                            contact_telegram_id, ai_enabled, status, paused_at, paused_reason
                        )
                        VALUES (
                            :id, :wid, :sid, :phone, :name, :tid,
                            false, 'bot_ignored', NOW(),
                            'Telegram bot account (event.sender.bot=True)'
                        )
                    """), {
                        "id": str(conv_id),
                        "wid": str(sender_info["workspace_id"]),
                        "sid": str(sender_info["id"]),
                        "phone": phone,
                        "name": name,
                        "tid": sender.id,
                    })
                else:
                    conv_id = existing.id
                    # Pitfall 3 guard: only downgrade from 'active'.
                    # Preserve lead/handoff/finished/manual/paused — historic truth.
                    if existing.status == 'active':
                        await session.execute(text("""
                            UPDATE conversations
                            SET status = 'bot_ignored',
                                ai_enabled = false,
                                paused_at = NOW(),
                                paused_reason = 'Telegram bot account (event.sender.bot=True)',
                                updated_at = NOW()
                            WHERE id = :cid
                        """), {"cid": str(conv_id)})

                # D-06: save inbound message history regardless (manager can see).
                await session.execute(text("""
                    INSERT INTO messages
                        (workspace_id, conversation_id, direction, message_text,
                         sent_by, telegram_message_id)
                    VALUES (:wid, :cid, 'inbound', :txt, 'contact', :tmid)
                    ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
                """), {
                    "wid": str(sender_info["workspace_id"]),
                    "cid": str(conv_id),
                    "txt": event.text or "<media>",
                    "tmid": event.id,
                })
                await session.commit()

                logger.info(
                    "🤖 Bot message ignored: %s (%s) → conv=%s",
                    name, phone, str(conv_id)[:8],
                )
        except Exception as e:
            logger.error("Bot filter failed: %s", e, exc_info=True)

    async def _handle_telegram_service_message(
        self,
        sender_info: dict,
        sender,           # Telethon User object (id 777000 / +42777)
        event,            # Telethon NewMessage event
        name: str,
        phone: str,
    ) -> None:
        """Persist Telegram service-account (777000 / +42777) login/auth-code
        notifications under status='telegram_service' so the inbox can render a
        dedicated 'Telegram' tab.

        Mirrors _handle_bot_message: NO AI dispatch, NO enqueue, NEVER touches
        sender restriction/lifecycle status. UPDATE guard (Pitfall 3): only
        downgrades from status='active' — preserves any historic status.
        Isolated AsyncSessionLocal so a transient failure never poisons the loop.
        """
        try:
            async with AsyncSessionLocal() as session:
                existing = (await session.execute(text("""
                    SELECT id, status FROM conversations
                    WHERE sender_id = :sid AND contact_telegram_id = :tid
                """), {
                    "sid": str(sender_info["id"]),
                    "tid": sender.id,
                })).fetchone()

                if existing is None:
                    conv_id = uuid.uuid4()
                    await session.execute(text("""
                        INSERT INTO conversations (
                            id, workspace_id, sender_id, contact_phone, contact_name,
                            contact_telegram_id, ai_enabled, status, paused_at, paused_reason
                        )
                        VALUES (
                            :id, :wid, :sid, :phone, :name, :tid,
                            false, 'telegram_service', NOW(),
                            'Telegram service account (login/auth codes)'
                        )
                    """), {
                        "id": str(conv_id),
                        "wid": str(sender_info["workspace_id"]),
                        "sid": str(sender_info["id"]),
                        "phone": phone,
                        "name": name,
                        "tid": sender.id,
                    })
                else:
                    conv_id = existing.id
                    # Pitfall 3 guard: only downgrade from 'active'.
                    if existing.status == 'active':
                        await session.execute(text("""
                            UPDATE conversations
                            SET status = 'telegram_service',
                                ai_enabled = false,
                                paused_at = NOW(),
                                paused_reason = 'Telegram service account (login/auth codes)',
                                updated_at = NOW()
                            WHERE id = :cid
                        """), {"cid": str(conv_id)})

                # Save inbound message history regardless (manager can read the code).
                await session.execute(text("""
                    INSERT INTO messages
                        (workspace_id, conversation_id, direction, message_text,
                         sent_by, telegram_message_id)
                    VALUES (:wid, :cid, 'inbound', :txt, 'contact', :tmid)
                    ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
                """), {
                    "wid": str(sender_info["workspace_id"]),
                    "cid": str(conv_id),
                    "txt": event.text or "<media>",
                    "tmid": event.id,
                })
                await session.commit()

                logger.info(
                    "📥 Telegram service message stored: %s (%s) → conv=%s",
                    name, phone, str(conv_id)[:8],
                )
        except Exception as e:
            logger.error("Telegram service message store failed: %s", e, exc_info=True)

    async def _persist_spambot_message(
        self,
        sender_info: dict,
        sender,           # Telethon User object (id 178220800 / @SpamBot)
        event,            # Telethon NewMessage event
        name: str,
        phone: str,
    ) -> None:
        """Persist an inbound @SpamBot (id 178220800) message under a dedicated
        status='spambot' conversation so the account-page "Text to SpamBot" side
        panel can render it (quick task 260713-hiw).

        Mirrors _handle_telegram_service_message: NO AI dispatch, NO enqueue,
        NEVER touches sender restriction_status/lifecycle_status (the existing
        `_handle_antispam_signal` — called separately, right after this — remains
        the sole owner of restriction parsing/paths). This method is ADDITIVE and
        purely persistence.

        Get-or-create by (sender_id, contact_telegram_id=178220800): a new row
        gets a sentinel contact_phone ('spambot:178220800', matching no real
        recipient so the send path's queue-cancel is a harmless no-op), ai_enabled
        =false, status='spambot'. An existing row is reused as-is — status is NEVER
        downgraded here (get-or-create keeps whatever the endpoint/prior run set).
        Isolated AsyncSessionLocal so a transient failure never poisons the loop.
        """
        try:
            async with AsyncSessionLocal() as session:
                existing = (await session.execute(text("""
                    SELECT id FROM conversations
                    WHERE sender_id = :sid AND contact_telegram_id = :tid
                      AND status = 'spambot'
                    ORDER BY created_at DESC LIMIT 1
                """), {
                    "sid": str(sender_info["id"]),
                    "tid": sender.id,
                })).fetchone()

                if existing is None:
                    conv_id = uuid.uuid4()
                    await session.execute(text("""
                        INSERT INTO conversations (
                            id, workspace_id, sender_id, contact_phone, contact_name,
                            contact_telegram_id, ai_enabled, status, paused_at, paused_reason
                        )
                        VALUES (
                            :id, :wid, :sid, :phone, '@SpamBot', :tid,
                            false, 'spambot', NOW(), 'SpamBot manual chat'
                        )
                    """), {
                        "id": str(conv_id),
                        "wid": str(sender_info["workspace_id"]),
                        "sid": str(sender_info["id"]),
                        "phone": "spambot:178220800",
                        "tid": sender.id,
                    })
                else:
                    conv_id = existing.id

                # 260713-jmp: capture any inline/reply keyboard @SpamBot attached
                # so the panel can render clickable buttons. reply_markup absent
                # (plain text) → buttons stays NULL (no behavior change).
                buttons = serialize_reply_markup(
                    getattr(event.message, "reply_markup", None)
                )

                # Save inbound message history so the panel can read SpamBot's reply.
                await session.execute(text("""
                    INSERT INTO messages
                        (workspace_id, conversation_id, direction, message_text,
                         sent_by, telegram_message_id, buttons)
                    VALUES (:wid, :cid, 'inbound', :txt, 'contact', :tmid,
                            CAST(:buttons AS JSONB))
                    ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
                """), {
                    "wid": str(sender_info["workspace_id"]),
                    "cid": str(conv_id),
                    "txt": event.text or "<media>",
                    "tmid": event.id,
                    "buttons": json.dumps(buttons) if buttons else None,
                })
                await session.commit()

                logger.info(
                    "📥 SpamBot message stored: %s → conv=%s",
                    sender_info["slug"], str(conv_id)[:8],
                )
        except Exception as e:
            logger.error("SpamBot message store failed: %s", e, exc_info=True)

    async def handle_outgoing_message(self, event, sender_info: dict):
        """Обработка исходящего сообщения (отправленного вручную)"""
        try:
            chat = await event.get_chat()

            # Пропускаем групповые чаты
            if event.is_group or event.is_channel:
                return

            if not event.text:
                return

            phone = chat.phone if hasattr(chat, 'phone') and chat.phone else "unknown"
            name = f"{chat.first_name or ''} {chat.last_name or ''}".strip() or "Unknown"

            # === Phase 15 D-01/D-02: симметричный internal short-circuit ===
            # Исходящее от нашего аккаунта к другому sender'у ТОГО ЖЕ workspace —
            # internal warmup-трафик. Дропаем ДО conversation lookup и любой записи
            # в conversations/messages. Признак — chat.id ∈ senders этого workspace,
            # детерминированный, не зависит от phone / warmup_pool / restriction
            # (заменяет прежний pool-scoped блок, который течёт при phone="unknown").
            internal_ids = await self._get_workspace_sender_tg_ids(
                sender_info["workspace_id"]
            )
            if chat.id in internal_ids or await self._is_internal_counterparty(
                sender_info["workspace_id"], chat.id
            ):
                logger.debug(f"🔥 internal warmup outgoing dropped (tg_id={chat.id})")
                return

            logger.info(f"📤 Исходящее к {name}: {event.text[:50]}...")

            async with AsyncSessionLocal() as session:
                # Проверяем, есть ли такой диалог
                result = await session.execute(
                    text("SELECT id, ai_enabled FROM conversations WHERE sender_id = :sender_id AND contact_telegram_id = :tg_id"),
                    {"sender_id": sender_info["id"], "tg_id": chat.id}
                )
                row = result.fetchone()

                conversation_id = None
                if row:
                    conversation_id = str(row[0])
                    ai_was_enabled = row[1]

                    # Проверяем, не AI ли отправил это сообщение
                    # (проверяем последнее сохранённое сообщение)
                    result = await session.execute(
                        text("""
                            SELECT sent_by FROM messages
                            WHERE conversation_id = :conv_id
                            ORDER BY created_at DESC LIMIT 1
                        """),
                        {"conv_id": conversation_id}
                    )
                    last_msg = result.fetchone()

                    # Если последнее сообщение от AI — это значит AI только что ответил, пропускаем
                    if last_msg and last_msg[0] == "ai":
                        logger.debug(f"Пропускаем — это ответ AI, который был только что отправлен")
                        return

                # Если диалога нет, создаём его
                if not conversation_id:
                    conv = await self.get_or_create_conversation(
                        session=session,
                        sender_id=sender_info["id"],
                        contact_phone=phone,
                        contact_name=name,
                        contact_telegram_id=chat.id,
                        ai_context_id=sender_info.get("ai_context_id")
                    )
                    conversation_id = conv["id"]
                    logger.info(f"📝 Создан новый диалог для исходящего: {conversation_id[:8]}...")

            # Сохраняем как сообщение от человека (дубликаты обрабатываются через DB constraint)
            message_saved = await self.save_message(
                conversation_id=conversation_id,
                direction="outbound",
                message_text=event.text,
                sent_by="human",
                telegram_message_id=event.id
            )

            if not message_saved:
                # Сообщение было дубликатом - пропускаем
                return

            # НЕ отключаем AI автоматически - пользователь управляет через переключатель
            # Только обновляем updated_at
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        UPDATE conversations
                        SET updated_at = NOW()
                        WHERE id = :conv_id
                    """),
                    {"conv_id": conversation_id}
                )
                await session.commit()

            logger.info(f"✅ Исходящее сохранено в {conversation_id[:8]}...")

        except RPCError as e:
            logger.error(f"❌ Telegram RPC ошибка при обработке исходящего: {e}")
        except SQLAlchemyError as e:
            logger.error(f"❌ Ошибка БД при обработке исходящего: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка обработки исходящего: {e}", exc_info=True)
    
    async def start_client(self, sender_info: dict):
        """Запуск клиента для одного отправителя с автоматическим реконнектом"""
        MAX_RETRIES = 0  # бесконечный реконнект
        RECONNECT_DELAY = 5  # секунд между попытками
        retry_count = 0

        while self.running:
            client = None
            try:
                session_string = decrypt_session(sender_info["session_string"])

                client = make_telegram_client(
                    StringSession(session_string),
                    proxy=sender_info.get("proxy"),
                    client_class=ResilientTelegramClient,
                    fingerprint=sender_info.get("client_fingerprint"),
                )

                await client.connect()

                if not await client.is_user_authorized():
                    logger.error(f"❌ {sender_info['slug']}: сессия не авторизована")
                    await self._set_auth_status(sender_info["id"], sender_info["slug"], "session_expired")
                    return

                # Регистрируем обработчики
                logger.info(f"📝 Регистрируем обработчики событий для {sender_info['slug']}")
                @client.on(events.NewMessage(incoming=True))
                async def incoming_handler(event):
                    logger.debug(f"🎯 Incoming handler triggered for {sender_info['slug']}")
                    await self.handle_incoming_message(event, sender_info)

                @client.on(events.NewMessage(outgoing=True))
                async def outgoing_handler(event):
                    logger.debug(f"🎯 Outgoing handler triggered for {sender_info['slug']}")
                    await self.handle_outgoing_message(event, sender_info)

                self.clients[sender_info["slug"]] = client
                # Phase 2 (D-18): track for reconcile diff.
                self._connected_sender_ids.add(str(sender_info["id"]))
                self._proxy_snapshot[str(sender_info["id"])] = sender_info.get("proxy")
                self._sender_id_to_slug[str(sender_info["id"])] = sender_info["slug"]

                me = await client.get_me()
                logger.info(f"✅ {sender_info['slug']} ({me.first_name}) — слушаем сообщения")

                # Сохраняем telegram_id аккаунта в БД — нужен для фильтрации warmup-диалогов
                try:
                    async with AsyncSessionLocal() as db_session:
                        await db_session.execute(
                            text("UPDATE senders SET telegram_id = :tg_id WHERE id = :sid"),
                            {"tg_id": me.id, "sid": sender_info["id"]}
                        )
                        await db_session.commit()
                    logger.debug(f"📝 telegram_id={me.id} сохранён для {sender_info['slug']}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось сохранить telegram_id для {sender_info['slug']}: {e}")

                retry_count = 0  # сброс счётчика при успешном подключении

                # Периодический catch_up для подхватывания пропущенных обновлений
                # (API-контейнер кратковременно подключается с тем же auth key,
                # что может сбить маршрутизацию обновлений от Telegram)
                async def periodic_catch_up(cl, slug):
                    while cl.is_connected():
                        await asyncio.sleep(15)
                        try:
                            await cl.catch_up()
                            logger.debug(f"🔄 catch_up для {slug}")
                        except Exception as e:
                            logger.warning(f"⚠️ catch_up ошибка для {slug}: {e}")

                catch_up_task = asyncio.create_task(periodic_catch_up(client, sender_info['slug']))

                # Держим клиент активным
                try:
                    await client.run_until_disconnected()
                finally:
                    catch_up_task.cancel()

                logger.warning(f"⚠️ Клиент {sender_info['slug']} отключился, переподключаемся...")

            except AUTH_ERRORS as e:
                logger.critical(f"❌ {sender_info['slug']}: auth error (session dead) — {e}")
                await self._set_auth_status(sender_info["id"], sender_info["slug"], "session_expired")
                # Phase 2 (D-18): drop from reconcile bookkeeping so next tick
                # can re-attempt after reauth.
                self._connected_sender_ids.discard(str(sender_info["id"]))
                self._proxy_snapshot.pop(str(sender_info["id"]), None)
                self.clients.pop(sender_info["slug"], None)
                return  # no reconnect — session is dead

            except UserDeactivatedBanError as e:
                logger.critical(f"❌ {sender_info['slug']}: account banned — {e}")
                await self._set_auth_status(sender_info["id"], sender_info["slug"], "banned")
                self._connected_sender_ids.discard(str(sender_info["id"]))
                self._proxy_snapshot.pop(str(sender_info["id"]), None)
                self.clients.pop(sender_info["slug"], None)
                return  # no reconnect — account is banned

            except Exception as e:
                retry_count += 1
                logger.error(
                    f"❌ Ошибка клиента {sender_info['slug']} (попытка {retry_count}): {e}",
                    exc_info=True
                )
            finally:
                if client and client.is_connected():
                    await client.disconnect()

            if not self.running:
                break

            delay = min(RECONNECT_DELAY * retry_count, 60) if retry_count > 0 else RECONNECT_DELAY
            logger.info(f"🔄 Реконнект {sender_info['slug']} через {delay}с...")
            await asyncio.sleep(delay)
    
    # ─── Phase 2 (D-18): periodic reconcile loop ─────────────────────────────

    async def _disconnect_sender(self, sender_id: str) -> None:
        """Disconnect a sender's Telethon client and clear reconcile bookkeeping."""
        slug = self._sender_id_to_slug.get(sender_id)
        client = self.clients.pop(slug, None) if slug else None
        if client is not None:
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"🔄 [reconcile] disconnect error for {slug}: {e}")
        self._connected_sender_ids.discard(sender_id)
        self._proxy_snapshot.pop(sender_id, None)
        self._sender_id_to_slug.pop(sender_id, None)

    async def _reconcile_tick(self) -> dict:
        """One reconcile pass — diff desired vs currently-connected senders.

        Public-ish (single underscore) so unit tests can drive it directly
        without spinning up the background loop. Returns counts for tests/logging.
        """
        desired_list = await self.get_active_senders()
        desired = {str(s["id"]): s for s in desired_list}
        current = set(self._connected_sender_ids)

        added = 0
        removed = 0
        reproxied = 0

        # NEW desired senders → connect.
        for sid in set(desired.keys()) - current:
            s = desired[sid]
            logger.info(
                f"🔄 [reconcile] connecting sender={s['slug']} sid={sid[:8]}"
            )
            asyncio.create_task(self.start_client(s))
            added += 1

        # REMOVED / paused / errored → disconnect.
        for sid in current - set(desired.keys()):
            slug = self._sender_id_to_slug.get(sid, "?")
            logger.info(
                f"🔄 [reconcile] disconnecting sender={slug} sid={sid[:8]} "
                f"(no longer in desired set)"
            )
            await self._disconnect_sender(sid)
            removed += 1

        # PROXY CHANGED → disconnect; next tick will reconnect via NEW branch.
        for sid in current & set(desired.keys()):
            desired_proxy = desired[sid].get("proxy")
            snapshot = self._proxy_snapshot.get(sid)
            if desired_proxy != snapshot:
                slug = self._sender_id_to_slug.get(sid, "?")
                logger.info(
                    f"🔄 [reconcile] proxy changed for sender={slug} sid={sid[:8]}, "
                    f"will reconnect on next tick"
                )
                await self._disconnect_sender(sid)
                reproxied += 1

        logger.debug(
            f"🔄 [reconcile] tick: +{added} -{removed} ~proxy={reproxied} "
            f"total={len(self.clients)}"
        )
        return {
            "added": added,
            "removed": removed,
            "reproxied": reproxied,
            "total": len(self.clients),
        }

    async def _reconcile_loop(self):
        """Periodic reconcile (D-18) — every ``reconcile_interval`` seconds."""
        logger.info(
            f"🔄 Reconcile loop started (interval={self.reconcile_interval}s)"
        )
        while self.running:
            try:
                await asyncio.sleep(self.reconcile_interval)
                if not self.running:
                    break
                await self._reconcile_tick()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error(f"❌ [reconcile] error: {e}", exc_info=True)
        logger.info("🛑 Reconcile loop stopped")

    # ─── Migration 028: restriction reconcile (spam-limit / freeze) ───────────

    async def _restriction_reconcile_tick(self) -> dict:
        """Re-check senders flagged spam_limited/frozen whose recheck window elapsed.

        For each due sender we ask SpamBot (reusing the live listener client — the
        sender is still active+ok, so it is connected) and act on the verdict:
            free      → clear restriction + un-pause that sender's paused queue items
            limited   → extend restricted_until (still restricted)
            suspended → real ban → auth_status='banned'
            unknown   → bump restricted_until to avoid hammering SpamBot every tick

        Single underscore so tests can drive one pass without the loop. Returns counts.
        """
        recheck = self.restriction_reconcile_interval

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                text("""
                    SELECT id, slug, restriction_status
                    FROM senders
                    WHERE restriction_status <> 'none'
                      AND restricted_until IS NOT NULL
                      AND restricted_until <= NOW()
                """)
            )).fetchall()

        checked = cleared = extended = banned = skipped = 0

        for r in rows:
            slug = r[1]
            client = self.clients.get(slug)
            if client is None:
                # Not connected this tick — next reconcile connects it, then we re-check.
                skipped += 1
                continue

            # selfcheck_key=slug → solicited window so the antispam handler (same
            # process) skips auto-cancelling this sender's own queue on the reply.
            result = await telegram_service.check_spambot(client, selfcheck_key=slug)
            verdict = result.get("status", "unknown")
            checked += 1
            next_recheck = datetime.now(timezone.utc) + timedelta(seconds=recheck)

            # Guard (frozen-spambot-check-error.md): Telegram's read-only FREEZE is
            # reversible + session-intact, but SpamBot reports it with "blocked"/
            # «заблокирован» wording that classify_spambot_text maps to 'suspended'.
            # An already-frozen sender (r[2] from the batch SELECT) must NOT be
            # escalated to a permanent auth_status='banned' by ambiguous text — that
            # flips derived status frozen→error and demands reauth on a live session.
            # A real hard ban surfaces on the AUTH path (SessionAuthError in
            # get_client), not via a SpamBot reply body. Treat it as still-frozen.
            if verdict == "suspended" and r[2] == "frozen":
                verdict = "frozen"

            async with AsyncSessionLocal() as db:
                # D-01 gate atomicity (B-1): read the CURRENT restricted_until INSIDE
                # this per-sender transaction (NOT from the outer batch SELECT, which
                # is stale — a concurrent reconcile tick may have moved it). The
                # extension event is gated against THIS old_until.
                old_until = (await db.execute(
                    text("SELECT restricted_until FROM senders WHERE id = :sid"),
                    {"sid": str(r[0])},
                )).scalar_one_or_none()

                # A 'frozen' verdict (explicit freeze wording, or the guard above) is a
                # still-restricted state handled by the mechanical-recheck else-branch;
                # ensure the column reflects it if SpamBot reports a freeze on a sender
                # not yet flagged frozen (e.g. previously spam_limited).
                if verdict == "frozen" and r[2] != "frozen":
                    await db.execute(
                        text("UPDATE senders SET restriction_status = 'frozen' WHERE id = :sid"),
                        {"sid": str(r[0])})

                if verdict == "free":
                    await db.execute(
                        text("""
                            UPDATE senders
                            SET restriction_status = 'none', restricted_until = NULL
                            WHERE id = :sid
                        """), {"sid": str(r[0])})
                    # Un-pause: pull this sender's paused pending items back to now.
                    await db.execute(
                        text("""
                            UPDATE message_queue SET scheduled_at = NOW()
                            WHERE sender_id = :sid AND status = 'pending'
                              AND scheduled_at > NOW()
                        """), {"sid": str(r[0])})
                    # Symmetric rebalance-back (the inverse of Phase-9 failover):
                    # when this sender got PeerFlood'd / frozen, failover_cold_backlog
                    # (queue.py) moved its cold-pending backlog onto the healthy pool so
                    # cold contacts didn't stall. The un-pause above only touches rows
                    # STILL assigned to this sender — the shed cold backlog never comes
                    # back on its own, so a cold-outreach sender returns 'active' with an
                    # empty queue and campaign throughput stays degraded. Now that the
                    # restriction is cleared to 'none' in THIS same transaction, the
                    # sender is eligible again: call rebalance_on_attach for each of its
                    # campaigns to pull a fair ±1-of-total/P share of cold-pending backlog
                    # back onto it (and evacuate any still-ineligible-donor rows). It is
                    # idempotent (no-op on an already-even pool) and worker-safe, and it
                    # does NOT touch rate limits — the recovered sender simply resumes
                    # sending within its own 4/min·20/h·150/day caps.
                    from app.services.rebalance import rebalance_on_attach
                    camp_rows = (await db.execute(
                        text("SELECT campaign_id FROM campaign_senders WHERE sender_id = :sid"),
                        {"sid": str(r[0])},
                    )).fetchall()
                    rebalanced = 0
                    for cr in camp_rows:
                        rebalanced += await rebalance_on_attach(cr[0], r[0], db)
                    # Phase 10 (HLTH-01): durable cleared event, same TX as the lift.
                    await record_restriction_event(
                        r[0], "cleared", "spambot_reconcile",
                        None, result.get("raw_text"), db=db,
                    )
                    await db.commit()
                    cleared += 1
                    logger.info(
                        f"✅ [restriction] {slug} cleared (SpamBot: free) — queue resumed"
                        + (f", rebalanced {rebalanced} cold-pending rows back" if rebalanced else "")
                    )
                elif verdict == "suspended":
                    await db.execute(
                        text("UPDATE senders SET auth_status = 'banned' WHERE id = :sid"),
                        {"sid": str(r[0])})
                    # Phase 10 (HLTH-01): durable banned event, same TX.
                    await record_restriction_event(
                        r[0], "banned", "spambot_reconcile",
                        old_until, result.get("raw_text"), db=db,
                    )
                    await db.commit()
                    banned += 1
                    logger.critical(f"⛔ [restriction] {slug} suspended (SpamBot) → auth_status=banned")
                else:
                    # 'limited' or 'unknown' → still restricted. Prefer SpamBot's quoted
                    # release time (recheck just after it); else use the fixed interval.
                    #
                    # CR-01 (Phase 10): `next_at` defaults to a MECHANICAL recheck-interval
                    # bump (now + recheck) which is NOT a real restriction extension — it is
                    # merely the next time we re-poll SpamBot. Only when SpamBot quotes a
                    # concrete future release date (`limit_until`, verdict='limited') does
                    # `next_at` represent a genuine restriction horizon. Track that as
                    # `quoted_shift` so the D-01 gate fires on the parsed quote, NOT on the
                    # recheck bump (which would emit the 37/day reconcile noise the gate
                    # exists to suppress — fired on every still-limited / unknown tick).
                    next_at = next_recheck
                    quoted_shift = False
                    iso = result.get("limit_until")
                    if verdict == "limited" and iso:
                        try:
                            candidate = datetime.fromisoformat(iso) + timedelta(minutes=5)
                            if candidate > datetime.now(timezone.utc):
                                next_at = candidate
                                quoted_shift = True
                        except ValueError:
                            pass
                    # Phase 10 (D-01 gate): emit an 'extension' event ONLY when SpamBot
                    # reported a genuinely NEW, concrete future release date (a parsed
                    # quote) that is materially later (> old_until + 1 min) than the
                    # previously recorded restricted_until. A verdict='unknown' tick, or
                    # 'limited' WITHOUT a parsed concrete date, is a mechanical recheck —
                    # NOT a state change — so NO event is recorded.
                    if quoted_shift and (
                        old_until is None or next_at > old_until + timedelta(minutes=1)
                    ):
                        await record_restriction_event(
                            r[0], "extension", "spambot_reconcile",
                            next_at, result.get("raw_text"), db=db,
                        )
                    # The unconditional recheck bump STAYS regardless of the gate.
                    await db.execute(
                        text("UPDATE senders SET restricted_until = :next WHERE id = :sid"),
                        {"next": next_at, "sid": str(r[0])})
                    await db.commit()
                    extended += 1
                    logger.info(
                        f"🔁 [restriction] {slug} still restricted (SpamBot: {verdict}) — "
                        f"recheck {next_at.strftime('%Y-%m-%d %H:%M UTC')}"
                    )

        if rows:
            logger.info(
                f"🔁 [restriction] tick: checked={checked} cleared={cleared} "
                f"extended={extended} banned={banned} skipped={skipped}"
            )
        return {
            "checked": checked, "cleared": cleared,
            "extended": extended, "banned": banned, "skipped": skipped,
        }

    async def _restriction_reconcile_loop(self):
        """Periodic restriction reconcile — every ``restriction_reconcile_interval`` seconds."""
        logger.info(
            f"🔁 Restriction reconcile loop started "
            f"(interval={self.restriction_reconcile_interval}s)"
        )
        while self.running:
            try:
                await asyncio.sleep(self.restriction_reconcile_interval)
                if not self.running:
                    break
                await self._restriction_reconcile_tick()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error(f"❌ [restriction] error: {e}", exc_info=True)
        logger.info("🛑 Restriction reconcile loop stopped")

    async def run(self):
        """Запуск всех клиентов"""
        logger.info("🚀 Запуск Telegram Listener с AI Engine...")

        senders = await self.get_active_senders()

        if not senders:
            logger.warning("⚠️ Нет активных отправителей")
            # Phase 2 (D-18): still start the reconcile loop so newly-onboarded
            # senders are picked up without restarting the container.

        logger.info(f"📋 Найдено {len(senders)} отправителей")

        # Initial connect — fire-and-forget so we can also run reconcile in parallel.
        for s in senders:
            asyncio.create_task(self.start_client(s))

        # Phase 2 (D-18): periodic reconcile loop.
        self._stop_event = asyncio.Event()
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name="listener-reconcile"
        )
        # Migration 028: restriction reconcile sweep.
        self._restriction_task = asyncio.create_task(
            self._restriction_reconcile_loop(), name="listener-restriction-reconcile"
        )

        # Block until stop() flips the event.
        await self._stop_event.wait()

    async def stop(self):
        """Остановка всех клиентов"""
        logger.info("🛑 Останавливаем клиенты...")
        self.running = False
        if self._stop_event is not None:
            self._stop_event.set()
        for task in (self._reconcile_task, self._restriction_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for slug, client in list(self.clients.items()):
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"disconnect error for {slug}: {e}")
            logger.info(f"  - {slug} отключён")
        self.clients.clear()
        self._connected_sender_ids.clear()
        self._proxy_snapshot.clear()
        self._sender_id_to_slug.clear()


async def handle_no_reply_revert(conversation_id: str) -> dict:
    """Phase 19 D-03 / D-17 (first guard) — react to a genuine contact reply.

    Called from handle_incoming_message when an inbound contact message arrives.
    Two effects, both scoped to this conversation:

    1. **D-03 revert:** if the conversation is in the follow-up ``no_reply`` state,
       flip it back to ``active`` so the normal AI answerer fires again. Guarded on
       ``status = 'no_reply'`` (a manual/lead/finished/handoff status is preserved).
    2. **D-17 first guard:** cancel this conversation's *pending* follow-up ping
       queue rows (``status='cancelled'``) so a ping scheduled hours ago can never
       land in a dialog the contact has already answered. Scoped to the
       conversation's sender_id + recipient_phone (+ campaign_id when set), matching
       the antispam queue-cancel precedent in ``_handle_antispam_signal``.

    Returns a dict ``{"reverted": bool, "cancelled": int}``. Never raises — a
    transient failure here must not poison the listener's incoming path (the
    inbound message is already saved by the caller).
    """
    reverted = False
    cancelled = 0
    try:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(text("""
                SELECT sender_id, contact_phone, campaign_id, status
                FROM conversations WHERE id = :cid
            """), {"cid": str(conversation_id)})).first()

            if row is None:
                return {"reverted": False, "cancelled": 0}

            # 1. D-03 revert no_reply -> active (guarded so it can't clobber
            #    manual/lead/finished/handoff/bot_ignored states).
            if row.status == "no_reply":
                await session.execute(text("""
                    UPDATE conversations
                    SET status = 'active', updated_at = NOW()
                    WHERE id = :cid AND status = 'no_reply'
                """), {"cid": str(conversation_id)})
                reverted = True

            # 2. D-17 first guard: cancel this conversation's pending pings.
            #    Scope to sender + recipient_phone (+ campaign when known) and
            #    status='pending' — the sent opener is already 'sent', so pending
            #    rows for an in-conversation contact are follow-up pings.
            cancel_params = {
                "sid": str(row.sender_id),
                "phone": row.contact_phone,
            }
            campaign_filter = ""
            if row.campaign_id is not None:
                campaign_filter = " AND campaign_id = :cid_campaign"
                cancel_params["cid_campaign"] = str(row.campaign_id)

            cancelled_rows = await session.execute(text(f"""
                UPDATE message_queue
                SET status = 'cancelled',
                    finished_at = NOW(),
                    error_message = 'contact replied'
                WHERE sender_id = :sid
                  AND recipient_phone = :phone
                  AND status = 'pending'{campaign_filter}
                RETURNING id
            """), cancel_params)
            cancelled = len(cancelled_rows.fetchall())

            await session.commit()

        if reverted or cancelled:
            logger.info(
                "↩️  no_reply revert conv=%s reverted=%s cancelled_pings=%d",
                str(conversation_id)[:8], reverted, cancelled,
            )
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"❌ handle_no_reply_revert failed for conversation "
            f"{str(conversation_id)[:8]}: {e}", exc_info=True
        )
    return {"reverted": reverted, "cancelled": cancelled}


async def main():
    listener = TelegramListener()
    
    # Graceful shutdown
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Получен сигнал остановки")
        asyncio.create_task(listener.stop())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    await listener.run()


if __name__ == "__main__":
    asyncio.run(main())
