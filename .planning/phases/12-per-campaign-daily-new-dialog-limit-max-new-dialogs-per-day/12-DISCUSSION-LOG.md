# Phase 12: Per-campaign daily new-dialog limit - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-25
**Phase:** 12-per-campaign-daily-new-dialog-limit-max-new-dialogs-per-day
**Areas discussed:** Counting scope, New-dialog definition, Enforcement mechanism, Backfill/default

---

## Counting scope (per-sender vs campaign-wide)

| Option | Description | Selected |
|--------|-------------|----------|
| Per-sender в кампании | Каждый аккаунт открывает до 50 новых диалогов/сутки; согласуется с anti-spam per-account и принципом «rate limits per-sender» | ✓ |
| Campaign-wide | Вся кампания ≤50 новых/сутки независимо от числа аккаунтов; проще для пользователя, но конфликтует с per-sender логикой | |
| Обсудить | Разобрать оба подробнее | |

**User's choice:** Per-sender в кампании (как текст роадмапа)
**Notes:** Counting key = `(sender_id, campaign_id)`, trailing-24h. Campaign-wide потолок = limit × N — осознанное следствие.

---

## New-dialog definition + dedup scope + in-flight

| Option | Description | Selected |
|--------|-------------|----------|
| В рамках кампании, считаем sent | NOT EXISTS prior `sent` к recipient_phone в ЭТОЙ кампании; pending не считается; trailing-24h | ✓ |
| По всему workspace | Телефону не слали ни в одной кампании — строже, дороже, размывает «лимит кампании» | |
| Обсудить определение/окно | rolling-24h vs календарный день | |

**User's choice:** В рамках этой кампании, считаем sent (рекоменд.)
**Notes:** allow_recontact к телефону с прошлым sent = follow-up, не новый диалог. Window = trailing-24h rolling.

---

## Enforcement mechanism (как не блокировать follow-ups)

| Option | Description | Selected |
|--------|-------------|----------|
| Per-item фильтр в выборке | Исключать новые-диалоговые элементы из LIMIT-8 кандидатов при достигнутом лимите; follow-ups реально проходят | ✓ |
| В _check_rate_limits (return False per-tick) | Простой путь по тексту роадмапа, но per-tick блок тормозит и follow-ups | |
| Обсудить trade-off | — | |

**User's choice:** Per-item фильтр в выборке (follow-ups реально проходят)
**Notes:** Новый лимит выносится из `_check_rate_limits` в item-selection. Per-sender 4/20/150 + 15/hour остаются в `_check_rate_limits` нетронутыми (CLAUDE.md guard).

---

## Backfill / default для существующих кампаний

| Option | Description | Selected |
|--------|-------------|----------|
| 50 для всех, включая running | Чистая «зелёный коридор по умолчанию» семантика; горячие кампании могут притормозиться — это цель фичи | ✓ |
| Backfill running повышенным (100) | Мягкий rollout, не ломать работающие задним числом | |
| Обсудить | running vs draft/done раздельно | |

**User's choice:** 50 для всех, включая running (как в acceptance)
**Notes:** Backfill повышенным значением НЕ делаем.

## Claude's Discretion

- Точная формулировка warning-message и green-corridor copy (рус.).
- Конкретный SQL-shape per-item фильтра (subquery / CTE / window-count).
- Размещение порог-констант (dict vs inline).

## Deferred Ideas

- Аналитика по новым диалогам — отдельная фича.
- Campaign-wide агрегатный потолок — отдельная фаза при необходимости.
- Календарный-день reset — отклонён в пользу rolling-24h.
