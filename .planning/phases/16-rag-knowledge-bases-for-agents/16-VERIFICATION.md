---
phase: 16-rag-knowledge-bases-for-agents
verified: 2026-06-30T13:45:00Z
status: passed
score: 6/6 must-haves verified
re_verification: false
gaps: []
human_verification:
  - test: "End-to-end RAG conversation via a live Telegram agent"
    expected: "Agent calls search_knowledge_base when a factual question is asked, retrieves passages from an attached KB, and answers from them rather than guessing"
    why_human: "Already performed and passed by the user during Phase 16 (résumé PDF, agent answered education question from KB). Documented for future regression testing."
---

# Phase 16: RAG Knowledge Bases for Agents — Verification Report

**Phase Goal:** Give the user a RAG knowledge base for AI agents — (1) a "Knowledge bases" UI tab to create isolated workspace-scoped KBs and upload data; (2) KBs attach at the AGENT level (M:N) and the agent retrieves from them on demand during reply generation. Workspace-isolated, no cross-workspace leak.

**Verified:** 2026-06-30T13:45:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can create workspace-isolated KBs; other workspaces cannot see them | ✓ VERIFIED | `_load_kb` enforces `workspace_id == ctx.workspace_id`; `test_create_kb_workspace_isolated` GREEN |
| 2 | User can upload files (PDF/DOCX/TXT/MD/CSV) or paste text — 202 Accepted, worker indexes async | ✓ VERIFIED | `POST /api/v1/knowledge-bases/{id}/documents` (multipart, 20 MB cap, ext→source_kind); `POST /documents/paste`; worker claims `pending` and drives pending→indexed/failed; `test_upload_creates_pending_doc` GREEN |
| 3 | Ingest worker drives pending→processing→indexed/failed with chunk_count; re-index is idempotent | ✓ VERIFIED | `KnowledgeIngestWorker._tick`: claim+flip-to-processing commit, delete-then-insert chunks; `test_tick_indexes_pending_doc` + `test_reindex_is_idempotent` GREEN |
| 4 | KB attaches to an agent (M:N); attach/detach and reverse list work | ✓ VERIFIED | `agent_knowledge_bases` through-table; `POST /knowledge-bases/{id}/agents`, `DELETE /{id}/agents/{agent_id}`, `GET /{id}/agents`; `test_attach_detach_agent` GREEN |
| 5 | Agent retrieves from attached KBs via `search_knowledge_base` tool; tool gated on ≥1 KB; conversation.status unchanged | ✓ VERIFIED | `build_kb_tool_spec(has_kb)` gates tool; dispatch branch in `generate_response` resolves locally via `kb_search`, appends `role:"tool"`, continues two-pass WITHOUT touching `conversation.status`; `test_tool_gated_on_attached_kb` + `test_search_kb_continues_conversation` GREEN |
| 6 | All KB search and CRUD endpoints are strictly workspace-scoped; no cross-workspace chunk leak | ✓ VERIFIED | `kb_search` WHERE clause: `KbChunk.workspace_id == workspace_id AND KbChunk.kb_id.in_(kb_ids)` (KB-06 defence-in-depth); `test_search_workspace_isolated` + `test_hybrid_keyword_still_workspace_isolated` GREEN |

**Score:** 6/6 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/models/__init__.py` | KnowledgeBase / KbDocument / KbChunk(Vector(1536)) / AgentKnowledgeBase ORM classes | ✓ VERIFIED | All four classes present, `Vector(1536)` imported from `pgvector.sqlalchemy`, `server_default=gen_random_uuid()` on id columns (mig-042 fix) |
| `migrations/041_knowledge_bases.sql` | knowledge_bases / kb_documents / kb_chunks / agent_knowledge_bases tables + HNSW + btree, idempotent | ✓ VERIFIED | 74-line migration with `CREATE EXTENSION IF NOT EXISTS vector`, all four tables, `HNSW vector_cosine_ops` index, wrapped in BEGIN/COMMIT |
| `migrations/042_kb_id_server_defaults.sql` | ALTER TABLE to set `DEFAULT gen_random_uuid()` on KB id columns (ORM drift fix) | ✓ VERIFIED | Present; `ALTER TABLE IF EXISTS knowledge_bases/kb_documents/kb_chunks ALTER COLUMN id SET DEFAULT gen_random_uuid()` |
| `migrations/043_kb_chunks_fts_index.sql` | GIN functional index on `to_tsvector('simple', content)` for hybrid keyword search | ✓ VERIFIED | Present; `CREATE INDEX IF NOT EXISTS idx_kb_chunks_content_fts ON kb_chunks USING gin (to_tsvector('simple', content))` |
| `app/services/kb_ingest.py` | `chunk_text`, `extract_text`/`extract_text_async`, `embed_texts` — pipeline pieces | ✓ VERIFIED | 172 lines; all three functions substantive; NUL-byte strip present; lazy tiktoken init; embed batches ≤256 |
| `app/services/kb_ingest_worker.py` | `KnowledgeIngestWorker` singleton, lifespan-managed, claim→process→indexed/failed | ✓ VERIFIED | 234 lines; `FOR UPDATE SKIP LOCKED`, committed flip to `processing`, delete-then-insert idempotency, never-dying `_run` loop |
| `app/services/kb_search.py` | `embed_query`, `attached_kb_ids`, `kb_search` (hybrid cosine + FTS, workspace-filtered) | ✓ VERIFIED | 177 lines; hybrid `or_(distance <= ceiling, kw_match)`, KB-06 double-filter, empty-kb_ids fast path, patchable `embed_query` |
| `app/services/ai_engine.py` | `SEARCH_KB_TOOL`, `build_kb_tool_spec(has_kb)`, KB dispatch branch in `generate_response`, `<knowledge_base>` RAG-awareness directive | ✓ VERIFIED | All present; tool NOT in `BUILT_IN_TOOL_NAMES` (data tool); lazy import inside function (cycle-break); `has_kb` gate; directive injected when `has_kb` |
| `app/routers/knowledge_bases.py` | 14 workspace-scoped endpoints under `auth_dep` | ✓ VERIFIED | 14 `@router.` decorators verified; `_load_kb`/`_load_doc` 404 on cross-workspace; D-09 aggregate via one-pass `COUNT(*) FILTER` |
| `app/schemas/knowledge_bases.py` | Pydantic v2 request/response models incl. D-09 aggregate, D-10 per-doc, search, reverse M:N | ✓ VERIFIED | All schema classes present: `KnowledgeBaseCreate/Update/Response` (D-09 five-field aggregate + derived `status`), `KbDocumentResponse`, `KbPasteTextRequest`, `KbSearchRequest/Hit/Response`, `AgentKbAttachRequest`, `AgentForKbResponse` |
| `app/config.py` | KB knobs: `openai_embedding_model`, `kb_ingest_poll_interval`, `kb_search_max_distance` (0.8), `kb_search_top_k`, `kb_chunk_max_tokens` (250), `kb_chunk_overlap` (50) | ✓ VERIFIED | All six knobs present with final tuned defaults |
| `app/database.py` | `CREATE EXTENSION IF NOT EXISTS vector` before `create_all` in `init_db` | ✓ VERIFIED | Line 180: `await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")` precedes `create_all` |
| `app/main.py` | `kb_ingest_worker` registered in lifespan (start + stop); `knowledge_bases` router included | ✓ VERIFIED | `kb_ingest_worker.start()` at line 66, `await kb_ingest_worker.stop()` at line 73, `app.include_router(knowledge_bases.router)` at line 198 |
| `docker-compose.yml` | db image `pgvector/pgvector:pg16` | ✓ VERIFIED | `image: pgvector/pgvector:pg16` in compose file |
| `aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.index.tsx` | KB list page — create, list, empty state | ✓ VERIFIED | 342 lines; `POST /api/v1/knowledge-bases`; list renders with status pill; empty state present |
| `aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.$id.tsx` | KB detail page — D-09 header + 4 tabs (Documents/Search/Agents/Settings), poll-while-processing | ✓ VERIFIED | 1152 lines; `refetchInterval` on `processing\|pending` documents; upload/paste/reindex/delete wired; Search tab calls `POST /search`; Agents tab calls `GET /{id}/agents` |
| `aimly-tg-outreach/src/components/AppSidebar.tsx` | "Knowledge bases" sidebar entry | ✓ VERIFIED | Line 32: `{ to: "/knowledge-bases", label: "Knowledge bases", icon: Library }` |
| `aimly-tg-outreach/src/routes/_authenticated/agents.tsx` | KbMultiSelect with deferred-attach on agent create | ✓ VERIFIED | `pendingKbIds` state, `Promise.allSettled` attach-on-create, `KbMultiSelect` component with attach/detach mutations |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `kb_ingest_worker._tick` | `kb_ingest.embed_texts` | module reference (`kb_ingest.embed_texts`) | ✓ WIRED | `vectors = await kb_ingest.embed_texts(chunks, settings.openai_embedding_model)` — monkeypatch-safe |
| `app/main.py` lifespan | `KnowledgeIngestWorker` | `from app.services.kb_ingest_worker import kb_ingest_worker` | ✓ WIRED | `kb_ingest_worker.start()` in lifespan `startup`; `await kb_ingest_worker.stop()` in `shutdown` |
| `app/main.py` | `knowledge_bases` router | `app.include_router(knowledge_bases.router)` | ✓ WIRED | Line 198 confirmed |
| `ai_engine.generate_response` | `kb_search.kb_search` | lazy import `from app.services import kb_search as _kb_search` | ✓ WIRED | Import inside function (cycle-break); `_kb_search.kb_search(db=session, workspace_id=kb_workspace_id, kb_ids=kb_ids, query=...)` |
| `ai_engine.generate_response` | `kb_search.attached_kb_ids` | same lazy import | ✓ WIRED | `kb_workspace_id, kb_ids = await _kb_search.attached_kb_ids(session, kb_agent_id)` |
| `kb_search.embed_query` | `kb_ingest.embed_texts` | `from app.services import kb_ingest` | ✓ WIRED | `vecs = await kb_ingest.embed_texts([query], settings.openai_embedding_model)` |
| `knowledge_bases.py` router (manual search) | `kb_search.kb_search` | `from app.services.kb_search import kb_search` | ✓ WIRED | Import at module level; used in `POST /{kb_id}/search` handler |
| `app/database.py init_db` | pgvector extension | `exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")` | ✓ WIRED | Runs before `create_all`; prevents `type "vector" does not exist` on fresh DB |
| Frontend KB list → create | `POST /api/v1/knowledge-bases` | `api()` call with `method: "POST"` | ✓ WIRED | `knowledge-bases.index.tsx` line 264; mutation wired with `queryClient.invalidateQueries` |
| Frontend KB detail → search | `POST /api/v1/knowledge-bases/{id}/search` | `api()` call | ✓ WIRED | `knowledge-bases.$id.tsx` line 839; `SearchTab` mutation; results rendered from `searchMut.data?.results` |
| Frontend agent editor → KB attach | `POST /api/v1/knowledge-bases/{id}/agents` | `api()` call | ✓ WIRED | `agents.tsx` line 578; `KbMultiSelect` attach/detach mutations; deferred via `Promise.allSettled` on create |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `knowledge-bases.index.tsx` | `kbs` (list) | `GET /api/v1/knowledge-bases` → `knowledge_bases.workspace_id == ctx.workspace_id` DB query | Yes — real DB SELECT | ✓ FLOWING |
| `knowledge-bases.$id.tsx` Documents tab | `docs` | `GET /api/v1/knowledge-bases/{id}/documents` → SELECT kb_documents WHERE workspace_id | Yes — real DB SELECT | ✓ FLOWING |
| `knowledge-bases.$id.tsx` Search tab | `results` | `POST /api/v1/knowledge-bases/{id}/search` → `kb_search()` → pgvector cosine_distance query | Yes — real pgvector query (hybrid) | ✓ FLOWING |
| `knowledge-bases.$id.tsx` Agents tab | `agentsQ` | `GET /api/v1/knowledge-bases/{id}/agents` → SELECT agent_knowledge_bases JOIN ai_contexts | Yes — real DB SELECT | ✓ FLOWING |
| `agents.tsx` KbMultiSelect | `kbs` / `attachedKbIds` | `GET /api/v1/knowledge-bases` + per-KB `GET /{id}/agents` | Yes — real DB queries | ✓ FLOWING |
| `ai_engine.generate_response` | `hits` (RAG passages) | `attached_kb_ids()` → `kb_search()` → pgvector cosine_distance + FTS hybrid query | Yes — real pgvector query over `kb_chunks` table | ✓ FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 13 KB test functions pass (KB-01..KB-06 coverage) | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_kb_search.py tests/test_kb_ingest.py tests/test_kb_ingest_worker.py tests/test_knowledge_bases.py tests/test_ai_engine_kb_tool.py -q` | 13 passed in 6.10s | ✓ PASS |
| `build_kb_tool_spec(has_kb=False)` returns empty | tested via `test_tool_gated_on_attached_kb` | GREEN — no tool spec when no KB attached | ✓ PASS |
| `build_kb_tool_spec(has_kb=True)` returns `[SEARCH_KB_TOOL]` | tested via `test_tool_gated_on_attached_kb` | GREEN — tool spec returned | ✓ PASS |
| pgvector extension created before `create_all` | `grep "CREATE EXTENSION IF NOT EXISTS vector" app/database.py` — precedes `create_all` | Line 180 confirmed | ✓ PASS |
| All Phase 16 commits present in git log | `git log --oneline` shows all 17 commits bb53592..85b8451 | All 17 present | ✓ PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| KB-01 | 16-01 (scaffold), 16-03 (router) | Workspace-isolated KB — other workspaces cannot see or access it | ✓ SATISFIED | `KnowledgeBase.workspace_id` FK; `_load_kb` 404 on cross-workspace; `test_create_kb_workspace_isolated` GREEN |
| KB-02 | 16-02 (ingest), 16-03 (router) | Upload files/paste text → `status='pending'` + 202; worker indexes async | ✓ SATISFIED | `POST /documents` (multipart, 20 MB, ext whitelist) + `POST /documents/paste`; worker `_tick` claims pending; `test_upload_creates_pending_doc` GREEN |
| KB-03 | 16-02 (worker), 16-03 (router) | Worker drives pending→processing→indexed/failed; chunk_count; re-index idempotent; D-09 aggregate + D-10 per-doc | ✓ SATISFIED | `KnowledgeIngestWorker`; delete-then-insert; `_aggregates_for_kbs` COUNT FILTER; `test_tick_indexes_pending_doc` + `test_reindex_is_idempotent` + `test_kb_detail_aggregate` GREEN |
| KB-04 | 16-03 (router) | KB attaches to agent (M:N); attach/detach; reverse list; KB reusable between agents | ✓ SATISFIED | `agent_knowledge_bases` table; `POST /knowledge-bases/{id}/agents` + `DELETE /{id}/agents/{agent_id}` + `GET /{id}/agents`; `test_attach_detach_agent` GREEN |
| KB-05 | 16-04 (tool wiring) | `search_knowledge_base` DATA tool gated on ≥1 KB; local vector search; role:tool message; two-pass; no status change | ✓ SATISFIED | `build_kb_tool_spec(has_kb)`; dispatch branch `if func_name == SEARCH_KB_TOOL_NAME` with `continue` (no status write); `test_tool_gated_on_attached_kb` + `test_search_kb_continues_conversation` GREEN |
| KB-06 | 16-04 (search) | Search workspace-scoped — `workspace_id + kb_ids` double-filter; no cross-workspace chunk leak | ✓ SATISFIED | `kb_search` WHERE `KbChunk.workspace_id == workspace_id AND KbChunk.kb_id.in_(kb_ids)`; `test_search_workspace_isolated` + `test_hybrid_keyword_still_workspace_isolated` GREEN |

All 6 requirements satisfied. No orphaned requirements.

---

### Anti-Patterns Found

No blockers or warnings. HTML input `placeholder=` attributes in frontend forms were the only matches for the word "placeholder" — these are standard UX labels, not code stubs.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | — | — | — | — |

---

### Human Verification Required

#### 1. Live RAG Conversation (Regression)

**Test:** In the Aimly UI, attach a KB to an agent, start a conversation, and ask a factual question that is only answerable from the KB content.
**Expected:** The agent calls `search_knowledge_base`, retrieves relevant passages, and answers from them. No hallucination; no "I don't know" before searching.
**Why human:** Requires live Telegram account, live OpenAI API call, and visual inspection of the response. Already passed once in live human-verify (Phase 16 résumé PDF → education question answered correctly). Flagged here as the regression test to run after any changes to `ai_engine.py` or `kb_search.py`.

---

### Gaps Summary

No gaps. All 6 requirements verified across both repos:

- **Backend (this repo):** All 4 ORM models, 3 migrations, 3 service modules, 1 router, 1 schema module, and all config knobs are substantive and wired. The worker is registered in `app/main.py` lifespan. pgvector extension correctly initialized before `create_all`. 13/13 KB tests pass.
- **Frontend (sibling repo `aimly-tg-outreach`):** "Knowledge bases" sidebar tab wired; list page creates/lists KBs with D-09 status pills; detail page has 4 substantive tabs with real API calls and background polling; agent editor has `KbMultiSelect` with deferred-attach on create.
- **7 backend defects found during live human-verify were all fixed and deployed** (pgvector init ordering, NUL-strip, id server_default, search threshold, hybrid search, chunk sizing, RAG-awareness directive) — none remain open.

---

_Verified: 2026-06-30T13:45:00Z_
_Verifier: Claude (gsd-verifier)_
