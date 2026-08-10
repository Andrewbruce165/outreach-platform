---
status: resolved
trigger: "почему-то кнопка с фильтрами не работает на странице /accounts"
created: 2026-08-10
updated: 2026-08-10
---

# Debug Session: accounts-filter-button-not-working

## Symptoms

- **Expected behavior:** Клик на кнопку фильтров на /accounts открывает панель/дропдаун с фильтрами (по статусу, restriction_status и т.п.)
- **Actual behavior:** Ничего — кнопка визуально не реагирует, нет реакции в UI
- **Error messages:** Не проверено пользователем (консоль браузера не проверялась)
- **Timeline:** Не уверен(а) когда началось
- **Reproduction:** Воспроизводится везде (несколько браузеров/устройств)

## Current Focus

- hypothesis: CONFIRMED — the Filters button on /accounts is a static Lovable-generated
  placeholder: it has no `onClick`, no state, and no dropdown JSX anywhere in the route.
  Nothing can happen on click by construction.
- test: static read of the button JSX + grep for any filter state/handler in accounts.tsx
  + git history of those exact lines
- expecting: if the button were ever wired, git history would show an onClick that was
  later removed, or grep would find filter state — neither exists
- next_action: DONE — user signed off on option A (build the dropdown, contacts.tsx
  pattern). Implemented, typechecked and built. NOT deployed: ./deploy-frontend.sh stays
  an explicit user-issued command.

## Evidence

- timestamp: 2026-08-10
  checked: frontend/src/routes/_authenticated/accounts.tsx:111-113 (the Filters button)
  found: |
    <button className="btn btn--ghost btn--sm" type="button">
      <Filter size={14} /> Filters
    </button>
    No onClick. No aria-expanded. No sibling dropdown. `type="button"` only, so it does
    not even submit anything.
  implication: The button is inert by construction — not a broken handler, not a runtime
    error. Browser console would be clean. This is why it is reproducible on every
    browser/device: there is no code path to fail.

- timestamp: 2026-08-10
  checked: grep -n "Filter" in accounts.tsx (whole file, 1866 lines)
  found: only 2 hits — the lucide-react import (line 14) and the button label (line 112).
    No `filtersOpen` state, no `statusFilter`, no filter predicate. The only client-side
    narrowing that exists on the page is the free-text `search` box (lines 55, 64-73),
    which works.
  implication: Zero filter implementation exists on this route. Nothing was deleted or
    regressed — it was never built.

- timestamp: 2026-08-10
  checked: git log -L 105,120:frontend/src/routes/_authenticated/accounts.tsx
  found: the button was introduced already handler-less and has never been touched since;
    surrounding commits (5c15a9b "bulk account import UI", b501065) added onClick-wired
    siblings (Import accounts, Connect account) right next to it while leaving the
    Filters button untouched and inert.
  implication: Matches the reported timeline "не уверен когда началось" — it never worked.
    Classic Lovable-generated visual placeholder that was never wired up.

- timestamp: 2026-08-10
  checked: the other 4 "Filters" buttons in the app, for a working in-repo pattern
  found: |
    - routes/_authenticated/index.tsx:512      → onClick={() => setOpen(o => !o)}  WIRED
    - routes/_authenticated/contacts.tsx:699   → onClick={() => setFiltersOpen(v => !v)}  WIRED
      (full dropdown at 720-774: absolute-positioned panel, radio-style options,
       active-filter count badge, blue label when a filter is on)
    - routes/_authenticated/campaigns.index.tsx:239 → `disabled title="Filters (v2)"` —
      an HONEST placeholder: greyed out, obviously not clickable
    - routes/_authenticated/accounts.tsx:111   → nothing  ← THE BUG
  implication: contacts.tsx is a proven, in-house pattern to copy verbatim. Also: the
    campaigns page shows the correct way to ship an unbuilt filter (`disabled`), which is
    the minimal alternative fix — accounts.tsx is the only page that renders a fully
    enabled-looking button over dead code, which is exactly why it reads as "broken".

## Eliminated

- hypothesis: JS runtime error in the click handler swallows the interaction
  evidence: there is no handler at all — nothing to throw. Console will be clean.
  timestamp: 2026-08-10

- hypothesis: dropdown opens but is invisible (z-index / overflow clipping in Topbar)
  evidence: no dropdown JSX exists in the route and no open-state variable exists;
    nothing is rendered to be clipped
  timestamp: 2026-08-10

- hypothesis: stale deploy — a working build exists but /var/www/aimly serves an old bundle
  evidence: the handler is absent in HEAD source itself, so no build (old or new) could
    contain it. Deploy state is irrelevant to this symptom.
  timestamp: 2026-08-10

- hypothesis: a regression — the filter used to work and broke
  evidence: git -L history on those lines shows the button was born without a handler and
    was never modified
  timestamp: 2026-08-10

## Resolution

- root_cause: |
    The Filters button on /accounts was never implemented. It is a Lovable-generated
    visual placeholder rendered as a fully enabled `<button type="button">` with no
    onClick, no filter state and no dropdown. It is inert by construction — this is a
    missing feature presented as a working control, not a malfunction.
- fix: |
    Option A (user-chosen at the checkpoint): built the filter dropdown, copying the
    proven in-repo pattern from contacts.tsx:696-774. All in
    frontend/src/routes/_authenticated/accounts.tsx:

    1. Module-scope filter vocabulary: `StatusFilter` ("all"|"active"|"warmup"|"paused"|
       "restricted"|"error"), `RoleFilter` ("all"|"sender"|"checker"), their option
       tables, and the predicates `matchesStatusFilter` / `matchesRoleFilter`.
       "restricted" folds limited+frozen, matching the Restricted metric card.
    2. Reusable generic `<FilterGroup>` — one labelled radio-style group (Check icon on
       the selected row, blue + semibold), so Status and Role share one implementation.
    3. Wired the button: `onClick={() => setFiltersOpen(v => !v)}` + `aria-expanded`,
       blue label and a count badge when any filter is active — plus an absolute
       dropdown panel (zIndex 50) behind an `ob__menuScrim` (z 40, the class already used
       by this file's card menus) that closes on outside click. Selecting an option
       deliberately keeps the panel open, since there are two groups to set.
    4. Client-side filtering only — one `allSenders.filter()` where search and both
       filters compose (row must satisfy every active narrowing). No backend/query-param
       change, so no new network surface.
    5. Metric cards refactored to compute their counts through the same
       `matchesStatusFilter` predicate (`countBy`), so a card and its filter bucket can
       never drift apart. Cards stay whole-fleet totals and are NOT narrowed.
    6. Empty state now distinguishes search-only / filters-only / both, and is gated on
       `isNarrowed` so it can't render when nothing is being narrowed.
- verification: |
    - Root symptom gone by construction: the button now has an onClick and a rendered
      panel; previously there was no code path at all.
    - `bunx tsc --noEmit`: 0 errors in accounts.tsx. Three errors remain in
      __root.tsx / _authenticated.tsx / settings.tsx (all about `{to:"/login"}` missing a
      required `search` param) — confirmed PRE-EXISTING: `git diff --name-only HEAD` shows
      those three files are untouched by this change.
    - `bun run build` (docker oven/bun:1, same stage deploy-frontend.sh uses): SUCCESS.
    - Built chunk dist/client/assets/accounts-*.js contains the new strings
      ("Warm-up"/"Restricted", "Sender"/"Checker", "selected filters") — proof the wiring
      is actually in the emitted bundle, not just the source.
    - NOT deployed. /var/www/aimly still serves the old bundle; ./deploy-frontend.sh is
      the user's explicit call. PENDING LIVE CHECK after deploy: click Filters → panel
      opens; pick Restricted → list narrows to limited+frozen; badge shows 1 and label
      turns blue; pick Role=Checker → badge shows 2; click outside → closes.
- files_changed: ["frontend/src/routes/_authenticated/accounts.tsx"]
