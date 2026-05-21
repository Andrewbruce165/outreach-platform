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


# === Senders (Phase 2 — D-11/D-13/D-14) ===

class RateLimits(BaseModel):
    """Per-sender лимиты (D-13). Defaults = "зелёный коридор" 4/20/150."""
    per_minute: int = 4
    per_hour: int = 20
    per_day: int = 150


class WarningItem(BaseModel):
    """D-14: warn-only, когда значения превышают soft cap, но в пределах hard cap."""
    field: str
    value: int
    recommended_max: int
    severity: Literal["warning"] = "warning"


class SenderCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., max_length=100)
    phone: str = Field(..., max_length=20)
    session_string: str
    ai_context_id: Optional[UUID] = Field(None, description="ID контекста AI для этого sender")
    role: Literal["sender", "checker"] = Field(
        "sender", description="'sender' = отправщик, 'checker' = проверщик номеров"
    )
    proxy: Optional[ProxyConfig] = Field(None, description="Прокси для подключения к Telegram")
    # Optional per-sender rate-limit overrides (with hard cap, D-14).
    rate_per_min: Optional[int] = Field(None, ge=1, le=10)
    rate_per_hour: Optional[int] = Field(None, ge=1, le=50)
    rate_per_day: Optional[int] = Field(None, ge=1, le=300)


class SenderUpdate(BaseModel):
    """PATCH /senders/{id}. Hard cap D-14: rate_per_min<=10, hour<=50, day<=300."""
    name: Optional[str] = None
    phone: Optional[str] = None
    session_string: Optional[str] = None
    lifecycle_status: Optional[Literal["active", "warmup", "paused"]] = None
    rate_per_min: Optional[int] = Field(None, ge=1, le=10)
    rate_per_hour: Optional[int] = Field(None, ge=1, le=50)
    rate_per_day: Optional[int] = Field(None, ge=1, le=300)
    ai_context_id: Optional[UUID] = None
    role: Optional[Literal["sender", "checker"]] = None
    proxy: Optional[ProxyConfig] = None


class SenderResponse(BaseModel):
    """D-11: derived `status` = 'error' если auth_status!='ok', иначе lifecycle_status."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    phone: str
    status: Literal["active", "warmup", "paused", "error"]   # derived
    auth_status: str
    lifecycle_status: Literal["active", "warmup", "paused"]
    rate_limits: RateLimits
    role: str = "sender"
    proxy: Optional[ProxyConfig] = None
    ai_context_id: Optional[UUID] = None
    ai_context_name: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class SenderCreateResponse(BaseModel):
    """Возврат create/update sender'а с warnings[] (D-14)."""
    sender: SenderResponse
    warnings: List[WarningItem] = []


class SenderListResponse(BaseModel):
    senders: list[SenderResponse]


class AssignProxyRequest(BaseModel):
    """POST /senders/{id}/assign-proxy (D-22)."""
    proxy_id: UUID


class ProxyPoolItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    host: str
    port: int
    type: str = "socks5"
    username: Optional[str] = None
    assigned_to_sender_id: Optional[UUID] = None


class ProxyPoolCreate(BaseModel):
    host: str
    port: int = Field(..., ge=1, le=65535)
    type: Literal["socks5", "socks4", "http"] = "socks5"
    username: Optional[str] = None
    password: Optional[str] = None


class ProxyPoolListResponse(BaseModel):
    proxies: List[ProxyPoolItem]
    total: int


# === Phase 2: Folders ===

class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class FolderUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class FolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    contact_count: int = 0
    created_at: datetime
    updated_at: datetime


class FolderListResponse(BaseModel):
    folders: List[FolderResponse]
    total: int


# === Phase 2: Contacts ===

class ContactBase(BaseModel):
    phone: Optional[str] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    source: Optional[str] = None
    custom: dict = {}


class ContactCreate(ContactBase):
    folder_id: Optional[UUID] = None
    folder_name: Optional[str] = None

    @model_validator(mode="after")
    def folder_or_name(self):
        if not self.folder_id and not self.folder_name:
            raise ValueError("Either folder_id or folder_name required")
        if not self.phone and not self.username:
            raise ValueError("Either phone or username required")
        return self


class ContactBatchPush(BaseModel):
    contacts: List[ContactCreate] = Field(..., min_length=1, max_length=1000)


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    folder_id: UUID
    phone: Optional[str]
    username: Optional[str]
    full_name: Optional[str]
    source: Optional[str]
    custom: dict
    tg_status: str
    tg_telegram_id: Optional[int]
    tg_username_resolved: Optional[str]
    tg_checked_at: Optional[datetime]
    created_at: datetime


class ContactImportRequest(BaseModel):
    """POST /contacts/import — applies stored CSV preview blob with user mapping."""
    import_id: UUID
    folder_id: Optional[UUID] = None
    folder_name: Optional[str] = None
    # mapping: {"0": "phone", "1": "full_name", "2": "custom.company"}
    mapping: dict[str, str]
    on_duplicate: Literal["skip"] = "skip"

    @model_validator(mode="after")
    def has_folder_target(self):
        if not self.folder_id and not self.folder_name:
            raise ValueError("Either folder_id or folder_name required")
        return self


class ContactImportPreviewResponse(BaseModel):
    import_id: UUID
    columns: List[str]
    sample_rows: List[dict]
    suggested_mapping: dict[str, str]
    encoding: Optional[str]
    delimiter: Optional[str]
    looks_like_no_header: bool = False


class ContactImportSummary(BaseModel):
    """202 Accepted body for /contacts/import + /contacts (push)."""
    total: int
    imported: int
    skipped_duplicates: int
    skipped_invalid: int
    skipped_phones: List[str] = []


class MoveContactRequest(BaseModel):
    folder_id: UUID


class MoveContactBatchRequest(BaseModel):
    contact_ids: List[UUID] = Field(..., min_length=1)
    folder_id: UUID


class RecheckRequest(BaseModel):
    contact_ids: Optional[List[UUID]] = None
    folder_id: Optional[UUID] = None

    @model_validator(mode="after")
    def one_required(self):
        if not self.contact_ids and not self.folder_id:
            raise ValueError("Either contact_ids or folder_id required")
        return self


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
