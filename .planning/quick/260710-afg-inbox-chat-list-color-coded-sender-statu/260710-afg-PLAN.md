---
phase: quick-260710-afg
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - frontend/src/lib/sender-health.ts
  - frontend/src/routes/_authenticated/inbox.tsx
autonomous: true
requirements: [AFG-01]
must_haves:
  truths:
    - "In the inbox chat list, the 'via @nickname' sender line is colored green when the sender account is active/healthy"
    - "The sender line is colored yellow when the sender is spam-limited, paused/resting, or warming up (отлежка)"
    - "The sender line is colored red when the sender needs re-authorization (session expired) or is frozen/blocked/errored"
    - "The color semantics are derived from the SAME sender fields the TG Accounts page uses (auth_status + status), not a new invented scheme"
  artifacts:
    - path: "frontend/src/lib/sender-health.ts"
      provides: "deriveSenderHealth(sender) → 3-state traffic-light bucket + CSS color token, mirroring accounts.tsx status semantics"
      exports: ["deriveSenderHealth", "SenderHealth"]
    - path: "frontend/src/routes/_authenticated/inbox.tsx"
      provides: "Inbox ConvList sender line colored by deriveSenderHealth, sender looked up by slug from the already-fetched senders list"
  key_links:
    - from: "frontend/src/routes/_authenticated/inbox.tsx (ConvList)"
      to: "frontend/src/lib/sender-health.ts"
      via: "import deriveSenderHealth + lookup sender by c.sender_slug in senders prop"
      pattern: "deriveSenderHealth"
---

<objective>
Color-code the sender line ("via @nickname — phone") in the inbox conversation list by the sender account's health, so the user can see at a glance which sender accounts are alive and which are dead:
- **green** — account active/healthy
- **yellow** — spam-limited or resting/cooldown/warmup (отлежка)
- **red** — needs re-authorization (session expired) or frozen/blocked

Purpose: quick operational visibility in the inbox without opening the TG Accounts page.
Output: a shared `deriveSenderHealth` helper (3-state collapse of the exact fields the Accounts page reads) + the inbox list sender line wired to it.

**No backend change and no migration needed.** The inbox page already fetches the full workspace senders list (`GET /api/v1/senders`) and passes it into `ConvList` (currently only used for the sender-filter dropdown). Each conversation carries `sender_slug`, so we look the sender up client-side and read its `auth_status` / `status`. All required fields already exist on `SenderResponse`.
</objective>

<context>
@.planning/STATE.md

# Sender status semantics — MIRROR these, do not invent a new scheme.
# From frontend/src/routes/_authenticated/accounts.tsx:
#
#   SENDER_STATUS_STYLE (dot colors, keyed by sender.status):
#     active  → var(--success)      (GREEN)
#     warmup  → var(--tg-blue)      (blue)
#     paused  → var(--text-muted)   (grey)
#     limited → var(--warning)      (YELLOW/orange)  ← spam-limited
#     error   → var(--danger)       (RED)
#     frozen  → var(--danger)       (RED)
#
#   Re-auth check (priorityTier / needsReauth): sender.auth_status !== "ok"
#     → "Action required · session expired" → RED (takes precedence over status).

<interfaces>
From frontend/src/types/api.ts — SenderResponse (fields this plan reads):
```typescript
status: "active" | "warmup" | "paused" | "error" | "limited" | "frozen";
auth_status: string;   // "ok" when session is healthy; anything else = re-auth needed
slug: string;
phone: string;
```

From frontend/src/types/api.ts — ConversationResponse (row in the inbox list):
```typescript
sender_id: string;
sender_slug?: string | null;   // key to look up the sender
status: string;                // conversation status (unrelated to sender health)
```

Inbox wiring already present (frontend/src/routes/_authenticated/inbox.tsx):
- InboxPage runs `sendersQ = useQuery(... "/api/v1/senders")` and passes `senders={senders}` into `<ConvList>`.
- `ConvList` already declares the `senders: Sender[]` prop (used today only for the sender-filter <select>).
- The sender line is rendered inside the `items.map((c) => ...)` block, currently:
  ```tsx
  {c.sender_slug && (
    <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 4, ... }}>
      via @{c.sender_slug}
    </div>
  )}
  ```
  (around lines 973–986). This `<div>`'s `color` is what must become health-driven.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create shared deriveSenderHealth helper</name>
  <files>frontend/src/lib/sender-health.ts</files>
  <action>
Create a new module `frontend/src/lib/sender-health.ts` exporting a single 3-state traffic-light derivation that MIRRORS the semantics already used by accounts.tsx (do NOT invent new status values):

```typescript
import type { components } from "@/types/api";

type Sender = components["schemas"]["SenderResponse"];

export type SenderHealth = "green" | "yellow" | "red";

export interface SenderHealthInfo {
  health: SenderHealth;
  /** CSS var token for the line color. */
  color: string;
  /** Short human label for a title/tooltip, RU (matches accounts.tsx tone). */
  label: string;
}

/**
 * Collapse a sender's account state into a 3-color traffic light.
 * Semantics mirror frontend/src/routes/_authenticated/accounts.tsx:
 *   - RED   (re-auth needed OR hard-dead): auth_status !== "ok"  → session expired,
 *           OR status "frozen" / "error"  → blocked/frozen.
 *           auth check takes precedence, exactly like accounts.tsx priorityTier.
 *   - YELLOW (recoverable / resting / отлежка): status "limited" (spam-limited),
 *           "paused" (resting), or "warmup" (warming up).
 *   - GREEN  (healthy): status "active".
 */
export function deriveSenderHealth(sender: Sender): SenderHealthInfo {
  // Re-auth takes precedence (accounts.tsx: auth_status !== "ok" ⇒ tier 0 / "session expired").
  if (sender.auth_status !== "ok") {
    return { health: "red", color: "var(--danger)", label: "Требует повторной авторизации" };
  }
  switch (sender.status) {
    case "frozen":
      return { health: "red", color: "var(--danger)", label: "Заморожен" };
    case "error":
      return { health: "red", color: "var(--danger)", label: "Ошибка аккаунта" };
    case "limited":
      return { health: "yellow", color: "var(--warning)", label: "Спам-лимит" };
    case "paused":
      return { health: "yellow", color: "var(--warning)", label: "На паузе (отлёжка)" };
    case "warmup":
      return { health: "yellow", color: "var(--warning)", label: "Прогрев" };
    case "active":
      return { health: "green", color: "var(--success)", label: "Активен" };
    default:
      // Unknown/future status → neutral, never crash.
      return { health: "yellow", color: "var(--warning)", label: sender.status };
  }
}
```

Notes:
- Use the CSS vars `--success` / `--warning` / `--danger` — all three exist in frontend/src/styles.css and are used across accounts.tsx.
- Keep this the SINGLE source for the 3-color collapse; the inbox consumes it in Task 2.
  </action>
  <verify>
    <automated>cd frontend && bun run build 2>&1 | tail -5</automated>
  </verify>
  <done>Module compiles; `deriveSenderHealth` and `SenderHealth` are exported; mapping matches the accounts.tsx status/auth semantics documented above.</done>
</task>

<task type="auto">
  <name>Task 2: Color the inbox sender line by sender health</name>
  <files>frontend/src/routes/_authenticated/inbox.tsx</files>
  <action>
Wire the helper into `ConvList` (the left conversation list). The `senders: Sender[]` prop is already passed in — no new prop, no new query, no backend call.

1. Add the import at the top of the file:
   ```tsx
   import { deriveSenderHealth } from "@/lib/sender-health";
   ```

2. Inside `ConvList`, before the `return (`, build a slug→sender lookup once per render (memoized) so the per-row lookup is O(1) and avoids re-scanning the senders array for every conversation:
   ```tsx
   const senderBySlug = useMemo(() => {
     const m = new Map<string, Sender>();
     for (const s of senders) m.set(s.slug, s);
     return m;
   }, [senders]);
   ```
   (`useMemo` is already imported in this file.)

3. In the `items.map((c) => ...)` block, replace the existing sender-line `<div>` (currently `color: "var(--text-muted)"`, rendering `via @{c.sender_slug}`) with a health-colored version. Look the sender up by slug; if found, color the line by `deriveSenderHealth(sender).color` and append the phone to match the "via @nickname — phone" line the user expects; if not found, fall back to the current muted style and slug-only text:
   ```tsx
   {c.sender_slug && (() => {
     const s = senderBySlug.get(c.sender_slug);
     const info = s ? deriveSenderHealth(s) : null;
     return (
       <div
         title={info ? info.label : undefined}
         style={{
           fontSize: 11.5,
           color: info ? info.color : "var(--text-muted)",
           fontWeight: info && info.health !== "green" ? 600 : 500,
           marginBottom: 4,
           whiteSpace: "nowrap",
           overflow: "hidden",
           textOverflow: "ellipsis",
         }}
       >
         via @{c.sender_slug}
         {s?.phone ? ` — ${s.phone}` : ""}
       </div>
     );
   })()}
   ```

Do NOT change any other part of the row (avatar, status pill, unread badge, delete affordance). This is a color + optional-phone change to the single existing sender line only.
  </action>
  <verify>
    <automated>cd frontend && bun run build 2>&1 | tail -5</automated>
  </verify>
  <done>
Inbox builds clean. In the chat list, the "via @slug — phone" line renders green for active senders, yellow for spam-limited/paused/warmup, red for re-auth-needed/frozen/error. Sender is resolved from the already-fetched senders list by slug; unknown slug falls back to the prior muted style without crashing.
  </done>
</task>

</tasks>

<verification>
- `cd frontend && bun run build` succeeds (TypeScript + Vite) with no new errors.
- Manual/visual (after `./deploy-frontend.sh` or `bun run dev`): open Inbox — sender lines are color-coded; cross-check a few accounts against the TG Accounts page and confirm the color matches that account's Accounts-page status (green=active, yellow=spam-limited/paused/warmup, red=re-auth/frozen).
- No backend, schema, or migration change was made (grep confirms only the two frontend files touched).
</verification>

<success_criteria>
- Sender line in the inbox chat list is colored by account health using the exact `auth_status`/`status` semantics from accounts.tsx (green/yellow/red).
- Zero backend changes; data comes from the senders list the inbox already fetches.
- Color derivation lives in one shared helper (`frontend/src/lib/sender-health.ts`) reusable by other surfaces later.
</success_criteria>

<output>
After completion, create `.planning/quick/260710-afg-inbox-chat-list-color-coded-sender-statu/260710-afg-SUMMARY.md`
</output>
