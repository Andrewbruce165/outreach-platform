-- Add role column to senders table
-- role = 'sender'  -> main account for sending messages (default)
-- role = 'checker' -> disposable account for bulk phone number checking only

ALTER TABLE senders
ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'sender';

COMMENT ON COLUMN senders.role IS 'sender = основной аккаунт (отправка), checker = проверщик номеров';

-- Index to speed up cross-sender lookup in contacts_cache:
-- SELECT is_registered FROM contacts_cache WHERE phone = :phone AND is_registered = false
CREATE INDEX IF NOT EXISTS idx_contacts_cache_phone_registered
ON contacts_cache (phone, is_registered);

-- Rollback:
-- ALTER TABLE senders DROP COLUMN IF EXISTS role;
-- DROP INDEX IF EXISTS idx_contacts_cache_phone_registered;
