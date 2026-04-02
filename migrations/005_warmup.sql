-- migrations/005_warmup.sql
-- Таблицы для системы прогрева аккаунтов
BEGIN;

-- Пул аккаунтов для прогрева
CREATE TABLE IF NOT EXISTS warmup_pool (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_id   UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(sender_id)
);

-- Сессии диалогов между парами аккаунтов
CREATE TABLE IF NOT EXISTS warmup_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_a_id     UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    sender_b_id     UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    topic           TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'active',   -- active, completed
    messages_sent   INTEGER NOT NULL DEFAULT 0,
    target_messages INTEGER NOT NULL DEFAULT 6,
    next_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_sender_id  UUID REFERENCES senders(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_warmup_sessions_due
    ON warmup_sessions(status, next_message_at)
    WHERE status = 'active';

-- Отдельные warmup-сообщения (хранятся независимо от основной таблицы messages)
CREATE TABLE IF NOT EXISTS warmup_messages (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     UUID NOT NULL REFERENCES warmup_sessions(id) ON DELETE CASCADE,
    from_sender_id UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    to_sender_id   UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    message_text   TEXT NOT NULL,
    sent_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_warmup_messages_session
    ON warmup_messages(session_id, sent_at);

-- Для подсчёта дневного лимита по sender'у
CREATE INDEX IF NOT EXISTS idx_warmup_messages_daily
    ON warmup_messages(from_sender_id, sent_at);

COMMIT;
