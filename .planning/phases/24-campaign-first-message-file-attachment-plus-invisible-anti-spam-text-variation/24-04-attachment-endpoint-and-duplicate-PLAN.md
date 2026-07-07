---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 04
type: execute
wave: 2
depends_on: ["24-02"]
files_modified:
  - app/routers/campaigns.py
  - tests/test_campaign_attachment.py
autonomous: true
requirements: [D-03, D-13, D-19, D-20, D-01]
must_haves:
  truths:
    - "D-19: POST /api/v1/campaigns/{id}/attachment (multipart) stores the uploaded file as ONE campaign_attachments row (upsert — delete-then-insert per campaign, D-01 one-file), workspace-scoped; DELETE /api/v1/campaigns/{id}/attachment removes it (204); both alias-tolerant to the multipart field name (file|attachment)"
    - "D-03: upload >50 MB (52428800 bytes) → 413 with {code: FILE_TOO_LARGE}; any file type accepted below the limit"
    - "D-13: variation_enabled is wired through create_campaign (from payload) and PATCH (existing setattr loop) so the flag round-trips; CampaignResponse carries variation_enabled + a computed has_attachment"
    - "D-20: duplicate_campaign copies BOTH the variation_enabled flag AND the attachment blob (new campaign_attachments row for the copy) so the duplicate is send-ready"
    - "Workspace isolation: attachment endpoints load the campaign via _load_campaign (cross-workspace → CAMPAIGN_NOT_FOUND 404); the attachment row carries workspace_id"
  artifacts:
    - path: "app/routers/campaigns.py"
      provides: "upload_attachment + delete_attachment endpoints, variation_enabled wiring in create/response/duplicate, attachment copy in duplicate, has_attachment computed"
      contains: "/{campaign_id}/attachment"
    - path: "tests/test_campaign_attachment.py"
      provides: "endpoint tests: upload stores blob, >50MB->413, DELETE clears, duplicate copies blob+flag, has_attachment true after upload"
      min_lines: 60
  key_links:
    - from: "POST /campaigns/{id}/attachment"
      to: "campaign_attachments table"
      via: "delete-then-insert one row per campaign (upsert)"
      pattern: "campaign_attachments"
    - from: "duplicate_campaign"
      to: "campaign_attachments (copy)"
      via: "SELECT src blob -> INSERT new_c row"
      pattern: "variation_enabled=src.variation_enabled"
---

<objective>
Expose the attachment lifecycle over the API (D-19): a multipart upload endpoint with the 50 MB → FILE_TOO_LARGE guard (D-03), a delete endpoint, the variation_enabled flag wired through create/patch/response (D-13), and duplicate_campaign copying both the flag and the blob (D-20).

Purpose: let the frontend attach/remove a file and toggle variation; make duplicates send-ready.
Output: two endpoints + wiring in campaigns.py + endpoint tests. Depends on 24-02 (CampaignAttachment model + schemas).
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
<!-- From 24-02: model + schemas. Multipart precedent: account_import.py:85-105. -->
```python
# app/routers/campaigns.py (add near the top with the other module constants)
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024   # 50 MB (D-03)

# endpoints on the existing router (prefix "/api/v1/campaigns")
@router.post("/{campaign_id}/attachment")            # multipart, 200 -> {campaign_id, file_name, size_bytes, content_type}
@router.delete("/{campaign_id}/attachment", status_code=204)
```
Existing helpers to reuse: `_load_campaign(db, ctx, campaign_id)` (campaigns.py:115, workspace-scoped 404), `_campaign_to_response` (319), `create_campaign` (435), `duplicate_campaign` (977).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: upload + delete attachment endpoints (50MB guard, alias tolerance, upsert)</name>
  <read_first>
    - app/routers/account_import.py:85-105 (UploadFile = File(...) + await file.read() + len guard → 413 {code: FILE_TOO_LARGE})
    - app/routers/campaigns.py:57 (router prefix), :115 (_load_campaign), :434-548 (create — imports/patterns), :1119-1173 (attach_sender — how existing sub-resource endpoints are shaped, commit style)
    - app/schemas/__init__.py:1101-1114 (AliasChoices precedent for Lovable field tolerance, D-19)
    - app/models/__init__.py CampaignAttachment (from 24-02)
  </read_first>
  <action>
    In app/routers/campaigns.py:
    1. Add `MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024` module constant; import `UploadFile, File` from fastapi and `CampaignAttachment` from app.models (and `delete` from sqlalchemy if not present).
    2. `@router.post("/{campaign_id}/attachment")` async def upload_attachment(campaign_id: UUID, file: UploadFile = File(default=None), attachment: UploadFile = File(default=None), ctx=Depends(auth_dep), db=Depends(get_db)):
       - alias tolerance (D-19): `upload = file or attachment`; if `upload is None` → HTTPException(422, {"code":"FILE_REQUIRED","message":"no file field"}).
       - `raw = await upload.read()`; if `len(raw) > MAX_ATTACHMENT_BYTES` → HTTPException(413, {"code":"FILE_TOO_LARGE","message": f"Max {MAX_ATTACHMENT_BYTES} bytes"}).
       - `c = await _load_campaign(db, ctx, campaign_id)` (workspace 404).
       - Upsert one row (D-01): `await db.execute(delete(CampaignAttachment).where(CampaignAttachment.campaign_id == c.id))` then `db.add(CampaignAttachment(campaign_id=c.id, workspace_id=ctx.workspace_id, file_data=raw, file_name=(upload.filename or "file"), content_type=upload.content_type, size_bytes=len(raw)))`; `await db.commit()`.
       - return {"campaign_id": str(c.id), "file_name": upload.filename or "file", "size_bytes": len(raw), "content_type": upload.content_type}.
    3. `@router.delete("/{campaign_id}/attachment", status_code=204)` async def delete_attachment(...): `c = await _load_campaign(...)`; `await db.execute(delete(CampaignAttachment).where(CampaignAttachment.campaign_id == c.id))`; `await db.commit()`; return None.
    Keep the temp-file-free contract — bytes go straight to the BYTEA column (D-02).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_attachment.py -k "upload or delete or too_large or alias" -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n '/{campaign_id}/attachment' app/routers/campaigns.py` shows a POST and a DELETE
    - `grep -n 'MAX_ATTACHMENT_BYTES = 50 \* 1024 \* 1024' app/routers/campaigns.py` matches; `grep -n 'FILE_TOO_LARGE' app/routers/campaigns.py` matches with status 413
    - upload endpoint reads either `file` or `attachment` multipart field (alias tolerance)
    - tests: upload returns size_bytes==len(raw) and stores exactly ONE row; a 2nd upload replaces (still one row); >50MB → 413; DELETE → 204 and row gone
  </acceptance_criteria>
  <done>Upload/delete endpoints store/replace/remove exactly one blob per campaign, workspace-scoped, with the 50 MB guard.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: wire variation_enabled + has_attachment; duplicate copies flag + blob</name>
  <read_first>
    - app/routers/campaigns.py:319-390 (_campaign_to_response field mapping — add variation_enabled + has_attachment)
    - app/routers/campaigns.py:475-521 (create_campaign Campaign(...) kwargs — add variation_enabled=payload.variation_enabled)
    - app/routers/campaigns.py:626-732 (patch_campaign — confirm the setattr loop at :706 already covers variation_enabled; no change needed but verify update_data excludes unset)
    - app/routers/campaigns.py:1009-1077 (duplicate_campaign Campaign(...) kwargs + post-flush commit — add variation_enabled + attachment copy)
  </read_first>
  <action>
    In app/routers/campaigns.py:
    1. create_campaign: add `variation_enabled=payload.variation_enabled,` to the Campaign(...) constructor (beside allow_recontact).
    2. _campaign_to_response: add `variation_enabled=campaign.variation_enabled,` and compute has_attachment: `has_attachment = (await db.execute(text("SELECT 1 FROM campaign_attachments WHERE campaign_id = :cid"), {"cid": str(campaign.id)})).first() is not None` then pass `has_attachment=has_attachment,`. (text already imported in campaigns.py; if not, import from sqlalchemy.)
    3. duplicate_campaign: add `variation_enabled=src.variation_enabled,` to the new_c Campaign(...). After `await db.flush()` (new_c.id exists) and BEFORE/at the same commit, copy the attachment if present:
       ```python
       att = (await db.execute(text("SELECT file_data, file_name, content_type, size_bytes FROM campaign_attachments WHERE campaign_id = :cid"), {"cid": str(src.id)})).first()
       if att is not None:
           db.add(CampaignAttachment(campaign_id=new_c.id, workspace_id=ctx.workspace_id,
                                     file_data=att.file_data, file_name=att.file_name,
                                     content_type=att.content_type, size_bytes=att.size_bytes))
       ```
       Ensure this is committed in the same transaction as new_c.
    4. PATCH: verify variation_enabled flows through the existing setattr loop (update_data = payload.model_dump(exclude_unset=True)); add nothing unless it's excluded — if the loop already covers it, note that in the summary.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_attachment.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n 'variation_enabled=payload.variation_enabled' app/routers/campaigns.py` matches (create)
    - `grep -n 'variation_enabled=src.variation_enabled' app/routers/campaigns.py` matches (duplicate)
    - `grep -n 'has_attachment' app/routers/campaigns.py` matches (computed in _campaign_to_response)
    - tests: after upload, GET campaign → has_attachment True; PATCH variation_enabled=false round-trips in response; duplicate of a campaign-with-attachment → the copy has its OWN campaign_attachments row (same bytes) AND variation_enabled matches src
  </acceptance_criteria>
  <done>variation_enabled round-trips through create/patch/response; has_attachment reflects the blob; duplicate copies flag + blob (D-13/D-20).</done>
</task>

</tasks>

<verification>
- `pytest tests/test_campaign_attachment.py -x` GREEN (model tests from 24-02 + endpoint tests here).
- `pytest tests/test_campaign_router.py -q` still GREEN (no regression to existing create/patch/duplicate).
</verification>

<success_criteria>
Attachment upload/delete works with the 50 MB FILE_TOO_LARGE guard and alias tolerance; one blob per campaign; variation_enabled round-trips; has_attachment surfaces the blob; duplicate copies both (D-01/D-03/D-13/D-19/D-20).
</success_criteria>

<output>
After completion, create `.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-04-SUMMARY.md`.
</output>
