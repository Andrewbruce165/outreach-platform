---
phase: 20-account-profile-management
plan: 05
type: execute
wave: 5
depends_on: ["20-02", "20-03", "20-04"]
files_modified:
  - lovable-handoff/openapi.json
  - lovable-handoff/types/api.ts
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/accounts.tsx
autonomous: false
requirements: [PROF-09]
must_haves:
  truths:
    - "The regenerated openapi.json exposes every Phase-20 endpoint (/senders/{slug}/profile, /username-check, /photo, /resync, /2fa, /2fa/recovery-email[/confirm]) and the SenderResponse profile fields"
    - "The accounts row shows the cached avatar photo (initials fallback) + @username; the kebab has Изменить профиль (edit) + Обновить профиль (resync)"
    - "The profile modal has Section A (identity, one scoped save) + Section B (2FA password + two-step recovery email), each with its own scoped primary button — no single generic Save"
    - "Username/photo changed <1h ago hard-blocks the save with a live countdown; name/bio + warmup/<7day only warn"
  artifacts:
    - path: "lovable-handoff/openapi.json"
      provides: "regenerated API contract incl. Phase-20 paths"
      contains: "/senders/{slug}/profile"
    - path: "/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/accounts.tsx"
      provides: "enriched SenderRow + kebab + profile modal + guardrails"
      contains: "Изменить профиль"
  key_links:
    - from: "accounts.tsx SenderRow avatar"
      to: "GET /api/v1/senders/{slug}/photo (authenticated)"
      via: "<img> src when has_photo, initials fallback otherwise"
      pattern: "/photo"
    - from: "accounts.tsx profile modal Section B email step"
      to: "POST /senders/{slug}/2fa/recovery-email then /confirm"
      via: "two-step confirm state (EMAIL_CONFIRMATION_SENT → code input)"
      pattern: "recovery-email"
---

<objective>
Surface Phase 20 in the product: regenerate the Lovable handoff contract from the finished backend, then build the enriched account row + kebab + full profile edit modal + guardrails in the sibling frontend repo, per the APPROVED 20-UI-SPEC. Ends with a blocking human-verify gate (visual + the live recovery-email confirm that automated tests cannot cover).

Purpose: closes PROF-09 and the phase's manual-only verifications (photo render correctness, live 2FA recovery-email round-trip).
Output: regenerated openapi.json + types (backend repo), enriched accounts.tsx (sibling repo), human sign-off.

CROSS-REPO ISOLATION (CLAUDE.md + memory feedback-parallel-agent-careful-commits): openapi.json/types → backend repo `Andrewbruce165/outreach-platform`; accounts.tsx → sibling `AGS-Venture-Lab/aimly-tg-outreach`. NEVER `git add -A`; stage explicit files per repo. Commit each repo separately from inside its own working dir.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/20-account-profile-management/20-UI-SPEC.md
@.planning/phases/20-account-profile-management/20-CONTEXT.md
@.planning/phases/20-account-profile-management/20-RESEARCH.md
@.planning/phases/20-account-profile-management/20-VALIDATION.md

<interfaces>
<!-- Handoff regen precedent (18-05 / 19-05): regenerate OFFLINE via app.openapi() in the TEST container — no un-gated prod api rebuild. -->
<!-- Backend endpoints added by 20-02..20-04 (all under /api/v1, workspace-scoped via auth_dep): -->
  PATCH  /senders/{slug}/profile            {first_name?, last_name?, about?, username?} -> SenderCreateResponse
  GET    /senders/{slug}/username-check?username=  -> {available, reason}
  POST   /senders/{slug}/resync             -> SenderResponse
  POST   /senders/{slug}/photo              (multipart file) -> SenderCreateResponse
  DELETE /senders/{slug}/photo              -> SenderCreateResponse
  GET    /senders/{slug}/photo              -> raw bytes (image)
  POST   /senders/{slug}/2fa                {current_password?, new_password, hint?}
  POST   /senders/{slug}/2fa/recovery-email {current_password?, email} -> {status, code_length}
  POST   /senders/{slug}/2fa/recovery-email/confirm {code}
<!-- SenderResponse gained: tg_username, tg_bio, has_photo, profile_field_changed_at (for the client-side 1h countdown). -->
<!-- Frontend current state: src/routes/_authenticated/accounts.tsx (836 lines). FleetTable -> SenderRow (Account cell ~333-360, avatar .avatar--sm ~337, kebab .ob__menu, EditSenderModal name+role only). aimly.css utility classes only (NOT bare shadcn). Light theme only. RU form copy. -->
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Regenerate lovable-handoff openapi.json + types (backend repo)</name>
  <files>lovable-handoff/openapi.json, lovable-handoff/types/api.ts</files>
  <read_first>
    - scripts/export-handoff.sh (the canonical regen flow — boot api, scrape /openapi.json, openapi-typescript)
    - .planning/STATE.md (Phase 18-05 / 19-05 decisions: regenerate OFFLINE via app.openapi() in the test container — no un-gated prod deploy)
    - lovable-handoff/openapi.json (current — confirm it does NOT yet contain the Phase-20 paths before regen)
  </read_first>
  <action>
Regenerate the handoff bundle from the finished backend (20-02..20-04 merged). Prefer the OFFLINE route used by 18-05/19-05 to avoid an un-gated prod api rebuild:

```bash
cd /root/apps/aimly/tg-outreach
# Dump the spec from app.openapi() inside the TEST container (no prod api touch):
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api \
  python -c "import json, app.main as m; print(json.dumps(m.app.openapi()))" \
  | jq . > lovable-handoff/openapi.json
# Regenerate TS types:
npx -y openapi-typescript@7 lovable-handoff/openapi.json -o lovable-handoff/types/api.ts
```
If importing `app.main` at module scope is awkward in the test container, fall back to `scripts/export-handoff.sh` (which boots the api service and scrapes /openapi.json with the built-in Outreach-title sanity guard). Either way, verify the spec is THIS project (title contains Outreach/aimly) and that the new paths are present.

Commit ONLY these two files to the BACKEND repo:
```bash
cd /root/apps/aimly/tg-outreach
git add lovable-handoff/openapi.json lovable-handoff/types/api.ts
git commit -m "docs(20): regenerate handoff openapi+types for account profile endpoints

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach && jq -e '.paths | has("/api/v1/senders/{slug}/profile") and has("/api/v1/senders/{slug}/2fa/recovery-email/confirm") and has("/api/v1/senders/{slug}/photo")' lovable-handoff/openapi.json</automated>
  </verify>
  <acceptance_criteria>
    - `jq '.paths | keys' lovable-handoff/openapi.json` includes `/api/v1/senders/{slug}/profile`, `/api/v1/senders/{slug}/username-check`, `/api/v1/senders/{slug}/resync`, `/api/v1/senders/{slug}/photo`, `/api/v1/senders/{slug}/2fa`, `/api/v1/senders/{slug}/2fa/recovery-email`, `/api/v1/senders/{slug}/2fa/recovery-email/confirm`
    - `jq -r '.info.title' lovable-handoff/openapi.json` contains Outreach or aimly (not a neighbouring FastAPI)
    - `grep -c "tg_username" lovable-handoff/types/api.ts` >= 1 (SenderResponse profile fields present in generated types)
    - The commit touched ONLY lovable-handoff/openapi.json + lovable-handoff/types/api.ts (git show --stat)
  </acceptance_criteria>
  <done>openapi.json + types regenerated offline, carry every Phase-20 path + SenderResponse profile fields, committed to the backend repo only.</done>
</task>

<task type="auto">
  <name>Task 2: Enriched account row + kebab + profile modal + guardrails (sibling repo)</name>
  <files>/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/accounts.tsx</files>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/accounts.tsx (FleetTable, SenderRow Account cell, avatar .avatar--sm, ob__menu kebab, EditSenderModal, statusStyle map, ApiError banner)
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/contacts.tsx (the .ct__dropzone + hidden file input pattern to reuse for photo upload)
    - .planning/phases/20-account-profile-management/20-UI-SPEC.md (ALL of it — Surfaces 1-4, Interaction Contracts C1-C6, Copywriting Contract, Color/Typography/Spacing rules)
  </read_first>
  <action>
In the SIBLING repo `/root/apps/aimly/aimly-tg-outreach`, edit `src/routes/_authenticated/accounts.tsx` (+ small helpers if needed) to implement the APPROVED 20-UI-SPEC. Rebase onto `origin/main` first (Lovable may have pushed). Use `aimly.css` utility classes only (NOT bare shadcn), light theme only, RU form copy. Reference the exact copy strings from 20-UI-SPEC §Copywriting Contract verbatim.

**Surface 1 — enriched SenderRow (D-10/D-13):** In the `Account` cell, when `sender.has_photo`, render an `<img>` from `/api/v1/senders/{slug}/photo` (through the app's authenticated fetch/axios base — same auth header the app already sends; on 404 fall back to initials) inside the existing `.avatar .avatar--sm` frame, keeping the status-dot overlay. Below the name, show `@{sender.tg_username}` in `.muted .text-xs` when present (omit the line entirely if absent — no bare `@`). Phone stays muted mono. No new table columns.

**Surface 2 — kebab (D-12/D-14):** Replace `Изменить` with **`Изменить профиль`** (`Pencil`) → opens the profile modal (Surface 3). Replace `Обновить статус` with **`Обновить профиль`** (`RefreshCcw`) → calls `POST /senders/{slug}/resync`; show `Loader2.ob__spin` while pending; on success `toast.success("Профиль обновлён")` + `invalidateQueries(["senders"])`; on error `toast.error(error.message)`. Keep `История ограничений` + `Удалить` unchanged.

**Surface 3 — profile modal `.modal--wide` (two scoped sections, D-01..D-05, Reconciliation §5):**
- **Section A — Профиль** (uppercase label `Профиль`): fields Имя / Фамилия / Username (prefixed `@`, debounced availability via `GET /username-check` → `.field__hint` states from C5) / Описание (`.textarea maxLength=70` + `{n}/70` counter) / Фото профиля (avatar preview + `.ct__dropzone` "Загрузить фото" + "Удалить фото" ghost-danger when present). Footer: `Отмена` (btn--ghost) + **`Сохранить профиль`** (btn--primary, pending "Сохранение…") → `PATCH /senders/{slug}/profile`. Photo upload → `POST /senders/{slug}/photo` (multipart); delete → confirm `Удалить фото профиля?` then `DELETE /senders/{slug}/photo`. Success toast `Профиль обновлён`.
- **Section B — Безопасность (2FA)** (uppercase label `Безопасность (2FA)`): `Текущий пароль 2FA` (type=password, shown only if account already has 2FA) / `Новый пароль 2FA` (+ optional hint) with **`Обновить пароль 2FA`** → `POST /senders/{slug}/2fa` (success `Пароль 2FA обновлён`). `Email для восстановления` two-step (C4): step-1 **`Отправить код подтверждения`** → `POST /2fa/recovery-email` → on `EMAIL_CONFIRMATION_SENT` transition to code state `Мы отправили код на {email}. Введите его ниже.` + code input (hint length=`code_length`) + **`Подтвердить email`** → `POST /2fa/recovery-email/confirm` (success `Email восстановления обновлён`) + `Отправить снова` ghost. Section B has NO combined save with Section A.

**Surface 4 — guardrails (C3, D-06/D-07/D-08/D-09):** compute client-side from `sender.profile_field_changed_at`:
- username/photo changed <1h ago → HARD block: disable that field's save affordance + live `mm:ss` countdown message (`Username можно менять не чаще раза в час. Попробуйте снова через {mm:ss}.` / photo variant). Also respect backend 409 `TOO_FREQUENT` (surface same copy).
- name/bio → WARNING-only advisory once before save (`Слишком частая смена имени или описания может насторожить Telegram. Продолжить?`), never block.
- warmup (`lifecycle_status==='warmup'`) OR account `<7 days` (`created_at`) → append advisory line (`Аккаунт ещё прогревается (моложе 7 дней). Резкие изменения профиля повышают риск ограничений. Продолжить?`), still not blocked.

**Errors:** map backend codes to the RU copy in §Copywriting (USERNAME_TAKEN / USERNAME_INVALID / BIO_TOO_LONG / PASSWORD_INVALID / EMAIL_INVALID / TOO_FRESH / FLOOD_WAIT / FILE_TOO_LARGE / UNSUPPORTED_FILE_TYPE / PHOTO_TOO_SMALL / PHOTO_FORMAT_INVALID). Session-expired (`auth_status!=='ok'`) → existing inline reauth link (do NOT build new reauth). Regenerate/copy TS types locally from the updated openapi if the repo vendors them; run the repo's typecheck (`bun run build` or `tsc --noEmit`) clean.

Commit ONLY the frontend changes to the SIBLING repo:
```bash
cd /root/apps/aimly/aimly-tg-outreach
git add src/routes/_authenticated/accounts.tsx   # + any small helper files you created
git commit -m "feat(accounts): account profile management — enriched row, edit modal, 2FA, guardrails

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && grep -q "Изменить профиль" src/routes/_authenticated/accounts.tsx && grep -q "Обновить профиль" src/routes/_authenticated/accounts.tsx && grep -q "recovery-email" src/routes/_authenticated/accounts.tsx</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "Изменить профиль"` and `grep -q "Обновить профиль"` in accounts.tsx both match (kebab items)
    - `grep -q "recovery-email"` in accounts.tsx matches (two-step email flow wired)
    - `grep -q "/photo"` in accounts.tsx matches (avatar served from the auth endpoint)
    - `grep -q "Сохранить профиль"` and `grep -q "Обновить пароль 2FA"` in accounts.tsx match (the two scoped section CTAs)
    - The repo typecheck/build (`bun run build` or `npx tsc --noEmit`) exits 0
    - The commit touched ONLY files under `src/` in the sibling repo (git show --stat) — nothing in the backend repo
  </acceptance_criteria>
  <done>accounts.tsx enriched per the approved UI-SPEC (row photo + @username, kebab edit/resync, two-section modal, two-step email, guardrails); typecheck clean; committed to the sibling repo only.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 3: Human verification — visual + live 2FA recovery-email round-trip</name>
  <files>N/A — verification only (no files modified)</files>
  <read_first>
    - .planning/phases/20-account-profile-management/20-VALIDATION.md (§Manual-Only Verifications — the two items this gate covers)
    - .planning/phases/20-account-profile-management/20-UI-SPEC.md (§Surfaces 1-4 — the visual contract to eyeball)
  </read_first>
  <action>
This is a BLOCKING human-verify checkpoint — no autonomous code action. Before pausing for the user, ensure the backend is deployed and the frontend is live so the user can test:
```bash
cd /root/apps/aimly/tg-outreach && docker compose up -d --build api listener
```
Then present the <what-built> + <how-to-verify> steps to the user and WAIT for the resume signal. Do not proceed or self-approve.
  </action>
  <what-built>
    Full account-profile management end-to-end: backend endpoints (profile/username/photo/resync/2FA/recovery-email) deployed, handoff regenerated, and the frontend accounts screen enriched with the avatar photo + @username row, the Изменить профиль / Обновить профиль kebab, and the two-section profile modal with the two-step recovery-email flow and the frequency guardrails.
  </what-built>
  <how-to-verify>
    Load the deployed frontend at https://aimly.agsventurelab.com → Accounts.
    1. ROW: an account with a photo shows its avatar (not initials) + `@username` under the name; an account without a photo shows initials.
    2. RESYNC (D-12): kebab → `Обновить профиль` → spinner → toast `Профиль обновлён`; if you changed the profile in the native Telegram client first, the row reflects it after resync.
    3. IDENTITY (Section A): kebab → `Изменить профиль`; change name + bio (`{n}/70` counter) → `Сохранить профиль` → toast `Профиль обновлён`. Type a username → inline `Проверяем…` → `Свободно`/`Занято`.
    4. PHOTO (D-08/D-11): upload a JPG → avatar updates. Immediately try to change photo again → hard block with `mm:ss` countdown. Upload a >5MB file → `Файл слишком большой`.
    5. USERNAME hard block (D-08): change username, then immediately try again → blocked with countdown.
    6. WARMUP advisory (D-09): on a warmup / <7-day account, saving name/bio shows the advisory but is NOT blocked.
    7. 2FA (Section B): set/change the 2FA password → toast `Пароль 2FA обновлён`; wrong current password → `Неверный текущий пароль 2FA`.
    8. RECOVERY EMAIL (MANUAL, the riskiest flow — RESEARCH MEDIUM-LOW): on a REAL test account with 2FA set, enter a new recovery email → `Отправить код подтверждения` → confirm the `Мы отправили код на {email}` state appears; retrieve the real code from that inbox → `Подтвердить email` → toast `Email восстановления обновлён`; verify the new recovery email in the native Telegram client (Settings → Privacy → Two-Step Verification).
  </how-to-verify>
  <verify>Manual — human confirms all 8 checks in <how-to-verify>. No automated command (visual + live Telegram email round-trip that cannot be mocked).</verify>
  <resume-signal>Type "approved" if all 8 checks pass, or describe the issues (which surface, expected vs actual) to drive gap-closure.</resume-signal>
  <done>User has run all 8 verification steps and typed "approved"; any issues are captured for gap-closure.</done>
</task>

</tasks>

<verification>
- openapi.json carries all 7 Phase-20 paths + SenderResponse profile fields; types regenerated.
- Frontend typecheck/build clean; kebab + modal + guardrails present.
- Human gate confirms the visual contract + the live recovery-email round-trip (the manual-only items from 20-VALIDATION).
</verification>

<success_criteria>
- PROF-09 closed: product surface live end-to-end; handoff contract regenerated; human sign-off recorded.
- Cross-repo commit isolation preserved (openapi/types → backend repo; accounts.tsx → sibling repo).
</success_criteria>

<output>
After completion, create `.planning/phases/20-account-profile-management/20-05-SUMMARY.md`.
</output>
