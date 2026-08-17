---
status: resolved
trigger: "при попытке удалить папку или переименовать ее выходит ошибка: Folder contains 2 contact(s). Move them, delete them, or pass ?force=true. хочу чтобы при удалении папки она удалялсь вместе с контактами"
created: 2026-08-17
updated: 2026-08-17
---

## Symptoms

DATA_START
- **Expected behavior:** Deleting a folder should delete it together with the contacts inside it (user's desired behavior). Renaming a folder should never be blocked by contained contacts at all.
- **Actual behavior:** Both deleting AND renaming a folder fail with an error when the folder contains contacts.
- **Error message:** `Folder contains 2 contact(s). Move them, delete them, or pass ?force=true.`
- **Timeline:** Unknown / likely by-design guard on the delete endpoint (the error text itself advertises `?force=true`). The rename failure is anomalous.
- **Reproduction:** In the UI, try to delete or rename a folder that contains 2 contacts.
DATA_END

## Notes

- The 409-style guard with `?force=true` escape hatch suggests the backend DELETE endpoint intentionally blocks non-empty folders. Two candidate root causes to investigate:
  1. Frontend delete flow never passes `force=true` (and user wants cascade delete as the default/confirmed UX).
  2. Rename also failing suggests either the frontend implements rename as delete+recreate, or the guard is wired into the wrong route/shared handler.
- Desired outcome per user: folder deletion cascades contacts (with appropriate UI confirmation); rename must work regardless of contents.

## Current Focus

- hypothesis: CONFIRMED + FIXED + DEPLOYED + VERIFIED LIVE. Two independent root causes (delete missing force, rename form cancelled by onBlur) plus one latent 500 (campaign RESTRICT guard).
- test: live curl against prod api on a scratch folder + rename of the real 2-contact folder
- expecting: rename 200 with contacts intact; delete 409 without force, 204 + contacts gone with force
- next_action: awaiting user confirmation in the real UI

## Evidence

- checked: `grep -rn "Folder contains"` across repo
  found: Single occurrence — `app/routers/folders.py:325` inside `delete_folder`, gated by `if contact_count > 0 and not force`. No other route raises it.
  implication: Only DELETE can emit that message. Rename cannot produce it from the backend.

- checked: `app/routers/folders.py::rename_folder` (PATCH /{folder_id}, lines 219-265) read completely
  found: Guards are only: 404 if folder missing/cross-tenant, 400 empty name, 409 FOLDER_NAME_DUPLICATE. Zero references to Contact / contact_count.
  implication: Backend rename is NOT blocked by contained contacts. The rename symptom has a different cause than the delete symptom.

- checked: All frontend folder API call sites — `grep -rn "folders/" frontend/src` (excluding generated types)
  found: Exactly 3 calls, all in `frontend/src/routes/_authenticated/contacts.tsx`: GET stats (:437), PATCH rename (:443), DELETE (:452). Rename is a plain PATCH with `{name}` — no delete+recreate. DELETE sends NO `force` query param.
  implication: (1) Delete root cause is confirmed: frontend never passes force → 409 always for non-empty folders. (2) There is no code path where rename calls delete.

- checked: `frontend/src/lib/error-codes.ts` for FOLDER_NOT_EMPTY
  found: No mapping → `errorMessageFromEnvelope` falls through to raw backend `message`, which is why the user saw the verbatim "…or pass ?force=true." text (an internal-facing string leaking into UI).
  implication: The error text the user reported is definitively the DELETE guard's raw message.

- checked: prod DB FK delete rules referencing `folders`
  found: `contacts_folder_id_fkey` = CASCADE; `campaigns_folder_id_fkey` = RESTRICT. ORM mirrors this (app/models/__init__.py:605 CASCADE, :788 RESTRICT).
  implication: DB-level cascade for contacts ALREADY exists — no migration needed for the user's desired cascade behavior; only the application-level `force` gate stands in the way. Separately, the router only blocks campaigns with `status='running'`, so a draft/paused/done campaign on the folder would hit the RESTRICT FK and surface as a 500 IntegrityError, not a friendly 409.

- checked: `frontend/src/routes/_authenticated/contacts.tsx:593-611` rename UI
  found: Inline form input has `onBlur={() => setRenaming(false)}`. Clicking the "Save" button blurs the input first, which unmounts the form; whether submit still fires is browser/timing dependent.
  implication: Candidate explanation for "rename doesn't work" that is unrelated to contacts — the rename may silently never be submitted, and the user attributed the leftover delete-error toast to it.

- checked: api access log over last 168h — counts of `PATCH /api/v1/folders` vs `DELETE /api/v1/folders`
  found: **PATCH count = 0. DELETE count = 5, every one returned 409 Conflict.**
  implication: DECISIVE. The rename request was NEVER sent to the backend even once. The rename symptom is a pure frontend bug (onBlur cancels the form before submit), NOT the contacts guard. The user saw the persistent delete-error toast and reasonably attributed it to rename as well.

- checked: prod DB state of the folder in the failing DELETEs (18cfe3e6-…)
  found: name='test', workspace bb96789d-…, exactly **2 contacts** — matches the reported "2 contact(s)" verbatim. Zero campaigns reference this folder (any status).
  implication: Confirms the exact reproduction. Nothing but the `force` gate blocks this delete.

## Eliminated

## Resolution

- root_cause: |
    THREE distinct defects, not one. The single error message masked two unrelated bugs.

    (1) DELETE blocked — frontend/src/routes/_authenticated/contacts.tsx:452 called
        `DELETE /api/v1/folders/{id}` with NO `force` query param, while the confirm
        dialog it showed already promised "Contacts will be removed". The backend guard
        (app/routers/folders.py:319-333) therefore 409'd on every non-empty folder. The
        raw internal message ("…or pass ?force=true") leaked to the user because
        FOLDER_NOT_EMPTY has no entry in frontend/src/lib/error-codes.ts.

    (2) RENAME "blocked" — a pure frontend bug, completely unrelated to contacts. The
        rename input had `onBlur={() => setRenaming(false)}`; clicking "Save" blurs the
        input first, unmounting the form before submit fired. PROOF: the api access log
        over 168h contains **0** `PATCH /api/v1/folders` requests vs 5 `DELETE` 409s —
        the rename never reached the server even once. The backend rename handler has no
        contact guard at all. The user saw the leftover delete-error toast and reasonably
        attributed it to rename too.

    (3) Latent 500 (found while mapping the delete path) — campaigns.folder_id is FK
        ON DELETE RESTRICT, but the guard only queried `status='running'`. A draft/paused/
        done campaign slipped past it and blew up as an unhandled ForeignKeyViolationError
        (500). Verified by reverting the fix: the test reproduced the exact IntegrityError.

- fix: |
    frontend/src/routes/_authenticated/contacts.tsx
      - deleteMut now sends `query: { force: true }` (cascade is the intended UX).
      - confirm() text now states the actual contact count and "cannot be undone".
      - rename form: removed the submit-killing `onBlur` cancel; added explicit Cancel
        button + Escape-to-cancel, pending state on Save, and a no-op short-circuit when
        the name is unchanged.

    app/routers/folders.py
      - delete guard now matches ANY campaign referencing the folder (dropped the
        `status='running'` filter) → clear 409 FOLDER_USED_BY_RUNNING_CAMPAIGN with each
        campaign's status, instead of a 500. Workspace scoping (`workspace_id = :wid`)
        preserved verbatim.

    NOTE: no migration needed — `contacts_folder_id_fkey` is ALREADY ON DELETE CASCADE in
    prod (verified via information_schema). Cascade is DB-level, matching project pattern;
    the app-level `force` flag remains the intent gate. Tradeoff: deletion is irreversible
    with no soft-delete/undo — only the confirm dialog protects the user.

    tests/test_folders.py — 3 regression tests added:
      - test_rename_folder_with_contacts_is_never_blocked
      - test_delete_folder_used_by_draft_campaign_returns_409_not_500
      - test_delete_folder_campaign_guard_is_workspace_scoped

- verification: |
    - Tests: 16/16 tests_folders.py pass; 26/26 including adjacent campaign suites
      (test_campaign_draft_optional, test_phase5_1_campaign_v2_router) via required overlay.
    - Negative control: temporarily reverted the router guard → new test FAILED with the
      exact predicted `ForeignKeyViolationError … campaigns_folder_id_fkey`, proving the
      test is real regression protection, not a tautology. Fix restored after.
    - Deployed: `docker compose up -d --build api` (clean startup, migrations applied) and
      `./deploy-frontend.sh`. Confirmed `force:!0` present in the shipped bundle
      /var/www/aimly/assets/contacts-DA93PM2r.js.
    - Live prod API (temp workspace key, deleted after use):
        * PATCH on the real 2-contact folder 18cfe3e6 → 200, contact_count still 2
          (renamed to "test-renamed" then back to "test"). Rename is NOT blocked.
        * Scratch folder + 2 contacts: DELETE without force → 409 reproducing the user's
          exact message; DELETE ?force=true → 204, then contacts=0 AND folder=0.
    - Data safety: user's "test" folder + its 2 contacts left intact; scratch folder gone;
      temp API key deleted (only a pre-existing already-revoked key remains).

- files_changed:
    - frontend/src/routes/_authenticated/contacts.tsx
    - app/routers/folders.py
    - tests/test_folders.py
