---
phase: 16-rag-knowledge-bases-for-agents
plan: 02
subsystem: ingest
tags: [rag, embeddings, openai, tiktoken, pypdf, python-docx, worker, asyncio, pgvector]

# Dependency graph
requires:
  - phase: 16-01-infra-data-model-test-scaffold
    provides: kb_documents / kb_chunks tables + ORM (Vector(1536)) + conftest vector-extension wiring + RED worker tests
  - phase: 03-agents-ai-templates
    provides: ai_engine.py module-level AsyncOpenAI client (reused, not duplicated)
provides:
  - app.services.kb_ingest.embed_texts — batched AsyncOpenAI embeddings (the symbol Wave 2/3/4 tests monkeypatch)
  - app.services.kb_ingest.chunk_text / extract_text / extract_text_async — pure ingest pipeline pieces
  - app.services.kb_ingest_worker.KnowledgeIngestWorker — lifespan-managed singleton that drives docs pending→processing→indexed/failed
  - config knobs openai_embedding_model + kb_ingest_poll_interval + kb_search/chunk knobs
  - kb_ingest_worker registered in app/main.py lifespan next to contact_check_worker
affects: [16-03-api-endpoints-and-handoff, 16-04-search-tool-wiring, 16-05-frontend-surfaces]

# Tech tracking
tech-stack:
  added: []  # all deps (tiktoken/pypdf/python-docx) pinned in 16-01
  patterns:
    - "Lifespan-managed background worker mirroring ContactCheckWorker (start/stop/_run/_tick singleton)"
    - "CPU-bound parse/chunk off the event loop via asyncio.to_thread (Pitfall 3)"
    - "Module-reference embedder (kb_ingest.embed_texts) so worker tests monkeypatch deterministically"
    - "Delete-then-insert kb_chunks for idempotent re-index (Pitfall 8)"
    - "pgvector raw-SQL INSERT bind via '[f1,f2,...]' string form"
    - "Claim-and-flip-to-processing in its own committed TX so the UI poll sees processing mid-parse"

key-files:
  created:
    - app/services/kb_ingest.py
    - app/services/kb_ingest_worker.py
  modified:
    - app/config.py
    - app/main.py

key-decisions:
  - "Reuse ai_engine.py module-level AsyncOpenAI client (one pool, one key) — do NOT create a second client"
  - "Embedding batch size 256 (≈205k tokens at 800 tok/chunk) — under the 2048-item / 300k-token OpenAI ceiling (Pitfall 6)"
  - "Lazy tiktoken init (_get_encoding) to avoid the cold-start BPE fetch at import for non-ingest processes (listener shares requirements.txt)"
  - "Claim+flip-to-processing committed separately from parse/embed so a UI poll sees processing; failure marked in a fresh TX so the worker never dies"
  - "Empty/whitespace doc → indexed chunk_count=0 (NOT failed) per spec"
  - "raw_content normalised via bytes() (driver may return memoryview)"
  - "Partial-staged only KB hunks of app/main.py (git apply --cached); left the parallel Phase-15 CORS allow_methods edit unstaged"

requirements-completed: [KB-02, KB-03]

# Metrics
duration: 8min
completed: 2026-06-30
---

# Phase 16 Plan 02: Ingest Pipeline & Worker Summary

**Token-based ingest pipeline (tiktoken cl100k_base ~800/120 chunking, multi-format extract off the event loop, batched text-embedding-3-small embeds) plus a lifespan-managed `KnowledgeIngestWorker` that drives `kb_documents` pending→processing→indexed/failed with idempotent delete-then-insert re-index; worker tests GREEN.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-06-30T11:45:54Z
- **Completed:** 2026-06-30T11:53:58Z
- **Tasks:** 2
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments
- `app/services/kb_ingest.py` — three pure pipeline pieces + parsers:
  - `chunk_text` token-accurate sliding window (tiktoken `cl100k_base`, `start = end - overlap`), empty/whitespace → `[]`.
  - `extract_text` dispatch for `pdf`|`docx`|`txt`|`md`|`csv`|`text` (pypdf / python-docx / utf-8+latin-1 fallback), `ValueError` on unknown kind; `extract_text_async` wraps it in `asyncio.to_thread` (Pitfall 3 — never block the loop).
  - `embed_texts` reuses the existing `ai_engine.client` AsyncOpenAI, batches ≤256 (Pitfall 6), order-preserving; `[]` in → `[]` out.
- `app/services/kb_ingest_worker.py` — `KnowledgeIngestWorker` mirroring `ContactCheckWorker`: idempotent `start()`, graceful `stop()`, never-dying `_run()` loop, single-doc `_tick()`.
  - `_tick` claims one `pending` doc `FOR UPDATE SKIP LOCKED`, flips to `processing` (committed) so the UI poll sees progress, then parses/chunks/embeds/stores in a second TX.
  - Re-index idempotency (Pitfall 8): `DELETE FROM kb_chunks WHERE document_id` ALWAYS runs before insert — re-running a doc keeps `chunk_count` stable, no duplicates.
  - Empty doc → `indexed` chunk_count=0; any failure → `failed` + `error` (≤1000 chars) in a fresh TX, the worker loop continues.
- Config: `openai_embedding_model` (env knob, `text-embedding-3-small`, 1536 dims) + `kb_ingest_poll_interval` + `kb_search_max_distance` / `kb_search_top_k` / `kb_chunk_max_tokens` / `kb_chunk_overlap`.
- Lifespan: `kb_ingest_worker` imported + `.start()` (after `campaign_enqueue_worker.start()`) + `await .stop()` (before `campaign_enqueue_worker.stop()`) in `app/main.py`.

## Task Commits

Each task was committed atomically:

1. **Task 1: kb_ingest pipeline + config knobs** - `6f35e34` (feat)
2. **Task 2: KnowledgeIngestWorker + lifespan registration** - `ecf6f3b` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP/REQUIREMENTS) — see final docs commit.

## Files Created/Modified
- `app/services/kb_ingest.py` - `chunk_text` (tiktoken sliding window), `extract_text`/`extract_text_async` (6 source kinds, to_thread), `embed_texts` (batched AsyncOpenAI, monkeypatch contract documented)
- `app/services/kb_ingest_worker.py` - `KnowledgeIngestWorker` singleton (claim→processing→parse/chunk/embed→indexed/failed; delete-then-insert; never-dies)
- `app/config.py` - `openai_embedding_model` + 5 KB knobs (mirroring the `openai_model` Field idiom)
- `app/main.py` - lifespan registration of `kb_ingest_worker` (import + start + await stop) — partial-staged KB hunks only

## Decisions Made
- **Reuse the existing AsyncOpenAI client** (`from app.services.ai_engine import client`) — a second client would mean a second connection pool / key surface for no benefit.
- **Embedding batch 256** — ≈205k tokens at ~800 tok/chunk, comfortably under the OpenAI 2048-item / 300k-token per-request ceiling; loops batches per doc, concatenating in order (`resp.data` is order-preserving).
- **Lazy tiktoken init** — `_get_encoding()` defers the cold-start BPE vocab fetch so processes that never chunk (the listener shares `requirements.txt`) don't pay it at import.
- **Claim+flip committed before parse** — the worker flips `pending→processing` and commits in its own TX so a UI poll sees `processing` while the (slower) parse/embed runs; the index/fail write is a second TX; failure is recorded in yet another fresh TX so a mid-parse error can't roll back the failure marker. This keeps the worker loop alive (mirrors `ContactCheckWorker._run`).
- **pgvector raw-SQL bind** — the embedding is bound as the pgvector text form `'[f1,f2,...]'` for the raw-SQL INSERT (the ORM Vector adapter is for ORM-level inserts; we INSERT via `text()` to mirror the contact worker's raw-SQL style).
- **`bytes(raw_content)`** — the async driver may hand back `memoryview`; normalise before passing to the parsers.
- **Partial-stage of `app/main.py`** — the working tree carries a parallel **Phase-15** CORS `allow_methods` edit (adds `PUT`). Per the sequential-execution / parallel-agent rule, only the three KB hunks (import + `.start()` + `await .stop()`) were staged via `git apply --cached` of a crafted patch; the Phase-15 CORS hunk was verified to remain unstaged for its own phase to commit (mirrors 16-01's partial-stage of `app/models/__init__.py`).

## Deviations from Plan

None affecting code behaviour. Two clarifications:

1. **Task 1 `<verify>` references `tests/test_kb_ingest.py`** — that file contains only the Wave-3 upload-endpoint test (`test_upload_creates_pending_doc`), which 404s until plan 16-03 lands the `POST /api/v1/knowledge-bases/{id}/documents` router. It cannot go GREEN in this plan and is out of scope (the plan's `files_modified` lists no router; the objective explicitly assigns the upload endpoint to Wave 3). The real RED→GREEN contract for the Task-1 pipeline functions is exercised by the worker test (Task 2 `<verify>`), which monkeypatches `app.services.kb_ingest.embed_texts` and drives a full `_tick`. Both worker tests are GREEN. Verified the Task-1 module imports cleanly and all six source kinds + chunking + latin-1 fallback + ValueError behave (container smoke).
2. **Worker tests were already complete** — the plan's Task-2 step 3 ("flesh out the two worker tests to GREEN") was unnecessary: the 16-01 RED scaffold already wrote both tests fully (correct `_EMBED_TARGET = "app.services.kb_ingest.embed_texts"`, fixed 1536-dim stub, direct `_tick()` calls). They were left unmodified; the production code was built to satisfy them.

## Issues Encountered
None. Worker tests passed on the first run after implementation.

## Out-of-Scope Discoveries (not fixed — scope boundary)
- **Full suite: 9 failed / 827 passed / 1 skipped.** None are regressions from this plan:
  - 8 are Phase-16 later-wave RED scaffold tests: `test_knowledge_bases.py` (3, KB CRUD — 16-03), `test_kb_ingest.py::test_upload_creates_pending_doc` (upload endpoint — 16-03), `test_kb_search.py` (2, `app.services.kb_search` ModuleNotFound — 16-04), `test_ai_engine_kb_tool.py` (2, `build_kb_tool_spec` / data-tool dispatch — 16-04).
  - 1 is a **Phase-15** RED test: `test_warmup_worker.py::test_restricted_sender_excluded` — its assertion message literally says "restriction clause not added yet (WARM-14)". Driven by the uncommitted Phase-15 work-in-progress in the working tree (`app/services/warmup.py`); 16-02 never touches warmup. Logged here, not fixed.

## Known Stubs
None. The only "empty" branch (`chunks == []` → `indexed` with `chunk_count=0`) is intentional per the plan's behaviour spec (an empty/whitespace document is indexed, not failed), not a stub.

## User Setup Required
None for this plan's automated scope. NOTE for the OPS deploy step (user-gated, after merge): the worker auto-registers in the lifespan, so a `docker compose up -d --build api` picks it up. `OPENAI_API_KEY` must be present in the api environment for real embeds (already required by `ai_engine`); `OPENAI_EMBEDDING_MODEL` is optional (defaults to `text-embedding-3-small`). Per CLAUDE.md, do NOT deploy here — deploy is user-gated.

## Next Phase Readiness
- `app.services.kb_ingest.embed_texts` and `app.services.kb_ingest_worker.KnowledgeIngestWorker` exist as the symbols later waves expect.
- 16-03 (upload/CRUD endpoints) can now record a `pending` `kb_documents` row and the worker will index it asynchronously (KB-02/KB-03 close end-to-end once the router lands).
- The `kb_search` knobs (`kb_search_max_distance`, `kb_search_top_k`) are already in config for 16-04 to consume.

## Self-Check: PASSED

All created/modified files present on disk:
- `app/services/kb_ingest.py`, `app/services/kb_ingest_worker.py` — FOUND.
- `app/config.py` (KB knobs), `app/main.py` (lifespan registration) — modifications present.

All task commits exist:
- `6f35e34` (Task 1), `ecf6f3b` (Task 2) — FOUND.

Worker tests GREEN: `tests/test_kb_ingest_worker.py` 2 passed.

---
*Phase: 16-rag-knowledge-bases-for-agents*
*Completed: 2026-06-30*
