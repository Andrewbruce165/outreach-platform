-- migrations/021_uuid_defaults.sql
-- Восстановление DEFAULT gen_random_uuid() на UUID PK колонках.
--
-- Корень проблемы: ORM `Base.metadata.create_all` не транслирует Python-level
-- `default=uuid.uuid4` в DDL. Колонка создаётся как `id UUID PRIMARY KEY NOT
-- NULL` БЕЗ серверного default'а. ORM-вставки работают (uuid генерится в
-- Python), но любой raw `text("INSERT INTO x (workspace_id, ...) VALUES ...")`
-- без явного `gen_random_uuid()` в VALUES падает с NotNullViolationError.
--
-- Пример пострадавшего: app/services/checker.py:90 — `INSERT INTO
-- contacts_cache ... ON CONFLICT (sender_id, phone) DO UPDATE` не передаёт id
-- → cache save всегда падает в warning → 7-дневный TTL-кеш checker'а мёртв.
--
-- Все миграции 010–018 при `CREATE TABLE` указывают `id UUID PRIMARY KEY
-- DEFAULT gen_random_uuid()` — но миграции не доехали, ORM создал таблицы
-- без default'ов.
--
-- ALTER COLUMN ... SET DEFAULT — идемпотентная операция (last wins,
-- одинаковый default = no-op). Безопасно прогонять повторно.
--
-- Исключения:
--   - campaign_senders: composite PK (campaign_id, sender_id) — FK-ссылки,
--     не генерируемые UUID. Не трогаем.
--   - telemetry_events.event_id: по дизайну принимается от клиента
--     (UI-SPEC §9), default не нужен.

BEGIN;

ALTER TABLE ai_contexts                  ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE campaign_contact_assignments ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE campaigns                    ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE contacts                     ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE contacts_cache               ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE conversations                ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE csv_imports                  ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE folders                      ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE llm_calls                    ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE message_queue                ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE messages_log                 ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE onboarding_sessions          ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE proxy_pool                   ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE senders                      ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE user_workspaces              ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE warmup_messages              ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE warmup_pool                  ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE warmup_sessions              ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE workspace_api_keys           ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE workspaces                   ALTER COLUMN id SET DEFAULT gen_random_uuid();

COMMIT;
