import logging
import asyncio
import tempfile
import os
import socks
from typing import Optional
from urllib.parse import urlparse

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import ImportContactsRequest, GetContactsRequest, ResolvePhoneRequest
from telethon.tl.types import InputPhoneContact, InputPeerUser
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    UserNotMutualContactError,
    AuthKeyError,
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    AuthKeyPermEmptyError,
    UserDeactivatedBanError,
)
from sqlalchemy import select, text

from app.config import get_settings
from app.services.encryption import decrypt_session
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Auth errors that mean the session is dead and needs re-authorization
AUTH_ERRORS = (AuthKeyError, AuthKeyUnregisteredError, AuthKeyDuplicatedError, AuthKeyPermEmptyError)


class SessionAuthError(Exception):
    """Raised when Telegram session is invalid and needs re-authorization."""
    def __init__(self, slug: str, auth_status: str, detail: str):
        self.slug = slug
        self.auth_status = auth_status
        self.detail = detail
        super().__init__(f"[{slug}] {auth_status}: {detail}")


async def _set_auth_status(slug: str, auth_status: str):
    """Update sender auth_status in DB."""
    from app.models import Sender
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Sender).where(Sender.slug == slug))
        sender = result.scalar_one_or_none()
        if sender:
            sender.auth_status = auth_status
            await db.commit()
    logger.warning(f"auth_status for {slug} -> {auth_status}")

settings = get_settings()

# Device fingerprint mimicking Telegram Desktop.
# lang_pack is the key signal Telegram uses to identify official vs third-party clients.
# Sessions with empty lang_pack (Telethon default) are terminated when mobile logs out.
# Telethon doesn't expose lang_pack as a constructor parameter — we patch _init_request
# manually before connect() in make_telegram_client().
_CLIENT_FINGERPRINT = {
    "device_model": "Desktop",
    "system_version": "Windows 10",
    "app_version": "5.3.1",
    "lang_code": "ru",
    "system_lang_code": "ru-RU",
}

# Keep alias for imports in listener.py / checker.py / onboarding.py
DESKTOP_CLIENT_KWARGS = _CLIENT_FINGERPRINT

_PROXY_TYPE_MAP = {
    "socks5": socks.SOCKS5,
    "socks4": socks.SOCKS4,
    "http": socks.HTTP,
}


def build_proxy_tuple(proxy: dict | None) -> tuple | None:
    """Convert proxy config dict to Telethon-compatible proxy tuple.

    Returns None if proxy is None (client connects directly).
    Format: (type, host, port) or (type, host, port, True, username, password)
    """
    if not proxy:
        return None
    proxy_type = _PROXY_TYPE_MAP.get(proxy["type"].lower())
    if not proxy_type:
        logger.warning(f"Unknown proxy type: {proxy['type']}, connecting without proxy")
        return None
    host = proxy["host"]
    port = proxy["port"]
    username = proxy.get("username")
    if username:
        return (proxy_type, host, port, True, username, proxy.get("password", ""))
    return (proxy_type, host, port)


def make_telegram_client(
    session: StringSession,
    proxy: dict | None = None,
    flood_sleep_threshold: int = 60,
    client_class: type = TelegramClient,
) -> TelegramClient:
    """Create a TelegramClient with official-client fingerprint.

    Patches _init_request.lang_pack before connect() so Telegram identifies
    the session as a known platform client (tdesktop). Without this patch,
    Telethon sends an empty lang_pack which Telegram uses to mark the session
    as third-party and terminates it when the user logs out from mobile.

    Pass client_class=ResilientTelegramClient for the listener subprocess.
    Must be called before client.connect().
    """
    client = client_class(
        session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        flood_sleep_threshold=flood_sleep_threshold,
        proxy=build_proxy_tuple(proxy),
        **_CLIENT_FINGERPRINT,
    )
    client._init_request.lang_pack = "tdesktop"
    return client


class TelegramService:
    """Service for Telegram operations via Telethon.

    IMPORTANT: Clients are created per-operation and disconnected after use.
    Persistent connections would steal Telegram updates from the listener container.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_client(
        self,
        sender_slug: str,
        encrypted_session: str,
        proxy: dict | None = None
    ) -> TelegramClient:
        """Create a temporary Telegram client for a single operation.

        IMPORTANT: Caller MUST disconnect the client after use via disconnect_client(),
        otherwise the persistent connection will steal updates from the listener.

        Raises SessionAuthError if the session is dead (expired/revoked/banned).
        """
        if sender_slug not in self._locks:
            self._locks[sender_slug] = asyncio.Lock()

        async with self._locks[sender_slug]:
            session_string = decrypt_session(encrypted_session)
            client = make_telegram_client(
                StringSession(session_string),
                proxy=proxy,
            )

            try:
                await client.connect()
            except AUTH_ERRORS as e:
                await _set_auth_status(sender_slug, "session_expired")
                raise SessionAuthError(sender_slug, "session_expired", str(e))
            except UserDeactivatedBanError as e:
                await _set_auth_status(sender_slug, "banned")
                raise SessionAuthError(sender_slug, "banned", str(e))

            try:
                if not await client.is_user_authorized():
                    await client.disconnect()
                    await _set_auth_status(sender_slug, "session_expired")
                    raise SessionAuthError(sender_slug, "session_expired", "Session is not authorized")
            except SessionAuthError:
                raise
            except AUTH_ERRORS as e:
                await client.disconnect()
                await _set_auth_status(sender_slug, "session_expired")
                raise SessionAuthError(sender_slug, "session_expired", str(e))
            except UserDeactivatedBanError as e:
                await client.disconnect()
                await _set_auth_status(sender_slug, "banned")
                raise SessionAuthError(sender_slug, "banned", str(e))

            return client

    async def disconnect_client(self, client: TelegramClient):
        """Disconnect client after operation to free the update stream for the listener."""
        try:
            if client and client.is_connected():
                await client.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting client: {e}")

    async def check_spambot(self, client: TelegramClient) -> dict:
        """Send /start to @SpamBot and parse the response.

        Returns dict with:
            status: 'free' | 'limited' | 'suspended' | 'unknown'
            raw_text: full SpamBot response
            limit_until: optional date string if limited
        """
        try:
            await client.send_message("SpamBot", "/start")
            await asyncio.sleep(2)

            messages = await client.get_messages("SpamBot", limit=1)
            if not messages:
                return {"status": "unknown", "raw_text": "No response from SpamBot"}

            text = messages[0].text or ""
            result = {"raw_text": text}

            text_lower = text.lower()
            if any(phrase in text_lower for phrase in ["good news", "no limits", "нет ограничений", "всё хорошо"]):
                result["status"] = "free"
            elif any(phrase in text_lower for phrase in ["limited", "restrict", "ограничен"]):
                result["status"] = "limited"
            elif any(phrase in text_lower for phrase in [
                "suspended", "blocked", "banned",
                "заблокирован", "приостановлен", "забанен"
            ]):
                result["status"] = "suspended"
            else:
                result["status"] = "unknown"

            return result

        except FloodWaitError as e:
            return {"status": "unknown", "raw_text": f"FloodWait: retry after {e.seconds}s"}
        except Exception as e:
            logger.error(f"SpamBot check failed: {e}")
            return {"status": "unknown", "raw_text": f"Error: {str(e)}"}
    
    async def _get_cached_contact(self, sender_id: str, phone: str) -> Optional[dict]:
        """Look up contact in DB cache (contacts_cache + conversations)."""
        async with AsyncSessionLocal() as db:
            # 1. Check contacts_cache
            row = (await db.execute(
                text("""
                    SELECT telegram_id, first_name, last_name, username, is_registered, access_hash
                    FROM contacts_cache
                    WHERE sender_id = :sender_id AND phone = :phone
                      AND updated_at > NOW() - INTERVAL '7 days'
                """),
                {"sender_id": sender_id, "phone": phone}
            )).fetchone()

            if row and row[0] and row[5] is not None:  # telegram_id + access_hash exist
                return {
                    "is_registered": True,
                    "telegram_id": row[0],
                    "access_hash": row[5],
                    "first_name": row[1],
                    "last_name": row[2],
                    "username": row[3],
                    "from_cache": True
                }
            if row and row[4] is False:  # known unregistered
                return {"is_registered": False, "from_cache": True}

            # 2. Check conversations table as fallback
            row = (await db.execute(
                text("""
                    SELECT contact_telegram_id, contact_name
                    FROM conversations
                    WHERE sender_id = :sender_id AND contact_phone = :phone
                      AND contact_telegram_id IS NOT NULL
                    LIMIT 1
                """),
                {"sender_id": sender_id, "phone": phone}
            )).fetchone()

            # conversations table has no access_hash — don't return from cache,
            # let ResolvePhoneRequest fetch it so we get a valid access_hash
            pass

            # 3. Cross-sender lookup — ONLY for is_registered=false
            # If a checker account already confirmed this number is unregistered,
            # skip ResolvePhoneRequest entirely (access_hash is not needed for rejections).
            # For registered numbers we do NOT use another account's cache: each sender
            # needs its own access_hash, so we let ResolvePhoneRequest run (but only
            # for confirmed-live numbers — that's the efficiency win).
            cross_row = (await db.execute(
                text("""
                    SELECT is_registered FROM contacts_cache
                    WHERE phone = :phone AND is_registered = false
                      AND updated_at > NOW() - INTERVAL '7 days'
                    LIMIT 1
                """),
                {"phone": phone}
            )).fetchone()

            if cross_row:
                logger.debug(f"Contact {phone} found unregistered in cross-sender cache — skipping ResolvePhone")
                return {"is_registered": False, "from_cache": True}

        return None

    async def _save_contact_cache(self, sender_id: str, phone: str, contact_info: dict):
        """Save contact lookup result to DB cache."""
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("""
                        INSERT INTO contacts_cache
                            (sender_id, phone, telegram_id, access_hash, first_name, last_name, username, is_registered)
                        VALUES (:sender_id, :phone, :tg_id, :access_hash, :first_name, :last_name, :username, :is_reg)
                        ON CONFLICT (sender_id, phone) DO UPDATE SET
                            telegram_id = EXCLUDED.telegram_id,
                            access_hash = EXCLUDED.access_hash,
                            first_name = EXCLUDED.first_name,
                            last_name = EXCLUDED.last_name,
                            username = EXCLUDED.username,
                            is_registered = EXCLUDED.is_registered,
                            updated_at = NOW()
                    """),
                    {
                        "sender_id": sender_id,
                        "phone": phone,
                        "tg_id": contact_info.get("telegram_id"),
                        "access_hash": contact_info.get("access_hash"),
                        "first_name": contact_info.get("first_name"),
                        "last_name": contact_info.get("last_name"),
                        "username": contact_info.get("username"),
                        "is_reg": contact_info.get("is_registered", False)
                    }
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to save contact cache: {e}")

    async def resolve_contact(
        self,
        client: TelegramClient,
        sender_id: str,
        phone: str,
        recipient_name: Optional[str] = None
    ) -> dict:
        """Resolve phone to telegram_id using cache first, then ImportContacts as fallback.

        This minimizes ImportContactsRequest calls to avoid Telegram spam detection.
        """
        # 1. Try cache
        cached = await self._get_cached_contact(sender_id, phone)
        if cached:
            logger.debug(f"Contact {phone} resolved from cache: tg_id={cached.get('telegram_id')}")
            return cached

        # 2. Cache miss — call Telegram API using ResolvePhoneRequest (no contact import)
        logger.info(f"Contact {phone} not in cache, calling ResolvePhoneRequest")
        try:
            result = await client(ResolvePhoneRequest(phone=phone))

            if result and result.users:
                user = result.users[0]
                contact_info = {
                    "is_registered": True,
                    "telegram_id": user.id,
                    "access_hash": user.access_hash,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "username": user.username
                }
            else:
                contact_info = {"is_registered": False}

            # 3. Cache the result
            await self._save_contact_cache(sender_id, phone, contact_info)
            return contact_info

        except PhoneNumberInvalidError:
            contact_info = {"is_registered": False}
            await self._save_contact_cache(sender_id, phone, contact_info)
            return {"is_registered": False, "error": "Invalid phone number"}
        except Exception as e:
            # ResolvePhoneRequest raises an RPC error (e.g. PHONE_NOT_OCCUPIED) when
            # the number is not registered — treat that as unregistered and cache it.
            err = str(e)
            if "PHONE_NOT_OCCUPIED" in err or "phone_not_occupied" in err.lower():
                contact_info = {"is_registered": False}
                await self._save_contact_cache(sender_id, phone, contact_info)
                return {"is_registered": False}
            # Any other exception (frozen account, FloodWait, network error, etc.)
            # must NOT be silently treated as "not registered" — raise so the queue
            # worker records the real error and retries.
            logger.error(f"Error checking contact via ResolvePhone: {e}")
            raise

    async def check_contact(
        self,
        client: TelegramClient,
        phone: str
    ) -> dict:
        """Check if phone number is registered in Telegram (legacy, no cache)."""
        try:
            result = await client(ImportContactsRequest(
                contacts=[InputPhoneContact(
                    client_id=0,
                    phone=phone,
                    first_name="Check",
                    last_name=""
                )]
            ))

            if result.users:
                user = result.users[0]
                return {
                    "is_registered": True,
                    "telegram_id": user.id,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "username": user.username
                }
            else:
                return {"is_registered": False}

        except PhoneNumberInvalidError:
            return {"is_registered": False, "error": "Invalid phone number"}
        except Exception as e:
            logger.error(f"Error checking contact: {e}")
            return {"is_registered": False, "error": str(e)}
    
    async def send_message(
        self,
        client: TelegramClient,
        phone: str,
        recipient_name: Optional[str],
        message: str,
        as_draft: bool = False,
        sender_id: Optional[str] = None
    ) -> dict:
        """Send message to phone number. Client is disconnected after the operation."""
        try:
            # Resolve contact (cache first, ImportContacts only for new contacts)
            if sender_id:
                contact_info = await self.resolve_contact(client, sender_id, phone, recipient_name)
            else:
                contact_info = await self.check_contact(client, phone)

            if not contact_info.get("is_registered"):
                return {
                    "success": False,
                    "error": {
                        "code": "RECIPIENT_NOT_IN_TELEGRAM",
                        "message": f"Номер {phone} не зарегистрирован в Telegram"
                    }
                }

            telegram_id = contact_info["telegram_id"]
            access_hash = contact_info.get("access_hash")

            # Use InputPeerUser with access_hash to avoid "entity not found" errors.
            # ResolvePhoneRequest returns the user object but doesn't add it to the
            # session entity cache — so bare telegram_id would fail on send.
            if access_hash is not None:
                peer = InputPeerUser(user_id=telegram_id, access_hash=access_hash)
            else:
                peer = telegram_id  # fallback for cached contacts without access_hash

            if as_draft:
                return {
                    "success": True,
                    "action": "draft",
                    "recipient": {
                        "telegram_id": telegram_id,
                        "name": contact_info.get("first_name"),
                        "username": contact_info.get("username"),
                        "was_added_to_contacts": False
                    }
                }

            # Send message
            sent = await client.send_message(peer, message)

            return {
                "success": True,
                "action": "sent",
                "message_id": str(sent.id),
                "recipient": {
                    "telegram_id": telegram_id,
                    "name": contact_info.get("first_name"),
                    "username": contact_info.get("username"),
                    "was_added_to_contacts": not contact_info.get("from_cache", False)
                }
            }

        except FloodWaitError as e:
            return {
                "success": False,
                "error": {
                    "code": "FLOOD_WAIT",
                    "message": f"Rate limited. Retry after {e.seconds} seconds",
                    "retry_after": e.seconds
                }
            }
        except PeerFloodError:
            return {
                "success": False,
                "error": {
                    "code": "PEER_FLOOD",
                    "message": "Спам-ограничение аккаунта. Требуется пауза и ручная проверка."
                }
            }
        except UserNotMutualContactError:
            return {
                "success": False,
                "error": {
                    "code": "PRIVACY_RESTRICTED",
                    "message": "Пользователь ограничил приватность сообщений"
                }
            }
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return {
                "success": False,
                "error": {
                    "code": "SEND_FAILED",
                    "message": str(e)
                }
            }
        finally:
            await self.disconnect_client(client)

    async def send_file(
        self,
        client: TelegramClient,
        phone: str,
        recipient_name: Optional[str],
        file_url: str,
        file_name: Optional[str] = None,
        caption: Optional[str] = None,
        sender_id: Optional[str] = None
    ) -> dict:
        """Download file from URL and send to recipient. Client is disconnected after the operation."""
        tmp_path = None
        try:
            # Resolve contact (cache first, ImportContacts only for new contacts)
            if sender_id:
                contact_info = await self.resolve_contact(client, sender_id, phone, recipient_name)
            else:
                contact_info = await self.check_contact(client, phone)

            if not contact_info.get("is_registered"):
                return {
                    "success": False,
                    "error": {
                        "code": "RECIPIENT_NOT_IN_TELEGRAM",
                        "message": f"Номер {phone} не зарегистрирован в Telegram"
                    }
                }

            telegram_id = contact_info["telegram_id"]
            access_hash = contact_info.get("access_hash")

            if access_hash is not None:
                peer = InputPeerUser(user_id=telegram_id, access_hash=access_hash)
            else:
                peer = telegram_id  # fallback for cached contacts without access_hash

            # Download file from URL
            if not file_name:
                parsed = urlparse(file_url)
                file_name = os.path.basename(parsed.path) or "file"

            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
                resp = await http.get(file_url)
                resp.raise_for_status()
                file_data = resp.content

            # Save to temp file
            suffix = os.path.splitext(file_name)[1] or ""
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(file_data)
                tmp_path = f.name

            # Telegram caption limit for media is 1024 chars.
            # If caption exceeds the limit, send file without caption and follow up
            # with a separate text message so the full text is delivered.
            CAPTION_LIMIT = 1024
            file_caption = None
            overflow_text = None
            if caption:
                if len(caption) <= CAPTION_LIMIT:
                    file_caption = caption
                else:
                    overflow_text = caption

            # Send file via Telethon
            sent = await client.send_file(
                peer,
                tmp_path,
                caption=file_caption,
                file_name=file_name,
                force_document=True
            )

            # Send overflow caption as a follow-up text message
            if overflow_text:
                try:
                    await client.send_message(peer, overflow_text)
                    logger.info(f"Caption overflow sent as separate message to {phone}")
                except Exception as e:
                    logger.warning(f"File sent but follow-up text message failed for {phone}: {e}")

            return {
                "success": True,
                "action": "file_sent",
                "message_id": str(sent.id),
                "recipient": {
                    "telegram_id": telegram_id,
                    "name": contact_info.get("first_name"),
                    "username": contact_info.get("username"),
                    "was_added_to_contacts": not contact_info.get("from_cache", False)
                }
            }

        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "error": {
                    "code": "FILE_DOWNLOAD_FAILED",
                    "message": f"Не удалось скачать файл: HTTP {e.response.status_code}"
                }
            }
        except httpx.RequestError as e:
            return {
                "success": False,
                "error": {
                    "code": "FILE_DOWNLOAD_FAILED",
                    "message": f"Ошибка скачивания файла: {e}"
                }
            }
        except FloodWaitError as e:
            return {
                "success": False,
                "error": {
                    "code": "FLOOD_WAIT",
                    "message": f"Rate limited. Retry after {e.seconds} seconds",
                    "retry_after": e.seconds
                }
            }
        except PeerFloodError:
            return {
                "success": False,
                "error": {
                    "code": "PEER_FLOOD",
                    "message": "Спам-ограничение аккаунта. Требуется пауза и ручная проверка."
                }
            }
        except UserNotMutualContactError:
            return {
                "success": False,
                "error": {
                    "code": "PRIVACY_RESTRICTED",
                    "message": "Пользователь ограничил приватность сообщений"
                }
            }
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            return {
                "success": False,
                "error": {
                    "code": "SEND_FAILED",
                    "message": str(e)
                }
            }
        finally:
            await self.disconnect_client(client)
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def send_message_by_telegram_id(
        self,
        sender_slug: str,
        encrypted_session: str,
        telegram_id: int,
        message: str,
        proxy: dict | None = None
    ) -> dict:
        """Send message directly by Telegram ID (for existing conversations)."""
        client = None
        try:
            client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)

            # Send message directly to telegram_id
            sent = await client.send_message(telegram_id, message)

            return {
                "success": True,
                "telegram_message_id": sent.id
            }

        except FloodWaitError as e:
            return {
                "success": False,
                "error": f"Rate limited. Retry after {e.seconds} seconds"
            }
        except Exception as e:
            logger.error(f"Error sending message by telegram_id: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            if client:
                await self.disconnect_client(client)


# Global instance
telegram_service = TelegramService()
