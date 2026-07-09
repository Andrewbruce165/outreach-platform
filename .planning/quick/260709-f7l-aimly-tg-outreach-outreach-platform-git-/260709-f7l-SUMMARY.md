---
phase: quick-260709-f7l
plan: 01
subsystem: frontend-monorepo / deploy / infra
tags: [monorepo, git-subtree, tanstack-start, spa, nginx, vps-deploy, cloudflare-decommission, supabase-auth]
requires:
  - frontend repo AGS-Venture-Lab/aimly-tg-outreach (TanStack Start + Vite + bun + shadcn)
  - existing aimly.agsventurelab.com nginx vhost (SNI stream -> :8444 ssl proxy_protocol -> api :8005)
  - Docker + oven/bun:1 image (host has no bun)
provides:
  - frontend/ subtree in the backend monorepo with preserved git history
  - static SPA build (nitro:false + tanstackStart.spa.enabled) -> dist/client/{_shell.html,assets/}
  - deploy-frontend.sh (Docker bun build -> rsync dist/client/ to /var/www/aimly)
  - aimly vhost rewired: static SPA at /, /api/ reverse-proxied to 127.0.0.1:8005
  - updated CLAUDE.md (Стек / Git & Deploy / Сетевая топология / Lovable quirks)
affects:
  - frontend/vite.config.ts
  - frontend/.env
  - deploy-frontend.sh
  - /etc/nginx/sites-available/aimly.agsventurelab.com (not repo-tracked)
  - /var/www/aimly (filesystem webroot)
  - CLAUDE.md
tech-stack:
  added: []
  patterns:
    - "git subtree add --prefix=frontend to fold an unrelated-history repo in with history"
    - "TanStack Start SPA mode via @lovable.dev/vite-tanstack-config (nitro:false + spa.enabled)"
    - "Docker bun build stage (host has no bun) -> rsync static bundle to nginx webroot (vitrina pattern)"
    - "additive, backup-first, reversible prod nginx vhost edit behind human-verify checkpoint"
key-files:
  created:
    - deploy-frontend.sh
    - frontend/ (subtree, whole frontend repo)
  modified:
    - frontend/vite.config.ts
    - frontend/.env
    - CLAUDE.md
    - /etc/nginx/sites-available/aimly.agsventurelab.com (filesystem, .bak taken)
decisions:
  - "Route A (static SPA), not SSR: SSR here is inert (zero server functions, all fetch client-side) so the Cloudflare Workers deploy plugin is dropped with no functional loss."
  - "Build output is dist/client/ with shell _shell.html (NOT index.html) — this is what SPA mode emits; nginx try_files falls back to /_shell.html and rsync publishes dist/client/."
  - "Old frontend repo (AGS-Venture-Lab/aimly-tg-outreach) + its Cloudflare deploy LEFT INTACT as archive/rollback; aimly-frontend remote kept for future subtree pulls."
  - "Every prod-touching step reversible: timestamped vhost .bak, SNI stream + TLS untouched, no certbot run, no docker down -v."
  - "Supabase Auth Redirect URLs allow-list must include https://aimly.agsventurelab.com/** (discovered live at checkpoint, not in original plan)."
metrics:
  tasks: 6
  completed: 2026-07-09
---

# Quick 260709-f7l: Fold Lovable Frontend Into the Monorepo + VPS SPA Deploy

Folded the Lovable-generated frontend (`AGS-Venture-Lab/aimly-tg-outreach`, TanStack Start + Vite + bun + shadcn) into the backend monorepo (`Andrewbruce165/outreach-platform`) as a history-preserving `frontend/` subtree, converted the build from a Cloudflare Workers SSR target to a static SPA, and cut `aimly.agsventurelab.com` over to serving that SPA from the VPS nginx + Docker stack — mirroring the `vitrina` service. Result: one repo, one `git log`, one manual deploy path for back+front; Cloudflare/Lovable is no longer the frontend source of truth.

## What was built

- **Task 1 — history-preserving subtree.** Cleaned the pre-existing tracked working-tree changes via `git stash` (untracked files left in place — they don't block `git subtree`), branched `frontend-monorepo-migration`, added remote `aimly-frontend`, and `git subtree add --prefix=frontend aimly-frontend main` (merge commit `c176901`). `git log -- frontend/` shows the full imported frontend history; node_modules/dist/.tanstack excluded (gitignored upstream). Stash restored afterward. `aimly-frontend` remote retained for future subtree pulls / rollback reference. `frontend/.env` reviewed — only browser-public Supabase URL + anon key, no server secret leak.
- **Task 2 — static SPA build.** `frontend/vite.config.ts` set to `nitro: false` (drops the Cloudflare Workers deploy plugin) + `tanstackStart: { spa: { enabled: true } }`. `frontend/.env` `VITE_BACKEND_URL=""` (empty ⇒ same-origin relative `/api/...`, no CORS). Docker bun build (`oven/bun:1`, `bun install --frozen-lockfile && bun run build`) succeeds and emits **`dist/client/_shell.html` + hashed `dist/client/assets/`** with no Workers server module required to serve. Commit `3e42fa6`.
- **Task 3 — deploy script + webroot.** `deploy-frontend.sh` (executable) builds in the Docker bun stage and `rsync -a --delete dist/client/ /var/www/aimly/`. Ran once → `/var/www/aimly/{_shell.html,assets/}` populated. Non-disruptive: writes only to a webroot nginx did not yet serve. Commit `6abac9f`.
- **Task 4 — additive nginx cutover (prod, reversible).** Backed up the live vhost to `/etc/nginx/sites-available/aimly.agsventurelab.com.bak.20260709-112740`. Edited only the `:8444 ssl proxy_protocol` server block: added `root /var/www/aimly;`, `location /api/ { proxy_pass http://127.0.0.1:8005; ... }`, and `location / { try_files $uri /_shell.html; }`. `listen ... proxy_protocol`, all `ssl_*`, security-headers, the `:80` acme/redirect block, and the `:443` SNI stream dispatcher were left untouched; no certbot run. `nginx -t` passed, `systemctl reload nginx`. Local smoke: `/` → 200 HTML shell, `/api/v1/health` → 200 (API/SNI/TLS path intact).
- **Task 5 — human-verify checkpoint (APPROVED).** User verified on the live domain: SPA shell loads, Supabase magic-link login completes, deep-link hard-refresh survives (try_files fallback), and inbox/senders/campaigns data + a real message send all succeed same-origin. Independently confirmed via nginx access logs + `outreach-platform-api` container logs: GET `/inbox`, `/api/v1/senders|campaigns|agents|conversations` all 200 (referer aimly.agsventurelab.com), POST `/api/v1/conversations/.../send` 200 with a real Telethon `send_by_id`.
- **Task 6 — docs.** Updated `CLAUDE.md`: **Стек** (frontend now under `frontend/`, SPA build, Cloudflare plugin off, Lovable no longer source of truth), **Git & Deploy** (frontend in `Andrewbruce165/outreach-platform`, `./deploy-frontend.sh` step, upstream repo + Cloudflare retained as archive/rollback, corrected the stale "independent sibling repo" note), **Сетевая топология** (SPA-at-`/` + `/api/`→`:8005` routing, unchanged SNI/TLS chain, vhost `.bak` rollback convention), and **Lovable quirks** (monorepo note + the Supabase Redirect-URL gotcha below).

## Deviations from Plan

- **Shell filename confirmed as `_shell.html`, not `index.html`.** The plan flagged this as a MEDIUM-confidence item resolvable only after the Task 2 build. SPA mode emitted `dist/client/_shell.html`; the deploy rsync source (`dist/client/`) and the nginx fallback (`try_files $uri /_shell.html`) were set accordingly. Not a defect — the plan explicitly anticipated substituting the real name.
- **Supabase Auth Redirect URLs allow-list (discovered live at the checkpoint, NOT in the plan/research).** Project ref `qhxkyzmwnehnrfndpxxo`. The allow-list was missing the new domain, so the magic-link redirected to the old lovable.app origin and login on `aimly.agsventurelab.com` failed until `https://aimly.agsventurelab.com/**` was added. Fixed live by the user; documented in CLAUDE.md (Lovable-фронт quirks) with a "update this first if the domain ever changes" warning.

## Verification

- `git log -- frontend/` shows preserved frontend history; `frontend/` has `package.json`, `vite.config.ts`, `src/`; no node_modules/dist imported.
- `frontend/vite.config.ts` has `nitro:false` + `spa`; Docker bun build emits `dist/client/_shell.html` + `assets/`, no Workers server module.
- `deploy-frontend.sh` executable; `/var/www/aimly/{_shell.html,assets/}` present.
- Vhost `.bak.20260709-112740` exists; vhost serves `/var/www/aimly` at `/` + proxies `/api/` to `:8005`; `listen ... proxy_protocol` + `ssl_*` unchanged; `nginx -t` passes.
- Through the domain: `/` → 200 HTML, `/api/v1/health` → 200. Human checkpoint confirmed login + deep-link refresh + live same-origin API calls (all 200, not Cloudflare).
- CLAUDE.md Task 6 automated check passes (`frontend/`, `deploy-frontend.sh`, `/var/www/aimly` all present).

## NOT touched (safety envelope held)

`:443` SNI stream block, TLS certs (no certbot run), the old `AGS-Venture-Lab/aimly-tg-outreach` repo, the Cloudflare deploy, and the API container (no `docker compose down -v`). Rollback for the one prod-touching change (vhost) is: restore the `.bak` + `nginx -t && systemctl reload nginx`.

## Known Stubs / Deferred

- `frontend/src/server.ts` (SSR error wrapper) is now dead code under SPA mode; left on disk (removing dead Cloudflare artifacts is optional cleanup, out of scope).
- The old frontend repo + Cloudflare deploy remain live as a fallback; decommissioning them is a deliberate out-of-scope follow-up.

## Notes for whoever pushes

Not pushed to origin (per plan — push only when the user asks). Task commits: `c176901` (subtree), `3e42fa6` (SPA config), `6abac9f` (deploy-frontend.sh), plus the Task 6 CLAUDE.md commit. The working tree also carries UNRELATED changes from other/parallel activity (`.planning/codebase/*` deletions, debug `.md` edits, untracked files) — per the repo's parallel-agent rule, stage specific files only; never `git add -A`.
