# aimly — Lovable PRD (v1)

> **Single source of truth for the Lovable build.** Read this end-to-end before generating any screen. Companion files in this folder (`AGENTS.md`, `KNOWLEDGE.md`, `screen-build-order.md`, `error-codes.md`, `telemetry-events.md`, `reconciliation.md`) are referenced inline — open them when this PRD points to them.

---

## 0. What we're building

**aimly** — multi-tenant SaaS for Telegram outreach automation via personal accounts plus an AI auto-responder. The Lovable build is the **whole frontend**. Backend (Python/FastAPI) is shipped and live.

| | |
|---|---|
| **Brand** | `aimly` (lowercase, no exclamation marks, AI-first SaaS tone) |
| **UI language** | English (en-US). User-generated content (contact names, prompts, messages) renders in any language untouched. |
| **Persona** | B2B SaaS founders & SDR teams. Non-technical. 1–5 Telegram accounts. Wants: paste CSV, pick agent, launch. |
| **Core Value (success metric)** | `time_to_first_campaign_seconds < 600` — from `signup_completed` to `campaign_launched`. Measured by telemetry §10. |
| **Backend status** | Phases 1–5 + 5.1 shipped. All endpoints in §6 are live on staging. |
| **Frontend status** | Greenfield Lovable project — this PRD. |

### End-to-end flow

```
Sign up (Supabase magic link)
   → Onboarding wizard (connect first TG account)
   → Dashboard (empty state)
   → Contacts (import CSV)
   → Agents (create first agent)
   → Campaign builder (7 steps → Launch)
   → Inbox (3-pane with LLM trace)
   → Analytics (dashboard funnel + campaign detail)
   → Settings (workspace, API keys)
```

11 screens total. Build order is fixed — see `screen-build-order.md`.

---

## 1. Stack

| Layer | Choice | Why |
|---|---|---|
| Framework | **React 18 + Vite** | Lovable default |
| Styling | **Tailwind CSS** + CSS variables from `design-source/project/styles.css` | Tokens drive everything; Tailwind is the consumer |
| Components | **shadcn/ui** + Radix primitives | Accessibility for free |
| Data | **TanStack Query v5** | Caching, polling, mutations |
| Forms | **react-hook-form** + **zod** + `@hookform/resolvers/zod` | All forms |
| Toasts | **Sonner** | Top-right, 3 max |
| Routing | **React Router v6** | File-system routing OK |
| Icons | **lucide-react** | Already used in design source |
| Auth | **@supabase/supabase-js** (client SDK) | Magic-link only |
| Telemetry | thin wrapper in `src/lib/telemetry.ts` + `navigator.sendBeacon` on `pagehide` | See §10 |

**Forbidden** (do not add):
- Framer Motion or any motion library — CSS keyframes only (`pulse-ring`, `shimmer` already in styles.css)
- Redux / Zustand / Jotai — TanStack Query owns server state; React `useState` owns local UI state
- Any UI library that isn't shadcn (no MUI, Mantine, Chakra)
- Date libraries other than `date-fns` (smallest, tree-shakeable)

---

## 2. Project structure

```
src/
├── lib/
│   ├── api.ts                  # fetch wrapper, 401 redirect, error envelope parse
│   ├── auth.ts                 # Supabase client + getSession helper
│   ├── telemetry.ts            # track(event, props) + sendBeacon flush
│   ├── query-keys.ts           # centralized TanStack Query keys
│   └── validators/             # zod schemas per resource
│       ├── campaign.ts
│       ├── agent.ts
│       ├── contact.ts
│       └── ...
├── types/
│   └── api.ts                  # GENERATED from backend OpenAPI — do not edit by hand
├── components/
│   ├── ui/                     # shadcn primitives (button, dialog, sheet, ...)
│   ├── shell/                  # AppShell, Sidebar, Topbar
│   ├── viz/                    # SankeyFunnel, CorridorBar, HealthDonut, WarmupSparkline, ToneSlider
│   ├── empty/                  # EmptyState (4-element formula)
│   └── overlays/               # LaunchOverlay, ImportOverlay, OnboardingFlow, ToolEditorModal
├── routes/
│   ├── login.tsx
│   ├── auth.callback.tsx
│   ├── onboarding.tsx
│   ├── dashboard.tsx
│   ├── campaigns/
│   │   ├── index.tsx           # list
│   │   ├── new.tsx             # 7-step builder
│   │   └── [id].tsx            # detail (5 tabs)
│   ├── inbox/
│   │   ├── index.tsx
│   │   └── [id].tsx
│   ├── agents/
│   │   ├── index.tsx
│   │   └── [id].tsx            # 4-tab editor
│   ├── contacts.tsx
│   ├── accounts.tsx
│   └── settings.tsx
├── styles/
│   └── aimly.css               # ingested verbatim from design-source/project/styles.css
└── main.tsx
```

---

## 3. Environment + connections

### 3.1 Frontend env vars (`.env.local` in Lovable project)

```env
# Backend API base URL
VITE_API_URL=https://aimly.agsventurelab.com/api/v1
# In dev / preview: point to staging
# VITE_API_URL=http://localhost:8810/api/v1  (only for local dev tunnels)

# Supabase
VITE_SUPABASE_URL=https://qhxkyzmwnehnrfndpxxo.supabase.co
VITE_SUPABASE_ANON_KEY=<paste-anon-key-from-supabase-dashboard>

# Build-time only
NODE_ENV=production  # set by Lovable automatically
```

**Rules:**
- All env vars MUST start with `VITE_` (Vite requirement).
- `VITE_SUPABASE_ANON_KEY` is the **anon JWT** (safe in client). Do NOT put `SUPABASE_JWT_SECRET` here — that's backend-only.
- `VITE_API_URL` always ends in `/api/v1` so route strings stay short (`api.get('/agents')` not `/api/v1/agents`).

### 3.2 Auth flow (Supabase magic link → JWT → backend)

```
1. User submits email on /login
2. supabase.auth.signInWithOtp({ email, options: { emailRedirectTo: window.location.origin + '/auth/callback' }})
3. User clicks link in email → lands on /auth/callback
4. Supabase SDK auto-processes the hash → session stored in localStorage
5. Frontend reads access_token = session.access_token (HS256-signed JWT)
6. Frontend calls POST /api/v1/auth/me with Authorization: Bearer <access_token>
7. Backend verifies JWT against SUPABASE_JWT_SECRET (HS256), lazy-creates workspace if first sign-in
8. Response: { workspace_id, workspace_name, user_email, is_first_session }
9. Frontend stores workspace info in TanStack Query (key ['auth', 'me'])
10. Redirect: senders.length === 0 → /onboarding ; else → /dashboard
```

**Supabase project setup (one-time, before any auth works):**
1. Dashboard → Settings → API → **JWT Settings → Algorithm = HS256**. Pitfall 3 — backend rejects everything else.
2. Authentication → URL Configuration → Site URL = production frontend URL; Redirect URLs add `*.lovableproject.com` and any preview domains.
3. Email templates → magic link subject "Sign in to aimly"; body uses the default `{{ .ConfirmationURL }}` token.

### 3.3 API client wrapper (`src/lib/api.ts`)

```ts
import { supabase } from './auth'

const BASE = import.meta.env.VITE_API_URL  // /api/v1

type ApiError = { code: string; message: string; [key: string]: unknown }

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { data: { session } } = await supabase.auth.getSession()
  const token = session?.access_token

  const res = await fetch(BASE + path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })

  if (res.status === 401) {
    await supabase.auth.signOut()
    window.location.href = '/login?reason=session_expired'
    throw new Error('TOKEN_EXPIRED')
  }

  if (!res.ok) {
    const detail = await res.json().catch(() => ({})) as { detail?: ApiError }
    const err = detail?.detail ?? { code: 'UNKNOWN', message: res.statusText }
    throw Object.assign(new Error(err.message), { code: err.code, status: res.status, ...err })
  }

  return res.status === 204 ? (undefined as T) : res.json()
}

export const api = {
  get:    <T>(p: string)             => request<T>('GET', p),
  post:   <T>(p: string, b?: unknown) => request<T>('POST', p, b),
  patch:  <T>(p: string, b?: unknown) => request<T>('PATCH', p, b),
  delete: <T>(p: string)             => request<T>('DELETE', p),
}
```

**Error envelope contract.** Backend uses HTTPException with `{ detail: { code, message, ...extra } }`. Frontend reads `code` and maps via `error-codes.md`. Never display raw `message` to users — it's often internal English. See §11.

### 3.4 TanStack Query setup

```ts
// src/main.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,                // 30s default freshness
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      retry: (count, err: any) => err.status >= 500 && count < 2,
    },
    mutations: {
      onError: (err: any) => toast.error(mapError(err.code) ?? 'Server is unreachable. Retry.'),
    },
  },
})
```

**Polling cadences (per UI-SPEC §5):**

| Screen | Endpoint | Interval |
|---|---|---|
| Inbox list | `GET /conversations` | 10s |
| Inbox thread + trace | `GET /conversations/{id}`, `/messages`, `/llm-calls` | 15s |
| Dashboard | `GET /analytics/workspace`, `/analytics/funnel`, `/senders` | 30s |
| Campaign detail | `GET /campaigns/{id}` + analytics | 30s |
| Onboarding QR | `GET /onboarding/qr-status/{session_id}` | 2s |

All polls **pause on window blur** (`refetchIntervalInBackground: false`).

### 3.5 Query key convention

```ts
// src/lib/query-keys.ts
export const qk = {
  auth:      { me: () => ['auth', 'me'] as const },
  agents:    { all: () => ['agents'] as const, one: (id: string) => ['agents', id] as const },
  campaigns: { all: () => ['campaigns'] as const, one: (id: string) => ['campaigns', id] as const,
               analytics: (id: string) => ['campaigns', id, 'analytics'] as const },
  inbox:     { list: (filters: object) => ['conversations', filters] as const,
               thread: (id: string) => ['conversations', id] as const,
               messages: (id: string) => ['conversations', id, 'messages'] as const,
               trace: (id: string) => ['conversations', id, 'llm-calls'] as const },
  senders:   { all: () => ['senders'] as const, one: (slug: string) => ['senders', slug] as const },
  contacts:  { folders: () => ['folders'] as const, list: (folderId: string) => ['contacts', { folderId }] as const },
  analytics: { workspace: () => ['analytics', 'workspace'] as const,
               funnel: (scope: string, id?: string) => ['analytics', 'funnel', scope, id] as const },
}
```

Always invalidate via prefix: after mutating a campaign, `qc.invalidateQueries({ queryKey: ['campaigns'] })`.

---

## 4. Design system

### 4.1 Source of truth

`design-source/project/styles.css` is the canonical token file. **Lovable must ingest it verbatim** (copy to `src/styles/aimly.css`, import once in `main.tsx`). Do not redefine any `--*` variable.

### 4.2 Color palette

| Role | CSS var | Hex | Reserved for |
|---|---|---|---|
| Primary | `--tg-blue` | `#3390ec` | Primary CTAs, active nav, focus ring, links, AI-on toggle |
| Primary hover | `--tg-blue-hover` | `#2481cc` | `:hover` on `.btn--primary` |
| Primary soft | `--tg-blue-soft` | `#e8f3fe` | `.btn--soft`, `.pill--blue`, active nav background |
| Primary softer | `--tg-blue-softer` | `#f3f9ff` | Selected list rows (inbox), `<TraceBlock tone=blue>` |
| **AI accent** | `--ai-purple` | `#8774e1` | **AI moments only** — `<live-dot>`, `<ai-shimmer>`, thought trace, AI co-pilot panel, launch overlay icon, AI suggestion chips |
| AI soft | `--ai-purple-soft` | `#f1eefb` | Trace latest-entry tint, builder co-pilot panel, negative tone-slider fill |
| Success | `--success` | `#4dcd5e` | Status pills (lead, active), donut high health, finish corridor |
| Success soft | `--success-soft` | `#e8faec` | `.pill--green`, success row background, +delta.up |
| Warning | `--warning` | `#f5a623` | Status pills (warmup), donut medium, corridor approaching ceiling |
| Warning soft | `--warning-soft` | `#fff4e0` | `.pill--orange`, take-over button, paused campaign tile |
| Danger | `--danger` | `#e13b30` | Destructive CTAs, error pulse, account error row |
| Danger soft | `--danger-soft` | `#fde8e6` | `.pill--red`, error toast, −delta.down |
| Surface | `--bg` | `#ffffff` | Sidebar, topbar, cards |
| Surface soft | `--bg-soft` | `#f4f5f7` | Page background |
| Surface softer | `--bg-softer` | `#fafbfc` | Card surfaces alt |
| Surface 2 | `--surface-2` | `#f7f8fa` | Table header, codeblocks |
| Border | `--border` | `#ebedf0` | Card borders, input borders |
| Border strong | `--border-strong` | `#dde0e5` | Selected card borders, table header borders |
| Text | `--text` | `#0f1419` | Body, headings (19.6:1 AAA) |
| Text soft | `--text-soft` | `#4b5563` | Secondary, nav (7.2:1 AAA) |
| Text muted | `--text-muted` | `#707579` | Metadata, hints (4.8:1 AA) |
| Text faint | `--text-faint` | `#98a2b2` | Decorative only — never body text (2.9:1, fails AA) |

**60/30/10 split:** 60% surfaces (`--bg`, `--bg-soft`), 30% card alts (`--surface-2`, `--bg-softer`), 10% accents (~7% `--tg-blue`, ~3% `--ai-purple`).

**Hard rule (Pitfall 7):** `--ai-purple` is reserved. Use only in: `<live-dot>`, `<ai-shimmer>`, thought-trace panel, AI co-pilot side rail, launch overlay icon, AI suggestion chips, AI-assisted brief pill. **Never** as a generic accent.

### 4.3 Typography

| Property | Value |
|---|---|
| Font sans | `Geist` (weights 400, 600 — load via Google Fonts `<link>`) |
| Font mono | `Geist Mono` (weights 400, 500 — for API keys, phones, JSON, tool names) |
| Base | `14px / 1.45` |
| Tabular numerals | `font-variant-numeric: tabular-nums` on `.num`, `.mono`, metric values, table numeric cells |

**Weight contract — exactly 2 weights** (400 + 600). Where styles.css uses 500 (sidebar items, buttons, metric headers, field labels), remap to **400 + 1–2px smaller + muted color** instead of adding weight 500. Cleaner mental model + smaller font payload.

**Type scale:**

| Role | Size | Weight |
|---|---|---|
| Title (topbar) | 18 | 600 |
| Brand | 17 | 600 |
| Metric value | 26 | 600 |
| Section heading | 16 | 600 |
| Body | 14 | 400 |
| Card title | 14 | 600 |
| Sub-label / table cell | 13 | 400 |
| Muted / hint / pill | 12 | 400 |
| Tiny (section label) | 11 | 600 |

### 4.4 Spacing — 8-point base with 10 and 14 half-steps

| Token | Value | Use |
|---|---|---|
| 2xs | 4 | Inline icon gaps, badge padding |
| xs | 8 | Tight gaps inside rows, button gap |
| sm | 10 | Card-internal element gap, sidebar-item gap, avatar gap |
| md | 12 | Card padding (compact), section padding |
| lg | 14 | Card padding (default), grid gap, metric padding (half-step — required) |
| xl | 16 | Card body padding, message bubble |
| 2xl | 18 | Card body wide |
| 3xl | 20 | Topbar message-bubble, sidebar brand (half-step — required) |
| 4xl | 24 | Page hero, content padding |
| 5xl | 36 | Builder editor horizontal padding |

**Layout constants:**
- Sidebar width: **248px**
- Topbar height: **60px**
- Input height: **38px**
- Button height: 36px (sm 30px)
- Toggle: 36×20px

### 4.5 Radius

| Token | CSS var | Value | Use |
|---|---|---|---|
| sm | `--r-sm` | 6 | Inline badges, kbd, delta chips |
| md | `--r-md` | 10 | Inputs, sidebar workspace card |
| lg | `--r-lg` | 14 | Cards, metrics, dialogs |
| xl | `--r-xl` | 20 | Onboarding modal, launch overlay |
| full | `--r-full` | 999 | Pills, avatars, toggles, status dots |

### 4.6 Shadow

```css
--shadow-sm: 0 1px 2px rgba(15,20,25,0.04), 0 0 0 1px rgba(15,20,25,0.04);   /* resting cards */
--shadow-md: 0 4px 16px rgba(15,20,25,0.06), 0 0 0 1px rgba(15,20,25,0.04);  /* hover cards, node labels */
--shadow-lg: 0 12px 40px rgba(15,20,25,0.10), 0 0 0 1px rgba(15,20,25,0.05); /* modals, overlays */
```

Each shadow includes a 1px outer-spread ring → border-without-border. **Never** add a CSS `border` to a card that already has a shadow — it'll double up.

### 4.7 Motion

Two AI-reserved keyframes are pre-defined in `styles.css`:

| Keyframe | Use |
|---|---|
| `pulse-ring` | `.live-dot` halo (dashboard "updating live" chip, running campaign indicator) |
| `shimmer` | `.ai-shimmer` gradient sweep (AI generation skeleton) |

Standard motion uses CSS `transition: ...` already on each element (e.g. `.btn { transition: all 0.12s }`). **No motion libraries.** **No additional keyframes.**

**Reduced motion guard — mandatory:**

```css
@media (prefers-reduced-motion: reduce) {
  .live-dot, .ai-shimmer { animation: none !important; transition: none !important; }
}
```

**Bar/funnel charts are CSS-flex, not SVG `preserveAspectRatio="none"`.** Sankey is the one SVG chart in the build (curved ribbons). Everything else (corridor bars, FunnelMini, FunnelBars, 24h volume) is `display: flex` with `flex: <ratio>` siblings.

---

## 5. Layout shell

### 5.1 Structure

```
┌──────────────────────────────────────────────────────────┐
│ grid-template-columns: 248px 1fr;  height: 100vh         │
│ background: var(--bg-soft)                                │
│                                                            │
│ ┌──────────┐  ┌─────────────────────────────────────────┐│
│ │ Sidebar  │  │ Topbar  (60px, --bg, border-bottom)     ││
│ │ 248px    │  ├─────────────────────────────────────────┤│
│ │ --bg     │  │ Content (scroll, --bg-soft, padding 24) ││
│ │ border-r │  │                                          ││
│ └──────────┘  └─────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

### 5.2 Sidebar nav (left)

Primary (6 items):

| Icon | Label | Path | Notes |
|---|---|---|---|
| dashboard | Dashboard | `/` | Active = `--tg-blue-soft` bg, `--tg-blue` text |
| campaigns | Campaigns | `/campaigns` | + badge running-count (muted) |
| inbox | Inbox | `/inbox` | + badge unread-count (`--tg-blue` filled) |
| agents | Agents | `/agents` | |
| contacts | Contacts | `/contacts` | |
| accounts | TG accounts | `/accounts` | + dot warning if any account in error |

Section divider: `Workspace` (uppercase, `--text-faint`, 11/600/0.06em letter-spacing).

Secondary nav:

| Icon | Label | Path |
|---|---|---|
| settings | Settings | `/settings` |
| help | Help & docs | external `docs.aimly.com` (placeholder) |

**Workspace switcher** (sidebar bottom, `margin: auto 12px 12px`): avatar (32×32 orange gradient initials) + name + email (muted) + chevron-down (opens user menu — Sign out, theme toggle stub). Multi-workspace switcher is v2.

### 5.3 Topbar (per-screen, 60px)

- **Left:** back button (icon, hidden on root) + breadcrumb (`.tb__crumb`, muted) + title (`.tb__title`, 18/600)
- **Right** (`.tb__right`): action cluster — mix of `.btn--ghost.btn--sm` and 36×36 `.tb__icon-btn`
- ⌘K search lives in sidebar (`.sb__search`) — **v1 stub with disabled state**, tooltip "Coming soon"

### 5.4 Responsive

**Desktop ≥ 1280px is the priority.**

| Breakpoint | Behavior |
|---|---|
| ≥ 1280px | Full 248px sidebar + all 3-pane layouts |
| 1024–1279 | Sidebar collapses to 64px icons-only; 3-pane inbox → 2-pane (hide trace by default; toggle) |
| 768–1023 | Sidebar → Sheet drawer (hamburger); tables remain |
| < 768 | Single-pane stacks. CSV import + onboarding wizards desktop-only (banner "Use desktop for setup") |

---

## 6. Screen specifications

11 screens. Full pixel detail lives in `.planning/phases/05.1-lovable-ui-v1/05.1-UI-SPEC.md §5` — that is **the contract**. This section is a compact reference. When in doubt, the UI-SPEC wins.

For each screen below: path · purpose · layout · key endpoints · telemetry. Build order is enforced — see `screen-build-order.md`.

### 6.1 Login (`/login`, `/auth/callback`) — UI-SPEC §5.1

| | |
|---|---|
| Layout | Standalone (no shell). Single 420px card centered on `--bg-soft`. `--shadow-lg`, `--r-xl`, padding 32. |
| Zones | (1) `<PulseLogo>` 40×40 + aimly wordmark; (2) "Sign in to aimly" (20/600); (3) sub "We'll email you a magic link — no password needed."; (4) email input; (5) primary "Send magic link" full-width; (6) terms/privacy helper |
| Post-submit | Replace form with checkmark + "We sent a link to {{email}}. Click it to sign in." + "Use a different email" link |
| Errors | Inline below input (role=alert): "Couldn't send the link. Try again." / "Too many attempts. Try in {{N}}s." |
| Callback | `/auth/callback` — Supabase auto-processes hash → POST `/auth/me` → redirect: `senders.length === 0` → `/onboarding`, first session → `/dashboard?welcome=1`, else `/dashboard` |
| Endpoints | `supabase.auth.signInWithOtp(...)`, `POST /api/v1/auth/me` |
| Telemetry | `magic_link_requested` on submit; `signup_completed` on first successful callback (T0 for Core Value) |

### 6.2 Onboarding wizard (`/onboarding`) — UI-SPEC §5.2

| | |
|---|---|
| Trigger | Auto-redirect from `/auth/callback` when `senders.length === 0` |
| Layout | Centered modal, 720px, no shell. Left rail (240px) = purple gradient `linear-gradient(135deg, #f1eefb 0%, #e8f3fe 100%)` with contextual "Why we ask" copy. Right = step content card. Stepper at top (3 dots / labels). |
| Steps | (1) **Phone** — input + "Send code" (POST `/onboarding/start`); (2) **Verify** — 5-box code input (auto-advance focus) → POST `/onboarding/verify-code`. On `2FA_REQUIRED` → step 2.5 password → `/verify-2fa`. (3) **Success** — checkmark + sender name + "Continue to dashboard" |
| QR tab | Step 1 has "Phone / QR" toggle. QR shows `<QRPlaceholder>` + 3-step instructions, polls `GET /onboarding/qr-status/{session_id}` every 2s |
| Skip | Top-right "Skip for now" — AlertDialog → if skipped, dashboard shows top banner "Connect your first TG account to launch campaigns" |
| Endpoints | `POST /onboarding/{start, verify-code, verify-2fa, qr-start}`, `GET /onboarding/qr-status/{session_id}` |
| Telemetry | `sender_added` on step 3 success |
| Errors | `INVALID_PHONE` / `CODE_INVALID` (inline + attempts counter) / `2FA_INVALID` / `SESSION_EXPIRED` (AlertDialog → reset wizard) / `FLOOD_WAIT N` (banner with countdown) |

### 6.3 Dashboard (`/`) — UI-SPEC §5.3

| | |
|---|---|
| Topbar | "Welcome back, {{first_name}}" + "Last 7 days · {{range}}"; right: date picker, Filters, Export, bell, avatar |
| Zones | (1) 4-up KPI row (Messages sent / Response rate / Leads / Handoffs) — each = icon chip + label + value (26/600) + delta pill + sparkline. (2) **Full-width Sankey funnel** — `<LivePulseCard>` with 5-stage `<SankeyFunnel>` (Sent → Replied → Engaged → Lead → Handoff). Card header: "Conversion funnel" + "Sent → Handoff · last 7 days" + `.live-dot` "updating live". (3) Account health (full-width `<AccountHealthCard>`). (4) Campaign performance + Activity (`1.6fr 1fr`) |
| Empty state | If `analytics.workspace.sent === 0`: full-width card with illustration + "No campaigns yet" + "Launch your first campaign to see live activity here." + primary "Create campaign" |
| Refresh | 30s auto-refresh (pauses on blur) + manual refresh button per card |
| Endpoints | `GET /analytics/workspace`, `GET /analytics/funnel?scope=workspace`, `GET /senders`, `GET /conversations?status=active&limit=20` |
| Telemetry | `dashboard_viewed` (debounced 1/session via `sessionStorage`) |

**Sankey funnel — implementation contract.** Single inline SVG. 5 vertical bars proportional to count. Curved bezier ribbons between stages with per-stage `linearGradient` `<defs>` (opacity 0.4). Drop-off "bleed" shadow paths falling below the floor between stages (opacity 0.08). Floating white conversion-% chips above each ribbon (rounded pill, 1px border, tabular-nums). **`viewBox="0 0 1200 240"` with `preserveAspectRatio="xMidYMid meet"`** — the only SVG that does NOT use `none`.

**Engaged stage definition (LOCKED — Pitfall 5).** Backend: `engaged = COUNT(DISTINCT conversation_id WHERE inbound_message_count >= 2 AND status NOT IN ('lead','handoff','finished','bot_ignored'))`. Frontend **renders backend's number** — do not recompute.

### 6.4 Campaigns list (`/campaigns`) — UI-SPEC §5.4

| | |
|---|---|
| Topbar | "Campaigns" + right: search (220px), Filters, primary "New campaign" → `/campaigns/new` |
| Zones | (1) Tabs (`.tabs`): All / Running / Paused / Scheduled / Drafts / Finished — each with count pill. (2) Table inside card: checkbox / Campaign (icon + name + start-date) / Status `<StatusPill>` / Agent · Folder (2-line) / Senders (avatar stack max 3 + overflow) / Progress (bar + %) / Funnel (sent → replied → leads inline 3-bar mini) / row actions ⋯ |
| Row actions | Open / Duplicate (POST `/duplicate`) / Pause / Resume / Stop / Delete (AlertDialog) |
| Empty | `Megaphone` 96px + "Launch your first campaign" + body + "Create campaign" (disabled tooltip "Add an agent and a folder first" if either missing) |
| Endpoints | `GET /campaigns`, `POST /campaigns/{id}/{start,pause,resume,stop,duplicate}`, `DELETE /campaigns/{id}` |
| Telemetry | `campaign_paused`, `campaign_resumed` on lifecycle clicks |

**Important — use `/stop` not `/finish`.** Backend Phase 05.1 added `/campaigns/{id}/stop` as the canonical alias. The legacy `/finish` endpoint still exists but is deprecated in UI.

### 6.5 Campaign builder (`/campaigns/new`) — UI-SPEC §5.5

| | |
|---|---|
| Layout | 3-pane `260px 1fr 360px` — **Steps rail** / **Editor** (max-width 640px) / **Live preview** |
| Steps | brief → agent → accounts → audience → schedule → integrations → review. Active = `--tg-blue-soft` bg; completed = green check chip. |
| Step 1 — Brief | Name input + Brief textarea (5 rows, with purple "AI-assisted" pill in label) + Goal chips |
| Step 2 — Agent | (a) 2×2 agent grid + "Create new agent" dashed CTA (opens agent editor in Sheet); (b) "Customize for this campaign" — Audience hints / Primary goal (2×2 RadioCard: Book a meeting / Qualify lead / Get a click / Engage) / Success criteria |
| Step 3 — Senders | Multi-select from `/senders` filtered to `status ∈ [active, warmup]` — checkbox + avatar + name + proxy + age + warmup day + corridor + status pill. Banner: "{{N}} accounts selected can send up to {{N * limitDaily}} messages/day." Sender-lock 409 → inline warning per row |
| Step 4 — Audience | 2×3 grid of folder cards from `/folders`. Selected = blue border. Hint: "New contacts added later are auto-enrolled." |
| Step 5 — Schedule | 7-day toggle squares (M/T/W/T/F/S/S) + From-To-Timezone + Start · End (optional). Green corridor info card: "aimly enforces 4 / 20 / 150 per account (hour / day / week) plus warm-up. You'll never exceed." |
| Step 6 — Integrations | (a) Built-in signals — read-only info card with 3 `<BuiltinSignalRow>`: **Mark as lead** (`type: "lead"`), **Transfer to manager** (`type: "handoff"`), **Finish conversation** (`type: "finished"`). (b) Custom tools — empty state OR list of tool cards + "Add tool" `<ToolEditorModal>`. (c) Webhook URL input + "Send test" ghost button. (d) **Custom signals + push URL** — disabled "In development" purple-pill card. |
| Step 7 — Review | Read-only summary rows (each with edit-jump) + **AI Forecast panel** (purple gradient): "Forecast: at this pace, you'll reach all {{N}} contacts in ~{{days}} working days. Expected ~{{replies}} replies and ~{{leads}} leads." |
| Launch | Step 7 "Launch campaign" (`.btn--dark`) → opens `<LaunchOverlay>` — full-screen scrim, 480px modal, 5 sequential stages with check/spinner (~580ms each): "Validating audience…" → "Locking N senders…" → "Compiling prompt…" → "Calibrating green corridor…" → "Scheduling first wave…" → "Launched." → auto-close after 1.1s → redirect `/campaigns/{id}` |
| Right rail (live preview) | Card "Campaign {{name}}" + tags + Agent panel + Senders panel + Audience panel + Schedule panel + estimated start |
| AI co-pilot (under steps rail) | Purple-gradient panel, "AI co-pilot" header + "Drop a brief and I'll pre-fill" + "Auto-fill from brief" button → `POST /campaigns/auto-fill` (v1 stub returns seeded defaults) |
| Save as draft | Topbar right `.btn--ghost.btn--sm` — `POST /campaigns` with `status: 'draft'` then exit |
| Endpoints | `POST /campaigns` (draft on step 1 Continue) → `PATCH /campaigns/{id}` (subsequent steps) → `POST /campaigns/{id}/start` (launch) ; `POST /campaigns/auto-fill` |
| Tools persistence | `campaigns.tools` JSONB = `[{id, name, description, parameters: [{name, type, description, required}]}]`. `ToolSpec.webhook_url` is **Optional/deprecated** in v1 (single webhook on campaign level). |
| Telemetry | `campaign_created` (POST 201); `campaign_launched` (start 200 — T1 for Core Value KPI) |

### 6.6 Campaign detail (`/campaigns/{id}`) — UI-SPEC §5.6

| | |
|---|---|
| Topbar | Back + crumb + campaign name + `<StatusPill>` + `.live-dot` (if running) + right: Duplicate / Edit / lifecycle button (Pause / Resume) |
| Tabs | **Overview** (default) / **Conversations** / **Senders** / **LLM trace** / **Settings** |
| Overview | 6-up `<MiniMetric>` (Sent / Delivered / Replied / Engaged / Leads / Handoffs) + chart row `1.7fr 1fr` (`<StackedAreaChart>` 14d funnel + `<FunnelBars>` gradient blue→purple 6 steps) + live convos + sender corridor (`1.5fr 1fr`) |
| Conversations | `<InboxScreen embedded campaignFilter={c.name}/>` — same 3-pane, pre-filtered |
| Senders | Table: Sender / Health (donut + score) / Today / This week / Replies sent / Leads / 14d trend |
| LLM trace | 4-up `<MiniMetric>` (Total calls / Avg latency / Tokens in/out / Spend 7d) + recent calls table (When / Contact / Intent / Tools / Tokens / Latency / Signals). Click row → Sheet with full prompt JSONB |
| Settings | 2-col `1fr 1fr` aligned top. **Left**: (a) "Customize for this campaign" (same 3 controls as builder step 2 — audience hints / primary goal RadioCard / success criteria); (b) "Schedule" (read-only `<SettingRow>` list). **Right**: (a) "Built-in signals" — 3 rows with "fired N× / 7d" counter; (b) "Webhook & tools" — webhook input + custom tool chips + dashed "+ Add tool" → `<ToolEditorModal>`; (c) "Custom signals" disabled overlay |
| Destructive | Stop → AlertDialog + checkbox "I understand". Delete (from list ⋯) → type-the-name confirmation (cascades conversations → status=manual) |
| Endpoints | `GET /campaigns/{id}`, `GET /analytics/campaigns/{id}`, `GET /analytics/funnel?scope=campaign&id={id}`, `GET /analytics/llm?scope=campaign&id={id}`, `GET /conversations?campaign_id={id}`, `GET /conversations/{id}/llm-calls`, lifecycle POSTs, `PATCH /campaigns/{id}` |

### 6.7 Inbox (`/inbox`, `/inbox/{conversation_id}`) — UI-SPEC §5.7

| | |
|---|---|
| Layout | 3-pane `320px 1fr 360px` — **List** / **Thread** / **Thought trace**. Trace toggleable; when hidden grid = `320px 1fr` |
| Topbar | "Inbox" + right: Saved views, Show/Hide trace, Export |
| List pane | Search input (full-width 36px) + filter chips (All / Active / Leads / Handoff / No reply / Finished) + conversation rows (avatar + name + username · country + 2-line snippet clamped + status pill + star + unread badge). Selected = `--tg-blue-softer` bg + 3px left border `--tg-blue` |
| Thread pane | Header (avatar + name + KV strip Agent/Sender/Campaign + star + pin + "Take over" button [`--warning-soft` bg, `#a86200` text] + brain icon) + lead-signal banner (if status=lead, green-to-blue gradient: "Lead detected · Meeting booked for {{datetime}}") + message stream (outbound = `--tg-blue` filled, inbound = white bg + `--shadow-sm`, max-width 70%) + reply composer (3 suggestion chips + autosize textarea + Send button) |
| Trace pane | Header gradient (purple→blue) + "Thought trace · Why {{agent_name}} said what they said". Body: collapsible `<TraceEntry>` cards. Closed: sparkles + intent + timestamp · latency · tokens. Expanded: System block (gray) → Tool calls (mono pill + JSON args + ✓ result) → Agent response (blue tint) → Signals fired (green zap pills) → Footer (model · tokens in→out · cost). Latest entry has purple gradient top tint. |
| Take over | Click "Take over" → AlertDialog → `POST /conversations/{id}/disable-ai` → status flips to manual. Composer warning when AI is on: "Sending now will turn AI off for this thread" (D-04 safety) |
| Poll cadence | List 10s, thread + trace 15s, messages 10s — all pause on blur |
| Endpoints | `GET /conversations` (filters) + `GET /conversations/{id}` + `GET /conversations/{id}/messages` + `GET /conversations/{id}/llm-calls` + `POST /conversations/{id}/{enable-ai,disable-ai,send}` + `PATCH /conversations/{id}` + `DELETE /conversations/{id}` |
| Telemetry | `conversation_taken_over_by_human` (on disable-ai OR first manual send OR safety auto-pause), `llm_trace_opened` (on Show trace click) |

**This screen is UI-INBX-01.** Backend was fully shipped in Phase 5; no backend gap. See `AGENTS.md` "Inbox build" section and `reconciliation.md` "UI-INBX-01 closure note".

### 6.8 Agents (`/agents`, `/agents/{id}`) — UI-SPEC §5.8

| | |
|---|---|
| List | Topbar "Agents" + right "Filters" + primary "New agent". Body: auto-fill grid `repeat(auto-fill, minmax(320px, 1fr))` of agent cards. Each card: top = avatar (40px colored gradient) + name (15/600) + role (sm muted) on left; right = `<VoiceBadge>` chip (Professional / Friendly / Playful) + 28×28 row-actions (⋯). Below: clamp-2 preview of "Who is this agent?" text. Bottom strip: 3-stat row (Campaigns / Conversations / Leads). Footer: "Updated {{relative}}" muted xs. + Dashed "Create new agent" tile. |
| Editor tabs | **Context** / **Voice** / **FAQ & Knowledge** / **Safety** (exactly 4 tabs — older Task / Signals / Tools / Sandbox removed) |
| Context tab | 2-col `1fr 340px` (right = sticky `<LivePreviewCard>`). Left = stacked `<Panel>`: (1) "Who is this agent?" 4-row textarea; (2) "What does it know about the company?" 8-row; (3) "Variables (auto-substituted)" tag chips `{{first_name}}` `{{company}}` `{{role}}` `{{source}}` `{{custom.industry}}` `{{имя}}` + dashed "Add variable" pill |
| Voice tab | 2-col. Left = 4 `<Panel>`: (1) **Voice baseline** 1×3 RadioCard: Professional (shield, blue) / Friendly (smile, green) / Playful (sparkles, ai-purple); (2) **Tone** 3 `<ToneSlider>` (Formal↔Casual / Reserved↔Warm / Brief↔Detailed), −50..+50, central tick, blue fill +, purple fill −, numeric chip; (3) **Reply constraints** — Max message length (number input default 280 + "chars"), Mirror language switch (default ON), Allow emoji switch (default OFF); (4) **Banlist** `<TagInput>` default `["revolutionary", "synergy", "circle back"]` |
| FAQ tab | 2-col. Left = (1) "Knowledge base" 10-row textarea (ICP, pricing, top-3 objections); (2) "Q&A pairs (N)" — each = bordered card with Q chip + textarea + delete + A chip + textarea. "+ Add Q&A" `.btn--soft.btn--sm` |
| Safety tab | Single-col, 3 `<Panel>`: (1) **Auto-pause triggers** `<TagInput>` default `["unsubscribe", "stop messaging me", "не пишите больше", "report you", "spam", "lawyer", "GDPR"]`; (2) **Pause behavior** 3 `<RadioRow>`: Pause this conversation only (default) / Pause contact across campaigns / Pause entire campaign; (3) **Recent auto-pauses** — last 7 days list (avatar + name + "Matched `<trigger>`" monospace danger + relative time + Review) |
| Endpoints | `GET /agents`, `POST /agents`, `GET /agents/{id}`, `PATCH /agents/{id}`, `POST /agents/{id}/duplicate`, `DELETE /agents/{id}` |
| Agent v2 payload columns | `who_is_agent` (text), `company_knowledge` (text), `knowledge_base` (text), `voice_baseline` enum, `tone` jsonb `{formal:int, warm:int, brief:int}`, `max_message_length` (int), `mirror_language` (bool), `allow_emoji` (bool), `banlist` (text[]), `qa_pairs` jsonb `[{q,a}]`, `auto_pause_triggers` (text[]), `auto_pause_scope` enum |
| Destructive | Delete → 409 if attached to running campaign → AlertDialog "Stop the campaign first." (read-only, no force) |
| Telemetry | `agent_created` on POST 201; `agent_voice_changed` on tab=Voice patch |

### 6.9 Contacts (`/contacts`) — UI-SPEC §5.9

| | |
|---|---|
| Layout | 2-pane `280px 1fr` — Folders sidebar / Folder detail |
| Topbar | "Contacts" + right: "Import CSV" → `<ImportOverlay>` + primary "New folder" |
| Folder pane | Section label "Folders ({{count}})" + scrollable list (10px gap). Each folder = colored icon + name + "{{count}} contacts". Bottom: dashed "New folder" |
| Detail pane | Folder header (icon + name 20/600 + "{{count}} contacts · {{source}}" + Move to + trash) + 4-up `<MiniMetric>` (Total / In Telegram / Currently messaged / Replied) + contacts card (in-card search 240×32 + Filters + table: checkbox / Contact / Company · Role / Username (mono blue) / Phone (mono muted) / Source / In TG) |
| CSV import flow (`<ImportOverlay>` 4 stages, 560px modal --r-xl 18) | (1) **Upload** — dashed drag-drop + "CSV up to 200 MB or paste Google Sheets URL" + folder select; (2) **Mapping** — CSV cols → aimly fields with arrow indicators; (3) **Importing** — gradient icon + progress bar `--ai-purple → --tg-blue` + counter; (4) **Done** — green check + "Import complete" summary + "Open folder" primary |
| Endpoints | `GET /folders`, `POST/PATCH/DELETE /folders/{id}`, `GET /contacts?folder_id={id}` (NOT `/folders/{id}/contacts`), `POST /contacts/import/preview`, `POST /contacts/import`, `POST /contacts/recheck` |
| Telemetry | `csv_import_completed` on stage 4 (props: `folder_id, created, updated, skipped`) — alias of `contacts_imported` |

### 6.10 TG accounts (`/accounts`) — UI-SPEC §5.10

| | |
|---|---|
| Topbar | "Telegram accounts" + Filters + primary "Connect account" → `<OnboardingFlow>` |
| Zones | (1) 5-up `<MiniMetric>` (Connected / Active / Warm-up / Paused / Errors); (2) fleet table (Account avatar+status-dot, name, username · phone / Status / Campaign / Today corridor / This week / Proxy / Warm-up Day N/30 + bar / Health donut+score / ⋯); (3) Rate corridor + Events row (`1fr 1fr`): `<BarChart>` CSS-flex 24h hourly volume vs ceiling + Account events feed (5 most recent) |
| Onboarding modal (4 steps) | (1) Method — 2 cards Phone+SMS / QR + info "MTProto, encrypted, bound to proxy"; (2) Phone + code OR QR with `<QRPlaceholder>` + 3-step instructions; (3) 2FA + Proxy (proxy select with flag prefix "🇩🇪 DE residential" + ping); (4) Warm-up — success gradient + 30-bar warm-up curve + "aimly ramps 4 → 20 msgs/day across 30 days" |
| Endpoints | `GET /senders`, `GET /senders/{slug}`, `PATCH /senders/{slug}`, `POST /senders/{slug}/{pause,resume,reauth,assign-proxy}`, `DELETE /senders/{slug}`, `POST /onboarding/{start,verify-code,verify-2fa,qr-start}` |
| Path-param note | **All sender paths use `{slug}` not `{id}`.** Slug is the natural identifier (human-readable per onboarding flow). UI-SPEC § 5.10 §13 changelog confirms this reconciliation. |
| Telemetry | `sender_added` on step 4 success |

### 6.11 Settings (`/settings`) — UI-SPEC §5.11

| | |
|---|---|
| Tabs | Workspace / API keys / Members / Profile / Appearance |
| Workspace | Card "Workspace details" — name (PATCH on blur), workspace_id (mono + copy, readonly), created_at (relative + absolute), has_checker badge |
| API keys | Card with table (name / prefix `wsk_abcd…` mono / created / last_used / status). "Create key" → AlertDialog with name → backend returns full token → green-bordered card with copy + warning "Save now — won't be shown again." Row action Revoke → destructive AlertDialog with type-the-prefix (last 6 chars) |
| Members | v1 placeholder: 1-row table with current user (Owner) + disabled "Invite teammates" + tooltip "Coming in v2 — share API keys for read-only access" |
| Profile | Email (readonly) + "Sign out" (Supabase signOut → /login) + link "Manage account in Supabase" (external) |
| Appearance | Theme RadioGroup System / Light / Dark — stored in localStorage, applied via `data-theme` on `<html>`. **v1: Light only fully wired; Dark stubbed** (no dark tokens in design source — note as deferred) |
| Endpoints | `GET/PATCH /workspace`, `GET/POST/DELETE /workspace/api-keys` |
| Telemetry | `workspace_api_key_created`, `settings_changed` (debounced 1/5s per tab) |

---

## 7. Component inventory

From `design-source/project/screens/*.jsx`. shadcn components are listed by their shadcn name; custom components have no shadcn equivalent and must be built from scratch.

### 7.1 Shell

| Component | Source |
|---|---|
| Sidebar | `screens/sidebar.jsx` |
| Topbar | inlined per-screen |
| Workspace switcher | `sidebar.jsx` lines 77–86 |

### 7.2 Buttons (CSS `.btn--*`)

| Class | shadcn variant | Use |
|---|---|---|
| `.btn--primary` | `<Button variant="default">` | Single primary CTA per screen — `--tg-blue` |
| `.btn--ghost` | `<Button variant="outline">` | Secondary toolbar actions |
| `.btn--soft` | custom (Tailwind: `bg-blue-soft text-blue`) | Tertiary AI-flavored — `--tg-blue-soft` filled |
| `.btn--dark` | custom (Tailwind: `bg-slate-900 text-white`) | High-stakes launch (`#1a1d24`) |
| `.btn--sm` | `<Button size="sm">` | Compact 30px toolbar |
| `.btn--icon` | `<Button variant="ghost" size="icon">` | 36×36 icon-only — **must have `aria-label`** |

### 7.3 Surface

| Component | shadcn equivalent | Use |
|---|---|---|
| Card | `<Card>` | Universal surface |
| MetricCard | custom (`.metric` + `.metric__value`) | KPI cards |
| MiniMetric | custom (compact metric) | Inline 6-up rows |

### 7.4 Status + data display

| Component | shadcn | Use |
|---|---|---|
| Pill / Badge | `<Badge>` + variants | Status labels |
| StatusPill | custom wrapper on Badge with `status` prop | Conversation / sender / campaign state — see `common.jsx STATUS_STYLES` |
| Avatar | `<Avatar>` | Contact initials, gradient backgrounds |
| Table | `<Table>` from shadcn | Lists |
| Donut | **custom SVG** (`AccountHealthCard` `Donut`) | Account health % |
| CorridorBar | **custom** (3 stacked horizontal bars: green / yellow / red) | Sender rate-limit |
| Sparkline | **custom SVG** | Metric trend mini-chart |
| FunnelMini | **custom CSS-flex** | Inline sent→replied→leads bars |
| FunnelBars | **custom CSS-flex** with gradient | Campaign detail Overview |
| StackedAreaChart | **custom** | Daily funnel time-series |
| BarChart | **custom CSS-flex** | 24h hourly volume |
| **SankeyFunnel** | **custom inline SVG** (the only SVG chart) | Dashboard 5-stage funnel |
| VoiceBadge | **custom** | Agent voice baseline chip |
| ToneSlider | **custom range** | Bi-polar −50..+50 |
| TagInput | **custom** | Chip-style multi-input (Banlist, Triggers) |
| BuiltinSignalRow | **custom** | Read-only signal display with `type: "…"` mono badge |
| ToolEditorModal | **custom centered modal** | Snake_case-validated tool editor |

### 7.5 Forms (CSS `.field` `.input` `.textarea` etc.)

| Component | shadcn |
|---|---|
| Field wrapper | shadcn `<Form>` + `<FormField>` |
| Input | `<Input>` (38px height — override default) |
| Textarea | `<Textarea>` (autosize from `react-textarea-autosize` or shadcn) |
| Select | `<Select>` |
| Toggle | `<Switch>` (36×20) |
| Tabs | `<Tabs>` underline variant (44px) |
| RadioCard | **custom** — bordered button with icon + title + description, blue border + softer bg when selected |

### 7.6 AI-motion primitives

| Component | Use |
|---|---|
| `<live-dot>` | Pulse halo around live indicator |
| `<ai-shimmer>` | Gradient sweep for generation/loading |
| AI co-pilot panel | `linear-gradient(135deg, #f1eefb 0%, #e8f3fe 100%)` background |
| Launch overlay | Full-screen scrim + gradient icon + 5 sequential check/spinner stages |
| Thought trace pane | Inbox right pane |

---

## 8. Forms + validation

### 8.1 Pattern

```ts
// src/lib/validators/campaign.ts
import { z } from 'zod'

export const campaignSchema = z.object({
  name: z.string().min(1, 'Required').max(120),
  brief: z.string().max(2000).optional(),
  primary_goal: z.enum(['book_meeting', 'qualify_lead', 'get_click', 'engage']),
  audience_hints: z.string().max(2000).optional(),
  success_criteria: z.string().max(1000).optional(),
  agent_id: z.string().uuid(),
  folder_id: z.string().uuid(),
  sender_slugs: z.array(z.string()).min(1, 'Attach at least one account'),
  webhook_url: z.string().url().optional().or(z.literal('')),
  tools: z.array(z.object({
    id: z.string(),
    name: z.string().regex(/^[a-z][a-z0-9_]*$/, 'Must be lowercase snake_case'),
    description: z.string().max(500),
    parameters: z.array(z.object({
      name: z.string().regex(/^[a-z][a-z0-9_]*$/),
      type: z.enum(['string', 'number', 'boolean']),
      description: z.string(),
      required: z.boolean(),
    })),
  })).optional(),
})

export type CampaignFormValues = z.infer<typeof campaignSchema>
```

### 8.2 Hookup

```ts
const form = useForm<CampaignFormValues>({
  resolver: zodResolver(campaignSchema),
  defaultValues: { ... },
})

const onSubmit = form.handleSubmit(async (values) => {
  await api.post('/campaigns', values)
  qc.invalidateQueries({ queryKey: qk.campaigns.all() })
  toast.success('Campaign created')
  navigate(`/campaigns/${result.id}`)
})
```

### 8.3 Field UX rules

- **Input height:** 38px (`--input-h` from styles.css). **Never** use shadcn's default 40px.
- **Focus ring:** `box-shadow: 0 0 0 3px rgba(51, 144, 236, 0.12)` + `border-color: var(--tg-blue)`.
- **Error display:** inline below field, `--danger` color, 11.5/400, `role="alert"`, linked via `aria-describedby`.
- **Submit feedback:** button disabled + inline `<Spinner>` + text "Sending…" / "Saving…". On success → Sonner toast.
- **Required marker:** asterisk `*` in `--danger` color after label.
- **Trigger:** `onBlur` per-field, `onSubmit` for cross-field validation.

### 8.4 Wizards

- Steps rail (left, vertical) — current = `--tg-blue-soft` bg; completed = green check chip.
- Sticky bottom bar: **Back** (`--ghost`, disabled on step 1) + **Continue** (`--primary`) or **Launch** (`--dark`).
- `⌘+Enter` advances to next step from any focused field.
- Quit: top-right close (X) → AlertDialog "Discard {{N}} steps?" — except builder which offers "Save as draft".

---

## 9. Interaction patterns

### 9.1 Toasts (Sonner)

Mount once at root: `<Toaster position="top-right" />`.

| Variant | Background | Auto-dismiss |
|---|---|---|
| success | `--success-soft` + `#1e8a3a` text | 4s |
| info | `--bg` + `--border` | 5s |
| warning | `--warning-soft` + `#a86200` | 6s |
| error | `--danger-soft` + `#b8332a` + Retry action button | 8s |

Max 3 visible. **All toasts respect `prefers-reduced-motion`** (no slide).

### 9.2 Loading (4-tier policy)

1. **Initial page load** → shadcn `<Skeleton>` matching exact layout shape.
2. **In-flight mutation** → button disabled + inline spinner; list/table → 60% opacity overlay + centered spinner.
3. **Background refetch** (poll) → **NO indicator** (would flicker).
4. **Long-running async** (CSV import, QR poll, launch) → `<Progress>` bar OR sequential-stage overlay.

### 9.3 Empty states — 4-element formula (mandatory)

```tsx
<EmptyState
  icon={Megaphone}           // 1. lucide icon, 96×96, --text-faint
  heading="Launch your first campaign"   // 2. 16/600, no trailing period
  body="A campaign connects an agent, senders, and a contact folder with a schedule."  // 3. 1-2 sentences, what + why
  cta={{ label: 'Create campaign', onClick: () => navigate('/campaigns/new') }}  // 4. primary CTA (or text hint if no action)
/>
```

### 9.4 Error boundaries

- `<ErrorBoundary>` at AppShell level → fallback "Something went wrong" + Reload.
- TanStack Query `onError` → toast with mapped code.
- **401** → auto-redirect to `/login` + toast "Your session expired. Sign in again."
- **403** → inline Alert or `/forbidden` page.
- **404** → `/not-found` page + "Back to dashboard".
- **5xx** → toast destructive "Server is unreachable. Retry." + Retry action.

### 9.5 Destructive confirmations (3-tier)

| Risk | Pattern | Examples |
|---|---|---|
| Low | AlertDialog (2 buttons) | Revoke API key, delete unused agent, dismiss banner |
| Medium | AlertDialog + mandatory checkbox "I understand the consequences" | Delete sender/folder/campaign in draft |
| High | `<ConfirmDestructiveDialog>` with **type-the-name** pattern | Delete running campaign, sender with active campaign, cascade-delete folder with contacts |

### 9.6 Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `⌘K` / `Ctrl+K` | Command palette (v2 — v1 disabled stub) |
| `Esc` | Close modal / sheet / popover |
| `J` / `K` | Navigate up/down (inbox list, campaigns table) |
| `Enter` (inbox composer) | Send message |
| `Shift+Enter` (inbox composer) | New line |
| `⌘+Enter` / `Ctrl+Enter` (forms) | Submit / advance wizard step |

---

## 10. Telemetry

All events go through a thin wrapper. Backend whitelists 17 event names (Phase 05.1 ships the ingest endpoint + Core Value KPI query).

### 10.1 Client wrapper

```ts
// src/lib/telemetry.ts
import { v4 as uuidv4 } from 'uuid'
import { api } from './api'

type EventName =
  | 'magic_link_requested' | 'signup_completed' | 'sender_added'
  | 'contacts_imported' | 'csv_import_completed' | 'agent_created'
  | 'campaign_created' | 'campaign_launched' | 'campaign_paused' | 'campaign_resumed'
  | 'conversation_taken_over_by_human' | 'llm_trace_opened'
  | 'workspace_api_key_created' | 'settings_changed' | 'agent_voice_changed'
  | 'custom_tool_added' | 'dashboard_viewed'

interface EventPayload {
  event_id: string         // UUID, client-supplied for idempotency
  event: EventName
  client_ts: string        // ISO 8601 UTC
  props?: Record<string, unknown>
}

const queue: EventPayload[] = []

export function track(event: EventName, props?: Record<string, unknown>) {
  const payload: EventPayload = {
    event_id: uuidv4(),
    event,
    client_ts: new Date().toISOString(),
    props,
  }
  // Fire-and-forget; never blocks UI
  api.post('/telemetry/events', payload).catch(() => queue.push(payload))
}

// Flush on pagehide via sendBeacon (Pitfall 4)
window.addEventListener('pagehide', () => {
  if (queue.length === 0) return
  const blob = new Blob([JSON.stringify(queue.splice(0))], { type: 'application/json' })
  navigator.sendBeacon(import.meta.env.VITE_API_URL + '/telemetry/events', blob)
})
```

### 10.2 Full event list

See `telemetry-events.md` for one-line definitions of all 17 events + Core Value KPI computation. Trigger summary:

| Event | When |
|---|---|
| `magic_link_requested` | Login form submit |
| `signup_completed` | First `/auth/callback` success (workspace lazy-created). **T0 for Core Value.** |
| `sender_added` | Onboarding step 3 success (or `/accounts` modal step 4) |
| `contacts_imported` / `csv_import_completed` | CSV import stage 4 (aliases) |
| `agent_created` | `POST /agents` 201 |
| `campaign_created` | `POST /campaigns` 201 (final step) |
| `campaign_launched` | `POST /campaigns/{id}/start` 200. **T1 for Core Value.** |
| `campaign_paused` / `campaign_resumed` | Lifecycle POSTs |
| `conversation_taken_over_by_human` | `POST /disable-ai` OR first manual `/send` OR safety auto-pause |
| `llm_trace_opened` | "Show LLM trace" button click |
| `workspace_api_key_created` | `POST /workspace/api-keys` 201 |
| `settings_changed` | `PATCH /workspace` OR theme toggle (debounced 5s) |
| `agent_voice_changed` | PATCH on Voice tab Save |
| `custom_tool_added` | ToolEditorModal Save (new tool added) |
| `dashboard_viewed` | Dashboard mount (debounced 1/session via `sessionStorage`) |

### 10.3 Core Value KPI

`time_to_first_campaign_seconds = MIN(campaign_launched.server_ts) - MIN(signup_completed.server_ts)` per workspace. Query via `GET /api/v1/telemetry/core-value`. Target: < 600s for 80% of new users.

---

## 11. Error code mapping

Backend returns `{ detail: { code, message, ...extra } }`. Frontend maps `code` to user-friendly English. **Never display raw `message`** (it's internal English).

See `error-codes.md` for the full table. Quick reference:

| Code | HTTP | UI string | Handling |
|---|---|---|---|
| `TOKEN_EXPIRED` / `TOKEN_INVALID` | 401 | "Your session expired. Sign in again." | Auto-redirect to `/login` |
| `WORKSPACE_NOT_FOUND` | 404 | "Workspace not found" | Should not occur post-login |
| `CAMPAIGN_NOT_FOUND` | 404 | "Campaign not found" | Cross-workspace also returns this (silent isolation per Phase 1 D-04) |
| `SENDER_NOT_FOUND` | 404 | "Account not found" | UI calls them "accounts", not "senders" |
| `AGENT_NOT_FOUND` | 404 | "Agent not found" | |
| `FOLDER_NOT_FOUND` | 404 | "Folder not found" | |
| `CONVERSATION_NOT_FOUND` | 404 | "Conversation not found" | |
| `INVALID_TRANSITION` | 409 | "Can't change from {from} to {to}" | Surface `from`/`to` from detail |
| `SENDER_LOCK_CONFLICT` | 409 | "Sender {name} is locked by campaign {other}. Stop that campaign to free the sender." | Inline warning in builder step 3 |
| `NO_SENDERS_ATTACHED` | 422 | "Attach at least one account before launching" | Builder step 3 hint |
| `INVALID_PHONE` | 422 | "Phone number is invalid. Use +1 415 555 2810 format." | Onboarding step 1 |
| `ID_REQUIRED` | 422 | "Missing id parameter" | `/analytics/funnel` + `/analytics/llm` |
| `UNKNOWN_EVENT` | 400 | (debug — `console.warn`) | Should not reach user — fix client code |
| 5xx | 5xx | "Server is unreachable. Retry." | Sonner toast with Retry action |

---

## 12. Accessibility minimums

| Requirement | Implementation |
|---|---|
| Focus visible | `box-shadow: 0 0 0 3px rgba(51, 144, 236, 0.12)` + `--tg-blue` border on every interactive element. Never `outline: none` without replacement. |
| Tab order | Logical: topbar → sidebar → main content. Radix Dialog/Sheet auto-trap. |
| ARIA labels | All `.btn--icon` / `.tb__icon-btn` REQUIRE `aria-label`. Status pills get `role="status"` + `aria-live="polite"` when value changes. |
| Form labels | Every `<Input>` has matching `.field__label` via `htmlFor`. Hints via `aria-describedby`. Errors via `aria-invalid` + `aria-describedby`. |
| Contrast | All body text on white ≥ 4.5:1. `--text-faint` (2.9:1) restricted to ≥18px decorative use — never body. |
| Keyboard nav | Every action reachable without mouse. Combobox uses `cmdk` (arrow/Enter/Esc). |
| Screen reader | Unique `<title>` per route. Skip-to-content link at top (visually hidden until focus). Sidebar uses `<nav aria-label="Main navigation">`. |
| **Reduced motion (mandatory)** | `@media (prefers-reduced-motion: reduce) { .live-dot, .ai-shimmer { animation: none !important; transition: none !important; } }` |
| Color independence | Status conveyed by color AND icon + text. Never color-only. |
| Touch targets | Minimum 44×44px for primary buttons on mobile (media query bumps from 36px). |

**Lighthouse a11y goal: ≥ 90.** Before considering any screen done, run Lighthouse a11y audit.

---

## 13. Hard rules — AGENTS.md verbatim

Quoted from `AGENTS.md`. **Never break these.**

1. **Never invent backend types.** Import all `/api/v1/*` request/response types from `@/types/api.ts` (generated from backend OpenAPI). If a type is missing, the build is broken — flag it, do not guess.
2. **All forms use react-hook-form + zod.** Zod schemas in `src/lib/validators/*.ts`.
3. **All data fetching uses TanStack Query.** Cache keys = `['<resource>', ...params]`. `refetchInterval` only where UI-SPEC §5 says so (inbox = 10s, dashboard = 30s).
4. **Design tokens come from `src/styles/aimly.css`** (ingest `design-source/project/styles.css` verbatim). Do not redefine tokens.
5. **No new motion libraries.** CSS keyframes (`pulse-ring`, `shimmer`) only. Always wrap in `prefers-reduced-motion`.
6. **Rate limits 4/20/150 are not configurable in v1** (Pitfall 9). Never offer a UI control to change them. Hard backend constraint per CLAUDE.md.
7. **AI accent `--ai-purple` is reserved.** Only on `<live-dot>`, `<ai-shimmer>`, thought-trace, AI co-pilot panel, launch overlay, AI suggestion chips.
8. **Brand "aimly" is lowercase, no exclamation marks.**
9. **No `console.log` in committed code.** Use `import.meta.env.DEV` guards if needed.
10. **All telemetry events go through `track(event, props)`** from `src/lib/telemetry.ts` — POST to `/api/v1/telemetry/events`. Use `navigator.sendBeacon` on `pagehide` (Pitfall 4).

---

## 14. Out of scope (do NOT build in v1)

- Push notifications
- Mobile-native shell (iOS / Android)
- WebSocket inbox (10s poll is enough — UI-SPEC §0)
- Multi-user invitations / multiple users per workspace (v2)
- Dark theme (v2 — leave Appearance toggle stub, only Light wired)
- ⌘K command palette (v2 — disabled stub with tooltip)
- HMAC webhook signing
- LLM-driven campaign auto-fill (v1 stub returns seeded defaults — button still wired)
- GDPR data export portal (v1.1 — mailto link placeholder OK)
- Multi-step follow-up sequences
- A/B testing message variants
- CSV analytics export

---

## 15. Build order — fixed

See `screen-build-order.md`. **Build sequentially**, smoke-test each against staging backend before starting the next:

1. **Auth** (`/login`, `/auth/callback`) — UI-SPEC §5.1
2. **Settings** (`/settings`) — UI-SPEC §5.11 (so Sign Out works during dev)
3. **Onboarding** (`/onboarding`) — UI-SPEC §5.2
4. **TG accounts** (`/accounts`) — UI-SPEC §5.10
5. **Contacts** (`/contacts`) — UI-SPEC §5.9
6. **Agents** (`/agents`, `/agents/{id}`) — UI-SPEC §5.8
7. **Campaigns list** (`/campaigns`) — UI-SPEC §5.4
8. **Campaign builder** (`/campaigns/new`) — UI-SPEC §5.5
9. **Campaign detail** (`/campaigns/{id}`) — UI-SPEC §5.6
10. **Inbox** (`/inbox`, `/inbox/{id}`) — UI-SPEC §5.7 (UI-INBX-01 — backend already shipped in Phase 5)
11. **Dashboard** (`/`) — UI-SPEC §5.3 (last because it depends on all other data shapes)

---

## 16. Per-screen acceptance gate

Before marking any screen done:

- [ ] Lighthouse accessibility ≥ 90
- [ ] `prefers-reduced-motion` CSS guard present and tested
- [ ] All icon-only buttons have `aria-label`
- [ ] Empty states have icon + heading + body + CTA (4-element formula)
- [ ] Error envelope `{code, message}` mapped via `error-codes.md` copy
- [ ] 401 redirects to `/login` with toast "Your session expired. Sign in again."
- [ ] Telemetry events fire on documented triggers (verify in network panel)
- [ ] Polling cadence matches UI-SPEC §5 (inbox 10s, dashboard 30s, etc.)
- [ ] Tabular numerals on all numeric values (no width jitter on refetch)
- [ ] No `console.log` / `console.warn` outside `import.meta.env.DEV` guards
- [ ] All forms use react-hook-form + zod (no raw `<form onSubmit>` with manual validation)
- [ ] Types imported from `@/types/api.ts` — no inline shape definitions

---

## 17. Companion files in this folder

| File | Purpose |
|---|---|
| `PRD.md` | **This file** — the master spec. Read first. |
| `AGENTS.md` | Concrete build rules + pitfalls + Inbox build section + per-screen verification gate |
| `KNOWLEDGE.md` | Project background — what aimly is, persona, entities, architecture |
| `README.md` | Setup + how to ingest the bundle into Lovable |
| `screen-build-order.md` | Build order + per-screen Lovable prompts |
| `error-codes.md` | Backend code → UI string mapping |
| `telemetry-events.md` | 17 event triggers + Core Value KPI |
| `reconciliation.md` | UI-SPEC path patches (where UI-SPEC and backend disagree, backend wins) |
| `design-source/` | `styles.css` + `screens/*.jsx` references + `uploads/*.png` mockups (populated when `bash scripts/export-handoff.sh` runs server-side) |
| `openapi.json` + `types/api.ts` | Generated from backend OpenAPI on the same export run |

**Authoritative UI spec**: `.planning/phases/05.1-lovable-ui-v1/05.1-UI-SPEC.md` — full pixel detail per screen. Open it when this PRD points to "UI-SPEC §X.Y".

---

## 18. Quickstart for Lovable

```text
1. Create a new Lovable project (React + Vite + Tailwind + shadcn).
2. Paste this PRD into the chat:
   "Build the aimly frontend per this PRD: <paste contents of PRD.md>.
    Companion files attached: AGENTS.md, KNOWLEDGE.md, screen-build-order.md,
    error-codes.md, telemetry-events.md, reconciliation.md.
    Tokens: ingest design-source/project/styles.css verbatim.
    Backend OpenAPI: openapi.json (use it to generate src/types/api.ts via openapi-typescript@7)."
3. Build Auth screen first. Verify magic link round-trip against staging.
4. Build Settings second (so Sign Out works during dev).
5. Continue in the order listed in §15.
6. Run the per-screen acceptance gate (§16) before moving on.
```

---

*End of PRD. Questions or contradictions vs UI-SPEC §5 → UI-SPEC wins (it has pixel detail). Questions on data shapes → `types/api.ts` wins (it's generated from the live backend).*
