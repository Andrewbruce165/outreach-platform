-- migrations/019_schema_drift_fix.sql
-- Закрытие schema-drift: ORM Base.metadata.create_all() создаёт только то, что
-- объявлено как Column в моделях. CHECK-constraints, partial indexes, partial
-- UNIQUE, индексы на FK и композитные индексы существуют ТОЛЬКО в raw-SQL
-- миграциях 010, 012, 013, 014, 015, 016, 018 и в БД не доехали.
--
-- Аудит показал 38 недостающих объектов и 0 нарушений данных на момент
-- накатки (все будущие CHECK'и и UNIQUE'и встают чисто). Эта миграция —
-- идемпотентная сборка всех недостающих DDL в одну транзакцию.
--
-- Соответствие исходным миграциям: см. блочные комментарии (010..018).

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. CHECK constraints (13 шт) — целостность данных
-- ─────────────────────────────────────────────────────────────────────────────

-- 012 user_workspaces.role
ALTER TABLE user_workspaces DROP CONSTRAINT IF EXISTS user_workspaces_role_check;
ALTER TABLE user_workspaces ADD CONSTRAINT user_workspaces_role_check
    CHECK (role IN ('owner','admin','member'));

-- 013 contacts.tg_status
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_tg_status_check;
ALTER TABLE contacts ADD CONSTRAINT contacts_tg_status_check
    CHECK (tg_status IN ('pending','registered','not_registered','error','unchecked'));

-- 013 contacts (phone OR username NOT NULL)
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_phone_or_username_check;
ALTER TABLE contacts ADD CONSTRAINT contacts_phone_or_username_check
    CHECK (phone IS NOT NULL OR username IS NOT NULL);

-- 013 senders.lifecycle_status
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_lifecycle_status_check;
ALTER TABLE senders ADD CONSTRAINT senders_lifecycle_status_check
    CHECK (lifecycle_status IN ('active','warmup','paused'));

-- 013 senders.role
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_role_check;
ALTER TABLE senders ADD CONSTRAINT senders_role_check
    CHECK (role IN ('sender','checker'));

-- 013 onboarding_sessions.role
ALTER TABLE onboarding_sessions DROP CONSTRAINT IF EXISTS onboarding_sessions_role_check;
ALTER TABLE onboarding_sessions ADD CONSTRAINT onboarding_sessions_role_check
    CHECK (role IN ('sender','checker'));

-- 013 onboarding_sessions.status
ALTER TABLE onboarding_sessions DROP CONSTRAINT IF EXISTS onboarding_sessions_status_check;
ALTER TABLE onboarding_sessions ADD CONSTRAINT onboarding_sessions_status_check
    CHECK (status IN ('code_sent','awaiting_2fa','completed','failed'));

-- 016 campaigns.status
ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_status_check;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_status_check
    CHECK (status IN ('draft','running','paused','done'));

-- 016 campaigns.work_hours
ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_work_hours_check;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_work_hours_check
    CHECK (work_hour_start >= 0 AND work_hour_end <= 24 AND work_hour_start < work_hour_end);

-- 016 campaigns.work_days_mask
ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_work_days_check;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_work_days_check
    CHECK (work_days_mask BETWEEN 1 AND 127);

-- 018 ai_contexts.voice_baseline
ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_voice_baseline_check;
ALTER TABLE ai_contexts ADD CONSTRAINT ai_contexts_voice_baseline_check
    CHECK (voice_baseline IS NULL OR voice_baseline IN ('Professional','Friendly','Playful'));

-- 018 ai_contexts.auto_pause_scope
ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_auto_pause_scope_check;
ALTER TABLE ai_contexts ADD CONSTRAINT ai_contexts_auto_pause_scope_check
    CHECK (auto_pause_scope IN ('conversation','contact','campaign'));

-- 018 campaigns.primary_goal
ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_primary_goal_check;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_primary_goal_check
    CHECK (primary_goal IS NULL OR primary_goal IN ('book_meeting','qualify','click','engage'));

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Partial UNIQUE indexes (4) — корректность бизнес-логики
-- ─────────────────────────────────────────────────────────────────────────────

-- 013 contacts: partial UNIQUE на (workspace_id, phone) и (workspace_id, username).
-- ЭТО ОСНОВНОЙ БАГ — без них ON CONFLICT DO NOTHING в _insert_contacts_with_dedup
-- не находит таргет → дубликаты вставляются.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_workspace_phone_unique
    ON contacts(workspace_id, phone)
    WHERE phone IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_workspace_username_unique
    ON contacts(workspace_id, username)
    WHERE username IS NOT NULL;

-- 015 ai_contexts UNIQUE (workspace_id, name) — защита от дублей агентов в workspace
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_contexts_workspace_name
    ON ai_contexts(workspace_id, name);

-- 016 campaigns UNIQUE (workspace_id, name) — защита от дублей кампаний
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_workspace_name
    ON campaigns(workspace_id, name);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Composite UNIQUE (2)
-- ─────────────────────────────────────────────────────────────────────────────

-- 014 senders.slug per-workspace UNIQUE (WR-02). Сейчас в БД только ix_senders_slug
-- (non-unique) — drop его и заменяем на UNIQUE (workspace_id, slug), который
-- также покрывает быстрый lookup по slug в пределах workspace.
DROP INDEX IF EXISTS ix_senders_slug;

CREATE UNIQUE INDEX IF NOT EXISTS idx_senders_workspace_slug
    ON senders(workspace_id, slug);

-- 016 campaign_contact_assignments UNIQUE (campaign_id, contact_phone)
CREATE UNIQUE INDEX IF NOT EXISTS idx_cca_campaign_phone
    ON campaign_contact_assignments(campaign_id, contact_phone);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Partial indexes для воркеров — производительность
-- ─────────────────────────────────────────────────────────────────────────────

-- 010 message_queue: главный индекс queue-воркера
CREATE INDEX IF NOT EXISTS idx_message_queue_sender_status_scheduled
    ON message_queue(sender_id, status, scheduled_at)
    WHERE status IN ('pending','processing');

-- 010 proxy_pool: lookup assigned-to-sender
CREATE INDEX IF NOT EXISTS idx_proxy_pool_assigned_sender
    ON proxy_pool(assigned_to_sender_id)
    WHERE assigned_to_sender_id IS NOT NULL;

-- 012 workspace_api_keys.prefix WHERE NOT revoked — auth lookup
CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_prefix_active
    ON workspace_api_keys(prefix)
    WHERE revoked_at IS NULL;

-- 013 contacts.tg_status WHERE 'pending' — на нём висит ContactCheckWorker
CREATE INDEX IF NOT EXISTS idx_contacts_tg_status
    ON contacts(tg_status)
    WHERE tg_status = 'pending';

-- 014 onboarding_sessions.original_sender_id partial — reauth-flow lookup
CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_original_sender_id
    ON onboarding_sessions(original_sender_id)
    WHERE original_sender_id IS NOT NULL;

-- 016 campaigns: фильтр живых кампаний в queue _tick
CREATE INDEX IF NOT EXISTS idx_campaigns_status_running
    ON campaigns(workspace_id, status)
    WHERE status = 'running';

-- 016 conversations.campaign_id — per-campaign analytics
CREATE INDEX IF NOT EXISTS idx_conversations_campaign_id
    ON conversations(campaign_id)
    WHERE campaign_id IS NOT NULL;

-- 016 message_queue composite — per-campaign queue tick
CREATE INDEX IF NOT EXISTS idx_message_queue_workspace_campaign_status_scheduled
    ON message_queue(workspace_id, campaign_id, status, scheduled_at)
    WHERE campaign_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Обычные индексы на FK / sort columns
-- ─────────────────────────────────────────────────────────────────────────────

-- 010 messages_log, conversations, warmup_sessions
CREATE INDEX IF NOT EXISTS idx_messages_log_sender_id
    ON messages_log(sender_id);
CREATE INDEX IF NOT EXISTS idx_conversations_sender_status
    ON conversations(sender_id, status);
CREATE INDEX IF NOT EXISTS idx_conversations_ai_enabled
    ON conversations(ai_enabled);
CREATE INDEX IF NOT EXISTS idx_warmup_sessions_sender_a
    ON warmup_sessions(sender_a_id);
CREATE INDEX IF NOT EXISTS idx_warmup_sessions_sender_b
    ON warmup_sessions(sender_b_id);

-- 012 workspace_id индексы на 10 тенантных таблицах
-- (context_contact_assignments DROP'нут миграцией 016 — не включаем).
CREATE INDEX IF NOT EXISTS idx_senders_workspace          ON senders(workspace_id);
CREATE INDEX IF NOT EXISTS idx_messages_log_workspace     ON messages_log(workspace_id);
CREATE INDEX IF NOT EXISTS idx_contacts_cache_workspace   ON contacts_cache(workspace_id);
CREATE INDEX IF NOT EXISTS idx_ai_contexts_workspace      ON ai_contexts(workspace_id);
CREATE INDEX IF NOT EXISTS idx_message_queue_workspace    ON message_queue(workspace_id);
CREATE INDEX IF NOT EXISTS idx_conversations_workspace    ON conversations(workspace_id);
CREATE INDEX IF NOT EXISTS idx_warmup_pool_workspace      ON warmup_pool(workspace_id);
CREATE INDEX IF NOT EXISTS idx_warmup_sessions_workspace  ON warmup_sessions(workspace_id);
CREATE INDEX IF NOT EXISTS idx_warmup_messages_workspace  ON warmup_messages(workspace_id);
CREATE INDEX IF NOT EXISTS idx_proxy_pool_workspace       ON proxy_pool(workspace_id);

-- 012 user_workspaces и workspace_api_keys
CREATE INDEX IF NOT EXISTS idx_user_workspaces_workspace_id
    ON user_workspaces(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_workspace_id
    ON workspace_api_keys(workspace_id);

-- 013 folders, contacts, onboarding_sessions, csv_imports
CREATE INDEX IF NOT EXISTS idx_folders_workspace            ON folders(workspace_id);
CREATE INDEX IF NOT EXISTS idx_contacts_workspace           ON contacts(workspace_id);
CREATE INDEX IF NOT EXISTS idx_contacts_folder              ON contacts(folder_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_workspace
    ON onboarding_sessions(workspace_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_expires_at
    ON onboarding_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_csv_imports_workspace  ON csv_imports(workspace_id);
CREATE INDEX IF NOT EXISTS idx_csv_imports_expires_at ON csv_imports(expires_at);

-- 016 campaigns/campaign_senders/campaign_contact_assignments
CREATE INDEX IF NOT EXISTS idx_campaigns_agent_id  ON campaigns(agent_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_folder_id ON campaigns(folder_id);
CREATE INDEX IF NOT EXISTS idx_campaign_senders_sender
    ON campaign_senders(sender_id);
CREATE INDEX IF NOT EXISTS idx_campaign_senders_workspace
    ON campaign_senders(workspace_id);
CREATE INDEX IF NOT EXISTS idx_cca_sender
    ON campaign_contact_assignments(sender_id);
CREATE INDEX IF NOT EXISTS idx_cca_workspace
    ON campaign_contact_assignments(workspace_id);

-- 018 telemetry_events composite
CREATE INDEX IF NOT EXISTS idx_telemetry_workspace_event_server
    ON telemetry_events(workspace_id, event, server_ts DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. 018 webhook_url backfill (идемпотентно через WHERE webhook_url IS NULL)
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE campaigns SET webhook_url = COALESCE(
    webhook_url, lead_webhook_url, handoff_webhook_url, finish_webhook_url
) WHERE webhook_url IS NULL
  AND (lead_webhook_url IS NOT NULL OR handoff_webhook_url IS NOT NULL OR finish_webhook_url IS NOT NULL);

COMMIT;
