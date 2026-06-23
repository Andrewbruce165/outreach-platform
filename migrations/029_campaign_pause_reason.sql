-- 029: auto-pause visibility — why a campaign stopped sending.
--
-- When a running campaign has no eligible sender left (pool empty or every
-- attached sender restricted/offline/auth-failed) while work remains, the
-- CampaignEnqueueWorker flips it to status='paused' and records the reason here
-- so the UI can surface "рассылка не идёт — надо что-то решать".
--
-- pause_reason is NULL for a manual pause (user-initiated) and set to a machine
-- code ('no_senders_attached' | 'senders_unavailable') for an auto-pause.
-- Cleared on start/resume.

ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS pause_reason VARCHAR(40),
    ADD COLUMN IF NOT EXISTS paused_at    TIMESTAMPTZ;
