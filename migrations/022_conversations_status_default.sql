-- migrations/022_conversations_status_default.sql
-- Исправляет некорректный server_default на conversations.status.
--
-- Корень: app/models/__init__.py:257 объявлял:
--   status = Column(String(20), default="active", server_default="'active'")
-- (одинарные кавычки внутри Python-строки). SQLAlchemy транслировал
-- server_default как SQL-литерал, получалось DDL:
--   DEFAULT '''active'''::character varying
-- что в Postgres значит строка из 8 символов `'active'` (с апострофами
-- как символами). CHECK constraint требует status IN ('active', ...) —
-- 6 символов без апострофов. Mismatch → CheckViolationError на любом
-- INSERT INTO conversations без явного status.
--
-- Последствия до фикса:
--   - listener не мог создать conversation на входящее сообщение → AI
--     никогда не отвечал на reply'и
--   - queue worker не мог записать outbound message → переписка
--     не появлялась в inbox несмотря на успешную отправку в TG
--
-- Модель тоже исправлена в этом коммите: server_default="active" (без
-- кавычек, как у всех других статусных колонок).

BEGIN;

ALTER TABLE conversations ALTER COLUMN status SET DEFAULT 'active';

COMMIT;
