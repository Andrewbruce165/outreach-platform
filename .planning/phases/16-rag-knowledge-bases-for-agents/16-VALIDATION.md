---
phase: 16
slug: rag-knowledge-bases-for-agents
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-30
---

# Phase 16 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `16-RESEARCH.md` § Validation Architecture. Requirement IDs (KB-01…KB-06) are provisional — the planner finalizes them against ROADMAP/REQUIREMENTS.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio (`asyncio_mode="auto"`, session-scoped loop) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_knowledge_bases.py tests/test_kb_*.py tests/test_ai_engine_kb_tool.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~60–90 seconds (full suite); KB-scoped subset ~15s |

**MANDATORY:** tests run ONLY through the test-overlay (CLAUDE.md guard + conftest hard guard `tests/conftest.py::_assert_test_dsn`). Never `docker compose run --rm api pytest` without the overlay (DATABASE_URL → prod, conftest does DROP SCHEMA). **Wave 0 must bump the `db-test` image in `docker-compose.test.yml` to `pgvector/pgvector:pg16`** so the test DB has the `vector` extension.

---

## Sampling Rate

- **After every task commit:** Run the quick command (KB-scoped test files, `-x`)
- **After every plan wave:** Run the full suite — must stay GREEN (baseline is GREEN per memory `project-test-baseline-red.md`)
- **Before `/gsd:verify-work`:** Full suite green **+ manual live smoke** (upload a real PDF in deployed app → Documents tab transitions processing→indexed → Search tab returns chunks → agent answers from KB content)
- **Max feedback latency:** ~15 seconds (quick subset)

---

## Per-Task Verification Map

> Filled provisionally from research. Planner refines `Task ID` / `Plan` / `Wave` once PLAN.md files exist.

| Req | Behavior | Test Type | Automated Command | File Exists | Status |
|-----|----------|-----------|-------------------|-------------|--------|
| KB-01 | Create KB scoped to workspace; another workspace can't see it | integration | `pytest tests/test_knowledge_bases.py::test_create_kb_workspace_isolated -x` | ❌ W0 | ⬜ pending |
| KB-02 | Upload file / paste text → `kb_documents` row, status `pending`, `size_bytes` set | integration | `pytest tests/test_kb_ingest.py::test_upload_creates_pending_doc -x` | ❌ W0 | ⬜ pending |
| KB-03 | Worker: pending→processing→indexed; `chunk_count`>0; failed doc carries error | integration | `pytest tests/test_kb_ingest_worker.py::test_tick_indexes_pending_doc -x` | ❌ W0 | ⬜ pending |
| KB-03 | Aggregate counters (DOCUMENTS/INDEXED/PROCESSING/FAILED/STORAGE) correct | integration | `pytest tests/test_knowledge_bases.py::test_kb_detail_aggregate -x` | ❌ W0 | ⬜ pending |
| KB-04 | Attach/detach agent↔KB (M:N); reverse list (Agents tab) | integration | `pytest tests/test_knowledge_bases.py::test_attach_detach_agent -x` | ❌ W0 | ⬜ pending |
| KB-05 | `search_knowledge_base` returns nearest chunks by cosine distance; respects top-K + threshold | unit | `pytest tests/test_kb_search.py::test_cosine_search_orders_by_distance -x` | ❌ W0 | ⬜ pending |
| KB-05 | Tool exposed ONLY when agent has ≥1 KB (D-04) | unit | `pytest tests/test_ai_engine_kb_tool.py::test_tool_gated_on_attached_kb -x` | ❌ W0 | ⬜ pending |
| KB-05 | Data-tool branch: KB hits appended as `role:"tool"`, model continues (NOT terminating) | unit (mock OpenAI) | `pytest tests/test_ai_engine_kb_tool.py::test_search_kb_continues_conversation -x` | ❌ W0 | ⬜ pending |
| KB-06 | Search never returns chunks from another workspace's KB | integration | `pytest tests/test_kb_search.py::test_search_workspace_isolated -x` | ❌ W0 | ⬜ pending |
| KB-03 | Re-index idempotency: re-index a doc → `chunk_count` stable, no duplicate chunks | integration | `pytest tests/test_kb_ingest_worker.py::test_reindex_is_idempotent -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `docker-compose.test.yml` — bump `db-test` image to `pgvector/pgvector:pg16`
- [ ] `tests/conftest.py::_build_outreach_schema` — add `CREATE EXTENSION IF NOT EXISTS vector` **before** `create_all` (Pitfall 1)
- [ ] `tests/conftest.py` — shared fixtures: seeded KB + indexed chunks; deterministic embedder stub (monkeypatch `embed_texts`); OpenAI-client stub returning a `search_knowledge_base` tool_call
- [ ] `tests/test_knowledge_bases.py` — CRUD + isolation + aggregate + attach/detach (KB-01/03/04/06)
- [ ] `tests/test_kb_ingest.py` + `tests/test_kb_ingest_worker.py` — upload/paste + worker tick + re-index idempotency (KB-02/03)
- [ ] `tests/test_kb_search.py` — cosine ordering + threshold + workspace isolation (KB-05/06)
- [ ] `tests/test_ai_engine_kb_tool.py` — tool gating + data-tool continue-not-terminate (KB-05)
- [ ] `requirements.txt` — add `pgvector==0.4.2`, `tiktoken==0.13.0`, `pypdf==6.14.2`, `python-docx==1.2.0`; rebuild api + listener

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real PDF/DOCX parse fidelity in deployed app | KB-02/03 | Parser output on real-world files isn't fully assertable in unit fixtures | Upload a real PDF + DOCX in the deployed Documents tab; confirm both transition processing→indexed with sensible `chunk_count` |
| Agent visibly answers from KB content end-to-end | KB-05 | True LLM behavior (model deciding to call the tool) needs a live model, not a mock | In a live conversation with a KB-attached agent, ask a question only answerable from an uploaded doc; confirm the answer reflects KB content |
| Search-tab relevance / threshold feel | KB-05 | Threshold/top-K tuning is qualitative | Run several Search-tab queries; confirm relevant chunks surface and irrelevant ones don't (tune env knobs if off) |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (pgvector test image + extension + deps)
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s (quick subset)
- [ ] `nyquist_compliant: true` set in frontmatter (after planner maps tasks→tests)

**Approval:** pending
