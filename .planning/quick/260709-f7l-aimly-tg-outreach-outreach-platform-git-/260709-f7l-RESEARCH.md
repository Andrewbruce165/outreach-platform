# Quick Task 260709-f7l: Frontend → Monorepo Migration - Research

**Researched:** 2026-07-09
**Domain:** git subtree, TanStack Start build targets, nginx static SPA serving on the aimly VPS chain
**Confidence:** HIGH (all findings verified against the actual repos/infra on this box; SPA-mode conversion is MEDIUM — needs one test build)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Монорепо: перенести фронт как подпапку в `outreach-platform` через `git subtree`, сохранив git-историю.
- После переноса: один remote (`Andrewbruce165/outreach-platform`), один `git log`, единый деплой.
- Старый репо `AGS-Venture-Lab/aimly-tg-outreach` НЕ удаляется — остаётся архивом/точкой отката.
- Деплой фронта переезжает с Cloudflare на VPS — раздача через тот же nginx/docker-compose стек, что и API (`aimly.agsventurelab.com`).
- SPA-роуты не должны ломаться (fallback на index.html для client-side routing). API-запросы фронта продолжают идти на существующий backend (`127.0.0.1:8005`).
- Встроиться в существующую SNI-цепочку (`:443 → SNI stream → nginx:8444 ssl proxy_protocol → 127.0.0.1:8005 → api:8000`) БЕЗ поломки текущего API-роутинга.
- Полный переход на прямые правки кода. Lovable больше не источник правды.

### Claude's Discretion
- Имя подпапки (`frontend/`, `web/`, `apps/web/`) — не конфликтующее с `app/ docs/ migrations/ scripts/ tests/`.
- Механизм сборки/деплоя (Dockerfile+service vs multi-stage → volume для nginx) — простейший, согласованный с паттерном `vitrina`/`funnel-dashboard-api`.
- Переносить ли CI/CD workflow фронта — выбрать вариант, максимально похожий на текущий ручной деплой бэка.
- Обновить `CLAUDE.md` («Стек», «Git & Deploy», «Сетевая топология») под монорепо.

### Deferred Ideas (OUT OF SCOPE)
- Не трогать Cloudflare-конфиг, завязанный на DNS/CDN не относящийся к хостингу самого SPA.
</user_constraints>

## Summary

The frontend (`/root/apps/aimly/aimly-tg-outreach`) is a **TanStack Start SSR app** (React 19, Vite 7, `@tanstack/react-start` 1.168) built for **Cloudflare Workers** via `@cloudflare/vite-plugin` + Nitro (`cloudflare-module` preset, entry `src/server.ts`). Its build output is `dist/server/server.js` (a Workers-runtime `export default { fetch }` module) + `dist/client/assets` — crucially **there is no `index.html`**, so it cannot be served statically as-is, and the server bundle will not run under plain `node`.

**Decisive finding: the app has ZERO server functions.** `grep` for `createServerFn`/`createServerFileRoute`/`use server` returns nothing. All data fetching is client-side: the browser calls the FastAPI backend via `VITE_BACKEND_URL` (`src/lib/api.ts`, `telemetry.ts`) and Supabase via `VITE_SUPABASE_*`. SSR here does nothing but render the initial shell — no loaders hit a server, no server-only secrets. For an authenticated internal SaaS behind a login, SSR provides no functional value.

Therefore the clean path (and the one CONTEXT already anticipates with "fallback на index.html") is to **convert the build to TanStack Start SPA mode** — a static client bundle + prerendered shell — and serve it exactly like the existing **`vitrina`** service does (static files in `/var/www/...` + nginx same-origin `/api/` proxy). This adds **no running Node container**. A `node-server` SSR container is a documented fallback if SPA-mode conversion fights the Lovable config wrapper.

**Primary recommendation:** subtree the repo into `frontend/`, switch the TanStack Start build to **SPA mode** (`spa: { enabled: true }` + drop the Cloudflare deploy plugin via the Lovable wrapper's `nitro: false`), and follow the `vitrina` nginx+deploy pattern verbatim: static SPA at `/`, `/api/` reverse-proxied to `127.0.0.1:8005`.

## Finding 1: git subtree mechanics (HIGH)

Verified the two repos have **unrelated histories** — the right precondition for `subtree add` (not a plain copy):
- Frontend root commit: `86becaa` (`template: tanstack_start_ts_2026-05-12`), remote `AGS-Venture-Lab/aimly-tg-outreach`, 342 commits.
- Backend root commit: `54430ec` (`init: base from telegram-api`), remote `Andrewbruce165/outreach-platform`.
- `git version 2.51.0` — `git subtree` **available**.
- No subfolder collision: backend top-level dirs are `app docs lovable-handoff migrations scratchpad scripts tests`. Recommended prefix **`frontend/`** (mirrors `vitrina`'s `api/` + `frontend/` layout exactly).

**Commands (run in `/root/apps/aimly/tg-outreach`, on a branch, clean tree):**
```bash
git remote add aimly-frontend https://github.com/AGS-Venture-Lab/aimly-tg-outreach.git
git fetch aimly-frontend
git subtree add --prefix=frontend aimly-frontend main
# optional: keep the remote for future pulls, or drop it:
# git remote remove aimly-frontend
```
- Full history is preserved and browsable via `git log -- frontend/`. Paths are rewritten under the prefix in the merge.
- Only **tracked** files are imported — `node_modules/`, `dist/`, `.tanstack/`, `.output/` are gitignored in the frontend repo, so they won't come across (good).
- The frontend's `.env` **is tracked** and holds real values, but they are browser-public by design (Supabase URL + anon key are meant to ship to the client) — no secret leak. Still, review before pushing.
- CLAUDE.md rule "never `git add -A`, parallel agents" applies — subtree stages its own merge commit cleanly; do the subtree on a dedicated branch and confirm `git status` before/after.

## Finding 2: build/serve strategy — SPA vs SSR (HIGH diagnosis, MEDIUM on chosen config)

**Current state (verified):**
- `wrangler.jsonc`: `main: src/server.ts` (Workers entry). `vite.config.ts` uses `@lovable.dev/vite-tanstack-config` which auto-injects `tanstackStart`, `viteReact`, `tailwindcss`, and the **Cloudflare build plugin** (default Nitro preset `cloudflare-module`).
- Build output: `dist/server/server.js` (Workers module) + `dist/client/assets/` — **no `index.html`** (confirmed: `dist/client` contains only `assets/`).
- **Zero server functions** (confirmed by grep). Routes under `src/routes/_authenticated/*` are all client file-routes with client-side auth guards.
- API base URL: `src/lib/api.ts` → `BACKEND_URL = VITE_BACKEND_URL ?? ""`, then `base = BACKEND_URL || ""`. **Empty ⇒ same-origin relative requests** — ideal for a co-located deploy.

**The Lovable config wrapper (`@lovable.dev/vite-tanstack-config` v2.7.1) exposes the levers we need** (verified in its `dist/index.js`):
- `nitro: false` → disables the deploy plugin entirely.
- `nitro: { preset: '...' }` → override the default `cloudflare-module` preset (e.g. `node-server`).
- `tanstackStart: { ... }` → passthrough to the TanStack Start plugin (we already use `tanstackStart: { server: { entry: "server" } }`).

### Route A — SPA static build (RECOMMENDED)
Since SSR is inert here, enable TanStack Start **SPA mode**, which emits a prerendered static shell + client bundle servable by nginx (verified against TanStack Start docs):
```ts
// vite.config.ts
import { defineConfig } from "@lovable.dev/vite-tanstack-config";
export default defineConfig({
  nitro: false,                    // drop the Cloudflare Workers deploy plugin
  tanstackStart: {
    spa: { enabled: true },        // prerender a static shell, client-only routing
  },
});
```
- SPA mode generates a static shell (default pathname `/`; TanStack emits a `_shell.html` / index-style shell) and configures 404-→shell rewrites — the SPA-fallback semantics CONTEXT asks for.
- Then serve statically like `vitrina`: `vite build` → rsync `dist/` (or `.output/public`) into `/var/www/aimly` → nginx `try_files $uri /index.html` (or `/_shell.html`).
- **No Node container.** `src/server.ts` (the SSR error wrapper) and `wrangler.jsonc` become dead and can be removed after conversion.
- **MEDIUM confidence:** exact static output dir and shell filename for this wrapper+version must be confirmed with one `bun run build` locally; adjust nginx `try_files` fallback and `rsync` source path to whatever the build emits (likely `dist/` root with `index.html`, or `_shell.html`).

### Route B — node-server SSR container (FALLBACK)
If SPA-mode conversion fights the wrapper, keep SSR and just retarget Nitro:
```ts
export default defineConfig({
  nitro: { preset: "node-server" },
  tanstackStart: { server: { entry: "server" } },
});
```
- Emits `.output/server/index.mjs` runnable via `node .output/server/index.mjs` (listens on `PORT`, default 3000).
- Run in Docker (`node:22-alpine`), bind e.g. `127.0.0.1:8007:3000`; nginx `location / { proxy_pass http://127.0.0.1:8007; }`.
- Faithful to current behavior; costs one always-on Node container + memory. Only choose if Route A's build won't produce clean static output.

**Recommendation: Route A.** It matches the sole existing frontend-on-this-box pattern (`vitrina`), needs no runtime service, and loses nothing because there are no server functions.

## Finding 3: nginx + docker-compose integration (HIGH)

**Existing `vitrina` pattern (the exact analog — a React SPA + FastAPI on this same box):**
- `vitrina/deploy.sh`: `cd frontend && npm run build` → `rsync -a --delete frontend/dist/ /var/www/vitrina/`; API via `cd api && docker compose up -d --build`.
- Two vhosts: `vitrina.agsventurelab.com` (SPA static + same-origin `/api` proxy + analytics proxy) and `vitrina-api.agsventurelab.com` (direct API). API on `127.0.0.1:8006`.

**Current aimly vhost** (`/etc/nginx/sites-available/aimly.agsventurelab.com`) — the whole domain currently proxies to the API:
```nginx
server {
    server_name aimly.agsventurelab.com;
    include snippets/security-headers.conf;
    client_max_body_size 10m;
    location / { proxy_pass http://127.0.0.1:8005; ... }   # ← currently ALL → API
    listen 127.0.0.1:8444 ssl proxy_protocol;              # ← behind SNI stream dispatcher
    ssl_certificate /etc/letsencrypt/live/aimly.agsventurelab.com/fullchain.pem; ...
}
```
The SNI chain (`:443 → SNI stream → 8444 ssl proxy_protocol`) and the TLS cert are **untouched** — only the vhost's `location` blocks change. Backend confirmed to serve everything under **`/api/v1/...`** (health at `/api/v1/health`; verified in `app/main.py`), so there is **no path collision** between SPA asset paths (`/`, `/assets/...`) and the API (`/api/`).

**Target aimly vhost (Route A):**
```nginx
server {
    server_name aimly.agsventurelab.com;
    include snippets/security-headers.conf;
    client_max_body_size 10m;
    root /var/www/aimly;

    # API — same-origin, forwarded to the existing backend container
    location /api/ {
        proxy_pass http://127.0.0.1:8005;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $proxy_protocol_addr;
        proxy_set_header X-Forwarded-For $proxy_protocol_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }

    # SPA static + client-side routing fallback
    location / {
        try_files $uri $uri/ /index.html;   # confirm shell filename after build (index.html vs _shell.html)
    }

    listen 127.0.0.1:8444 ssl proxy_protocol;   # unchanged
    ssl_certificate     /etc/letsencrypt/live/aimly.agsventurelab.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aimly.agsventurelab.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}
```
- Apply with `nginx -t && systemctl reload nginx` (NOT certbot `--nginx` — CLAUDE.md guard: certbot only `certonly --webroot`, else the SNI stream schema breaks).
- Add a build+rsync step to the deploy flow. Simplest, matching CLAUDE.md's manual backend deploy: a small `deploy-frontend.sh` (build in Docker → rsync into `/var/www/aimly`) or extend the existing `git pull && docker compose up -d --build` note. A dedicated `docker-compose` service is only needed for Route B.

## Finding 4: common pitfalls (HIGH unless noted)

| Pitfall | Detail | Mitigation |
|---------|--------|------------|
| **Build-time env vars** | `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_BACKEND_URL` are baked at `vite build` (browser `import.meta.env`). Not runtime-configurable. | For same-origin deploy set `VITE_BACKEND_URL=""` (⇒ relative `/api/...`, no CORS) or `https://aimly.agsventurelab.com`. Supabase URL+anon key stay (public by design). Provide via `.env`/Docker build-arg at build time. |
| **bun not on host** | Host has `node v22.22.2` + `docker`, but **`bun` is NOT installed**. `vitrina` builds with `npm`; this repo uses `bun.lock` + `bunfig.toml` (24h supply-chain guard). | Build inside a Docker stage (`oven/bun:1` image → `bun install --frozen-lockfile && bun run build`), OR install bun on the host, OR regenerate a npm lockfile. Docker build stage keeps the host clean and matches "no new host tooling". |
| **SPA fallback filename** (MEDIUM) | SPA mode may emit `_shell.html` rather than `index.html`; static output dir may be `dist/` or `.output/public`. | Run one test build, inspect output, set nginx `try_files … /<shell>` and `rsync` source accordingly. |
| **SNI/TLS chain** | Domain sits behind `:443 → SNI stream → 8444 ssl proxy_protocol`. Careless certbot `--nginx` or removing `proxy_protocol` breaks it. | Only edit `location` blocks; keep `listen 127.0.0.1:8444 ssl proxy_protocol`; renew via `certonly --webroot` only. |
| **Dead Cloudflare artifacts** | After Route A, `wrangler.jsonc` + `src/server.ts` (SSR error wrapper) are unused; `@cloudflare/vite-plugin`, `nitro` deps become dead weight. | Remove after conversion works (Discretion: leave Cloudflare DNS/CDN config untouched — only the SPA hosting moves). |
| **Asset caching** | Vite emits content-hashed `assets/*`. | Optional nginx `location /assets/ { expires 1y; add_header Cache-Control immutable; }`; keep the shell uncached so deploys take effect. |
| **openapi/types drift** | Repeated history note: the sibling repo's `types-openapi.json` / `src/types` drifts from the backend spec. Now co-located, keep them in sync from `lovable-handoff/openapi.json`. | Not blocking for this task; note for post-migration hygiene. |

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| git subtree | history-preserving merge | ✓ | git 2.51.0 | — |
| node | build / (Route B runtime) | ✓ | v22.22.2 | — |
| docker | build stage / API service | ✓ | 29.0.2 | — |
| bun | frontend install/build (`bun.lock`) | ✗ | — | Build in `oven/bun` Docker stage, or install bun, or npm-lockfile |
| nginx | static serve + `/api` proxy | ✓ | (host, active vhosts present) | — |

**Missing with fallback:** `bun` — use a Docker build stage (`oven/bun:1`) so the host needs nothing new.
**Missing, blocking:** none.

## Sources

### Primary (HIGH)
- Live inspection of `/root/apps/aimly/aimly-tg-outreach` (package.json, vite.config.ts, wrangler.jsonc, src/server.ts, src/lib/api.ts, dist/, grep for server functions, git roots/remotes).
- Live inspection of `/root/apps/aimly/tg-outreach/app/main.py` (`/api/v1` prefix, health route), git roots.
- `/root/apps/vitrina/deploy.sh`, `/root/apps/vitrina/CLAUDE.md`, `/root/apps/vitrina/api/docker-compose.yml` — the working SPA+FastAPI analog on this box.
- `/etc/nginx/sites-available/aimly.agsventurelab.com` (+ vitrina vhosts present).
- `@lovable.dev/vite-tanstack-config@2.7.1` `dist/index.js` — `nitro: false` / `nitro.preset` / `tanstackStart` passthrough options.
- Host probes: `git --version`, `node --version`, `docker --version`, `command -v bun` (absent).

### Secondary (MEDIUM — verify with one test build)
- [TanStack Start — SPA mode](https://tanstack.com/start/latest/docs/framework/react/guide/spa-mode) — `spa: { enabled: true }`, static shell + 404→shell rewrites.
- [TanStack Start — Static Prerendering](https://tanstack.com/start/latest/docs/framework/react/guide/static-prerendering)

## Metadata

**Confidence breakdown:**
- git subtree mechanics: HIGH — preconditions verified on the actual repos.
- SSR-is-inert diagnosis: HIGH — zero server functions confirmed by grep; API is client-side same-origin.
- SPA-mode build config: MEDIUM — option surface verified in the wrapper + docs, but exact static output/shell filename needs one local build.
- nginx/compose integration: HIGH — mirrors a live working service (`vitrina`); chain/prefix constraints verified.
- Pitfalls: HIGH (bun-absent, env-baking, SNI chain all verified) except SPA shell filename (MEDIUM).

**Research date:** 2026-07-09
**Valid until:** ~2026-08-09 (stable; TanStack Start SPA API and infra unlikely to shift in 30 days)
