-- Migration: Add UNIQUE constraint to messages table
-- Date: 2026-01-20
-- Purpose: Prevent duplicate message storage from Telegram catchup events
--
-- This migration adds a UNIQUE constraint on (conversation_id, telegram_message_id)
-- to prevent the same Telegram message from being saved multiple times.

-- Step 1: Remove any existing duplicates before adding the constraint
-- (Keep the oldest message for each duplicate set)
WITH duplicates AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY conversation_id, telegram_message_id
            ORDER BY created_at ASC
        ) as rn
    FROM messages
    WHERE telegram_message_id IS NOT NULL
)
DELETE FROM messages
WHERE id IN (
    SELECT id FROM duplicates WHERE rn > 1
);

-- Step 2: Add the UNIQUE constraint
ALTER TABLE messages
ADD CONSTRAINT messages_conversation_telegram_unique
UNIQUE (conversation_id, telegram_message_id);

-- Step 3: Add index for better query performance on telegram_message_id
CREATE INDEX IF NOT EXISTS idx_messages_telegram_message_id
ON messages(telegram_message_id)
WHERE telegram_message_id IS NOT NULL;

-- Step 4: Add index for conversation_id + created_at (for message history queries)
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
ON messages(conversation_id, created_at DESC);

-- Rollback script (if needed):
-- ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_conversation_telegram_unique;
-- DROP INDEX IF EXISTS idx_messages_telegram_message_id;
-- DROP INDEX IF EXISTS idx_messages_conversation_created;
