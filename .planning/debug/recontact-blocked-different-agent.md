---
slug: recontact-blocked-different-agent
status: fix_applied
trigger: |
  DATA_START
  Запустил новую кампанию "Паша аналитика", получателям не пришло сообщение.
  Пользователь сам нашёл причину: enqueue-воркер не добавил элементы в
  message_queue, потому что все контакты кампании уже имеют существующие
  диалоги (строки в conversations) в этой workspace, а у кампании
  allow_recontact=false (по умолчанию). Дедуп исключает такие контакты.

  Заявленное ожидаемое поведение: должны иметь возможность писать повторно,
  ЕСЛИ это другой агент (sender) И другая кампания. Текущий дедуп режет
  слишком широко — по (workspace, contact), игнорируя sender/campaign.
  DATA_END
created: 2026-06-23
updated: 2026-06-23
tdd_mode: false
goal: find_and_fix
---

## Symptoms

- **expected:** Повторный контакт разрешён, если сообщение идёт от другого
  агента (sender) в рамках другой кампании. Контакт с существующим диалогом
  не должен безусловно исключаться из enqueue.
- **actual:** Контакты с существующей записью в `conversations` (в той же
  workspace) исключаются из enqueue, когда `allow_recontact=false`. Кампания
  "Паша аналитика" → 0 pending в очереди → сообщения не ушли.
- **errors:** Нет ошибки/исключения. Тихий no-op: pending=0.
- **timeline:** Проявилось при запуске новой кампании "Паша аналитика".
- **reproduction:**
  1. Кампания id = b509e93a-211f-4afc-99cc-8ea003ce5e6a, status=running, start_date=NULL.
  2. Контакты в папке (2): +79308902205 и @potasuev — оба уже есть в `conversations`.
  3. allow_recontact=false (default).
  4. `SELECT COUNT(*) FROM message_queue WHERE campaign_id=... AND status IN ('pending','processing')` → 0.

## Current Focus

- **status:** root_cause_confirmed — ожидается решение пользователя по семантике политики.
- **root_cause:** CONFIRMED. Фильтр в `campaign_enqueue._tick_one_campaign`
  (строки 154-158) исключает контакт, если в `conversations` этой workspace
  есть ЛЮБАЯ строка с этим `contact_phone`. При `allow_recontact=false`
  (default) `conv_dedup_filter=""` → блокирует безусловно. `campaign_id` и
  `sender_id` в подзапросе НЕ учитываются, хотя в таблице `conversations` обе
  колонки есть (campaign_id nullable SET NULL, sender_id not null).
- **next_action:** выбрать семантику recontact (см. Decision ниже), внести
  правку в SELECT-фильтр (+ возможно per-contact пост-проверку sender),
  добавить миграцию-настройку при необходимости, написать тест, прогнать
  через test-overlay.

## Decision (policy semantics — pending user)

Block ⟺ ?  (allow recontact в остальных случаях)
- Вариант A: block только same-campaign (scope `AND campaign_id = :cid`).
  Другая кампания → всегда можно. Минус: тот же sender может задвоить контакт
  из двух кампаний.
- Вариант B (литерально «другой агент И кампания»): block ⟺ same-campaign OR
  same-sender. Allow только если различаются обе. Нужна per-contact проверка
  назначенного sender внутри savepoint.
- Вариант C: оставить текущее, сделать поведение настройкой кампании.

## Evidence

- timestamp: 2026-06-23 — campaign b509e93a status=running, start_date=NULL; pending/processing в message_queue = 0.
- timestamp: 2026-06-23 — оба контакта (+79308902205, @potasuev) присутствуют в conversations этой workspace.
- timestamp: 2026-06-23 — allow_recontact=false (default) на кампании.

## Eliminated

(none yet)

## Resolution

- **decision:** Вариант B — block ⟺ та же кампания ИЛИ sender входит в пул
  senders этой кампании (тот же агент). Иначе recontact разрешён.
- **fix:** `app/services/campaign_enqueue.py::_tick_one_campaign` — подзапрос
  дедупа по `conversations` сужен: добавлено условие
  `AND (campaign_id = :cid OR sender_id IN (SELECT sender_id FROM
  campaign_senders WHERE campaign_id = :cid))`. Реализовано целиком в SELECT
  (детерминированно, без per-contact запросов и отката savepoint). Фильтр
  freshness/protected (allow_recontact) накладывается сверху без изменений.
- **verification:**
  * Живые данные кампании b509e93a: оба контакта имеют диалоги в чужой кампании
    (53afb448) с sender 6b0e6958, которого НЕТ в пуле Паши (86236ba1/09378d41/
    2fe00ee0) → по новой логике пройдут в очередь. ✓
  * Тесты через test-overlay: tests/test_recontact.py (9, +3 новых),
    tests/test_campaign_enqueue_worker.py, queue/rotation/bot_filter — 51 passed,
    1 skipped. Обновлены 6 старых тестов (фиксировали старую широкую политику)
    на pool-sender; добавлены identity-scope тесты.
- **files_changed:**
  * app/services/campaign_enqueue.py
  * tests/test_recontact.py
  * tests/test_campaign_enqueue_worker.py
- **note:** 4 падения в tests/test_send_campaign.py — пред-существующие
  (user_workspaces.user_id / ImportError), к фиксу не относятся.
- **pending:** деплой на прод (`docker compose up -d --build api`) — НЕ выполнен,
  ждёт подтверждения пользователя.
