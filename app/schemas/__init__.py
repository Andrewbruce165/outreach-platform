from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal, Optional, List
from datetime import datetime
from uuid import UUID


# === Proxy ===
class ProxyConfig(BaseModel):
    type: Literal["socks5", "socks4", "http"]
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None


# === Send Message ===
class SendMessageRequest(BaseModel):
    sender: Optional[str] = Field(None, description="Slug отправителя. Если не указан — обязателен ai_context_id")
    ai_context_id: Optional[UUID] = Field(None, description="ID AI-контекста для авто-выбора аккаунта (ротация)")
    recipient_phone: str = Field(..., description="Номер получателя с кодом страны")
    recipient_name: Optional[str] = Field(None, description="Имя получателя")
    message: str = Field(..., max_length=4096, description="Текст сообщения")
    as_draft: bool = Field(False, description="Сохранить как черновик")
    metadata: Optional[dict] = Field(default_factory=dict, description="Дополнительные данные")
    callback_url: Optional[str] = Field(None, description="URL для webhook-уведомления после отправки")

    @model_validator(mode="after")
    def sender_or_context_required(self) -> "SendMessageRequest":
        if not self.sender and not self.ai_context_id:
            raise ValueError("Either 'sender' or 'ai_context_id' must be provided")
        return self


class RecipientInfo(BaseModel):
    telegram_id: Optional[int] = None
    name: Optional[str] = None
    username: Optional[str] = None
    was_added_to_contacts: bool = False


class SendMessageResponse(BaseModel):
    success: bool
    action: Optional[str] = None
    message_id: Optional[str] = None
    sender_slug: Optional[str] = None
    recipient: Optional[RecipientInfo] = None
    timestamp: datetime
    error: Optional[dict] = None


# === Send File ===
class SendFileRequest(BaseModel):
    sender: Optional[str] = Field(None, description="Slug отправителя. Если не указан — обязателен ai_context_id")
    ai_context_id: Optional[UUID] = Field(None, description="ID AI-контекста для авто-выбора аккаунта (ротация)")
    recipient_phone: str = Field(..., description="Номер получателя с кодом страны")
    recipient_name: Optional[str] = Field(None, description="Имя получателя")
    file_url: str = Field(..., description="URL файла для скачивания и отправки")
    file_name: Optional[str] = Field(None, description="Имя файла (если не указано, берётся из URL)")
    caption: Optional[str] = Field(None, max_length=4096, description="Подпись к файлу")
    metadata: Optional[dict] = Field(default_factory=dict, description="Дополнительные данные")
    callback_url: Optional[str] = Field(None, description="URL для webhook-уведомления после отправки")

    @model_validator(mode="after")
    def sender_or_context_required(self) -> "SendFileRequest":
        if not self.sender and not self.ai_context_id:
            raise ValueError("Either 'sender' or 'ai_context_id' must be provided")
        return self


# === Senders ===
class SenderCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., max_length=100)
    phone: str = Field(..., max_length=20)
    session_string: str
    ai_context_id: Optional[UUID] = Field(None, description="ID контекста AI для этого sender")
    role: Optional[str] = Field("sender", description="'sender' = отправщик, 'checker' = проверщик номеров")
    proxy: Optional[ProxyConfig] = Field(None, description="Прокси для подключения к Telegram")


class SenderUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    session_string: Optional[str] = None
    is_active: Optional[bool] = None
    ai_context_id: Optional[UUID] = None
    role: Optional[str] = Field(None, description="'sender' или 'checker'")
    proxy: Optional[ProxyConfig] = None


class SenderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    phone: str
    is_active: bool
    role: str = "sender"
    auth_status: str = "ok"
    proxy: Optional[ProxyConfig] = None
    ai_context_id: Optional[UUID] = None
    ai_context_name: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class SenderListResponse(BaseModel):
    senders: list[SenderResponse]


# === Check Contact ===
class CheckContactRequest(BaseModel):
    sender: str
    phone: str


class CheckContactResponse(BaseModel):
    phone: str
    is_registered: bool
    telegram_id: Optional[int] = None
    name: Optional[str] = None
    username: Optional[str] = None
    is_in_contacts: bool = False


# === Queue ===
class EnqueueResponse(BaseModel):
    success: bool
    queued: bool = True
    queue_id: Optional[str] = None
    queue_position: Optional[int] = None
    sender_slug: Optional[str] = None
    estimated_send_at: Optional[datetime] = None
    timestamp: datetime
    error: Optional[dict] = None


class QueueItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sender_slug: str
    item_type: str
    status: str
    recipient_phone: str
    recipient_name: Optional[str] = None
    message_text: Optional[str] = None
    file_url: Optional[str] = None
    queue_position: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Result fields — populated after successful send
    result_message_id: Optional[str] = None
    result_recipient_telegram_id: Optional[int] = None
    result_recipient_name: Optional[str] = None
    result_recipient_username: Optional[str] = None
    error_message: Optional[str] = None


class QueueStatsResponse(BaseModel):
    sender_slug: str
    pending: int
    processing: int
    sent_last_hour: int
    sent_last_minute: int
    next_send_at: Optional[datetime] = None


# === Batch Send ===
class BatchRecipient(BaseModel):
    phone: str
    name: Optional[str] = None
    metadata: Optional[dict] = None


class BatchSendRequest(BaseModel):
    sender: str
    recipients: List[BatchRecipient] = Field(..., min_length=1, max_length=500)
    message: str = Field(..., max_length=4096)
    as_draft: bool = False
    priority: int = 0
    callback_url: Optional[str] = None


class BatchEnqueueResult(BaseModel):
    phone: str
    queued: bool
    queue_id: Optional[str] = None
    error: Optional[str] = None


class BatchSendResponse(BaseModel):
    total: int
    queued: int
    failed: int
    results: List[BatchEnqueueResult]
    timestamp: datetime


# === Health ===
class SendersHealth(BaseModel):
    total: int
    active: int
    sessions_valid: int


class HealthResponse(BaseModel):
    status: str
    database: str
    senders: SendersHealth
    version: str
    uptime_seconds: int
