---
phase: 16-rag-knowledge-bases-for-agents
plan: 02
type: execute
wave: 2
depends_on: ["16-01"]
files_modified:
  - app/config.py
  - app/services/kb_ingest.py
  - app/services/kb_ingest_worker.py
  - app/main.py
  - tests/test_kb_ingest_worker.py
autonomous: true
requirements: [KB-02, KB-03]
must_haves:
  truths:
    - "A pending kb_document is picked up by the worker, parsed, chunked, embedded, and flipped to indexed with chunk_count > 0"
    - "PDF/DOCX/TXT/MD/CSV/pasted-text all extract to text without blocking the event loop"
    - "A failed parse/embed flips the doc to status='failed' with an error message and never crashes the worker"
    - "Re-indexing a document deletes its existing chunks first so chunk_count does not double"
    - "Embeddings come from text-embedding-3-small (1536 dims) via the existing AsyncOpenAI client, model name from an env knob"
  artifacts:
    - path: "app/services/kb_ingest.py"
      provides: "extract_text(), chunk_text(), embed_texts() pipeline pieces"
      contains: "def chunk_text"
    - path: "app/services/kb_ingest_worker.py"
      provides: "KnowledgeIngestWorker singleton with start/stop/_tick"
      contains: "class KnowledgeIngestWorker"
    - path: "app/config.py"
      provides: "openai_embedding_model + kb knobs"
      contains: "openai_embedding_model"
  key_links:
    - from: "app/main.py lifespan"
      to: "kb_ingest_worker.start() / .stop()"
      via: "registered next to contact_check_worker"
      pattern: "kb_ingest_worker"
    - from: "app/services/kb_ingest_worker.py _tick"
      to: "kb_documents status flip pending→processing→indexed/failed"
      via: "claim + update inside the worker"
      pattern: "status.*indexed|status.*failed"
    - from: "app/services/kb_ingest.py embed_texts"
      to: "AsyncOpenAI embeddings.create(model=text-embedding-3-small)"
      via: "settings.openai_embedding_model"
      pattern: "embeddings\\.create"
---

<objective>
Build the ingest pipeline (text extraction, token-based chunking, embedding) and
the background `KnowledgeIngestWorker` that drives documents pending → processing →
indexed/failed. This is the piece that turns an uploaded blob into searchable
`kb_chunks` with vectors.

Purpose: KB-02 (upload/paste) and KB-03 (per-doc + aggregate indexing status) both
require a worker that runs OUTSIDE the HTTP request — the upload endpoint (Wave 3,
plan 16-03) just records a `pending` doc; this worker does the CPU-bound parse +
the OpenAI embed call and flips status so the UI can poll.

Output: `app/services/kb_ingest.py` (pure pipeline functions), `app/services/kb_ingest_worker.py`
(lifespan-managed singleton mirroring ContactCheckWorker), config knobs, lifespan
registration, and the worker tests turn GREEN.
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
@.planning/phases/16-rag-knowledge-bases-for-agents/16-01-SUMMARY.md

<interfaces>
<!-- Reference implementations to mirror. -->

Worker class shape (mirror exactly), app/services/contact_check_worker.py:135-214:
```python
class ContactCheckWorker:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.poll_interval = CONTACT_CHECK_POLL_INTERVAL
    def start(self):           # idempotent: if self._task is None or done → create_task(self._run(), name="...")
    async def stop(self):      # self._running=False; cancel + await; swallow CancelledError
    async def _run(self):      # while self._running: try: await self._tick() except CancelledError: break except Exception: log; await asyncio.sleep(poll)
    async def _tick(self) -> int:  # claim → process → update; single predictable op for tests
contact_check_worker = ContactCheckWorker()   # module-scope singleton
```

Lifespan registration points, app/main.py:17 (import), 60-61 (start), 70 (stop):
```python
from app.services.contact_check_worker import contact_check_worker
...
contact_check_worker.start()
logger.info("Contact check worker started")
...
await contact_check_worker.stop()
```

Existing AsyncOpenAI client (reuse, do NOT create a second), app/services/ai_engine.py:39:
```python
client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
```

Config env-knob pattern, app/config.py:54:
```python
openai_model: str = Field(default="gpt-5-mini-2025-08-07", validation_alias="OPENAI_MODEL", description="...")
```

ORM models (from 16-01): KbDocument(status, source_kind, size_bytes, error, chunk_count, raw_content),
KbChunk(kb_id, document_id, chunk_index, content, embedding Vector(1536), workspace_id).

DB session for the worker (mirror contact_check_worker): `from app.database import AsyncSessionLocal`.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: kb_ingest.py — extract_text / chunk_text / embed_texts + config knobs</name>
  <read_first>
    - app/config.py (lines 49-67, the Settings class + openai_model Field at 54 to mirror)
    - app/services/ai_engine.py (line 39 AsyncOpenAI client; lines 1385-1407 RateLimitError/APIError handling to mirror)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md (§Code Examples 1 & 2, Pitfalls 3/6/7, §Standard Stack supporting libs)
    - tests/test_kb_ingest.py (the RED test contracts from 16-01)
  </read_first>
  <files>app/config.py, app/services/kb_ingest.py, tests/test_kb_ingest_worker.py</files>
  <behavior>
    - chunk_text("...long...", max_tokens=800, overlap=120) → list of chunks, each ≤800 tokens, sliding window with 120-token overlap; short text → single chunk.
    - extract_text(blob, source_kind) → text for each of 'pdf'|'docx'|'txt'|'md'|'csv'|'text'; unknown kind → ValueError.
    - embed_texts([...], model) → list of 1536-float vectors, order-preserving; batches under the 2048-item / 300k-token ceiling.
    - An empty/whitespace document → zero chunks (caller marks indexed with chunk_count=0, NOT failed).
  </behavior>
  <action>
    1. In `app/config.py::Settings`, add two knobs mirroring the `openai_model` Field idiom:
       ```python
       openai_embedding_model: str = Field(
           default="text-embedding-3-small",
           validation_alias="OPENAI_EMBEDDING_MODEL",
           description="OpenAI embedding model for KB ingest/search. 1536 dims. Override via env without redeploy.",
       )
       kb_ingest_poll_interval: int = Field(default=5, validation_alias="KB_INGEST_POLL_INTERVAL")
       kb_search_max_distance: float = Field(default=0.55, validation_alias="KB_SEARCH_MAX_DISTANCE")
       kb_search_top_k: int = Field(default=5, validation_alias="KB_SEARCH_TOP_K")
       kb_chunk_max_tokens: int = Field(default=800, validation_alias="KB_CHUNK_MAX_TOKENS")
       kb_chunk_overlap: int = Field(default=120, validation_alias="KB_CHUNK_OVERLAP")
       ```
    2. Create `app/services/kb_ingest.py` with three pure functions + the parsers:
       - `_enc = tiktoken.get_encoding("cl100k_base")` at module scope (encoding for the
         text-embedding-3 family). Optionally lazy-init to avoid a cold-start fetch at import.
       - `def chunk_text(text: str, max_tokens: int = 800, overlap: int = 120) -> list[str]`
         — EXACTLY the RESEARCH Example 2 sliding-window implementation:
         ```python
         toks = _enc.encode(text)
         chunks, start = [], 0
         while start < len(toks):
             end = min(start + max_tokens, len(toks))
             chunks.append(_enc.decode(toks[start:end]))
             if end == len(toks):
                 break
             start = end - overlap
         return chunks
         ```
         Return `[]` for empty/whitespace-only input.
       - `def _extract_pdf(blob: bytes) -> str` using `pypdf.PdfReader(io.BytesIO(blob))`,
         join `page.extract_text()` across pages.
       - `def _extract_docx(blob: bytes) -> str` using `docx.Document(io.BytesIO(blob))`,
         join `p.text for p in document.paragraphs`.
       - `def _extract_plaintext(blob: bytes) -> str` decode utf-8 with latin-1 fallback
         (mirror the contacts CSV encoding sniff). Used for txt/md/csv/text.
       - `def extract_text(blob: bytes, source_kind: str) -> str` dispatch on source_kind;
         raise `ValueError(f"unsupported source_kind: {source_kind}")` on unknown.
       - `async def extract_text_async(blob, source_kind) -> str` wrapping the CPU-bound
         extract in `await asyncio.to_thread(extract_text, blob, source_kind)` (Pitfall 3).
       - `async def embed_texts(texts: list[str], model: str) -> list[list[float]]` — reuse
         the existing client (`from app.services.ai_engine import client` OR a shared
         `AsyncOpenAI`); batch the input list into groups of ≤256 items (Pitfall 6) and call
         `await client.embeddings.create(model=model, input=batch)`; concatenate
         `[d.embedding for d in resp.data]` preserving order. Let RateLimitError/APIError
         propagate (the worker's _tick catches → marks doc failed).
    3. Add a deterministic-embedder monkeypatch hook the worker tests can use: e.g. the worker
       should call `kb_ingest.embed_texts` by its module reference so tests can monkeypatch
       `app.services.kb_ingest.embed_texts`. Note this contract in a docstring.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_kb_ingest.py -x -q 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `app/config.py` contains `openai_embedding_model` with default `text-embedding-3-small` and validation_alias `OPENAI_EMBEDDING_MODEL`
    - `app/services/kb_ingest.py` defines `chunk_text`, `extract_text`, `embed_texts` (grep each)
    - `chunk_text` uses tiktoken `cl100k_base` and the `start = end - overlap` sliding window
    - `extract_text` dispatches pdf/docx/txt/md/csv/text and raises `ValueError` on unknown kind
    - `embed_texts` calls `client.embeddings.create` with `model=` from the embedding-model setting and batches ≤256
    - CPU-bound parsing is wrapped via `asyncio.to_thread` (grep `to_thread`)
  </acceptance_criteria>
  <done>Pipeline functions exist with token-based chunking, multi-format extraction off the event loop, and batched embeddings via the embedding-model env knob.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: KnowledgeIngestWorker + lifespan registration</name>
  <read_first>
    - app/services/contact_check_worker.py (lines 135-260: __init__, start, stop, _run, _tick claim/update pattern)
    - app/main.py (lines 1-34 imports, 46-75 lifespan)
    - app/services/kb_ingest.py (the functions from Task 1)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md (§Architecture Pattern 3, Pitfall 8 re-index idempotency)
    - tests/test_kb_ingest_worker.py (RED contracts: test_tick_indexes_pending_doc, test_reindex_is_idempotent)
  </read_first>
  <files>app/services/kb_ingest_worker.py, app/main.py, tests/test_kb_ingest_worker.py</files>
  <behavior>
    - `await kb_ingest_worker._tick()` on one pending doc: status pending→processing→indexed, chunk_count = number of chunks, kb_chunks rows inserted with non-null embedding, kb_documents.updated_at bumped.
    - A doc whose extract/embed raises: status→failed, kb_documents.error set, worker does NOT crash, returns 0 (or moves on).
    - Re-index (a previously-indexed doc set back to pending, or an explicit re-index): DELETE existing kb_chunks for document_id FIRST, then re-insert → chunk_count stable, no duplicate chunks (Pitfall 8).
    - Empty document → status indexed, chunk_count=0, no chunks.
  </behavior>
  <action>
    1. Create `app/services/kb_ingest_worker.py` mirroring ContactCheckWorker:
       ```python
       class KnowledgeIngestWorker:
           def __init__(self):
               self._task = None
               self._running = False
               self.poll_interval = get_settings().kb_ingest_poll_interval
           def start(self):    # idempotent; create_task(self._run(), name="kb-ingest-worker")
           async def stop(self):   # _running=False; cancel + await; swallow CancelledError
           async def _run(self):   # while _running: try _tick except CancelledError break except Exception log; sleep(poll)
           async def _tick(self) -> int: ...
       kb_ingest_worker = KnowledgeIngestWorker()
       ```
       `_tick` logic (one doc per tick, predictable for tests), using `AsyncSessionLocal`:
       - Claim ONE `kb_documents` row `WHERE status='pending' ORDER BY created_at LIMIT 1
         FOR UPDATE SKIP LOCKED` (concurrency-safe like contact_check_worker), flip it to
         `processing`, commit (so the UI poll sees `processing`).
       - Load `raw_content` + `source_kind`. Wrap the whole parse+embed+store in try/except:
         - `text = await kb_ingest.extract_text_async(raw_content, source_kind)`
           (pasted text: `source_kind='text'`, raw_content is the utf-8 bytes).
         - `chunks = await asyncio.to_thread(kb_ingest.chunk_text, text, settings.kb_chunk_max_tokens, settings.kb_chunk_overlap)`
         - **Re-index idempotency (Pitfall 8):** `DELETE FROM kb_chunks WHERE document_id = :doc_id`
           BEFORE inserting new chunks — ALWAYS, so a re-run never doubles.
         - If `chunks` empty → set status `indexed`, chunk_count 0, commit, return.
         - `vectors = await kb_ingest.embed_texts(chunks, settings.openai_embedding_model)`
         - INSERT one kb_chunks row per (chunk_index, content, embedding) carrying the doc's
           workspace_id + kb_id; set `kb_documents.status='indexed'`, `chunk_count=len(chunks)`,
           `error=NULL`, `updated_at=now()`. Commit.
       - On exception: rollback, set `status='failed'`, `error=str(exc)[:1000]`, `updated_at=now()`,
         commit; log error; the worker loop continues (never dies).
       - Note: the worker calls `kb_ingest.embed_texts` by module reference so tests monkeypatch
         it deterministically.
    2. Register in `app/main.py` lifespan, next to contact_check_worker:
       - Import: `from app.services.kb_ingest_worker import kb_ingest_worker` (add to the
         existing import block ~line 17-18).
       - Startup (after `contact_check_worker.start()` ~line 60-61):
         `kb_ingest_worker.start()` + `logger.info("Knowledge ingest worker started")`.
       - Shutdown (next to `await contact_check_worker.stop()` ~line 70):
         `await kb_ingest_worker.stop()`.
    3. Flesh out the two worker tests to GREEN using a monkeypatched `embed_texts` returning
       fixed 1536-dim vectors (e.g. `[0.1]*1536`), a tiny TXT blob, and direct `_tick()` calls.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_kb_ingest_worker.py -x -q 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/kb_ingest_worker.py` defines `class KnowledgeIngestWorker` with `start`/`stop`/`_run`/`_tick` and a module-scope `kb_ingest_worker` singleton
    - `_tick` flips status pending→processing→indexed/failed and uses `FOR UPDATE SKIP LOCKED`
    - `_tick` runs `DELETE FROM kb_chunks WHERE document_id` before inserting (idempotent re-index)
    - `app/main.py` imports `kb_ingest_worker`, calls `.start()` in startup and `await .stop()` in shutdown (grep all three)
    - `pytest tests/test_kb_ingest_worker.py` exits 0 (both `test_tick_indexes_pending_doc` and `test_reindex_is_idempotent` GREEN)
  </acceptance_criteria>
  <done>Worker indexes pending docs end-to-end, marks failures, re-indexes idempotently, and is wired into the lifespan; worker tests GREEN.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_kb_ingest.py tests/test_kb_ingest_worker.py` GREEN.
- Full suite after this wave: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` must stay GREEN (baseline is GREEN).
- Lifespan registration is grep-verifiable; do NOT deploy here (OPS step is user-gated).
</verification>

<success_criteria>
- Pending docs are parsed, chunked (tiktoken ~800/120), embedded (text-embedding-3-small, batched), and flipped to indexed with chunk_count.
- All six source kinds extract; CPU work runs off the event loop.
- Failures mark the doc failed with an error and never kill the worker.
- Re-index is idempotent (delete-then-insert).
- Worker registered in lifespan; KB-02/KB-03 worker tests GREEN.
</success_criteria>

<output>
After completion, create `.planning/phases/16-rag-knowledge-bases-for-agents/16-02-SUMMARY.md`
</output>
