-- migrations/020_contacts_cache_unique.sql
-- Закрытие пробела в исходной схеме contacts_cache (унаследовано из
-- /root/apps/telegram-api при форке): app/services/checker.py делает
-- `INSERT INTO contacts_cache ... ON CONFLICT (sender_id, phone) DO UPDATE`,
-- но соответствующего UNIQUE constraint в БД никогда не было — ни одна из
-- миграций 003/004/010–019 его не создаёт. Без этого constraint Postgres
-- кидает InvalidColumnReferenceError, cache save падает в warning, и
-- 7-дневный TTL-кеш checker'а фактически мёртв (каждый recheck идёт живым
-- Telethon-запросом, повышает риск FloodWait).
--
-- Эффект: ON CONFLICT находит таргет, кэш сохраняется/обновляется по
-- (sender_id, phone), TTL-кэш начинает работать.

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_cache_sender_phone_unique
    ON contacts_cache(sender_id, phone);

COMMIT;
