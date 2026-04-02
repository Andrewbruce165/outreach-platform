"""
Telegram Listener Service
Слушает входящие сообщения, сохраняет в БД и отвечает через AI
"""

import asyncio
import random
import logging
from datetime import datetime, timezone
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
from app.services.telegram import make_telegram_client


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
from app.services.ai_engine import ai_engine


@dataclass
class BufferedMessage:
    """Сообщение в буфере debounce"""
    text: str
    telegram_message_id: int
    is_voice: bool = False
    is_document: bool = False
    document_info: Optional[str] = None  # "📎 Документ: file.pdf"


class TelegramListener:
    # Debounce настройки
    DEBOUNCE_MIN = 20.0    # минимум секунд ожидания после последнего сообщения
    DEBOUNCE_MAX = 180.0   # максимум секунд ожидания после последнего сообщения (3 минуты)
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

    async def _set_auth_status(self, sender_id: str, slug: str, auth_status: str):
        """Update sender auth_status in DB."""
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("UPDATE senders SET auth_status = :status, is_active = false WHERE id = :sid"),
                    {"status": auth_status, "sid": sender_id}
                )
                await db.commit()
            logger.warning(f"auth_status for {slug} -> {auth_status}, deactivated")
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

        # Создаём новый таймер
        delay = min(random.uniform(self.DEBOUNCE_MIN, self.DEBOUNCE_MAX), self.MAX_BUFFER_TIME - buffer_age)
        logger.info(f"⏱️ Debounce таймер для {conversation_id[:8]}: {delay:.1f}с")
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
        logger.info(f"📦 Обработка буфера {conversation_id[:8]}: {len(messages)} сообщений")

        # Отправляем на AI
        await self._send_to_ai(conversation_id, combined_text, context)

    async def _send_to_ai(self, conversation_id: str, message_text: str, context: dict):
        """Отправить сообщение на AI и ответить пользователю"""
        ai_context_id = context.get("ai_context_id")
        contact_name = context.get("contact_name")
        client = context.get("client")
        recipient_id = context.get("recipient_id")
        sender_info = context.get("sender_info")

        if not ai_context_id:
            logger.warning(f"⚠️ Нет ai_context_id для {conversation_id[:8]}")
            return

        async with AsyncSessionLocal() as session:
            conversation_context = {
                "conversation_id": conversation_id,
                "contact_phone": context.get("contact_phone"),
                "contact_name": contact_name,
                "contact_telegram_id": recipient_id,
                "sender_id": sender_info["id"],
                "sender_slug": sender_info["slug"],
                "sender_name": sender_info.get("name", sender_info["slug"]),
                "ai_context_id": ai_context_id
            }

            reply = await ai_engine.generate_response(
                session=session,
                conversation_id=conversation_id,
                context_id=ai_context_id,
                contact_name=contact_name,
                new_message=message_text,
                conversation_context=conversation_context
            )

        if reply and client:
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
            result = await session.execute(
                text("""
                    SELECT id, slug, phone, session_string, ai_context_id, proxy
                    FROM senders
                    WHERE is_active = true AND role = 'sender'
                """)
            )
            rows = result.fetchall()
            return [
                {
                    "id": str(r[0]),
                    "slug": r[1],
                    "phone": r[2],
                    "session_string": r[3],
                    "ai_context_id": str(r[4]) if r[4] else None,
                    "proxy": r[5]
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
            # Ищем существующий
            result = await session.execute(
                text("SELECT id, ai_enabled, ai_context_id, status FROM conversations WHERE sender_id = :sender_id AND contact_telegram_id = :tg_id"),
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
            result = await session.execute(
                text("""
                    INSERT INTO conversations (sender_id, contact_phone, contact_name, contact_telegram_id, ai_enabled, ai_context_id)
                    VALUES (:sender_id, :phone, :name, :tg_id, true, :ai_context_id)
                    RETURNING id
                """),
                {"sender_id": sender_id, "phone": contact_phone, "name": contact_name, "tg_id": contact_telegram_id, "ai_context_id": ai_context_id}
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
        telegram_message_id: int
    ) -> bool:
        """
        Сохранить сообщение в БД
        Returns: True if message was saved, False if it was a duplicate
        """
        async with AsyncSessionLocal() as session:
            try:
                await session.execute(
                    text("""
                        INSERT INTO messages (conversation_id, direction, message_text, sent_by, telegram_message_id)
                        VALUES (:conv_id, :direction, :msg_text, :sent_by, :msg_id)
                    """),
                    {
                        "conv_id": conversation_id,
                        "direction": direction,
                        "msg_text": message_text,
                        "sent_by": sent_by,
                        "msg_id": telegram_message_id
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
                result = await session.execute(text("""
                    SELECT s.phone, s.telegram_id, s.id
                    FROM warmup_pool wp
                    JOIN senders s ON s.id = wp.sender_id
                    WHERE wp.is_active = true AND s.is_active = true AND s.role = 'sender'
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

            # Пропускаем системные сообщения Telegram (+42777, id=777000)
            TELEGRAM_SERVICE_PHONES = {"+42777", "42777"}
            if phone in TELEGRAM_SERVICE_PHONES or sender.id == 777000:
                logger.info(f"📨 Пропускаем сервисное сообщение Telegram от {phone} (id={sender.id})")
                return

            # Групповые чаты и каналы: только помечаем прочитанным, AI не запускаем
            if event.is_group or event.is_channel:
                try:
                    await event.client.send_read_acknowledge(event.chat_id, max_id=event.message.id)
                    logger.debug(f"📨 Прочитано сообщение в чате/канале {event.chat_id} от {name}")
                except Exception as e:
                    logger.debug(f"📨 Не удалось пометить прочитанным {event.chat_id}: {e}")
                return

            # === Детект antispam bot — отключаем AI и останавливаем очередь ===
            ANTISPAM_BOT_IDS = {178220800, 777000}  # Spam Info Bot, Telegram official
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
                await self._handle_antispam_signal(sender_info, name, sender.id, event.text or "")
                return

            # Пропускаем warmup-сообщения от наших аккаунтов в пуле прогрева.
            # Они обрабатываются WarmupWorker'ом, AI не нужен.
            if phone != "unknown":
                warmup_phones = await self._get_warmup_phones()
                if phone in warmup_phones:
                    logger.debug(f"🔥 Пропускаем warmup сообщение от {name} ({phone})")
                    return

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

                # Получаем webhook URL из контекста
                document_webhook_url = None
                if ai_context_id:
                    async with AsyncSessionLocal() as session:
                        result = await session.execute(
                            text("SELECT document_webhook_url FROM ai_contexts WHERE id = :id"),
                            {"id": ai_context_id}
                        )
                        row = result.fetchone()
                        if row and row[0]:
                            document_webhook_url = row[0]
                            logger.info(f"📎 document_webhook_url найден: {document_webhook_url}")
                        else:
                            logger.info(f"📎 document_webhook_url не настроен для контекста {ai_context_id}")

                # Скачиваем и отправляем на webhook если URL настроен
                if document_webhook_url:
                    tmp_path = None
                    try:
                        # Определяем расширение
                        ext = os.path.splitext(file_name)[1] or ".bin"
                        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_file:
                            tmp_path = tmp_file.name

                        await event.message.download_media(file=tmp_path)
                        logger.info(f"📥 Медиа скачано: {tmp_path}")

                        # Fire-and-forget отправка на webhook
                        asyncio.create_task(
                            self.send_document_to_webhook(
                                file_path=tmp_path,
                                file_name=file_name,
                                file_type=file_type,
                                conversation_id=conversation_id,
                                contact_name=name,
                                contact_telegram_id=sender.id,
                                webhook_url=document_webhook_url
                            )
                        )
                        # Удалим файл после небольшой задержки (даём время на отправку)
                        asyncio.create_task(self._delayed_file_cleanup(tmp_path, delay=60))

                    except Exception as e:
                        logger.error(f"❌ Ошибка скачивания медиа от {name}: {e}", exc_info=True)
                        if tmp_path:
                            try:
                                os.unlink(tmp_path)
                            except Exception:
                                pass
                else:
                    logger.info(f"ℹ️ document_webhook_url не настроен, документ не отправлен на обработку")

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

            # === Сохраняем в БД ===
            message_saved = await self.save_message(
                conversation_id=conversation_id,
                direction="inbound",
                message_text=message_text,
                sent_by="contact",
                telegram_message_id=event.id
            )

            if not message_saved:
                return

            logger.info(f"✅ Сообщение сохранено в conversation {conversation_id[:8]}...")

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

                    # Контекст для отложенной обработки
                    context = {
                        "ai_context_id": ai_context_id,
                        "contact_name": name,
                        "contact_phone": phone,
                        "client": event.client,
                        "recipient_id": sender.id,
                        "sender_info": sender_info
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
        Реакция на сообщение от antispam-бота:
        1. Отключить AI во всех диалогах этого sender'а
        2. Отменить все pending задачи в очереди для этого sender'а
        3. Залогировать
        """
        sender_id = sender_info["id"]
        sender_slug = sender_info["slug"]

        try:
            async with AsyncSessionLocal() as session:
                # 1. Отключаем AI для всех активных диалогов этого аккаунта
                result = await session.execute(
                    text("""
                        UPDATE conversations
                        SET ai_enabled = false,
                            paused_at = NOW(),
                            paused_reason = :reason,
                            updated_at = NOW()
                        WHERE sender_id = :sender_id
                          AND ai_enabled = true
                        RETURNING id
                    """),
                    {
                        "sender_id": sender_id,
                        "reason": f"Auto-disabled: antispam signal from {bot_name} (id={bot_id}). Message: {message_text[:200]}"
                    }
                )
                disabled_rows = result.fetchall()
                disabled_count = len(disabled_rows)

                # 2. Отменяем все pending задачи в очереди для этого sender'а
                result2 = await session.execute(
                    text("""
                        UPDATE message_queue
                        SET status = 'failed',
                            error_message = :reason,
                            finished_at = NOW()
                        WHERE sender_id = :sender_id
                          AND status IN ('pending', 'processing')
                        RETURNING id
                    """),
                    {
                        "sender_id": sender_id,
                        "reason": f"Auto-cancelled: antispam signal received from {bot_name}"
                    }
                )
                cancelled_rows = result2.fetchall()
                cancelled_count = len(cancelled_rows)

                await session.commit()

            logger.warning(
                f"🚨 ANTISPAM [{sender_slug}]: "
                f"отключён AI в {disabled_count} диалогах, "
                f"отменено {cancelled_count} задач в очереди."
            )

        except Exception as e:
            logger.error(f"❌ Ошибка при обработке antispam-сигнала для {sender_slug}: {e}", exc_info=True)
    
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

            # Пропускаем warmup-сообщения: исходящее от warmup-аккаунта к другому warmup-аккаунту.
            # Эти диалоги не должны попадать в основной дашборд.
            if sender_info["id"] in self._warmup_sender_ids:
                warmup_tg_ids = await self._get_warmup_telegram_ids()
                if chat.id in warmup_tg_ids:
                    logger.debug(f"🔥 Пропускаем warmup исходящее от {sender_info['slug']} к {name}")
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
                return  # no reconnect — session is dead

            except UserDeactivatedBanError as e:
                logger.critical(f"❌ {sender_info['slug']}: account banned — {e}")
                await self._set_auth_status(sender_info["id"], sender_info["slug"], "banned")
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
    
    async def run(self):
        """Запуск всех клиентов"""
        logger.info("🚀 Запуск Telegram Listener с AI Engine...")
        
        senders = await self.get_active_senders()
        
        if not senders:
            logger.warning("⚠️ Нет активных отправителей")
            return
        
        logger.info(f"📋 Найдено {len(senders)} отправителей")
        
        # Запускаем клиенты параллельно
        tasks = [self.start_client(s) for s in senders]
        await asyncio.gather(*tasks)
    
    async def stop(self):
        """Остановка всех клиентов"""
        logger.info("🛑 Останавливаем клиенты...")
        self.running = False
        for slug, client in self.clients.items():
            await client.disconnect()
            logger.info(f"  - {slug} отключён")


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
