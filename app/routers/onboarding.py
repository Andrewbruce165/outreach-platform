"""
Onboarding Router
Self-service подключение Telegram аккаунтов
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from telethon import TelegramClient
from telethon.sessions import StringSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import ProxyPool
from app.schemas import ProxyConfig
from telethon.errors import (
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
    FloodWaitError,
    PhoneNumberBannedError
)
import asyncio
import base64
import logging
from io import BytesIO

import qrcode
import socks

from app.config import get_settings
from app.routers.auth import verify_api_key
from app.services.telegram import build_proxy_tuple, make_telegram_client
from app.services.encryption import encrypt_session
from app.models import Sender

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])

# Временное хранилище сессий онбординга (в памяти)
# В продакшене лучше использовать Redis
_onboarding_sessions: dict[str, dict] = {}


# === Schemas ===

class StartOnboardingRequest(BaseModel):
    phone: str  # Номер телефона с кодом страны, например +79001234567


class StartOnboardingResponse(BaseModel):
    session_id: str
    phone_code_hash: str
    status: str  # "code_sent"


class VerifyCodeRequest(BaseModel):
    session_id: str
    code: str  # 5-значный код из Telegram
    role: Optional[str] = Field("sender", description="'sender' = отправщик, 'checker' = проверщик номеров")


class VerifyCodeResponse(BaseModel):
    status: str  # "success" или "2fa_required"
    session_string: Optional[str] = None  # Зашифрованный, только при success
    role: Optional[str] = None  # Пробрасывается в POST /api/v1/senders при создании аккаунта
    proxy: Optional[ProxyConfig] = None  # Прокси для передачи в POST /api/v1/senders


class Verify2FARequest(BaseModel):
    session_id: str
    password: str  # Пароль 2FA


class Verify2FAResponse(BaseModel):
    status: str  # "success"
    session_string: str  # Зашифрованный
    role: Optional[str] = None  # Пробрасывается в POST /api/v1/senders при создании аккаунта
    proxy: Optional[ProxyConfig] = None  # Прокси для передачи в POST /api/v1/senders


class OnboardingError(BaseModel):
    error: str
    code: str
    retry_after: Optional[int] = None


class StartQRRequest(BaseModel):
    role: Optional[str] = Field("sender", description="'sender' или 'checker'")


class StartQRResponse(BaseModel):
    session_id: str
    qr_image: str  # base64 PNG
    status: str    # "pending"


class QRStatusResponse(BaseModel):
    status: str    # "pending" | "success" | "2fa_required" | "expired"
    session_string: Optional[str] = None
    qr_image: Optional[str] = None  # свежий QR при status=="pending"
    role: Optional[str] = None
    proxy: Optional[ProxyConfig] = None  # Прокси для передачи в POST /api/v1/senders


# === Pool helper ===

async def _get_free_proxy(db: AsyncSession) -> ProxyConfig:
    """Select the first free proxy from proxy_pool. Raises 503 if pool is empty."""
    result = await db.execute(
        select(ProxyPool)
        .where(ProxyPool.assigned_to_sender_id.is_(None))
        .limit(1)
    )
    pool_entry = result.scalar_one_or_none()

    if pool_entry is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Нет свободных прокси в пуле. Освободите существующий или инициализируйте пул через POST /api/v1/proxy-pool/init",
                "code": "NO_FREE_PROXY",
            },
        )

    return ProxyConfig(
        type="socks5",
        host=pool_entry.host,
        port=pool_entry.port,
        username=pool_entry.username,
        password=pool_entry.password,
    )


# === QR helpers ===

def _make_qr_image(url: str) -> str:
    """Generate a base64-encoded PNG QR code from a tg:// URL."""
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def _wait_for_qr(session_id: str) -> None:
    """Background task: wait for QR scan and update session status."""
    session_data = _onboarding_sessions.get(session_id)
    if not session_data:
        return

    client: TelegramClient = session_data["client"]
    qr_login = session_data["qr_login"]

    try:
        await qr_login.wait(timeout=120)

        if session_id not in _onboarding_sessions:
            return

        session_data["status"] = "success"
        session_data["session_string"] = client.session.save()
        logger.info(f"✅ QR авторизация: {session_id[:8]}...")

        # Auto-save for reauth flow
        await _auto_save_reauth(session_data, session_data["session_string"])

    except SessionPasswordNeededError:
        if session_id not in _onboarding_sessions:
            return
        session_data["status"] = "2fa_required"
        session_data["awaiting_2fa"] = True
        session_data["phone"] = "qr_auth"  # для совместимости с verify-2fa логированием
        logger.info(f"🔒 QR требует 2FA: {session_id[:8]}...")

    except Exception as e:
        if session_id in _onboarding_sessions:
            session_data["status"] = "expired"
        try:
            await client.disconnect()
        except Exception:
            pass
        logger.info(f"⏰ QR сессия завершена: {session_id[:8]}... ({type(e).__name__})")


# === Reauth helper ===

async def _auto_save_reauth(session_data: dict, session_string: str) -> None:
    """Auto-save session_string to sender on reauth. Encrypts and updates DB."""
    reauth_slug = session_data.get("reauth_slug")
    if not reauth_slug:
        return

    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Sender).where(Sender.slug == reauth_slug))
        sender = result.scalar_one_or_none()
        if sender:
            sender.session_string = encrypt_session(session_string)
            sender.auth_status = "ok"
            sender.is_active = True
            await db.commit()
            logger.info(f"🔄 Реавторизация: session обновлён для {reauth_slug}, auth_status -> ok")

            # Restart listener for sender-role accounts
            if sender.role == "sender":
                import subprocess
                try:
                    subprocess.run(["docker", "restart", "telegram-listener"], capture_output=True, timeout=10)
                    logger.info(f"✅ Listener перезапущен после реавторизации {reauth_slug}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось перезапустить Listener: {e}")


# === Endpoints ===

@router.post("/reauth/{slug}", response_model=StartOnboardingResponse)
async def start_reauth(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Реавторизация существующего аккаунта.
    Берёт прокси из sender'а, отправляет код на его телефон.
    После verify-code / verify-2fa session_string обновляется автоматически.
    """
    result = await db.execute(select(Sender).where(Sender.slug == slug))
    sender = result.scalar_one_or_none()

    if not sender:
        raise HTTPException(status_code=404, detail={
            "error": f"Sender '{slug}' не найден",
            "code": "SENDER_NOT_FOUND"
        })

    if not sender.phone:
        raise HTTPException(status_code=400, detail={
            "error": "У sender'а не указан номер телефона",
            "code": "NO_PHONE"
        })

    phone = sender.phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        phone = "+" + phone

    # Use sender's own proxy
    proxy_tuple = build_proxy_tuple(sender.proxy) if sender.proxy else None
    proxy_config = ProxyConfig(**sender.proxy) if sender.proxy else None

    logger.info(f"🔄 Реавторизация {slug} ({phone[:6]}***), прокси: {sender.proxy.get('host', 'none') if sender.proxy else 'none'}")

    client = make_telegram_client(
        StringSession(),
        proxy=sender.proxy,
    )

    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)

        import uuid
        session_id = str(uuid.uuid4())

        code_type = type(sent_code.type).__name__

        _onboarding_sessions[session_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "proxy": proxy_config,
            "reauth_slug": slug,
        }

        logger.info(f"✅ Код отправлен для реавторизации {slug}, session_id: {session_id[:8]}..., тип: {code_type}")

        return StartOnboardingResponse(
            session_id=session_id,
            phone_code_hash=sent_code.phone_code_hash,
            status="code_sent"
        )

    except PhoneNumberBannedError:
        await client.disconnect()
        raise HTTPException(status_code=400, detail={
            "error": "Этот номер заблокирован в Telegram",
            "code": "PHONE_NUMBER_BANNED"
        })
    except FloodWaitError as e:
        await client.disconnect()
        raise HTTPException(status_code=429, detail={
            "error": f"Слишком много попыток. Подождите {e.seconds} секунд",
            "code": "FLOOD_WAIT",
            "retry_after": e.seconds
        })
    except (ConnectionError, OSError) as e:
        await client.disconnect()
        logger.error(f"❌ Прокси недоступен для реавторизации {slug}: {e}")
        raise HTTPException(status_code=502, detail={
            "error": f"Прокси недоступен: {e}",
            "code": "PROXY_UNAVAILABLE"
        })
    except Exception as e:
        await client.disconnect()
        logger.error(f"❌ Ошибка реавторизации {slug}: {e}")
        raise HTTPException(status_code=500, detail={
            "error": str(e),
            "code": "UNKNOWN_ERROR"
        })


@router.post("/reauth/qr/{slug}", response_model=StartQRResponse)
async def start_reauth_qr(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    QR-реавторизация существующего аккаунта.
    Берёт прокси из sender'а, возвращает QR-код.
    Поллить /qr/status/{session_id} — session_string сохранится автоматически.
    """
    import uuid

    result = await db.execute(select(Sender).where(Sender.slug == slug))
    sender = result.scalar_one_or_none()

    if not sender:
        raise HTTPException(status_code=404, detail={
            "error": f"Sender '{slug}' не найден",
            "code": "SENDER_NOT_FOUND"
        })

    proxy_tuple = build_proxy_tuple(sender.proxy) if sender.proxy else None

    logger.info(f"🔄 QR реавторизация {slug}, прокси: {sender.proxy.get('host', 'none') if sender.proxy else 'none'}")

    client = make_telegram_client(
        StringSession(),
        proxy=sender.proxy,
    )

    try:
        await client.connect()
        qr_login = await client.qr_login()
    except (ConnectionError, OSError) as e:
        await client.disconnect()
        logger.error(f"❌ QR прокси недоступен для реавторизации {slug}: {e}")
        raise HTTPException(status_code=502, detail={
            "error": f"Прокси недоступен: {e}",
            "code": "PROXY_UNAVAILABLE"
        })
    except Exception as e:
        await client.disconnect()
        logger.error(f"❌ QR reauth init error для {slug}: {e}")
        raise HTTPException(status_code=500, detail={
            "error": str(e),
            "code": "QR_INIT_ERROR"
        })

    session_id = str(uuid.uuid4())
    qr_image = _make_qr_image(qr_login.url)

    _onboarding_sessions[session_id] = {
        "client": client,
        "qr_login": qr_login,
        "status": "pending",
        "role": sender.role,
        "session_string": None,
        "proxy": None,
        "reauth_slug": slug,
    }

    asyncio.create_task(_wait_for_qr(session_id))

    logger.info(f"📱 QR реавторизация начата для {slug}: {session_id[:8]}...")

    return StartQRResponse(
        session_id=session_id,
        qr_image=qr_image,
        status="pending"
    )


@router.post("/start", response_model=StartOnboardingResponse)
async def start_onboarding(
    request: StartOnboardingRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Начать онбординг — отправить код подтверждения на телефон.
    Прокси выбирается автоматически из пула (POST /api/v1/proxy-pool/init должен быть вызван заранее).
    """
    phone = request.phone.strip().replace(" ", "").replace("-", "")

    if not phone.startswith("+"):
        phone = "+" + phone

    proxy = await _get_free_proxy(db)

    logger.info(f"🚀 Начинаем онбординг для {phone[:6]}***, прокси: {proxy.host}:{proxy.port}")

    # Создаём новый клиент с пустой сессией и прокси из пула
    client = make_telegram_client(
        StringSession(),
        proxy=proxy.model_dump(),
    )

    try:
        await client.connect()

        # Отправляем код
        sent_code = await client.send_code_request(phone)

        # Генерируем session_id
        import uuid
        session_id = str(uuid.uuid4())

        # Определяем тип доставки кода
        code_type = type(sent_code.type).__name__  # e.g. SentCodeTypeApp, SentCodeTypeSms

        # Сохраняем в память
        _onboarding_sessions[session_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "proxy": proxy,
        }

        logger.info(f"✅ Код отправлен для {phone[:6]}***, session_id: {session_id[:8]}..., тип доставки: {code_type}")
        
        return StartOnboardingResponse(
            session_id=session_id,
            phone_code_hash=sent_code.phone_code_hash,
            status="code_sent"
        )
        
    except PhoneNumberInvalidError:
        await client.disconnect()
        raise HTTPException(status_code=400, detail={
            "error": "Неверный формат номера телефона",
            "code": "PHONE_NUMBER_INVALID"
        })
    except PhoneNumberBannedError:
        await client.disconnect()
        raise HTTPException(status_code=400, detail={
            "error": "Этот номер заблокирован в Telegram",
            "code": "PHONE_NUMBER_BANNED"
        })
    except FloodWaitError as e:
        await client.disconnect()
        raise HTTPException(status_code=429, detail={
            "error": f"Слишком много попыток. Подождите {e.seconds} секунд",
            "code": "FLOOD_WAIT",
            "retry_after": e.seconds
        })
    except (ConnectionError, OSError) as e:
        await client.disconnect()
        logger.error(f"❌ Прокси недоступен для {phone[:6]}***: {e}")
        raise HTTPException(status_code=502, detail={
            "error": f"Прокси недоступен: {e}",
            "code": "PROXY_UNAVAILABLE"
        })
    except Exception as e:
        await client.disconnect()
        error_str = str(e)
        if "RECAPTCHA_CHECK" in error_str:
            logger.warning(f"⚠️ Recaptcha для {phone[:6]}***: номер не зарегистрирован или заблокирован Telegram")
            raise HTTPException(status_code=400, detail={
                "error": "Telegram требует проверку для этого номера. Возможно, номер не зарегистрирован в Telegram или временно ограничен.",
                "code": "RECAPTCHA_REQUIRED"
            })
        logger.error(f"❌ Ошибка отправки кода: {e}")
        raise HTTPException(status_code=500, detail={
            "error": error_str,
            "code": "UNKNOWN_ERROR"
        })


@router.post("/verify-code", response_model=VerifyCodeResponse)
async def verify_code(
    request: VerifyCodeRequest,
    _: str = Depends(verify_api_key)
):
    """
    Проверить код из Telegram.
    Возвращает session_string или требует 2FA.
    """
    session_data = _onboarding_sessions.get(request.session_id)
    
    if not session_data:
        raise HTTPException(status_code=404, detail={
            "error": "Сессия не найдена или истекла",
            "code": "SESSION_NOT_FOUND"
        })
    
    client: TelegramClient = session_data["client"]
    phone = session_data["phone"]
    phone_code_hash = session_data["phone_code_hash"]
    
    logger.info(f"🔐 Проверяем код для {phone[:6]}***")
    
    try:
        # Пробуем войти с кодом
        await client.sign_in(
            phone=phone,
            code=request.code,
            phone_code_hash=phone_code_hash
        )
        
        # Успешно! Получаем session string
        session_string = client.session.save()
        role = request.role or "sender"
        proxy: ProxyConfig | None = session_data.get("proxy")

        # Auto-save for reauth flow
        await _auto_save_reauth(session_data, session_string)
        reauth_slug = session_data.get("reauth_slug")

        # Очищаем временную сессию
        await client.disconnect()
        del _onboarding_sessions[request.session_id]

        logger.info(f"✅ Авторизация успешна для {phone[:6]}***, role={role}")

        return VerifyCodeResponse(
            status="success",
            session_string=session_string if not reauth_slug else None,
            role=role,
            proxy=proxy
        )

    except SessionPasswordNeededError:
        # Нужен 2FA пароль — сохраняем role для финального шага
        logger.info(f"🔒 Требуется 2FA для {phone[:6]}***")
        session_data["awaiting_2fa"] = True
        session_data["role"] = request.role or "sender"

        return VerifyCodeResponse(
            status="2fa_required",
            session_string=None,
            role=request.role or "sender"
        )
        
    except PhoneCodeInvalidError:
        raise HTTPException(status_code=400, detail={
            "error": "Неверный код. Попробуйте ещё раз",
            "code": "PHONE_CODE_INVALID"
        })
    except PhoneCodeExpiredError:
        # Очищаем сессию
        await client.disconnect()
        del _onboarding_sessions[request.session_id]
        
        raise HTTPException(status_code=400, detail={
            "error": "Код истёк. Запросите новый",
            "code": "PHONE_CODE_EXPIRED"
        })
    except FloodWaitError as e:
        raise HTTPException(status_code=429, detail={
            "error": f"Слишком много попыток. Подождите {e.seconds} секунд",
            "code": "FLOOD_WAIT",
            "retry_after": e.seconds
        })
    except Exception as e:
        logger.error(f"❌ Ошибка верификации кода: {e}")
        raise HTTPException(status_code=500, detail={
            "error": str(e),
            "code": "UNKNOWN_ERROR"
        })


@router.post("/verify-2fa", response_model=Verify2FAResponse)
async def verify_2fa(
    request: Verify2FARequest,
    _: str = Depends(verify_api_key)
):
    """
    Проверить пароль двухфакторной аутентификации.
    """
    session_data = _onboarding_sessions.get(request.session_id)
    
    if not session_data:
        raise HTTPException(status_code=404, detail={
            "error": "Сессия не найдена или истекла",
            "code": "SESSION_NOT_FOUND"
        })
    
    if not session_data.get("awaiting_2fa"):
        raise HTTPException(status_code=400, detail={
            "error": "2FA не требуется для этой сессии",
            "code": "2FA_NOT_REQUIRED"
        })
    
    client: TelegramClient = session_data["client"]
    phone = session_data["phone"]
    
    logger.info(f"🔐 Проверяем 2FA для {phone[:6]}***")
    
    try:
        # Входим с паролем 2FA
        await client.sign_in(password=request.password)
        
        # Успешно! Получаем session string
        session_string = client.session.save()
        role = session_data.get("role", "sender")
        proxy: ProxyConfig | None = session_data.get("proxy")

        # Auto-save for reauth flow
        await _auto_save_reauth(session_data, session_string)
        reauth_slug = session_data.get("reauth_slug")

        # Очищаем временную сессию
        await client.disconnect()
        del _onboarding_sessions[request.session_id]

        logger.info(f"✅ 2FA пройден для {phone[:6]}***, role={role}")

        return Verify2FAResponse(
            status="success",
            session_string=session_string if not reauth_slug else "saved",
            role=role,
            proxy=proxy
        )
        
    except PasswordHashInvalidError:
        raise HTTPException(status_code=400, detail={
            "error": "Неверный пароль 2FA",
            "code": "PASSWORD_INVALID"
        })
    except FloodWaitError as e:
        logger.warning(f"⏳ FloodWait 2FA для {phone[:6]}***: ждать {e.seconds} сек")
        raise HTTPException(status_code=429, detail={
            "error": f"Слишком много попыток. Подождите {e.seconds} секунд",
            "code": "FLOOD_WAIT",
            "retry_after": e.seconds
        })
    except Exception as e:
        logger.error(f"❌ Ошибка 2FA: {e}")
        raise HTTPException(status_code=500, detail={
            "error": str(e),
            "code": "UNKNOWN_ERROR"
        })


@router.post("/qr/start", response_model=StartQRResponse)
async def start_qr_onboarding(
    request: StartQRRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Начать QR-авторизацию — вернуть QR-код для сканирования в Telegram.
    Прокси выбирается автоматически из пула (POST /api/v1/proxy-pool/init должен быть вызван заранее).
    Поллить /qr/status/{session_id} каждые 2-3 сек до получения session_string.
    """
    import uuid

    proxy = await _get_free_proxy(db)

    client = make_telegram_client(
        StringSession(),
        proxy=proxy.model_dump(),
    )

    try:
        await client.connect()
        qr_login = await client.qr_login()
    except (ConnectionError, OSError) as e:
        await client.disconnect()
        logger.error(f"❌ QR прокси недоступен: {e}")
        raise HTTPException(status_code=502, detail={
            "error": f"Прокси недоступен: {e}",
            "code": "PROXY_UNAVAILABLE"
        })
    except Exception as e:
        await client.disconnect()
        logger.error(f"❌ QR init error: {e}")
        raise HTTPException(status_code=500, detail={
            "error": str(e),
            "code": "QR_INIT_ERROR"
        })

    session_id = str(uuid.uuid4())
    qr_image = _make_qr_image(qr_login.url)

    _onboarding_sessions[session_id] = {
        "client": client,
        "qr_login": qr_login,
        "status": "pending",
        "role": request.role or "sender",
        "session_string": None,
        "proxy": proxy,
    }

    asyncio.create_task(_wait_for_qr(session_id))

    logger.info(f"📱 QR онбординг начат: {session_id[:8]}...")

    return StartQRResponse(
        session_id=session_id,
        qr_image=qr_image,
        status="pending"
    )


@router.get("/qr/status/{session_id}", response_model=QRStatusResponse)
async def get_qr_status(
    session_id: str,
    _: str = Depends(verify_api_key)
):
    """
    Проверить статус QR-авторизации.
    - pending: QR ещё не отсканирован, возвращает свежий qr_image
    - success: авторизация прошла, возвращает session_string
    - 2fa_required: нужен пароль → POST /verify-2fa с тем же session_id
    - expired: QR истёк или ошибка, начни заново
    """
    session_data = _onboarding_sessions.get(session_id)

    if not session_data:
        raise HTTPException(status_code=404, detail={
            "error": "Сессия не найдена или истекла",
            "code": "SESSION_NOT_FOUND"
        })

    status = session_data.get("status", "pending")
    role = session_data.get("role", "sender")

    if status == "success":
        session_string = session_data["session_string"]
        reauth_slug = session_data.get("reauth_slug")
        proxy: ProxyConfig | None = session_data.get("proxy")
        del _onboarding_sessions[session_id]
        return QRStatusResponse(
            status="success",
            session_string=session_string if not reauth_slug else None,
            role=role,
            proxy=proxy
        )

    if status == "2fa_required":
        return QRStatusResponse(status="2fa_required", role=role)

    if status == "expired":
        del _onboarding_sessions[session_id]
        return QRStatusResponse(status="expired")

    # pending — возвращаем актуальный QR (url обновляется внутри qr_login.wait())
    qr_login = session_data.get("qr_login")
    qr_image = _make_qr_image(qr_login.url) if qr_login else None
    return QRStatusResponse(status="pending", qr_image=qr_image, role=role)


@router.delete("/cancel/{session_id}")
async def cancel_onboarding(
    session_id: str,
    _: str = Depends(verify_api_key)
):
    """
    Отменить онбординг и очистить сессию.
    """
    session_data = _onboarding_sessions.get(session_id)
    
    if session_data:
        client: TelegramClient = session_data["client"]
        await client.disconnect()
        del _onboarding_sessions[session_id]
        logger.info(f"🗑️ Онбординг отменён: {session_id[:8]}...")
    
    return {"status": "cancelled"}
