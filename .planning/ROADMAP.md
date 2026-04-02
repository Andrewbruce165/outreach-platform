# Roadmap: Outreach Platform

## Overview

Превращаем внутренний инструмент AGS Foods в мультитенантную SaaS-платформу для Telegram-аутрича.
Бизнес-логика (очередь, AI-ответчик, Telethon-клиент) уже работает — строим поверх неё workspace-изоляцию, auth, per-agent настройки и клиентский UI.
Цель v1: первый внешний клиент может зарегистрироваться, подключить аккаунт, загрузить контакты и запустить рассылку самостоятельно.

## Phases

- [ ] **Phase 1: Workspace Foundation** — мультитенантная схема БД + auth middleware + новый API-скелет
- [ ] **Phase 2: Agent Management** — онбординг TG-аккаунтов в workspace + страница настроек агента
- [ ] **Phase 3: Contacts & Outreach** — загрузка CSV, персонализация переменных, push через API
- [ ] **Phase 4: AI & Inbox** — настройка AI-контекста, auto_pause_triggers, inbox с ручным управлением

## Phase Details

### Phase 1: Workspace Foundation
**Goal**: Заложить мультитенантный фундамент — все данные изолированы по workspace_id, вход через magic link, новый API-слой готов к расширению.
**Depends on**: Nothing (first phase)
**Requirements**: TENT-01, TENT-02, TENT-03, TENT-04, AUTH-01, AUTH-02, AUTH-03, AUTH-04
**Success Criteria** (what must be TRUE):
  1. Пользователь вводит email → получает magic link → входит в систему
  2. При первом входе workspace создаётся автоматически
  3. FastAPI принимает Supabase JWT и отклоняет запросы без валидного токена (403)
  4. Workspace имеет уникальный API-ключ (виден в настройках)
  5. Все новые таблицы имеют `workspace_id`; запросы без него невозможны на уровне кода
**Plans**: 3 plans

Plans:
- [ ] 01-01: DB migration — add workspaces table, workspace_id FK to all core tables
- [ ] 01-02: Auth middleware — Supabase JWT verification, workspace context injection
- [ ] 01-03: API skeleton rewrite — new router structure, workspace API key endpoint

---

### Phase 2: Agent Management
**Goal**: Клиент добавляет свои Telegram-аккаунты (агентов) в workspace и тонко настраивает каждый — лимиты, расписание, прокси, AI-контекст.
**Depends on**: Phase 1
**Requirements**: ONBD-01, ONBD-02, ONBD-03, ONBD-04, ONBD-05, AGNT-01, AGNT-02, AGNT-03, AGNT-04, AGNT-05, AGNT-06
**Success Criteria** (what must be TRUE):
  1. Пользователь проходит онбординг TG-аккаунта (телефон → SMS → готово) — аккаунт привязан к его workspace
  2. Поддерживается 2FA и QR-вход
  3. На странице агента можно задать: rate limits (с предупреждением при выходе за рекомендованные), расписание, прокси, AI-контекст
  4. Список агентов workspace показывает статус каждого (активен / прогрев / пауза / ошибка)
  5. Настройки агента применяются к очереди сразу после сохранения
**Plans**: 4 plans

Plans:
- [ ] 02-01: Wire onboarding flow to workspace — scope sessions and senders to workspace_id
- [ ] 02-02: Per-agent settings model — DB schema for agent config (limits, schedule, proxy, ai_context_id)
- [ ] 02-03: Agent settings API endpoints — CRUD for agent configuration
- [ ] 02-04: Agent list & status endpoint — return agents with live status per workspace

---

### Phase 3: Contacts & Outreach
**Goal**: Клиент загружает базу контактов через CSV или API, сообщения персонализируются переменными из контакта.
**Depends on**: Phase 2
**Requirements**: CONT-01, CONT-02, CONT-03, CONT-04
**Success Criteria** (what must be TRUE):
  1. Клиент загружает CSV (телефон обязателен, имя/компания/кастомные поля опционально) — контакты сохранены в его workspace
  2. Контакты можно добавить через POST /api/v1/contacts с workspace API-ключом
  3. При постановке в очередь `{{имя}}`, `{{компания}}`, `{{кастомное_поле}}` заменяются значениями из контакта
  4. Неизвестные переменные не вызывают ошибку — заменяются пустой строкой (или configurable fallback)
**Plans**: 3 plans

Plans:
- [ ] 03-01: Contacts model & CSV import endpoint — parse, validate, store with workspace_id
- [ ] 03-02: Push contacts API — POST /api/v1/contacts with workspace API key auth
- [ ] 03-03: Variable substitution in queue worker — replace {{var}} from contact fields before send

---

### Phase 4: AI & Inbox
**Goal**: Клиент настраивает AI-ответчик и управляет диалогами из inbox — видит историю, переключает агента на ручной режим, контролирует auto_pause.
**Depends on**: Phase 3
**Requirements**: AIRC-01, AIRC-02, AIRC-03, AIRC-04, AIRC-05, INBX-01, INBX-02, INBX-03, INBX-04
**Success Criteria** (what must be TRUE):
  1. Клиент создаёт AI-контекст (промпт, тон, правила, FAQ) — привязан к workspace, применяется ко всем агентам
  2. Задаёт auto_pause_triggers — при совпадении AI замолкает, диалог помечается
  3. Из inbox можно переключить диалог в режим "менеджер" (AI отключается для этого диалога)
  4. AI не отвечает системным ботам (SpamBot и аналоги)
  5. Inbox показывает все диалоги workspace с историей сообщений и статусом AI
**Plans**: 4 plans

Plans:
- [ ] 04-01: AI context CRUD API — create/update/delete contexts scoped to workspace
- [ ] 04-02: Auto-pause triggers & bot blocking — wire triggers to listener, block system bots
- [ ] 04-03: Manual manager mode — endpoint to disable AI per-conversation + status tracking
- [ ] 04-04: Inbox API — conversations list + message history + AI status per workspace

---

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Workspace Foundation | 0/3 | Not started | - |
| 2. Agent Management | 0/4 | Not started | - |
| 3. Contacts & Outreach | 0/3 | Not started | - |
| 4. AI & Inbox | 0/4 | Not started | - |
