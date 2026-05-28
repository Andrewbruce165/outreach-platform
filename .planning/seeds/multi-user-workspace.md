---
title: Multi-user workspace — инвайт, роли, управление участниками
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: TEAM-01..02
related_phases: [v2]
---

## Идея

Workspace становится мульти-юзерным: owner может инвайтить других пользователей с ролями. Расширение существующего placeholder из PROJECT.md "Out of Scope for v1".

## Скоуп

- **Модель `user_workspaces`:**
  - Revert UNIQUE из migration 023 (одновременно с WSPC-02)
  - Добавить колонку `role` ENUM('owner', 'admin', 'operator')
  - Добавить колонки `invited_by`, `invited_at`, `joined_at` для аудита
- **Инвайт:**
  - По email через Supabase magic-link (создаёт pending invite → клиент по ссылке принимает)
  - Pending invite в таблице `workspace_invites` (TTL 7 дней)
- **Роли:**
  - **owner** — может всё; не может удалить себя без передачи owner-role; биллинг
  - **admin** — может всё кроме удаления/billing workspace; может приглашать
  - **operator** — может работать с inbox/кампаниями/agents; не может менять senders/billing/инвайтить
- **UI:** страница Settings → Team — список членов, инвайт по email, change role, remove member
- **Audit log:** кто пригласил / кто удалил / кто менял роль

## Зачем

1. Агентство = несколько менеджеров в одном workspace (один настраивает, второй работает с inbox)
2. Разделение ответственности по ролям
3. Off-boarding бывших сотрудников — нужна возможность удалить без потери workspace-настроек

## Зависимости

- **Пересекается с WSPC-02** (multi-workspace per user) — оба меняют `user_workspaces`, делать в одной фазе
- AuthCtx должен учитывать роль (сейчас плоский — workspace_id)
- Pre-req: WSPC-01 (Settings UI)

## Альтернатива

- **Owner-only** — каждый workspace = один юзер (текущее состояние, плохо для агентств)
- **Shared login** (несколько юзеров с одним паролем) — security nightmare, не вариант

## Открытые вопросы

- Биллинг при multi-user: per-workspace или per-user?
- Три роли (owner/admin/operator) достаточно или нужны кастомные permissions per-resource (RBAC matrix)?
- При удалении owner — кому передаётся workspace? (Самому раннему admin? Forced транзит?)
