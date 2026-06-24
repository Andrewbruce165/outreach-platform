---
title: Account restriction / PEER_FLOOD observability gap
date: 2026-06-24
context: explore session — "история PEER_FLOOD на аккаунтах"
related_requirements: [HLTH-01, HLTH-02, HLTH-03]
related_phases: [Phase 10]
---

# Аудита блокировок аккаунтов нет — снимок 2026-06-24

Попытка собрать историю PEER_FLOOD по аккаунтам показала, что **долгой истории нет — её негде хранить**. Это оформлено в требования HLTH-01..03 (вшиты в Phase 10).

## Состояние на момент проверки

Под ограничением — 2 аккаунта:

| Аккаунт | restriction_status | restricted_until | Чем подтверждено |
|---|---|---|---|
| `sender-8218483045` (Anastasiya, @tanova_business) | `spam_limited` | 2026-06-25 14:00 UTC | Явный спам-лимит **2026-06-19 11:52**; ровная дата = @SpamBot назвал точную дату разбана |
| `sender-8071536685` | `spam_limited` | плавающая (~07:47, двигалась) | restriction-reconcile каждые ~15 мин: @SpamBot отвечает "still limited" → срок продлевается; трафика нет (`last_used` пуст) |

За сутки логов (23.06 13:45 → 24.06 07:49) restriction-loop отработал 37 раз, **все** тики `checked=1 extended=1`, ни одного `cleared`/`banned`.

## Где что хранится (и почему истории нет)

- **`message_queue.error_message`** — только *последняя* ошибка по item'у; упавшие на PEER_FLOOD сообщения уходят обратно в `pending` → текст затирается.
- **`messages_log`** — постоянный, но за всё время лежит ровно **1** спам-строка (*«Спам-ограничение аккаунта. Требуется пауза и ручная проверка.»*, 19.06) + пара privacy-ошибок.
- **`telemetry_events`** — смену `restriction_status` **не пишет** вообще (только UI-события: campaign_paused/launched и т.п.).
- **`senders.restriction_status` / `restricted_until`** — только *текущее* состояние, без истории.
- **Логи контейнера** (`outreach-platform-listener`) — живут ~18 часов.

→ Восстановить «как часто за месяц ловили PEER_FLOOD» по данным сейчас невозможно.

## Важная оговорка: PRIVACY_PREMIUM_REQUIRED ≠ флуд

`sender-8017533134` 23.06 трижды ловил `RPCError 403: PRIVACY_PREMIUM_REQUIRED`. Это **не флуд и не бан** — настройка приватности получателя: писать ему в личку может только аккаунт с Telegram Premium. Аккаунт здоров, ограничение на него не вешалось. При построении аналитики флуд-событий этот класс ошибок нельзя смешивать с restriction.
