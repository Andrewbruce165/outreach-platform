# Quick Task 260709-f7l: Перенести фронтенд aimly-tg-outreach в монорепо outreach-platform - Context

**Gathered:** 2026-07-09
**Status:** Ready for planning

<domain>
## Task Boundary

Перенести фронтенд-репозиторий `AGS-Venture-Lab/aimly-tg-outreach` (TanStack Start, React, TS, Vite, bun, shadcn — сейчас склонирован сиблингом в `/root/apps/aimly/aimly-tg-outreach`, генерится через Lovable, деплоится на Cloudflare) в монорепо `outreach-platform` (текущий репозиторий, `/root/apps/aimly/tg-outreach`, `Andrewbruce165/outreach-platform`). Цель: единое место для кода бэка и фронта, единый git-репозиторий с коммитами/пушами, единый деплой на VPS.

Это НЕ мгновенная задача — она трогает прод-инфраструктуру (nginx SNI-цепочка на `aimly.agsventurelab.com`, docker-compose стек). Разбита на несколько подзадач/коммитов, но остаётся одной quick-task (без отдельной ROADMAP-фазы).

</domain>

<decisions>
## Implementation Decisions

### Repo strategy
- Монорепо: перенести фронт как подпапку в `outreach-platform` (рекомендованный вариант).
- Использовать `git subtree` (или эквивалент) чтобы сохранить git-историю фронтенд-репозитория при переносе в подпапку (например `frontend/` или `web/`).
- После переноса: один git remote, один `git log`, коммиты/пуши/деплои — в одном месте (`Andrewbruce165/outreach-platform`).
- Старый репозиторий `AGS-Venture-Lab/aimly-tg-outreach` не удаляется сразу — остаётся как архив/точка отката, но перестаёт быть источником правок.

### Deploy target
- Фронт переезжает с Cloudflare (Workers/Pages, `wrangler.jsonc`) на VPS — раздаётся через тот же nginx/docker-compose стек, что и API (`aimly.agsventurelab.com`).
- Нужно: сборка фронта в Docker (bun install + vite build, или соответствующий TanStack Start build), сервис в `docker-compose.yml` (аналог `funnel-dashboard-api`/`vitrina` паттерна упомянутого в CLAUDE.md), маршрутизация в nginx-vhost так, чтобы SPA-роуты не ломались (fallback на index.html для client-side routing), API-запросы фронта продолжают идти на существующий backend (`/root/apps/aimly/tg-outreach` API, `127.0.0.1:8005`).
- Учитывать существующую сетевую топологию: `:443 → SNI stream → nginx:8444 ssl proxy_protocol → 127.0.0.1:8005 → api:8000` (см. CLAUDE.md «Сетевая топология»). Новый фронт-сервис должен встроиться в эту схему без поломки текущей цепочки для API.
- Cloudflare больше не используется для этого фронта после переноса (Claude's discretion: если что-то в Cloudflare-конфиге завязано на DNS/CDN не относящееся к хостингу самого SPA — не трогать, только раздачу статики).

### Lovable usage
- Полностью переходим на прямые правки в коде (не через Lovable). Lovable больше не источник правды.
- `lovable-handoff/openapi.json` (если используется как контракт) можно оставить как справочный документ, но реальный источник кода — файлы в монорепо.

### Claude's Discretion
- Точное имя подпапки для фронта (`frontend/`, `web/`, `apps/web/` и т.п.) — выбрать то, что не конфликтует с существующей структурой репо (`app/`, `migrations/`, `tests/`, `scripts/`, `docs/`).
- Конкретный механизм сборки/деплоя (отдельный Dockerfile для фронта + отдельный `docker compose` service, либо multi-stage build, копирующий статику в volume, который раздаёт nginx) — выбрать наиболее простой и согласующийся с текущим паттерном других сервисов на этом сервере (`funnel-dashboard-api`, `vitrina`).
- Нужно ли переносить CI/CD workflow файлы (`.github/workflows` фронт-репо, если есть) в монорепо, или деплой останется ручным (`git pull && docker compose up -d --build`, как у бэка сейчас) — выбрать вариант, максимально похожий на текущий процесс деплоя бэка, описанный в CLAUDE.md.
- Обновление документации (`CLAUDE.md` секции «Стек», «Git & Deploy», «Сетевая топология») после переноса — отразить новую монорепо-структуру и убрать упоминания отдельного фронт-репо/Cloudflare как деплой-таргета.

</decisions>

<specifics>
## Specific Ideas

Нет специфических примеров/референсов сверх сказанного выше — открыто к стандартным подходам (git subtree merge для сохранения истории, nginx serving static build + reverse proxy к API).

</specifics>

<canonical_refs>
## Canonical References

- `/root/apps/aimly/tg-outreach/CLAUDE.md` — секции «Стек», «Git & Deploy», «Сетевая топология» (текущая схема nginx SNI dispatcher, порты, TLS через certbot webroot).
- `/root/apps/aimly/aimly-tg-outreach` (локальный клон фронт-репо) — текущий код фронта, `wrangler.jsonc`, структура TanStack Start проекта.
- `lovable-handoff/openapi.json` в backend-репо — контракт API, из которого Lovable генерировал фронт.

</canonical_refs>
