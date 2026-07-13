-- 063: messages gains a nullable `buttons` JSONB column (quick task 260713-jmp).
-- Inbound @SpamBot messages that carry an inline/reply keyboard now persist their
-- button layout as a 2D array of {text} (rows → cols, matching Telethon's own
-- row/col addressing used by message.click(row, col)). Plain-text messages leave
-- `buttons` NULL — no behavior change from 260713-hiw.
-- `messages` is raw-SQL (mig 017), NO ORM model, so create_all never builds this
-- column — the migration is the only source. Idempotent: safe to re-run via the
-- auto-applier.

BEGIN;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS buttons JSONB;

COMMIT;
