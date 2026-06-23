---
phase: 08-pool-management-and-even-distribution
plan: 04
type: execute
wave: 4
depends_on: [03]
autonomous: false
cross_repo: true
files_modified:
  # Backend repo (this repo) — generated artifact + types are committed where the script writes them
  - lovable-handoff/openapi.json
  # Frontend repo (SIBLING — AGS-Venture-Lab/aimly-tg-outreach) — commits land in that repo, NOT this one
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts
  - /root/apps/aimly/aimly-tg-outreach/src/types/api.ts
requirements: [POOL-09]
must_haves:
  truths:
    - "On a campaign page, the Senders/Пул panel lists attached senders with a remove control and offers eligible senders to add via multiselect/chips"
    - "Adding a sender calls POST /campaigns/{id}/senders and the panel refreshes; removing calls DELETE and refreshes"
    - "Locked senders show their locking campaign and cannot be added/removed blindly"
    - "409 errors (SENDER_LOCK_CONFLICT / MIN_POOL_GUARD / DETACH_BLOCKED_PENDING) render as human-readable messages in the existing actionError banner"
    - "openapi.json and src/types/api.ts include the two new /senders endpoints"
    - "Honors decisions D-10 (a dedicated Senders/Пул panel on the campaign page, works for draft and running), D-11 (multiselect/chips add+remove, locked-account display from attached_senders[].locked_by_campaign_name, human-readable 409s), D-12 (wizard sender-selection in campaigns.new.tsx stays; PATCH still ignores sender_ids)"
  artifacts:
    - path: /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx
      provides: "interactive Senders/Пул panel (attach/detach)"
      contains: "campaigns/${id}/senders"
    - path: /root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts
      provides: "MIN_POOL_GUARD + DETACH_BLOCKED_PENDING keys + fixed SENDER_LOCK_CONFLICT formatter"
      contains: "MIN_POOL_GUARD"
    - path: lovable-handoff/openapi.json
      provides: "schema for POST/DELETE /campaigns/{id}/senders"
      contains: "/senders"
  key_links:
    - from: campaigns.$id.tsx (attachMut/detachMut)
      to: "/api/v1/campaigns/{id}/senders"
      via: "TanStack useMutation + invalidateQueries(['campaign', id])"
      pattern: "campaigns/\\$\\{id\\}/senders"
    - from: campaigns.$id.tsx
      to: error-codes.ts
      via: "errMsg(e) → errorMessageFromEnvelope → CODE_MAP"
      pattern: "errMsg"
---

<objective>
Upgrade the existing read-only "Senders" section on the campaign page into an interactive Senders/Пул panel (add/remove, locked display, human-readable 409s), fix the pre-existing SENDER_LOCK_CONFLICT formatter, and regenerate the OpenAPI handoff so the frontend types include the two new endpoints. Implements D-10/D-11/D-12 (POOL-09).

Purpose: gives the user self-service pool management on draft and running campaigns — the visible payoff of the backend work in Plans 02/03.
Output: interactive panel in the SIBLING repo `aimly-tg-outreach`, corrected error-codes, regenerated openapi.json + src/types/api.ts.

CROSS-REPO: backend (openapi.json regen + commit) lands in THIS repo (Andrewbruce165/outreach-platform). Frontend changes (campaigns.$id.tsx, error-codes.ts, src/types/api.ts) live in the SIBLING repo `/root/apps/aimly/aimly-tg-outreach` and commit to AGS-Venture-Lab/aimly-tg-outreach. Keep the two commits separate.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/phases/08-pool-management-and-even-distribution/08-RESEARCH.md
@.planning/phases/08-pool-management-and-even-distribution/08-PATTERNS.md

@/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx
@/root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts

<interfaces>
<!-- Frontend analogs to copy. Verified line numbers from RESEARCH/PATTERNS this session. -->
campaigns.$id.tsx:
- existing read-only Senders <section className="card"> mapping c.attached_senders[] with locked_by_campaign_name in var(--danger)  (~lines 361-407)
- lifecycleMut pattern (useMutation + qc.invalidateQueries(['campaign', id]) + setActionError(errMsg(e)))  (~lines 89-104)
- errMsg(e) helper  (~lines 29-33); actionError state + banner (~lines 57-58, 103)

campaigns.new.tsx:
- AccountsStep toggle block: checkbox + avatar avatar--sm + status pill, --tg-blue tokens  (~lines 1037-1093)
- workspace senders query: useQuery(['senders'], () => api<{senders: Sender[]}>('/api/v1/senders'))  (~lines 141-143)

src/lib/error-codes.ts:
- CODE_MAP (~lines 4-25); SENDER_LOCK_CONFLICT currently reads d.name/d.other (WRONG — backend emits conflicts:[...])  (~lines 16-19)
src/lib/api.ts: routes detail.code through errorMessageFromEnvelope (~lines 98-100)

Backend contract (from Plan 03):
- POST /api/v1/campaigns/{id}/senders  body {sender_id}  → CampaignResponse  (409 SENDER_LOCK_CONFLICT {conflicts:[{sender_id,campaign_id,campaign_name}]})
- DELETE /api/v1/campaigns/{id}/senders/{sid} → CampaignResponse (409 MIN_POOL_GUARD / DETACH_BLOCKED_PENDING {code,message})
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Regenerate OpenAPI handoff (openapi.json + src/types/api.ts)</name>
  <files>lovable-handoff/openapi.json, /root/apps/aimly/aimly-tg-outreach/src/types/api.ts</files>
  <read_first>
    - scripts/export-handoff.sh (the regen mechanism — boots db+api, pulls /openapi.json, regenerates types; do NOT hand-edit generated files)
    - 08-RESEARCH.md §"OpenAPI / Lovable Handoff" and §Pitfall 7 (never hand-edit)
  </read_first>
  <action>
    With Plan 03's endpoints merged, run `scripts/export-handoff.sh` from the backend repo root. It boots `docker compose up -d db api`, fetches `/openapi.json` from inside the api container, writes `lovable-handoff/openapi.json` via jq, regenerates the frontend `src/types/api.ts` via `npx openapi-typescript@7`, and validates the project title. Do NOT hand-edit either generated file. If the api container needs the new code, ensure `docker compose up -d --build api` first (restart does not pick up code changes).
  </action>
  <verify>
    <automated>grep -c "senders" lovable-handoff/openapi.json && python -c "import json,sys; d=json.load(open('lovable-handoff/openapi.json')); paths=[p for p in d['paths'] if p.endswith('/senders') or '/senders/' in p]; print(paths); sys.exit(0 if paths else 1)"</automated>
  </verify>
  <acceptance_criteria>
    - `lovable-handoff/openapi.json` contains a path ending `/senders` (POST) and a path `/senders/{...}` (DELETE) under the campaigns prefix.
    - `src/types/api.ts` in the sibling repo regenerated (git diff in sibling shows the two new operations).
    - Neither generated file hand-edited (produced solely by scripts/export-handoff.sh).
  </acceptance_criteria>
  <done>OpenAPI + types include the two new endpoints; produced by the script, not by hand.</done>
</task>

<task type="auto">
  <name>Task 2: Fix error-codes.ts (add MIN_POOL_GUARD/DETACH_BLOCKED_PENDING, fix SENDER_LOCK_CONFLICT)</name>
  <files>/root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts</files>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts:4-25 (CODE_MAP; current SENDER_LOCK_CONFLICT at :16-19 reads d.name/d.other)
    - /root/apps/aimly/aimly-tg-outreach/src/lib/api.ts:98-100 (how detail.code routes through errorMessageFromEnvelope)
    - 08-PATTERNS.md §"src/lib/error-codes.ts" (exact replacement formatter + new keys)
  </read_first>
  <action>
    In the sibling repo's `src/lib/error-codes.ts` CODE_MAP:
    - Add `MIN_POOL_GUARD: () => "Can't remove the last account from a running campaign. Pause it first."`
    - Add `DETACH_BLOCKED_PENDING: () => "This account still has un-sent contacts. Pause the campaign or wait for the queue to drain."`
    - FIX `SENDER_LOCK_CONFLICT` to read `detail.conflicts[]` (array of `{campaign_name}`) instead of `d.name`/`d.other`: map conflicts → campaign_name, join with ", "; fall back to a generic "Account is locked by another running campaign." when the array is empty. (This is a latent /start bug Phase 8 surfaces — see RESEARCH §Open Questions #3.)
    Keep the existing CODE_MAP entry style. Russian or English copy per existing convention in that file.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && grep -q "MIN_POOL_GUARD" src/lib/error-codes.ts && grep -q "DETACH_BLOCKED_PENDING" src/lib/error-codes.ts && grep -q "conflicts" src/lib/error-codes.ts && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - CODE_MAP has MIN_POOL_GUARD and DETACH_BLOCKED_PENDING keys.
    - SENDER_LOCK_CONFLICT reads `detail.conflicts` (no longer `d.name`/`d.other`).
    - `npx tsc --noEmit` (or the repo's typecheck) passes for the changed file (no type errors introduced).
  </acceptance_criteria>
  <done>Three 409 codes render human-readably; SENDER_LOCK_CONFLICT formatter fixed.</done>
</task>

<task type="auto">
  <name>Task 3: Make the Senders/Пул panel interactive (attach/detach)</name>
  <files>/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx</files>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx:361-407 (existing read-only Senders section to upgrade), :89-104 (lifecycleMut), :29-33 + :57-58 + :103 (errMsg + actionError banner)
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx:1037-1093 (AccountsStep toggle UI to copy), :141-143 (senders query)
    - 08-PATTERNS.md §"campaigns.$id.tsx" (attachMut/detachMut + multiselect + design tokens)
    - 08-RESEARCH.md §"Frontend (D-10/D-11)"
  </read_first>
  <action>
    In the sibling repo's `campaigns.$id.tsx`, upgrade the existing read-only Senders `<section className="card">` into an interactive panel (inline on the page, NOT a modal — D-10):
    - Add a workspace senders query mirroring the wizard: `useQuery(['senders'], () => api<{senders: Sender[]}>('/api/v1/senders'))`.
    - Add `attachMut` (POST `/api/v1/campaigns/${id}/senders` body `{sender_id}`) and `detachMut` (DELETE `/api/v1/campaigns/${id}/senders/${sid}`) mirroring `lifecycleMut` (useMutation + `qc.invalidateQueries({queryKey:['campaign', id]})` onSuccess + `setActionError(errMsg(e))` onError). Reuse the EXISTING actionError banner — no new error surface.
    - Render each attached sender `<li>` with a remove button firing detachMut; surface `locked_by_campaign_name` (already shown in danger color) and disable remove for locked ones.
    - Add multiselect/chips for adding: copy the AccountsStep toggle block (checkbox + `avatar avatar--sm` + status `pill`); list workspace senders filtered to exclude already-attached and `status === "error"`; clicking an eligible sender fires attachMut. Disable add for locked senders.
    - Works for draft, paused, and running campaigns (D-10). Reuse only existing design tokens (`card`, `pill`/`pill--green`/`pill--red`, `avatar avatar--sm`, `--tg-blue*`, `--bg-soft`, `--danger`, `--border`) — no new CSS system. Wizard sender-selection in campaigns.new.tsx stays unchanged (D-12).
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && grep -q "campaigns/\${id}/senders" src/routes/_authenticated/campaigns.\$id.tsx && (npm run typecheck 2>/dev/null || npx tsc --noEmit) && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - campaigns.$id.tsx defines attachMut (POST .../senders) and detachMut (DELETE .../senders/{sid}) using invalidateQueries(['campaign', id]).
    - The panel filters out already-attached and `status === "error"` senders from the add list, and disables add/remove for locked senders.
    - Uses the existing actionError banner via errMsg(e) (no new error component).
    - No new CSS system added (only existing tokens reused).
    - Repo typecheck (`tsc --noEmit`) passes.
  </acceptance_criteria>
  <done>Interactive panel attaches/detaches and surfaces locked + 409 states via the existing banner; typecheck clean.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 4: UAT — verify the Senders/Пул panel in-browser (POOL-09)</name>
  <what-built>
    Interactive Senders/Пул panel on the campaign page (add via multiselect/chips, remove per row), locked-sender display, and human-readable 409 rendering (sender-locked / min-pool / detach-blocked). Backend endpoints POST/DELETE /campaigns/{id}/senders are live; openapi.json + types regenerated; error-codes.ts fixed.
  </what-built>
  <how-to-verify>
    1. Open a campaign page at https://aimly.agsventurelab.com (a draft or paused campaign with ≥1 sender available to add).
    2. In the Senders/Пул panel, add a 2nd eligible sender via the multiselect/chips → confirm it appears in the attached list after refresh.
    3. Try removing the LAST sender of a RUNNING campaign → confirm a human-readable "Can't remove the last account from a running campaign. Pause it first." appears in the action banner (MIN_POOL_GUARD).
    4. Try adding a sender that is locked by another running campaign → confirm "Account is already in running campaign(s): {name}..." appears (SENDER_LOCK_CONFLICT, array-based).
    5. On a running campaign where a sender still has un-sent contacts, try removing it → confirm "This account still has un-sent contacts..." appears (DETACH_BLOCKED_PENDING).
    6. Confirm locked senders are visibly marked and their add/remove control is disabled.
  </how-to-verify>
  <files>/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx</files>
  <action>Human-in-the-loop UAT: this task is a blocking manual checkpoint. The executor must PAUSE and ask the user to perform the six in-browser verification steps above against the deployed panel, then wait for the resume signal. No code is written in this task; it confirms POOL-09 behavior that cannot be asserted by an automated test (visual + interaction in the Lovable frontend).</action>
  <verify>Manual: user performs the 6 steps in how-to-verify and confirms each renders/behaves as described. No automated command (frontend visual/interaction UAT).</verify>
  <done>User replies "approved" after all 6 steps pass; any reported issues are fixed and re-verified before the plan is marked complete.</done>
  <acceptance_criteria>
    - All 6 steps behave as described; 409s render human-readably (not raw JSON / not "[object Object]").
    - The wizard sender-selection on campaign create is unchanged.
  </acceptance_criteria>
  <resume-signal>Type "approved" or describe issues to fix.</resume-signal>
</task>

</tasks>

<threat_model>
ASVS L1 surface for the frontend panel (presentation layer; primary authz is enforced server-side in Plan 03):
- **T1 — Client trusting attach/detach without server authz (V4 Access Control).** The panel issues requests but ALL isolation/lock/guard enforcement is server-side (Plan 03 _load_campaign + _validate_workspace_owns_senders + _check_sender_lock + guards). The UI MUST NOT assume success — it renders server 409/404 via the actionError banner. Disabling add/remove for locked senders is a UX nicety, not a security control.
- **T2 — Error envelope leaking internals (V7 Logging / info exposure).** Mitigation: error-codes.ts maps known codes to fixed human strings; it reads only `detail.conflicts[].campaign_name` (workspace-scoped names the backend already returns), not stack traces or IDs of foreign workspaces.
- **T3 — Generated-file tampering / drift (supply chain / integrity).** Mitigation: openapi.json + src/types/api.ts are produced solely by scripts/export-handoff.sh (Pitfall 7), never hand-edited, keeping the client contract honest.
No new auth, secrets, or network surface introduced by the frontend.
</threat_model>

<verification>
- Backend repo: `grep -c senders lovable-handoff/openapi.json` > 0 and the two paths present.
- Sibling repo typecheck passes (`tsc --noEmit`).
- Human UAT (Task 4) approved.
- Two-repo commit discipline: openapi.json commit → Andrewbruce165/outreach-platform; campaigns.$id.tsx + error-codes.ts + src/types/api.ts commit → AGS-Venture-Lab/aimly-tg-outreach.
</verification>

<success_criteria>
- Interactive Senders/Пул panel live for draft/paused/running; add/remove + locked display + human-readable 409s.
- SENDER_LOCK_CONFLICT formatter fixed (array-based); MIN_POOL_GUARD + DETACH_BLOCKED_PENDING mapped.
- openapi.json + types regenerated via the script; wizard selection unchanged (D-12).
- POOL-09 verified by human UAT.
</success_criteria>

<output>
After completion, create `.planning/phases/08-pool-management-and-even-distribution/08-04-SUMMARY.md`
</output>
