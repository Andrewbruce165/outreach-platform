---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
verified: 2026-07-08T07:57:53Z
status: human_needed
score: 34/35 must-haves verified
behavior_unverified: 1
overrides_applied: 0
gaps: []
deferred:
  - truth: "24-06 must_have: 'a campaign file-opener's messages inbox row carries the concrete message_type ... so the inbox renders a media bubble, not a plain-text line' (full read+render outcome, not the DB-write sub-claim)"
    addressed_in: "Phase 23 (Edit and delete-for-everyone of sent messages plus file sending from inbox UI) — planned but NOT YET EXECUTED (6 PLAN.md files exist, zero SUMMARY.md files)"
    evidence: "Phase 23 plan 23-01 explicitly scopes 'widened GET /messages SELECT' and 23-03/23-04 own the MessageResponse/message-rendering surface end-to-end (incoming AND, by natural extension, the same SELECT/schema fix closes the outgoing file-opener gap). Phase 23's own ROADMAP goal: 'входящие файлы ОТ контакта отображаются как file-бабблы' — same GET /conversations/{id}/messages + MessageResponse + frontend MessageBubble surface Phase 24 needs fixed."
behavior_unverified_items:
  - truth: "D-06 (24-07 must_have): a real .jpg attached to a campaign arrives in a real Telegram client AS AN INLINE PHOTO with the opener caption (not a grey document)"
    test: "Attach a real .jpg/.png (not PDF) to a test campaign with variation_enabled ON, one warmed sender, one controlled test contact; start the campaign; on the recipient's Telegram Desktop AND mobile confirm the message renders as an inline photo (not a document icon) with the opener text as its caption."
    expected: "Message renders as an inline photo bubble, caption reads cleanly, DB messages.message_type='photo' for that row."
    why_human: "Client-side rendering — Telethon's auto-media classification (force_document=False + preserved file extension) is unit-tested with mocked Telethon, but whether a REAL Telegram client actually renders a .jpg as an inline photo (vs. document) can only be observed on a real device. The one live smoke actually run (2026-07-07) used a PDF, which is a document in Telegram regardless of force_document — it proves document/blob delivery + clean caption + clean DB, but does not exercise the photo-specific code path at all on a real client."
human_verification:
  - test: "Re-run the 24-07 live-smoke checkpoint with a real .jpg or .png attachment (not a PDF)."
    expected: "Recipient device (Desktop + mobile) shows an inline photo bubble with the opener as caption; messages.message_type='photo' in DB for that send."
    why_human: "Client-rendering behavior; cannot be asserted from mocks or DB state alone (see behavior_unverified_items above)."
---

# Phase 24: Campaign First-Message File Attachment + Invisible Anti-Spam Text Variation — Verification Report

**Phase Goal:** Campaign first-message file attachment (upload/deliver a photo/document as auto-media with the opener as caption) plus invisible anti-spam text variation (per-send unicode variation that changes the wire bytes without changing what the recipient reads).
**Verified:** 2026-07-08T07:57:53Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

Merged from all 7 plans' `must_haves.truths` (no pre-mapped REQ-IDs in ROADMAP for this phase — contract is the D-01..D-20 decision cluster). ROADMAP has no separate `success_criteria` list distinct from the plan-level must-haves for this phase, so the plan frontmatter IS the contract.

| # | Truth (plan) | Status | Evidence |
|---|---|---|---|
| 1 | D-09 vary() emits only U+200B/U+200C/U+2060 + NBSP/U+202F jitter; U+200D and homoglyphs never used (24-01) | VERIFIED | `app/services/variation.py:1-40`; `tests/test_variation.py` 16/16 pass |
| 2 | D-10/D-14 `strip_invisible(vary(x)) == x` for Latin/Cyrillic/emoji/URL/@mention/markdown fixtures (24-01) | VERIFIED | `strip_invisible()` at variation.py:122; test suite pass |
| 3 | D-16 two independent `vary()` calls differ in bytes (24-01) | VERIFIED | test pass; confirmed no shared seed in code |
| 4 | D-09 safe-spans — never inserts inside URL/domain/email/@mention/#hashtag/digit-run/emoji (24-01) | VERIFIED | test pass |
| 5 | D-15 density cap ~1-3/10 words, hard cap 20 (24-01) | VERIFIED | test pass |
| 6 | D-11 pure stdlib function, no DB/I/O/network (24-01) | VERIFIED | code inspection — no imports beyond `re`/`random`/`string` |
| 7 | D-02/D-04 `campaign_attachments` 1-1 BYTEA-blob table, kept out of `SELECT campaigns` (24-02) | VERIFIED | `migrations/054...sql`; live schema confirmed via `\d campaign_attachments` |
| 8 | D-01 exactly one attachment/campaign — UNIQUE + ON DELETE CASCADE (24-02) | VERIFIED | migration 054 + live schema (`campaign_attachments_campaign_id_key` UNIQUE) |
| 9 | D-13 `campaigns.variation_enabled` NOT NULL DEFAULT true, exposed in Create/Update/Response (24-02) | VERIFIED | `app/schemas/__init__.py:807,879,946` |
| 10 | D-04 ORM-drift guard on new NOT NULL columns (24-02) | VERIFIED | `app/models/__init__.py:669-689` (server_default + ORM default both set) |
| 11 | Migration 054 idempotent (24-02) | VERIFIED | `ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` throughout; applied live |
| 12 | D-08 `send_file` accepts `file_bytes` blob source, URL path preserved (24-03) | VERIFIED | `app/services/telegram.py:910-1015`; `tests/test_send_file_blob.py` 5/5 pass |
| 13 | D-06 `force_document: bool = True` default preserves today's behavior; `False` → auto-media via preserved extension (24-03) | VERIFIED (mechanism, mocked) | code + tests confirm; real-client confirmation only done for document case — see item 20/21 |
| 14 | D-07 caption >1024 reuses existing overflow branch unchanged (24-03) | VERIFIED | test pass |
| 15 | D-19 upload/delete attachment endpoints, alias-tolerant, workspace-scoped, one-file upsert (24-04) | VERIFIED | `app/routers/campaigns.py:1116-1180`; `tests/test_campaign_attachment.py` pass |
| 16 | D-03 upload >50MB → 413 FILE_TOO_LARGE (24-04) | VERIFIED | `campaigns.py:1140-1144`; test pass |
| 17 | D-13 `variation_enabled` wired through create/update/response (24-04) | VERIFIED | code inspection + test |
| 18 | D-20 `duplicate_campaign` copies both `variation_enabled` and the attachment blob (24-04) | VERIFIED | `campaigns.py:1053,1075-1089`; live code confirms `variation_enabled=src.variation_enabled` + new `CampaignAttachment` row insert in the same transaction |
| 19 | D-05/D-18 attachment present → enqueue emits ONE `item_type='file'` row/contact with rendered caption+message_text (24-05) | VERIFIED | `app/services/campaign_enqueue.py:340-421`; test pass |
| 20 | No-attachment campaigns unchanged (`item_type='message'`) (24-05) | VERIFIED | code + test |
| 21 | Attachment presence resolved once per campaign, not per contact (24-05) | VERIFIED | single `SELECT 1 FROM campaign_attachments` at campaign_enqueue.py:346 |
| 22 | D-17 `rerender_pending_queue` re-renders pending file-row captions on template edit (24-05) | VERIFIED | campaign_enqueue.py:476-534; test pass |
| 23 | D-14 variation NOT applied at enqueue time (24-05) | VERIFIED | code inspection — enqueue writes clean rendered text only |
| 24 | D-14 variation applied to a LOCAL COPY at send time; DB (`message_queue`/`messages`/`messages_log`) never mutated (24-06) | VERIFIED | `app/services/queue.py:881-899`; **live production data**: real sent message row `message_text='Hi!'`, `has_invisible=false`, on a campaign with `variation_enabled=true` |
| 25 | D-12 variation gate: campaign + not-followup + `variation_enabled` read at send time (24-06) | VERIFIED | queue.py:889-891; `tests/test_queue_variation.py` 5/5 pass |
| 26 | D-16 `vary()` called fresh per send (24-06) | VERIFIED | code + test |
| 27 | D-05/D-06/D-08 file-opener blob loaded by `campaign_id`, sent via `send_file(file_bytes=..., force_document=False)` (24-06) | VERIFIED (document case, live prod) | `queue.py:905-947`; **live production data**: real `campaign_attachments` row (PDF) → real `messages` row with `message_type='document'`, correct `file_name`/`mime_type`/`size_bytes` |
| 28 | Text/message branch sends varied copy; `messages_log` write still reads untouched `item.message_text` (24-06) | VERIFIED | queue.py:990-999 reads `item.message_text`, not `text_to_send` |
| 29 | Inbox fidelity — `messages` row for a file-opener carries `message_type`/`file_name`/`mime_type`/`size_bytes` (the DB-write sub-claim) (24-06) | VERIFIED | `queue.py:1667-1688`; migration 055; **live prod row confirms all 4 fields populated correctly** |
| 29b | ...**"so the inbox renders a media bubble, not a plain-text line"** (the full read+render sub-claim, same bullet) (24-06) | **FAILED as literally written — DEFERRED, see Scope Assessment** | `GET /conversations/{id}/messages` SELECT (`app/routers/conversations.py:258-260`) and `MessageResponse` (`app/schemas/__init__.py:1094-1104`) do NOT include these 4 columns; frontend `MessageBubble` was not located/modified. A file-opener message is indistinguishable from a plain-text row in the app's own inbox UI today. |
| 30 | D-19 openapi.json regenerated with `/attachment` (POST+DELETE) + `variation_enabled` + `has_attachment` (24-07) | VERIFIED | `lovable-handoff/openapi.json` — both methods confirmed present |
| 31 | D-19 `error-codes.md` documents `FILE_TOO_LARGE` (24-07) | VERIFIED | `lovable-handoff/error-codes.md:22` |
| 32 | D-06 (live smoke): real .jpg arrives as INLINE PHOTO with caption (24-07) | ⚠️ **PRESENT_BEHAVIOR_UNVERIFIED** | Live smoke used a PDF, not a .jpg (24-07-SUMMARY.md "Issues Encountered" #4, self-admitted). Mechanism is unit-tested with mocks; real-client photo rendering never observed. See Human Verification. |
| 33 | D-09 (live smoke): varied opener shows no visible artifacts on Desktop/mobile (24-07) | VERIFIED | 24-07-SUMMARY.md: human confirmed "clean caption" on real device + real DB row confirmed byte-clean |
| 34 | Live production end-to-end delivery actually occurred (implicit roadmap goal) | VERIFIED | `campaign_attachments` row `77aeeb1c...` (PDF, 28348 bytes) → delivered `messages` row `1eee5629...` (`message_type='document'`, same file_name/mime/size) on 2026-07-07 |
| 35 | Requirements-completed frontmatter claim `[D-19, D-06, D-09]` in 24-07-SUMMARY.md accurately reflects verified state | **Internally contradicted** | The same SUMMARY's own prose (Issues Encountered #4) states "D-06 is not fully verified by this live smoke" — frontmatter overclaims relative to body. Flagged, not scored as a separate truth (folded into item 32's disposition). |

**Score:** 34/35 truths verified (1 present-but-behavior-unverified: item 32). Item 29b is treated as a deferred cross-phase item (see below), not counted against the score, per the Scope Assessment.

### Scope Assessment — the two flagged gaps (explicit request)

**1. Inbox media rendering (item 29b).** Investigated `24-06-worker-variation-and-blob-delivery-PLAN.md` and `24-07-handoff-and-live-smoke-PLAN.md` frontmatter directly (not just SUMMARY prose, per the request):

- The claim **is** a stated `must_haves.truths` bullet in 24-06's own PLAN.md frontmatter, not merely SUMMARY aspiration — so answer (b) has real textual grounding.
- However, 24-06's own `artifacts`/`key_links` blocks (the actual work contract) list ONLY `app/services/queue.py` + two test files — no router, no schema, no frontend file is declared anywhere in the plan. The "renders a media bubble" clause is a downstream causal claim riding on a write-path deliverable, not a separately-resourced piece of work.
- 24-06's own SUMMARY.md (line 111) is explicit about the intended scope: *"The bridge migration is required for correctness of the inbox-fidelity requirement and does not expand scope beyond the four columns Plan 24-06 consumes. If Phase 23 is later executed it can add the same columns idempotently without conflict."* — i.e., the plan's authors already knew the read/render side was Phase 23's job.
- The ROADMAP's Phase 24 goal sentence itself never mentions the app's own inbox UI — it is scoped to delivery to the real Telegram recipient ("он уходит одним media-сообщением...").
- **Phase 23** ("Edit and delete-for-everyone of sent messages plus file sending from inbox UI") is a real, already-planned (6 PLAN.md files exist), **not-yet-executed** phase (zero SUMMARY.md files in `.planning/phases/23-.../`) whose own scope explicitly includes "widened GET /messages SELECT" (23-03) and incoming-media `message_type` tagging (23-04) — the exact surface this gap needs.

**Verdict: (a) out of phase-24's ROADMAP-level scope**, with the caveat that 24-06's own must_have wording overreached beyond what its declared artifacts could deliver — that wording is a documentation defect worth fixing in future plans (don't state a must_have whose second half isn't backed by an artifact/key_link), not evidence that 24-06's executor skipped assigned work. Classified as `deferred`, addressed by Phase 23 once it executes. **Not a phase-24 blocker.** The SUMMARY's own recommendation (line 126: "Fix the inbox media-rendering gap ... tracked, not blocking, per user's explicit 'not now'") matches this conclusion.

**2. D-06 photo-specific claim (item 32).** This one IS squarely phase-24's own scope (no later phase owns it) — it's a stated `must_haves.truths` bullet in **24-07's** PLAN frontmatter, with an explicit `<how-to-verify>` step (step 3) requiring confirmation on a real device. The live smoke that ran used a PDF, not a `.jpg`, so this specific claim was never actually exercised on a real client. The underlying mechanism (`force_document=False` + extension-based classification to `photo`) is verified at the unit-test/mock level and the parallel document-delivery path is proven end-to-end in production — so this is not a "code is missing" gap, it's a "the one behavior that needed a real device was never actually tried with the right file type" gap. **Verdict: genuine phase-24 gap, not deferrable to another phase** — routed to human verification (re-run with a real `.jpg`), not blocking, but should not be closed out as done until re-tested.

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `app/services/variation.py` | `vary()`/`strip_invisible()` | VERIFIED | 135 lines, both functions present |
| `tests/test_variation.py` | RED-first unit tests | VERIFIED | 16 tests, all pass |
| `migrations/054_campaign_attachment_and_variation.sql` | attachment table + variation flag, idempotent | VERIFIED | applied live, idempotent guards present |
| `app/models/__init__.py::CampaignAttachment` | ORM model | VERIFIED | present, drift-guarded |
| `app/schemas/__init__.py` | `variation_enabled`/`has_attachment` | VERIFIED | present on Create/Update/Response |
| `app/services/telegram.py::send_file` | `file_bytes`/`force_document` params | VERIFIED | signature confirmed |
| `tests/test_send_file_blob.py` | blob/auto-media/overflow tests | VERIFIED | 5 tests pass |
| `app/routers/campaigns.py` | attachment upload/delete + duplicate copy | VERIFIED | endpoints + duplicate logic confirmed |
| `tests/test_campaign_attachment.py` | endpoint tests | VERIFIED | 15 tests pass |
| `app/services/campaign_enqueue.py` | conditional `item_type`, rerender extension | VERIFIED | confirmed by code + 20+9 tests |
| `app/services/queue.py` | variation gate, blob delivery, media-typed INSERT | VERIFIED | confirmed by code + live prod data |
| `tests/test_queue_variation.py` / `test_queue_file_opener.py` | gate + delivery tests | VERIFIED | 5+4 tests pass |
| `migrations/055_messages_media_columns.sql` | bridges never-executed Phase 23 mig 053 | VERIFIED | applied live, columns present |
| `lovable-handoff/openapi.json` / `error-codes.md` | regenerated contract | VERIFIED | `/attachment` (POST+DELETE), `variation_enabled`, `has_attachment`, `FILE_TOO_LARGE` all present |

### Data-Flow Trace (Level 4) — live production evidence

| Artifact | Data Source | Produces Real Data | Status |
|---|---|---|---|
| `campaign_attachments` row | `POST /campaigns/{id}/attachment` upload | Yes — real PDF, 28348 bytes, `campaign_id=9c6be719...` | FLOWING |
| `messages` row (file-opener) | `queue.py` send branch → `_upsert_conversation` INSERT | Yes — real row `message_type='document'`, `file_name`/`mime_type`/`size_bytes` all match the source attachment | FLOWING |
| `messages.message_text` (variation invariant) | `queue.py` local-copy variation | Yes — `message_text='Hi!'`, regex-checked `has_invisible=false` while `variation_enabled=true` on the source campaign | FLOWING (clean, as designed) |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Targeted phase-24 test subset | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_variation.py tests/test_campaign_attachment.py tests/test_send_file_blob.py tests/test_campaign_enqueue_worker.py tests/test_rerender_pending_queue.py tests/test_queue_variation.py tests/test_queue_file_opener.py -q` | `74 passed in 47.47s` | PASS |
| Live prod: attachment upload → delivery → media-typed inbox row | SQL query against `outreach-platform-db` | 1 real attachment row + 1 real matching `messages` row, fields consistent | PASS |
| Live prod: DB stays clean under variation | `message_text ~ '[invisible-codepoints]'` on the sent row | `false` (clean) while `variation_enabled=true` | PASS |
| D-06 photo-specific rendering | — | Not run (PDF used instead of .jpg) | SKIP → human verification |

### Probe Execution

No `scripts/*/tests/probe-*.sh` declared or found for this phase. N/A.

### Requirements Coverage

No pre-mapped REQ-IDs in ROADMAP for Phase 24 (confirmed — `grep` for the phase's cluster IDs `ATT-*`/`VAR-*`/`RER-FILE`/`DUP-COPY` in REQUIREMENTS.md returns nothing; contract lives entirely in `24-CONTEXT.md`'s D-01..D-20 decisions). All 20 decisions (D-01 through D-20) are traced to at least one plan's `requirements:` frontmatter and to code:

| Decision | Covered by | Status |
|---|---|---|
| D-01 (one file/campaign) | 24-02, 24-04 | SATISFIED |
| D-02 (DB-blob storage) | 24-02 | SATISFIED |
| D-03 (50MB limit) | 24-04 | SATISFIED |
| D-04 (physical model + drift guard) | 24-02 | SATISFIED |
| D-05 (one media message, caption=opener) | 24-05, 24-06 | SATISFIED |
| D-06 (auto-media, force_document=False) | 24-03, 24-06 (mechanism); 24-07 (real-device confirmation) | **PARTIAL — mechanism SATISFIED, photo-specific device confirmation NOT DONE** |
| D-07 (overflow >1024) | 24-03 | SATISFIED |
| D-08 (blob→temp source) | 24-03, 24-06 | SATISFIED |
| D-09 (zero-width + jitter combo) | 24-01; 24-07 (device confirmation) | SATISFIED |
| D-10 (near-invisible invariant) | 24-01 | SATISFIED |
| D-11 (accepted risk, defense-in-depth) | 24-01 | SATISFIED (documented) |
| D-12 (scope = campaign opener only) | 24-06 | SATISFIED |
| D-13 (per-campaign flag, default ON) | 24-02, 24-04 | SATISFIED |
| D-14 (apply at send time, local copy only) | 24-05, 24-06 | SATISFIED — confirmed live in prod |
| D-15 (fixed density/green corridor) | 24-01 | SATISFIED |
| D-16 (fresh per send) | 24-01, 24-06 | SATISFIED |
| D-17 (file-opener queue row) | 24-05 | SATISFIED |
| D-18 (counts as one send/dialog) | 24-05 | SATISFIED |
| D-19 (upload endpoint + handoff) | 24-04, 24-07 | SATISFIED |
| D-20 (duplicate copies both) | 24-04 | SATISFIED |

No orphaned requirements found (there is no central REQUIREMENTS.md entry set for this phase to check against beyond the D-01..D-20 cluster, all of which trace to code).

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `app/services/failover.py` | 65, 75, 135 | Hardcoded `item_type = 'message'` predicate (pre-existing, from the earlier Failover phase, requirement FAIL-03) was never extended to include the new `item_type = 'file'` value Phase 24 introduces | ⚠️ WARNING (not a stated must_have of any Phase-24 plan — discovered during verification, not scored against the phase) | If a sender holding pending campaign file-opener rows (`item_type='file'`) gets restricted/frozen mid-campaign, `failover_cold_backlog` will silently skip those rows (its discovery `WHERE item_type = 'message'` excludes them) — they will NOT be auto-moved to a healthy sender the way text-opener rows are. They will sit paused until the existing reconcile loop resumes the frozen sender, i.e. attachment campaigns are more fragile under sender restriction than text campaigns, with no error/log surfaced. `rebalance_on_attach` (rebalance.py) is unaffected — its predicate has no `item_type` filter. |
| `app/services/queue.py:1108` | — | Pre-existing `TODO: add external alert...` | INFO | Predates Phase 24 (git blame: 2026-04-02), not introduced by this phase |
| `app/routers/campaigns.py:126,164,182,203,622` | — | Pre-existing `TODO(v2-rls)` markers | INFO | Predates Phase 24 (git blame: 2026-05-22), not introduced by this phase |

No `FIXME`/`XXX`/unreferenced-`TBD` markers introduced by this phase's commits.

### Human Verification Required

1. **Re-run the D-06 live smoke with a real photo.**
   **Test:** Attach a real `.jpg`/`.png` (not a PDF) to a test campaign, `variation_enabled` ON, one warmed sender, one controlled test contact. Start the campaign, let the worker send on normal timing.
   **Expected:** Recipient's Telegram Desktop AND mobile show an inline photo bubble (not a document icon) with the opener as caption; `messages.message_type='photo'` in DB for that row.
   **Why human:** Client-side rendering can't be asserted from mocks or DB state; the one live smoke actually run used a PDF, which is always a document in Telegram regardless of the code path, so it never exercised the photo branch on a real client.

2. **Decide whether to schedule/fast-track Phase 23 (or a scoped hot-fix) to close the inbox-media-rendering gap**, given it directly touches the credibility of Phase 24's own "renders as a media bubble" claim (24-06 SUMMARY line 72) even though it's deferred out of Phase 24's roadmap-level scope. Not blocking phase-24 sign-off per the user's explicit "not now," but flagged so it doesn't get lost — the existing Phase 23 plans (23-01/23-03/23-04) already scope the exact fix (widened `GET /messages` SELECT + `MessageResponse` schema + frontend rendering).

3. **(Optional, lower priority) Consider whether the failover-cold-backlog `item_type` gap (Anti-Patterns table) needs a follow-up.** It's a one-line predicate change (`item_type IN ('message','file')` in `failover.py` at the three call sites) but affects operational resilience of attachment campaigns specifically, and was not part of any Phase 24 plan's scope.

### Gaps Summary

No BLOCKER-level gaps found against Phase 24's own must-haves. All backend/data-model/delivery work (24-01 through 24-06's write path, 24-07's contract publication) is implemented, unit-tested (74/74 targeted tests GREEN), and independently confirmed against **live production data** (a real attachment was uploaded, delivered as a Telegram document with a clean caption, and the DB stayed byte-clean under an active variation flag — exactly matching D-05/D-06/D-08/D-12/D-14).

Two items remain open, both correctly identified and disclosed by the executor rather than hidden:
- **D-06 photo-specific claim** — genuine phase-24 gap, not deferrable, routed to human verification (re-test with a real image).
- **Inbox media-rendering ("renders as a media bubble")** — a real gap in outcome, but out of Phase 24's ROADMAP-level scope; the fix belongs to the still-unexecuted Phase 23, which already plans the exact surface needed. Recorded as `deferred`, not a phase-24 blocker, per explicit user instruction not to fix now.

One additional finding surfaced during adversarial verification that was in neither SUMMARY nor any PLAN's must_haves: the pre-existing sender-failover mechanism (`failover.py`) was not extended to cover the new `item_type='file'` queue rows, so attachment-campaign backlog on a restricted sender won't auto-recover the way text-campaign backlog does. Flagged as a WARNING, not scored against the phase's own contract, but worth a follow-up.

---

_Verified: 2026-07-08T07:57:53Z_
_Verifier: Claude (gsd-verifier)_
