---
status: partial
phase: 03-agents-ai-templates
source: [03-VERIFICATION.md]
started: 2026-05-21T23:53:16Z
updated: 2026-05-21T23:53:16Z
---

## Current Test

[awaiting human testing on DigitalOcean server]

## Tests

### 1. Run pytest suite on server (Docker Postgres environment)
expected: ~27 Phase 3 tests pass (12 from plan 03-01 + 15 from plan 03-02), 0 failures; Phase 1+2 regression suite remains green; migration 015 does not break older tests.
command: |
  cd /root/apps/outreach-platform
  git pull
  docker compose up -d --build api
  docker compose exec api pytest \
    tests/test_migration_015.py tests/test_ai_engine.py tests/test_listener.py \
    tests/test_rotation.py tests/test_queue_enqueue.py tests/test_senders.py \
    tests/test_agents.py tests/test_send.py -x -v
why_human: Local macOS env (Python 3.14 + SQLAlchemy 2.0.25 + no Docker/Postgres) cannot run pytest — project-wide constraint, not a Phase 3 failure.
result: [pending]

### 2. Live smoke test: full Phase 3 user flow
expected: All 6 curl steps respond as specified — create agent (201), duplicate name conflict (409 AGENT_NAME_DUPLICATE), duplicate endpoint (201 with "(copy)" suffix), send without ai_context_id (422), cross-workspace agent (404 AGENT_NOT_FOUND), successful send (200 EnqueueResponse). FastAPI version=2.0.0-phase3; /docs OpenAPI shows /api/v1/agents endpoints; migration 015 applied at startup.
command: |
  # 1. Create agent
  curl -X POST http://localhost:8000/api/v1/agents \
    -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
    -d '{"name":"Sales Agent","system_prompt":"be helpful","tone_of_voice":"friendly","faq":[{"question":"Q","answer":"A"}]}'
  # 2. Duplicate name → 409
  curl -X POST http://localhost:8000/api/v1/agents \
    -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
    -d '{"name":"Sales Agent"}'
  # 3. Duplicate endpoint → 201 + "Sales Agent (copy)"
  curl -X POST http://localhost:8000/api/v1/agents/$AGENT_ID/duplicate \
    -H "Authorization: Bearer $JWT"
  # 4. Send without ai_context_id → 422
  curl -X POST http://localhost:8000/api/v1/send \
    -H "Authorization: Bearer $JWT" \
    -d '{"recipient_phone":"+79991234567","message":"hi"}'
  # 5. Cross-workspace agent → 404
  curl -X POST http://localhost:8000/api/v1/send \
    -H "Authorization: Bearer $JWT_OTHER_WORKSPACE" \
    -d '{"ai_context_id":"$AGENT_ID","recipient_phone":"+79991234567","message":"hi"}'
  # 6. Successful send → 200 EnqueueResponse
  curl -X POST http://localhost:8000/api/v1/send \
    -H "Authorization: Bearer $JWT" \
    -d '{"ai_context_id":"$AGENT_ID","recipient_phone":"+79991234567","message":"hi"}'
why_human: Requires live FastAPI + Postgres + Supabase JWT setup + Lovable UI smoke. End-to-end runtime proof, not static analysis.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
