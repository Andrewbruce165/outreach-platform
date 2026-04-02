-- migrations/006_senders_telegram_id.sql
-- Добавляем telegram_id в senders для надёжной фильтрации warmup-диалогов
BEGIN;

ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS telegram_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_senders_telegram_id
    ON senders(telegram_id)
    WHERE telegram_id IS NOT NULL;

COMMIT;
