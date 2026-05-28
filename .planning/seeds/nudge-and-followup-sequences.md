---
title: Re-engagement nudge (NUDGE-01) + многошаговые follow-up последовательности (ADVN-01)
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: NUDGE-01 (single-shot) + ADVN-01 (multi-step)
related_phases: [v2]
---

## Идея

Два связанных, но разных уровня:

- **NUDGE-01** — single-shot ping после read+silent (быстрая ценность, маленькая фича)
- **ADVN-01** — полноценный sequence-builder с ветвлениями по событиям (большая фича, отдельная sub-phase)

## Скоуп NUDGE-01 (single-shot, делать первым)

- На уровне кампании: поля `nudge_enabled`, `nudge_after_hours`, `nudge_template`
- Триггер: listener видит `read-receipt` (`messages.read=true` или Telethon `UpdateReadHistoryInbox`)
  → schedule nudge через `nudge_after_hours` часов в очередь
- Если за это время пришёл reply — отменить nudge (DELETE из queue WHERE conversation_id=X AND is_nudge=true)
- Если время прошло без reply — отправить `nudge_template` (рендеринг с теми же variables что и первое сообщение)
- Per-conversation cap: **1 nudge на диалог** (после nudge — больше не дёргаем)
- Bot-фильтр и manager-mode проверки те же что у обычных сообщений

## Скоуп ADVN-01 (multi-step, делать после NUDGE-01)

- Sequence builder: список шагов, каждый = { template, trigger_condition, delay }
- Триггеры: `read` / `no-read` / `replied` / `blocked` / `no-event-N-days`
- Каждый шаг → условие выбора следующего шага (decision tree)
- UI как у Mailchimp/Lemlist customer journey — drag-drop nodes
- Новая модель: `sequences`, `sequence_steps`, sequence-runner worker
- Гораздо больше работы — отдельная sub-phase в v2

## Зачем

- **NUDGE-01:** классический паттерн повышения reply rate (lemlist/instantly показывают +30–50% к replies)
- **ADVN-01:** полноценный outbound — большая фича, конкурентный paritet с lemlist/instantly

## Зависимости

- NUDGE-01 строится на готовых: `messages.read` (Phase 05), `template.py` (Phase 04), `queue.py`
- ADVN-01 — новая модель `sequences`/`sequence_steps`, sequence-runner worker, UI builder

## Альтернатива

Сделать только NUDGE-01 в v2, ADVN-01 отложить в v3. Single-shot покрывает 80% UX-выгоды от multi-step.

## Открытые вопросы

- Хранить ли nudge как отдельный message или с пометкой `messages.is_nudge=true`?
- Что делать если read-receipt не пришёл (выключен у контакта) — fallback по времени без события (например, "если за 48ч нет события — пинг")?
- Где жёстко лочить ADVN-01 чтобы не превратить в массовый spam (cap = max 3 шага в sequence?)
