---
phase: 16-rag-knowledge-bases-for-agents
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - requirements.txt
  - docker-compose.yml
  - docker-compose.test.yml
  - tests/conftest.py
  - migrations/041_knowledge_bases.sql
  - app/models/__init__.py
  - tests/test_knowledge_bases.py
  - tests/test_kb_ingest.py
  - tests/test_kb_ingest_worker.py
  - tests/test_kb_search.py
  - tests/test_ai_engine_kb_tool.py
autonomous: true
requirements: [KB-01, KB-02, KB-03, KB-04, KB-05, KB-06]
must_haves:
  truths:
    - "The test suite can build its schema with a Vector(1536) column (pgvector extension present in test DB)"
    - "Prod db runs the pgvector image while keeping log_statement=ddl logging intact"
    - "Migration 041 creates knowledge_bases / kb_documents / kb_chunks / agent_knowledge_bases with the vector extension + HNSW index, idempotently"
    - "ORM mirrors the four new tables (incl. Vector(1536)) so create_all builds the same test schema"
    - "All KB test files exist and are RED (import-inside-body), full suite still collects with 0 errors"
  artifacts:
    - path: "migrations/041_knowledge_bases.sql"
      provides: "CREATE EXTENSION vector + 4 tables + HNSW + btree indexes (idempotent)"
      contains: "CREATE EXTENSION IF NOT EXISTS vector"
    - path: "app/models/__init__.py"
      provides: "KnowledgeBase, KbDocument, KbChunk, AgentKnowledgeBase ORM classes"
      contains: "class KbChunk"
    - path: "requirements.txt"
      provides: "pgvector / tiktoken / pypdf / python-docx pins"
      contains: "pgvector==0.4.2"
    - path: "tests/conftest.py"
      provides: "CREATE EXTENSION vector before create_all + migration 041 in applied list"
      contains: "CREATE EXTENSION IF NOT EXISTS vector"
  key_links:
    - from: "tests/conftest.py::_build_outreach_schema"
      to: "Base.metadata.create_all"
      via: "CREATE EXTENSION vector executed BEFORE create_all"
      pattern: "CREATE EXTENSION IF NOT EXISTS vector"
    - from: "app/models/__init__.py KbChunk.embedding"
      to: "pgvector Vector(1536) type"
      via: "from pgvector.sqlalchemy import Vector"
      pattern: "Vector\\(1536\\)"
---

<objective>
Wave 0 for Phase 16 (RAG Knowledge Bases). Stand up everything the rest of the
phase builds on: the pip deps, the pgvector Docker images (prod + test), the
`vector` extension in the test-overlay schema build, migration 041 with the four
new tables + indexes, the ORM mirror of those tables (so `create_all` builds the
same test schema), and a RED test scaffold mapping every KB-01..KB-06 behavior.

Purpose: pgvector is the gating dependency — nothing else in the phase can be
written or tested until the extension is installed and the test DB has it. The
ORM mirror is mandatory because the test-overlay builds schema from
`Base.metadata.create_all`, not migrations, so a `Vector(1536)` column that is
missing from the ORM would make the test schema diverge from prod.

Output: deps pinned, both db images swapped to `pgvector/pgvector:pg16` (command
block preserved), conftest extension + migration wiring, `migrations/041_knowledge_bases.sql`,
four ORM classes, and 5 RED test files.
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
@.planning/phases/16-rag-knowledge-bases-for-agents/16-VALIDATION.md

<interfaces>
<!-- Reference implementations the executor MUST mirror. -->

Idempotent raw-SQL migration style (mirror exactly): migrations/038_warmup_settings.sql,
migrations/030_sender_restriction_events.sql — BEGIN; ... COMMIT; with
CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / DO $$ EXCEPTION duplicate_object $$.

M:N through-table ORM (mirror exactly), app/models/__init__.py:645 CampaignSender:
```python
class CampaignSender(Base):
    __tablename__ = "campaign_senders"
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True)
    sender_id   = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    added_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

AIContext FK target (agent table is ai_contexts), app/models/__init__.py:203:
```python
class AIContext(Base):
    __tablename__ = "ai_contexts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    knowledge_base = Column(Text, nullable=True)   # D-08: static field STAYS, untouched
```

conftest schema-build order (tests/conftest.py:93-178): DROP SCHEMA → create_all (line 103)
→ stub cca → UUID-default ALTERs → apply migration list (028..031 hardcoded) → exists-guarded
032 / 038. NB the latest migration on disk is 040_warmup_settings_defaults_drift.sql, so the
new KB migration is 041 (NOT 040).

docker-compose db command block to PRESERVE verbatim (docker-compose.yml:8-13):
```yaml
    command:
      - "postgres"
      - "-c"
      - "log_statement=ddl"
      - "-c"
      - "log_min_duration_statement=1000"
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Deps + Docker image swaps (prod + test)</name>
  <read_first>
    - requirements.txt (current pins; append a new Phase 16 block)
    - docker-compose.yml (db service: image:3, command:8-13)
    - docker-compose.test.yml (db-test service: image:17, command:20-27)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md (§Standard Stack, §Runtime State Inventory, Pitfall 2)
  </read_first>
  <files>requirements.txt, docker-compose.yml, docker-compose.test.yml</files>
  <action>
    1. Append to `requirements.txt` (after the existing blocks):
       ```
       # Phase 16 — RAG knowledge bases
       pgvector==0.4.2
       tiktoken==0.13.0
       pypdf==6.14.2
       python-docx==1.2.0
       ```
    2. In `docker-compose.yml`, change the `db` service `image: postgres:16` →
       `image: pgvector/pgvector:pg16`. DO NOT touch the `command:` block — it MUST
       stay exactly:
       ```yaml
       command:
         - "postgres"
         - "-c"
         - "log_statement=ddl"
         - "-c"
         - "log_min_duration_statement=1000"
       ```
       (Pitfall 2 — the new image does NOT inherit per-service `command:`; dropping
       it silently disables the post-incident DDL logging. Same PG16 major ⇒ the
       existing `postgres_data` volume mounts unchanged, no re-init, no data loss.
       NEVER `docker compose down -v` — it wipes the prod volume.)
    3. In `docker-compose.test.yml`, change the `db-test` service `image: postgres:16`
       → `image: pgvector/pgvector:pg16`. Keep its existing `command:` block
       (`fsync=off`, `synchronous_commit=off`, `log_statement=ddl`) verbatim.
    DO NOT run any docker commands in this task — image rebuild/recreate is an OPS
    step the user runs after merge. This task only edits the three files.
  </action>
  <verify>
    <automated>grep -q "pgvector==0.4.2" requirements.txt && grep -q "tiktoken==0.13.0" requirements.txt && grep -q "pypdf==6.14.2" requirements.txt && grep -q "python-docx==1.2.0" requirements.txt && grep -q "pgvector/pgvector:pg16" docker-compose.yml && grep -q "pgvector/pgvector:pg16" docker-compose.test.yml && grep -q "log_statement=ddl" docker-compose.yml && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `requirements.txt` contains all four: `pgvector==0.4.2`, `tiktoken==0.13.0`, `pypdf==6.14.2`, `python-docx==1.2.0`
    - `docker-compose.yml` db service `image:` is `pgvector/pgvector:pg16`
    - `docker-compose.yml` still contains both `log_statement=ddl` and `log_min_duration_statement=1000` under the db `command:`
    - `docker-compose.test.yml` db-test `image:` is `pgvector/pgvector:pg16`
    - No `down -v` / docker command appears in the diff
  </acceptance_criteria>
  <done>Four deps pinned; both db images on pgvector/pgvector:pg16; prod DDL-logging command block intact.</done>
</task>

<task type="auto">
  <name>Task 2: Migration 041 + ORM models + conftest extension wiring</name>
  <read_first>
    - migrations/038_warmup_settings.sql AND migrations/030_sender_restriction_events.sql (idempotent raw-SQL style to mirror)
    - app/models/__init__.py (lines 200-239 AIContext incl. knowledge_base:222; lines 645-662 CampaignSender M:N pattern; top-of-file imports for Column/UUID/ForeignKey/func)
    - tests/conftest.py (lines 90-178 `_build_outreach_schema`: create_all at 103, migration list ends at 031 hardcoded + exists-guarded 032/038)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md (§Code Examples Example 4 migration skeleton, §Index Choice, Pitfall 1, Open Q 1/2)
  </read_first>
  <files>migrations/041_knowledge_bases.sql, app/models/__init__.py, tests/conftest.py</files>
  <action>
    1. Create `migrations/041_knowledge_bases.sql` — idempotent, wrapped in
       `BEGIN; ... COMMIT;`, using the EXACT DDL below (copied from RESEARCH Example 4,
       table name unchanged, FK to `ai_contexts` for the agent side):
       ```sql
       -- migrations/041_knowledge_bases.sql — Phase 16 RAG KBs (D-05/D-06/D-07).
       -- Idempotent: CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS — auto-applier re-runs safely.
       -- Fail-fast: api does NOT start if this raises. Auto-applied via _apply_migrations.
       BEGIN;

       CREATE EXTENSION IF NOT EXISTS vector;

       CREATE TABLE IF NOT EXISTS knowledge_bases (
           id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
           workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
           name          VARCHAR(150) NOT NULL,
           description   TEXT,
           source_kind   VARCHAR(20) NOT NULL DEFAULT 'files',
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
           size_bytes    BIGINT NOT NULL DEFAULT 0,
           status        VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|processing|indexed|failed
           error         TEXT,
           chunk_count   INTEGER NOT NULL DEFAULT 0,
           raw_content   BYTEA,
           created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
           updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
       );
       CREATE INDEX IF NOT EXISTS idx_kbdoc_kb_status ON kb_documents (kb_id, status);

       CREATE TABLE IF NOT EXISTS kb_chunks (
           id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
           workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
           kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
           document_id   UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
           chunk_index   INTEGER NOT NULL,
           content       TEXT NOT NULL,
           embedding     VECTOR(1536) NOT NULL,       -- D-06 text-embedding-3-small
           created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
       );
       CREATE INDEX IF NOT EXISTS idx_kbchunk_embedding_hnsw
           ON kb_chunks USING hnsw (embedding vector_cosine_ops);
       CREATE INDEX IF NOT EXISTS idx_kbchunk_ws_kb ON kb_chunks (workspace_id, kb_id);

       CREATE TABLE IF NOT EXISTS agent_knowledge_bases (
           agent_id      UUID NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
           kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
           workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
           added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
           PRIMARY KEY (agent_id, kb_id)
       );
       CREATE INDEX IF NOT EXISTS idx_akb_kb ON agent_knowledge_bases (kb_id);

       COMMIT;
       ```
       (HNSW chosen because it builds on an empty table — the index is created in the
       migration before any chunk exists; IVFFlat needs training rows and cannot.
       Open Q 1: pasted text stored in `raw_content` BYTEA as utf-8, discriminated by
       `source_kind='text'`. Open Q 2: STORAGE = SUM(kb_documents.size_bytes).)

    2. In `app/models/__init__.py`, add the four ORM classes (place them after the
       Campaign-related models, e.g. after CampaignContactAssignment). Import the
       Vector type at the top of the file alongside the existing SQLAlchemy imports:
       `from pgvector.sqlalchemy import Vector`. Mirror the existing column idioms
       (UUID PK with `default=uuid.uuid4`, `server_default=func.now()`, FK ondelete):
       ```python
       class KnowledgeBase(Base):
           __tablename__ = "knowledge_bases"
           id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
           workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
           name = Column(String(150), nullable=False)
           description = Column(Text, nullable=True)
           source_kind = Column(String(20), nullable=False, server_default="files")
           created_at = Column(DateTime(timezone=True), server_default=func.now())
           updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

       class KbDocument(Base):
           __tablename__ = "kb_documents"
           id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
           workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
           kb_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
           name = Column(String(255), nullable=False)
           source_kind = Column(String(20), nullable=False)
           size_bytes = Column(BigInteger, nullable=False, server_default="0")
           status = Column(String(20), nullable=False, server_default="pending")
           error = Column(Text, nullable=True)
           chunk_count = Column(Integer, nullable=False, server_default="0")
           raw_content = Column(LargeBinary, nullable=True)
           created_at = Column(DateTime(timezone=True), server_default=func.now())
           updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

       class KbChunk(Base):
           __tablename__ = "kb_chunks"
           id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
           workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
           kb_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
           document_id = Column(UUID(as_uuid=True), ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False)
           chunk_index = Column(Integer, nullable=False)
           content = Column(Text, nullable=False)
           embedding = Column(Vector(1536), nullable=False)
           created_at = Column(DateTime(timezone=True), server_default=func.now())

       class AgentKnowledgeBase(Base):
           __tablename__ = "agent_knowledge_bases"
           agent_id = Column(UUID(as_uuid=True), ForeignKey("ai_contexts.id", ondelete="CASCADE"), primary_key=True)
           kb_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)
           workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
           added_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
       ```
       Check the existing imports — `BigInteger`, `LargeBinary` may need adding to the
       `from sqlalchemy import (...)` line. DO NOT modify the existing AIContext class
       or its `knowledge_base` column (D-08 — the static field stays untouched).

    3. In `tests/conftest.py::_build_outreach_schema`:
       (a) Add `CREATE EXTENSION IF NOT EXISTS vector` BEFORE `create_all`. The
           cleanest spot is right after the `DROP SCHEMA public CASCADE; CREATE SCHEMA
           public;` block (around line 96) — reuse the same `asyncpg.connect(dsn=raw_dsn)`
           connection or add the statement to that execute call:
           `await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE EXTENSION IF NOT EXISTS vector;")`
           (Pitfall 1 — `create_all` at line 103 emits `embedding VECTOR(1536)`; the
           extension MUST exist at that point or the whole suite fails to set up with
           `type "vector" does not exist`.)
       (b) Add `kb_documents` and `kb_chunks` and `knowledge_bases` to the UUID-default
           ALTER loop (the tuple at lines 123-130) so raw-SQL test INSERTs get
           `gen_random_uuid()` defaults. (agent_knowledge_bases has a composite PK / no
           single `id` — the loop's try/except already swallows that.)
       (c) Add an exists-guarded apply of migration 041 (mirror the 032/038 guard at
           lines 180-191) so the test DB also runs the migration's DDL (the HNSW index
           in particular comes only from the migration, not create_all):
           ```python
           _mig_041 = PROJECT_ROOT / "migrations" / "041_knowledge_bases.sql"
           if _mig_041.exists():
               await asyncpg_conn.execute(_mig_041.read_text())
           ```
  </action>
  <verify>
    <automated>grep -q "CREATE EXTENSION IF NOT EXISTS vector" migrations/041_knowledge_bases.sql && grep -q "USING hnsw (embedding vector_cosine_ops)" migrations/041_knowledge_bases.sql && grep -q "class KbChunk" app/models/__init__.py && grep -q "Vector(1536)" app/models/__init__.py && grep -q "CREATE EXTENSION IF NOT EXISTS vector" tests/conftest.py && grep -q "041_knowledge_bases.sql" tests/conftest.py && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `migrations/041_knowledge_bases.sql` exists, wrapped in BEGIN/COMMIT, contains `CREATE EXTENSION IF NOT EXISTS vector`, all four `CREATE TABLE IF NOT EXISTS`, the HNSW index line `USING hnsw (embedding vector_cosine_ops)`, and `agent_knowledge_bases` FK to `ai_contexts(id)`
    - `app/models/__init__.py` defines `class KnowledgeBase`, `class KbDocument`, `class KbChunk`, `class AgentKnowledgeBase`; `KbChunk.embedding` is `Vector(1536)`; `from pgvector.sqlalchemy import Vector` is imported
    - The existing `AIContext.knowledge_base = Column(Text...)` line is unchanged (D-08)
    - `tests/conftest.py` runs `CREATE EXTENSION IF NOT EXISTS vector` before `create_all` and includes `041_knowledge_bases.sql` in the applied migrations
    - `grep -c "041_knowledge_bases" tests/conftest.py` ≥ 1
  </acceptance_criteria>
  <done>Migration 041, four ORM models (incl. Vector(1536)), and conftest extension+migration wiring all present; static knowledge_base field untouched.</done>
</task>

<task type="auto">
  <name>Task 3: RED test scaffold for KB-01..KB-06</name>
  <read_first>
    - tests/conftest.py (fixtures available: db session, workspace setup; how existing tests seed a workspace + agent — grep for `workspace` fixture usage)
    - tests/test_pool_endpoints.py OR tests/test_restriction_audit.py (existing Wave-0 RED scaffold style: import-inside-body, fully-asserting stubs that fail RED, per-test JWT/workspace seeding)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-VALIDATION.md (§Per-Task Verification Map — the exact test names to create)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md (§Validation Architecture — how each criterion is observed; deterministic embedder stub guidance)
  </read_first>
  <files>tests/test_knowledge_bases.py, tests/test_kb_ingest.py, tests/test_kb_ingest_worker.py, tests/test_kb_search.py, tests/test_ai_engine_kb_tool.py</files>
  <action>
    Create 5 RED test files. Each test imports the not-yet-existing production module
    INSIDE the test body (deferred import) so `pytest --collect-only` stays clean and the
    full suite keeps collecting with 0 errors (mirror the existing Wave-0 scaffold
    convention in tests/test_pool_endpoints.py). Each test must be GENUINELY RED — assert
    real expected behavior, not `pass`. Use the VALIDATION.md test names verbatim:

    `tests/test_knowledge_bases.py`:
      - `test_create_kb_workspace_isolated` (KB-01) — create a KB in workspace A; assert a
        request scoped to workspace B does not see it (404/empty).
      - `test_kb_detail_aggregate` (KB-03) — seed docs in mixed statuses; assert the KB
        detail aggregate returns DOCUMENTS/INDEXED/PROCESSING/FAILED counts + STORAGE =
        SUM(size_bytes).
      - `test_attach_detach_agent` (KB-04) — attach a KB to an agent, assert the M:N row +
        reverse list (agents-for-kb); detach, assert removed.
    `tests/test_kb_ingest.py`:
      - `test_upload_creates_pending_doc` (KB-02) — POST a small file/text; assert a
        `kb_documents` row exists with `status='pending'` and `size_bytes` set; 202.
    `tests/test_kb_ingest_worker.py`:
      - `test_tick_indexes_pending_doc` (KB-03) — insert a pending doc with a known small
        TXT blob, stub the embedder deterministically, run one `await kb_ingest_worker._tick()`;
        assert status→`indexed`, `chunk_count>0`, `kb_chunks` rows with non-null embedding.
      - `test_reindex_is_idempotent` (KB-03 / Pitfall 8) — index a doc, re-index it; assert
        `chunk_count` stable (not doubled), no duplicate chunks.
    `tests/test_kb_search.py`:
      - `test_cosine_search_orders_by_distance` (KB-05) — insert kb_chunks with hand-crafted
        unit vectors of known ordering; assert `search_knowledge_base`/`kb_search` returns
        them ordered by cosine distance, respects top-K + threshold.
      - `test_search_workspace_isolated` (KB-06) — seed chunks in two workspaces; search with
        workspace A id; assert zero rows from workspace B.
    `tests/test_ai_engine_kb_tool.py`:
      - `test_tool_gated_on_attached_kb` (KB-05/D-04) — assert the `search_knowledge_base`
        tool spec is included in the OpenAI request ONLY when the agent has ≥1 attached KB,
        and absent otherwise.
      - `test_search_kb_continues_conversation` (KB-05) — with a mocked OpenAI client that
        returns a `search_knowledge_base` tool_call, assert the dispatch appends a
        `role:"tool"` message containing the seeded chunk text and requests a SECOND
        completion (two-pass, NOT terminating, does NOT change conversation.status).

    For deterministic vector tests, add a shared helper in conftest (or in
    tests/test_kb_search.py) to monkeypatch the embedder to return fixed vectors — note
    this in the file so Wave 2/3 wires the real embedder name. It is acceptable for these
    to fail with ImportError/AttributeError until the production modules land (that IS the
    RED state); ensure the failure is a real failed assertion or import, not a skip.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_knowledge_bases.py tests/test_kb_ingest.py tests/test_kb_ingest_worker.py tests/test_kb_search.py tests/test_ai_engine_kb_tool.py --collect-only -q 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - All 5 files exist and define exactly the test functions named above (grep each name)
    - `pytest --collect-only` over the 5 files exits 0 (collection succeeds — deferred imports keep it clean)
    - Running the 5 files (without `--collect-only`) shows them RED (failures/errors), NOT passing and NOT skipped
    - Full suite `... run --rm api pytest --collect-only` reports 0 collection errors
  </acceptance_criteria>
  <done>5 KB test files exist, collect cleanly, and are RED — every KB-01..KB-06 behavior has a failing test waiting for implementation.</done>
</task>

</tasks>

<verification>
- `grep` checks in each task pass.
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only` → 0 errors (the new RED files collect via deferred imports; this also proves the test image build picked up pgvector + the extension is created in `_build_outreach_schema`).
- Do NOT run the prod `docker compose up -d --build` here — image recreate is a user-gated OPS step (Pitfall 2 / never down -v).
</verification>

<success_criteria>
- Four pip deps pinned; both db images on `pgvector/pgvector:pg16`; prod `command:` DDL-logging block intact.
- `migrations/041_knowledge_bases.sql` idempotent with vector extension + 4 tables + HNSW + btree.
- ORM mirrors all four tables incl. `Vector(1536)`; `AIContext.knowledge_base` untouched (D-08).
- conftest creates the `vector` extension before `create_all` and applies 041.
- 5 RED test files exist, collect cleanly, fail as expected.
</success_criteria>

<output>
After completion, create `.planning/phases/16-rag-knowledge-bases-for-agents/16-01-SUMMARY.md`
</output>
