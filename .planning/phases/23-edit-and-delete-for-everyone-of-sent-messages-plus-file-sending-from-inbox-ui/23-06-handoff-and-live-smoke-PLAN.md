---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 06
type: execute
wave: 4
depends_on: ["23-03", "23-05"]
files_modified:
  - lovable-handoff/openapi.json
  - lovable-handoff/error-codes.md
autonomous: false
requirements: [INBM-09]
must_haves:
  truths:
    - "lovable-handoff/openapi.json reflects the four new endpoints (PATCH/DELETE messages, POST send-file, GET messages/{id}/file) + the extended MessageResponse + EditMessageRequest/SendFileFromUIResponse schemas"
    - "lovable-handoff/error-codes.md documents every D-17 code with an HTTP status + UI string"
    - "openapi.json is regenerated from the running backend via export-handoff (no hand-editing of paths/schemas); info.title is still the Outreach Platform"
    - "A human confirms live-smoke: edit, delete-for-everyone, send photo (arrives as photo) + long-caption overflow + a document, and download an incoming file"
  artifacts:
    - path: "lovable-handoff/openapi.json"
      provides: "regenerated spec including the 4 Phase-23 endpoints + schemas"
      contains: "send-file"
    - path: "lovable-handoff/error-codes.md"
      provides: "D-17 error codes → HTTP status → UI strings"
      contains: "MESSAGE_EDIT_TOO_OLD"
  key_links:
    - from: "lovable-handoff/openapi.json"
      to: "app/routers/conversations.py routes"
      via: "scripts/export-handoff.sh (rebuild api first, then app.openapi() export)"
      pattern: "send-file"
---

<objective>
Regenerate the Lovable frontend handoff so the sibling repo (`AGS-Venture-Lab/aimly-tg-outreach`)
can regenerate the UI for the four new inbox capabilities: update
`lovable-handoff/openapi.json` (new endpoints + schemas, via export-handoff) and
`lovable-handoff/error-codes.md` (the D-17 code registry). Then a human live-smokes all four
operations against a real Telegram account.

Purpose: This repo's Phase-23 deliverable to the frontend is backend + handoff (D-22) — there
is no UI code in this repo. openapi.json MUST be regenerated from the running backend (not
hand-edited). Live-smoke is the only proof that the Telethon calls the tests mock actually
behave (edit-window is server-controlled; send-media auto-detection; revoke both-sides).
Output: regenerated openapi.json + updated error-codes.md + human sign-off.
Addresses: INBM-09 (D-22).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-CONTEXT.md
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-RESEARCH.md
@lovable-handoff/error-codes.md
@scripts/export-handoff.sh

<interfaces>
<!-- export-handoff.sh: boots `docker compose up -d db api`, waits for /openapi.json inside the
     api container, exports via app.openapi() -> jq -> lovable-handoff/openapi.json, regenerates
     types/api.ts, and sanity-checks info.title == Outreach Platform. Run it AFTER rebuilding api
     so the new routes are live:
       docker compose up -d --build api
       bash scripts/export-handoff.sh
-->

<!-- error-codes.md existing table shape (lovable-handoff/error-codes.md) -->
<!-- | Backend code | HTTP | UI string | Notes | -->
<!-- | CONVERSATION_NOT_FOUND | 404 | "Conversation not found" | silent isolation | -->

<!-- D-17 codes to document (from 23-CONTEXT.md) -->
<!-- new: MESSAGE_EDIT_TOO_OLD, MESSAGE_NOT_EDITABLE, MESSAGE_NOT_FOUND, DELETE_FAILED,
        FILE_TOO_LARGE, MEDIA_UNAVAILABLE -->
<!-- reused (already documented): NO_TELEGRAM_ID, RECIPIENT_NOT_IN_TELEGRAM, FLOOD_WAIT,
        ACCOUNT_FROZEN, USER_IS_BLOCKED -->
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Regenerate openapi.json + document D-17 error codes</name>
  <read_first>
    - scripts/export-handoff.sh (the canonical regen flow + info.title sanity check)
    - lovable-handoff/error-codes.md (existing table format + advisory-warning section to mirror)
    - .planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-CONTEXT.md (D-17 full code list; D-22 alias tolerance)
  </read_first>
  <action>
    (a) Rebuild the api container so the four new routes + schemas are live, then regenerate:
    run `docker compose up -d --build api` then `bash scripts/export-handoff.sh`.
    Confirm `lovable-handoff/openapi.json` now contains the four new paths
    (`/api/v1/conversations/{conversation_id}/messages/{message_id}` with PATCH + DELETE,
    `/api/v1/conversations/{conversation_id}/send-file` POST,
    `/api/v1/conversations/{conversation_id}/messages/{message_id}/file` GET) and that
    `MessageResponse` carries `message_type`/`file_name`/`mime_type`/`size_bytes`/`edited_at`
    and the `EditMessageRequest`/`SendFileFromUIResponse` schemas are present. Do NOT hand-edit
    paths/schemas — if something is missing, fix the backend and re-run export-handoff. If the
    environment cannot run docker/export-handoff, STOP and surface it to the orchestrator — do
    NOT hand-author the spec.

    (b) Update `lovable-handoff/error-codes.md`: add rows for every D-17 code with the HTTP
    status matching `_raise_inbox_message_error` (plan 23-03) and a UI string:
      - `MESSAGE_EDIT_TOO_OLD` | 409 | "This message is too old to edit."
      - `MESSAGE_NOT_EDITABLE` | 422 | "This message can't be edited."
      - `MESSAGE_NOT_FOUND` | 404 | "Message not found" (cross-workspace/inbound also returns this — silent isolation)
      - `DELETE_FAILED` | 502 | "Couldn't delete the message. Try again."
      - `FILE_TOO_LARGE` | 413 | "File is larger than 50 MB."
      - `MEDIA_UNAVAILABLE` | 410 | "This file is no longer available in Telegram."
      - `DOWNLOAD_FAILED` | 502 | "Couldn't download the file. Try again."
      - `TELEGRAM_OP_FAILED` | 502 | "Telegram operation failed. Try again." (generic fallback from _raise_inbox_message_error)
    Note that the reused codes already in the table (`NO_TELEGRAM_ID`, `RECIPIENT_NOT_IN_TELEGRAM`,
    `FLOOD_WAIT`, `ACCOUNT_FROZEN`, `USER_IS_BLOCKED`) apply to the new endpoints too, and add a
    one-line note that multipart send-file + EditMessageRequest tolerate Lovable field aliases
    (`message`/`message_text`/`text`) per D-22.
  </action>
  <verify>
    <automated>python3 -c "import json; s=json.load(open('lovable-handoff/openapi.json')); p=s['paths']; assert any('send-file' in k for k in p); assert any(k.endswith('/messages/{message_id}/file') for k in p); assert any(k.endswith('/messages/{message_id}') and 'patch' in p[k] and 'delete' in p[k] for k in p); assert s['info']['title'].strip(); print('openapi ok')"</automated>
  </verify>
  <acceptance_criteria>
    - `lovable-handoff/openapi.json` `paths` includes a `send-file` POST path, a `.../messages/{message_id}/file` GET path, and a `.../messages/{message_id}` path with both `patch` and `delete`.
    - `openapi.json` `components.schemas` includes `EditMessageRequest` and `SendFileFromUIResponse`; `MessageResponse` includes `message_type` and `edited_at`.
    - `lovable-handoff/error-codes.md` contains `MESSAGE_EDIT_TOO_OLD`, `MESSAGE_NOT_EDITABLE`, `MESSAGE_NOT_FOUND`, `DELETE_FAILED`, `FILE_TOO_LARGE`, `MEDIA_UNAVAILABLE`, `DOWNLOAD_FAILED`, `TELEGRAM_OP_FAILED`.
    - `info.title` unchanged (Outreach Platform, not a neighbouring FastAPI). Verify command prints `openapi ok`.
  </acceptance_criteria>
  <done>openapi.json regenerated (4 endpoints + schemas) via export-handoff, not hand-edited; error-codes.md documents all D-17 codes.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 2: Live-smoke all four inbox operations against a real Telegram account</name>
  <read_first>
    - .planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-CONTEXT.md (D-01..D-16 — the exact behaviours being confirmed)
    - CLAUDE.md (§Git & Deploy — rebuild api AND listener)
  </read_first>
  <action>
    Deploy the phase, then walk a human through the live-smoke below. Deploy:
    `cd /root/apps/aimly/tg-outreach && git pull && docker compose up -d --build api listener`
    (BOTH api and listener — the listener was touched in 23-04). Present the how-to-verify
    steps, collect pass/fail per step, and record any server-controlled deviation (e.g. the
    actual edit-time window) in the SUMMARY. This is a human-only checkpoint — the Telethon
    behaviour behind the mocks cannot be auto-verified.
  </action>
  <what-built>
    Four inbox capabilities are live on the backend and reflected in the handoff spec:
    edit a sent text, delete-for-everyone (revoke), send a file (auto-media), and download an
    incoming file. All Telethon calls were unit/integration-tested against mocks; this
    checkpoint proves the real MTProto behaviour the mocks stand in for.
  </what-built>
  <how-to-verify>
    From an active workspace sender with a live conversation:
    1. EDIT: send a text message, then edit it via the inbox → confirm the text changes in
       the real Telegram chat and the message shows "(изменено)". Then edit a very old
       message → confirm a `MESSAGE_EDIT_TOO_OLD` (409) toast (or success if the server window
       is still open — note which).
    2. DELETE: delete an outbound message via the inbox → confirm it disappears on BOTH sides
       of the real chat and the bubble is removed from the inbox; the conversation stays in
       its prior AI/status state (no takeover).
    3. SEND-FILE: send a photo → confirm it arrives as an inline photo (not a document); send
       a >1024-char caption with a file → confirm the overflow arrives as a follow-up text;
       send a .pdf → confirm it arrives as a document. Confirm the conversation flipped to
       manual (AI off) and any pending queue for that contact was cancelled.
    4. INCOMING + DOWNLOAD: from the contact side send a photo and a document → confirm each
       appears as a typed file bubble in the inbox, then click download → confirm the correct
       bytes/filename download on demand.
    5. Optionally try a >50 MB file → confirm a `FILE_TOO_LARGE` (413) toast and that nothing
       was sent.
  </how-to-verify>
  <verify>
    <automated>MANUAL — human live-smoke; no automated command (server-controlled Telethon behaviour). Automated coverage lives in tests/test_phase23_inbox_mutations.py.</automated>
  </verify>
  <acceptance_criteria>
    - Human has exercised edit, delete-for-everyone, send-file (photo/overflow/document), and incoming download against a live account and reports pass/fail per step.
    - Any deviation (e.g. edit window behaviour) is recorded in the SUMMARY for the record.
  </acceptance_criteria>
  <done>Human confirms all four operations behave correctly against a real Telegram account; deviations recorded.</done>
  <resume-signal>Type "approved" to close the phase, or describe the issues found (which become gap-closure input).</resume-signal>
</task>

</tasks>

<verification>
- `python3` openapi assertion prints `openapi ok` (4 endpoints present).
- `lovable-handoff/error-codes.md` contains all D-17 codes incl. `DOWNLOAD_FAILED` + `TELEGRAM_OP_FAILED`.
- Human live-smoke sign-off recorded.
</verification>

<success_criteria>
- Handoff spec (openapi.json + error-codes.md) reflects all Phase-23 endpoints/schemas/codes, regenerated (not hand-edited).
- Human confirms edit / delete-for-everyone / send-file (auto-media + overflow) / incoming download work against a real account.
</success_criteria>

<output>
After completion, create `.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-06-SUMMARY.md`.
</output>
