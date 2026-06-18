-- 024: campaign draft may be incomplete — agent_id/folder_id become nullable.
-- message_template уже nullable=False server_default='' (пустая строка = ок для драфта) — НЕ трогаем.
-- FK RESTRICT остаётся (nullable FK допустим).
-- DROP NOT NULL — no-op если колонка уже nullable, повторный прогон applier'а безопасен (идемпотентно).
ALTER TABLE campaigns ALTER COLUMN agent_id  DROP NOT NULL;
ALTER TABLE campaigns ALTER COLUMN folder_id DROP NOT NULL;
