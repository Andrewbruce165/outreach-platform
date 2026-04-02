-- Миграция: добавление document_webhook_url для обработки документов через внешний webhook
-- Дата: 2026-01-21

-- Добавляем поле document_webhook_url в таблицу ai_contexts
ALTER TABLE ai_contexts
ADD COLUMN IF NOT EXISTS document_webhook_url TEXT;

-- Комментарий к полю
COMMENT ON COLUMN ai_contexts.document_webhook_url IS 'URL webhook для отправки документов на внешнюю обработку (n8n и т.д.)';
