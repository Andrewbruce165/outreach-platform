---
phase: 16-rag-knowledge-bases-for-agents
plan: 03
subsystem: api
tags: [rag, knowledge-bases, fastapi, pgvector, multipart-upload, workspace-isolation, openapi, lovable-handoff]

# Dependency graph
requires:
  - phase: 16-01-infra-data-model-test-scaffold
    provides: knowledge_bases / kb_documents / kb_chunks / agent_knowledge_bases tables + ORM + RED CRUD/upload tests
  - phase: 16-02-ingest-pipeline-and-worker
    provides: KnowledgeIngestWorker (claims status='pending' kb_documents) + kb_ingest.embed_texts + kb_search config knobs
  - phase: 03-agents-ai-templates
    provides: AuthCtx/auth_dep workspace-scoping pattern + ai_contexts (agent) table = the M:N attach point
  - phase: 02-tg-accounts-contacts
    provides: contacts.py UploadFile multipart + 413 FILE_TOO_LARGE + 202-accepted idiom (mirrored)
provides:
  - app/routers/knowledge_bases.py — workspace-scoped KB CRUD + docs (upload/paste/list/reindex/delete) + manual search + agent attach/detach + reverse list, all under auth_dep
  - app/schemas/knowledge_bases.py — Pydantic v2 request/response models incl. the D-09 aggregate + D-10 per-doc shapes
  - knowledge_bases router registered in app/main.py
  - regenerated lovable-handoff/openapi.json (+ types/api.ts) carrying the 9 KB paths
affects: [16-04-search-tool-wiring, 16-05-frontend-surfaces]

# Tech tracking
tech-stack:
  added: []  # all deps (pgvector/tiktoken/pypdf/python-docx) pinned in 16-01
  patterns:
    - "COUNT(*) FILTER (...) one-pass D-09 aggregate over kb_documents (mirrors campaigns.py pool_health)"
    - "Multipart UploadFile → 202 + status='pending' row (mirrors contacts.py import); worker indexes async"
    - "Manual search: prefer shared app.services.kb_search.kb_search when 16-04 lands, self-contained cosine fallback now"
    - "M:N attach via raw INSERT ... ON CONFLICT (agent_id, kb_id) DO NOTHING; reverse list via JOIN ai_contexts"
    - "autouse KB-state cleanup fixture so a committed pending doc never leaks into the global-claim ingest-worker test"

key-files:
  created:
    - app/schemas/knowledge_bases.py
    - app/routers/knowledge_bases.py
  modified:
    - app/main.py
    - tests/test_knowledge_bases.py
    - tests/test_kb_ingest.py
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts

key-decisions:
  - "List + reverse-list endpoints return bare JSON arrays (the RED tests iterate the body directly), not {items:[...]} envelopes"
  - "KB status derived: failed > processing > indexed > empty (D-09)"
  - "Upload max 20 MB (413 FILE_TOO_LARGE); ext→source_kind whitelist pdf/docx/txt/md/csv else 422 UNSUPPORTED_FILE_TYPE"
  - "Reindex flips status='pending' + clears error (worker delete-then-insert is idempotent per 16-02)"
  - "openapi regenerated OFFLINE via app.openapi() from the freshly-built image — same spec the export script scrapes, without deploying un-reviewed code to the live prod api (deploy is user-gated)"
  - "Partial-staged app/main.py (only the knowledge_bases import + include_router hunks); left the parallel Phase-15 CORS allow_methods=PUT hunk unstaged"

patterns-established:
  - "KB endpoints mirror the agents.py auth_dep + _load helper(404) workspace-isolation contract verbatim"
  - "Per-doc aggregate computed in ONE grouped FILTER query, not N per-KB SELECTs"

requirements-completed: [KB-01, KB-02, KB-03, KB-04]

# Metrics
duration: 12min
completed: 2026-06-30
---

# Phase 16 Plan 03: API Endpoints & Handoff Summary

**Workspace-scoped Knowledge-Base API live — KB CRUD, multipart upload + paste-text (202, pending row for the worker), per-document list, reindex/delete, the D-09 COUNT(*) FILTER aggregate, a manual cosine `search_knowledge_base`, and agent↔KB attach/detach + reverse list — all under `auth_dep`, router registered, openapi handoff regenerated with the 9 KB paths. KB-01/02/03/04 tests GREEN.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-06-30T11:58:28Z
- **Completed:** 2026-06-30T12:10:53Z
- **Tasks:** 3
- **Files modified:** 7 (2 created, 5 modified)

## Accomplishments
- `app/schemas/knowledge_bases.py` — full Pydantic v2 surface: `KnowledgeBaseCreate/Update/Response` (with the five D-09 aggregate fields + derived `status`), `KbDocumentResponse` (D-10), paste request, `KbSearchRequest/Hit/Response`, and `AgentKbAttachRequest` + `AgentForKbResponse` (reverse M:N — carries both `id` and `agent_id`).
- `app/routers/knowledge_bases.py` — 13 endpoints under `prefix="/api/v1/knowledge-bases"`, every handler `Depends(auth_dep)` + workspace-filtered; `_load_kb`/`_load_doc`/`_load_agent` helpers 404 on cross-workspace access. D-09 aggregate computed via a single `COUNT(*) FILTER (...)` grouped query over `kb_documents`. Upload mirrors contacts.py (`UploadFile`, 413 at 20 MB, ext→source_kind whitelist or 422, `status='pending'` + `raw_content` + `size_bytes`). Manual search embeds the query and cosine-ranks `kb_chunks` (`<=> :qvec`, `LIMIT top_k`, `distance <= kb_search_max_distance`), preferring the shared `kb_search` helper when 16-04 lands.
- Router registered in `app/main.py` (partial-staged — Phase-15 CORS hunk left untouched).
- Regenerated `lovable-handoff/openapi.json` + `types/api.ts` — adds the 9 KB paths; the UI-SPEC drift check passes.
- KB-01 (isolation), KB-02 (upload→pending), KB-03 (aggregate), KB-04 (attach/detach + reverse list) tests turned GREEN; full suite 831 passed.

## Task Commits

Each task was committed atomically:

1. **Task 1: Pydantic KB schemas** - `73daf73` (feat)
2. **Task 2: KB router (CRUD/docs/search/M:N) + registration + tests** - `f8eed77` (feat)
3. **Task 3: Regenerate openapi handoff + types** - `d1e9e9f` (chore)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP/REQUIREMENTS) — see final docs commit.

## Files Created/Modified
- `app/schemas/knowledge_bases.py` - KB request/response models (D-09 aggregate inline, D-10 per-doc, search, reverse-M:N)
- `app/routers/knowledge_bases.py` - 13 workspace-scoped endpoints (CRUD + docs + reindex + delete + manual search + attach/detach + reverse list)
- `app/main.py` - `knowledge_bases` router import + `include_router` (partial-staged; Phase-15 CORS PUT hunk left unstaged)
- `tests/test_knowledge_bases.py` - added autouse KB-state cleanup fixture (isolation fix)
- `tests/test_kb_ingest.py` - added autouse KB-state cleanup fixture (isolation fix)
- `lovable-handoff/openapi.json` - regenerated (strict superset, +9 KB paths)
- `lovable-handoff/types/api.ts` - regenerated companion types (openapi-typescript@7)

## Decisions Made
- **Bare JSON arrays for list + reverse-list.** The Wave-0 RED tests iterate `response.json()` directly (`[kb["id"] for kb in list_b.json()]`, `[a["id"] for a in agents_for_kb.json()]`), so `GET ""` and `GET "/{id}/agents"` return bare arrays, not `{items:[...]}` envelopes. The list-envelope schemas (`KnowledgeBaseListResponse` etc.) remain available for callers that want them.
- **D-09 status derivation:** `failed > processing > indexed > empty`, computed router-side from the FILTER aggregate.
- **`AgentForKbResponse` carries both `id` and `agent_id`** (same value) — the test consumes `a["id"]`; `agent_id`/`agent_name` keep the response self-describing for the frontend Agents tab.
- **openapi regenerated offline via `app.openapi()`** from the freshly-built api image (test-overlay → ephemeral DB, never prod), producing the identical spec the export-handoff script scrapes from a running server — without `docker compose up -d api`, which would have replaced the live prod container with un-reviewed code (deploy is user-gated per CLAUDE.md). `types/api.ts` + the UI-SPEC drift check were then run exactly as the script does.
- **Partial-stage of `app/main.py`** — only the two `knowledge_bases` hunks were staged (`git add` whole file, then `git apply --cached -R` of a crafted CORS-only patch to unstage the Phase-15 `allow_methods=PUT` hunk). Verified staged diff carries no `PUT`; the Phase-15 hunk stays in the working tree (mirrors 16-01/16-02 partial-stage of shared files).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Cross-test pollution leaked a pending doc into the ingest-worker test**
- **Found during:** Task 2 (running the full suite after the router landed)
- **Issue:** The new upload endpoint commits a real `kb_documents` row `status='pending'`. The `KnowledgeIngestWorker` claims pending docs *globally* (`WHERE status='pending' ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED`). On the shared session DB, `test_kb_ingest.py` runs before `test_kb_ingest_worker.py` (alphabetical) and left an earlier-`created_at` pending doc, so the worker test's `_tick()` claimed *that* doc instead of its own freshly-seeded one — its doc stayed `pending` and the assertion failed (`expected indexed, got pending`). It was GREEN in isolation, RED only in the full suite. Not a production-code defect — a test-isolation gap the new endpoint surfaced.
- **Fix:** Added an `autouse` `_cleanup_kb_state` fixture to `tests/test_kb_ingest.py` and `tests/test_knowledge_bases.py` that purges `kb_chunks`/`kb_documents`/`agent_knowledge_bases`/`knowledge_bases` after each test — mirroring the identical autouse cleanup already present in `test_kb_ingest_worker.py` (whose own comment documents the global-claim leak hazard).
- **Files modified:** tests/test_kb_ingest.py, tests/test_knowledge_bases.py
- **Verification:** `pytest tests/test_kb_ingest.py tests/test_knowledge_bases.py tests/test_kb_ingest_worker.py` → 6 passed; full suite went from 6 failed → 5 failed (worker test now GREEN in context). The 5 remaining failures are pre-existing out-of-scope RED scaffolds (see Out-of-Scope Discoveries).
- **Committed in:** `f8eed77` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug — test isolation).
**Impact on plan:** The fix is test-only and was directly caused by this plan's new upload endpoint; no production behaviour changed, no scope creep.

## Issues Encountered
- The committed `lovable-handoff/openapi.json` (Jun 29) was slightly stale: it was missing three endpoints already in the code (`/campaigns/{id}/rerender-pending`, `/conversations/delete`, `/folders/{id}/stats`). The regeneration is a strict superset (0 paths removed, 12 added: 9 KB + those 3), so the "249 deletions" in the diff are pure jq key re-ordering of existing path bodies, not lost content — verified by diffing the path key-sets old-vs-new.

## Out-of-Scope Discoveries (not fixed — scope boundary)
- **Full suite: 5 failed / 831 passed / 1 skipped.** None are regressions from this plan:
  - 4 are Phase-16 **Wave-4 (16-04)** RED scaffold tests: `test_kb_search.py` (2 — `app.services.kb_search` ModuleNotFound) and `test_ai_engine_kb_tool.py` (2 — `build_kb_tool_spec` / data-tool dispatch ImportError). 16-04 owns those modules.
  - 1 is a **Phase-15** RED test: `test_warmup_worker.py::test_restricted_sender_excluded` (assertion literally "restriction clause not added yet (WARM-14)"), driven by the uncommitted Phase-15 WIP in the working tree (`app/services/warmup.py`). This plan never touches warmup.

## Known Stubs
None. The manual-search endpoint's `kb_search` fallback is a complete cosine query (not a stub) — it self-serves results now and transparently switches to the shared `app.services.kb_search.kb_search` helper the moment 16-04 lands it (the import is attempted first). The one `results=[]` branch is the legitimate empty-embedding guard.

## User Setup Required
None for this plan's automated scope. NOTE for the OPS deploy step (user-gated, after merge): `docker compose up -d --build api` picks up the new router (the api image was built locally during this plan for the offline openapi export, but the live prod container was deliberately NOT recreated). `OPENAI_API_KEY` must be present for real embeds during manual search (already required by ai_engine); `OPENAI_EMBEDDING_MODEL` is optional (defaults to `text-embedding-3-small`).

## Next Phase Readiness
- The full user-facing KB backend surface is live: 16-05 (frontend) can build the index page, the D-09 detail header + D-10 documents tab + Search tab + Agents reverse-list against `lovable-handoff/openapi.json` (now carrying all 9 KB paths + types).
- The manual-search endpoint already imports `app.services.kb_search.kb_search` when present — 16-04 only needs to land that module to make the endpoint (and the AI data-tool) use the canonical implementation; the endpoint keeps working in the meantime.
- KB-01/02/03/04 closed; KB-05 (search tool wiring) and KB-06 (search isolation) remain for 16-04.

## Self-Check: PASSED

All created/modified files present on disk:
- `app/schemas/knowledge_bases.py`, `app/routers/knowledge_bases.py` — FOUND.
- `app/main.py` (router registered), `tests/test_knowledge_bases.py` + `tests/test_kb_ingest.py` (cleanup fixtures), `lovable-handoff/openapi.json` + `types/api.ts` (KB paths) — modifications present.

All task commits exist:
- `73daf73` (Task 1), `f8eed77` (Task 2), `d1e9e9f` (Task 3) — FOUND.

KB contract tests GREEN: `tests/test_knowledge_bases.py` 3 passed + `tests/test_kb_ingest.py` 1 passed; worker test GREEN in full suite.

---
*Phase: 16-rag-knowledge-bases-for-agents*
*Completed: 2026-06-30*
