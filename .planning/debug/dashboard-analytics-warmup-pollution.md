---
slug: dashboard-analytics-warmup-pollution
status: resolved
trigger: "Дашборд показывает неверные данные по кампаниям и другой аналитике (sent/replied/leads/finishes/funnel/LLM); цифры завышены, занижены/нули и не обновляются. Запрос: сверить что бэкенд передаёт."
created: "2026-06-25"
updated: "2026-06-25"
tdd_mode: false
goal: find_and_fix
related_sessions: service-conflict-investigation
---

## Current Focus

- **hypothesis:** Аналитика считает warmup-трафик между нашими же 13 sender-аккаунтами как реальный аутрич. Метрики `sent`/`replied` (и funnel sent/replied) в `app/routers/analytics.py` суммируют ВСЕ outbound/inbound по workspace без фильтра на «контакт = наш собственный sender». RESOLVED — подтверждено данными.
- **next_action:** выбрать форму фикса (read-time фильтр в analytics + чистка/карантин исторических диалогов; опц. харднинг inbound warmup-фильтра в listener). Ждёт подтверждения подхода у пользователя.

## Root Cause

В workspace `bb96789d-ca84-4880-9568-90867aae6acd` всего **71 диалог**, но `sent`=5382 outbound / `replied`=5332 inbound.
**26 диалогов БЕЗ кампании** несут **5327 из 5382** исходящих. У всех топовых `contact_telegram_id` ∈ `senders.telegram_id` (наши собственные аккаунты), по 660–790 сообщений на пару, статусы `active`.

Происхождение: 2026-06-23/24 старый `telegram-api` warmup-воркер слал сообщения между общими 13 аккаунтами (см. `service-conflict-investigation.md`). Листенер aimly, подключённый к тем же аккаунтам, ловил трафик:
- у отправляющего аккаунта — `handle_outgoing_message` → `outbound/sent_by=human`
- у принимающего — inbound-handler → `inbound/sent_by=contact`

Отсюда 5350 human-out ≈ 5332 contact-in. Каждое warmup-сообщение задваивается: +1 к `sent` и +1 к `replied`.

Почему warmup-фильтры листенера не отсекли:
- inbound-фильтр (`listener.py:683`) — по `phone`, пропускается при `phone == "unknown"` (Telegram скрывает телефон по приватности) → leak.
- outgoing-фильтр (`listener.py:1113`) — по `telegram_id`, но в момент инцидента warmup-пул/кэш aimly мог не содержать этих партнёров (трафик генерил ЧУЖОЙ telegram-api, не наш WarmupWorker; `warmup_sessions` сейчас пуст).

## Evidence

- timestamp: 2026-06-25 — workspace SQL (повтор логики analytics.py): sent=5382, replied_msgs=5332, replied_conv=29, leads=1, finished=5. Диалогов всего 71.
- timestamp: 2026-06-25 — 26 no-campaign диалогов = 5327 outbound; 45 campaign-диалогов = всего 55 outbound.
- timestamp: 2026-06-25 — топ no-campaign диалоги: contact_is_our_sender=true для всех (tg_id 8526195634, 8218483045, 8525079460, …), 660–791 msg каждый, даты 06-23/24.
- timestamp: 2026-06-25 — загрязнение строго 06-23 (3100 out/3098 in) и 06-24 (2181/2181); на 06-25 — ноль. warmup_sessions пуст → не продолжается.
- timestamp: 2026-06-25 — warmup пишет ТОЛЬКО в warmup_messages (warmup.py:380), реальная отправка — warmup.py:607 client.send_message; в conversations/messages warmup сам по себе не пишет — туда попадает через listener.

## Eliminated

- hypothesis: «Баг в формуле прогресса кампаний» — отклонено. Тот баг (finishes/sent) уже починен 2026-06-25 (`campaign-progress-shows-0-percent.md`); текущая проблема в источнике данных, а не в формуле.
- hypothesis: «Импортированная история переписки реальных контактов» — отклонено. Контакты — наши же senders, трафик синтетический (warmup).

## Proposed Fix (ожидает подтверждения)

1. **Дашборд (основное):** в `analytics.py` исключать диалоги, где `contact_telegram_id ∈ senders.telegram_id` этого workspace (internal/warmup трафик) во всех метриках (cards + funnel). Robust независимо от происхождения данных.
2. **Исторические данные:** карантин/чистка 26 no-campaign диалогов (5327 msg). Вариант мягкий — пометить статусом, исключаемым аналитикой; жёсткий — удалить. Бэкап есть (backup.sh).
3. **Defense-in-depth (опц.):** inbound warmup-фильтр листенера (`listener.py:683`) перевести с phone-based на telegram_id-based (как outgoing на 1113), чтобы будущий warmup-конфликт не пере-загрязнил.

## Fix Applied (2026-06-25, код, без миграции)

1. `app/routers/analytics.py`: добавлена константа `_EXCLUDE_INTERNAL_CLAUSE`
   (null-safe NOT EXISTS: contact_telegram_id ∈ senders того же workspace).
   Подключена через `scope_clause` в `_compute_cards` (5 метрик) и в `/funnel`
   (5 стадий). `registered_contacts` (знаменатель) не тронут — warmup его не касается.
2. `app/services/listener.py`: inbound warmup-фильтр (handle_incoming_message)
   переведён на telegram_id (`sender.id in _get_warmup_telegram_ids()`) перед
   phone-веткой — симметрично outgoing-фильтру; robust при скрытом телефоне.

## Verification

- SQL по live-БД с фильтром: sent 5382 → **101**, replied msgs 5332 → **53**,
  replied conv 29 → **13** (реальные внешние диалоги остались, warmup убран).
- Тесты: `test_phase5_analytics.py` + `test_phase5_analytics_correctness.py` —
  22+1 passed, добавлен `test_internal_warmup_conversation_excluded` (cards+funnel).
- **PENDING:** деплой `docker compose up -d --build api && ... listener`
  (отложен — параллельный агент в репо, ребилд подхватит чужой working tree).
  Исторические 26 диалогов в БД НЕ трогаем — фильтр отсекает их на чтении.
