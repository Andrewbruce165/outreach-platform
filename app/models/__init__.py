from sqlalchemy import Column, String, Text, Boolean, BigInteger, DateTime, Integer, Float, LargeBinary, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func, text
from pgvector.sqlalchemy import Vector  # Phase 16 — RAG KB chunk embeddings (D-06)
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
    # Migration 006: senders.telegram_id (BIGINT, nullable) — used by the warmup-pair
    # exclude in list_conversations (app/routers/conversations.py). Mirrored in the ORM
    # so create_all-built test schemas include it (prod has it via migration 006, which
    # the test setup skips — migrations 001-011 are assumed covered by the ORM).
    telegram_id = Column(BigInteger)
    session_string = Column(Text, nullable=False)  # Encrypted
    role = Column(String(20), nullable=False, server_default='sender')  # 'sender' or 'checker' (CHECK in migration 013)
    proxy = Column(JSONB, nullable=True)  # {"type": "socks5", "host": "...", "port": 1080, ...}
    auth_status = Column(String(30), nullable=False, server_default='ok')  # ok, session_expired, session_revoked, deactivated, banned
    # Phase 2 (D-11/D-13): lifecycle_status replaces is_active; rate limits live per-sender.
    lifecycle_status = Column(String(20), nullable=False, server_default='active')  # 'active' | 'warmup' | 'paused'
    # Migration 028: write-restriction state, orthogonal to auth_status (session validity).
    # A spam-limited / frozen account still authenticates → auth_status stays 'ok'.
    restriction_status = Column(String(20), nullable=False, server_default='none')  # 'none' | 'spam_limited' | 'frozen'
    restricted_until = Column(DateTime(timezone=True), nullable=True)  # when reconcile re-checks via SpamBot
    # Migration 035 (Phase 14 / Plan 14-07, Q3): BENIGN post-batch rest for a checker.
    # After a healthy checker finishes a clean resolve batch the worker stamps this so
    # it cannot chain batch-after-batch on ONE account past the ~45-50 burst onset
    # (existing rotation then alternates ≥2 checkers). NOT a restriction — setting it
    # never touches restriction_status/lifecycle_status/restricted_until and writes no
    # sender_restriction_events row; a checker waking from rest is just re-selected.
    checker_rest_until = Column(DateTime(timezone=True), nullable=True)  # benign post-batch rest (NOT a restriction)
    # Migration 048 (quick-260703-ssv / WR-04): durable non-blocking long-pause
    # marker. Replaces the inline asyncio.sleep(long_pause) that stalled the whole
    # shared queue tick. _tick excludes a sender while long_pause_until > NOW(), so
    # the pause survives a process restart (re-read from DB each tick, no in-memory
    # state) and doubles as the "already paused, don't re-trigger" guard.
    long_pause_until = Column(DateTime(timezone=True), nullable=True)  # WR-04: durable non-blocking long-pause marker
    # Migration 036 (quick-260629-b7j): per-checker CONSECUTIVE contacts-API trip
    # counter for the ESCALATING backoff. Each spam_limited trip increments it; the
    # cooldown is base * 2^(trip-1) capped at contact_check_max_backoff_seconds; a
    # clean recovery resets it to 0. Durable (survives api restart) so backoff is not
    # lost on redeploy. NOT a restriction itself — distinct from restriction_status/
    # restricted_until (the current state); only the cooldown computation reads it.
    checker_trip_count = Column(Integer, nullable=False, server_default='0')  # escalating-backoff trip counter
    rate_per_min = Column(Integer, nullable=False, server_default='4')
    rate_per_hour = Column(Integer, nullable=False, server_default='20')
    rate_per_day = Column(Integer, nullable=False, server_default='150')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), onupdate=func.now())
    # NB: ai_context_id dropped (Phase 3 D-04). Sender больше не «знает» агента —
    # связь идёт через Campaign в Phase 4.

    # Phase 20 (PROF-01): cached Telegram profile (mig 049). NULL = not yet cached.
    tg_username = Column(String(32), nullable=True)
    tg_bio = Column(String(140), nullable=True)   # free ≤70 / premium ≤140; AboutTooLongError is the runtime backstop
    tg_photo = Column(LargeBinary, nullable=True)  # small square avatar bytes, served via authenticated endpoint (D-11)
    tg_photo_mime = Column(String(32), nullable=True)
    # Per-field cooldown STATE (not a log): {"username": iso8601, "photo": iso8601, "name": ..., "bio": ...}.
    # server_default MANDATORY (memory project-orm-default-vs-server-default-drift): create_all builds the
    # test/fresh-DB schema from the ORM, not the migration — a NOT NULL column without server_default breaks
    # raw INSERTs (_insert_sender_raw) that omit it.
    profile_field_changed_at = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

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


class SenderRestrictionEvent(Base):
    """Phase 10 (HLTH-01/02): durable append-only restriction event-log.

    One row per restriction state-change OR genuine forward shift of restricted_until
    (D-01). Restriction-category rows carry an activity_slice + proxy snapshot computed
    at write time (D-05). The migration (030) is the DDL source of truth — this model
    exists for ORM reads in the Wave 3 history endpoint (from_attributes).
    """

    __tablename__ = "sender_restriction_events"

    # server_default so the row gets an id under BOTH paths: raw text() INSERTs
    # from restriction_audit.py (no ORM default applied) AND create_all (test
    # overlay builds the table from metadata, not the migration's DEFAULT).
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id = Column(UUID(as_uuid=True),
                       ForeignKey("senders.id", ondelete="CASCADE"),
                       nullable=False)
    category = Column(String(20), nullable=False, server_default='restriction')
    event_type = Column(String(20), nullable=False)
    source = Column(String(20), nullable=False)
    restricted_until = Column(DateTime(timezone=True), nullable=True)
    raw_text = Column(Text, nullable=True)
    activity_slice = Column(JSONB, nullable=True)
    proxy = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
    # tone_of_voice dropped Phase 11 D-01 (migration 032) — use tone_preset.
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
    # Phase 11 D-01: tone_preset is the single tone source (replaces voice_baseline/tone/tone_of_voice).
    # voice_baseline/tone/tone_of_voice dropped in migration 032 (Phase 11 D-01).
    tone_preset = Column(String(20), nullable=True)
    # Phase 11 D-11: response speed setting ('instant'|'human'|'slow'|'manual').
    response_speed = Column(String(20), nullable=True)
    # Phase 11 D-11: exact delay in seconds when response_speed='manual'.
    response_delay_seconds = Column(Integer, nullable=True)
    max_message_length = Column(Integer, nullable=True, server_default="280")
    mirror_language = Column(Boolean, nullable=True, server_default="true")
    allow_emoji = Column(Boolean, nullable=True, server_default="false")
    banlist = Column(ARRAY(Text), nullable=True)
    qa_pairs = Column(JSONB, nullable=True)
    auto_pause_triggers = Column(ARRAY(Text), nullable=True)
    auto_pause_scope = Column(String(20), nullable=True, server_default="conversation")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # NB: is_active dropped (Phase 3 D-01).
    # NB: voice_baseline/tone/tone_of_voice dropped (Phase 11 D-01 via migration 032).
    #     tone_preset is the single source; response_speed/response_delay_seconds added.
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
    as_draft = Column(Boolean, default=False, server_default=text("false"))  # WR-02: DB default matches migration 047

    # Payload — for files
    file_url = Column(Text)
    file_name = Column(String(255))
    caption = Column(Text)

    # Extra metadata from caller
    extra_data = Column(JSONB, default={})

    # Webhook callback — called by worker after send (success or failure)
    callback_url = Column(Text, nullable=True)

    # Queue management
    priority = Column(Integer, default=0, server_default="0")  # WR-02: higher = processed first within same sender (DB default matches migration 047)
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
    attempts = Column(Integer, default=0, server_default="0")  # WR-02: DB default matches migration 047

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
    status = Column(String(20), default="active", server_default="active")  # active|manual|paused|lead|handoff|finished|bot_ignored|no_reply
    # Phase 19 (D-03/D-04/D-08): follow-up state counter — how many pings this
    # conversation has already received. server_default="0" duplicates migration-045
    # so the create_all rebuild path (post-DROP-incident) reconstructs the DB default.
    pings_sent = Column(Integer, nullable=False, default=0, server_default="0")
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
    status          = Column(String(20), nullable=False, default="active", server_default="active")  # active, completed
    messages_sent   = Column(Integer, nullable=False, default=0, server_default="0")
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


class WarmupSettings(Base):
    """Per-workspace warmup control + content settings (Phase 15, WARM-06/WARM-10).

    One row per workspace. `enabled` is the master on/off toggle (D-06); it
    DEFAULTS to FALSE — warmup is explicit opt-in and migration 038 seeds no
    live workspace. `topics`/`system_prompt`/`language`/`tone` are the
    configurable content object (D-10); empty `topics` or NULL `system_prompt`
    resolve in code to the hard-coded WARMUP_TOPICS / WARMUP_SYSTEM_PROMPT
    defaults so existing behaviour is unchanged when nothing is configured.
    """
    __tablename__ = "warmup_settings"

    workspace_id  = Column(UUID(as_uuid=True),
                           ForeignKey("workspaces.id", ondelete="CASCADE"),
                           primary_key=True)
    enabled       = Column(Boolean, nullable=False, server_default=text("false"))
    topics        = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    system_prompt = Column(Text, nullable=True)
    language      = Column(Text, nullable=False, server_default=text("'ru'"))
    tone          = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)


class LLMSettings(Base):
    """Per-workspace LLM provider/model/knobs + encrypted BYO key (Phase 18).

    D-01: setting scope is workspace-level — one row per workspace (PK workspace_id),
    no per-agent override this phase. Absence of a row = platform default (D-02):
    platform OPENAI_API_KEY + settings.openai_model. `api_key_encrypted`
    is Fernet ciphertext (D-04); `api_key_prefix` (prefix+last4) is the ONLY
    key material ever returned to the UI. `api_key_status` tracks validity
    (D-05/D-06). Knob columns are nullable => provider/code default.

    Every NOT NULL column carries server_default= matching the SQL DEFAULT in
    migration 044 (Pitfall 5 — mig 040/042 create_all drift): create_all must
    build the test schema WITH the DB default so raw-SQL INSERTs don't NotNull.
    """
    __tablename__ = "llm_settings"

    workspace_id      = Column(UUID(as_uuid=True),
                               ForeignKey("workspaces.id", ondelete="CASCADE"),
                               primary_key=True)
    provider          = Column(Text, nullable=False, server_default=text("'openai'"))
    model             = Column(Text, nullable=True)
    api_key_encrypted = Column(Text, nullable=True)
    api_key_prefix    = Column(Text, nullable=True)
    api_key_status    = Column(Text, nullable=False, server_default=text("'unset'"))
    temperature       = Column(Float, nullable=True)
    reasoning_effort  = Column(Text, nullable=True)
    max_tokens        = Column(Integer, nullable=True)
    created_at        = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(),
                               onupdate=func.now(), nullable=False)


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
    # Phase 14 (migration 034, RESV-06/D-09): resolution confidence/source.
    # The migration is prod source-of-truth; this ORM mirror is for the test-overlay
    # which builds schema via Base.metadata.create_all, not migrations.
    tg_confidence = Column(String(10), nullable=True)        # 'high'|'low'|NULL
    tg_resolved_by = Column(UUID(as_uuid=True), nullable=True)  # checker sender_id (resolver-provenance, D-09)
    tg_probe_state = Column(String(10), nullable=True)       # 'clean'|'suspect'|NULL
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
    # NB: success_criteria dropped (Phase 11 D-13 via migration 032) — merged into lead_trigger_hint.
    webhook_url = Column(Text, nullable=True)
    # ── Phase 11 campaign fields (D-04/D-12/D-14). ──
    # dialogue_flow: ordered list of DialogueStage objects (JSONB, full-replace on PATCH).
    dialogue_flow = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # arguments_facts: free-text product facts / proof points injected into the prompt.
    arguments_facts = Column(Text, nullable=True)
    # campaign_rules: free-text rules specific to this campaign (de-duped with agent rules in 11-03).
    campaign_rules = Column(Text, nullable=True)
    # ── Prompt template v2 (migration 037): preset-driven core_directive. ──
    # objective/disclosure/authority resolve to preset lines in ai_engine
    # (_OBJECTIVE_LINES / _DISCLOSURE_LINES / _AUTHORITY_LINES). NULL → engine
    # defaults (disclosure→reveal_nothing, authority→handoff_only,
    # objective→primary_goal). Allowed values enforced at API layer (Literal
    # enums), no DB CHECK — mirrors primary_goal. style_examples is an optional
    # per-campaign few-shot override; NULL → static both-language fallback.
    objective_preset = Column(Text, nullable=True)
    disclosure_preset = Column(Text, nullable=True)
    authority_preset = Column(Text, nullable=True)
    style_examples = Column(Text, nullable=True)
    # 026: per-campaign re-contact policy. allow_recontact=false (default) keeps
    # the strict cross-campaign dedup — never re-touch anyone with an existing
    # conversation. When true, only "protected" (active & fresh) dialogs block;
    # closed/stale ones are eligible again. recontact_min_age_days = staleness
    # threshold for "fresh".
    allow_recontact = Column(Boolean, nullable=False, server_default="false")
    recontact_min_age_days = Column(Integer, nullable=False, server_default="30")
    # Phase 12 (D-10/D-11, NDLG-01): per-sender-per-campaign daily new-dialog cap.
    # DEFAULT 50 applies to ALL existing campaigns incl. running (D-11, no backfill).
    # server_default="50" duplicates the migration-033 DB default for the create_all
    # path (post-DROP-incident rebuild reconstructs tables from the ORM). API enforces
    # the ge=1/le=100 bounds (D-12); no DB CHECK.
    max_new_dialogs_per_day = Column(Integer, nullable=False, server_default="50")
    # Phase 19 (D-08/D-12): no-reply follow-up + auto-finish policy per campaign.
    # follow_up_enabled off by default (opt-in). server_default values duplicate the
    # migration-045 DB defaults for the create_all rebuild path. API enforces the
    # bounds (interval 4–168h, max_pings 1–5, auto_finish 24–720h); no DB CHECK.
    follow_up_enabled = Column(Boolean, nullable=False, server_default="false")
    follow_up_interval_hours = Column(Integer, nullable=False, server_default="24")
    follow_up_max_pings = Column(Integer, nullable=False, server_default="2")
    auto_finish_hours = Column(Integer, nullable=False, server_default="72")
    # 029: auto-pause visibility. NULL = manual pause / never paused; a machine
    # code ('no_senders_attached' | 'senders_unavailable') = auto-paused by the
    # enqueue worker because the campaign could no longer send. Cleared on start/resume.
    pause_reason = Column(String(40), nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
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


# ─── Phase 16: RAG knowledge bases (KB-01..KB-06) ────────────────────────────
# ORM mirror of migration 041. The test-overlay builds schema from
# Base.metadata.create_all (NOT migrations), so these classes MUST mirror the
# four KB tables — incl. the Vector(1536) column — or the test schema diverges
# from prod. The static AIContext.knowledge_base Text field stays untouched
# (D-08): it is a separate always-in-prompt facts slot, NOT this RAG mechanism.


class KnowledgeBase(Base):
    """Workspace-scoped knowledge base (D-05). Container for documents + chunks."""
    __tablename__ = "knowledge_bases"

    # server_default so the row gets an id under BOTH the ORM path (client-side
    # default=) AND raw text() INSERTs. create_all wins over migration 041 in
    # init_db, so without server_default the prod column has no DB default and
    # any raw insert omitting id hits NotNullViolation (mirrors sender_restriction_events).
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    source_kind = Column(String(20), nullable=False, server_default="files")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KbDocument(Base):
    """Uploaded/pasted source document in a KB (D-01/D-02). Ingest-worker target."""
    __tablename__ = "kb_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    kb_id = Column(UUID(as_uuid=True),
                   ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                   nullable=False)
    name = Column(String(255), nullable=False)
    # 'pdf'|'docx'|'txt'|'md'|'csv'|'text' (text = pasted, raw_content holds utf-8).
    source_kind = Column(String(20), nullable=False)
    size_bytes = Column(BigInteger, nullable=False, server_default="0")
    # pending|processing|indexed|failed — worker claim WHERE status='pending'.
    status = Column(String(20), nullable=False, server_default="pending")
    error = Column(Text, nullable=True)
    chunk_count = Column(Integer, nullable=False, server_default="0")
    raw_content = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KbChunk(Base):
    """Embedded text chunk of a document (D-06 text-embedding-3-small, 1536 dims)."""
    __tablename__ = "kb_chunks"

    # server_default (gen_random_uuid) — the ingest worker inserts chunks via raw
    # text() SQL omitting id; without a DB default that hits NotNullViolation.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    kb_id = Column(UUID(as_uuid=True),
                   ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                   nullable=False)
    document_id = Column(UUID(as_uuid=True),
                         ForeignKey("kb_documents.id", ondelete="CASCADE"),
                         nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AgentKnowledgeBase(Base):
    """Through-table agent ↔ KB (D-07, M:N). PK (agent_id, kb_id).

    Mirrors the CampaignSender composite-PK pattern. agent_id FKs ai_contexts(id)
    (agent table is ai_contexts — see Terminology in PROJECT.md).
    """
    __tablename__ = "agent_knowledge_bases"

    agent_id = Column(UUID(as_uuid=True),
                      ForeignKey("ai_contexts.id", ondelete="CASCADE"),
                      primary_key=True)
    kb_id = Column(UUID(as_uuid=True),
                   ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                   primary_key=True)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    added_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


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
    provider = Column(Text, nullable=True)    # D-07 (Phase 18): 'openai'|'anthropic'
    key_source = Column(Text, nullable=True)  # D-07 (Phase 18): 'platform'|'byok'|'fallback'
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
