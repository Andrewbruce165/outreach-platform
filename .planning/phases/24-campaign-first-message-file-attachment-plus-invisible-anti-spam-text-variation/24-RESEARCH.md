# Phase 24: Campaign first-message file attachment + invisible anti-spam text variation — Research

**Researched:** 2026-07-07
**Domain:** Telethon media send (auto-media), Unicode invisible-text watermarking, Telegram anti-spam heuristics, FastAPI multipart upload, PostgreSQL BYTEA storage
**Confidence:** HIGH on code/integration facts and Telethon mechanics; MEDIUM on the *effectiveness* of zero-width variation against Telegram dedup (inherently closed-source — see D-11).

## Summary

Both capabilities bolt onto the **existing** campaign opener path (`campaign_enqueue.py → message_queue → queue.py worker → telegram.py`). The building blocks are already present: `MessageQueue` already carries `file_url`/`file_name`/`caption`/`item_type`; `send_file()` already implements the 1024-caption→overflow-follow-up pattern and full Telethon error mapping; `CsvImport.file_data` is a working BYTEA-blob precedent; `render_template()` is the clean opener render. The work is: (1) add a 1-1 `campaign_attachments` blob table + a multipart upload/delete endpoint + a `variation_enabled` flag on `campaigns`; (2) make the enqueue worker emit `item_type='file'` rows when a campaign has an attachment; (3) extend `send_file()` with a **blob source** and an **auto-media flag** (`force_document=False`); (4) apply a pure, stateless **invisible-variation** function to a *copy* of the opener text right before the Telethon call in the worker.

Two verified mechanics drive the design. **Auto-media:** Telethon's `send_file` computes `as_image = is_image(file) and not force_document`, and `is_image` is decided by the **file-name extension**. The existing code already creates the temp file with `suffix = os.path.splitext(file_name)[1]`, so preserving the original filename+extension (stored on the attachment row) is exactly what makes a `.jpg` blob arrive as a photo. **Markdown:** the client never sets `parse_mode`, so Telethon's default (`markdown`) is active on every opener — the variation algorithm must therefore avoid markdown delimiters, URLs, @mentions and emoji, which is naturally satisfied by inserting zero-width chars only *between two adjacent letters of a plain word*.

**Primary recommendation:** Store the blob in a dedicated 1-1 `campaign_attachments` table (not a column on `campaigns`). Persist the original filename+extension+mime — the extension is load-bearing for auto-media. Add `force_document: bool = True` and `file_bytes: Optional[bytes]` params to `send_file` (defaults preserve today's behavior). Apply variation as a pure function on a copy of the text in the worker, gated on `campaign_id IS NOT NULL AND extra_data.kind != 'followup' AND campaign.variation_enabled`. Treat the variation as **defense-in-depth only** (D-11): it defeats naive byte-exact dedup, not ML/behavioral spam detection.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

These are **binding**. Do not re-open. Cite by ID.

### Locked Decisions

**A. Attachment storage & model**
- **D-01:** Exactly ONE file per campaign (no album). Multi-attachment deferred.
- **D-02:** Bytes stored as **DB-blob** (PostgreSQL `LargeBinary`/BYTEA) modeled on `csv_imports.file_data`. Rationale: in `pg_dump` backups, free workspace-scope, no persistent volume, no api↔listener sync. Worker pulls bytes → temp file → Telethon; temp deleted in `finally`.
- **D-03:** Size limit **~50 MB** (as Phase 23 D-10), **any** file type; over-limit at upload → `FILE_TOO_LARGE`.
- **D-04:** Exact physical model (separate `campaign_attachments` 1-1 table vs blob columns on `campaigns`) is **planner's discretion**. Recommendation: separate table so the blob isn't dragged into every `SELECT campaigns`. Honor ORM `default=` vs `server_default=` drift for new NOT NULL columns (set BOTH).

**B. File delivery (opener)**
- **D-05:** File + text go as **ONE** message: media with `caption` = rendered opener. One queue row `item_type='file'`, `caption` carries the text, `message_text` empty or duplicates the clean text (discretion — but caption is the single source of truth).
- **D-06:** **Auto-media, `force_document=False`** (photo→photo, video→video, else document). Differs from today's queue `send_file` (`force_document=True`) but matches Phase 23 D-11. NB: generic file-queue path (`enqueue_file`/`item_type='file'`) has **no callers outside queue.py** — the campaign opener is the first live consumer, so the behavior change is safe. Still: add an auto-media param/flag, don't override the `send_file` default blindly.
- **D-07:** `caption` > 1024 chars → **reuse existing overflow pattern**: file without caption + full text as a separate follow-up message. Nothing lost.
- **D-08:** Byte source for opener send = **DB-blob → temp** (not URL download). Do not delete the existing URL path in `send_file`; add the blob source compatibly.

**C. Invisible variation — technique**
- **D-09:** **Combo:** zero-width insertions (U+200B ZWSP, U+200C ZWNJ, U+2060 WORD JOINER) as the base + **occasional** near-invisible jitter (NBSP `U+00A0` / thin space instead of a regular space). Homoglyphs **rejected** (break copy-paste/search; mixed Cyrillic↔Latin script can itself be a Telegram spam signal).
- **D-10:** "Invisible" invariant **allows near-invisible** (NBSP / thin space), not only true zero-width. Conscious compromise for entropy + resilience to possible normalization.
- **D-11:** **Accepted risk:** if Telegram normalizes zero-width/spaces before dedup comparison, the effect is weaker (but harmless). Document as accepted risk; do NOT promise deliverability on the strength of this measure alone.

**D. Variation — control**
- **D-12:** Scope = **campaign opener only** (including overflow-follow-up as part of the opener). AI replies are already unique; Phase 19 follow-ups are out of scope (deferred).
- **D-13:** Toggle = **per-campaign flag, default ON** (`server_default=true`). Changes behavior of existing campaigns — account for in migration. API bounds like other campaign fields (Literal/API-level validation); DB CHECK not required.
- **D-14:** Application moment = **at send in worker**: a **copy** of the text is varied right before the Telethon call. `message_queue.message_text`/`caption` and the `messages` row stay **clean** (no invisible chars) → inbox/logs readable, `rerender_pending_queue` untouched, each send freshly unique. Variation applies to opener text AND caption AND overflow text.
- **D-15:** Intensity = **fixed default ("green corridor")**, hardcoded (guideline: 1–3 insertions per ~10 words, with an upper cap), **not** client-configurable in v1.
- **D-16:** Each send is **unique**: variation regenerated on every send (per-item random; do **not** reuse one seed across contacts).

**E. Queue / rerender interaction**
- **D-17:** Opener-with-file = queue row `item_type='file'` with `caption`. `rerender_pending_queue` currently updates `message_text` **only** for `item_type='message'` — **extend** it so editing `campaign.message_template` also re-renders `caption` of pending `item_type='file'` rows of the same campaign.
- **D-18:** Opener-with-file counts as **one** new dialog / **one** send (one queue item → one rate-limit tick → one new-dialog cap) — like a normal text opener. Limits/caps unchanged.

**F. API / frontend**
- **D-19:** File upload = **separate multipart endpoint** (e.g. `POST /campaigns/{id}/attachment`, `DELETE` to remove), since campaign create/patch are JSON. The **variation flag** is part of the campaign JSON schema (create/patch). Exact names/shapes = planner. Update `lovable-handoff/openapi.json` + `error-codes.md` (`FILE_TOO_LARGE` etc.). NB: Lovable may send non-standard field names — build alias tolerance (like `SendMessageFromUIRequest`).
- **D-20:** `duplicate_campaign` must copy **both** the attachment **and** the variation flag.

### Claude's Discretion (research recommends below)
- Exact storage model (separate table vs columns), column/endpoint/schema names.
- Exact zero-width alphabet + insertion algorithm (positions/frequency); "green corridor" density implementation.
- How to separate auto-media (opener) vs generic `send_file` (extra param/flag).
- 50 MB limit implementation (`Content-Length` vs stream-to-temp with early abort).
- Whether to log the applied variation (debug).
- Invisibility tests: `strip(zero-width + NBSP + thin-space) == original`; two runs of the same text → different bytes.

### Deferred Ideas (OUT OF SCOPE)
- Multiple attachments / album (media-group) — v1 = exactly one file (D-01).
- Variation of follow-up messages (Phase 19) and AI replies — out of scope (D-12).
- Configurable variation intensity (low/med/high) — v1 fixed default (D-15).
- Robustness against Telegram normalization — accepted risk D-11; revisit only if zero-width proves insufficient.
- Persistent object storage for media — v1 = DB-blob (D-02); revisit at volume.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

No REQ IDs are mapped in ROADMAP.md (Requirements = TBD). Success criteria are derived from the locked CONTEXT decisions. The planner should treat each row below as a verifiable requirement.

| Derived ID | Behavior | Source | Research Support |
|-----------|----------|--------|------------------|
| ATT-STORE | One BYTEA blob per campaign, ≤50 MB, any type; over-limit → `FILE_TOO_LARGE` | D-01/02/03 | `CsvImport.file_data` precedent (models:598-612); size-guard pattern (account_import.py:95-105) |
| ATT-UPLOAD | Multipart `POST /campaigns/{id}/attachment` + `DELETE`; alias-tolerant | D-19 | UploadFile pattern (account_import.py:85-105); AliasChoices (schemas:1105-1114) |
| ATT-DELIVER | Opener-with-file sent as ONE media msg, caption=opener, auto-media (`force_document=False`) | D-05/06/08 | `send_file` (telegram.py:910-984); `as_image = is_image and not force_document` (verified) |
| ATT-OVERFLOW | caption >1024 → file w/o caption + full text follow-up | D-07 | Existing overflow (telegram.py:964-991) |
| ATT-ENQUEUE | Campaign with attachment enqueues `item_type='file'` (not `'message'`); counts as ONE tick | D-05/18 | `campaign_enqueue.py:387-410` (currently always `'message'`) |
| VAR-INVIS | `strip(ZW + NBSP→space + thin→space)(vary(x)) == x` | D-09/10 | Pure function; §Variation Algorithm |
| VAR-UNIQUE | Two renders of same text → different bytes; both strip-equal | D-16 | Pure function |
| VAR-SCOPE | Variation applied to opener text/caption/overflow ONLY; NOT follow-ups; DB stays clean | D-12/14 | Worker gate on `extra_data.kind != 'followup'` (queue.py:819-823) |
| VAR-FLAG | Per-campaign `variation_enabled` default ON | D-13 | `Campaign` bool-flag precedents (models:739/751) |
| RER-FILE | Editing template re-renders `caption` of pending `item_type='file'` rows | D-17 | `rerender_pending_queue` (campaign_enqueue.py:425-462) |
| DUP-COPY | `duplicate_campaign` copies attachment + variation flag | D-20 | `duplicate_campaign` (campaigns.py:976-1077) |
</phase_requirements>

---

## Project Constraints (from CLAUDE.md)

- **Migrations:** raw SQL only, `NNN_short_name.sql`, **idempotent** (`IF NOT EXISTS`, `DO $$ … EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`), auto-applied at api start, **fail-fast** (api won't start on a failed migration). Never Alembic.
- **Migration number:** latest committed on disk = **052** (`052_sender_tg_premium.sql`). **Phase 23 reserves slot 053** (`053_phase23_messages_media.sql`, confirmed in `23-01-…-PLAN.md`). Phase 24 **depends on Phase 23** → use **054+**. ⚠ Confirm the real max at plan time; if 053 has landed, Phase 24 = 054 (and 055 if two migrations).
- **Async everywhere:** all DB via `async/await` + `AsyncSession`. No `time.sleep()`, no sync `requests`, no `print()`.
- **Queue/rate-limits:** do NOT touch intervals, rate limits, or new-dialog caps (D-18 says unchanged — this is the safe default).
- **ORM drift (memory `project-orm-default-vs-server-default-drift`):** `create_all` (test/fresh DB) builds schema from the ORM, **without** DB defaults for `default=`-only columns. Every new NOT NULL column MUST set **both** `server_default=` AND an ORM value; every id uses BOTH `default=uuid.uuid4` and `server_default=text("gen_random_uuid()")`. Also `ALTER … SET DEFAULT` in the migration.
- **Tests:** ONLY via test-overlay:
  `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`
  Never `docker compose run --rm api pytest` (DROP SCHEMA → prod). Never `down -v` (wipes prod volume — memory `feedback-never-down-v-on-tg-outreach`).
- **Deploy:** `docker compose up -d --build api`. This phase touches the api container + the in-api queue worker; the separate `listener` container is not involved unless plans say otherwise.

---

## Standard Stack

Everything needed is already a project dependency — **no new packages**. The variation feature is pure stdlib.

| Library | Version (pinned) | Purpose | Notes |
|---------|------------------|---------|-------|
| Telethon | **1.42.0** | MTProto send (`send_file`, `send_message`) | v1 line; `as_image = is_image and not force_document`; default `parse_mode = markdown` |
| httpx | 0.26.0 | (existing) URL download in `send_file` | Keep for the legacy URL path (D-08); opener uses blob→temp |
| SQLAlchemy | 2.0 async | ORM (`LargeBinary`/BYTEA blob) | `CsvImport`/`AccountImportStaging` blob precedents |
| PostgreSQL | 16 | BYTEA storage | Blob rides in `pg_dump` (D-02) |
| FastAPI | (existing) | `UploadFile = File(...)` multipart | account_import/contacts/KB precedents |
| Python `unicodedata`, `re`, `random`, `secrets` | stdlib | Variation function (codepoint insertion, grapheme/URL guards) | No 3rd-party lib needed |
| Python `tempfile`, `os` | stdlib | blob → temp file (already used in `send_file`) | temp suffix from filename ext is load-bearing |

**Do NOT add** a homoglyph/obfuscation library, a "spintax" package, or an emoji/grapheme library. The variation is ~40 lines of stdlib; a Unicode-aware regex handles grapheme/URL protection adequately (see §Variation Algorithm).

**Version verification:** Telethon 1.42.0 confirmed in `requirements.txt`; the `send_file` photo-vs-document logic and markdown-default were verified against the v1 source (`telethon/client/uploads.py`) and 1.44 docs — behavior is identical to 1.42.

---

## Architecture Patterns & Integration Points (file:line, verified)

### 1. Attachment storage — recommended model (D-04 discretion)
Separate 1-1 table (blob stays out of `SELECT campaigns`):

```
campaign_attachments
  id            UUID PK   default uuid4 + server_default gen_random_uuid()
  campaign_id   UUID FK campaigns ON DELETE CASCADE  UNIQUE   -- 1-1
  workspace_id  UUID FK workspaces ON DELETE CASCADE          -- scope/defence-in-depth
  file_data     BYTEA  NOT NULL                               -- the blob (D-02)
  file_name     VARCHAR(255) NOT NULL                         -- ORIGINAL name + EXTENSION (load-bearing, D-06)
  content_type  VARCHAR(100)                                  -- UploadFile.content_type (advisory)
  size_bytes    BIGINT NOT NULL server_default '0'
  created_at    TIMESTAMPTZ server_default now()
```
- No `expires_at` (permanent campaign asset, unlike the CSV/ZIP preview blobs which have TTLs).
- Model precedent: `CsvImport` (`app/models/__init__.py:598-612`), `AccountImportStaging` (622-632). Copy the "id uses BOTH default + server_default" pattern (comment at models:615-620).
- Mirror the ORM class exactly to the migration DDL (drift memory).

### 2. `variation_enabled` flag on `campaigns` (D-13)
- ORM: `variation_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))`. Add next to the other campaign booleans (`allow_recontact` models:739, `follow_up_enabled` models:751).
- Migration: `ADD COLUMN IF NOT EXISTS variation_enabled boolean NOT NULL DEFAULT true;` — the `DEFAULT true` also **retro-enables existing campaigns** (D-13 "changes behavior of existing campaigns").
- Pydantic: add `variation_enabled: bool = True` to `CampaignCreate` (schemas:757) and `Optional[bool] = None` to `CampaignUpdate` (schemas:835); expose in `CampaignResponse` (schemas:895). Wire in `create_campaign` (campaigns.py:475-521), `patch_campaign` (setattr loop covers it, campaigns.py:706-707), `duplicate_campaign` (campaigns.py:1009-1051, D-20).

### 3. Multipart upload endpoint (D-19)
Model on `account_import.py:85-105`:
```python
@router.post("/{campaign_id}/attachment")
async def upload_attachment(campaign_id: UUID, file: UploadFile = File(...), ctx=..., db=...):
    raw = await file.read()
    if len(raw) > MAX_ATTACHMENT_BYTES:          # 50*1024*1024
        raise HTTPException(413, {"code": "FILE_TOO_LARGE", "message": f"Max {MAX_ATTACHMENT_BYTES} bytes"})
    # upsert one row per campaign (ON CONFLICT (campaign_id) or delete-then-insert)
```
- **Status code:** established convention for `FILE_TOO_LARGE` is **413** (account_import.py:100, contacts.py:314, knowledge_bases.py:334). Use 413.
- **Size limit (D-03/discretion):** `await file.read()` then `len(raw)` guard mirrors every existing uploader — simplest and consistent. (Streaming-with-early-abort is possible but no precedent; not worth it at 50 MB.)
- **Alias tolerance (D-19):** the `AliasChoices` precedent is `schemas/__init__.py:1105-1114` (`SendMessageFromUIRequest` reads `message`|`message_text`). For a multipart file field, tolerate common names (`file`|`attachment`) via multiple `UploadFile` params or a small dependency.
- **DELETE** `/campaigns/{id}/attachment` → delete the row (204).

### 4. Enqueue emits `item_type='file'` when attachment present (D-05/18)
- `campaign_enqueue.py:387-410` currently does a raw `INSERT … 'message' …`. Extend: load the campaign's attachment presence once per campaign per tick (one `SELECT 1 FROM campaign_attachments WHERE campaign_id=`), then per-contact INSERT with `item_type='file'`, `caption=:text` (and `message_text=:text` mirror for inbox/log readability), else `item_type='message'`.
- **Still ONE row per contact** → one rate-limit tick / one new-dialog cap (D-18 satisfied structurally).
- The queue row does **not** need `file_url` for campaign openers (blob lives in `campaign_attachments`, keyed by `campaign_id`). Keep `file_url` NULL; the worker loads the blob by `campaign_id`.

### 5. Worker send branch + variation application (D-08/D-14)
`queue.py:881-901` is the branch point:
```python
if item.item_type == QueueItemType.file:
    result = await telegram_service.send_file(..., file_url=item.file_url, caption=item.caption, ...)
else:
    result = await telegram_service.send_message(..., message=item.message_text, ...)
```
Changes:
- For a **campaign** file opener (`item.campaign_id` set, `file_url` NULL): load `campaign_attachments.file_data` + `file_name` by `campaign_id`; pass `file_bytes=` + `file_name=` + `force_document=False` to `send_file`.
- **Variation gate (D-12/14):** apply `vary()` to a *copy* of the text right here, before the Telethon call, when:
  `item.campaign_id IS NOT NULL` **AND** `(item.extra_data or {}).get("kind") != "followup"` **AND** the campaign's `variation_enabled` is true.
  - Follow-up discriminator is verified: `extra_data.kind == "followup"` (queue.py:819-823).
  - Read `variation_enabled` at send time (a lightweight JOIN/SELECT on `campaigns`) so toggling the flag affects already-pending rows immediately (consistent with the rerender philosophy). Do NOT snapshot it onto the queue row.
  - Apply to `item.message_text` (text opener) and to the caption passed to `send_file`. The **overflow follow-up text** inside `send_file` is also part of the opener (D-12) → vary it too (see §Variation wiring in send_file).
- **DB stays clean (D-14):** the `messages` log write (queue.py:921-930) and the queue row are written from `item.message_text`/`item.caption`, which are NEVER mutated. Only the local copy handed to Telethon is varied. ✅ This also means `rerender_pending_queue` is untouched by variation.

### 6. `send_file` extension — blob source + auto-media (D-06/D-08)
`telegram.py:910-984`. Recommended signature additions (defaults preserve today's behavior for any future caller):
```python
async def send_file(self, client, phone, recipient_name, file_url=None,
                    file_name=None, caption=None, sender_id=None, workspace_id=None,
                    file_bytes: Optional[bytes] = None,      # NEW: blob source (D-08)
                    force_document: bool = True):            # NEW: default True = unchanged (D-06)
```
- If `file_bytes` is provided → skip the httpx download (telegram.py:953-956), write bytes straight to the temp file. Temp suffix from `file_name` extension is already computed at telegram.py:959 — **keep it**; it is what makes auto-media work.
- Pass `force_document=force_document` into `client.send_file` (replace the hardcoded `True` at telegram.py:982).
- **Variation of caption/overflow:** simplest is to vary in the worker and pass the already-varied caption in; but the **overflow** text is currently constructed *inside* `send_file` (telegram.py:986-988). Two clean options: (a) pass a `variation_fn` callable that `send_file` applies to `file_caption` and `overflow_text` just before send; or (b) keep variation entirely in the worker and have the worker pass the full (already-varied) caption — `send_file` then splits varied text at 1024 and varies nothing itself. **Recommendation:** option (b)-with-care — vary the *clean* caption in the worker, then let `send_file` do the 1024 split on the varied string. ⚠ Caveat: zero-width chars inflate byte/char length, so the 1024 split must be applied to the varied string (Telegram counts UTF-16 code units; keep a safety margin — see Pitfall 4).
- Error mapping already complete: `FLOOD_WAIT`/`PEER_FLOOD`/`USER_IS_BLOCKED`/`PRIVACY_RESTRICTED`/`ACCOUNT_FROZEN`/`FILE_DOWNLOAD_FAILED`/`SEND_FAILED` (telegram.py:1005-1080). Add no new media-specific errors; media send raises the same `FloodWaitError`/`PeerFloodError` family. `FILE_TOO_LARGE` for the >50 MB case is caught at **upload** (API), not at send.

### 7. Rerender extension (D-17)
`rerender_pending_queue` (campaign_enqueue.py:425-462) hard-filters `item_type = 'message'` (line 459) and updates `message_text`. Extend so it ALSO re-renders `caption` (and mirrored `message_text`) for pending `item_type='file'` rows of the campaign. Keep the per-row `WHERE id=:id AND status='pending'` re-check (no clobber of in-flight items). Called from `patch_campaign` (campaigns.py:711-712) and `POST /rerender-pending` (campaigns.py:775-787) — both keep working.

### 8. `enqueue_file` has no production callers — safe to leave/repurpose
Verified: `enqueue_file` (queue.py:1711-) is referenced only in tests and one doc comment in `send.py:13`; `item_type='file'` never appears in production INSERTs today. The campaign opener is the first live consumer of the file path, so switching auto-media on is safe (D-06). You do NOT need to route through `enqueue_file`; the campaign enqueue writes its own raw INSERT.

---

## Variation Algorithm (Claude's Discretion — recommendation)

### Codepoint safety (verified against Unicode + Telegram-client behavior)
| Codepoint | Name | Verdict | Notes |
|-----------|------|---------|-------|
| U+200B | ZERO WIDTH SPACE | ✅ use | Truly zero-width; occasionally normalized/collapsed by Telegram — works best mixed with others |
| U+2060 | WORD JOINER | ✅ use (preferred) | Zero-width, "often resilient" (less likely to be collapsed than U+200B) |
| U+200C | ZERO WIDTH NON-JOINER | ✅ use, with a caveat | Invisible in Latin/Cyrillic; it IS a semantic joiner-control in Arabic/Persian/Indic — safe here because we only insert between Latin/Cyrillic letters |
| **U+200D** | ZERO WIDTH JOINER | ❌ **NEVER** | This is the **emoji joiner**; inserting it can merge/alter emoji sequences and produce unexpected glyphs. CONTEXT D-09 correctly excludes it — keep it excluded |
| U+00A0 | NO-BREAK SPACE (NBSP) | ✅ occasional jitter | Renders as a normal-width space; imperceptible. Use as a space substitute |
| U+202F | NARROW NO-BREAK SPACE | ✅ occasional jitter | **Slightly narrow** visible width — the "near-invisible" compromise of D-10 |
| U+2009 | THIN SPACE | ⚠ optional | Visibly thinner than a normal space; more perceptible than U+202F. Prefer U+202F for jitter |

**Recommendation:** base alphabet = `{U+200B, U+200C, U+2060}` for insertions; space-jitter = `{U+00A0, U+202F}`. Match this exact set in the invisibility test (§Validation).

### Insertion rules (avoid every hazard from research_focus)
Insert a zero-width char **only between two adjacent alphabetic letters** (Latin `a-zA-Z` or Cyrillic `а-яА-ЯёЁ`) that are both inside a *plain word*. This single rule inherently avoids:
- **Markdown delimiters** (`* _ ~ \` [ ] ( )`) — never adjacent to a letter-letter gap. (Client default `parse_mode=markdown` is active — verified — so this matters.)
- **URLs / bare domains / emails** — Telegram auto-links these; inserting inside `example.com` breaks the link. Protect with a URL/email/@-mention/#-hashtag regex and never insert inside those spans.
- **Phone numbers / digit runs** — auto-linked `tel:`; letter-only insertion skips digits entirely.
- **Emoji / combining sequences** — never inside a grapheme cluster; letter-letter gaps are never inside an emoji or a base+combining-mark pair.

Space-jitter: replace a *small* random fraction of regular spaces (only spaces flanked by letters on both sides, outside protected spans) with NBSP/U+202F.

### Green-corridor density (validate/refine D-15)
- **Byte-uniqueness only needs 1 differing byte.** A single insertion already makes two sends byte-distinct. So even minimal density satisfies D-16.
- D-15's **1–3 insertions per ~10 words** is sound. Concretely: sample ~10–20% of eligible letter-letter gaps, randomized per send, with a **hard cap** (recommend **≤ 20 insertions** per message regardless of length) so a long opener doesn't accumulate hundreds of invisible chars (which amplifies any client that renders one visibly, and bloats copy-paste).
- Randomize per send (D-16): (a) count within the corridor, (b) positions, (c) which codepoint at each position. Use `random`/`secrets` — no shared seed across contacts.
- Keep the whole thing a **pure function** `vary(text: str) -> str` (no DB, no I/O) so it is trivially unit-testable and worker-cheap.

**Effectiveness honesty (D-11, MEDIUM confidence):** public sources describe Telegram's spam detection as **ML-based on content patterns/semantics + behavioral signals (rate, timing, stranger-volume, reports)** — not naive exact-hash. There is **no public evidence** that it strips zero-width before comparison, and equally no evidence byte-variation defeats the ML template detector. Net: variation reliably defeats **naive byte-exact dedup**; it is **defense-in-depth**, not a deliverability guarantee. The platform's real anti-spam levers remain behavioral (existing rate limits, new-dialog cap, warmup) — do not weaken those.

---

## Don't Hand-Roll

| Problem | Don't build | Use instead | Why |
|---------|-------------|-------------|-----|
| Blob storage + backup | Filesystem volume + api↔listener sync | BYTEA column, `CsvImport` pattern (D-02) | Rides `pg_dump`, workspace-scoped, no volume plumbing |
| Photo-vs-document decision | MIME sniffing / magic bytes | Telethon `force_document=False` + preserved filename extension | Telethon's `is_image` already does extension→type; reinventing it fights the library |
| Caption >1024 handling | New splitter | Existing `send_file` overflow (telegram.py:964-991) | Already handles it; just feed it the varied string (mind UTF-16 counting, Pitfall 4) |
| Template edit → pending rows | New queue-rewrite | Extend `rerender_pending_queue` (D-17) | Count-guarded, in-flight-safe UPDATE already exists |
| Multipart size guard | Custom middleware | `await file.read()` + `len` guard (account_import.py:95-105) | Consistent 413/`FILE_TOO_LARGE` across the codebase |
| Invisible watermark | Homoglyph/spintax lib | ~40-line stdlib `vary()` | Homoglyphs rejected (D-09); no dependency justified |

**Key insight:** almost the entire phase is *wiring existing, tested primitives together* — the only genuinely new code is the pure `vary()` function and the attachment table/endpoint.

---

## Common Pitfalls

### Pitfall 1: Losing auto-media because the temp file has no extension
**What goes wrong:** blob written to a temp file with no/wrong suffix → Telethon's `is_image` returns False → a `.jpg` arrives as a grey document, not a photo (breaks D-05 "feels like a live first message").
**Avoid:** persist the ORIGINAL filename+extension on `campaign_attachments`; keep `send_file`'s `suffix = os.path.splitext(file_name)[1]` (telegram.py:959); pass `force_document=False`. Verified: `as_image = is_image(file) and not force_document`, `is_image` is extension-driven.

### Pitfall 2: ORM `default=` vs `server_default=` drift (repeat offender)
**What goes wrong:** new NOT NULL column (`variation_enabled`, `size_bytes`) or the attachment `id` set with `default=` only → `create_all` (test/fresh DB) builds it WITHOUT a DB default → raw-SQL INSERT that omits the column → `NotNullViolation`. Bit warmup_sessions, contacts, kb_chunks.
**Avoid:** every new NOT NULL column: `server_default=` **and** an ORM value; every id: `default=uuid.uuid4` **and** `server_default=text("gen_random_uuid()")`; migration also `ALTER … SET DEFAULT`.

### Pitfall 3: Variation leaking into stored text / inbox / rerender
**What goes wrong:** varying `item.message_text`/`item.caption` in place (not a copy) → invisible chars land in `messages`, inbox, and get re-varied on rerender → unreadable logs, broken invariant.
**Avoid:** vary a **local copy** immediately before the Telethon call (D-14). Never write the varied string back to the DB. The `messages` write at queue.py:921-930 must read the untouched `item.message_text`.

### Pitfall 4: 1024 caption split miscounts after variation
**What goes wrong:** you vary the caption, then split at 1024 on Python `len()`. Telegram counts **UTF-16 code units**, and zero-width chars add length — a caption that was 1000 clean chars can exceed the limit after insertion, or the split can land mid-insertion.
**Avoid:** apply the 1024 overflow decision to the **varied** string, keep a safety margin (e.g. split at ~1000), and never let an insertion straddle the boundary. Simplest: vary first, then split; if the varied caption >1024, send file w/o caption + varied full text as follow-up (D-07).

### Pitfall 5: Applying variation to follow-ups or manual/inbox sends
**What goes wrong:** variation hits Phase-19 follow-up pings or Phase-23 inbox sends → out of scope (D-12), and could corrupt manually-typed text.
**Avoid:** gate strictly: `campaign_id IS NOT NULL AND extra_data.kind != 'followup' AND campaign.variation_enabled`. Verified follow-up marker: `extra_data.kind == "followup"` (queue.py:819-823).

### Pitfall 6: Migration number collision with Phase 23
**What goes wrong:** Phase 23 reserves 053; using 053 for Phase 24 → duplicate slot, fail-fast on the second-applied.
**Avoid:** use 054+ (confirm max at plan time).

### Pitfall 7: 50 MB blob inflates every backup / campaign SELECT
**What goes wrong:** blob-in-`campaigns` would drag 50 MB into every `SELECT campaigns` and into rerender/list queries.
**Avoid:** separate 1-1 table (D-04 recommendation); only the worker's send path `SELECT`s `file_data`. Note: `pg_dump` size grows with attachments (accepted per D-02) — flag to ops if many large campaigns.

---

## Code Examples

### Auto-media send with blob source (extension preserved) — Telethon 1.42
```python
# Source: telethon/client/uploads.py (v1) — as_image = is_image and not force_document
# Existing temp-suffix logic at app/services/telegram.py:959 is what makes this work.
suffix = os.path.splitext(file_name)[1] or ""      # e.g. ".jpg"  ← load-bearing
with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
    f.write(file_bytes)                             # blob, not URL download (D-08)
    tmp_path = f.name
sent = await client.send_file(peer, tmp_path, caption=file_caption,
                              file_name=file_name, force_document=False)  # D-06
```

### Variation gate in the worker (D-12/D-14)
```python
# app/services/queue.py ~881, before the send call
is_followup = isinstance(item.extra_data, dict) and item.extra_data.get("kind") == "followup"  # queue.py:819-823
apply_var = (item.campaign_id is not None) and (not is_followup) and campaign_variation_enabled
text_to_send = vary(item.message_text) if apply_var else item.message_text     # local copy only
caption_to_send = vary(item.caption) if apply_var else item.caption            # DB row untouched
```

### Invisibility invariant (test target)
```python
_ZW = {"\u200b", "\u200c", "\u2060"}          # ZWSP, ZWNJ, WORD JOINER
def strip_invisible(s: str) -> str:
    s = "".join(ch for ch in s if ch not in _ZW)
    return s.replace("\u00a0", " ").replace("\u2009", " ").replace("\u202f", " ")  # NBSP, THIN, NNBSP
assert strip_invisible(vary(x)) == x        # VAR-INVIS
assert vary(x) != vary(x)                    # VAR-UNIQUE (bytes differ)
assert strip_invisible(vary(x)) == strip_invisible(vary(x)) == x
```

---

## Environment Availability

Purely code + DB + migration changes. All dependencies already present and pinned (Telethon 1.42.0, httpx 0.26.0, SQLAlchemy 2.0 async, PostgreSQL 16, FastAPI, pytest 8 / pytest-asyncio). No external tool/service acquisition needed. **No blockers.**

---

## Validation Architecture

> `workflow.nyquist_validation` is enabled (config.json). Section included.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest ≥ 8.0 + pytest-asyncio ≥ 0.23 (`asyncio_mode = "auto"`, `testpaths = ["tests"]`, `pyproject.toml`) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]`; fixtures in `tests/conftest.py` |
| Quick run (targeted) | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_variation.py tests/test_campaign_attachment.py -x` |
| Full suite | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| ⚠ Guard | NEVER `docker compose run --rm api pytest` without the test overlay (DROP SCHEMA → prod). NEVER `down -v`. |

**Baseline caveat (memory `project-test-baseline-red`):** the full suite is order-dependent and RED on clean main (~88 failed/115 errors in aggregate) while the same files pass targeted/isolated. Do NOT trust full-suite `TEST_EXIT==0`; verify Phase-24 tests with a **targeted `-k`/path subset**, and diff against a git-stash clean tree.

### Requirements → Test Map
| Req | Behavior | Type | Automated command (targeted) | Mock vs Real |
|-----|----------|------|------------------------------|--------------|
| VAR-INVIS | `strip_invisible(vary(x)) == x` over Latin/Cyrillic/emoji/URL/@/markdown fixtures | unit (pure) | `pytest tests/test_variation.py -k invisible` | Real (pure fn) |
| VAR-UNIQUE | two `vary(x)` → different bytes, both strip-equal | unit (pure) | `pytest tests/test_variation.py -k unique` | Real |
| VAR-SAFE | no insertion inside URL/@mention/email/emoji/markdown-delim; digit runs untouched | unit (pure) | `pytest tests/test_variation.py -k safe_spans` | Real |
| VAR-SCOPE | worker varies opener text+caption; skips `kind='followup'`; DB `message_text`/`caption` stay clean | integration | `pytest tests/test_queue_variation.py` | Mock Telethon (capture sent text), Real DB |
| VAR-FLAG | `variation_enabled` default true; toggling off → worker sends clean text | integration | `pytest tests/test_queue_variation.py -k flag` | Mock Telethon, Real DB |
| ATT-STORE | blob persisted; `>50MB` → 413 `FILE_TOO_LARGE`; DELETE removes | integration | `pytest tests/test_campaign_attachment.py -k upload` | Real DB, no Telethon |
| ATT-DELIVER | blob→temp with correct extension → `send_file(force_document=False)`; caption=opener | integration | `pytest tests/test_queue_file_opener.py` | Mock `client.send_file` (assert temp suffix + force_document arg), Real DB |
| ATT-OVERFLOW | varied caption >1024 → file w/o caption + full text follow-up | integration | `pytest tests/test_queue_file_opener.py -k overflow` | Mock Telethon (2 calls asserted) |
| ATT-ENQUEUE | campaign w/ attachment enqueues ONE `item_type='file'` row per contact | integration | `pytest tests/test_campaign_enqueue_worker.py -k file` | Real DB |
| RER-FILE | template edit re-renders `caption` of pending `item_type='file'` rows | integration | `pytest tests/test_rerender_pending_queue.py -k file` | Real DB |
| DUP-COPY | `duplicate_campaign` copies attachment + variation flag | integration | `pytest tests/test_campaign_router.py -k duplicate_attachment` | Real DB |

### Sampling Rate
- **Per task commit:** `pytest tests/test_variation.py tests/test_campaign_attachment.py -x` (fast; pure-fn tests are instant).
- **Per wave merge:** targeted subset across all Phase-24 files + adjacent (`test_queue_*`, `test_campaign_*`, `test_rerender_pending_queue.py`).
- **Phase gate:** targeted Phase-24 subset green + a clean-tree diff (do not rely on full-suite green — see baseline caveat). One live smoke on a real photo through a warmed sender to confirm it arrives AS A PHOTO with caption (auto-media can't be fully proven by mocks).

### Wave 0 Gaps
- [ ] `tests/test_variation.py` — pure-fn invisibility/uniqueness/safe-span tests (VAR-INVIS/UNIQUE/SAFE). No fixtures needed.
- [ ] `tests/test_campaign_attachment.py` — upload/size-limit/delete (ATT-STORE). Reuse workspace/campaign factories in `conftest.py:493+`.
- [ ] `tests/test_queue_variation.py` — worker gate + clean-DB invariant (VAR-SCOPE/FLAG). Mock `telegram_service.send_message/send_file` (AsyncMock) to capture the text actually sent; pattern precedents: `test_queue_new_dialog_limit.py`, `test_send_campaign.py`.
- [ ] `tests/test_queue_file_opener.py` — blob→temp→auto-media + overflow (ATT-DELIVER/OVERFLOW). Mock `client.send_file`; assert `force_document=False` and temp suffix.
- [ ] Extend existing: `test_rerender_pending_queue.py` (RER-FILE), `test_campaign_enqueue_worker.py` (ATT-ENQUEUE), `test_campaign_router.py` (DUP-COPY).
- Framework install: none — pytest/pytest-asyncio already present.

---

## State of the Art

| Old approach | Current approach | Impact |
|--------------|------------------|--------|
| `imghdr` content sniffing (Telethon <1.x) | extension/MIME-driven `is_image`; provide filename ext for buffers | Must preserve/synthesize the extension; already handled by temp suffix |
| Regular-user caption 1024 | Premium caption 2048/4096 (mig 052 added `senders.tg_premium`) | D-07 keeps 1024 = conservative-safe (never exceeds a premium's real limit; only splits earlier). Optional future refinement, NOT required |

---

## Open Questions

1. **Where to vary the overflow text — worker or `send_file`?**
   - Known: overflow text is built inside `send_file` (telegram.py:986-988); variation is scoped to the whole opener incl. overflow (D-12).
   - Recommendation: vary the clean caption in the worker, pass the varied string; let `send_file` split it at ~1000 (UTF-16-safe margin, Pitfall 4). Avoids a callback param and keeps `send_file` dumb.

2. **How does the worker read `variation_enabled` (JOIN vs snapshot)?**
   - Recommendation: read at send time (small SELECT/JOIN on `campaigns`), so toggling the flag reaches already-pending rows — matches the rerender philosophy. Do not stamp it on the queue row.

3. **Premium caption limit (1024 vs 2048).**
   - D-07 locks 1024. Leave as-is; the `tg_premium` flag makes a future bump trivial but it is out of scope now.

4. **Effectiveness of variation vs Telegram dedup (D-11, unresolvable publicly).**
   - No public source confirms whether Telegram normalizes zero-width/spaces before comparison, nor whether byte-variation defeats the ML detector. Ship as defense-in-depth; do not promise deliverability. This is exactly the accepted risk D-11 already books.

---

## Sources

### Primary (HIGH confidence)
- **Codebase (read directly, cited inline):** `app/services/telegram.py:775-1084` (`send_message`, `send_file`, overflow, error map), `app/services/queue.py:809-901,1711-1759` (worker branch, follow-up marker, `enqueue_file`), `app/services/campaign_enqueue.py:340-462` (enqueue INSERT + `rerender_pending_queue`), `app/services/template.py:1-151` (`render_template`), `app/models/__init__.py:11-27,275-319,598-666,671-787` (enums, `MessageQueue`, `CsvImport`, `AccountImportStaging`, `Campaign`), `app/routers/campaigns.py:434-1077` (create/patch/duplicate/rerender), `app/routers/account_import.py:85-105` + `app/routers/contacts.py:312-317` (multipart + size guard), `app/schemas/__init__.py:757-906,1105-1114` (campaign schemas, AliasChoices), `migrations/052_sender_tg_premium.sql`, `pyproject.toml` / `requirements.txt` (versions), `.planning/phases/23-…/23-01-…-PLAN.md` (migration 053 reservation).
- **Telethon v1 source** — `telethon/client/uploads.py`: `as_image = is_image and not force_document`; extension-driven `is_image`; `file_name += utils._get_extension(...)`.
- **Telethon docs 1.44** — utils (`is_image`/`is_video`/`get_attributes`), client (`parse_mode` defaults to `telethon.extensions.markdown`).

### Secondary (MEDIUM confidence)
- Telegram caption limit 1024 (regular) / 2048–4096 (premium): bugs.telegram.org, limits.tginfo.me, lettercounter.org.
- Zero-width rendering in Telegram (U+200B sometimes collapsed; U+2060 resilient; U+200C/U+200D semantics): Wikipedia "Zero-width space", unicode-explorer, symbl.cc.

### Tertiary (LOW confidence — flagged for D-11)
- Telegram anti-spam / bulk-dedup being ML + behavioral rather than exact-hash: telegramgrowthstudio, adspower, dev.to "Lessons from processing millions of Telegram messages". No source addresses zero-width normalization directly — hence D-11 accepted risk.

## Metadata

**Confidence breakdown:**
- Code/integration facts: **HIGH** — files read directly, line-cited.
- Telethon auto-media + markdown-default: **HIGH** — verified against v1 source + docs.
- Codepoint safety + algorithm: **HIGH** for the letter-letter-insertion invariant (self-verifiable via the strip test); MEDIUM on U+200B collapse behavior.
- Variation *effectiveness* vs Telegram: **MEDIUM/LOW** — closed system; D-11 accepted risk.

**Research date:** 2026-07-07
**Valid until:** ~2026-08-07 for Telegram/Telethon facts (stable); codebase facts valid until the files change (re-grep line numbers at plan time — Phase 23 will land migration 053 and edit `messages`).
