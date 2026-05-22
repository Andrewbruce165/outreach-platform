---
status: partial
phase: 04-campaigns
source: [04-VERIFICATION.md]
started: 2026-05-22T09:30:00Z
updated: 2026-05-22T09:30:00Z
---

## Current Test

[awaiting human testing on DigitalOcean server]

## Tests

### 1. Apply migration 016 + run full Phase 4 pytest suite (Docker / Postgres environment)
expected: |
  Migration 016 applies cleanly (idempotent — safe to re-run on already-migrated DB). All Phase 4 tests pass across 14 files: test_migration_016, test_campaigns_model, test_campaign_router, test_sender_lock, test_campaign_schedule, test_queue_per_campaign_hours, test_template_render, test_campaign_enqueue_worker, test_queue_campaign_id, test_send_campaign, test_rotation_campaign, test_builtin_tools, test_campaign_webhooks, test_custom_tools_wiring. Phase 1/2/3 regression suite remains green.
command: |
  cd /root/apps/outreach-platform
  git pull
  docker compose up -d --build api listener
  docker compose exec api pytest \
    tests/test_migration_016.py tests/test_campaigns_model.py \
    tests/test_campaign_router.py tests/test_sender_lock.py \
    tests/test_campaign_schedule.py tests/test_queue_per_campaign_hours.py \
    tests/test_template_render.py tests/test_campaign_enqueue_worker.py \
    tests/test_queue_campaign_id.py tests/test_send_campaign.py \
    tests/test_rotation_campaign.py tests/test_builtin_tools.py \
    tests/test_campaign_webhooks.py tests/test_custom_tools_wiring.py -x -v
why_human: Local macOS env (Python 3.14 + SQLAlchemy 2.0.25 + no Docker/Postgres) cannot run pytest — project-wide constraint inherited from Phase 3 (see 03-VERIFICATION.md human_verification).
result: [pending]

### 2. Live end-to-end smoke: campaign create → start → enqueue → signal → webhook
expected: |
  1) POST /api/v1/campaigns with full body (name, agent_id, folder_id, sender_ids[], message_template='Привет, {{имя}}!', lead_webhook_url, work_hour_start=9, work_hour_end=20, timezone='Europe/Moscow') → 201.
  2) POST /api/v1/campaigns/{id}/start → 200 + status='running'.
  3) Within ~30s — CampaignEnqueueWorker tick sees folder contacts with tg_status='registered', INSERTs into message_queue + campaign_contact_assignments (per-campaign UNIQUE).
  4) queue.py worker tick picks pending → sends via Telethon with rendered template ('Привет, Иван!').
  5) Contact replies → built-in tool finish_conversation → UPDATE conversation.status='finished' + ai_enabled=false + POST to campaigns.finish_webhook_url with C-01 payload (event_type, campaign_id, conversation_id, workspace_id, contact{phone/name/username/source/custom}, reason, message_history_excerpt[20], timestamp).
  6) Sender lock: POST /api/v1/campaigns with the same sender_id when status='running' on another campaign → 409 SENDER_LOCK_CONFLICT.
  7) POST /api/v1/campaigns/{id}/pause → status='paused', queue worker SKIPs (INNER JOIN + WHERE status='running').
  8) Add contact to folder via POST /api/v1/folders/{id}/contacts → next CampaignEnqueueWorker tick tops up the queue (CAMP-09).
command: |
  # All requests require: -H "Authorization: Bearer $JWT" -H "Content-Type: application/json"

  # 1. Create campaign
  curl -X POST http://localhost:8000/api/v1/campaigns -d '{
    "name":"Smoke Test Campaign","agent_id":"$AGENT_ID","folder_id":"$FOLDER_ID",
    "sender_ids":["$SENDER_ID"],"message_template":"Привет, {{имя}}!",
    "lead_webhook_url":"https://webhook.site/$ID","work_hour_start":9,"work_hour_end":20,
    "timezone":"Europe/Moscow"}'

  # 2. Start campaign → 200 + status='running'
  curl -X POST http://localhost:8000/api/v1/campaigns/$CAMPAIGN_ID/start

  # 3-4. Wait 30-60s then inspect:
  docker compose exec db psql -U postgres -d outreach -c \
    "SELECT id, status, campaign_id FROM message_queue WHERE campaign_id='$CAMPAIGN_ID' LIMIT 5;"
  docker compose logs --tail=100 api | grep -i 'campaign_enqueue\|sent'

  # 5. Inspect signal + webhook
  docker compose exec db psql -U postgres -d outreach -c \
    "SELECT id, status, ai_enabled FROM conversations WHERE campaign_id='$CAMPAIGN_ID';"
  # check webhook.site dashboard for C-01 payload arrival

  # 6. Sender lock
  curl -X POST http://localhost:8000/api/v1/campaigns -d '{
    "name":"Conflict Campaign","agent_id":"$AGENT_ID","folder_id":"$FOLDER_ID",
    "sender_ids":["$SENDER_ID"]}'
  curl -X POST http://localhost:8000/api/v1/campaigns/$NEW_CAMPAIGN_ID/start
  # → expect 409 SENDER_LOCK_CONFLICT

  # 7. Pause
  curl -X POST http://localhost:8000/api/v1/campaigns/$CAMPAIGN_ID/pause
  # verify queue worker stops picking up: docker compose logs --tail=50 api

  # 8. Top-up via folder
  curl -X POST http://localhost:8000/api/v1/folders/$FOLDER_ID/contacts -d '{
    "phone":"+79991234567","first_name":"Иван"}'
  # wait next CampaignEnqueueWorker tick (30s) then check message_queue.campaign_id
why_human: |
  Requires live FastAPI + Postgres + Telethon session (real TG accounts) + Lovable workspace + webhook listener (e.g. webhook.site or n8n endpoint). End-to-end checks prove Goal Achievement at runtime — static analysis cannot verify CampaignEnqueueWorker tick behaviour, fire-and-forget webhook delivery, or Telethon dispatch. Also covers UI smoke: Lovable should render is_exhausted + attached_senders[].locked_by_campaign_id correctly.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps

### G1. Cosmetic: FastAPI version constructor string out of date
location: app/main.py:72
detail: `FastAPI(version="2.0.0-phase3")` while root endpoint at app/main.py:106 correctly returns `"2.0.0-phase4"`. Inconsistency only — /docs OpenAPI metadata and root response disagree. Не блокирует phase goal.
severity: cosmetic
status: failed
