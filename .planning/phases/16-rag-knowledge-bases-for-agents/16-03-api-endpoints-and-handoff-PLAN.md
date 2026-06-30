---
phase: 16-rag-knowledge-bases-for-agents
plan: 03
type: execute
wave: 3
depends_on: ["16-01", "16-02"]
files_modified:
  - app/schemas/knowledge_bases.py
  - app/routers/knowledge_bases.py
  - app/main.py
  - lovable-handoff/openapi.json
  - tests/test_knowledge_bases.py
  - tests/test_kb_ingest.py
autonomous: true
requirements: [KB-01, KB-02, KB-03, KB-04]
must_haves:
  truths:
    - "User creates / lists / renames / deletes a workspace-scoped KB; another workspace cannot see or touch it"
    - "User uploads a file (multipart) or pastes text → a pending kb_document is recorded (202) and the worker indexes it"
    - "KB detail returns the D-09 aggregate (DOCUMENTS/INDEXED/PROCESSING/FAILED/STORAGE) + per-document list (D-10)"
    - "User attaches/detaches a KB to an agent (M:N) and can list which agents a KB is attached to (reverse M:N)"
    - "A document can be re-indexed (set pending) and deleted (cascades its chunks)"
  artifacts:
    - path: "app/routers/knowledge_bases.py"
      provides: "workspace-scoped KB CRUD + docs + search + attach/detach endpoints under AuthDep"
      contains: "router = APIRouter(prefix=\"/api/v1/knowledge-bases\""
    - path: "app/schemas/knowledge_bases.py"
      provides: "Pydantic request/response models"
      contains: "class KnowledgeBaseResponse"
    - path: "lovable-handoff/openapi.json"
      provides: "regenerated spec including the KB endpoints"
      contains: "/api/v1/knowledge-bases"
  key_links:
    - from: "app/main.py"
      to: "knowledge_bases router"
      via: "app.include_router registration in the routers import block"
      pattern: "knowledge_bases"
    - from: "POST /api/v1/knowledge-bases/{id}/documents"
      to: "kb_documents row status='pending'"
      via: "multipart upload mirroring contacts.py import"
      pattern: "UploadFile"
    - from: "all KB endpoints"
      to: "workspace isolation"
      via: "Depends(auth_dep) + .where(workspace_id == ctx.workspace_id)"
      pattern: "auth_dep"
---

<objective>
Expose the workspace-scoped KB API: KB CRUD, document upload (multipart, 202) +
paste-text, list documents, re-index a doc, delete a doc, the D-09 aggregate +
D-10 per-document detail, a manual `search_knowledge_base` endpoint (the Search
tab), and agent↔KB attach/detach + reverse list. Then regenerate the
`lovable-handoff/openapi.json` so the frontend (plan 16-05) builds against it.

Purpose: closes the user-facing backend surface for KB-01 (create/isolation),
KB-02 (ingest entry points), KB-03 (status aggregate + per-doc), KB-04 (M:N).
The upload endpoint mirrors the proven `contacts.py` multipart + 202-accepted
pattern — it records a `pending` doc and lets the Wave-2 worker do the indexing.

Output: `app/schemas/knowledge_bases.py`, `app/routers/knowledge_bases.py`, router
registration, regenerated openapi, and the KB CRUD/ingest tests turn GREEN.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-CONTEXT.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-UI-SPEC.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-01-SUMMARY.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-02-SUMMARY.md

<interfaces>
<!-- Reference implementations to mirror. -->

Router shape + workspace scoping (mirror exactly), app/routers/agents.py:40,165-178:
```python
from app.utils.auth import AuthCtx, auth_dep
router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

@router.get("", response_model=AgentListResponse)
async def list_agents(ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AIContext).where(AIContext.workspace_id == ctx.workspace_id) ...)
```

Multipart upload + 202 (mirror exactly), app/routers/contacts.py:301-371:
```python
@router.post("/import/preview", response_model=...)
async def import_preview(file: UploadFile = File(...), ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    raw = await file.read()
    if len(raw) > MAX_CSV_BYTES: raise HTTPException(413, detail={"code": "FILE_TOO_LARGE", ...})
    ...
@router.post("/import", response_model=ContactImportSummary, status_code=202)  # 202 Accepted: worker finishes async
```

ORM models from 16-01: KnowledgeBase, KbDocument(status, source_kind, size_bytes, error, chunk_count, raw_content),
KbChunk, AgentKnowledgeBase(agent_id, kb_id, workspace_id). Agent table is `ai_contexts` (AIContext).

Router registration, app/main.py:19-34 (routers import tuple) + the include_router calls below it.
Add `knowledge_bases` to the import tuple and an `app.include_router(knowledge_bases.router)` call.

Existing schemas dir: app/schemas/ (mirror an existing module's BaseModel + ConfigDict style).
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Pydantic schemas for KB + documents + search + attach</name>
  <read_first>
    - app/schemas/ (list the dir; read one existing module, e.g. the agents or campaigns schema, for BaseModel/ConfigDict/Field conventions)
    - app/routers/contacts.py (ContactImportSummary / preview response shapes for the 202 idiom)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-UI-SPEC.md (Surface 1 list columns, Surface 2 D-09 header + 5-metric row + D-10 per-doc fields, Search tab result shape)
  </read_first>
  <files>app/schemas/knowledge_bases.py</files>
  <action>
    Create `app/schemas/knowledge_bases.py` with these Pydantic v2 models (mirror existing
    schema module's `model_config = ConfigDict(from_attributes=True)` style):
    - `KnowledgeBaseCreate` — `name: str` (Field min_length 1, max_length 150), `description: Optional[str] = None`.
    - `KnowledgeBaseUpdate` — `name: Optional[str]`, `description: Optional[str]`.
    - `KnowledgeBaseResponse` — `id, name, description, source_kind, created_at, updated_at`
      PLUS the D-09 aggregate fields for the detail/list view:
      `documents: int, indexed: int, processing: int, failed: int, storage_bytes: int,
      status: str` (status derived: `failed`→has failed docs, else `processing`→any processing,
      else `indexed`/`ready` if ≥1 indexed, else `empty`).
    - `KnowledgeBaseListResponse` — `items: list[KnowledgeBaseResponse]`.
    - `KbDocumentResponse` — `id, kb_id, name, source_kind, size_bytes, status, error,
      chunk_count, created_at, updated_at` (D-10 per-doc list).
    - `KbDocumentListResponse` — `items: list[KbDocumentResponse]`.
    - `KbPasteTextRequest` — `name: str` (max 255), `content: str` (Field min_length 1).
    - `KbSearchRequest` — `query: str` (Field min_length 1), `top_k: Optional[int] = None`.
    - `KbSearchHit` — `content: str, document_id: UUID, document_name: Optional[str],
      distance: float`.
    - `KbSearchResponse` — `results: list[KbSearchHit]`.
    - `AgentKbAttachRequest` — `kb_id: UUID`.
    - `AgentForKbResponse` — `agent_id: UUID, agent_name: str` (reverse M:N, Agents tab).
    - `AgentForKbListResponse` — `items: list[AgentForKbResponse]`.
    Use `UUID` from `uuid` and `datetime` from `datetime`. No business logic in schemas.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "from app.schemas.knowledge_bases import KnowledgeBaseResponse, KbDocumentResponse, KbSearchResponse, AgentForKbResponse; print('OK')" 2>&1 | tail -3</automated>
  </verify>
  <acceptance_criteria>
    - `app/schemas/knowledge_bases.py` defines all listed classes (grep `class KnowledgeBaseResponse`, `class KbDocumentResponse`, `class KbSearchResponse`, `class AgentForKbResponse`)
    - `KnowledgeBaseResponse` carries the five D-09 aggregate fields (`documents`, `indexed`, `processing`, `failed`, `storage_bytes`) + `status`
    - Module imports cleanly inside the api container (the python -c check prints OK)
  </acceptance_criteria>
  <done>All KB request/response schemas exist and import; D-09 aggregate fields and D-10 per-doc fields modeled.</done>
</task>

<task type="auto">
  <name>Task 2: knowledge_bases router — CRUD, docs, search, attach/detach + registration</name>
  <read_first>
    - app/routers/agents.py (full: AuthDep scoping, _load helper with 404, list/create/patch/delete, duplicate-name 409 handling)
    - app/routers/contacts.py (lines 295-371: UploadFile multipart, MAX bytes 413, 202 status)
    - app/services/kb_search.py IF it exists yet (plan 16-04 owns it) — if not present, the manual search endpoint should import it lazily / share the helper signature `kb_search(db, workspace_id, kb_ids, query, top_k, max_distance)`; coordinate so 16-04 provides the canonical impl. For this plan, implement a thin self-contained vector query if 16-04 has not landed, OR import from kb_search and let it be the source of truth.
    - app/main.py (router import tuple 19-34 + include_router calls)
    - app/services/kb_ingest.py (embed_texts for the manual-search query embedding)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md (Pattern 2 cosine query, Pitfall 4 distance threshold)
    - tests/test_knowledge_bases.py + tests/test_kb_ingest.py (RED contracts)
  </read_first>
  <files>app/routers/knowledge_bases.py, app/main.py, tests/test_knowledge_bases.py, tests/test_kb_ingest.py</files>
  <action>
    Create `app/routers/knowledge_bases.py` — `router = APIRouter(prefix="/api/v1/knowledge-bases",
    tags=["knowledge-bases"])`. EVERY endpoint takes `ctx: AuthCtx = Depends(auth_dep)` and
    `db: AsyncSession = Depends(get_db)` and filters by `ctx.workspace_id`. Add a
    `_load_kb(db, ctx, kb_id) -> KnowledgeBase` helper that 404s
    (`KB_NOT_FOUND`) when the row is missing OR belongs to another workspace.

    Endpoints:
    - `GET ""` → list workspace KBs with the D-09 aggregate per KB. Compute the aggregate
      in ONE query per KB or a grouped query over kb_documents:
      `COUNT(*) documents, COUNT(*) FILTER (WHERE status='indexed') indexed,
       COUNT(*) FILTER (WHERE status='processing') processing,
       COUNT(*) FILTER (WHERE status='failed') failed, COALESCE(SUM(size_bytes),0) storage_bytes`
      GROUP BY kb_id (mirror the one-pass FILTER aggregate from app/routers/campaigns.py pool_health).
    - `POST ""` (201) → create KB (`name`, `description`, `source_kind='files'`); 409
      `KB_NAME_CONFLICT` on duplicate `(workspace_id, name)` if you add that guard (optional, mirror agents).
    - `GET "/{kb_id}"` → KB detail = `KnowledgeBaseResponse` incl. aggregate (D-09).
    - `PATCH "/{kb_id}"` → rename / description (Settings tab).
    - `DELETE "/{kb_id}"` → delete KB (FK cascade drops docs+chunks+agent links).
    - `GET "/{kb_id}/documents"` → per-doc list `KbDocumentListResponse` (D-10), newest-first.
    - `POST "/{kb_id}/documents"` (202) → multipart `file: UploadFile = File(...)`. Read blob
      (`raw = await file.read()`), reject > a sane max (e.g. 20 MB → 413 `FILE_TOO_LARGE`),
      derive `source_kind` from the filename extension (pdf/docx/txt/md/csv → else 422
      `UNSUPPORTED_FILE_TYPE`), INSERT a `kb_documents` row with `status='pending'`,
      `size_bytes=len(raw)`, `raw_content=raw`, `name=file.filename`. Return the
      `KbDocumentResponse` (the worker indexes it asynchronously).
    - `POST "/{kb_id}/documents/paste"` (202) → body `KbPasteTextRequest`; INSERT a
      `kb_documents` row `source_kind='text'`, `raw_content=content.encode('utf-8')`,
      `size_bytes=len(encoded)`, `status='pending'`, `name=payload.name`.
    - `POST "/{kb_id}/documents/{doc_id}/reindex"` (202) → set the doc `status='pending'`,
      `error=NULL` (the worker re-runs; chunk delete-then-insert is idempotent per 16-02).
    - `DELETE "/{kb_id}/documents/{doc_id}"` → delete the doc (cascade drops its chunks).
    - `POST "/{kb_id}/search"` → manual test search (Search tab). Embed `payload.query` via
      `kb_ingest.embed_texts([query], settings.openai_embedding_model)[0]`, run the cosine
      query over `kb_chunks` filtered by `workspace_id` + this `kb_id`, ORDER BY
      `embedding <=> :qvec` LIMIT `top_k or settings.kb_search_top_k`, keep
      `distance <= settings.kb_search_max_distance` (Pitfall 4 — distance, lower is better).
      Return `KbSearchResponse`. (Prefer importing the shared `kb_search` helper from
      `app/services/kb_search.py` when 16-04 has landed; keep the signature aligned.)
    - `GET "/{kb_id}/agents"` → reverse M:N: agents this KB is attached to
      (JOIN agent_knowledge_bases → ai_contexts), `AgentForKbListResponse` (Agents tab).
    - `POST "/{kb_id}/agents"` → attach: body `AgentKbAttachRequest` carrying an `agent_id`
      that the workspace owns (validate the agent is workspace-scoped → 404 else); INSERT
      `agent_knowledge_bases` row `ON CONFLICT DO NOTHING`.
    - `DELETE "/{kb_id}/agents/{agent_id}"` → detach: DELETE the M:N row.
    NB: agent-side attach is ALSO done from the agent form (16-05); these endpoints are the
    KB-side mirror + the reverse list. Both write the same `agent_knowledge_bases` table.

    Register in `app/main.py`: add `knowledge_bases` to the routers import tuple (19-34)
    and add `app.include_router(knowledge_bases.router)` alongside the others.

    Flesh out the RED tests to GREEN: `test_create_kb_workspace_isolated`,
    `test_kb_detail_aggregate`, `test_attach_detach_agent` (in test_knowledge_bases.py) and
    `test_upload_creates_pending_doc` (in test_kb_ingest.py).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_knowledge_bases.py tests/test_kb_ingest.py -x -q 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `app/routers/knowledge_bases.py` defines `router = APIRouter(prefix="/api/v1/knowledge-bases"...)` and every handler depends on `auth_dep`
    - The upload handler uses `UploadFile = File(...)`, returns 202, inserts `status='pending'` + `size_bytes` + `raw_content`
    - The paste handler stores `source_kind='text'` with utf-8-encoded `raw_content`
    - The list/detail handlers compute the D-09 aggregate via `COUNT(*) FILTER (...)` over kb_documents
    - `_load_kb` 404s on cross-workspace access (grep `KB_NOT_FOUND` or the 404 path)
    - `app/main.py` imports and `include_router`s `knowledge_bases`
    - `pytest tests/test_knowledge_bases.py tests/test_kb_ingest.py` exits 0 (KB-01/02/03/04 tests GREEN)
  </acceptance_criteria>
  <done>Full workspace-scoped KB API live (CRUD + docs + reindex + delete + manual search + M:N attach/detach + reverse list), router registered, KB-01/02/03/04 tests GREEN.</done>
</task>

<task type="auto">
  <name>Task 3: Regenerate lovable-handoff/openapi.json</name>
  <read_first>
    - lovable-handoff/ (find the export-handoff script — grep for a Makefile target / scripts/export-handoff; prior plans regenerated via "export-handoff flow, rebuild API container first")
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-CONTEXT.md (frontend is a SEPARATE repo generated from this openapi.json)
    - CLAUDE.md (Lovable-handoff quirks section — openapi regenerated, never hand-edited)
  </read_first>
  <files>lovable-handoff/openapi.json</files>
  <action>
    Regenerate `lovable-handoff/openapi.json` via the project's export-handoff flow (the same
    one used by Phase 11/12 plans — typically rebuild the api image so FastAPI serves the new
    routes, then run the export script that dumps `app.openapi()` to the handoff file). Do NOT
    hand-edit the spec. Locate the exact command in the repo (Makefile/scripts) and run it.
    If the script requires a running/rebuilt api container, that rebuild is acceptable here
    (it does NOT touch the db image — the OPS-gated pgvector swap is separate). After regen,
    confirm the KB paths appear in the spec.
  </action>
  <verify>
    <automated>grep -q "/api/v1/knowledge-bases" lovable-handoff/openapi.json && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `lovable-handoff/openapi.json` contains `/api/v1/knowledge-bases` and the documents/search/agents sub-paths
    - The file was produced by the export script (no manual JSON edits — diff shows only generated additions)
  </acceptance_criteria>
  <done>openapi handoff regenerated with the KB endpoints so plan 16-05 (frontend) builds against it.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_knowledge_bases.py tests/test_kb_ingest.py` GREEN.
- Full suite GREEN after the wave: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`.
- `grep "/api/v1/knowledge-bases" lovable-handoff/openapi.json` matches.
</verification>

<success_criteria>
- Workspace-scoped KB CRUD + document upload/paste (202) + list + reindex + delete + manual search + agent attach/detach + reverse list, all under AuthDep.
- D-09 aggregate + D-10 per-doc returned.
- Router registered in main.py; openapi handoff regenerated.
- KB-01/02/03/04 tests GREEN.
</success_criteria>

<output>
After completion, create `.planning/phases/16-rag-knowledge-bases-for-agents/16-03-SUMMARY.md`
</output>
