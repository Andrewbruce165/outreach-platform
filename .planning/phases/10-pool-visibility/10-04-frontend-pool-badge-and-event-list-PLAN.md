---
phase: 10-pool-visibility
plan: 04
type: execute
wave: 4
depends_on: [03]
files_modified:
  - "SIBLING REPO /root/apps/aimly/aimly-tg-outreach: campaign page pool badge + account page event-list + regenerated openapi.json/types"
  - lovable-handoff/openapi.json
autonomous: false
requirements: [POOLV-03, POOLV-04]
must_haves:
  truths:
    - "Campaign page shows a 3-state pool badge: green (all active), yellow (K of N paused until T), red (all paused) — derived on the frontend from numeric pool_health"
    - "Account page shows a mini list of restriction events newest-first (type, source, restricted_until, activity-slice summary)"
  artifacts:
    - path: "SIBLING /root/apps/aimly/aimly-tg-outreach (campaign page component)"
      provides: "3-state pool badge reading pool_health {active, paused, total, earliest_resume_at}"
    - path: "SIBLING /root/apps/aimly/aimly-tg-outreach (account page component)"
      provides: "restriction event mini-list off GET /senders/{slug}/restriction-events"
  key_links:
    - from: "campaign page badge"
      to: "CampaignResponse.pool_health"
      via: "fetch campaign → derive green/yellow/red from paused vs total"
      pattern: "pool_health"
    - from: "account page event-list"
      to: "GET /senders/{slug}/restriction-events"
      via: "react-query fetch, newest-first list"
      pattern: "restriction-events"
---

<objective>
Мини-UI фазы (D-11) в СИБЛИНГ-репозитории фронта `aimly-tg-outreach`: (1) 3-состояный бейдж пула на странице кампании — главный UX-сигнал «частичная пауза» (🟡 K из N на паузе до T); (2) мини-список restriction-событий на странице аккаунта. Следует кросс-репо + human-UAT паттерну Phase 08-04 (бэкенд-pytest для бейджа/списка нет — ручная верификация).

Purpose: Сделать видимой частичную паузу кампании и историю ограничений по аккаунту. Богатый агрегат-дашборд отложен в backlog (D-11) — здесь только бейдж + мини-список.
Output: компоненты в сиблинг-репо + регенерированный openapi.json/types. Бэкенд (pool_health + endpoint) уже готов планом 03.

CRITICAL cross-repo: фронт — ОТДЕЛЬНЫЙ репозиторий `/root/apps/aimly/aimly-tg-outreach` (origin AGS-Venture-Lab/aimly-tg-outreach). Коммиты внутри него летят в тот репо. `.planning/` коммиты — в Andrewbruce165/outreach-platform. НЕ путать. Лавабл иногда расходится с openapi — регенерировать через export-handoff, НЕ править руками (Pitfall 5).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/STATE.md
@.planning/phases/10-pool-visibility/10-RESEARCH.md
@.planning/codebase/INTEGRATIONS.md

<interfaces>
<!-- Backend contracts (shipped by Plan 03) the frontend consumes. -->

CampaignResponse.pool_health = {active: int, paused: int, total: int, earliest_resume_at: string|null}
Badge derivation (FRONTEND ONLY — no server badge field):
  paused === 0           → 🟢 green  "пул активен"
  0 < paused < total     → 🟡 yellow "K из N на паузе до {earliest_resume_at}"
  paused === total && total>0 → 🔴 red "весь пул на паузе"
  (OQ#4: for frozen senders earliest_resume_at is a recheck horizon — wording "до проверки в T", not "возобновится в T")

attached_senders[].restriction_status ("none"|"spam_limited"|"frozen") + .restricted_until — for the per-sender chip display.

GET /senders/{slug}/restriction-events → list of {id, event_type, source, category, restricted_until, raw_text, activity_slice, proxy, created_at}, newest-first. event_type ∈ {spam_limited, frozen, flood_wait, cleared, banned, extension, privacy_restricted}.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Regenerate openapi.json/types + build pool badge and event-list components in sibling repo</name>
  <read_first>
    - .planning/phases/08-pool-management-and-even-distribution/08-04-frontend-pool-panel-PLAN.md (cross-repo pattern: where the Senders/Пул panel lives, how attachMut/invalidateQueries are wired, error-codes.ts, OpenAPI regen via export-handoff — the exact precedent to mirror)
    - lovable-handoff/openapi.json (current handoff spec — regenerate, do NOT hand-edit)
    - .planning/codebase/INTEGRATIONS.md (two-repo topology + how the frontend consumes the API)
    - /root/apps/aimly/aimly-tg-outreach: the campaign detail page + account/sender detail page components (locate the existing Senders/Пул panel from 08-04 — the pool badge sits near it; locate the sender/account page for the event-list)
    - .planning/phases/10-pool-visibility/10-RESEARCH.md §Pitfall 5 (cross-repo openapi drift) + Open Q #4 (frozen wording)
  </read_first>
  <action>
    Step 1 (backend repo): regenerate lovable-handoff/openapi.json via the export-handoff script used in Phase 05.1/08-04 (NOT hand-edited) so pool_health + attached_senders restriction fields + the /senders/{slug}/restriction-events endpoint appear. Regenerate the frontend TS types from it.

    Step 2 (SIBLING repo /root/apps/aimly/aimly-tg-outreach — commits go to AGS-Venture-Lab/aimly-tg-outreach): On the campaign detail page, add a 3-state pool badge near the existing Senders/Пул panel (08-04). Derive state on the frontend from pool_health: paused===0 → green "пул активен"; 0<paused<total → yellow "K из N на паузе до {earliest_resume_at}" (use OQ#4 wording "до проверки в T" for the recheck horizon); paused===total&&total>0 → red "весь пул на паузе". Use the enriched attached_senders[].restriction_status/restricted_until to show which specific senders are paused (chip/row indicator). On the account/sender detail page, add a mini list of restriction events fetched via react-query from GET /senders/{slug}/restriction-events, newest-first, showing event_type, source, restricted_until and a one-line activity_slice summary (e.g. "12 отпр./час, 138 уник. контактов/24ч"). Mirror the data-fetching/query-invalidation conventions established in 08-04 (useQuery keyed by slug/campaign id). Reconcile onto origin/main if Lovable has concurrent commits (rebase, drop no Lovable commit — as 08-04 did).
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && (bun run tsc --noEmit 2>/dev/null || npx tsc --noEmit) && echo TSC_CLEAN</automated>
  </verify>
  <acceptance_criteria>
    - `lovable-handoff/openapi.json` contains `pool_health` under the campaign response and a `/senders/{slug}/restriction-events` path (regenerated, not hand-edited): `grep -q "pool_health" lovable-handoff/openapi.json` and `grep -q "restriction-events" lovable-handoff/openapi.json` both succeed.
    - Sibling repo typechecks clean (tsc --noEmit exits 0) — TSC_CLEAN printed.
    - Badge state is computed in the frontend from numeric pool_health (no reliance on a server color field); the three branches (green/yellow/red) are present in the component.
    - Event-list fetches from the restriction-events endpoint and renders newest-first.
  </acceptance_criteria>
  <done>openapi/types regenerated; pool badge (3 states) + account event-list built in the sibling repo; tsc clean.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 2: Human UAT — pool badge 3 states + account event-list</name>
  <files>SIBLING /root/apps/aimly/aimly-tg-outreach (campaign + account pages); lovable-handoff/openapi.json</files>
  <action>Verification-only checkpoint — no code is written here. The implementer pauses and the reviewer manually exercises the badge + event-list per how-to-verify. All automation was completed in Task 1.</action>
  <what-built>
    3-state pool badge on the campaign page (green/yellow/red, derived from pool_health) + per-sender restriction chips, and a mini restriction-event list on the account page (newest-first off GET /senders/{slug}/restriction-events). Backend pool_health + endpoint shipped in Plan 03; frontend in sibling repo aimly-tg-outreach.
  </what-built>
  <how-to-verify>
    1. Attach ≥2 senders to a running campaign. Open the campaign page → badge is 🟢 green "пул активен".
    2. Force one sender into spam_limited with a future restricted_until (e.g. via a SpamBot-reconcile or a manual UPDATE in the test/staging DB) → reload campaign page → badge turns 🟡 yellow showing "K из N на паузе до T"; the paused sender is marked in the attached-senders list, the others stay active.
    3. Force ALL attached senders into a restriction → badge turns 🔴 red "весь пул на паузе".
    4. Clear the restriction → badge returns to 🟢 green.
    5. Open the account/sender page for a sender that has logged events → the mini list shows restriction events newest-first with type / source / restricted_until and a short activity-slice summary; a freeze→extend→clear sequence reads as a clean chronology (no 37-tick noise — D-01).
    6. Confirm a cleared/healthy account shows no spurious "paused" state and its event history is readable.
  </how-to-verify>
  <read_first>
    - .planning/phases/10-pool-visibility/10-VALIDATION.md §Manual-Only Verifications (POOLV-03/04 instructions)
    - .planning/phases/08-pool-management-and-even-distribution/08-04-frontend-pool-panel-PLAN.md (UAT format precedent)
  </read_first>
  <acceptance_criteria>
    - Reviewer confirms badge renders all three states correctly (green all-active, yellow partial with "K из N ... до T", red all-paused) and reflects live pool_health changes.
    - Reviewer confirms the account-page event-list renders restriction history newest-first with type/source/restricted_until + activity-slice summary, and that a freeze→extension→clear sequence reads as a clean chronology without per-tick noise.
    - No cross-tenant data visible (only the current workspace's senders/events).
  </acceptance_criteria>
  <verify>Manual UAT per how-to-verify (no automated test — frontend lives in the sibling repo; matches Phase 08-04 cross-repo human-verify pattern).</verify>
  <done>Reviewer types "approved" after confirming the 3-state badge, per-sender chips, and account-page event-list all render correctly with no cross-tenant leakage.</done>
  <resume-signal>Type "approved" or describe issues to fix.</resume-signal>
</task>

</tasks>

<threat_model>
ASVS L1 (block_on=high). Frontend plan — focus areas:
- **No cross-tenant leakage in the UI:** the event-list reads GET /senders/{slug}/restriction-events which is workspace-scoped server-side (Plan 03 auth_dep + _load_sender_by_slug). The frontend must send the auth token (existing react-query auth wrapper) and never accept a workspace id from the client. UAT step confirms only the current workspace's data is visible.
- **No secrets surfaced in the UI:** the event-list shows raw_text (human-facing error/@SpamBot text — no secrets, guaranteed by Plan 02) and an activity_slice summary; the proxy field, if displayed, is the workspace's own config (same-tenant). Do NOT render any session/API-key material (none is in the payload).
- **Presentation-only on the client:** badge color is derived client-side from numeric pool_health — no trust boundary crossed; the server stays presentation-free.
- **Cross-repo hygiene:** sibling-repo commits go to AGS-Venture-Lab/aimly-tg-outreach; openapi.json regenerated (not hand-edited) to avoid spec drift (Pitfall 5).
</threat_model>

<verification>
- openapi.json/types regenerated (not hand-edited); pool_health + endpoint present.
- Sibling repo tsc clean.
- Human UAT: 3-state badge + per-sender chips + account event-list verified live (matches VALIDATION.md Manual-Only rows POOLV-03/04).
</verification>

<success_criteria>
- POOLV-03: campaign-page 3-state pool badge (green/yellow/red) from numeric pool_health, partial-pause visible.
- POOLV-04: account-page mini restriction-event list, newest-first, clean chronology (D-01).
- Cross-repo done right (sibling repo commit + regenerated handoff spec); human UAT signed off.
</success_criteria>

<output>
After completion, create `.planning/phases/10-pool-visibility/10-04-SUMMARY.md`
</output>
