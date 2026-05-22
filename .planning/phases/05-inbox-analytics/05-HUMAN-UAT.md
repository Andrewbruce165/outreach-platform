---
status: partial
phase: 05-inbox-analytics
source: [05-VERIFICATION.md]
started: 2026-05-22T00:00:00Z
updated: 2026-05-22T00:00:00Z
---

## Current Test

[awaiting human testing on VPS deploy]

## Tests

### 1. Run server-side pytest for all Phase 5 tests after deploy
expected: All ~94 Phase 5 tests pass — `pytest tests/test_phase5_*.py -x -q` on VPS. Covers: migration_017 (11), inbox (14), manager_mode (5), send_takeover (6), bot_filter (12), analytics (12), analytics_correctness (8), llm_logger (11), llm_logger_no_block_on_error (3), llm_calls_endpoint (8), plus regression in migration_016 / campaign_router / ai_engine.
result: [pending]

### 2. Apply migration 017 to fresh DB
expected: `\d+ conversations` shows status CHECK with 7 values incl `bot_ignored`; `\d+ llm_calls` has 15 columns + 2 indexes; 3 composite indexes on conversations present; `messages` table exists with `workspace_id` column.
result: [pending]

### 3. Manager-mode auto-takeover from Lovable UI (D-04)
expected: Open inbox in Lovable, send message from manager UI on an active dialog → dialog flips to `status='manual'`, `ai_enabled=false`, `paused_reason='Manager sent message via UI'`; any pending queue items for that `recipient_phone` become `status='failed'`; manager message arrives in Telegram.
result: [pending]

### 4. Proactive bot filter on live Telegram interaction
expected: Send Telegram message to a sender from a verified bot account (SpamBot id=178220800 or any @BotFather bot) → listener creates conversation `status='bot_ignored'`, `ai_enabled=false`, stores inbound message, `ai_engine.generate_response` NOT invoked; for 178220800 specifically delegate to `_handle_antispam_signal` (D-08 safety net) pauses sender lifecycle and cancels ALL queue items for that sender.
result: [pending]

### 5. Analytics workspace endpoint correctness
expected: Hit `GET /api/v1/analytics/workspace` with auth_headers on a workspace with seeded sent/replied/lead/finished data → response `{sent, replied:{conversation_count, message_count}, leads, finishes}` reflects only that workspace's counts; `bot_ignored` conversations excluded; leads strict EQ (does NOT include finished).
result: [pending]

### 6. End-to-end LLM call log on real OpenAI dialog
expected: Trigger AI response on a dialog → hit `GET /api/v1/conversations/{id}/llm-calls` → response shows 1 row per turn (or 2 if custom tools fired second OpenAI call); `prompt` JSONB has full `request_params`, `response_text` matches AI reply, `prompt_tokens`/`completion_tokens`/`total_tokens` populated, `latency_ms` ~= actual round-trip. Warmup-LLM calls must NOT appear (D-12 contract).
result: [pending]

### 7. Prompt-leak guard on live logs (T-05-03-PROMPT-LEAK)
expected: Inspect application logs `docker logs api 2>&1 | grep -i prompt` after a trigger of `generate_response` with system prompt containing distinctive text → distinctive prompt text NEVER appears in logs; only `conversation_id` + exception text on errors.
result: [pending]

## Summary

total: 7
passed: 0
issues: 0
pending: 7
skipped: 0
blocked: 0

## Gaps
