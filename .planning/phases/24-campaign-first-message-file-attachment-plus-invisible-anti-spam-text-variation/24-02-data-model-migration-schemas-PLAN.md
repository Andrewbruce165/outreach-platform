---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 02
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/054_campaign_attachment_and_variation.sql
  - app/models/__init__.py
  - app/schemas/__init__.py
  - tests/conftest.py
  - tests/test_campaign_attachment.py
autonomous: true
requirements: [D-01, D-02, D-04, D-13]
must_haves:
  truths:
    - "D-02/D-04: a 1-1 campaign_attachments BYTEA-blob table exists (modeled on CsvImport.file_data) so the 50 MB blob is NEVER dragged into a plain SELECT campaigns; blob rides pg_dump backups"
    - "D-01: exactly ONE attachment per campaign — campaign_attachments.campaign_id is UNIQUE with ON DELETE CASCADE"
    - "D-13: campaigns.variation_enabled boolean NOT NULL DEFAULT true — retro-enables ALL existing campaigns; exposed in Create/Update/Response schemas with API-level validation (no DB CHECK)"
    - "D-04 ORM-drift guard: every new NOT NULL column (variation_enabled, campaign_attachments.size_bytes) sets BOTH server_default AND an ORM value, id uses BOTH default=uuid.uuid4 AND server_default=text('gen_random_uuid()'); a raw-SQL INSERT omitting those columns does NOT NotNullViolation (create_all builds the same schema the migration does)"
    - "Migration 054 is idempotent (ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS / ALTER SET DEFAULT) and applies cleanly twice; fresh/test DB (create_all + conftest 054-guard) and prod (migration) converge to the identical schema"
  artifacts:
    - path: "migrations/054_campaign_attachment_and_variation.sql"
      provides: "campaigns.variation_enabled column + campaign_attachments table (idempotent)"
      contains: "ADD COLUMN IF NOT EXISTS variation_enabled"
    - path: "app/models/__init__.py"
      provides: "CampaignAttachment ORM model + Campaign.variation_enabled column"
      contains: "class CampaignAttachment"
    - path: "app/schemas/__init__.py"
      provides: "variation_enabled on CampaignCreate/Update/Response + has_attachment on CampaignResponse"
      contains: "variation_enabled"
    - path: "tests/conftest.py"
      provides: "exists-guarded apply of migration 054 in the hardcoded migration list"
      contains: "054_campaign_attachment_and_variation.sql"
    - path: "tests/test_campaign_attachment.py"
      provides: "RED-first model/drift tests: raw INSERT omitting defaulted cols OK; variation_enabled defaults true; migration idempotent"
      min_lines: 50
  key_links:
    - from: "app/models/__init__.py::CampaignAttachment"
      to: "campaign_attachments table"
      via: "SQLAlchemy Base metadata create_all + migration 054"
      pattern: "class CampaignAttachment"
---

<objective>
Lay the data-model foundation both features stand on: the `campaign_attachments` 1-1 BYTEA-blob table (D-01/D-02/D-04) and the `campaigns.variation_enabled` flag (D-13). Add the idempotent migration 054, the ORM mirrors (with the mandatory server_default drift guard), the Pydantic schema fields, and the conftest migration entry so the test DB matches prod.

Purpose: everything downstream (attachment endpoint 24-04, enqueue 24-05, worker 24-06) depends on this schema — so this is Wave 1, no deps, and stays deliberately small.
Output: migration 054 + ORM + schemas + conftest + a RED-first drift/idempotency test file.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-CONTEXT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-RESEARCH.md

<interfaces>
<!-- Exact ORM shape downstream plans read. -->
```python
# app/models/__init__.py — Campaign gains (near allow_recontact/follow_up_enabled ~line 739/751):
variation_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))

# NEW model (mirror CsvImport at models:598-612 + AccountImportStaging id-pattern at 627):
class CampaignAttachment(Base):
    __tablename__ = "campaign_attachments"
    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    campaign_id  = Column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, unique=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    file_data    = Column(LargeBinary, nullable=False)
    file_name    = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=True)
    size_bytes   = Column(BigInteger, nullable=False, default=0, server_default="0")
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
```
(BigInteger, LargeBinary, text, func already imported in app/models/__init__.py:1 — verified.)
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Migration 054 + ORM mirror (CampaignAttachment + variation_enabled)</name>
  <read_first>
    - app/models/__init__.py:598-632 (CsvImport + AccountImportStaging — blob + id-both-defaults precedent; comment at 615-620 explains the drift rule)
    - app/models/__init__.py:671-762 (Campaign model — add variation_enabled beside allow_recontact:739 / follow_up_enabled:751)
    - migrations/051_account_import.sql (idempotent table-create + column-add style to mirror)
    - migrations/052_sender_tg_premium.sql (latest on disk — confirms 054 is the next free slot after Phase 23 reserves 053)
  </read_first>
  <action>
    Confirm no migrations/053_* has landed on disk yet (Phase 23 owns 053). Create `migrations/054_campaign_attachment_and_variation.sql`, idempotent + fail-fast-safe:
    ```sql
    -- Phase 24: campaign first-message file attachment (D-01/D-02/D-04) + variation flag (D-13).
    -- D-13: per-campaign variation toggle, default ON — retro-enables existing campaigns.
    ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS variation_enabled boolean NOT NULL DEFAULT true;
    ALTER TABLE campaigns ALTER COLUMN variation_enabled SET DEFAULT true;   -- drift guard, idempotent

    -- D-01/D-02/D-04: 1-1 attachment blob table (blob kept OUT of SELECT campaigns).
    CREATE TABLE IF NOT EXISTS campaign_attachments (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        campaign_id   uuid NOT NULL UNIQUE REFERENCES campaigns(id) ON DELETE CASCADE,
        workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        file_data     bytea NOT NULL,
        file_name     varchar(255) NOT NULL,
        content_type  varchar(100),
        size_bytes    bigint NOT NULL DEFAULT 0,
        created_at    timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_campaign_attachments_workspace ON campaign_attachments(workspace_id);
    ```
    Then edit app/models/__init__.py: add the `variation_enabled` Column to Campaign (exactly as the interfaces block) and add the `CampaignAttachment` class (exactly as the interfaces block) near CsvImport/AccountImportStaging. Do NOT add any Campaign.relationship to the attachment (worker/endpoint query it by campaign_id directly — keeps the blob off every campaign SELECT, Pitfall 7).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_attachment.py -k "drift or idempotent or default" -x</automated>
  </verify>
  <acceptance_criteria>
    - `migrations/054_campaign_attachment_and_variation.sql` exists; `grep -c 'IF NOT EXISTS' migrations/054_*.sql` >= 3 AND contains `SET DEFAULT true` AND `campaign_id uuid NOT NULL UNIQUE`
    - No `migrations/053_*` created by this plan (053 is Phase 23's slot)
    - `app/models/__init__.py` contains `class CampaignAttachment` AND `variation_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))`
    - CampaignAttachment.id has BOTH `default=uuid.uuid4` AND `server_default=text("gen_random_uuid()")`; size_bytes has BOTH `default=0` AND `server_default="0"`
  </acceptance_criteria>
  <done>Migration 054 + ORM model in place; drift/idempotency tests GREEN; prod and create_all converge.</done>
</task>

<task type="auto">
  <name>Task 2: Pydantic schema fields + conftest 054 guard + RED drift tests</name>
  <read_first>
    - app/schemas/__init__.py:757-941 (CampaignCreate/Update/Response — add fields beside allow_recontact/follow_up_enabled; response has_attachment is new)
    - tests/conftest.py:150-232 (hardcoded migration list + exists-guarded 044/045 pattern to copy for 054)
    - tests/test_phase23_inbox_mutations.py or tests/test_campaign_router.py (async DB test idioms, raw-SQL insert style, workspace/campaign factory usage in conftest:493+)
  </read_first>
  <action>
    1. app/schemas/__init__.py: add `variation_enabled: bool = True` to CampaignCreate (~beside line 792 allow_recontact); `variation_enabled: Optional[bool] = None` to CampaignUpdate (~line 867); and to CampaignResponse (~line 929) add both `variation_enabled: bool = True` and `has_attachment: bool = False` (has_attachment is computed by the router, not a DB column). Comment each with the D-13/D-19 tag.
    2. tests/conftest.py: append an exists-guarded apply of migration 054 right after the 045 block (~line 232), mirroring the 044/045 pattern exactly:
       ```python
       _mig_054 = PROJECT_ROOT / "migrations" / "054_campaign_attachment_and_variation.sql"
       if _mig_054.exists():
           await asyncpg_conn.execute(_mig_054.read_text())
       ```
       Comment: campaign_attachments table + campaigns.variation_enabled come from ORM create_all (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS are no-ops here); the SET DEFAULT/UNIQUE/index are SQL-only — apply for parity. Exists-guard keeps this green until 054 lands.
    3. tests/test_campaign_attachment.py (RED, extended by 24-04 later): write the model/drift/idempotency tests:
       - test_variation_default_true: create a Campaign via the ORM (or raw INSERT omitting variation_enabled) → the row reads variation_enabled is True.
       - test_attachment_raw_insert_omitting_defaults: `INSERT INTO campaign_attachments (campaign_id, workspace_id, file_data, file_name) VALUES (...)` omitting id/size_bytes/created_at → succeeds (defaults fire, no NotNullViolation); row's size_bytes == 0, id is non-null.
       - test_migration_054_idempotent: read migrations/054_*.sql and execute its text twice on the test connection → second apply raises nothing.
       Use the existing workspace/campaign factories in conftest:493+.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_attachment.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `app/schemas/__init__.py` grep: `variation_enabled: bool = True` (Create) AND `variation_enabled: Optional[bool] = None` (Update) AND `has_attachment: bool = False` (Response)
    - `grep -c '054_campaign_attachment_and_variation.sql' tests/conftest.py` >= 1
    - `tests/test_campaign_attachment.py` contains `def test_variation_default_true`, `def test_attachment_raw_insert_omitting_defaults`, `def test_migration_054_idempotent`
    - `pytest tests/test_campaign_attachment.py` exits 0 (all GREEN)
  </acceptance_criteria>
  <done>Schemas carry variation_enabled + has_attachment; conftest applies 054; drift + idempotency tests GREEN. Endpoint tests are added later by 24-04 (extends this file).</done>
</task>

</tasks>

<verification>
- `pytest tests/test_campaign_attachment.py -x` GREEN.
- `grep -P '[\x{200b}\x{200c}\x{200d}\x{2060}]' migrations/054_*.sql app/models/__init__.py` returns nothing (no invisible glyphs leaked into schema files).
</verification>

<success_criteria>
campaign_attachments (1-1, UNIQUE campaign_id, BYTEA blob, CASCADE) and campaigns.variation_enabled (NOT NULL DEFAULT true) exist in prod (migration 054), test DB (conftest guard) and fresh DB (create_all) identically; schemas expose variation_enabled + has_attachment; raw INSERTs omitting defaulted columns never NotNullViolation (D-01/D-02/D-04/D-13).
</success_criteria>

<output>
After completion, create `.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-02-SUMMARY.md`.
</output>
