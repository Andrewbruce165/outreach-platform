---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 06
type: execute
wave: 2
depends_on: ["24-01", "24-02", "24-03"]
files_modified:
  - app/services/queue.py
  - tests/test_queue_variation.py
  - tests/test_queue_file_opener.py
autonomous: true
requirements: [D-05, D-06, D-08, D-12, D-14, D-16]
must_haves:
  truths:
    - "D-14: variation is applied to a LOCAL COPY of the opener text/caption RIGHT BEFORE the Telethon call; message_queue.message_text/caption and the messages log row are NEVER mutated (DB stays clean, inbox/logs readable, rerender untouched)"
    - "D-12 gate: variation applies ONLY when item.campaign_id is not None AND extra_data.kind != 'followup' AND the campaign's variation_enabled is true — read at send time (JOIN/SELECT on campaigns) so toggling the flag reaches already-pending rows; follow-up pings and non-campaign sends are never varied"
    - "D-16: vary() is called fresh per send (per-item random) so two sends of the same opener differ in bytes"
    - "D-05/D-06/D-08: a campaign file-opener (item_type='file', campaign_id set, file_url NULL) loads the blob from campaign_attachments by campaign_id and calls send_file with file_bytes=<blob>, file_name=<attachment name>, caption=<varied opener>, force_document=False (auto-media)"
    - "The text/message opener branch sends the varied copy via send_message(message=<varied text>); the messages_log write at queue.py:921-930 still reads the untouched item.message_text"
    - "Inbox fidelity (bridges Phase 23 mig 053): a campaign file-opener's messages inbox row carries the concrete message_type (photo|video|document, derived from the attachment extension the same way send_file force_document=False auto-classifies) plus file_name/mime_type/size_bytes — so the inbox renders a media bubble, not a plain-text line. Text openers keep message_type='text' (the DB DEFAULT); the clean caption/text still lands in message_text (D-14)"
  artifacts:
    - path: "app/services/queue.py"
      provides: "variation_enabled read at send time; vary() on local copy; blob load + send_file(file_bytes, force_document=False) for campaign file openers; messages INSERT carries message_type + media metadata for file openers"
      contains: "from app.services.variation import vary"
    - path: "tests/test_queue_variation.py"
      provides: "variation gate + clean-DB invariant tests (VAR-SCOPE/FLAG)"
      min_lines: 60
    - path: "tests/test_queue_file_opener.py"
      provides: "blob->send_file(force_document=False) + varied caption + overflow + messages-row message_type/media tests (ATT-DELIVER/OVERFLOW/INBOX-MEDIA)"
      min_lines: 60
  key_links:
    - from: "app/services/queue.py worker"
      to: "app/services/variation.py::vary"
      via: "text_to_send = vary(item.message_text) on a local copy when apply_var"
      pattern: "vary\\(item"
    - from: "app/services/queue.py worker (file branch)"
      to: "campaign_attachments + telegram.send_file"
      via: "SELECT file_data by campaign_id -> send_file(file_bytes=..., force_document=False)"
      pattern: "force_document=False"
    - from: "app/services/queue.py::_upsert_conversation messages INSERT (~1592)"
      to: "messages.message_type / file_name / mime_type / size_bytes (Phase 23 mig 053)"
      via: "file-opener media metadata carried on result['media'] into the INSERT column list"
      pattern: "message_type"
---

<objective>
The convergence point: at send time in the queue worker, (1) apply the invisible variation to a LOCAL COPY of the opener text/caption without ever touching the DB (D-12/D-14/D-16), (2) deliver a campaign file-opener by loading its blob from campaign_attachments and sending it as auto-media (D-05/D-06/D-08), and (3) persist the file-opener's inbox `messages` row with the concrete message_type + media metadata so it renders as a media bubble (bridges Phase 23 migration 053).

Purpose: wire the variation module (24-01), the data model (24-02) and the extended send_file (24-03) together at the exact send moment — and close the fidelity gap with Phase 23's `messages` media columns.
Output: queue.py send-branch + _upsert_conversation changes + integration tests (Telethon mocked, real DB).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-CONTEXT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-RESEARCH.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-01-variation-pure-module-PLAN.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-03-send-file-blob-source-automedia-PLAN.md

<interfaces>
<!-- Worker send branch at queue.py:877-901. Follow-up marker already computed at 819-822.
     Per-item campaign SELECT already exists at 751-758 (allow_recontact, recontact_min_age_days).
     send_file (from 24-03): send_file(..., file_bytes=?, file_name=?, caption=?, force_document=?)
     Messages inbox INSERT lives in _upsert_conversation at queue.py:1592-1603 (raw SQL, NO ORM model):
         INSERT INTO messages (conversation_id, direction, message_text, sent_by, telegram_message_id)
         VALUES (:cid, 'outbound', :txt, 'ai', :mid) ON CONFLICT ... DO NOTHING
     Phase 23 migration 053 (Phase 24 depends_on Phase 23 per ROADMAP → columns guaranteed present) added:
         message_type VARCHAR(20) NOT NULL DEFAULT 'text' CHECK (message_type IN ('text','photo','video','voice','document')),
         file_name VARCHAR(255), mime_type VARCHAR(255), size_bytes BIGINT   -- all nullable
     Attachments only ever classify to photo|video|document (never 'voice'); all within the CHECK set. -->
```python
from app.services.variation import vary   # add to queue.py imports
import os                                  # for os.path.splitext extension classification (add if absent)
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: read variation_enabled at send time + apply vary() to a local copy (gate)</name>
  <read_first>
    - app/services/queue.py:751-758 (existing per-item campaign SELECT — extend to also select variation_enabled)
    - app/services/queue.py:819-822 (is_followup marker — reuse verbatim)
    - app/services/queue.py:877-901 (client acquisition + send branch: file vs message)
    - app/services/queue.py:918-931 (messages_log write reading item.message_text — MUST stay reading the untouched value)
    - app/services/variation.py (vary contract from 24-01)
    - tests/test_send_campaign.py + tests/test_queue_new_dialog_limit.py (how the suite seeds a message_queue row + mocks telegram_service.send_message/send_file with AsyncMock to capture the sent text)
  </read_first>
  <behavior>
    - Campaign message opener with variation_enabled=true: send_message is called with a message whose strip_invisible == item.message_text but != item.message_text (varied), AND the DB message_queue.message_text and the messages_log row equal the clean original (no invisible chars).
    - variation_enabled=false: send_message called with exactly item.message_text (clean).
    - kind='followup': never varied even if campaign variation_enabled=true (send_message gets clean text).
    - Non-campaign item (campaign_id NULL): never varied.
    - Two consecutive sends of the same opener (variation on) produce different sent bytes (D-16).
  </behavior>
  <action>
    In app/services/queue.py:
    1. Add `from app.services.variation import vary` at the import block top.
    2. Extend the existing camp_row SELECT at 752-755 to also select `variation_enabled`:
       `SELECT allow_recontact, recontact_min_age_days, variation_enabled FROM campaigns WHERE id = :cid`.
       Add a local `variation_enabled = False` default before the `if item.campaign_id is not None` block; inside `if camp_row is not None:` set `variation_enabled = bool(camp_row.variation_enabled)`.
    3. Just before the send branch (~877), compute the gate (reuse is_followup from 819):
       ```python
       apply_var = (item.campaign_id is not None) and (not is_followup) and variation_enabled
       text_to_send = vary(item.message_text) if (apply_var and item.message_text) else item.message_text
       caption_to_send = vary(item.caption) if (apply_var and item.caption) else item.caption
       ```
       These are LOCAL variables — never write them back to item / DB.
    4. In the message branch (893-901) pass `message=text_to_send` (was item.message_text).
    5. Leave the messages_log write (921-930) reading `item.message_text` (untouched) — DB stays clean (D-14, Pitfall 3).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_variation.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n 'from app.services.variation import vary' app/services/queue.py` matches
    - `grep -n 'variation_enabled' app/services/queue.py` matches in the campaign SELECT
    - `grep -n 'vary(item.message_text)' app/services/queue.py` AND `grep -n 'vary(item.caption)' app/services/queue.py` match
    - tests: sent text is varied (strip==clean, bytes differ) with flag on; clean with flag off / followup / non-campaign; DB message_text + messages_log row stay clean
  </acceptance_criteria>
  <done>Variation applied to a local copy only, strictly gated (campaign + not-followup + flag), fresh per send; DB never mutated.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: campaign file-opener blob load + send_file(file_bytes, force_document=False) + media-typed inbox row</name>
  <read_first>
    - app/services/queue.py:881-891 (file branch: current send_file(file_url=item.file_url, ...))
    - app/services/queue.py:1590-1603 (_upsert_conversation messages INSERT — the raw-SQL row this task enriches for file openers)
    - app/services/telegram.py send_file (extended by 24-03: file_bytes + force_document params) + telegram.py:959 (`os.path.splitext(file_name)[1]` — the extension→auto-media seam to mirror)
    - app/models/__init__.py CampaignAttachment (from 24-02 — content_type + size_bytes columns available)
    - .planning/phases/23-.../23-01-schema-migration-and-red-scaffold-PLAN.md (Phase 23 mig 053: messages.message_type CHECK IN text|photo|video|voice|document + file_name/mime_type/size_bytes)
    - tests/test_send_file_blob.py (24-03 mock idioms for client.send_file)
  </read_first>
  <behavior>
    - item_type='file' with campaign_id set and file_url NULL: worker SELECTs file_data + file_name + content_type + size_bytes from campaign_attachments by campaign_id and calls send_file(file_bytes=<blob>, file_name=<name>, caption=caption_to_send, force_document=False, file_url=None). The mocked client.send_file receives force_document=False and a temp path ending in the attachment's extension.
    - The caption passed is the VARIED caption (when the gate is on) — so the file opener is byte-unique too (D-12 includes caption + overflow).
    - Inbox fidelity: the messages row written by _upsert_conversation for that file opener carries message_type derived from the attachment extension (image ext → 'photo', video ext → 'video', else 'document' — mirrors send_file force_document=False auto-media; NEVER 'voice' for attachments) plus file_name/mime_type/size_bytes; message_text stays the clean (unvaried) caption or a "[file: …]" placeholder if no caption. A NON-file (text) opener still writes message_type='text' (the DB DEFAULT) — its INSERT is byte-identical to today.
    - Defensive fallback: if item_type='file' but NO attachment row (legacy/edge), fall back to the existing file_url path (unchanged) so nothing crashes; that path writes the current plain-text messages row (message_type defaults to 'text').
    - Overflow (>1024 varied caption) still routes through send_file's existing overflow branch (from 24-03) → file w/o caption + follow-up text.
  </behavior>
  <action>
    In app/services/queue.py file branch (881-891):
    1. When `item.item_type == QueueItemType.file`: if `item.campaign_id is not None and not item.file_url`, load the blob + metadata:
       ```python
       att = (await db.execute(text(
           "SELECT file_data, file_name, content_type, size_bytes "
           "FROM campaign_attachments WHERE campaign_id = :cid"),
           {"cid": str(item.campaign_id)})).first()
       ```
       If `att is not None`: call
       ```python
       result = await telegram_service.send_file(
           client=client, phone=item.recipient_phone, recipient_name=item.recipient_name,
           file_bytes=att.file_data, file_name=att.file_name,
           caption=caption_to_send, force_document=False,
           sender_id=str(sender.id), workspace_id=str(item.workspace_id))
       ```
       On success, classify the message_type from the extension (mirror telegram.py:959 auto-media) and stash media metadata onto `result` so _upsert_conversation can enrich the inbox row (do NOT touch queue/rate-limit logic):
       ```python
       _ext = os.path.splitext(att.file_name or "")[1].lower()
       if _ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
           _mtype = "photo"
       elif _ext in (".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"):
           _mtype = "video"
       else:
           _mtype = "document"
       if isinstance(result, dict) and result.get("success"):
           result["media"] = {
               "message_type": _mtype,
               "file_name": att.file_name,
               "mime_type": att.content_type,
               "size_bytes": att.size_bytes,
           }
       ```
       else (defensive, no attachment row) fall through to the existing URL-based call with `caption=caption_to_send`.
    2. If `item.file_url` is set (legacy path), keep the existing send_file(file_url=..., caption=caption_to_send) call — but pass caption_to_send (varied) instead of item.caption. (Legacy path leaves the messages row as today — message_type defaults to 'text'.)
    3. In `_upsert_conversation` (queue.py:1592-1603), read the media metadata off `result` and choose the INSERT column list BEFORE execution (no in-transaction fallback — Phase 24 depends_on Phase 23 so mig 053's messages columns are guaranteed; the choice is purely presence-of-media):
       ```python
       media = result.get("media") if isinstance(result, dict) else None
       if media:
           await db.execute(text("""
               INSERT INTO messages
                   (conversation_id, direction, message_text, sent_by, telegram_message_id,
                    message_type, file_name, mime_type, size_bytes)
               VALUES (:cid, 'outbound', :txt, 'ai', :mid,
                       :mtype, :fname, :mime, :size)
               ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
           """), {"cid": conversation_id, "txt": item.message_text or item.caption,
                  "mid": int(message_id), "mtype": media["message_type"],
                  "fname": media["file_name"], "mime": media["mime_type"],
                  "size": media["size_bytes"]})
       else:
           # UNCHANGED existing plain-text INSERT (message_type falls back to DB DEFAULT 'text')
           await db.execute(text("""
               INSERT INTO messages (conversation_id, direction, message_text, sent_by, telegram_message_id)
               VALUES (:cid, 'outbound', :txt, 'ai', :mid)
               ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
           """), {"cid": conversation_id,
                  "txt": item.message_text or f"[file: {item.file_url}]",
                  "mid": int(message_id)})
       ```
       Keep this inside the existing try/except; message_text passed to the media INSERT is the CLEAN caption (unvaried) — D-14. Keep queue intervals / rate-limit logic untouched (CLAUDE.md guard).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_file_opener.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n 'SELECT file_data, file_name, content_type, size_bytes FROM campaign_attachments' app/services/queue.py` matches
    - `grep -n 'force_document=False' app/services/queue.py` matches AND `grep -n 'file_bytes=att.file_data' app/services/queue.py` matches
    - `grep -n 'caption=caption_to_send' app/services/queue.py` matches (varied caption passed)
    - `grep -n 'result\["media"\]' app/services/queue.py` matches AND the media INSERT lists `message_type, file_name, mime_type, size_bytes`
    - tests: file opener → send_file called with force_document=False + blob + varied caption; the resulting messages row has message_type in ('photo','video','document') matching the attachment extension + non-null file_name/mime_type/size_bytes; a text opener's messages row has message_type='text'; overflow → 2 calls; missing-attachment → URL fallback (no crash, plain-text row)
  </acceptance_criteria>
  <done>Campaign file openers deliver the blob as auto-media with the varied caption, and their inbox messages row carries the concrete message_type + media metadata (renders as a media bubble); overflow reused; URL fallback + text-opener rows unchanged.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_queue_variation.py tests/test_queue_file_opener.py -x` GREEN.
- `pytest tests/test_send_campaign.py tests/test_queue_new_dialog_limit.py -q` still GREEN (message-opener path + caps unchanged; text-opener messages row still message_type='text').
- `grep -P '[\x{200b}\x{200c}\x{200d}\x{2060}]' app/services/queue.py` returns nothing (no invisible glyphs in the worker — variation lives in the module).
</verification>

<success_criteria>
At send time the worker varies a local copy of the opener text AND caption (gated on campaign + not-followup + flag, fresh per send) while leaving the DB and messages log clean, delivers campaign file-openers as blob-sourced auto-media with force_document=False, and persists the file-opener inbox row with the concrete message_type + file_name/mime_type/size_bytes so it renders as a media bubble (D-05/D-06/D-08/D-12/D-14/D-16 + Phase 23 mig 053 fidelity). Queue intervals/caps untouched.
</success_criteria>

<output>
After completion, create `.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-06-SUMMARY.md`.
</output>
</content>
