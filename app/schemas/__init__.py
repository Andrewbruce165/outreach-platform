from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, computed_field, constr, model_validator
from typing import Any, Literal, Optional, List
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
    """Phase 4 D-16 rewrite: campaign_id REQUIRED (was the legacy agent-id in Phase 3).

    Agent выводится через JOIN campaigns.agent_id. Workspace API-key push (n8n)
    продолжает работать тем же endpoint'ом — n8n должен передавать campaign_id.
    """
    campaign_id: UUID = Field(..., description="Campaign ID (workspace-scoped validation)")
    sender_slug: Optional[str] = Field(None, description="Explicit sender slug; if None, rotation picks one from campaign_senders")
    recipient_phone: str = Field(..., description="Номер получателя с кодом страны (E.164)")
    recipient_name: Optional[str] = Field(None, description="Имя получателя (для new conversation)")
    message: Optional[str] = Field(
        None,
        max_length=4096,
        description="Текст сообщения. Если None — рендерится из campaign.message_template + contact lookup.",
    )
    as_draft: bool = Field(False, description="Сохранить как черновик")
    metadata: Optional[dict] = Field(default_factory=dict, description="Дополнительные данные")
    callback_url: Optional[str] = Field(None, description="Webhook-уведомление после отправки")


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
    # NB: ai_context_id field dropped (Phase 3 C-05) — sender больше не «знает»
    # агента, связь через Campaign в Phase 4.
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
    # NB: ai_context_id field dropped (Phase 3 C-05).
    role: Optional[Literal["sender", "checker"]] = None
    proxy: Optional[ProxyConfig] = None


class SenderResponse(BaseModel):
    """D-11: derived `status` = 'error' если auth_status!='ok', иначе lifecycle_status."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    phone: str
    status: Literal["active", "warmup", "paused", "error", "limited", "frozen"]   # derived
    auth_status: str
    lifecycle_status: Literal["active", "warmup", "paused"]
    # Migration 028: write-restriction state, orthogonal to auth_status.
    restriction_status: Literal["none", "spam_limited", "frozen"] = "none"
    restricted_until: Optional[datetime] = None
    rate_limits: RateLimits
    role: str = "sender"
    proxy: Optional[ProxyConfig] = None
    # NB: ai_context_id / ai_context_name fields dropped (Phase 3 C-05) —
    # without this, Pydantic from_attributes=True would crash on
    # AttributeError because Sender ORM no longer has ai_context_id (RESEARCH Pitfall 4).
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    # Messages sent in the trailing 24h window (numerator of the TODAY column).
    # Same definition as the rate-limiter daily cap (queue.py:450-466):
    # COUNT(message_queue WHERE status='sent' AND finished_at >= now()-24h).
    # Only computed on the list endpoint; single-sender paths default to 0.
    sent_today: int = 0
    # POOL-09 (08-04 UAT fix): per-sender lock state for the campaign pool
    # add-picker. Populated ONLY on the list endpoint (GET /senders) = the first
    # running campaign in the same workspace that holds this sender (deterministic
    # ORDER BY c.name, mirroring _check_sender_not_in_running_campaign). None on
    # every other path (single-sender endpoints report no lock — same convention
    # as sent_today=0). None = sender is free to attach.
    locked_by_campaign_id: Optional[UUID] = None
    locked_by_campaign_name: Optional[str] = None


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


class DeleteContactBatchRequest(BaseModel):
    contact_ids: List[UUID] = Field(..., min_length=1)


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


# === Agents (Phase 3 — AGNT-01..04) ===

class FaqItem(BaseModel):
    """Single FAQ Q&A pair. C-01 resolution: array of objects (over dict)."""
    question: str = Field(..., max_length=500)
    answer: str = Field(..., max_length=2000)


# ─── Phase 05.1 agent v2 helpers (UI-SPEC §5.8) ──────────────────────────────


class ToneSpec(BaseModel):
    """Bi-polar tone settings −50..+50 (UI-SPEC §5.8 Voice tab ToneSlider)."""
    formal: int = Field(default=0, ge=-50, le=50)
    warm: int = Field(default=0, ge=-50, le=50)
    brief: int = Field(default=0, ge=-50, le=50)


class QAPair(BaseModel):
    """Single Q&A entry in agent.qa_pairs (UI-SPEC §5.8 FAQ tab)."""
    q: constr(min_length=1, max_length=2000)
    a: constr(min_length=1, max_length=4000)


class AgentCreate(BaseModel):
    """POST /api/v1/agents body (D-02 + UI-SPEC §5.8 v2)."""
    name: constr(min_length=1, max_length=100)
    # Legacy fields (Phase 3) — kept Optional for back-compat:
    system_prompt: Optional[str] = None
    rules: Optional[str] = None
    tone_of_voice: Optional[str] = None
    faq: List[FaqItem] = Field(default_factory=list)
    company_info: Optional[str] = None
    product_info: Optional[str] = None
    # 05.1 v2 fields (UI-SPEC §5.8):
    who_is_agent: Optional[str] = None
    company_knowledge: Optional[str] = None
    knowledge_base: Optional[str] = None
    voice_baseline: Optional[Literal["Professional", "Friendly", "Playful"]] = None
    tone: Optional[ToneSpec] = None
    max_message_length: Optional[int] = Field(default=None, ge=1, le=4096)
    mirror_language: Optional[bool] = None
    allow_emoji: Optional[bool] = None
    banlist: Optional[List[str]] = None
    qa_pairs: Optional[List[QAPair]] = None
    auto_pause_triggers: Optional[List[str]] = None
    auto_pause_scope: Optional[Literal["conversation", "contact", "campaign"]] = None


class AgentUpdate(BaseModel):
    """PATCH /api/v1/agents/{id} body. Partial PATCH (C-03 Phase 2 convention)."""
    name: Optional[constr(min_length=1, max_length=100)] = None
    system_prompt: Optional[str] = None
    rules: Optional[str] = None
    tone_of_voice: Optional[str] = None
    # None = leave unchanged; [] = clear FAQ; [...] = full replace (Pitfall 7)
    faq: Optional[List[FaqItem]] = None
    company_info: Optional[str] = None
    product_info: Optional[str] = None
    # 05.1 v2 fields (UI-SPEC §5.8) — all Optional, same semantics as AgentCreate:
    who_is_agent: Optional[str] = None
    company_knowledge: Optional[str] = None
    knowledge_base: Optional[str] = None
    voice_baseline: Optional[Literal["Professional", "Friendly", "Playful"]] = None
    tone: Optional[ToneSpec] = None
    max_message_length: Optional[int] = Field(default=None, ge=1, le=4096)
    mirror_language: Optional[bool] = None
    allow_emoji: Optional[bool] = None
    banlist: Optional[List[str]] = None
    qa_pairs: Optional[List[QAPair]] = None
    auto_pause_triggers: Optional[List[str]] = None
    auto_pause_scope: Optional[Literal["conversation", "contact", "campaign"]] = None


class AgentResponse(BaseModel):
    """GET / POST / PATCH response body. D-10: campaign_count hardcoded 0 в Phase 3."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    system_prompt: Optional[str] = None
    rules: Optional[str] = None
    tone_of_voice: Optional[str] = None
    faq: List[FaqItem] = []
    company_info: Optional[str] = None
    product_info: Optional[str] = None
    # 05.1 v2 fields (UI-SPEC §5.8) — serialised straight from JSONB/TEXT[]:
    who_is_agent: Optional[str] = None
    company_knowledge: Optional[str] = None
    knowledge_base: Optional[str] = None
    voice_baseline: Optional[str] = None
    # tone serialised as dict (NOT ToneSpec) — DB JSONB round-trip without
    # re-validating the response payload (back-compat: pre-05.1 rows have NULL).
    tone: Optional[dict] = None
    max_message_length: Optional[int] = None
    mirror_language: Optional[bool] = None
    allow_emoji: Optional[bool] = None
    banlist: Optional[List[str]] = None
    qa_pairs: Optional[List[dict]] = None
    auto_pause_triggers: Optional[List[str]] = None
    auto_pause_scope: Optional[str] = None
    campaign_count: int = 0
    created_at: datetime
    updated_at: datetime


class AgentListResponse(BaseModel):
    agents: List[AgentResponse]
    total: int


# === Phase 4: Campaigns (CAMP-01..17) =========================================

class ToolParamSpec(BaseModel):
    """Single param spec inside a custom tool's parameters[] array.

    Shape recovered from `ai_engine.build_tools()` (Phase 4 AUDIT Section 4):
    flat array of {name, type, description, required} — NOT a JSON Schema object.
    """
    name: constr(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$", max_length=64)
    type: Literal["string", "number", "integer", "boolean"] = "string"
    description: Optional[str] = Field(default=None, max_length=1024)
    required: bool = False


class ToolSpec(BaseModel):
    """Pydantic validation для campaigns.tools JSONB items (CAMP-15, C-10).

    Shape matches the internal `webhook_functions` form (AUDIT Section 4).
    NOT OpenAI's JSON Schema form — `ai_engine.build_tools()` converts at runtime.

    Phase 05.1 (Pitfall 6): per-tool webhook_url DEPRECATED — campaign-level
    webhook_url (CampaignCreate.webhook_url) unifies signal + tool dispatch.
    Kept Optional for Phase 4 back-compat (test_custom_tools_wiring.py).
    """
    model_config = ConfigDict(from_attributes=True)

    # 05.1 NEW: client-supplied id for UI editor stable React keys
    # (UI-SPEC §10 CampaignTools shape).
    id: Optional[constr(min_length=1, max_length=40)] = None
    name: constr(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$", max_length=64)
    description: constr(max_length=1024)
    parameters: List[ToolParamSpec] = Field(default_factory=list)
    # Deprecated in 05.1 (UI-SPEC §10) — kept Optional for Phase 4 back-compat.
    webhook_url: Optional[HttpUrl] = None
    webhook_method: Optional[Literal["POST", "GET"]] = "POST"


class CampaignSenderAttach(BaseModel):
    """Read-only sender entry inside CampaignResponse.attached_senders[].

    locked_by_campaign_id / locked_by_campaign_name populated when the sender
    is currently attached to a DIFFERENT running campaign in the same workspace.

    `id` mirrors `sender_id` so the entry is keyed identically to the Sender it
    references (UI/tests read `attached_senders[].id`); `sender_id` is retained
    for back-compat with Phase 4 consumers.
    """
    sender_id: UUID
    locked_by_campaign_id: Optional[UUID] = None
    locked_by_campaign_name: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> UUID:
        return self.sender_id


class CampaignSenderAttachRequest(BaseModel):
    """POST /api/v1/campaigns/{id}/senders body — attach a single sender to a pool.

    Thin request body (Plan 08-03 / D-02): pool mutation is one sender at a time
    via POST/DELETE /campaigns/{id}/senders. Distinct from CampaignSenderAttach,
    which is the read-only response sub-object inside attached_senders[].
    """
    sender_id: UUID


class CampaignCreate(BaseModel):
    """POST /api/v1/campaigns body."""
    model_config = ConfigDict(from_attributes=True)

    name: constr(min_length=1, max_length=150)
    description: Optional[str] = None
    # 024: optional для draft — кампанию можно сохранить незавершённой и докрутить через PATCH.
    # Обязательность agent/folder/template проверяется только на POST /{id}/start.
    agent_id: Optional[UUID] = None
    folder_id: Optional[UUID] = None
    sender_ids: List[UUID] = Field(default_factory=list)
    message_template: Optional[constr(min_length=1)] = None
    timezone: str = "Europe/Moscow"
    work_hour_start: int = Field(default=9, ge=0, le=23)
    work_hour_end: int = Field(default=20, ge=1, le=24)
    work_days_mask: int = Field(default=31, ge=1, le=127)
    start_date: Optional[datetime] = None
    stop_date: Optional[datetime] = None
    lead_webhook_url: Optional[HttpUrl] = None
    handoff_webhook_url: Optional[HttpUrl] = None
    finish_webhook_url: Optional[HttpUrl] = None
    lead_trigger_hint: Optional[str] = None
    handoff_trigger_hint: Optional[str] = None
    finish_trigger_hint: Optional[str] = None
    tools: List[ToolSpec] = Field(default_factory=list)
    # ── 05.1 v2 fields (UI-SPEC §5.5 step 2 + step 6). ──
    audience_hints: Optional[str] = None
    primary_goal: Optional[Literal["book_meeting", "qualify", "click", "engage"]] = None
    success_criteria: Optional[str] = None
    # Unified webhook URL — supersedes per-tool webhook_url + 3 legacy signal URLs
    # for new campaigns. Legacy URLs remain Optional above for Phase 4 back-compat.
    webhook_url: Optional[HttpUrl] = None
    # 026: per-campaign re-contact policy. Default false → strict cross-campaign
    # dedup (any existing conversation blocks). When true, only live & fresh
    # dialogs block; closed/stale ones are re-contactable.
    allow_recontact: bool = False
    recontact_min_age_days: int = Field(default=30, ge=1, le=365)

    @model_validator(mode="after")
    def _check_work_hours(self) -> "CampaignCreate":
        if self.work_hour_start >= self.work_hour_end:
            raise ValueError("work_hour_start must be less than work_hour_end")
        return self


class CampaignUpdate(BaseModel):
    """PATCH /api/v1/campaigns/{id} body — partial PATCH (все поля Optional).

    Note: sender_ids НЕ обновляется через PATCH (D-12, намеренно). Пул sender'ов
    управляется отдельными эндпоинтами POST/DELETE /campaigns/{id}/senders
    (Plan 08-03) с изоляцией workspace, sender-lock и min-pool/cold-pending
    гардами. PATCH игнорирует sender_ids.
    """
    name: Optional[constr(min_length=1, max_length=150)] = None
    description: Optional[str] = None
    agent_id: Optional[UUID] = None
    folder_id: Optional[UUID] = None
    message_template: Optional[constr(min_length=1)] = None
    timezone: Optional[str] = None
    work_hour_start: Optional[int] = Field(default=None, ge=0, le=23)
    work_hour_end: Optional[int] = Field(default=None, ge=1, le=24)
    work_days_mask: Optional[int] = Field(default=None, ge=1, le=127)
    start_date: Optional[datetime] = None
    stop_date: Optional[datetime] = None
    lead_webhook_url: Optional[HttpUrl] = None
    handoff_webhook_url: Optional[HttpUrl] = None
    finish_webhook_url: Optional[HttpUrl] = None
    lead_trigger_hint: Optional[str] = None
    handoff_trigger_hint: Optional[str] = None
    finish_trigger_hint: Optional[str] = None
    tools: Optional[List[ToolSpec]] = None
    # ── 05.1 v2 fields (UI-SPEC §5.5 step 2 + step 6). ──
    audience_hints: Optional[str] = None
    primary_goal: Optional[Literal["book_meeting", "qualify", "click", "engage"]] = None
    success_criteria: Optional[str] = None
    webhook_url: Optional[HttpUrl] = None
    # 026: per-campaign re-contact policy (partial PATCH).
    allow_recontact: Optional[bool] = None
    recontact_min_age_days: Optional[int] = Field(default=None, ge=1, le=365)


class CampaignResponse(BaseModel):
    """GET/POST/PATCH response body. Computed fields: is_exhausted, attached_senders."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    description: Optional[str] = None
    # 024: nullable на incomplete-draft (agent/folder ещё не заданы).
    agent_id: Optional[UUID] = None
    folder_id: Optional[UUID] = None
    status: str  # draft|running|paused|done
    timezone: str
    work_hour_start: int
    work_hour_end: int
    work_days_mask: int
    start_date: Optional[datetime] = None
    stop_date: Optional[datetime] = None
    message_template: str
    lead_webhook_url: Optional[str] = None
    handoff_webhook_url: Optional[str] = None
    finish_webhook_url: Optional[str] = None
    lead_trigger_hint: Optional[str] = None
    handoff_trigger_hint: Optional[str] = None
    finish_trigger_hint: Optional[str] = None
    tools: List[dict[str, Any]] = Field(default_factory=list)
    # ── 05.1 v2 fields (UI-SPEC §5.5 step 2 + step 6) — all Optional / NULL on
    # pre-05.1 rows; CampaignResponse keeps str (not HttpUrl) for the response
    # half so DB → JSON serialisation is a flat passthrough. ──
    audience_hints: Optional[str] = None
    primary_goal: Optional[str] = None
    success_criteria: Optional[str] = None
    webhook_url: Optional[str] = None
    # 026: per-campaign re-contact policy.
    allow_recontact: bool = False
    recontact_min_age_days: int = 30
    # 029: auto-pause visibility. pause_reason is NULL for a manual / never-paused
    # campaign; 'no_senders_attached' | 'senders_unavailable' when the worker
    # auto-paused it because it could no longer send.
    pause_reason: Optional[str] = None
    paused_at: Optional[datetime] = None
    attached_senders: List[CampaignSenderAttach] = Field(default_factory=list)
    is_exhausted: bool = False
    created_at: datetime
    updated_at: datetime


class CampaignListResponse(BaseModel):
    items: List[CampaignResponse]
    total: int


# === Phase 5: Inbox & Analytics (INBX-01..05, AIRC-04) ========================

CONVERSATION_STATUSES = {
    "active",
    "manual",
    "paused",
    "lead",
    "handoff",
    "finished",
    "bot_ignored",
}


class ConversationResponse(BaseModel):
    """GET /api/v1/conversations[/{id}] response row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    sender_id: UUID
    sender_slug: Optional[str] = None  # filled via JOIN in router
    contact_phone: str
    contact_name: Optional[str] = None
    contact_telegram_id: Optional[int] = None
    ai_enabled: bool
    ai_context_id: Optional[UUID] = None
    campaign_id: Optional[UUID] = None
    status: str  # one of CONVERSATION_STATUSES
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    conversations: List[ConversationResponse]
    total: int


class ConversationUpdate(BaseModel):
    """PATCH /api/v1/conversations/{id} body — partial PATCH."""

    ai_enabled: Optional[bool] = None
    ai_context_id: Optional[UUID] = None
    status: Optional[str] = None

    @model_validator(mode="after")
    def _validate_status(self) -> "ConversationUpdate":
        if self.status is not None and self.status not in CONVERSATION_STATUSES:
            raise ValueError(
                f"Invalid status '{self.status}'. "
                f"Must be one of: {sorted(CONVERSATION_STATUSES)}"
            )
        return self


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    direction: str
    message_text: str
    sent_by: str
    telegram_message_id: Optional[int] = None
    created_at: datetime


class MessageListResponse(BaseModel):
    messages: List[MessageResponse]
    total: int


class SendMessageFromUIRequest(BaseModel):
    """POST /api/v1/conversations/{id}/send body (D-04 auto-takeover).

    2026-05-26: Accepts both ``message`` (canonical, per openapi spec) and
    ``message_text`` (what Lovable's generated client sends). Pydantic's
    ``AliasChoices`` lets us read either name without forcing the frontend
    to ship a hotfix. Outgoing serialization still uses canonical ``message``.
    """

    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        validation_alias=AliasChoices("message", "message_text"),
    )


class SendMessageFromUIResponse(BaseModel):
    """POST /api/v1/conversations/{id}/send response (D-04 auto-takeover)."""

    success: bool
    message_id: Optional[UUID] = None
    telegram_message_id: Optional[int] = None
    error: Optional[str] = None


# === Phase 5 analytics schemas (ANLX-01..04) =================================


class AnalyticsReplied(BaseModel):
    """Per D-15: «Отвечено» = две цифры — conversation_count + message_count.

    conversation_count = COUNT(DISTINCT m.conversation_id) на JOIN messages+conversations
                         WHERE m.direction='inbound' AND m.sent_by='contact'.
    message_count      = COUNT(*)                          (same JOIN/WHERE).
    """

    conversation_count: int
    message_count: int


class AnalyticsCards(BaseModel):
    """Per D-16: identical schema across all 4 levels (workspace / campaign / agent / sender).

    Все 4 analytics endpoints (workspace, campaigns/{id}, agents/{id}, senders/{id})
    возвращают эту схему — UI Lovable рендерит одну и ту же сетку из 4 карточек.

    Pitfall 9: leads и finishes — mutually exclusive (status='lead' НЕ включает
    status='finished'). UI label для leads: «Активные лиды (ещё не финишировали)».
    Все counts исключают status='bot_ignored' (Pitfall 8).
    """

    sent: int
    replied: AnalyticsReplied
    leads: int
    finishes: int


# === Phase 5 LLM call schemas (ANLX-05) ======================================


class LLMCallResponse(BaseModel):
    """Per-conversation LLM audit row for inbox-debug UI.

    Mirrors the 15-column llm_calls table (D-09). prompt is the full request
    dict (messages + tools + temperature + model). tool_calls is the parsed
    list of {id, name, arguments} (None when LLM returned plain text).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    campaign_id: Optional[UUID] = None
    agent_id: Optional[UUID] = None
    sender_id: Optional[UUID] = None
    model: str
    prompt: dict
    response_text: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime


class LLMCallListResponse(BaseModel):
    """Paginated wrapper for GET /api/v1/conversations/{id}/llm-calls."""

    llm_calls: list[LLMCallResponse]
    total: int


# === Phase 05.1: Telemetry ingest + Core Value KPI (UI-TEL-01) ================


class TelemetryEventIn(BaseModel):
    """POST /api/v1/telemetry/events body (UI-SPEC §9).

    event_id is client-supplied for navigator.sendBeacon idempotency
    (retry on flaky networks → same event lands once). Default factory
    generates a fresh UUIDv4 when the client omits it.
    """

    event_id: Optional[UUID] = Field(default=None)
    event: constr(min_length=1, max_length=80)
    props: dict = Field(default_factory=dict)
    client_timestamp: Optional[datetime] = None


class CoreValueResponse(BaseModel):
    """GET /api/v1/telemetry/core-value response (UI-SPEC §9 KPI #9).

    All three fields may be None for workspaces that have not yet signed up or
    launched a campaign; UI renders them as "—" in that case.
    """

    time_to_first_campaign_seconds: Optional[int] = None
    signup_at: Optional[datetime] = None
    first_launch_at: Optional[datetime] = None


# === Phase 05.1: Analytics — Funnel (UI-DASH-01) + LLM aggregates (UI-CAMPD-01)

class FunnelResponse(BaseModel):
    """UI-SPEC §5.3 dashboard Sankey funnel — 5 stage counts.

    Stages are monotonically non-increasing under typical seeded data:
        sent >= replied >= engaged >= lead >= handoff
    (the API does not enforce monotonicity — pathological data such as manual
    human-sends without prior outbound, or stale leads/handoffs from other
    funnel branches, can break the chain. UI renders the Sankey as-is.)

    'engaged' definition is locked per RESEARCH Pitfall 5:
        COUNT(DISTINCT conversation_id) where >= 2 inbound contact messages AND
        status NOT IN ('lead','handoff','finished','bot_ignored').
    """

    sent: int
    replied: int
    engaged: int
    lead: int
    handoff: int


class LLMAggregatesResponse(BaseModel):
    """UI-SPEC §5.6 LLM trace tab top-of-tab metrics.

    Aggregates over the since-window (1d/7d/30d/90d). avg_latency_ms is None
    when no llm_calls rows match the filter (empty window).

    spend_usd_cents is 0 in v1 (per-model pricing deferred to v2 — RESEARCH
    §"Backend Gap Map"); the field is reserved in the response shape so the UI
    can render a placeholder without breaking the schema in v2.
    """

    total_calls: int
    avg_latency_ms: Optional[int] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    spend_usd_cents: int  # v1 stub: always 0; pricing-per-model added in v2
