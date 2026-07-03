-- 047_message_queue_priority_default.sql
-- WR-02: message_queue.priority/attempts/as_draft had NO DB DEFAULT; the
-- campaign_enqueue raw INSERT stored NULL. NULL priority sorts FIRST under
-- ORDER BY priority DESC → inverts documented "higher = processed first".
-- Set DB defaults + backfill existing NULLs. Idempotent.
ALTER TABLE message_queue ALTER COLUMN priority SET DEFAULT 0;
UPDATE message_queue SET priority = 0 WHERE priority IS NULL;

ALTER TABLE message_queue ALTER COLUMN attempts SET DEFAULT 0;
UPDATE message_queue SET attempts = 0 WHERE attempts IS NULL;

ALTER TABLE message_queue ALTER COLUMN as_draft SET DEFAULT false;
UPDATE message_queue SET as_draft = false WHERE as_draft IS NULL;
