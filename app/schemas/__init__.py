from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, HttpUrl, computed_field, conint, conlist, constr, model_validator
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
    # PFH-03: override for flipping an in-running-campaign sender to role='checker'
    # (see update_sender guard). Default False → no behaviour change for existing PATCHes.
    force: bool = False


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
    # Checker-specific UI status (role='checker' only; None for senders). Splits the
    # generic derived `status` into action-vs-auto buckets so the UI can show a
    # self-healing throttle ('cooling_down', amber, no action) separately from a
    # problem that needs the user ('reauth_needed' / 'banned', red).
    checker_status: Optional[
        Literal["active", "cooling_down", "frozen", "paused", "reauth_needed", "banned"]
    ] = None
    # Consecutive contacts-API trip counter (migration 036) — drives the escalating
    # cooldown; surfaced so the UI can hint "attempt N, longer rest".
    checker_trip_count: int = 0
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
    # Phase 20 (PROF-01/07/D-08): cached profile surfaced for the enriched row + edit form.
    tg_username: Optional[str] = None
    tg_bio: Optional[str] = None
    # Telegram Premium flag (mig 052) — surfaced as a badge on the account card.
    tg_premium: bool = False
    has_photo: bool = False   # list carries only this bool; photo bytes served via GET /senders/{slug}/photo (D-11)
    # Per-field last-change timestamps (iso8601 strings) so the UI can compute the D-08 1h countdown client-side.
    profile_field_changed_at: dict = {}


class SenderCreateResponse(BaseModel):
    """Возврат create/update sender'а с warnings[] (D-14)."""
    sender: SenderResponse
    warnings: List[WarningItem] = []


# === Account Profile Management (Phase 20 — PROF-01..08 + D-08/D-09) ===

class ProfileUpdate(BaseModel):
    """PATCH /senders/{slug}/profile — Section A identity. Only non-None fields are written.
    username="" clears the username; username=None leaves it untouched (D-07/D-08)."""
    first_name: Optional[str] = Field(None, max_length=64)
    last_name: Optional[str] = Field(None, max_length=64)
    # No field-level max_length: the endpoint enforces the 70-char cap and returns a
    # structured 400 BIO_TOO_LONG (RED-test contract) rather than a 422 validation error.
    # AboutTooLongError from Telegram is the premium (140-char) backstop.
    about: Optional[str] = Field(None)
    username: Optional[str] = Field(None, max_length=32)


class ProfileWarningItem(BaseModel):
    """D-09 advisory for profile edits. DISTINCT from the PRE-EXISTING rate-limit
    WarningItem (field/value/recommended_max, D-14) — that schema is
    shaped for numeric soft-caps and MUST NOT be reused or modified here."""
    code: str
    message: str
    severity: Literal["warning"] = "warning"


class ProfileUpdateResponse(BaseModel):
    """Response for PATCH /senders/{slug}/profile and POST/DELETE /senders/{slug}/photo:
    sender + D-09 advisory warnings (ProfileWarningItem, NOT the rate-limit WarningItem)."""
    sender: SenderResponse
    warnings: List[ProfileWarningItem] = []


class UsernameCheckResponse(BaseModel):
    """GET /senders/{slug}/username-check?username= (C5)."""
    available: bool
    reason: Optional[str] = None   # 'taken' | 'invalid' | None


class TwoFAPasswordUpdate(BaseModel):
    """POST /senders/{slug}/2fa — password set/change (D-03/D-04). Password never persisted."""
    current_password: Optional[str] = None   # required only if 2FA already set (D-04)
    new_password: str = Field(..., min_length=1)
    hint: Optional[str] = Field(None, max_length=100)


class RecoveryEmailStart(BaseModel):
    """POST /senders/{slug}/2fa/recovery-email — step 1 (D-02/D-04)."""
    current_password: Optional[str] = None
    email: EmailStr


class RecoveryEmailConfirm(BaseModel):
    """POST /senders/{slug}/2fa/recovery-email/confirm — step 2 (code only, no SRP)."""
    code: str = Field(..., min_length=1)


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


class FolderStatsResponse(BaseModel):
    """Per-folder Telegram-status breakdown.

    Computed server-side via a single GROUP BY so the /contacts stat cards render
    correct folder-wide numbers immediately, instead of the frontend deriving them
    from the first paginated page of contacts.

    Buckets mirror the frontend classifiers (contacts.tsx):
      in_telegram  ← tg_status in (registered, ok, found, in_telegram)
      checking     ← tg_status in (pending, checking, unknown, unchecked, '')
      not_found    ← tg_status in (not_registered, not_found, privacy, missing, error)
    """
    total: int = 0
    in_telegram: int = 0
    checking: int = 0
    not_found: int = 0


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


class QAPair(BaseModel):
    """Single Q&A entry in agent.qa_pairs (UI-SPEC §5.8 FAQ tab)."""
    q: constr(min_length=1, max_length=2000)
    a: constr(min_length=1, max_length=4000)


# ─── Phase 11: DialogueStage (D-04 / FLD-04) ─────────────────────────────────


class DialogueStage(BaseModel):
    """One stage in the campaign's dialogue_flow sequence.

    title is optional (label for the UI); instruction is the stage directive
    injected into the prompt. Validated: title≤120, instruction 1..2000 chars.
    Security: conlist max_length=7 on the containing field guards array-size abuse (T2).
    """
    title: Optional[constr(max_length=120)] = None
    instruction: constr(min_length=1, max_length=2000)


class AgentCreate(BaseModel):
    """POST /api/v1/agents body (D-02 + Phase 11 field split)."""
    name: constr(min_length=1, max_length=100)
    # Legacy fields (Phase 3) — kept Optional for back-compat:
    system_prompt: Optional[str] = None
    rules: Optional[str] = None
    faq: List[FaqItem] = Field(default_factory=list)
    company_info: Optional[str] = None
    product_info: Optional[str] = None
    # 05.1 v2 fields (UI-SPEC §5.8):
    who_is_agent: Optional[str] = None
    company_knowledge: Optional[str] = None
    knowledge_base: Optional[str] = None
    # Phase 11 D-01/D-11: single-source tone and response speed (replaces voice_baseline/tone/tone_of_voice).
    tone_preset: Optional[Literal["Friendly", "Professional", "Direct", "Casual"]] = None
    response_speed: Optional[Literal["instant", "human", "slow", "manual"]] = None
    # T3: delay bounded 0..3600s so manual delay cannot DoS the queue worker.
    response_delay_seconds: Optional[conint(ge=0, le=3600)] = None
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
    # None = leave unchanged; [] = clear FAQ; [...] = full replace (Pitfall 7)
    faq: Optional[List[FaqItem]] = None
    company_info: Optional[str] = None
    product_info: Optional[str] = None
    # 05.1 v2 fields (UI-SPEC §5.8) — all Optional, same semantics as AgentCreate:
    who_is_agent: Optional[str] = None
    company_knowledge: Optional[str] = None
    knowledge_base: Optional[str] = None
    # Phase 11 D-01/D-11: single-source tone and response speed.
    tone_preset: Optional[Literal["Friendly", "Professional", "Direct", "Casual"]] = None
    response_speed: Optional[Literal["instant", "human", "slow", "manual"]] = None
    response_delay_seconds: Optional[conint(ge=0, le=3600)] = None
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
    faq: List[FaqItem] = []
    company_info: Optional[str] = None
    product_info: Optional[str] = None
    # 05.1 v2 fields (UI-SPEC §5.8) — serialised straight from JSONB/TEXT[]:
    who_is_agent: Optional[str] = None
    company_knowledge: Optional[str] = None
    knowledge_base: Optional[str] = None
    # Phase 11 D-01/D-11: single-source tone fields (voice_baseline/tone/tone_of_voice removed).
    tone_preset: Optional[str] = None
    response_speed: Optional[str] = None
    response_delay_seconds: Optional[int] = None
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


class PoolHealth(BaseModel):
    """POOLV-01 (D-08): numeric pool-health aggregate for a campaign's sender pool.

    Presentation-free — the green/yellow/red badge is derived ON THE FRONTEND
    (paused==0→green; 0<paused<total→yellow; paused==total && total>0→red).
    earliest_resume_at = MIN(restricted_until) among restricted senders (OQ#4
    recheck horizon); None when no sender is restricted.

    has_backup (quick-260706-c1p, SOFT advisory): True iff the pool has >=2
    truly-sendable (active) senders, i.e. a single freeze still leaves a backup
    that can carry the campaign. Purely advisory — the frontend renders a yellow
    "no backup sender — a single freeze stalls this campaign" nudge when False.
    NO blocking behaviour on attach/detach/start is derived from this field.
    """
    active: int
    paused: int
    total: int
    earliest_resume_at: Optional[datetime] = None
    has_backup: bool = False


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
    # Migration 028: write-restriction state, orthogonal to auth_status.
    restriction_status: Literal["none", "spam_limited", "frozen"] = "none"
    restricted_until: Optional[datetime] = None

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
    # PFH-02: attaching a role='checker' account (or any account with special
    # handling) requires an explicit override. Default False → backward-compatible
    # for every existing caller that posts only {sender_id}.
    force: bool = False


class SenderAttachWarning(BaseModel):
    """PFH-01/PFH-02: advisory (NON-blocking) warning surfaced by
    POST /campaigns/{id}/senders in CampaignResponse.attach_warnings[].

    code:
      RECENT_RESTRICTION      — sender had a (non-'cleared') restriction event in the
                                last 7 days ("зелёный коридор"); attaching may
                                re-trigger anti-spam.
      CHECKER_FORCE_ATTACHED  — a role='checker' account was force-attached as a
                                campaign sender (force=true); it will leave the
                                contact-check pool once it sends.

    Returned ONLY by attach_sender; every other endpoint leaves attach_warnings
    defaulting to [] (backward-compatible).
    """
    code: str
    sender_id: UUID
    message: str
    event_type: Optional[str] = None
    restricted_until: Optional[datetime] = None
    last_event_at: Optional[datetime] = None


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
    # NB: success_criteria removed (Phase 11 D-13) — merged into lead_trigger_hint.
    # Unified webhook URL — supersedes per-tool webhook_url + 3 legacy signal URLs
    # for new campaigns. Legacy URLs remain Optional above for Phase 4 back-compat.
    webhook_url: Optional[HttpUrl] = None
    # 026: per-campaign re-contact policy. Default false → strict cross-campaign
    # dedup (any existing conversation blocks). When true, only live & fresh
    # dialogs block; closed/stale ones are re-contactable.
    allow_recontact: bool = False
    recontact_min_age_days: int = Field(default=30, ge=1, le=365)
    # ── Phase 12 NDLG-03/NDLG-04 (D-12/D-13/D-14). ──
    max_new_dialogs_per_day: int = Field(
        default=10, ge=1, le=30,
        description="Daily new-dialog cap per sender within this campaign (D-12). "
                    "Green corridor <=10; soft-warn >10; hard cap 30.",
    )
    # ── Phase 19 NORP-02/NORP-05 follow-up + auto-finish (D-08/D-12). ──
    # Toggle defaults OFF; bounds enforced at the API layer (Pydantic), no DB CHECK.
    follow_up_enabled: bool = False
    follow_up_interval_hours: int = Field(default=24, ge=4, le=168)
    follow_up_max_pings: int = Field(default=2, ge=1, le=5)
    auto_finish_hours: int = Field(default=72, ge=24, le=720)
    # ── Phase 24 (D-13): invisible anti-spam text-variation toggle, default ON. ──
    variation_enabled: bool = True
    # ── Phase 11 campaign fields (D-04/D-12/D-14). ──
    # dialogue_flow: ordered conversation stages (max 7 — T2 size guard).
    dialogue_flow: Optional[conlist(DialogueStage, max_length=7)] = None
    arguments_facts: Optional[str] = None
    campaign_rules: Optional[str] = None
    # ── Prompt template v2 (migration 037): preset-driven core_directive. ──
    # Resolve to preset lines in ai_engine. NULL → engine defaults
    # (disclosure→reveal_nothing, authority→handoff_only, objective→primary_goal).
    objective_preset: Optional[
        Literal["book_call", "book_demo", "collect_contact", "qualify",
                "direct_sale", "support", "custom"]
    ] = None
    disclosure_preset: Optional[
        Literal["reveal_nothing", "list_price_ok", "quote_from_pricelist",
                "full_disclosure"]
    ] = None
    authority_preset: Optional[
        Literal["handoff_only", "can_schedule", "can_send_materials", "can_offer"]
    ] = None
    # Optional per-campaign few-shot override; NULL → static both-language fallback.
    style_examples: Optional[str] = None

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
    # NB: success_criteria removed (Phase 11 D-13) — merged into lead_trigger_hint.
    webhook_url: Optional[HttpUrl] = None
    # 026: per-campaign re-contact policy (partial PATCH).
    allow_recontact: Optional[bool] = None
    recontact_min_age_days: Optional[int] = Field(default=None, ge=1, le=365)
    # ── Phase 12 NDLG-03/NDLG-04 (D-12/D-13/D-14) — partial PATCH. ──
    max_new_dialogs_per_day: Optional[int] = Field(default=None, ge=1, le=30)
    # ── Phase 19 NORP-02/NORP-05 follow-up + auto-finish (D-08/D-12) — partial PATCH. ──
    follow_up_enabled: Optional[bool] = None
    follow_up_interval_hours: Optional[int] = Field(default=None, ge=4, le=168)
    follow_up_max_pings: Optional[int] = Field(default=None, ge=1, le=5)
    auto_finish_hours: Optional[int] = Field(default=None, ge=24, le=720)
    # ── Phase 24 (D-13): variation toggle — partial PATCH. ──
    variation_enabled: Optional[bool] = None
    # ── Phase 11 campaign fields (D-04/D-12/D-14) — partial PATCH. ──
    dialogue_flow: Optional[conlist(DialogueStage, max_length=7)] = None
    arguments_facts: Optional[str] = None
    campaign_rules: Optional[str] = None
    # ── Prompt template v2 (migration 037) — partial PATCH. ──
    objective_preset: Optional[
        Literal["book_call", "book_demo", "collect_contact", "qualify",
                "direct_sale", "support", "custom"]
    ] = None
    disclosure_preset: Optional[
        Literal["reveal_nothing", "list_price_ok", "quote_from_pricelist",
                "full_disclosure"]
    ] = None
    authority_preset: Optional[
        Literal["handoff_only", "can_schedule", "can_send_materials", "can_offer"]
    ] = None
    style_examples: Optional[str] = None


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
    # NB: success_criteria removed (Phase 11 D-13) — merged into lead_trigger_hint.
    webhook_url: Optional[str] = None
    # 026: per-campaign re-contact policy.
    allow_recontact: bool = False
    recontact_min_age_days: int = 30
    # ── Phase 12 NDLG-03/NDLG-04 (D-12/D-13/D-14). ──
    max_new_dialogs_per_day: int = 10
    # ── Phase 19 NORP-02/NORP-05 follow-up + auto-finish (D-08/D-12). ──
    follow_up_enabled: bool = False
    follow_up_interval_hours: int = 24
    follow_up_max_pings: int = 2
    auto_finish_hours: int = 72
    # ── Phase 24 (D-13/D-19): variation toggle + computed attachment presence. ──
    # variation_enabled is a real column; has_attachment is computed by the router
    # (EXISTS on campaign_attachments), NOT a campaigns column — keeps the blob off
    # every SELECT campaigns (Pitfall 7).
    variation_enabled: bool = True
    has_attachment: bool = False
    # ── Phase 11 campaign fields (D-04/D-12/D-14). ──
    dialogue_flow: List[dict] = Field(default_factory=list)
    arguments_facts: Optional[str] = None
    campaign_rules: Optional[str] = None
    # ── Prompt template v2 (migration 037). Plain str passthrough on the response. ──
    objective_preset: Optional[str] = None
    disclosure_preset: Optional[str] = None
    authority_preset: Optional[str] = None
    style_examples: Optional[str] = None
    # 029: auto-pause visibility. pause_reason is NULL for a manual / never-paused
    # campaign; 'no_senders_attached' | 'senders_unavailable' when the worker
    # auto-paused it because it could no longer send.
    pause_reason: Optional[str] = None
    paused_at: Optional[datetime] = None
    attached_senders: List[CampaignSenderAttach] = Field(default_factory=list)
    # PFH-01/PFH-02: advisory pre-flight warnings, populated ONLY by attach_sender.
    # Optional (default empty) → backward-compatible for get/patch/start/pause/detach.
    attach_warnings: List[SenderAttachWarning] = Field(default_factory=list)
    is_exhausted: bool = False
    # WR-12b: number of status='failed' queue rows for this campaign. Computed at
    # read time (COUNT(*), not a stored column) so the UI can surface a "retry
    # failed" affordance backed by POST /campaigns/{id}/requeue-failed.
    failed_count: int = 0
    # POOLV-01: numeric pool-health aggregate (active/paused/total + earliest
    # resume horizon). Computed in one pass in _campaign_to_response; badge color
    # derived on the frontend (presentation-free API).
    pool_health: PoolHealth
    created_at: datetime
    updated_at: datetime


class RestrictionEventResponse(BaseModel):
    """HLTH-03: one restriction-event row from sender_restriction_events.

    Read-only response for GET /senders/{slug}/restriction-events. ORM read via
    from_attributes; mirrors the migration-030 columns.
    """
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    source: str
    category: str
    restricted_until: Optional[datetime] = None
    raw_text: Optional[str] = None
    activity_slice: Optional[dict] = None
    proxy: Optional[dict] = None
    created_at: datetime


class SenderBlockRateResponse(BaseModel):
    """SRLD-08 (D-15/D-16): read-only per-sender block-rate aggregate.

    Response for GET /senders/{slug}/block-rate — counts durable 'blocked'
    restriction events vs 'sent' messages over a trailing 7-day window.
    Strictly read-only: no control-loop, no auto-pause (D-16). block_rate is
    blocks_7d / sends_7d (0.0 when no sends).
    """
    blocks_7d: int
    sends_7d: int
    block_rate: float


class CampaignWriteResponse(BaseModel):
    """Phase 12 D-14: campaign create/update response carrying soft-cap warnings[].
    GET paths keep returning CampaignResponse directly (no warnings)."""
    campaign: CampaignResponse
    warnings: List[WarningItem] = []


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
    "telegram_service",
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


class DeleteConversationsBatchRequest(BaseModel):
    """POST /api/v1/conversations/delete body — bulk hard delete.

    Зеркало DeleteContactBatchRequest: cross-tenant ids молча пропускаются
    воркером (не светим существование чужих бесед через 404).
    """

    conversation_ids: List[UUID] = Field(..., min_length=1)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    direction: str
    message_text: Optional[str] = None      # nullable for file bubbles (D-20)
    sent_by: str
    telegram_message_id: Optional[int] = None
    # Phase 23 media/edit fields — all optional/defaulted so the current
    # GET /messages SELECT (which does not yet return them) still constructs the
    # model; plan 23-03 widens the SELECT.
    message_type: str = "text"              # text|photo|video|voice|document
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    edited_at: Optional[datetime] = None    # (изменено) marker (D-07)
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


class EditMessageRequest(BaseModel):
    """PATCH /conversations/{id}/messages/{message_id} body (D-06/D-07).

    Tolerates the Lovable field aliases exactly like SendMessageFromUIRequest
    (D-22): accepts ``message`` (canonical), ``message_text`` or ``text``.
    """

    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        validation_alias=AliasChoices("message", "message_text", "text"),
    )


class SendFileFromUIResponse(BaseModel):
    """POST /conversations/{id}/send-file response (D-12).

    Mirrors SendMessageFromUIResponse. The send-file endpoint uses multipart
    Form/File params, so caption + file arrive as form fields — no request
    BODY model is needed.
    """

    success: bool
    message_id: Optional[UUID] = None
    telegram_message_id: Optional[int] = None
    message_type: Optional[str] = None
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

    Progress fields (campaign scope only): прогресс кампании = доля reachable
    контактов, до которых уже дотянулись.
    - ``contacts_messaged`` = COUNT(DISTINCT conversation_id) среди outbound —
      сколько РАЗНЫХ контактов получили хотя бы одно сообщение (НЕ raw message
      count, иначе несколько сообщений одному контакту раздувают числитель).
    - ``registered_contacts`` = COUNT(contacts WHERE folder_id=campaign.folder_id
      AND tg_status='registered') — знаменатель «достижимые контакты в папке»
      (та же семантика, что _compute_is_exhausted, migration 013).
    Для workspace/agent/sender scope оба поля = 0 (нет одной целевой папки),
    UI не рисует для них progress-бар.

    ``llm_spend_usd_cents`` = all-time LLM spend в центах (USD) для текущего
    scope (workspace/campaign/agent/sender — фильтр по соответствующей колонке
    llm_calls). All-time (без since-окна), как и остальные карточки (D-14).
    Additive optional-with-default поле — существующие consumer'ы фронта не
    ломаются; фронт делит на 100 для отображения в USD.
    """

    sent: int
    replied: AnalyticsReplied
    leads: int
    finishes: int
    contacts_messaged: int = 0
    registered_contacts: int = 0
    llm_spend_usd_cents: int = 0


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
