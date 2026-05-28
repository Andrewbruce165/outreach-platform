---
title: Несколько workspace на одного пользователя + UI switcher
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: WSPC-02
related_phases: [v2]
---

## Идея

Пользователь может иметь / создавать / переключаться между несколькими workspace (как Slack, Notion). Сейчас `migration 023_user_workspaces_unique.sql` жёстко наложила `UNIQUE(user_workspaces.supabase_user_id)` — 1 user = 1 workspace.

## Скоуп

- **Migration:** drop UNIQUE constraint из migration 023, заменить на `UNIQUE(supabase_user_id, workspace_id)` (для many-to-many)
- **UI workspace switcher:** top-bar dropdown с списком моих workspace + "+ New Workspace"
- **Создание нового workspace:** кнопка "+ New Workspace" → форма с именем → auto-create
- **Active workspace per session:** в JWT claims (`active_workspace_id`) или в localStorage с server-validation на каждом запросе
- **AuthCtx:** брать active workspace из user-session, не из user_workspaces (т.к. их несколько)

## Зачем

1. Агентство ведёт несколько клиентов — каждый клиент = свой workspace, но один логин для менеджера
2. Пользователь хочет отделить тестовый workspace от продакшна
3. Стандартная SaaS-модель — клиенты ждут такой возможности

## Зависимости

- Пересекается с TEAM-01..02 (user_workspaces становится many-to-many с ролями)
- Конфликтует с миграцией 023 (которая решала race condition на auto-create) — нужно решить race condition другим способом
- Pre-req: WSPC-01 (редактирование workspace metadata) — иначе клиент не сможет назвать новые workspace

## Альтернатива

Оставить 1:1 — клиенту с двумя проектами заводить два аккаунта (плохой UX, разные email).

## Открытые вопросы

- Как решать race condition auto-create без жёсткого UNIQUE? Возможно через `INSERT ... ON CONFLICT (supabase_user_id, name) DO NOTHING` + дефолтное имя "Workspace 1"
- Migration plan для существующих юзеров: добавить `active_workspace_id` в user-profile, ставить первый существующий workspace по умолчанию
- Если у пользователя 0 workspace (после удаления всех) — что показывать? Onboarding?
