---
phase: 16-rag-knowledge-bases-for-agents
plan: 04
subsystem: retrieval
tags: [rag, knowledge-bases, pgvector, cosine-distance, openai-tools, function-calling, ai-engine, workspace-isolation]

# Dependency graph
requires:
  - phase: 16-01-infra-data-model-test-scaffold
    provides: kb_chunks / agent_knowledge_bases tables + ORM (Vector(1536)) + HNSW vector_cosine_ops index + RED kb_search / ai_engine_kb_tool tests
  - phase: 16-02-ingest-pipeline-and-worker
    provides: kb_ingest.embed_texts (batched AsyncOpenAI embedder) + openai_embedding_model / kb_search_top_k / kb_search_max_distance config knobs
  - phase: 16-03-api-endpoints-and-handoff
    provides: the manual POST /{kb_id}/search router that already imports app.services.kb_search.kb_search(db, workspace_id, kb_ids, query, top_k, max_distance)
  - phase: 04-agents-ai-templates
    provides: ai_engine.generate_response two-pass tool-dispatch loop (builtin_signals vs custom_calls split, role:tool message + second completion)
provides:
  - app/services/kb_search.py — embed_query (patchable single-query embedder) + attached_kb_ids (workspace_id+kb_ids from agent_knowledge_bases) + kb_search (pgvector cosine_distance over attached KBs, workspace-filtered)
  - app/services/ai_engine.py — SEARCH_KB_TOOL spec + build_kb_tool_spec(has_kb) gate + search_knowledge_base data-tool dispatch branch (local vector search, two-pass continuation, no conversation.status change)
affects: [16-05-frontend-surfaces]

# Tech tracking
tech-stack:
  added: []  # pgvector / openai client all pinned in 16-01/16-02
  patterns:
    - "pgvector cosine_distance ORDER BY ascending (Pitfall 4 — distance, lower=better; never invert to similarity)"
    - "WHERE workspace_id AND kb_id.in_(...) — defence-in-depth workspace isolation (KB-06)"
    - "Module-level embed_query so tests monkeypatch app.services.kb_search.embed_query without OpenAI"
    - "DATA tool (search_knowledge_base) deliberately NOT in BUILT_IN_TOOL_NAMES → lands in custom_calls → resolved LOCALLY then continue (NOT execute_webhook)"
    - "attached_kb_ids derives workspace_id from the agent's attach rows so the ai_engine legacy get_context path (no workspace_id in context dict) is still isolated"
    - "Empty hits → explicit {results:[], note:'no relevant passages found'} so the model inherits existing off-topic behaviour (Pitfall 5)"

key-files:
  created:
    - app/services/kb_search.py
  modified:
    - app/services/ai_engine.py

key-decisions:
  - "kb_search signature is kb_search(db, workspace_id, kb_ids, query, top_k, max_distance) — NOT the plan's (workspace_id, agent_id, ...). Both real consumers (16-03 router + both RED tests) pass kb_ids directly; agent→kb_ids resolution lives in the separate attached_kb_ids helper."
  - "attached_kb_ids returns (workspace_id, kb_ids) so the ai_engine legacy get_context path (which omits workspace_id) stays workspace-isolated without a second lookup."
  - "Lazy import of app.services.kb_search inside generate_response to avoid the import cycle (kb_search → kb_ingest → ai_engine.client)."
  - "build_kb_tool_spec(has_kb) helper added (the RED test imports it by name) instead of inlining the gate."
  - "33 full-suite failures under random test ordering proven pre-existing (baseline reproduces them; deterministic order = clean)."

patterns-established:
  - "RAG retrieval = one shared kb_search helper consumed by both the manual-search endpoint AND the AI data-tool (single source of truth)"
  - "A data-tool (returns data, model continues) vs a signal-tool (terminates / flips status) is distinguished purely by membership in BUILT_IN_TOOL_NAMES + a local-resolve `continue` branch"

requirements-completed: [KB-05, KB-06]

# Metrics
duration: 7min
completed: 2026-06-30
---

# Phase 16 Plan 04: Search Tool Wiring Summary

**Retrieval wired end-to-end: `app/services/kb_search.py` runs the pgvector cosine-distance query over the union of an agent's attached KBs (workspace-filtered, top-K + distance threshold), and `ai_engine.generate_response` offers a gated `search_knowledge_base` DATA tool that — only when the agent has ≥1 attached KB (D-04) — resolves locally via `kb_search`, appends a `role:"tool"` message with the chunks, and lets the existing two-pass flow continue the reply WITHOUT touching `conversation.status`. KB-05 (retrieval influences answers) + KB-06 (search isolation) tests GREEN; the 20 existing signal-tool tests unaffected.**

## Performance

- **Duration:** ~7 min
- **Tasks:** 2
- **Files modified:** 2 (1 created, 1 modified)

## Accomplishments
- `app/services/kb_search.py`:
  - `embed_query(query)` — single-query embedder delegating to `kb_ingest.embed_texts` (same AsyncOpenAI client + embedding model as ingest, so query and chunk vectors are comparable). Module-level so tests monkeypatch `app.services.kb_search.embed_query`.
  - `attached_kb_ids(db, agent_id) -> (workspace_id, kb_ids)` — reads `agent_knowledge_bases`; derives the workspace from the attach rows so the caller doesn't need to know it. Zero attached → `(None, [])`.
  - `kb_search(db, workspace_id, kb_ids, query, top_k=None, max_distance=None)` — `KbChunk.embedding.cosine_distance(query_vec)` in both the ORDER BY and the distance label, `WHERE workspace_id AND kb_id.in_(kb_ids)` (KB-06 defence-in-depth), `LEFT JOIN kb_documents` for `document_name`, `LIMIT top_k`, then drops `distance > max_distance` (Pitfall 4). Empty `kb_ids` → `[]` with no embed/DB call. Signature matches the import already done by `app/routers/knowledge_bases.py` (16-03) exactly.
- `app/services/ai_engine.py`:
  - `SEARCH_KB_TOOL` + `SEARCH_KB_TOOL_NAME` constants (deliberately NOT added to `BUILT_IN_TOOL_NAMES`).
  - `build_kb_tool_spec(has_kb)` → `[SEARCH_KB_TOOL]` iff `has_kb` else `[]` (D-04 gate; the RED test imports this helper by name).
  - In `generate_response`: resolves `agent_id` (`context["agent_id"]` or the legacy `context_id`), calls `attached_kb_ids` to get `(kb_workspace_id, kb_ids)`, sets `has_kb`, and appends the KB tool to `all_tools` only inside the `has_kb` gate.
  - Dispatch branch (in the per-custom-call loop, BEFORE the webhook lookup): `if func_name == SEARCH_KB_TOOL_NAME:` → `await kb_search.kb_search(...)`, writes `tool_results[tool_call.id]` (the `{results:[...]}` JSON, or the no-passages note on empty), then `continue` — so the existing two-pass block appends the `role:"tool"` message and runs the second completion UNCHANGED. `conversation.status` is never touched. Both the lookup and the search are wrapped defensively so a KB failure degrades to the no-passages note instead of crashing the reply (Pitfall 5).

## Task Commits

Each task was committed atomically (specific files only — never `git add -A`; the parallel uncommitted Phase-15 warmup changes were left untouched):

1. **Task 1: kb_search.py — cosine query over attached KBs, workspace-filtered** — `2f404d9` (feat)
2. **Task 2: search_knowledge_base data-tool in ai_engine.generate_response** — `94f4220` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP/REQUIREMENTS) — see final docs commit.

## Files Created/Modified
- `app/services/kb_search.py` — `embed_query` + `attached_kb_ids` + `kb_search` (created)
- `app/services/ai_engine.py` — `SEARCH_KB_TOOL`/`SEARCH_KB_TOOL_NAME`/`build_kb_tool_spec` constants + gated tool registration + the local-resolve dispatch branch in `generate_response`

## Decisions Made
- **`kb_search` signature follows the real callers, not the plan's prose.** The plan's `<interfaces>` block sketched `kb_search(db, workspace_id, agent_id, query, ...)`, but BOTH actual consumers pass `kb_ids` directly: 16-03's router already does `kb_search(db=, workspace_id=, kb_ids=[kb_id], ...)`, and both RED tests call `kb_search(db=, workspace_id=, kb_ids=[...], ...)`. Adopting the plan's `agent_id` signature would have broken the existing 16-03 import and the RED tests. The agent→kb_ids resolution lives in a separate `attached_kb_ids` helper that ai_engine calls first (Rule 3 — blocking-issue fix to match the established contract).
- **`attached_kb_ids` returns `(workspace_id, kb_ids)`.** The ai_engine KB test seeds a random `conversation_id` (no conversation row) so `generate_response` falls through to the legacy `get_context(context_id)` path, whose context dict has NO `workspace_id`/`agent_id`. Deriving the workspace from the agent's attach rows keeps the search workspace-isolated on that path without an extra query.
- **Lazy `import app.services.kb_search` inside `generate_response`.** `kb_search → kb_ingest → ai_engine.client` is a cycle at module-import time; a function-local import breaks it (and matches the engine's existing resilient lazy-import style).
- **`build_kb_tool_spec(has_kb)` helper** added because the RED `test_tool_gated_on_attached_kb` imports it by name and asserts `[tool] iff has_kb else []`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `kb_search` signature reconciled with the real callers**
- **Found during:** Task 1 (reading the 16-03 router import + the RED tests before writing the module).
- **Issue:** The plan's interface sketch used `kb_search(..., agent_id, ...)`, but 16-03's already-committed router (`app/routers/knowledge_bases.py`) imports and calls `kb_search(db=, workspace_id=, kb_ids=[kb_id], ...)`, and both RED tests call it with `kb_ids=[...]`. Implementing the plan's signature verbatim would have broken the existing 16-03 import and failed the RED tests.
- **Fix:** Implemented `kb_search(db, workspace_id, kb_ids, query, top_k, max_distance)` to match the live contract, and put the agent→kb_ids+workspace resolution in a separate `attached_kb_ids` helper that ai_engine calls before `kb_search`.
- **Files modified:** app/services/kb_search.py, app/services/ai_engine.py
- **Verification:** `tests/test_kb_search.py` (2) + `tests/test_ai_engine_kb_tool.py` (2) GREEN; 16-03's `tests/test_knowledge_bases.py` still GREEN (the router now uses the shared helper).
- **Committed in:** `2f404d9` (Task 1) + `94f4220` (Task 2)

---

**Total deviations:** 1 auto-fixed (1 blocking — signature reconciliation). No code-behaviour deviation from the plan's intent; only the helper boundary moved to honour the established contract.

## Issues Encountered
- **The full suite reports 33 failures under RANDOM test ordering — proven PRE-EXISTING, not caused by this plan.** The default `pytest-randomly` shuffle triggers cross-test DB-state pollution in the senders/campaigns/sender-lock/rerender cluster. Proof: (a) each of those files passes 21/21, 5/5, 8/8, etc. in isolation on this code; the focused cluster of all 7 files passes 64/65 (the 1 = the Phase-15 warmup RED). (b) The 16-03 BASELINE (my two files reverted) reproduces the same shuffle-only failures, and under deterministic order (`-p no:randomly`) the baseline is `5 failed / 831 passed` (the 5 = my 4 KB RED + the Phase-15 warmup RED). (c) MY code under deterministic order is `1 failed / 835 passed` — i.e. my 4 KB tests went RED→GREEN, +4 passing, and the only remaining failure is the known Phase-15 warmup RED. This plan adds zero regressions; the shuffle pollution is an out-of-scope test-isolation fragility in the senders suite, logged here for a future hardening pass.

## Out-of-Scope Discoveries (not fixed — scope boundary)
- **`test_warmup_worker.py::test_restricted_sender_excluded`** — a Phase-15 RED test (assertion message: "restriction clause not added yet (WARM-14)"), driven by the uncommitted Phase-15 WIP in the working tree (`app/services/warmup.py`). This plan never touches warmup. Documented identically in the 16-02 and 16-03 summaries. Logged to the phase's deferred items, not fixed.
- **Random-order cross-test pollution in the senders/campaigns cluster** (see Issues Encountered) — pre-existing, reproduces on the baseline; a test-isolation hardening task, out of scope for this retrieval-wiring plan.

## Known Stubs
None. The single `{results:[], note:"no relevant passages found"}` branch is the intentional Pitfall-5 empty-result fallback (so the model inherits the existing off-topic behaviour), not a stub. The `attached_kb_ids` zero-KB `(None, [])` return and `kb_search`'s `[]`-on-empty-kb_ids are intentional fast paths, not stubs.

## User Setup Required
None for this plan's automated scope. The retrieval helper + AI data-tool are pure code; they ride the existing `OPENAI_API_KEY` / `OPENAI_EMBEDDING_MODEL` already required by ingest. NOTE for the OPS deploy step (user-gated per CLAUDE.md): `docker compose up -d --build api` (and `listener`) picks up the new `kb_search` module and the ai_engine tool wiring; no migration is needed (16-01 already shipped the tables + HNSW index).

## Next Phase Readiness
- Retrieval is live end-to-end: the manual `POST /{kb_id}/search` endpoint (16-03) now transparently uses the canonical `kb_search`, and the AI answerer retrieves on demand when ≥1 KB is attached.
- 16-05 (frontend) can wire the Search tab against the manual-search endpoint and surface the agent↔KB attach UI knowing the AI side already consumes the attach state (D-04 gate).
- KB-05 (retrieval visibly influences answers) + KB-06 (search workspace isolation) closed. All six KB requirements (KB-01..KB-06) are now GREEN across the phase.

## Self-Check: PASSED

All created/modified files present on disk:
- `app/services/kb_search.py` (`embed_query` + `attached_kb_ids` + `kb_search`) — FOUND.
- `app/services/ai_engine.py` (`SEARCH_KB_TOOL` / `build_kb_tool_spec` / `search_knowledge_base` dispatch) — modifications present.

All task commits exist:
- `2f404d9` (Task 1), `94f4220` (Task 2) — FOUND.

Tests: `tests/test_kb_search.py` (2) + `tests/test_ai_engine_kb_tool.py` (2) GREEN; existing `tests/test_ai_engine.py` (20 signal-tool) GREEN; full suite deterministic = 835 passed / 1 failed (the failure is the known out-of-scope Phase-15 warmup RED).

---
*Phase: 16-rag-knowledge-bases-for-agents*
*Completed: 2026-06-30*
