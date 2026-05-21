from sqlalchemy import Column, String, Text, Boolean, BigInteger, DateTime, Integer, LargeBinary, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
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
    ai_context_id = Column(UUID(as_uuid=True), ForeignKey("ai_contexts.id", ondelete="SET NULL"), nullable=True)
    
    # Relationships
    messages = relationship("MessageLog", back_populates="sender")
    contacts = relationship("ContactCache", back_populates="sender")
    ai_context = relationship("AIContext", back_populates="senders")


class MessageLog(Base):
    __tablename__ = "messages_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id"), nullable=False)
    recipient_phone = Column(String(20), nullable=False)
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
    phone = Column(String(20), nullable=False, index=True)
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
    max_message_length = Column(BigInteger, default=500)
    response_delay_seconds = Column(BigInteger, default=5)
    auto_pause_triggers = Column(JSONB, default=[])
    is_active = Column(Boolean, default=True, server_default='true')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    senders = relationship("Sender", back_populates="ai_context")


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
    recipient_phone = Column(String(20), nullable=False)
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

    # Relationships
    sender = relationship("Sender")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"), nullable=False)
    contact_phone = Column(String(20), nullable=False)
    contact_name = Column(String(100), nullable=True)
    contact_telegram_id = Column(BigInteger, nullable=True)
    ai_enabled = Column(Boolean, default=True, server_default='true')
    ai_context_id = Column(UUID(as_uuid=True), ForeignKey("ai_contexts.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), default="active", server_default="'active'")  # active, manual, paused
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


class ContextContactAssignment(Base):
    """Persistent mapping (context_id, contact_phone) → sender_id for account rotation."""
    __tablename__ = "context_contact_assignments"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id  = Column(UUID(as_uuid=True),
                           ForeignKey("workspaces.id", ondelete="CASCADE"),
                           nullable=False)
    context_id    = Column(UUID(as_uuid=True), ForeignKey("ai_contexts.id", ondelete="CASCADE"), nullable=False)
    contact_phone = Column(String(20), nullable=False)
    sender_id     = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"), nullable=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    context = relationship("AIContext")
    sender  = relationship("Sender")


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
