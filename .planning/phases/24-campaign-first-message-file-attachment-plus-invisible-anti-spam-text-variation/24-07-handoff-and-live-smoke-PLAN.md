---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 07
type: execute
wave: 3
depends_on: ["24-04", "24-05", "24-06"]
files_modified:
  - lovable-handoff/openapi.json
  - lovable-handoff/error-codes.md
autonomous: false
requirements: [D-19, D-06, D-09]
must_haves:
  truths:
    - "D-19: lovable-handoff/openapi.json is regenerated (via export-handoff, api rebuilt first — no hand-editing) and now contains the POST + DELETE /api/v1/campaigns/{id}/attachment paths and the variation_enabled + has_attachment campaign fields"
    - "D-19: lovable-handoff/error-codes.md documents FILE_TOO_LARGE (413) for the attachment upload"
    - "D-06 (live smoke, manual): a real .jpg attached to a test campaign arrives in a real Telegram client AS AN INLINE PHOTO with the opener caption (not a grey document)"
    - "D-09 (live smoke, manual): the varied opener shows NO visible boxes/artifacts/extra spacing in Telegram Desktop + mobile"
  artifacts:
    - path: "lovable-handoff/openapi.json"
      provides: "regenerated spec with attachment endpoints + variation_enabled/has_attachment"
      contains: "/attachment"
    - path: "lovable-handoff/error-codes.md"
      provides: "FILE_TOO_LARGE row"
      contains: "FILE_TOO_LARGE"
  key_links:
    - from: "lovable-handoff/openapi.json"
      to: "sibling repo aimly-tg-outreach (Lovable UI)"
      via: "export-handoff.sh regen"
      pattern: "/attachment"
---

<objective>
Publish the API contract to the frontend and prove the two capabilities work end-to-end on a live account. Regenerate the openapi handoff (attachment endpoints + variation_enabled/has_attachment), document FILE_TOO_LARGE, then run the live smoke that mocks can't cover: a real photo arriving AS A PHOTO and invisible chars being truly invisible (D-06/D-09).

Purpose: close the phase's frontend + manual-verification gaps (24-VALIDATION.md Manual-Only table).
Output: regenerated openapi.json + error-codes.md update + a human-verified live smoke.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-CONTEXT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-VALIDATION.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-04-attachment-endpoint-and-duplicate-PLAN.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: rebuild api + regenerate openapi handoff + document FILE_TOO_LARGE</name>
  <files>lovable-handoff/openapi.json, lovable-handoff/error-codes.md</files>
  <read_first>
    - scripts/export-handoff.sh (the canonical regen flow: builds api, waits for /openapi.json, writes lovable-handoff/openapi.json via jq)
    - lovable-handoff/error-codes.md:5-22 (the error-code table format — add a FILE_TOO_LARGE row like the existing 4xx rows)
    - CLAUDE.md "Git & Deploy" (docker compose up -d --build api) + "Lovable-фронт quirks" (openapi is source of truth, do not hand-edit)
  </read_first>
  <action>
    1. Rebuild the api container so /openapi.json reflects the new endpoints + schema fields: `docker compose up -d --build api`.
    2. Run `bash scripts/export-handoff.sh` to regenerate lovable-handoff/openapi.json (+ types if the script emits them). Do NOT hand-edit the spec.
    3. Confirm the regenerated openapi.json contains `/api/v1/campaigns/{campaign_id}/attachment` (POST + DELETE) and that CampaignResponse carries `variation_enabled` + `has_attachment` and CampaignCreate/Update carry `variation_enabled`.
    4. Edit lovable-handoff/error-codes.md: add a row to the main error-code table (reuse the exact 4-column format at lines 5-22):
       `| FILE_TOO_LARGE | 413 | "File is too large (max 50 MB)." | Campaign attachment upload — surface the 50 MB limit before retry |`
  </action>
  <verify>
    <automated>grep -q "/attachment" lovable-handoff/openapi.json && grep -q "variation_enabled" lovable-handoff/openapi.json && grep -q "has_attachment" lovable-handoff/openapi.json && grep -q "FILE_TOO_LARGE" lovable-handoff/error-codes.md && echo HANDOFF_OK</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c '/attachment' lovable-handoff/openapi.json` >= 1 (POST + DELETE paths present)
    - `grep -q 'variation_enabled' lovable-handoff/openapi.json` AND `grep -q 'has_attachment' lovable-handoff/openapi.json`
    - `grep -q 'FILE_TOO_LARGE' lovable-handoff/error-codes.md`
    - openapi.json was produced by export-handoff.sh (not hand-edited) — git diff shows machine-generated formatting
  </acceptance_criteria>
  <done>openapi.json + error-codes.md carry the attachment endpoints, variation flag, has_attachment and FILE_TOO_LARGE; frontend can generate against them.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 2: live smoke — real photo arrives as photo + invisible variation truly invisible</name>
  <files>(none — live verification against a running deployment)</files>
  <what-built>
    End-to-end campaign file-opener + invisible variation: attachment upload (24-04), enqueue emits item_type='file' with caption (24-05), worker loads the blob and sends it as auto-media with a varied caption (24-06) via the extended send_file (24-03). Mocks proved force_document=False and the strip-invisible invariant; only a real client can confirm rendering (24-VALIDATION.md Manual-Only rows).
  </what-built>
  <read_first>
    - .planning/phases/24-.../24-VALIDATION.md §"Manual-Only Verifications" (the three manual rows + exact test instructions)
    - CLAUDE.md "Стек"/queue rate-limits (send through a warmed, healthy sender; do NOT touch caps)
  </read_first>
  <action>
    Claude sets up the live smoke via API/UI, then pauses for the human to confirm on-device (auto-media rendering + glyph invisibility are client-side and cannot be proven by mocks). Setup: create a draft test campaign with a short opener template + a real .jpg attachment (POST /campaigns/{id}/attachment) + variation_enabled ON + ONE warmed/healthy sender + a folder with ONE controlled test contact; start the campaign and let the worker send on normal timing (do NOT force / do NOT touch rate-limit caps). Then hand off to the human per <how-to-verify>.
  </action>
  <how-to-verify>
    1. In the UI (or via API): create a draft test campaign, set a short opener template (e.g. "Здравствуйте {{имя}}, короткое сообщение"), attach a real `.jpg` via POST /campaigns/{id}/attachment, ensure variation_enabled is ON, attach ONE warmed/healthy sender, target a folder containing ONE controlled test contact you own.
    2. Start the campaign; wait for the worker to send (respect normal queue timing — do NOT force).
    3. On the RECIPIENT device (Telegram Desktop AND mobile): confirm the message arrives as an INLINE PHOTO (not a grey document) with the opener as its caption.
    4. Confirm the caption text reads cleanly — NO visible boxes, tofu, or extra spacing artifacts from the invisible variation.
    5. In the platform inbox / DB, confirm the stored messages row text is CLEAN (no invisible chars) while the wire message was varied (D-14) — e.g. `SELECT message_text FROM messages ORDER BY created_at DESC LIMIT 1;` shows clean text.
    6. (Optional overflow check) Set the opener > ~1024 chars and confirm the file arrives with no caption + a following text message.
  </how-to-verify>
  <verify>
    <automated>MANUAL — client-side rendering; no automated command. Backend covered by 24-01/03/04/05/06 targeted tests.</automated>
  </verify>
  <resume-signal>Type "approved" if the photo renders inline with a clean caption and DB stays clean; otherwise describe what rendered wrong (document vs photo, visible artifacts, dirty DB) so a gap-closure plan can be scoped.</resume-signal>
  <done>A real .jpg opener arrives inline as a photo with a clean-reading caption on Desktop + mobile, the stored messages row is clean text (D-14), and (optional) overflow splits correctly — human types "approved".</done>
</task>

</tasks>

<verification>
- Task 1 automated grep passes (HANDOFF_OK).
- Task 2 human-verified: real photo inline + caption clean + DB clean; overflow (optional) behaves.
- Final targeted regression: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -k "variation or attachment or send_file or file_opener or rerender" -q` GREEN (Phase-24 subset — do NOT trust full-suite green per baseline caveat).
</verification>

<success_criteria>
The frontend contract is published (attachment endpoints + variation flag + FILE_TOO_LARGE) and a live send confirms a real photo arrives inline with a clean-reading varied caption while the DB stays clean (D-06/D-09/D-19).
</success_criteria>

<output>
After completion, create `.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-07-SUMMARY.md`.
</output>
