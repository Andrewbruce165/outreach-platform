# Phase 16: RAG Knowledge Bases for Agents - Research

**Researched:** 2026-06-30
**Domain:** RAG (retrieval-augmented generation) on PostgreSQL/pgvector, OpenAI embeddings, async ingest pipeline, OpenAI function-calling
**Confidence:** HIGH (stack + architecture grounded in existing code + verified library docs); MEDIUM on tuning constants (chunk size, top-K, threshold — defaults given, tune in flight)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** v1 ingest = manual text paste **+** file upload (PDF, DOCX, TXT, MD, CSV). URL/site crawl is NOT in v1 (deferred).
- **D-02:** Ingest pipeline: source → text extraction by file type → chunking → embeddings → write chunks-with-vectors to pgvector. File upload via multipart (existing pattern — see code_context).
- **D-03:** Retrieval is a **tool-call** — the agent itself decides to call `search_knowledge_base` "as needed". NOT auto-retrieval on every message.
- **D-04:** Tool `search_knowledge_base` is exposed to the agent **iff** the agent has ≥1 attached KB. The call searches across **all KBs attached to the agent** (one search over the union). Result returns to the model as a **tool-result message (data)**, then the model continues its answer — this is NOT a signal-tool (unlike mark_as_lead/transfer_to_manager/finish_conversation which change conversation state).
- **D-05:** Vectors live in **pgvector inside the current PostgreSQL 16** (same DB → workspace isolation + backups for free, no new infra). db image changes to `pgvector/pgvector:pg16` (or `CREATE EXTENSION vector`), migration idempotent.
- **D-06:** Embedding model = **OpenAI `text-embedding-3-small` (1536 dims)**. The dimension fixes the vector-column type. Model name → env knob (`OPENAI_EMBEDDING_MODEL`), default 3-small.
- **D-07:** **M:N agent↔KB** (table `agent_knowledge_bases`). KB attaches at **agent level only** (not campaign in v1). KBs reusable across agents within a workspace.
- **D-08:** The static `ai_contexts.knowledge_base` Text column (Phase 11) **stays alongside** the new RAG mechanism. The static block keeps filling the `[БАЗА ЗНАНИЙ]` prompt slot (short always-in-prompt facts); RAG KB is a separate mechanism via tool-call. No breaking existing agents, no content migration.
- **D-09:** KB detail header shows aggregate counters `DOCUMENTS / INDEXED / PROCESSING / FAILED / STORAGE (bytes)`, plus KB `Status` (e.g. READY), `Type` (e.g. Files), `Updated`.
- **D-10:** Documents tab shows per-document indexing status (indexed/processing/failed), size, date.
- **D-11:** KB detail tabs: **Documents · Search · Agents · Settings**. Tab set + two stat levels (KB-aggregate + per-document) are locked; exact layout/fields deferred to the UI phase.
- **D-12:** KB screens render in the **existing light theme** (Telegram-blue, `aimly.css`). The dark reference screenshot is layout/IA only. App-wide dark mode is a separate phase (deferred).

### Claude's Discretion
- Chunking strategy (size/overlap), top-K and threshold in `search_knowledge_base`, tool-result format, pgvector index (HNSW vs IVFFlat), empty-result handling (inherit existing off-topic ai_engine behavior), PDF/DOCX text extraction (library choice) — implementation detail, decided by research/plan.
- Exact KB-view visual layout, Settings-tab fields, Search-tab behavior (threshold/result count in the test search) — detailed in `/gsd:ui-phase 16`.

### Deferred Ideas (OUT OF SCOPE)
- URL / site crawling as a KB source.
- KB at campaign level.
- Replacing/migrating the static `knowledge_base` field onto RAG.
- KB-usage analytics (which chunks/docs actually influenced answers).
- Sharing KB across workspaces / a marketplace of ready bases.
- App-wide dark mode.
</user_constraints>

<phase_requirements>
## Phase Requirements

No formal REQ-IDs exist yet (derived during `/gsd:plan-phase 16`). Coverage maps to the CONTEXT.md / ROADMAP acceptance criteria:

| ID (provisional) | Behavior (from acceptance) | Research Support |
|------------------|----------------------------|------------------|
| KB-01 | User creates a workspace-isolated KB | Data model §"Data Model & Cascades"; `knowledge_bases` table mirrors `Folder`/`Campaign` workspace_id pattern |
| KB-02 | User uploads files / pastes text into a KB | Ingest §"File Upload + Text Extraction"; reuses `UploadFile = File(...)` pattern from contacts.py:301-348 |
| KB-03 | User sees indexing status per-doc + KB aggregate | Data model status columns; Background Ingest Worker §; UI poll via `refetchInterval` (UI-SPEC) |
| KB-04 | KB attaches to an agent (M:N) | `agent_knowledge_bases` through-table (mirrors `CampaignSender`); D-07 |
| KB-05 | Agent retrieval works via tool-call; KB knowledge visibly influences answers | `search_knowledge_base` tool wiring §; data-tool path in ai_engine generate_response |
| KB-06 | KBs are workspace-isolated | Every table carries `workspace_id` FK ON DELETE CASCADE; search filters by workspace + attached KBs |
</phase_requirements>

## Summary

Phase 16 is a textbook RAG-on-Postgres feature, and the codebase already contains every structural pattern it needs — there is almost nothing novel to invent, only to assemble. The vector store is **pgvector in the existing PostgreSQL 16** (D-05), embeddings come from the **already-instantiated `AsyncOpenAI` client** in `ai_engine.py` (D-06), the ingest worker mirrors `ContactCheckWorker`'s exact lifecycle (`start()`/`stop()` from `app/main.py` lifespan), file upload reuses the `UploadFile = File(...)` + `await file.read()` blob pattern from `contacts.py`, the M:N join-table mirrors `CampaignSender`, and the new tables go in idempotent raw-SQL migrations (`040+`) auto-applied at api start exactly like `038_warmup_settings.sql`.

The single genuinely new wiring is the **data-tool branch in `ai_engine.generate_response`**. The existing loop already has two tool paths: signal-tools (`mark_as_lead`/`transfer_to_manager`/`finish_conversation`, terminating) and custom webhook tools (two-pass: call → tool-result message → second LLM call). `search_knowledge_base` is exactly the **second shape** — it returns chunks as a `role:"tool"` message and the model continues. The cleanest implementation registers it alongside built-in tools (only when the agent has ≥1 KB, D-04), runs the vector search in the dispatch loop, appends the result as a tool message, and lets the existing two-pass `response2` flow produce the final reply. No new LLM-loop architecture is required.

**Primary recommendation:** Switch the db image to `pgvector/pgvector:pg16` (re-specifying the existing `command:` block), add `CREATE EXTENSION IF NOT EXISTS vector;` + four tables in migration `040`, store the vector as `pgvector.sqlalchemy.Vector(1536)` in the ORM (or raw-SQL-only with a server-side default and ORM treating reads via a typed query), index each KB's chunks with **HNSW + `vector_cosine_ops`** (no training step, builds on an empty table), chunk text at **~800 tokens with ~120-token overlap (tiktoken `cl100k_base`)**, embed in batches via `client.embeddings.create`, wire `search_knowledge_base` as a data-tool with **top-K=5 / cosine-distance threshold ≤ 0.55** defaults, and add a `KnowledgeIngestWorker` to the lifespan that claims `kb_documents` with status `pending`, extracts→chunks→embeds→stores, and flips per-doc status so the UI can poll.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pgvector` (Python) | `0.4.2` | SQLAlchemy `Vector` type + distance operators (`cosine_distance` → `<=>`) | Official pgvector Python binding; integrates directly with SQLAlchemy 2.0 (`pgvector.sqlalchemy.Vector`). Verified current via `pip index versions` 2026-06-30. |
| `pgvector/pgvector:pg16` (Docker image) | `pg16` tag | PostgreSQL 16 **with the `vector` extension precompiled** | D-05 locked. Same PG16 major → the existing `postgres_data` volume mounts unchanged (same data dir layout). Official pgvector image. |
| OpenAI Python SDK | `>=1.40.0,<2.0.0` (already pinned) | `client.embeddings.create(model=..., input=[...])` | Already in `requirements.txt`; the same `AsyncOpenAI` instance in `ai_engine.py` does embeddings. No new dependency. |
| `tiktoken` | `0.13.0` | Token-accurate chunk sizing for `text-embedding-3-small` (`cl100k_base` encoding) | Official OpenAI tokenizer. Lets chunking respect the 8191-token model limit precisely instead of guessing chars. Verified current 2026-06-30. |

### Supporting (text extraction — D-01 file types)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pypdf` | `6.14.2` | PDF → text | PDF uploads. Pure-Python, no system deps; successor to PyPDF2. CPU-bound → run via `asyncio.to_thread`. Verified current 2026-06-30. |
| `python-docx` | `1.2.0` | DOCX → text (iterate `document.paragraphs`) | DOCX uploads. CPU-bound → `asyncio.to_thread`. Verified current 2026-06-30. |
| (stdlib) `str.decode` / `csv` | — | TXT, MD, CSV → text | Plain text/markdown decode as UTF-8 (with latin-1 fallback, mirroring contacts CSV encoding sniff). CSV: read with stdlib `csv` and join rows to text, or just decode raw. **No new dependency.** |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `pgvector` in existing PG | Qdrant / Pinecone / pgvector in a separate DB | D-05 locked: separate infra loses free workspace-isolation + the existing backup/restore story (`backup.sh`). Rejected by decision. |
| HNSW index | IVFFlat | IVFFlat needs **training data** (build after rows exist) and re-tuning `lists` as data grows; HNSW builds on an empty table, no training, better recall at these sizes. See §Index Choice. |
| `pypdf` | `pdfplumber` / `PyMuPDF` (`fitz`) | pdfplumber/PyMuPDF extract layout/tables better but add heavier deps (PyMuPDF is AGPL/commercial-licensed). `pypdf` is pure-Python, permissive, sufficient for v1 plain-text extraction. Keep footprint minimal. |
| `tiktoken` token chunking | naive char-count chunking | Char chunking risks exceeding the 8191-token embed limit on dense text and produces uneven chunks. tiktoken is one small dep already maintained by OpenAI; worth it. (Acceptable fallback if dep is undesired: char-based ~3500 chars ≈ 800 tokens — flag as LOW precision.) |

**Installation (append to `requirements.txt`):**
```
# Phase 16 — RAG knowledge bases
pgvector==0.4.2
tiktoken==0.13.0
pypdf==6.14.2
python-docx==1.2.0
```

**Version verification (run before pinning in the plan — registry can move):**
```bash
pip index versions pgvector pypdf python-docx tiktoken
```
All four verified current on 2026-06-30: `pgvector 0.4.2`, `pypdf 6.14.2`, `python-docx 1.2.0`, `tiktoken 0.13.0`.

> Note: `pypdf`/`python-docx`/`tiktoken` are imported **only** inside the ingest worker (api container). The listener container (`Dockerfile.listener`) does not need them, but both build from the same `requirements.txt` — harmless extra weight. Confirm the `Dockerfile` `COPY app/` step already ships `app/data/` (it does — control_set file proof at `contact_check_worker.py:56`), so any bundled assets travel.

## Architecture Patterns

### Recommended module structure
```
app/
├── models/__init__.py          # + KnowledgeBase, KbDocument, KbChunk, AgentKnowledgeBase (ORM mirror)
├── routers/
│   └── knowledge_bases.py      # NEW — CRUD KB, upload/paste docs, list docs, test-search, attach/detach agents
├── services/
│   ├── kb_ingest.py            # NEW — extract_text(), chunk_text(), embed_chunks() (the pipeline pieces)
│   ├── kb_ingest_worker.py     # NEW — KnowledgeIngestWorker (lifespan start/stop, mirrors ContactCheckWorker)
│   ├── kb_search.py            # NEW — search_knowledge_base() vector query over attached KBs
│   └── ai_engine.py            # EDIT — register search_knowledge_base data-tool + dispatch branch
├── schemas/
│   └── knowledge_bases.py      # NEW — Pydantic request/response models
└── main.py                     # EDIT — import + start/stop KnowledgeIngestWorker in lifespan
migrations/
└── 040_knowledge_bases.sql     # NEW — CREATE EXTENSION vector + 4 tables + HNSW index (idempotent)
```

### Pattern 1: Vector column declaration (ORM mirror of a raw-SQL migration)
The project builds prod schema from migrations but builds the **test-overlay** schema from `Base.metadata.create_all` (conftest `_build_outreach_schema`). So the ORM **must** mirror new columns or test schema diverges. For the vector column:

```python
# Source: pgvector-python README (github.com/pgvector/pgvector-python) — verified 2026-06-30
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column

class KbChunk(Base):
    __tablename__ = "kb_chunks"
    # ... workspace_id, kb_id, document_id, content, chunk_index ...
    embedding = Column(Vector(1536), nullable=False)   # 1536 = text-embedding-3-small (D-06)
```

`create_all` emits `embedding VECTOR(1536)` only if the `vector` extension already exists in the test DB — so the **migration's `CREATE EXTENSION` must also run in the test-overlay**. Two safe options (pick one in the plan):
- **(A) Preferred:** the test-overlay applies migrations after `create_all` (conftest already layers migrations); ensure `040` is in the applied range so `CREATE EXTENSION` runs before/independent of the ORM build. Since `create_all` runs first and references `Vector`, the extension must exist at `create_all` time → add a tiny `CREATE EXTENSION IF NOT EXISTS vector` step to the conftest `_build_outreach_schema` **before** `create_all` (mirror the existing "context_contact_assignments stub" pre-step). Document this in the plan as a Wave-0 test-infra change.
- **(B) Fallback:** keep the column as raw-SQL-only in the migration and declare the ORM column as `Column(Text)` placeholder is **wrong** (create_all would make a text column and break vector inserts in tests). Avoid; use (A).

### Pattern 2: Cosine-distance similarity query (async SQLAlchemy)
```python
# Source: pgvector-python README — cosine_distance maps to the <=> operator
from sqlalchemy import select
# query_vec: list[float] of length 1536 (the embedded user query)
stmt = (
    select(
        KbChunk.id, KbChunk.content, KbChunk.document_id,
        KbChunk.embedding.cosine_distance(query_vec).label("distance"),
    )
    .where(
        KbChunk.workspace_id == ctx.workspace_id,        # workspace isolation (D-05/KB-06)
        KbChunk.kb_id.in_(attached_kb_ids),              # union of agent's attached KBs (D-04)
    )
    .order_by(KbChunk.embedding.cosine_distance(query_vec))
    .limit(top_k)
)
rows = (await db.execute(stmt)).all()
hits = [r for r in rows if r.distance <= DISTANCE_THRESHOLD]   # threshold filter (see Pitfall 4)
```
`cosine_distance` returns `1 - cosine_similarity` (0 = identical, 2 = opposite). With `vector_cosine_ops` HNSW index, the `ORDER BY ... cosine_distance LIMIT k` is index-accelerated. **Raw `text()` is acceptable too** (project uses `text()` heavily) but the typed `.cosine_distance()` is cleaner and binds the vector parameter correctly — prefer it. If raw SQL is used, the operator is `embedding <=> :qvec` and the param must be passed as a pgvector-formatted string or via the registered type.

### Pattern 3: Background worker (mirror ContactCheckWorker exactly)
The ingest worker copies the proven shape at `contact_check_worker.py:135-209`:
```python
class KnowledgeIngestWorker:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.poll_interval = int(os.environ.get("KB_INGEST_POLL_INTERVAL", "5"))
    def start(self):           # idempotent; create_task(self._run(), name="kb-ingest-worker")
    async def stop(self):      # set _running=False; cancel + await; swallow CancelledError
    async def _run(self):      # while _running: try: await self._tick(); except CancelledError: break
    async def _tick(self):     # claim 1 pending doc → extract → chunk → embed → store → status flip
kb_ingest_worker = KnowledgeIngestWorker()   # module-scope singleton
```
Register in `app/main.py` lifespan next to `contact_check_worker.start()` / `.stop()` (main.py:60-61, 70-71).

### Anti-Patterns to Avoid
- **Running CPU-bound parsing/embeddings in the request handler.** Upload endpoint must return fast (202-style, like `import_contacts` at contacts.py:371) and let the worker do extract/chunk/embed. CLAUDE.md "async everywhere" + the worker pattern demand this.
- **Calling `pypdf`/`python-docx` directly in an `async def` without a threadpool.** They are sync/CPU-bound and block the event loop. Wrap: `text = await asyncio.to_thread(_extract_pdf, blob)`.
- **Re-using the send-queue rate-limit knobs for embeddings.** Unrelated subsystem; embeddings have their own OpenAI rate limits. Do NOT touch `queue.py` constants (CLAUDE.md guard).
- **Adding the `search_knowledge_base` tool unconditionally.** D-04: expose it **only** when the agent has ≥1 attached KB, else the model may hallucinate calls with no data.
- **Treating `search_knowledge_base` like a signal-tool.** It must NOT terminate the loop or change `conversation.status`; it returns data and the model continues (the two-pass custom-tool path, not the `_handle_builtin_signal` path).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Vector storage + ANN search | Custom distance-in-Python over all rows | `pgvector` + HNSW index | Hand-rolled brute force doesn't scale, no index, reinvents a mature C extension. |
| PDF text extraction | Regex over raw PDF bytes | `pypdf` | PDF is a binary container format with compression/encoding; parsing it by hand is a multi-week trap. |
| DOCX text extraction | Unzip + XML parse by hand | `python-docx` | DOCX is a zipped OOXML package; the lib handles namespaces/relationships. |
| Token-accurate chunking | Char-count guessing | `tiktoken` (`cl100k_base`) | The embed model has a hard 8191-token limit; char heuristics over/under-shoot on dense or CJK/Cyrillic text. |
| Distance operator SQL | String-formatting the `<=>` query by hand | `pgvector.sqlalchemy` `cosine_distance()` | Correct parameter binding (vector literal formatting) + index-operator-class match are easy to get subtly wrong in raw SQL. |
| Embedding HTTP + retry | New httpx client | existing `AsyncOpenAI` client in `ai_engine.py` | Already configured with the API key; SDK handles retries/error types (`RateLimitError`, `APIError`) the codebase already catches. |

**Key insight:** This phase is 90% assembly of existing project patterns (workers, multipart upload, M:N join-tables, idempotent migrations, the OpenAI client) + one mature extension (pgvector). The risk is not in any single piece but in the **test-overlay schema (vector extension) and the ai_engine data-tool branch** — focus review there.

## Runtime State Inventory

> Phase 16 is **greenfield additive** (new tables, new tool, new worker) — it renames/migrates nothing. Most categories are N/A, but the Docker image swap (D-05) and a few env/build concerns are real.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | New tables only (`knowledge_bases`, `kb_documents`, `kb_chunks`, `agent_knowledge_bases`). No existing data is renamed or re-keyed. The existing `postgres_data` volume is **reused as-is**. | None — additive. Verify volume survives image swap (below). |
| Live service config | **Docker db image swap `postgres:16` → `pgvector/pgvector:pg16`** (D-05). This is a config change in `docker-compose.yml` not yet in any external UI. The `command:` block (`log_statement=ddl`, `log_min_duration_statement=1000`) **must be re-specified** — it is per-service config, the new image does not inherit it. The `pgvector/pgvector:pg16` image is `postgres:16` + the extension, so the same `postgres` entrypoint accepts the same `-c` flags. | Edit `docker-compose.yml`: change `image:`, keep the entire `command:` block verbatim. `docker compose up -d db` recreates the container against the **same** `postgres_data` volume (same PG16 data dir — no re-init, no data loss). **Never `docker compose down -v`** (MEMORY: wipes prod volume). |
| OS-registered state | None — no Task Scheduler / systemd / pm2 entries reference KB. | None. |
| Secrets / env vars | New optional knob `OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`, D-06) + optional `KB_INGEST_POLL_INTERVAL`. Reuse existing `OPENAI_API_KEY` (already injected to api + listener). No secret rename. | Add `openai_embedding_model` field to `app/config.py::Settings` (mirror `openai_model` at config.py:54). Add `OPENAI_EMBEDDING_MODEL` to `docker-compose.yml::api::environment` only if overriding; the Pydantic default covers the unset case. |
| Build artifacts / installed packages | New pip deps (`pgvector`, `tiktoken`, `pypdf`, `python-docx`) → both api and listener images must rebuild. `tiktoken` downloads its BPE vocab on first use — bundle or allow network at runtime. | `docker compose up -d --build api` (and `--build listener`) after adding to `requirements.txt`. Note: `tiktoken.get_encoding("cl100k_base")` fetches the encoding file on first call; the api container has outbound network, so this works, but it adds a one-time cold-start fetch — acceptable, or pre-warm at worker init. |

**Canonical question — "after every file is updated, what runtime still has the old string?"** N/A for renames. The one real runtime concern is the **image swap**: confirmed safe because same PG16 major + same data-dir layout + same volume mount; the `command:` flags must be preserved on the new image (verified the pgvector image uses the stock `postgres` entrypoint).

## Common Pitfalls

### Pitfall 1: Test-overlay schema lacks the `vector` extension
**What goes wrong:** `Base.metadata.create_all` runs first in conftest (`_build_outreach_schema`) and references `Vector(1536)`; if `CREATE EXTENSION vector` hasn't run on the ephemeral test DB, `create_all` raises `type "vector" does not exist` and the **entire test suite fails to set up**.
**Why it happens:** Test schema is built from the ORM, not migrations; the extension is a migration concern.
**How to avoid:** Add `await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")` to `_build_outreach_schema` **before** `create_all` (mirror the existing `context_contact_assignments` stub pre-step in conftest). The test image must be `pgvector/pgvector:pg16` too — update `docker-compose.test.yml`'s `db-test` image. **This is a Wave-0 task.**
**Warning signs:** `type "vector" does not exist` at test setup; green prod but red tests.

### Pitfall 2: Image swap loses the `command:` flags (anti-drift logging)
**What goes wrong:** Changing `image:` but dropping/forgetting the `command:` block silently disables `log_statement=ddl` — the exact safety net added after the 2026-05-26 prod-wipe incident.
**Why it happens:** `command:` is per-service; people change `image:` in isolation.
**How to avoid:** Copy the `command:` block verbatim onto the pgvector service. Verify after deploy: `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SHOW log_statement;"` must return `ddl`.
**Warning signs:** DDL no longer appears in `docker logs outreach-platform-db`.

### Pitfall 3: Blocking the event loop with sync parsers/embeddings
**What goes wrong:** `pypdf.PdfReader(io.BytesIO(blob))` or a large `tiktoken` encode in an `async def` blocks the single event loop → every other request/worker stalls.
**How to avoid:** `await asyncio.to_thread(_extract_and_chunk, blob, kind)`. The embedding HTTP call is already async via `client.embeddings.create` (await it), but the CPU work around it (decoding, tiktoken) goes in the thread.
**Warning signs:** API latency spikes while a large doc indexes; healthcheck flaps.

### Pitfall 4: Cosine-distance threshold semantics inverted
**What goes wrong:** Filtering `WHERE similarity >= 0.7` when pgvector returns **distance** (`1 - similarity`). You'd keep the worst matches and drop the best.
**Why it happens:** `cosine_distance` is distance (lower = better), not similarity.
**How to avoid:** Filter `distance <= threshold`. Sensible default **`DISTANCE_THRESHOLD = 0.55`** (≈ similarity ≥ 0.45) — generous enough not to starve the model, tight enough to drop noise. Tune against the Search tab during QA. Document the chosen number as an env knob if you want tunability (`KB_SEARCH_MAX_DISTANCE`).
**Warning signs:** Search tab returns garbage top results, or returns nothing on obviously-relevant queries.

### Pitfall 5: Exposing the tool with zero indexed chunks
**What goes wrong:** Agent has a KB attached but every doc is still `processing` (or `failed`) → `search_knowledge_base` returns empty, model may apologize confusingly or hallucinate.
**How to avoid:** D-04 gate is "≥1 attached KB", but pragmatically also handle the **empty-result** case by inheriting the existing off-topic behavior (`_PROMPT_OUT_OF_SCOPE` at ai_engine.py:593 — "say something neutral, then transfer_to_manager"). Make the tool-result message explicit on empty: e.g. `{"results": [], "note": "no relevant passages found"}` so the model knows to fall back rather than invent.
**Warning signs:** Agent invents facts when KB has no indexed content.

### Pitfall 6: Embedding batch exceeds OpenAI per-request limits
**What goes wrong:** A huge doc → hundreds of chunks → one `embeddings.create(input=[...])` call exceeds the **2048-items / 300,000-tokens-per-request** limit → 400 error.
**Why it happens:** Naively embedding all chunks of a doc in one call.
**How to avoid:** Batch chunks in groups well under both ceilings (e.g. ≤256 chunks/request, and sum tokens < ~250k). At ~800 tokens/chunk, ~256 chunks ≈ 205k tokens — safe. Loop batches per document.
**Warning signs:** `BadRequestError` / 400 from `embeddings.create` on large files.

### Pitfall 7: `text-embedding-3-small` silently truncates > 8191 tokens
**What goes wrong:** A single chunk over 8191 tokens is silently truncated by the API → lost content, no error.
**How to avoid:** tiktoken-based chunking with a max of ~800 tokens/chunk is far below the limit by design; just never let a chunk exceed the limit. (This is precisely why token-based chunking beats char-based.)
**Warning signs:** Long documents retrieve poorly; tail content never matches.

### Pitfall 8: Re-index must be idempotent (delete-then-insert chunks)
**What goes wrong:** "Re-index" (D-09 header `RefreshCw`, D-10 per-row on Failed) re-runs ingest and **doubles** the chunks if it only inserts.
**How to avoid:** Re-index = `DELETE FROM kb_chunks WHERE document_id = :id` then re-extract/chunk/embed/insert, inside the worker claim. Same for a failed doc retried. Set status `pending`→worker picks up→`processing`→`indexed`/`failed`.
**Warning signs:** Chunk counts climb on every re-index; duplicate search hits.

## Code Examples

### Example 1: Embedding chunks via the existing AsyncOpenAI client (D-06)
```python
# Source: OpenAI Python SDK embeddings API (platform.openai.com/docs/api-reference/embeddings)
# Reuse the module-level client already in ai_engine.py:39 (or import a shared one).
from openai import AsyncOpenAI
import os
_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

async def embed_texts(texts: list[str], model: str) -> list[list[float]]:
    # Batch under the 2048-item / 300k-token per-request ceiling (Pitfall 6).
    resp = await _client.embeddings.create(model=model, input=texts)
    # resp.data is ordered to match input order.
    return [d.embedding for d in resp.data]   # each is len 1536 for 3-small
```
`model` comes from `settings.openai_embedding_model` (default `text-embedding-3-small`). Wrap calls in the same `RateLimitError`/`APIError` handling the engine already uses (ai_engine.py:1385-1407) and, on failure, flip the doc to `failed` for re-index.

### Example 2: Token-based chunking (tiktoken)
```python
# Source: tiktoken usage + text-embedding-3-small limits (8191 tokens)
import tiktoken
_enc = tiktoken.get_encoding("cl100k_base")   # encoding for text-embedding-3 family

def chunk_text(text: str, max_tokens: int = 800, overlap: int = 120) -> list[str]:
    toks = _enc.encode(text)
    chunks, start = [], 0
    while start < len(toks):
        end = min(start + max_tokens, len(toks))
        chunks.append(_enc.decode(toks[start:end]))
        if end == len(toks):
            break
        start = end - overlap          # sliding window with overlap
    return chunks
# Run inside asyncio.to_thread for large docs (Pitfall 3).
```
~800/120 is a sensible default for `text-embedding-3-small`: large enough to hold a coherent passage, small enough that top-K=5 fits comfortably in the prompt, with overlap to avoid splitting answers across a boundary.

### Example 3: `search_knowledge_base` tool definition + data-tool dispatch
```python
# Tool spec — register ONLY when the agent has >=1 attached KB (D-04).
SEARCH_KB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": (
            "Search the agent's attached knowledge bases for relevant reference "
            "material. Call this when the contact asks something that may be answered "
            "by stored documents/facts. Returns relevant passages; use them to answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query."}
            },
            "required": ["query"],
        },
    },
}
```
Wiring in `generate_response` (ai_engine.py): `search_knowledge_base` is NOT in `BUILT_IN_TOOL_NAMES`. In the dispatch loop (ai_engine.py:1255-1270), treat it like a **custom call** that returns a tool-result, BUT resolve it locally (not via webhook). Cleanest: add a dedicated branch:
```python
# inside the tool_call loop, alongside the BUILT_IN check:
if func_name == "search_knowledge_base":
    hits = await kb_search(db=session, workspace_id=context["workspace_id"],
                           agent_id=context["agent_id"], query=func_args.get("query",""))
    tool_results[tool_call.id] = json.dumps(
        {"results": hits} if hits else {"results": [], "note": "no relevant passages found"},
        ensure_ascii=False,
    )
    # ensure this tool_call.id is appended as a role:"tool" message and the
    # existing two-pass response2 flow (ai_engine.py:1342-1383) produces the reply.
```
Then the existing second-LLM-call block (`messages.append(response_message)` + `role:"tool"` messages + `response2`) runs unchanged and the model continues. **Key design point:** the current code only enters the two-pass flow when `custom_calls` is non-empty (ai_engine.py:1310). The plan must ensure a `search_knowledge_base` call also triggers that two-pass flow (e.g. add KB hits to `tool_results` and include its `tool_call` in the messages-append list). This is the central edit the planner must spec precisely.

### Example 4: Migration 040 skeleton (idempotent, mirrors 038/030 style)
```sql
-- migrations/040_knowledge_bases.sql — Phase 16 RAG KBs (D-05/D-07).
-- Idempotent: CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS — auto-applier re-runs safely.
-- Fail-fast: api does NOT start if this raises. Auto-applied via _apply_migrations.
BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name          VARCHAR(150) NOT NULL,
    description   TEXT,
    source_kind   VARCHAR(20) NOT NULL DEFAULT 'files',   -- D-09 "Type: Files" (room for future kinds)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_workspace ON knowledge_bases (workspace_id);

CREATE TABLE IF NOT EXISTS kb_documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name          VARCHAR(255) NOT NULL,
    source_kind   VARCHAR(20) NOT NULL,        -- 'pdf'|'docx'|'txt'|'md'|'csv'|'text'
    size_bytes    BIGINT NOT NULL DEFAULT 0,   -- D-09 STORAGE accounting
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|processing|indexed|failed
    error         TEXT,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    raw_content   BYTEA,                        -- original blob (re-index source); or TEXT for pasted
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kbdoc_kb_status ON kb_documents (kb_id, status);
-- worker claim pattern: WHERE status='pending' ORDER BY created_at — see worker §.

CREATE TABLE IF NOT EXISTS kb_chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    document_id   UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    embedding     VECTOR(1536) NOT NULL,        -- D-06 text-embedding-3-small
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- HNSW, cosine ops — no training step, builds on empty table (see Index Choice).
CREATE INDEX IF NOT EXISTS idx_kbchunk_embedding_hnsw
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);
-- Plain btree for the workspace+kb filter that scopes the ANN search.
CREATE INDEX IF NOT EXISTS idx_kbchunk_ws_kb ON kb_chunks (workspace_id, kb_id);

CREATE TABLE IF NOT EXISTS agent_knowledge_bases (
    agent_id      UUID NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, kb_id)              -- mirrors campaign_senders composite PK
);
CREATE INDEX IF NOT EXISTS idx_akb_kb ON agent_knowledge_bases (kb_id);  -- reverse M:N (Agents tab, D-11)

COMMIT;
```

## Index Choice (HNSW vs IVFFlat) — Recommendation

**Use HNSW with `vector_cosine_ops`.** Rationale (verified against pgvector docs, 2026-06-30):

- **No training step / builds on an empty table.** IVFFlat requires existing rows to compute its `lists` centroids and recommends building "after the table has some data"; rebuilding/re-tuning `lists` as KBs grow is operational overhead. HNSW "can be created without any data in the table" — perfect for the auto-applied migration that runs `CREATE INDEX` on day one before any chunk exists.
- **Better recall at small/moderate sizes** (hundreds–low-thousands of chunks per workspace), which is exactly D-09's expected scale.
- **DDL:** `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops);` — defaults `m=16, ef_construction=64` are appropriate for these sizes; no need to override. (Match the operator class to the query operator: cosine query → `vector_cosine_ops` → `cosine_distance`/`<=>`.)
- **Filtered search caveat:** the search always filters `workspace_id` + `kb_id IN (...)`. With HNSW + a filter, Postgres applies the filter and the ANN scan; at these row counts recall stays high. The added btree `(workspace_id, kb_id)` helps the planner. If recall ever degrades under heavy filtering at larger scale, pgvector's iterative index scans are the lever — out of scope for v1.

**One-line answer:** HNSW, cosine ops, default params, plus a btree on the filter columns.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| PyPDF2 | `pypdf` (6.x) | PyPDF2 merged back into `pypdf` ~2023 | Use `pypdf`; PyPDF2 is deprecated. |
| `text-embedding-ada-002` | `text-embedding-3-small` (1536) | early 2024 | Cheaper, better; supports `dimensions` param to shrink. D-06 locks 3-small. |
| IVFFlat-only pgvector | HNSW added in pgvector 0.5.0 | mid-2023 | HNSW is now the default recommendation for most workloads. |

**Deprecated/outdated:**
- PyPDF2 — replaced by `pypdf`.
- Hand-rolled cosine loops / numpy brute force for retrieval — superseded by pgvector ANN indexes.

## Open Questions

1. **Pasted text vs file blob storage shape.**
   - What we know: D-01 supports both paste and upload; `kb_documents.raw_content BYTEA` stores the original for re-index; pasted text could go in BYTEA (utf-8 encoded) or a separate TEXT column.
   - What's unclear: whether to keep a separate `raw_text TEXT` column for pasted docs vs encoding into BYTEA.
   - Recommendation: single `raw_content BYTEA` for uniformity (pasted text → `.encode('utf-8')`); `source_kind='text'` discriminates. Simpler re-index path. Planner decides.

2. **STORAGE metric definition (D-09).**
   - What we know: D-09 shows `STORAGE` in bytes per KB.
   - What's unclear: whether STORAGE = sum of original `size_bytes` (uploaded file bytes) or sum of stored chunk text bytes or includes vector bytes.
   - Recommendation: `SUM(kb_documents.size_bytes)` (original uploaded/pasted byte size) — most intuitive to the user ("how much did I upload"), trivially computed. Vector storage is an internal detail. Planner confirms with UI phase.

3. **Test-overlay extension bootstrap (Pitfall 1).**
   - What we know: conftest builds schema via `create_all` before migrations; `Vector` needs the extension present at `create_all` time.
   - What's unclear: exact insertion point in `_build_outreach_schema`.
   - Recommendation: add `CREATE EXTENSION IF NOT EXISTS vector` as the first statement after `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` (conftest ~line "1. Wipe + create base tables") and bump `docker-compose.test.yml` `db-test` image to `pgvector/pgvector:pg16`. Wave-0 task.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL 16 (prod db) | Vector store | ✓ | 16 (`postgres:16`) | — (swap to pgvector image, same major) |
| pgvector extension | All vector ops | ✗ (not yet) | — | **Install via image swap** `pgvector/pgvector:pg16` + `CREATE EXTENSION` (D-05). No fallback — required. |
| OpenAI API (embeddings) | Indexing + query embed | ✓ | `OPENAI_API_KEY` already injected | — (same key as chat completions) |
| Docker / Docker Compose | Deploy | ✓ | — | — |
| Outbound network from api container | `tiktoken` BPE fetch on first use; OpenAI calls | ✓ (api makes OpenAI/JWKS calls today) | — | Pre-bundle tiktoken cache if air-gapped (not the case here) |
| pip (rebuild api/listener images) | New deps | ✓ | — | — |

**Missing dependencies with no fallback:**
- **pgvector extension** — not installed until the image swap + migration. This is the gating action; everything else depends on it. It is in scope and trivially provided by D-05's image swap, so not a blocker — just a sequencing requirement (the migration `CREATE EXTENSION` must run on the new image, and the test image must match).

**Missing dependencies with fallback:**
- `tiktoken` cold-start vocab fetch — fallback is bundling the cache, but the api container has outbound network so no action needed.

## Validation Architecture

> `workflow.nyquist_validation` is `true` in `.planning/config.json` — this section is REQUIRED.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.23+ (`asyncio_mode="auto"`, session-scoped loop) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_knowledge_bases.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

**MANDATORY:** tests run ONLY through the test-overlay (CLAUDE.md guard + conftest hard guard at `tests/conftest.py:_assert_test_dsn`). Never `docker compose run --rm api pytest` without the overlay. **The `db-test` image in `docker-compose.test.yml` must become `pgvector/pgvector:pg16`** so the test DB has the extension (Pitfall 1 / Open Q 3).

### Phase Requirements → Test Map
| Req | Behavior | Test Type | Automated Command | File Exists? |
|-----|----------|-----------|-------------------|--------------|
| KB-01 | Create KB scoped to workspace; another workspace can't see it | integration | `pytest tests/test_knowledge_bases.py::test_create_kb_workspace_isolated -x` | ❌ Wave 0 |
| KB-02 | Upload file / paste text → kb_documents row, status `pending`, size_bytes set | integration | `pytest tests/test_kb_ingest.py::test_upload_creates_pending_doc -x` | ❌ Wave 0 |
| KB-03 | Worker: pending → processing → indexed; chunk_count > 0; failed doc carries error | integration | `pytest tests/test_kb_ingest_worker.py::test_tick_indexes_pending_doc -x` | ❌ Wave 0 |
| KB-03 | Aggregate counters (DOCUMENTS/INDEXED/PROCESSING/FAILED/STORAGE) computed correctly | unit/integration | `pytest tests/test_knowledge_bases.py::test_kb_detail_aggregate -x` | ❌ Wave 0 |
| KB-04 | Attach/detach agent↔KB (M:N); reverse list (Agents tab) | integration | `pytest tests/test_knowledge_bases.py::test_attach_detach_agent -x` | ❌ Wave 0 |
| KB-05 | `search_knowledge_base` returns nearest chunks ordered by cosine distance; respects top-K + threshold | unit | `pytest tests/test_kb_search.py::test_cosine_search_orders_by_distance -x` | ❌ Wave 0 |
| KB-05 | Tool exposed ONLY when agent has ≥1 KB (D-04); not exposed otherwise | unit | `pytest tests/test_ai_engine_kb_tool.py::test_tool_gated_on_attached_kb -x` | ❌ Wave 0 |
| KB-05 | Data-tool branch: KB hits appended as role:"tool" message, model continues (NOT terminating) | unit (mock OpenAI) | `pytest tests/test_ai_engine_kb_tool.py::test_search_kb_continues_conversation -x` | ❌ Wave 0 |
| KB-06 | Search never returns chunks from another workspace's KB | integration | `pytest tests/test_kb_search.py::test_search_workspace_isolated -x` | ❌ Wave 0 |

### How each acceptance criterion is OBSERVED/MEASURED
- **"Retrieval actually influences a response"** — mock the OpenAI client (the codebase already wraps `client.chat.completions.create`; tests can monkeypatch `ai_engine.client`). Assert: (1) the request to OpenAI included the `search_knowledge_base` tool when a KB is attached; (2) when the model returns a `search_knowledge_base` tool_call, the dispatch appends a `role:"tool"` message whose content contains the seeded chunk text; (3) a second completion is requested (two-pass). Embeddings can be **stubbed deterministically** (e.g. monkeypatch `embed_texts` to return fixed vectors) so the cosine ordering is assertable without real API calls. This proves the data flows chunk→tool-result→model.
- **"Workspace isolation"** — seed two workspaces, each with a KB + chunks; call `kb_search` with workspace A's id and assert zero rows from workspace B (KB-06). Also assert KB list/detail endpoints 404/empty across workspaces (the existing `AuthDep` workspace-scoping pattern).
- **"Ingest pipeline end-to-end in the test-overlay"** — insert a `kb_documents` row with `status='pending'` and a small known blob (a tiny TXT/MD string is enough; PDF/DOCX parse paths can be unit-tested on fixture bytes separately). Run one `await kb_ingest_worker._tick()` (call the tick directly, like contact-check-worker tests do — a single `_tick` is a predictable single-doc op). Assert status flips to `indexed`, `chunk_count` matches the expected chunking, and `kb_chunks` rows exist with `embedding` non-null. Use a **stubbed embedder** to avoid network.
- **Cosine search correctness** — insert `kb_chunks` with hand-crafted unit vectors where the expected ordering is known; assert `cosine_distance` order. This validates the pgvector query + index path without OpenAI.
- **Re-index idempotency (Pitfall 8)** — index a doc, re-index it, assert `chunk_count` is stable (not doubled) and no duplicate chunks.

### Sampling Rate
- **Per task commit:** `pytest tests/test_knowledge_bases.py tests/test_kb_*.py -x` (the KB-scoped files)
- **Per wave merge:** full suite (`... run --rm api pytest`) — must stay GREEN (baseline is GREEN per MEMORY `project-test-baseline-red.md`)
- **Phase gate:** full suite green + a manual live smoke (upload a real PDF in the deployed app, confirm Documents tab transitions processing→indexed, run a Search-tab query, confirm an agent answers from KB content) before `/gsd:verify-work`.

### Wave 0 Gaps
- [ ] `docker-compose.test.yml` — bump `db-test` image to `pgvector/pgvector:pg16`
- [ ] `tests/conftest.py::_build_outreach_schema` — add `CREATE EXTENSION IF NOT EXISTS vector` before `create_all` (Pitfall 1)
- [ ] `tests/conftest.py` — shared fixtures: a seeded KB + indexed chunks; a deterministic embedder stub (monkeypatch `embed_texts`); an OpenAI-client stub returning a `search_knowledge_base` tool_call
- [ ] `tests/test_knowledge_bases.py` — CRUD + isolation + aggregate + attach/detach (KB-01/04/06, KB-03 aggregate)
- [ ] `tests/test_kb_ingest.py` + `tests/test_kb_ingest_worker.py` — upload/paste + worker tick + re-index idempotency (KB-02/03)
- [ ] `tests/test_kb_search.py` — cosine ordering + threshold + workspace isolation (KB-05/06)
- [ ] `tests/test_ai_engine_kb_tool.py` — tool gating + data-tool continue-not-terminate (KB-05)
- [ ] pip deps install: add 4 packages to `requirements.txt`, rebuild api+listener

## Sources

### Primary (HIGH confidence)
- Existing codebase (file:line grounding): `app/services/ai_engine.py` (tool loop 1255-1383, two-pass 1342-1383, BUILT_IN_TOOL_NAMES:45, build_builtin_tools:85, build_system_prompt:731, `[БАЗА ЗНАНИЙ]` slot:901, AsyncOpenAI:39, out_of_scope:593); `app/models/__init__.py` (AIContext:203, knowledge_base:222, CampaignSender M:N:645, workspace_id pattern throughout); `app/routers/contacts.py` (UploadFile/File pattern 301-348, 202-accepted import:371); `app/main.py` (lifespan workers 46-75); `app/services/contact_check_worker.py` (worker class 135-209); `app/database.py` (`_apply_migrations`, init_db); `docker-compose.yml` (db image+command); `migrations/038_warmup_settings.sql`, `migrations/030_sender_restriction_events.sql` (idempotent raw-SQL style); `app/config.py` (env-knob pattern, openai_model:54); `tests/conftest.py` (`_build_outreach_schema`, test-DSN guard); `requirements.txt`.
- pgvector-python README (github.com/pgvector/pgvector-python) — `Vector(1536)` column, `cosine_distance()` / `<=>`, async usage.
- pgvector README (github.com/pgvector/pgvector) — HNSW vs IVFFlat (HNSW no training, builds on empty table), `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)` defaults `m=16, ef_construction=64`, IVFFlat `lists` guidance.
- `pip index versions` (2026-06-30): pgvector 0.4.2, pypdf 6.14.2, python-docx 1.2.0, tiktoken 0.13.0.
- `.planning/config.json` (`nyquist_validation: true`); CONTEXT.md D-01..D-12; UI-SPEC.md.

### Secondary (MEDIUM confidence)
- OpenAI API reference / model page (developers.openai.com, platform.openai.com) — `text-embedding-3-small`: 8191 max input tokens, 1536 dims (reducible via `dimensions`), per-request limits 2048 array items / 300k tokens, ~$0.02/M tokens. Cross-checked across multiple sources.

### Tertiary (LOW confidence — tune in flight)
- Chunk size 800 / overlap 120, top-K=5, cosine-distance threshold 0.55 — sensible defaults, not authoritative numbers. Validate against the Search tab during QA; expose as env knobs if tunability is wanted.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions verified against PyPI 2026-06-30; pgvector/OpenAI patterns from official docs; all integration points grounded in existing code.
- Architecture: HIGH — every pattern (worker, upload, M:N, migration, OpenAI client) already exists in the repo; the one new piece (data-tool branch) maps onto the existing two-pass custom-tool flow.
- Pitfalls: HIGH on the structural ones (test-overlay extension, image command block, blocking parsers, re-index idempotency — all derived from concrete project facts); MEDIUM on threshold/limit details.

**Research date:** 2026-06-30
**Valid until:** ~2026-07-30 (stack is stable; re-verify pip versions and OpenAI limits if the plan slips a month).
