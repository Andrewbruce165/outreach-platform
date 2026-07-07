---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 05
type: execute
wave: 2
depends_on: ["24-02"]
files_modified:
  - app/services/campaign_enqueue.py
  - tests/test_campaign_enqueue_worker.py
  - tests/test_rerender_pending_queue.py
autonomous: true
requirements: [D-05, D-17, D-18]
must_haves:
  truths:
    - "D-05/D-18: when a campaign has a campaign_attachments row, the enqueue worker inserts ONE message_queue row per contact with item_type='file', caption=<rendered opener>, message_text=<rendered opener> (mirror for inbox/log readability); still exactly one row per contact = one rate-limit tick / one new-dialog cap (limits unchanged)"
    - "A campaign WITHOUT an attachment still enqueues item_type='message' rows exactly as today (no behavior change)"
    - "Attachment presence is resolved ONCE per campaign per tick (single SELECT 1 FROM campaign_attachments WHERE campaign_id=), not per contact"
    - "D-17: rerender_pending_queue re-renders the caption (and mirrored message_text) of pending item_type='file' rows of the campaign when message_template changes — so editing the opener reaches already-queued file rows; the per-row WHERE id=:id AND status='pending' re-check is preserved (no clobber of in-flight)"
    - "Variation is NOT applied here — the enqueue snapshot stays clean text; variation happens at send time in the worker (D-14, Plan 24-06)"
  artifacts:
    - path: "app/services/campaign_enqueue.py"
      provides: "per-campaign attachment presence + conditional item_type='file'/caption INSERT; rerender extended to file rows"
      contains: "item_type"
    - path: "tests/test_campaign_enqueue_worker.py"
      provides: "test: campaign with attachment enqueues one item_type='file' row with caption per contact"
      min_lines: 20
    - path: "tests/test_rerender_pending_queue.py"
      provides: "test: template edit re-renders caption of pending file rows"
      min_lines: 20
  key_links:
    - from: "campaign_enqueue INSERT"
      to: "message_queue (item_type, caption)"
      via: "conditional 'file' vs 'message' + caption param"
      pattern: "item_type"
    - from: "rerender_pending_queue"
      to: "pending item_type='file' rows"
      via: "item_type IN ('message','file') + caption update"
      pattern: "item_type IN"
---

<objective>
Make the enqueue worker emit a file-opener queue row when a campaign has an attachment (D-05), counting as one send/one new-dialog (D-18), and extend rerender so template edits reach pending file rows' captions (D-17). All while keeping the enqueue snapshot CLEAN (variation is a send-time concern, D-14).

Purpose: bridge the attachment data model (24-02) to the send path (24-06).
Output: campaign_enqueue.py INSERT + rerender extension + tests.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-CONTEXT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-RESEARCH.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-02-data-model-migration-schemas-PLAN.md

<interfaces>
<!-- Current INSERT at campaign_enqueue.py:387-410 (always 'message'). rerender at :425-514.
     message_queue columns present: item_type, caption, message_text, file_url, file_name. -->
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: enqueue emits item_type='file' + caption when campaign has attachment</name>
  <read_first>
    - app/services/campaign_enqueue.py:340-422 (the per-campaign tick loop, render_template call at 371, the raw INSERT ... SELECT ... WHERE EXISTS at 387-410, the enqueued rowcount counter)
    - app/models/__init__.py MessageQueue (item_type/caption/message_text columns) + QueueItemType enum (models:25)
    - tests/test_campaign_enqueue_worker.py (existing enqueue-worker test setup: how a running campaign + folder + contacts + attached sender are seeded)
  </read_first>
  <behavior>
    - Given a running campaign with a campaign_attachments row + a folder of 2 contacts + an attached sender: after a tick, message_queue has 2 rows, each item_type='file', caption == rendered opener, message_text == rendered opener (mirror), status='pending'. Exactly one row per contact.
    - Given a running campaign WITHOUT an attachment: rows are item_type='message', caption NULL (unchanged).
    - The attachment-presence SELECT runs once per campaign (assert via the seeded data — 2 contacts still produce the correct item_type without N extra queries; a light assertion that both rows share item_type='file' suffices).
  </behavior>
  <action>
    In app/services/campaign_enqueue.py, in the per-campaign block (before the contacts loop that starts ~line 340), compute attachment presence ONCE:
    ```python
    has_attachment = (await db.execute(
        text("SELECT 1 FROM campaign_attachments WHERE campaign_id = :cid"),
        {"cid": str(c.id)},
    )).first() is not None
    item_type = "file" if has_attachment else "message"
    ```
    Then modify the INSERT (387-410) to include `caption` and parametrize `item_type` + `caption`:
    ```sql
    INSERT INTO message_queue
        (workspace_id, campaign_id, sender_id, item_type, status,
         recipient_phone, recipient_name, message_text, caption,
         priority, scheduled_at, created_at)
    SELECT :wid, :cid, :sid, :item_type, 'pending',
           :phone, :name, :text, :caption, :priority, :scheduled, NOW()
    WHERE EXISTS (SELECT 1 FROM campaigns WHERE id = :cid AND status = 'running')
    ```
    Bind `"item_type": item_type` and `"caption": rendered if has_attachment else None`. Keep `message_text = :text` = `rendered` in BOTH cases (D-05: message_text mirrors clean text so inbox/log stay readable; caption is the source of truth for the file caption). Keep the rowcount==1 enqueued++ guard and the per-contact savepoint unchanged.
    Do NOT set file_url (blob lives in campaign_attachments, worker loads by campaign_id — RESEARCH §4).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_enqueue_worker.py -k "file or attachment" -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n ':item_type' app/services/campaign_enqueue.py` matches AND `grep -n 'caption' app/services/campaign_enqueue.py` matches in the INSERT
    - `grep -n 'SELECT 1 FROM campaign_attachments' app/services/campaign_enqueue.py` matches (presence check)
    - test: campaign-with-attachment → all queue rows item_type='file' with caption==message_text==rendered; campaign-without → item_type='message', caption NULL
  </acceptance_criteria>
  <done>Enqueue emits one file-opener row per contact with caption when an attachment exists, else unchanged message rows.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: extend rerender_pending_queue to re-render file-row captions (D-17)</name>
  <read_first>
    - app/services/campaign_enqueue.py:425-514 (rerender_pending_queue: SELECT filters item_type='message' at 459; UPDATE sets message_text only at 507-512; per-row status='pending' re-check)
    - app/routers/campaigns.py:696-717 (patch_campaign calls rerender on template change) + :775-787 (/rerender-pending endpoint) — both must keep working
    - tests/test_rerender_pending_queue.py (existing rerender test: how pending message rows + a template edit are asserted)
  </read_first>
  <behavior>
    - A campaign with pending item_type='file' rows: editing message_template then calling rerender_pending_queue re-renders each file row's caption AND message_text to the new template output.
    - Existing item_type='message' rows still re-render message_text (caption stays NULL for them).
    - A row a worker already flipped away from 'pending' is skipped (WHERE id=:id AND status='pending' preserved).
  </behavior>
  <action>
    In rerender_pending_queue: change the pending SELECT (455-462) to also fetch `item_type` and widen the filter to `item_type IN ('message','file')`. In the UPDATE (507-512), set message_text always and caption ONLY for file rows via a CASE so message rows keep caption NULL:
    ```sql
    UPDATE message_queue
    SET message_text = :txt,
        caption = CASE WHEN item_type = 'file' THEN :txt ELSE caption END
    WHERE id = :id AND status = 'pending'
    ```
    Keep the empty-template no-op guard, the folder contact map, the fallback contact, the per-row status re-check, and the "does NOT commit — caller owns the transaction" contract unchanged.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_rerender_pending_queue.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "item_type IN ('message','file')" app/services/campaign_enqueue.py` matches (widened SELECT)
    - `grep -n "CASE WHEN item_type = 'file'" app/services/campaign_enqueue.py` matches (caption update)
    - test: template edit propagates to pending file-row caption AND message_text; message rows keep caption NULL; in-flight (non-pending) row untouched
  </acceptance_criteria>
  <done>rerender_pending_queue re-renders captions of pending file rows on template edit, preserving in-flight safety (D-17).</done>
</task>

</tasks>

<verification>
- `pytest tests/test_campaign_enqueue_worker.py tests/test_rerender_pending_queue.py -x` GREEN.
- Empirical queue intervals / rate-limit constants untouched (CLAUDE.md guard) — this plan only changes the INSERT columns and the rerender filter.
</verification>

<success_criteria>
Campaigns with an attachment enqueue exactly one file-opener row per contact (caption=opener, one send, D-05/D-18); template edits reach pending file-row captions (D-17); the enqueue snapshot stays clean text (variation deferred to send, D-14).
</success_criteria>

<output>
After completion, create `.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-05-SUMMARY.md`.
</output>
