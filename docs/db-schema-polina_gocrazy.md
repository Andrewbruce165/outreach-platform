# Схема БД `outreach_platform` — доступ для `polina_gocrazy`

**Подключение:** host `outreach-platform-db`, port `5432`, database `outreach_platform`, user `polina_gocrazy`.

**Доступ:** только чтение (`SELECT`) к 31 таблице схемы `public`. Записи/изменения нет.

Почти все таблицы мультитенантные — фильтруй по `workspace_id` (FK → `workspaces.id`). Все `id` — UUID (`gen_random_uuid()`), временные поля — `timestamptz`.

---

## Карта таблиц

| Таблица | Назначение |
|---|---|
| `workspaces` | Воркспейсы (тенанты) |
| `user_workspaces` | Привязка Supabase-пользователей к воркспейсам |
| `workspace_api_keys` | API-ключи воркспейса (bcrypt-хэши) |
| `folders` | Папки контактов |
| `contacts` | Контакты (лиды) + статус резолва в Telegram |
| `contacts_cache` | Кэш Telegram-резолва per-sender (telegram_id, access_hash) |
| `csv_imports` | Временные CSV-загрузки (истекают по `expires_at`) |
| `campaigns` | Кампании аутрича: расписание, шаблон, вебхуки, dialogue flow |
| `campaign_senders` | Аккаунты, прикреплённые к кампании (M:N) |
| `campaign_contact_assignments` | Закрепление контакта за отправителем в кампании |
| `senders` | Telegram-аккаунты: лимиты, статусы ограничений, роль (sender/checker) |
| `sender_restriction_events` | Append-only лог ограничений аккаунтов (spam_limited/frozen/…) |
| `onboarding_sessions` | Незавершённые онбординги Telegram-аккаунтов |
| `proxy_pool` | Прокси, назначенные аккаунтам |
| `conversations` | Диалоги (sender × контакт), режим AI on/off |
| `messages` | Сообщения внутри диалогов |
| `messages_log` | Лог всех отправок (sent/draft/failed) |
| `message_queue` | Очередь исходящих сообщений |
| `ai_contexts` | AI-агенты: промпты, тон, правила, банлист |
| `agent_knowledge_bases` | Привязка агентов к базам знаний (M:N) |
| `knowledge_bases` | Базы знаний (RAG) |
| `kb_documents` | Документы баз знаний |
| `kb_chunks` | Чанки документов + pgvector-эмбеддинги |
| `llm_settings` | LLM-настройки воркспейса (provider, model, ключ) |
| `llm_calls` | Лог LLM-вызовов: промпт, ответ, токены, latency |
| `warmup_settings` | Настройки прогрева воркспейса |
| `warmup_pool` | Аккаунты в пуле прогрева |
| `warmup_sessions` | Сессии прогрева (пары аккаунтов) |
| `warmup_messages` | Сообщения прогрева |
| `telemetry_events` | UI-телеметрия фронта |
| `schema_migrations` | Служебная: применённые миграции |

**Enum-типы:**
- `message_queue.item_type`: `message`, `file`
- `message_queue.status`: `pending`, `processing`, `sent`, `failed`, `cancelled`
- `messages_log.message_type`: `sent`, `draft`, `failed`
- `kb_chunks.embedding`: pgvector `vector`

---

## workspaces

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `name` | varchar(100) | NO | |
| `created_at` | timestamptz | YES | now() |
| `updated_at` | timestamptz | YES | now() |

## user_workspaces

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `supabase_user_id` **UQ** | text | NO | |
| `workspace_id` FK→workspaces | uuid | NO | |
| `role` | varchar(20) | NO | 'owner' |
| `created_at` | timestamptz | YES | now() |

## workspace_api_keys

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `prefix` | varchar(12) | NO | |
| `bcrypt_hash` | text | NO | |
| `name` | varchar(50) | NO | |
| `created_at` | timestamptz | YES | now() |
| `last_used_at` | timestamptz | YES | |
| `revoked_at` | timestamptz | YES | |

## folders

Уникальность: `(workspace_id, name)`.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `name` | varchar(100) | NO | |
| `created_at` | timestamptz | YES | now() |
| `updated_at` | timestamptz | YES | now() |

## contacts

`tg_status='not_registered'` ≠ «нет Telegram» — это «не резолвится по телефону» (приватность/троттл чекера дают false negatives). Смотри `tg_confidence` / `tg_probe_state`.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `folder_id` FK→folders | uuid | NO | |
| `phone` | varchar(20) | YES | |
| `username` | varchar(50) | YES | |
| `full_name` | varchar(200) | YES | |
| `source` | varchar(100) | YES | |
| `custom` | jsonb | NO | '{}' |
| `tg_status` | varchar(20) | NO | 'pending' |
| `tg_telegram_id` | bigint | YES | |
| `tg_username_resolved` | varchar(50) | YES | |
| `tg_error` | text | YES | |
| `tg_checked_at` | timestamptz | YES | |
| `tg_confidence` | text | YES | |
| `tg_resolved_by` | uuid | YES | |
| `tg_probe_state` | text | YES | |
| `created_at` | timestamptz | YES | now() |
| `updated_at` | timestamptz | YES | now() |

## contacts_cache

Кэш резолва per-sender: `access_hash` валиден только для аккаунта `sender_id`.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `sender_id` FK→senders | uuid | NO | |
| `phone` | varchar(40) | NO | |
| `telegram_id` | bigint | YES | |
| `access_hash` | bigint | YES | |
| `first_name` | varchar(100) | YES | |
| `last_name` | varchar(100) | YES | |
| `username` | varchar(50) | YES | |
| `is_registered` | boolean | YES | |
| `updated_at` | timestamptz | YES | now() |

## csv_imports

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `file_data` | bytea | NO | |
| `columns` | jsonb | NO | |
| `suggested_mapping` | jsonb | NO | |
| `encoding` | varchar(20) | YES | |
| `delimiter` | varchar(5) | YES | |
| `created_at` | timestamptz | YES | now() |
| `expires_at` | timestamptz | NO | |

## campaigns

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `agent_id` FK→ai_contexts | uuid | YES | |
| `folder_id` FK→folders | uuid | YES | |
| `name` | varchar(150) | NO | |
| `description` | text | YES | |
| `status` | varchar(20) | NO | 'draft' |
| `timezone` | text | NO | 'Europe/Moscow' |
| `work_hour_start` | integer | NO | 9 |
| `work_hour_end` | integer | NO | 20 |
| `work_days_mask` | integer | NO | 31 |
| `start_date` | timestamptz | YES | |
| `stop_date` | timestamptz | YES | |
| `message_template` | text | NO | '' |
| `lead_webhook_url` | text | YES | |
| `handoff_webhook_url` | text | YES | |
| `finish_webhook_url` | text | YES | |
| `lead_trigger_hint` | text | YES | |
| `handoff_trigger_hint` | text | YES | |
| `finish_trigger_hint` | text | YES | |
| `tools` | jsonb | NO | '[]' |
| `audience_hints` | text | YES | |
| `primary_goal` | varchar(20) | YES | |
| `webhook_url` | text | YES | |
| `allow_recontact` | boolean | NO | false |
| `recontact_min_age_days` | integer | NO | 30 |
| `pause_reason` | varchar(40) | YES | |
| `paused_at` | timestamptz | YES | |
| `dialogue_flow` | jsonb | NO | '[]' |
| `arguments_facts` | text | YES | |
| `campaign_rules` | text | YES | |
| `max_new_dialogs_per_day` | integer | NO | 50 |
| `objective_preset` | text | YES | |
| `disclosure_preset` | text | YES | |
| `authority_preset` | text | YES | |
| `style_examples` | text | YES | |
| `created_at` | timestamptz | NO | now() |
| `updated_at` | timestamptz | NO | now() |

## campaign_senders

PK составной: `(campaign_id, sender_id)`.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `campaign_id` **PK** FK→campaigns | uuid | NO | |
| `sender_id` **PK** FK→senders | uuid | NO | |
| `workspace_id` FK→workspaces | uuid | NO | |
| `added_at` | timestamptz | NO | now() |

## campaign_contact_assignments

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `campaign_id` FK→campaigns | uuid | NO | |
| `contact_phone` | varchar(40) | NO | |
| `sender_id` FK→senders | uuid | NO | |
| `created_at` | timestamptz | NO | now() |

## senders

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `slug` | varchar(50) | NO | |
| `name` | varchar(100) | NO | |
| `phone` | varchar(20) | NO | |
| `session_string` | text | NO | (зашифровано) |
| `role` | varchar(20) | NO | 'sender' |
| `proxy` | jsonb | YES | |
| `auth_status` | varchar(30) | NO | 'ok' |
| `lifecycle_status` | varchar(20) | NO | 'active' |
| `rate_per_min` | integer | NO | 4 |
| `rate_per_hour` | integer | NO | 20 |
| `rate_per_day` | integer | NO | 150 |
| `telegram_id` | bigint | YES | |
| `restriction_status` | text | NO | 'none' |
| `restricted_until` | timestamptz | YES | |
| `checker_rest_until` | timestamptz | YES | |
| `checker_trip_count` | integer | NO | 0 |
| `created_at` | timestamptz | YES | now() |
| `last_used_at` | timestamptz | YES | |

## sender_restriction_events

Append-only, данные с 2026-06-24. `event_type`: spam_limited / frozen / cleared / extension / recipient_privacy. `source`: queue_error / antispam_signal / reconcile / privacy_check.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `sender_id` FK→senders | uuid | NO | |
| `category` | varchar(20) | NO | 'restriction' |
| `event_type` | varchar(20) | NO | |
| `source` | varchar(20) | NO | |
| `restricted_until` | timestamptz | YES | |
| `raw_text` | text | YES | |
| `activity_slice` | jsonb | YES | |
| `proxy` | jsonb | YES | |
| `created_at` | timestamptz | YES | now() |

## onboarding_sessions

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `phone` | varchar(20) | NO | |
| `phone_code_hash` | text | NO | |
| `encrypted_session_string` | text | NO | |
| `role` | varchar(20) | NO | 'sender' |
| `proxy` | jsonb | YES | |
| `status` | varchar(20) | NO | |
| `original_sender_id` FK→senders | uuid | YES | |
| `expires_at` | timestamptz | NO | |
| `created_at` | timestamptz | YES | now() |

## proxy_pool

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `host` | varchar(255) | NO | |
| `port` | integer | NO | |
| `username` | varchar(100) | NO | |
| `password` | varchar(100) | YES | |
| `assigned_to_sender_id` FK→senders | uuid | YES | |
| `created_at` | timestamptz | YES | now() |

## conversations

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `sender_id` FK→senders | uuid | NO | |
| `contact_phone` | varchar(40) | NO | |
| `contact_name` | varchar(100) | YES | |
| `contact_telegram_id` | bigint | YES | |
| `ai_enabled` | boolean | YES | true |
| `ai_context_id` FK→ai_contexts | uuid | YES | |
| `campaign_id` FK→campaigns | uuid | YES | |
| `status` | varchar(20) | YES | 'active' |
| `paused_at` | timestamptz | YES | |
| `paused_reason` | text | YES | |
| `created_at` | timestamptz | YES | now() |
| `updated_at` | timestamptz | YES | now() |

## messages

Уникальность: `(conversation_id, telegram_message_id)`. `direction`: incoming/outgoing; `sent_by`: ai/manager/….

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | YES | |
| `conversation_id` FK→conversations | uuid | NO | |
| `direction` | varchar(20) | NO | |
| `message_text` | text | NO | |
| `sent_by` | varchar(20) | NO | |
| `telegram_message_id` | bigint | YES | |
| `created_at` | timestamptz | NO | now() |

## messages_log

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `sender_id` FK→senders | uuid | NO | |
| `recipient_phone` | varchar(40) | NO | |
| `recipient_name` | varchar(100) | YES | |
| `recipient_telegram_id` | bigint | YES | |
| `message_text` | text | NO | |
| `message_type` | enum: sent, draft, failed | NO | |
| `error_message` | text | YES | |
| `extra_data` | jsonb | YES | |
| `created_at` | timestamptz | YES | now() |

## message_queue

Текст опенера рендерится при постановке в очередь (правка шаблона кампании не меняет pending-строки).

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `sender_id` FK→senders | uuid | NO | |
| `campaign_id` FK→campaigns | uuid | YES | |
| `item_type` | enum: message, file | NO | |
| `status` | enum: pending, processing, sent, failed, cancelled | NO | |
| `recipient_phone` | varchar(40) | NO | |
| `recipient_name` | varchar(100) | YES | |
| `message_text` | text | YES | |
| `as_draft` | boolean | YES | |
| `file_url` | text | YES | |
| `file_name` | varchar(255) | YES | |
| `caption` | text | YES | |
| `extra_data` | jsonb | YES | |
| `callback_url` | text | YES | |
| `priority` | integer | YES | |
| `scheduled_at` | timestamptz | YES | now() |
| `started_at` | timestamptz | YES | |
| `finished_at` | timestamptz | YES | |
| `result_message_id` | varchar(50) | YES | |
| `result_recipient_telegram_id` | bigint | YES | |
| `result_recipient_name` | varchar(100) | YES | |
| `result_recipient_username` | varchar(50) | YES | |
| `error_message` | text | YES | |
| `attempts` | integer | YES | |
| `created_at` | timestamptz | YES | now() |

## ai_contexts

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `name` | varchar(100) | NO | |
| `system_prompt` | text | YES | |
| `rules` | text | YES | |
| `company_info` | text | YES | |
| `product_info` | text | YES | |
| `faq` | jsonb | YES | |
| `who_is_agent` | text | YES | |
| `company_knowledge` | text | YES | |
| `knowledge_base` | text | YES | |
| `mirror_language` | boolean | YES | true |
| `allow_emoji` | boolean | YES | false |
| `banlist` | text[] | YES | |
| `qa_pairs` | jsonb | YES | |
| `auto_pause_scope` | varchar(20) | YES | 'conversation' |
| `auto_pause_triggers` | text[] | YES | |
| `max_message_length` | integer | YES | 280 |
| `tone_preset` | varchar(20) | YES | |
| `response_speed` | varchar(20) | YES | |
| `response_delay_seconds` | integer | YES | |
| `created_at` | timestamptz | YES | now() |
| `updated_at` | timestamptz | YES | now() |

## agent_knowledge_bases

PK составной: `(agent_id, kb_id)`.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `agent_id` **PK** FK→ai_contexts | uuid | NO | |
| `kb_id` **PK** FK→knowledge_bases | uuid | NO | |
| `workspace_id` FK→workspaces | uuid | NO | |
| `added_at` | timestamptz | NO | now() |

## knowledge_bases

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `name` | varchar(150) | NO | |
| `description` | text | YES | |
| `source_kind` | varchar(20) | NO | 'files' |
| `created_at` | timestamptz | YES | now() |
| `updated_at` | timestamptz | YES | now() |

## kb_documents

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `kb_id` FK→knowledge_bases | uuid | NO | |
| `name` | varchar(255) | NO | |
| `source_kind` | varchar(20) | NO | |
| `size_bytes` | bigint | NO | 0 |
| `status` | varchar(20) | NO | 'pending' |
| `error` | text | YES | |
| `chunk_count` | integer | NO | 0 |
| `raw_content` | bytea | YES | |
| `created_at` | timestamptz | YES | now() |
| `updated_at` | timestamptz | YES | now() |

## kb_chunks

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `kb_id` FK→knowledge_bases | uuid | NO | |
| `document_id` FK→kb_documents | uuid | NO | |
| `chunk_index` | integer | NO | |
| `content` | text | NO | |
| `embedding` | vector (pgvector) | NO | |
| `created_at` | timestamptz | YES | now() |

## llm_settings

PK = `workspace_id` (одна строка на воркспейс).

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `workspace_id` **PK** FK→workspaces | uuid | NO | |
| `provider` | text | NO | 'openai' |
| `model` | text | YES | |
| `api_key_encrypted` | text | YES | |
| `api_key_prefix` | text | YES | |
| `api_key_status` | text | NO | 'unset' |
| `temperature` | double precision | YES | |
| `reasoning_effort` | text | YES | |
| `max_tokens` | integer | YES | |
| `created_at` | timestamptz | NO | now() |
| `updated_at` | timestamptz | NO | now() |

## llm_calls

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `conversation_id` FK→conversations | uuid | NO | |
| `campaign_id` FK→campaigns | uuid | YES | |
| `agent_id` FK→ai_contexts | uuid | YES | |
| `sender_id` FK→senders | uuid | YES | |
| `model` | varchar(50) | NO | |
| `prompt` | jsonb | NO | |
| `response_text` | text | YES | |
| `tool_calls` | jsonb | YES | |
| `prompt_tokens` | integer | YES | |
| `completion_tokens` | integer | YES | |
| `total_tokens` | integer | YES | |
| `latency_ms` | integer | YES | |
| `error` | text | YES | |
| `provider` | text | YES | |
| `key_source` | text | YES | |
| `created_at` | timestamptz | NO | now() |

## warmup_settings

PK = `workspace_id`.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `workspace_id` **PK** FK→workspaces | uuid | NO | |
| `enabled` | boolean | NO | false |
| `topics` | jsonb | NO | '[]' |
| `system_prompt` | text | YES | |
| `language` | text | NO | 'ru' |
| `tone` | text | YES | |
| `created_at` | timestamptz | NO | now() |
| `updated_at` | timestamptz | NO | now() |

## warmup_pool

Уникальность: `sender_id`.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `sender_id` FK→senders, **UQ** | uuid | NO | |
| `is_active` | boolean | NO | true |
| `enrolled_at` | timestamptz | NO | now() |

## warmup_sessions

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `sender_a_id` FK→senders | uuid | NO | |
| `sender_b_id` FK→senders | uuid | NO | |
| `topic` | text | NO | |
| `status` | varchar(20) | NO | 'active' |
| `messages_sent` | integer | NO | 0 |
| `target_messages` | integer | NO | |
| `next_message_at` | timestamptz | NO | now() |
| `last_sender_id` FK→senders | uuid | YES | |
| `created_at` | timestamptz | NO | now() |
| `updated_at` | timestamptz | NO | now() |

## warmup_messages

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `id` **PK** | uuid | NO | gen_random_uuid() |
| `workspace_id` FK→workspaces | uuid | NO | |
| `session_id` FK→warmup_sessions | uuid | NO | |
| `from_sender_id` FK→senders | uuid | NO | |
| `to_sender_id` FK→senders | uuid | NO | |
| `message_text` | text | NO | |
| `sent_at` | timestamptz | NO | now() |

## telemetry_events

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `event_id` **PK** | uuid | NO | |
| `workspace_id` FK→workspaces | uuid | NO | |
| `user_id` | text | YES | |
| `event` | varchar(80) | NO | |
| `props` | jsonb | NO | '{}' |
| `client_ts` | timestamptz | YES | |
| `server_ts` | timestamptz | NO | now() |

## schema_migrations

Служебная таблица авто-applier'а миграций.

| Колонка | Тип | Null | Default |
|---|---|---|---|
| `version` **PK** | text | NO | |
| `applied_at` | timestamptz | NO | now() |
| `sha256` | text | NO | |
