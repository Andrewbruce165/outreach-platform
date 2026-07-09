---
phase: quick-260709-f7l
verified: 2026-07-09T13:10:00Z
status: passed
score: 7/7 must-haves verified
---

# Quick Task 260709-f7l: Fold Lovable Frontend Into Monorepo + VPS SPA Deploy — Verification Report

**Task Goal:** Перенести фронтенд aimly-tg-outreach в монорепо outreach-platform (git subtree, подпапка frontend/, история сохранена), конвертировать в SPA static build, деплой на VPS рядом с API (nginx, паттерн vitrina), отказ от Lovable/Cloudflare как источника деплоя.
**Verified:** 2026-07-09
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Frontend source lives under `frontend/` in the monorepo with git history preserved | ✓ VERIFIED (with a documented nuance) | `frontend/package.json`, `vite.config.ts`, `src/` exist on disk. `c176901` is a genuine 2-parent subtree merge (`git-subtree-dir: frontend`, `git-subtree-split: 456515f...`). The 342-commit frontend history is fully reachable and browsable (`git log 456515f...` → 342 commits; `git log --graph --all` shows the full chain back through `2b8e89f`, `ae0a9f7`, etc.). **Nuance:** the literal command in the must-have, `git log -- frontend/`, only returns 2 commits (the merge + one post-import edit) — this is inherent, expected `git subtree add` behavior (pre-import commits have trees rooted at `/`, not `frontend/`, so simple pathspec filtering doesn't walk through them), not an executor defect. History is preserved and inspectable, just not via that exact command. |
| 2 | vite build produces a static SPA bundle (client assets + shell HTML), no Cloudflare Workers server module, no required Node runtime | ✓ VERIFIED | `frontend/vite.config.ts`: `nitro: false` + `tanstackStart: { spa: { enabled: true } }`. Live build output `/var/www/aimly/{_shell.html,assets/*.js,*.css}` confirmed on disk. `dist/server` (inert SSR bundle) exists but is documented as not served (deploy-frontend.sh only rsyncs `dist/client/`). |
| 3 | aimly.agsventurelab.com serves the SPA shell at `/` (200, HTML) and hashed assets under `/assets/` | ✓ VERIFIED | Live curl: `GET /` → `200 text/html`; `GET /assets/index-CrWKbjl_.js` → `200 application/javascript`. |
| 4 | Deep-link client routes survive a hard refresh (nginx try_files fallback) | ✓ VERIFIED | Live curl: `GET /inbox/foo/bar` (nonexistent path) → `200 text/html` (served `_shell.html` via `try_files $uri /_shell.html;`), not a 404. |
| 5 | Frontend API calls reach the backend same-origin at `/api/v1/...` and succeed | ✓ VERIFIED | Live curl: `GET /api/v1/health` through the domain → `200 {"status":"healthy","database":"connected",...}`. Human checkpoint (already approved) additionally confirmed real authenticated GET/POST traffic (senders/campaigns/inbox lists + a real Telethon send) via nginx access log + API container log cross-check. |
| 6 | SNI stream chain and TLS cert untouched; API backend on 127.0.0.1:8005 still reachable | ✓ VERIFIED | `/etc/nginx/nginx.conf` `stream {}` block (SNI dispatcher) mtime `2026-05-26` — untouched by this task. `/etc/letsencrypt/renewal/aimly.agsventurelab.com.conf` mtime `2026-05-23` — untouched, no certbot run. `certbot.timer` active. API container `outreach-platform-api` StartedAt `2026-07-09T10:01:40Z`, uninterrupted through the cutover (no restart, no `down -v`). |
| 7 | CLAUDE.md Стек / Git & Deploy / Сетевая топология describe the monorepo frontend and VPS deploy path | ✓ VERIFIED | Read current `CLAUDE.md` on disk: Стек section documents `frontend/` subtree + SPA build + Cloudflare-off; Git & Deploy documents `./deploy-frontend.sh` and the retained-but-inactive upstream repo; Сетевая топология documents the new `root /var/www/aimly` + `location /api/` routing and `.bak` rollback convention. Content matches what was actually deployed (verified against live nginx config and filesystem, not just asserted). |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `frontend/vite.config.ts` | SPA-mode config (`nitro:false` + `spa`) | ✓ VERIFIED | Contains both `nitro: false` and `tanstackStart: { spa: { enabled: true } }`, with an explanatory comment. |
| `deploy-frontend.sh` | Docker-bun build + rsync to `/var/www/aimly` | ✓ VERIFIED | Executable (`-x`); builds via `oven/bun:1` (`bun install --frozen-lockfile && bun run build`); `rsync -a --delete dist/client/ /var/www/aimly/`. Matches the vitrina deploy pattern. |
| `/var/www/aimly` | Static SPA webroot | ✓ VERIFIED | Contains `_shell.html` + `assets/` (40+ hashed JS/CSS chunks), mtime `Jul 9 11:26` (matches the documented cutover time). |
| `/etc/nginx/sites-available/aimly.agsventurelab.com` | `root` + `location /api/` proxy + SPA fallback | ✓ VERIFIED | Live file matches the plan's `<target_vhost>` exactly: `root /var/www/aimly;`, `location /api/ { proxy_pass http://127.0.0.1:8005; ... }`, `location / { try_files $uri /_shell.html; }`. `listen 127.0.0.1:8444 ssl proxy_protocol` + all `ssl_*` lines + `:80` acme block unchanged. `nginx -t` passes. |
| `.bak` of the vhost | Rollback point | ✓ VERIFIED | `aimly.agsventurelab.com.bak.20260709-112740` exists; `diff` against the live file shows exactly the documented additive change (root + /api/ block + try_files), nothing else touched. |
| `CLAUDE.md` | Updated monorepo/deploy docs | ✓ VERIFIED | `frontend/`, `deploy-frontend.sh`, `/var/www/aimly` all present and contextually accurate (see Truth 7). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| nginx `location /api/` | `127.0.0.1:8005` | `proxy_pass` | ✓ WIRED | Config present; live `GET /api/v1/health` through the domain returns 200 from the real backend. |
| nginx `location /` | `/var/www/aimly` | `try_files` fallback | ✓ WIRED | `root /var/www/aimly;` + `try_files $uri /_shell.html;`; live deep-link probe returns 200 HTML, not 404. |
| `frontend/vite.config.ts` | static SPA output | `nitro:false` + `spa` enabled | ✓ WIRED | Build actually ran (Docker bun) and produced the expected `dist/client/{_shell.html,assets/}`, which is what's live in `/var/www/aimly` (identical shell content/asset hashes observed). |
| `frontend/.env` `VITE_BACKEND_URL=""` | same-origin `/api/...` calls | build-time env inlining | ✓ WIRED | `.env` confirmed empty `VITE_BACKEND_URL`; live shell/asset requests are same-origin (no CORS errors implied by human-verify checkpoint's confirmed authenticated API traffic). |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| SPA shell serves at root | `curl -sko /dev/null -w '%{http_code} %{content_type}' https://aimly.agsventurelab.com/` | `200 text/html` | ✓ PASS |
| Deep-link survives hard refresh | `curl .../inbox/foo/bar` | `200 text/html` (shell, not 404) | ✓ PASS |
| API reachable same-origin | `curl https://aimly.agsventurelab.com/api/v1/health` | `200 {"status":"healthy",...}` | ✓ PASS |
| Hashed asset served | `curl .../assets/index-CrWKbjl_.js` | `200 application/javascript` | ✓ PASS |
| `nginx -t` syntax valid | `nginx -t` | `syntax is ok / test is successful` | ✓ PASS |
| API container uninterrupted | `docker inspect outreach-platform-api` | `StartedAt 2026-07-09T10:01:40Z`, `Up 2 hours`, no restart around cutover time (11:27) | ✓ PASS |
| SNI stream block untouched | `stat /etc/nginx/nginx.conf` + content read | mtime `2026-05-26`, generic `default → 127.0.0.1:8444` mapping, no aimly-specific edit | ✓ PASS |
| Old frontend repo intact | `git log` in `/root/apps/aimly/aimly-tg-outreach` | HEAD `456515f`, matches the exact commit that was subtree-imported; `wrangler.jsonc` present unmodified | ✓ PASS |

### Requirements Coverage

Quick task, requirement `QT-260709-f7l` (self-contained, not tracked in phase-based `.planning/REQUIREMENTS.md`). Covered by the task's own success criteria, all satisfied per the truths/artifacts above.

### Anti-Patterns Found

None blocking. Scanned `frontend/vite.config.ts`, `deploy-frontend.sh`, `CLAUDE.md` diff, nginx vhost diff — no TODO/stub/placeholder patterns, no empty handlers, no hardcoded-empty data paths relevant to this task's scope.

ℹ️ Info: `frontend/src/server.ts` (SSR error wrapper) is dead code under SPA mode, left on disk — explicitly documented in the SUMMARY as an intentional, out-of-scope deferral, not a stub introduced by this task.

### Human Verification Required

None outstanding — the blocking human-verify checkpoint (Task 5) was already completed and approved live in conversation (login via Supabase magic link, deep-link hard-refresh, authenticated data loads, and a real message send all confirmed, cross-checked against nginx access logs and API container logs). No further human action needed for this task's scope.

### Gaps Summary

No gaps. One informational nuance is worth carrying forward (not a defect, not blocking):

- **Not yet merged/pushed to `main`/`origin`.** Per the plan (Task 1, step 2 — explicitly instructed), all work happened on a dedicated branch `frontend-monorepo-migration`, currently 4 commits ahead of `main` (`c176901` subtree merge, `3e42fa6` SPA config, `6abac9f` deploy-frontend.sh, `f86ee6e` CLAUDE.md+SUMMARY) and not merged into `main` or pushed to `origin`. This is intentional per the plan's Output section ("Do NOT push to origin in this task — pushing happens only when the user asks") and the SUMMARY's closing note. The live deployed artifacts (`/var/www/aimly`, the nginx vhost) already reflect this branch's `frontend/` content, so production is not blocked by this — but `main`/`origin` do not yet have the monorepo frontend until the user asks for the merge/push. Flagging so it isn't forgotten, not as a task failure.
- **`git log -- frontend/` (the literal command named in the plan's must-have) shows only 2 commits, not the full imported history** — this is inherent `git subtree add` behavior (see Truth 1 evidence), not a defect. The actual history is preserved and browsable via `git log <split-sha>` or `git log --graph --all`. Anyone relying on the literal must-have command for future audits should know to use the split SHA or `--all` instead.

---

_Verified: 2026-07-09_
_Verifier: Claude (gsd-verifier)_
