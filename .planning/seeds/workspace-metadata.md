---
title: Редактирование данных workspace (имя, аватар, soft-delete, экспорт)
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: WSPC-01
related_phases: [v2]
---

## Идея

Дать пользователю редактировать метаданные своего workspace: имя, описание, аватарка/лого, soft-delete, экспорт всех данных.

Сейчас Workspace создаётся неявно при первом логине (auto-create по AuthCtx из миграции 023) и не редактируется.

## Скоуп

- Поля: `name`, `description`, `logo_url` (optional)
- Soft-delete: `workspaces.deleted_at` + cascade-cleanup для sender sessions (logout Telethon, удалить session files)
- Экспорт всех данных workspace (GDPR-friendly): contacts CSV, conversations JSON, messages JSON, llm_calls JSON
- UI: страница Settings → Workspace (имя/лого/описание + Danger Zone с экспортом и удалением)
- Soft-delete с задержкой (30 дней) перед hard-delete для recovery

## Зачем

1. Сейчас клиент не может назвать свой workspace, заменить логотип — выглядит "не своим"
2. Off-boarding клиентов без потери PII через hard-delete — нужен soft-delete + экспорт
3. GDPR/RU 152-ФЗ: клиент должен иметь возможность забрать свои данные и удалить аккаунт

## Зависимости

- Модель Workspace в `app/models/workspace.py`
- Пересекается с TEAM-01..02 — только owner может редактировать/удалять workspace
- Cascade cleanup трогает Telethon session files — нужна аккуратная процедура logout перед delete

## Альтернатива

Только переименование без soft-delete и экспорта (минимальный MVP). Экспорт оставить на support-request.

## Открытые вопросы

- Logo storage — S3-совместимое хранилище или просто URL?
- Hard-delete через 30 дней — автоматический cron или ручной?
