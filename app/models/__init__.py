from sqlalchemy import Column, String, Text, Boolean, BigInteger, DateTime, Integer, LargeBinary, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func, text
from app.database import Base
import uuid
import enum


class MessageType(enum.Enum):
    sent = "sent"
    draft = "draft"
    failed = "failed"


class QueueItemStatus(enum.Enum):
    pending = "pending"
    processing = "processing"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"


class QueueItemType(enum.Enum):
    message = "message"
    file = "file"


# ─── Multi-tenant foundation (Phase 1 — TENT-01..04) ─────────────────────────

class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserWorkspace(Base):
    __tablename__ = "user_workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supabase_user_id = Column(Text, nullable=False, index=True)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    role = Column(String(20), nullable=False, server_default="owner")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    workspace = relationship("Workspace")


class WorkspaceApiKey(Base):
    __tablename__ = "workspace_api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    prefix = Column(String(12), nullable=False)
    bcrypt_hash = Column(Text, nullable=False)
    name = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    workspace = relationship("Workspace")


# ─── Tenant-scoped models (workspace_id added per Phase 1) ───────────────────

class Sender(Base):
    __tablename__ = "senders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    # Phase 02.1 (WR-02): slug per-workspace UNIQUE via idx_senders_workspace_slug
    # in migration 014. Globally unique constraint removed.
    slug = Column(String(50), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False)
    session_string = Column(Text, nullable=False)  # Encrypted
    role = Column(String(20), nullable=False, server_default='sender')  # 'sender' or 'checker' (CHECK in migration 013)
    proxy = Column(JSONB, nullable=True)  # {"type": "socks5", "host": "...", "port": 1080, ...}
    auth_status = Column(String(30), nullable=False, server_default='ok')  # ok, session_expired, session_revoked, deactivated, banned
    # Phase 2 (D-11/D-13): lifecycle_status replaces is_active; rate limits live per-sender.
    lifecycle_status = Column(String(20), nullable=False, server_default='active')  # 'active' | 'warmup' | 'paused'
    rate_per_min = Column(Integer, nullable=False, server_default='4')
    rate_per_hour = Column(Integer, nullable=False, server_default='20')
    rate_per_day = Column(Integer, nullable=False, server_default='150')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), onupdate=func.now())
    # NB: ai_context_id dropped (Phase 3 D-04). Sender больше не «знает» агента —
    # связь идёт через Campaign в Phase 4.

    # Relationships
    messages = relationship("MessageLog", back_populates="sender")
    contacts = relationship("ContactCache", back_populates="sender")


class MessageLog(Base):
    __tablename__ = "messages_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id"), nullable=False)
    # VARCHAR(40): holds either a phone (+7…) or the '@username' identity key (migration 025).
    recipient_phone = Column(String(40), nullable=False)
    recipient_name = Column(String(100))
    recipient_telegram_id = Column(BigInteger)
    message_text = Column(Text, nullable=False)
    message_type = Column(SQLEnum(MessageType), nullable=False)
    error_message = Column(Text)
    extra_data = Column(JSONB, default={})  # Renamed from metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    sender = relationship("Sender", back_populates="messages")


class ContactCache(Base):
    __tablename__ = "contacts_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id"), nullable=False)
    # VARCHAR(40): phone (+7…) or '@username' identity key (migration 025).
    phone = Column(String(40), nullable=False, index=True)
    telegram_id = Column(BigInteger)
    access_hash = Column(BigInteger)  # required for InputPeerUser when sending
    first_name = Column(String(100))
    last_name = Column(String(100))
    username = Column(String(50))
    is_registered = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    sender = relationship("Sender", back_populates="contacts")


class AIContext(Base):
    __tablename__ = "ai_contexts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    name = Column(String(100), nullable=False)
    system_prompt = Column(Text, nullable=True)
    tone_of_voice = Column(Text, nullable=True)
    rules = Column(Text, nullable=True)
    company_info = Column(Text, nullable=True)
    product_info = Column(Text, nullable=True)
    faq = Column(JSONB, default={})
    # ── 05.1 v2 columns (UI-SPEC §5.8 — additive, nullable; legacy fields kept). ──
    # auto_pause_triggers was dropped in migration 015 (Phase 3) and resurrected
    # in migration 018 — see SUMMARY for the reality check.
    who_is_agent = Column(Text, nullable=True)
    company_knowledge = Column(Text, nullable=True)
    knowledge_base = Column(Text, nullable=True)
    voice_baseline = Column(String(20), nullable=True)
    # tone JSONB default {"formal":0,"warm":0,"brief":0} — server_default lives in migration.
    tone = Column(JSONB, nullable=True)
    max_message_length = Column(Integer, nullable=True, server_default="280")
    mirror_language = Column(Boolean, nullable=True, server_default="true")
    allow_emoji = Column(Boolean, nullable=True, server_default="false")
    banlist = Column(ARRAY(Text), nullable=True)
    qa_pairs = Column(JSONB, nullable=True)
    auto_pause_triggers = Column(ARRAY(Text), nullable=True)
    auto_pause_scope = Column(String(20), nullable=True, server_default="conversation")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # NB: response_delay_seconds, is_active dropped (Phase 3 D-01).
    # max_message_length re-added в Phase 05.1 (UI-SPEC §5.8 length cap setting).
    # auto_pause_triggers re-added в Phase 05.1 (UI-SPEC §5.8 banned triggers).
    # NB: senders relationship dropped (Phase 3 D-04) — связь sender↔agent больше не через FK.


class MessageQueue(Base):
    """Queue for rate-limited outbound messages. Worker processes one item per ~12 sec per sender."""
    __tablename__ = "message_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"), nullable=False)
    item_type = Column(SQLEnum(QueueItemType), nullable=False, default=QueueItemType.message)
    status = Column(SQLEnum(QueueItemStatus), nullable=False, default=QueueItemStatus.pending, index=True)

    # Recipient
    # VARCHAR(40): phone (+7…) or '@username' identity key (migration 025).
    recipient_phone = Column(String(40), nullable=False)
    recipient_name = Column(String(100))

    # Payload — for messages
    message_text = Column(Text)
    as_draft = Column(Boolean, default=False)

    # Payload — for files
    file_url = Column(Text)
    file_name = Column(String(255))
    caption = Column(Text)

    # Extra metadata from caller
    extra_data = Column(JSONB, default={})

    # Webhook callback — called by worker after send (success or failure)
    callback_url = Column(Text, nullable=True)

    # Queue management
    priority = Column(Integer, default=0)  # higher = processed first within same sender
    scheduled_at = Column(DateTime(timezone=True), server_default=func.now())  # not before this time
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))

    # Result
    result_message_id = Column(String(50))
    result_recipient_telegram_id = Column(BigInteger)
    result_recipient_name = Column(String(100))
    result_recipient_username = Column(String(50))
    error_message = Column(Text)
    attempts = Column(Integer, default=0)

    # Phase 4 (D-16 + AUDIT Q1 override): campaign_id NULLable + ON DELETE SET NULL.
    # NULL means legacy/orphaned queue item after campaign hard-delete (D-07).
    campaign_id = Column(UUID(as_uuid=True),
                         ForeignKey("campaigns.id", ondelete="SET NULL"),
                         nullable=True)

    # Relationships
    sender = relationship("Sender")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"), nullable=False)
    # VARCHAR(40): phone (+7…) or '@username' identity key (migration 025).
    contact_phone = Column(String(40), nullable=False)
    contact_name = Column(String(100), nullable=True)
    contact_telegram_id = Column(BigInteger, nullable=True)
    ai_enabled = Column(Boolean, default=True, server_default='true')
    ai_context_id = Column(UUID(as_uuid=True), ForeignKey("ai_contexts.id", ondelete="SET NULL"), nullable=True)
    # Phase 4 D-05: NULLable FK + extended CHECK ('active','manual','paused','lead','handoff','finished').
    campaign_id = Column(UUID(as_uuid=True),
                         ForeignKey("campaigns.id", ondelete="SET NULL"),
                         nullable=True)
    status = Column(String(20), default="active", server_default="active")  # active|manual|paused|lead|handoff|finished
    paused_at = Column(DateTime(timezone=True), nullable=True)
    paused_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    sender = relationship("Sender")
    ai_context = relationship("AIContext")


# ─── Warmup ───────────────────────────────────────────────────────────────────

class WarmupPool(Base):
    """Аккаунты, участвующие в прогреве."""
    __tablename__ = "warmup_pool"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id   = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"),
                         nullable=False, unique=True)
    is_active   = Column(Boolean, nullable=False, default=True, server_default='true')
    enrolled_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    sender = relationship("Sender")


class WarmupSession(Base):
    """Сессия диалога между двумя аккаунтами в пуле прогрева."""
    __tablename__ = "warmup_sessions"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id    = Column(UUID(as_uuid=True),
                             ForeignKey("workspaces.id", ondelete="CASCADE"),
                             nullable=False)
    sender_a_id     = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"),
                             nullable=False)
    sender_b_id     = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"),
                             nullable=False)
    topic           = Column(Text, nullable=False)
    status          = Column(String(20), nullable=False, default="active")  # active, completed
    messages_sent   = Column(Integer, nullable=False, default=0)
    target_messages = Column(Integer, nullable=False, default=6)
    next_message_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_sender_id  = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="SET NULL"),
                             nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(),
                             onupdate=func.now(), nullable=False)

    sender_a = relationship("Sender", foreign_keys=[sender_a_id])
    sender_b = relationship("Sender", foreign_keys=[sender_b_id])


class WarmupMessage(Base):
    """Одно сообщение в рамках warmup-сессии."""
    __tablename__ = "warmup_messages"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id   = Column(UUID(as_uuid=True),
                            ForeignKey("workspaces.id", ondelete="CASCADE"),
                            nullable=False)
    session_id     = Column(UUID(as_uuid=True), ForeignKey("warmup_sessions.id", ondelete="CASCADE"),
                            nullable=False)
    from_sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"),
                            nullable=False)
    to_sender_id   = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"),
                            nullable=False)
    message_text   = Column(Text, nullable=False)
    sent_at        = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session = relationship("WarmupSession")


# ─── Rotation ─────────────────────────────────────────────────────────────────

class ProxyPool(Base):
    """Pool of static residential proxies (Decodo ISP).
    Each row = one proxy endpoint (host + port = unique static IP).
    assigned_to_sender_id=NULL means the proxy is free."""
    __tablename__ = "proxy_pool"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id          = Column(UUID(as_uuid=True),
                                   ForeignKey("workspaces.id", ondelete="CASCADE"),
                                   nullable=False)
    host                  = Column(String(255), nullable=False)
    port                  = Column(Integer, nullable=False)
    username              = Column(String(100), nullable=False)
    password              = Column(String(100), nullable=True)
    assigned_to_sender_id = Column(UUID(as_uuid=True),
                                   ForeignKey("senders.id", ondelete="SET NULL"),
                                   nullable=True)
    created_at            = Column(DateTime(timezone=True), server_default=func.now())

    sender = relationship("Sender")


# NB: ContextContactAssignment dropped in Phase 4 migration 016 (D-06).
# Rotation state переехало на campaign_contact_assignments (per-campaign).


# ─── Phase 2: Folders, Contacts, Onboarding sessions, CSV imports ─────────────

class Folder(Base):
    """Папка контактов внутри workspace (FLDR-01, D-05)."""
    __tablename__ = "folders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Contact(Base):
    """Workspace-level контакт (CONT-01..05, D-01).

    NB: НЕ ПУТАТЬ с ContactCache (per-sender Telegram-resolve cache из Phase 0):
    ContactCache хранит telegram_id + access_hash для отправки, Contact — это
    запись в адресной книге клиента, привязанная к папке.
    """
    __tablename__ = "contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    folder_id = Column(UUID(as_uuid=True),
                       ForeignKey("folders.id", ondelete="CASCADE"),
                       nullable=False)
    phone = Column(String(20), nullable=True)
    username = Column(String(50), nullable=True)
    full_name = Column(String(200), nullable=True)
    source = Column(String(100), nullable=True)
    custom = Column(JSONB, nullable=False, server_default='{}')
    # CHECK ('pending','registered','not_registered','error','unchecked') in migration 013
    tg_status = Column(String(20), nullable=False, server_default='pending')
    tg_telegram_id = Column(BigInteger, nullable=True)
    tg_username_resolved = Column(String(50), nullable=True)
    tg_error = Column(Text, nullable=True)
    tg_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    folder = relationship("Folder")


class OnboardingSession(Base):
    """Persistent state онбординга TG-аккаунта (D-16/D-17).

    Заменяет in-memory `_onboarding_sessions: dict` в старом onboarding.py.
    """
    __tablename__ = "onboarding_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    phone = Column(String(20), nullable=False)
    phone_code_hash = Column(Text, nullable=False)
    encrypted_session_string = Column(Text, nullable=False)
    # CHECK ('sender','checker') in migration 013
    role = Column(String(20), nullable=False, server_default='sender')
    proxy = Column(JSONB, nullable=True)
    # CHECK ('code_sent','awaiting_2fa','completed','failed') in migration 013
    status = Column(String(20), nullable=False)
    # Phase 02.1 (CR-05): reauth marker. NULL = обычный onboarding (INSERT new sender).
    # NOT NULL = reauth существующего sender'а (UPDATE session_string + auth_status).
    # Migration 014 добавляет колонку + partial index.
    original_sender_id = Column(UUID(as_uuid=True),
                                ForeignKey("senders.id", ondelete="CASCADE"),
                                nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CsvImport(Base):
    """CSV-импорт preview blob (D-07, C-02 Option B — DB-blob)."""
    __tablename__ = "csv_imports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    file_data = Column(LargeBinary, nullable=False)
    columns = Column(JSONB, nullable=False)
    suggested_mapping = Column(JSONB, nullable=False)
    encoding = Column(String(20), nullable=True)
    delimiter = Column(String(5), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)


# ─── Phase 4: Campaigns ───────────────────────────────────────────────────────

class Campaign(Base):
    """Outreach campaign — обёртка над рассылкой (D-01..D-15).

    Связи: agent_id (RESTRICT), folder_id (RESTRICT), senders через campaign_senders.
    Status enum: draft / running / paused / done (VARCHAR + CHECK per AUDIT Q6 — не SQLEnum,
    т.к. ALTER TYPE ADD VALUE нельзя в транзакции).
    """
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    # 024: nullable для незавершённого draft (agent/folder заполняются позже через PATCH;
    # обязательность проверяется только на POST /{id}/start). FK RESTRICT остаётся.
    agent_id = Column(UUID(as_uuid=True),
                      ForeignKey("ai_contexts.id", ondelete="RESTRICT"),
                      nullable=True)
    folder_id = Column(UUID(as_uuid=True),
                       ForeignKey("folders.id", ondelete="RESTRICT"),
                       nullable=True)
    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    # CHECK ('draft','running','paused','done') enforced in DB (CONSTRAINT campaigns_status_check)
    status = Column(String(20), nullable=False, server_default="draft")
    timezone = Column(Text, nullable=False, server_default="Europe/Moscow")
    work_hour_start = Column(Integer, nullable=False, server_default="9")
    work_hour_end = Column(Integer, nullable=False, server_default="20")
    work_days_mask = Column(Integer, nullable=False, server_default="31")  # Mo-Fri
    start_date = Column(DateTime(timezone=True), nullable=True)
    stop_date = Column(DateTime(timezone=True), nullable=True)
    message_template = Column(Text, nullable=False, server_default="")
    lead_webhook_url = Column(Text, nullable=True)
    handoff_webhook_url = Column(Text, nullable=True)
    finish_webhook_url = Column(Text, nullable=True)
    lead_trigger_hint = Column(Text, nullable=True)
    handoff_trigger_hint = Column(Text, nullable=True)
    finish_trigger_hint = Column(Text, nullable=True)
    tools = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # ── 05.1 v2 columns (UI-SPEC §5.5 step 2 + step 6 — additive, nullable;
    # legacy 3-webhook cols above kept for Phase 4 back-compat / Pitfall 6). ──
    audience_hints = Column(Text, nullable=True)
    primary_goal = Column(String(20), nullable=True)
    success_criteria = Column(Text, nullable=True)
    webhook_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    workspace = relationship("Workspace")
    agent = relationship("AIContext")
    folder = relationship("Folder")
    senders = relationship("CampaignSender", back_populates="campaign",
                           cascade="all, delete-orphan")


class CampaignSender(Base):
    """Through-table campaign ↔ sender (D-03). PK (campaign_id, sender_id)."""
    __tablename__ = "campaign_senders"

    campaign_id = Column(UUID(as_uuid=True),
                         ForeignKey("campaigns.id", ondelete="CASCADE"),
                         primary_key=True)
    sender_id = Column(UUID(as_uuid=True),
                       ForeignKey("senders.id", ondelete="CASCADE"),
                       primary_key=True)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    added_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    campaign = relationship("Campaign", back_populates="senders")
    sender = relationship("Sender")


class CampaignContactAssignment(Base):
    """Per-campaign rotation state (D-06). UNIQUE(campaign_id, contact_phone)."""
    __tablename__ = "campaign_contact_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    campaign_id = Column(UUID(as_uuid=True),
                         ForeignKey("campaigns.id", ondelete="CASCADE"),
                         nullable=False)
    # VARCHAR(40): phone (+7…) or '@username' identity key (migration 025).
    contact_phone = Column(String(40), nullable=False)
    sender_id = Column(UUID(as_uuid=True),
                       ForeignKey("senders.id", ondelete="CASCADE"),
                       nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    campaign = relationship("Campaign")
    sender = relationship("Sender")


# ─── Phase 5: LLM audit log ──────────────────────────────────────────────────

class LLMCall(Base):
    """Audit log of OpenAI chat.completions.create() calls (Phase 5 D-09..D-12).

    Logged from ai_engine.generate_response wrap (NOT warmup). Used for
    inbox-debug "почему AI ответил так".
    """
    __tablename__ = "llm_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    campaign_id = Column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_contexts.id", ondelete="SET NULL"),
        nullable=True,
    )
    sender_id = Column(
        UUID(as_uuid=True),
        ForeignKey("senders.id", ondelete="SET NULL"),
        nullable=True,
    )
    model = Column(String(50), nullable=False)
    prompt = Column(JSONB, nullable=False)
    response_text = Column(Text, nullable=True)
    tool_calls = Column(JSONB, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ─── Phase 05.1: Telemetry event ingest (UI-TEL-01) ───────────────────────────


class TelemetryEvent(Base):
    """UI telemetry event ingest (UI-SPEC §9, Phase 05.1).

    Storage for the 15-event whitelist (enforced at router-level in
    app/routers/telemetry.py — Wave 2). event_id is a client-supplied UUID
    for idempotency (navigator.sendBeacon retries on flaky networks).
    server_ts is authoritative for KPI queries; the core "time to first
    campaign" metric is computed as
        MIN(launch.server_ts) - MIN(signup_completed.server_ts)
    per (workspace_id, user_id).
    """
    __tablename__ = "telemetry_events"

    event_id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    user_id = Column(Text, nullable=True)
    event = Column(String(80), nullable=False)
    props = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    client_ts = Column(DateTime(timezone=True), nullable=True)
    server_ts = Column(DateTime(timezone=True),
                       server_default=func.now(), nullable=False)

    workspace = relationship("Workspace")
