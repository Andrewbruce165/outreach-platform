---
phase: 16-rag-knowledge-bases-for-agents
plan: 01
subsystem: infra
tags: [pgvector, postgres, rag, embeddings, sqlalchemy, pytest, docker]

# Dependency graph
requires:
  - phase: 01-multitenancy
    provides: workspaces table + workspace_id scoping pattern (KB tables FK workspaces)
  - phase: 03-agents-ai-templates
    provides: ai_contexts (agent) table — the M:N attach point for agent_knowledge_bases
provides:
  - pgvector/pgvector:pg16 db images (prod + test-overlay) with vector extension
  - migration 041 — knowledge_bases / kb_documents / kb_chunks / agent_knowledge_bases + HNSW + btree (idempotent)
  - four KB ORM classes mirroring the migration (incl. Vector(1536)) so create_all builds the same test schema
  - conftest CREATE EXTENSION vector before create_all + migration-041 apply (HNSW index in test DB)
  - 5 RED test files mapping every KB-01..KB-06 behavior
affects: [16-02-ingest-pipeline-and-worker, 16-03-api-endpoints-and-handoff, 16-04-search-tool-wiring, 16-05-frontend-surfaces]

# Tech tracking
tech-stack:
  added: [pgvector==0.4.2, tiktoken==0.13.0, pypdf==6.14.2, python-docx==1.2.0, "pgvector/pgvector:pg16 docker image"]
  patterns: ["Vector(1536) ORM column mirroring a raw-SQL migration", "CREATE EXTENSION vector before create_all in test-overlay", "RED test scaffold with deferred in-body imports + deterministic embedder stubs"]

key-files:
  created:
    - migrations/041_knowledge_bases.sql
    - tests/test_knowledge_bases.py
    - tests/test_kb_ingest.py
    - tests/test_kb_ingest_worker.py
    - tests/test_kb_search.py
    - tests/test_ai_engine_kb_tool.py
  modified:
    - requirements.txt
    - docker-compose.yml
    - docker-compose.test.yml
    - app/models/__init__.py
    - tests/conftest.py

key-decisions:
  - "HNSW + vector_cosine_ops index (builds on empty table — IVFFlat needs training rows)"
  - "Pasted text stored in raw_content BYTEA discriminated by source_kind='text' (Open Q 1)"
  - "STORAGE aggregate = SUM(kb_documents.size_bytes) (Open Q 2)"
  - "Stage only KB hunks of app/models/__init__.py — left the parallel Phase-15 WarmupSession server_default edit unstaged"

patterns-established:
  - "Vector(1536) ORM column must mirror the migration or the test-overlay (create_all) schema diverges from prod"
  - "Extension must be created BEFORE create_all in conftest (Pitfall 1) since create_all emits VECTOR(1536)"
  - "Image swap preserves the per-service command: block verbatim (Pitfall 2 — anti-drift DDL logging)"

requirements-completed: []  # Wave-0 scaffold — KB-01..KB-06 are RED, completed by later waves (16-02..16-05)

# Metrics
duration: 12min
completed: 2026-06-30
---

# Phase 16 Plan 01: Infra, Data Model & Test Scaffold Summary

**pgvector stood up (prod + test images), migration 041 with the four KB tables + HNSW index, ORM mirror incl. Vector(1536), conftest vector-extension wiring, and a 10-test RED scaffold covering every KB-01..KB-06 behavior.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-06-30T11:29:14Z
- **Completed:** 2026-06-30T11:41:49Z
- **Tasks:** 3
- **Files modified:** 11 (6 created, 5 modified)

## Accomplishments
- Pinned four RAG deps and swapped both db images to `pgvector/pgvector:pg16`, preserving the prod `command:` DDL-logging block verbatim (Pitfall 2).
- Migration 041: idempotent `CREATE EXTENSION vector` + `knowledge_bases` / `kb_documents` / `kb_chunks` / `agent_knowledge_bases` + HNSW (`vector_cosine_ops`) + btree indexes.
- Four ORM classes mirror the migration (`KbChunk.embedding = Vector(1536)`), so the test-overlay `create_all` builds the same schema as prod. The static `AIContext.knowledge_base` Text field was left untouched (D-08).
- conftest: `CREATE EXTENSION vector` before `create_all`, KB tables added to the UUID-default ALTER loop, exists-guarded migration-041 apply (brings the HNSW index into the test DB).
- 5 RED test files (10 tests) — all genuinely RED (assertion/import failures, no skips); full suite still collects 837 tests with 0 errors via deferred in-body imports.

## Task Commits

Each task was committed atomically:

1. **Task 1: Deps + Docker image swaps (prod + test)** - `bb53592` (chore)
2. **Task 2: Migration 041 + ORM models + conftest extension wiring** - `018b1ef` (feat)
3. **Task 3: RED test scaffold for KB-01..KB-06** - `0e9613b` (test)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP) — see final docs commit.

## Files Created/Modified
- `migrations/041_knowledge_bases.sql` - CREATE EXTENSION vector + 4 KB tables + HNSW + btree (idempotent, BEGIN/COMMIT)
- `app/models/__init__.py` - KnowledgeBase / KbDocument / KbChunk(Vector(1536)) / AgentKnowledgeBase ORM classes + pgvector Vector import
- `tests/conftest.py` - vector extension before create_all, KB tables in UUID-default loop, migration-041 apply guard
- `requirements.txt` - pgvector / tiktoken / pypdf / python-docx pins
- `docker-compose.yml` - db image → pgvector/pgvector:pg16 (command: block preserved)
- `docker-compose.test.yml` - db-test image → pgvector/pgvector:pg16
- `tests/test_knowledge_bases.py` - KB-01 isolation, KB-03 aggregate, KB-04 attach/detach
- `tests/test_kb_ingest.py` - KB-02 upload creates pending doc
- `tests/test_kb_ingest_worker.py` - KB-03 worker tick + reindex idempotency
- `tests/test_kb_search.py` - KB-05 cosine ordering/top-K/threshold, KB-06 isolation
- `tests/test_ai_engine_kb_tool.py` - KB-05/D-04 tool gating, KB-05 data-tool two-pass

## Decisions Made
- **HNSW over IVFFlat:** the migration's `CREATE INDEX` runs on an empty table at api start; HNSW builds without training rows, IVFFlat cannot.
- **Pasted text in `raw_content` BYTEA** (utf-8), discriminated by `source_kind='text'` (research Open Q 1) — single uniform re-index path.
- **STORAGE = `SUM(kb_documents.size_bytes)`** (research Open Q 2) — most intuitive "how much did I upload".
- **Partial staging of `app/models/__init__.py`:** the working tree carried a parallel Phase-15 `WarmupSession` `server_default` edit. Per the project parallel-agent rule, only the KB hunks (Vector import + four classes) were staged into Task 2; the Phase-15 hunk was left unstaged for its own phase to commit.

## Deviations from Plan

None - plan executed exactly as written. (The partial-staging of `app/models/__init__.py` is a commit-hygiene measure mandated by the sequential-execution instructions, not a code deviation: the file is on the plan's files list and the KB additions landed exactly as specified, layered alongside — not clobbering — the pre-existing Phase-15 edit.)

## Issues Encountered
- First ORM import smoke failed with `ModuleNotFoundError: No module named 'pgvector'` because the api image predated the requirements.txt change. Resolved by rebuilding the api image (`docker compose ... build api`) before re-running — expected, since deps install at image build. The `docker compose run` also recreated `outreach-platform-db` against the new pgvector image on the SAME `postgres_data` volume (same PG16 major — no re-init, no data loss; no `down -v` was ever issued).

## User Setup Required
None for this plan's automated scope. NOTE for the OPS deploy step (user-gated, after merge): rebuild + recreate prod containers (`docker compose up -d --build api listener`; the db image swap recreates the db container against the existing volume). After recreate, verify the anti-drift logging survived: `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SHOW log_statement;"` must return `ddl`.

## Next Phase Readiness
- pgvector is the gating dependency for the whole phase and is now installed in both prod and test images; the test DB has the `vector` extension and the HNSW index.
- The four KB tables + ORM mirror are in place; 16-02 (ingest worker) and 16-03 (API) can build directly on them.
- The RED scaffold names the exact production symbols later waves must provide: `app.services.kb_ingest.embed_texts`, `app.services.kb_ingest_worker.KnowledgeIngestWorker`, `app.services.kb_search.kb_search` + `embed_query`, `app.services.ai_engine.build_kb_tool_spec` + the `search_knowledge_base` data-tool dispatch.

## Self-Check: PASSED

All created files present on disk:
- `migrations/041_knowledge_bases.sql`, `tests/test_knowledge_bases.py`, `tests/test_kb_ingest.py`, `tests/test_kb_ingest_worker.py`, `tests/test_kb_search.py`, `tests/test_ai_engine_kb_tool.py`, `16-01-SUMMARY.md` — all FOUND.

All task commits exist:
- `bb53592` (Task 1), `018b1ef` (Task 2), `0e9613b` (Task 3) — all FOUND.

---
*Phase: 16-rag-knowledge-bases-for-agents*
*Completed: 2026-06-30*
