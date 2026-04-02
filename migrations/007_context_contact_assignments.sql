-- migrations/007_context_contact_assignments.sql
-- Persistent mapping (context_id, contact_phone) → sender_id for account rotation.
-- Ensures the same sender account is reused for all subsequent messages to a contact
-- within a given AI context. Reassignment happens automatically if the sender goes inactive.

BEGIN;

CREATE TABLE IF NOT EXISTS context_contact_assignments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_id    UUID NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    contact_phone VARCHAR(20) NOT NULL,
    sender_id     UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_context_contact UNIQUE (context_id, contact_phone)
);

-- Primary lookup: (context_id, contact_phone) → sender_id
CREATE INDEX IF NOT EXISTS idx_cca_context_contact
    ON context_contact_assignments(context_id, contact_phone);

-- Audit / cascade search when a sender is deactivated
CREATE INDEX IF NOT EXISTS idx_cca_sender
    ON context_contact_assignments(sender_id);

COMMIT;
