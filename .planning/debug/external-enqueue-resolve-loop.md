---
status: resolved
trigger: "Внешний enqueue-цикл — код-баг, который будет жечь резолвы на любой будущей базе с нерезолвящимися registered-контактами. При финализации в failed удаляется CCA (queue.py:1667), а enqueue-воркер тут же заново создаёт pending-строку для контакта со статусом registered. Любой registered-но-нерезолвящийся номер крутится вечно: enqueue → fail → enqueue. Это источник хронических «62–147 failed/день». Rotation-cap ограничивает каждый внутренний виток, но внешний цикл не останавливает."
created: 2026-08-13
updated: 2026-08-13
---

## Symptoms

DATA_START
**Expected behavior:** Контакт со статусом `registered`, который перманентно не резолвится ни одним sender'ом, должен после исчерпания попыток попадать в терминальное состояние (или backoff), а не пере-enqueue'иваться бесконечно.

**Actual behavior:** Внешний бесконечный цикл: при финализации попытки в `failed` удаляется CCA-строка (queue.py:1667), а enqueue-воркер на следующем тике заново создаёт `pending`-строку для того же контакта (статус `registered`). Цикл enqueue → resolve-fail → failed → delete CCA → re-enqueue крутится вечно, сжигая резолвы.

**Error messages:** Нет явных ошибок — проявляется как хронические 62–147 `failed`-строк message_queue в день на кампании 24658b65 (и потенциально любой будущей базе с нерезолвящимися registered-контактами).

**Timeline:** Хроническое, наблюдалось задолго до карусельного инцидента 2026-08-13. Фикс 44222d8 (rebalance pin + RESOLVE_ROTATION_CAP=3) ограничивает внутренний виток (число sender-ротаций внутри одной попытки), но НЕ останавливает внешний цикл re-enqueue.

**Reproduction:** Взять контакт с `contacts_cache`-статусом `registered`, у которого резолв реально невозможен (стухший access_hash / приватность / удалённый аккаунт). Он будет циклиться: enqueue-воркер создаёт pending → resolve fail по всем ротациям → финализация в failed → удаление CCA → снова pending на следующем тике.

**Related context:** Предыдущая сессия (resolve-carousel, коммит 44222d8) закрыла внутренний виток. Пользователь уже локализовал внешний цикл: удаление CCA при failed-финализации в queue.py:1667 + условие пере-enqueue в enqueue-воркере, которое смотрит только на статус registered и отсутствие CCA/pending-строки. Нужен fix: терминальное или backoff-состояние контакта после перманентного resolve-fail. 17 телефонов из карусельного инцидента ждут re-check чекером перед re-enqueue.
DATA_END

## Current Focus

hypothesis: CONFIRMED — WR-12a's unconditional cold-fail CCA release (queue.py:1659-1669) has no memory of prior cycles; enqueue-воркер (campaign_enqueue.py:352-355) реселектит контакт как только CCA исчез. Цикл не ограничен ничем.
test: bounded release (COLD_FAIL_RELEASE_CAP) в _fail_item + дедуп recovery-пути (requeue-failed) + регресс-тесты
expecting: третий подряд cold terminal fail для (campaign, phone) больше не удаляет CCA → контакт терминален для кампании; recovery через requeue-failed ре-пендит ровно одну строку на получателя
next_action: НИЧЕГО в коде — фикс завершён, оба пункта спец-ревью закрыты, 93 теста зелёные. Осталось: деплой пользователем (не сделан) + коммит (не сделан).

## Evidence

- timestamp: 2026-08-13
  checked: app/services/queue.py:1639-1671 (`_fail_item`) и app/services/campaign_enqueue.py:342-376 (`_tick_one_campaign` SELECT)
  found: На terminal fail без предшествующего `sent` для (campaign_id, recipient_phone) безусловно выполняется `DELETE FROM campaign_contact_assignments`. Селектор enqueue-воркера исключает контакт ровно одним предикатом — `COALESCE(phone,'@'||username) NOT IN (SELECT contact_phone FROM campaign_contact_assignments WHERE campaign_id=:cid)`. Дедуп по conversations не срабатывает, т.к. при resolve-fail диалог не создаётся.
  implication: удаление CCA — единственный «замок», и он снимается самим фейлом. Цикл замкнут: enqueue → fail → DELETE CCA → enqueue. Ни счётчика, ни backoff, ни классификации ошибки нет.

- timestamp: 2026-08-13
  checked: прод (read-only) — `SELECT campaign_id, recipient_phone, count(*) FROM message_queue WHERE status='failed' AND campaign_id IS NOT NULL GROUP BY 1,2 HAVING count(*)>=3`
  found: 1700 failed-строк на 181 уникальный (campaign, contact) — амплификация ×9.4. Топ: `+79104097409` кампания bb654c73 — **1081 failed-строка** за 07-27…08-10 (1072 из них «не зарегистрирован в Telegram»), ≈76 фейлов/день на ОДИН контакт. `@StanIsLove888` кампания 6d4ba212 — 166 строк с `RPCError 403: ALLOW_PAYMENT_REQUIRED_1775`. На running-кампании 24658b65: `+79163503202` — 5 строк/3 sender'а за 2 дня.
  implication: Наблюдаемые «62–147 failed/день» объясняются одним-двумя зациклившимися контактами. Цикл НЕ специфичен для resolve-fail — 166-строчный случай это `ALLOW_PAYMENT_REQUIRED` на SendMedia, т.е. петля срабатывает на ЛЮБОМ terminal fail холодного контакта. Значит фикс должен быть классо-агностичным (счётчик), а не только по классам ошибок.

- timestamp: 2026-08-13
  checked: `SELECT * FROM campaign_contact_assignments WHERE contact_phone IN (...)`
  found: У `+79104097409` ровно ОДНА CCA-строка (created_at 2026-08-10 10:37:51), при том что последний fail был 10:33:21 — т.е. выжила CCA только последнего витка, все предыдущие удалены.
  implication: Прямое подтверждение механизма: каждый виток создаёт CCA+queue-строку, fail удаляет CCA. 1081 fail = 1081 виток.

- timestamp: 2026-08-13
  checked: `.planning/reviews/260706-checker-campaigns-REREVIEW.md` (строки 141-154)
  found: Баг уже задокументирован как **WR-15** — «WR-12's CCA release creates an infinite re-enqueue/re-send loop … (regression of the WR-12 fix)», introduced by fbf75e6 (Batch E). Там же прописан фикс: считать прошлые terminal fail'ы для (campaign, phone) и перестать освобождать CCA после N циклов. В заключении ревью помечен как «secondary, should ship in the next batch» — и не был отгружен.
  implication: Не новый баг, а незакрытая известная регрессия. Прескриптивный фикс из ревью можно взять как есть; классификация по классам ошибок избыточна (см. ALLOW_PAYMENT_REQUIRED выше) — достаточно счётчика.

- timestamp: 2026-08-13
  checked: `grep -rn "DELETE FROM campaign_contact_assignments"` по app/ и tests/
  found: Единственный сайт удаления CCA — queue.py:1667. failover/rebalance/send_suspect только UPDATE'ят sender_id.
  implication: Ограничить нужно ровно одну точку; обходных путей размыкания «замка» нет.

- timestamp: 2026-08-13
  checked: `contacts.tg_status` для 5 зациклившихся телефонов кампании 24658b65
  found: Все 5 — `tg_status='unchecked'`, updated_at 2026-08-13 10:09 (ручная парковка из карусельной сессии).
  implication: Эти 17 телефонов уже выведены из-под селектора (он берёт только `registered`), поэтому цикл на running-кампании сейчас погашен вручную — но код-баг остался и повторится на любой следующей базе.

## Eliminated

- hypothesis: Цикл вызван `POST /campaigns/{id}/requeue-failed` (оператор/UI пере-пендит failed-строки)
  evidence: requeue-failed UPDATE'ит существующие строки (status → pending), НЕ создаёт новые. У `+79104097409` 1081 РАЗНАЯ message_queue-строка с растущим created_at — это INSERT'ы enqueue-воркера, не UPDATE'ы.
  timestamp: 2026-08-13

- hypothesis: Достаточно ограничить цикл классификацией ошибок (skip CCA release для PRIVACY_RESTRICTED / USER_IS_BLOCKED / not-in-telegram), как предлагает первая половина фикса WR-15
  evidence: Кейс `@StanIsLove888` (166 витков) — `RPCError 403 ALLOW_PAYMENT_REQUIRED_1775`, не входит ни в один из «перманентных recipient-level» классов. Плюс MEMORY-правило запрещает матчить локализованный `error_message` ('ограничен' ловил 'ограничений'), а `_fail_item` получает только строку ошибки, без error_code. Классификация и неполна, и хрупка.
  timestamp: 2026-08-13

## Resolution

root_cause: |
  WR-15 (регрессия WR-12a, коммит fbf75e6). В `QueueWorker._fail_item` (app/services/queue.py:1654-1669)
  терминальный фейл «холодного» контакта БЕЗУСЛОВНО удаляет его campaign_contact_assignments-строку.
  Эта CCA-строка — единственный предикат, которым `CampaignEnqueueWorker._tick_one_campaign`
  (app/services/campaign_enqueue.py:352-355) исключает контакт из выборки. Состояния «сколько раз этот
  контакт уже фейлился» не существует нигде, поэтому любой контакт с `tg_status='registered'`, который
  фейлится перманентно (нерезолвящийся номер, приватность, блок, ALLOW_PAYMENT_REQUIRED), крутится
  вечно: enqueue → 3 попытки → terminal fail → DELETE CCA → следующий тик снова enqueue.
  Прод: 1700 failed-строк на 181 уникальный контакт; один телефон дал 1081 виток за 14 дней.
fix: |
  (1) ОСНОВНОЙ — ограничить освобождение CCA счётчиком прошлых терминальных фейлов того же
  (campaign_id, recipient_phone). Новая константа `COLD_FAIL_RELEASE_CAP = 3` в queue.py;
  `_fail_item` одним запросом считает sent/failed по паре и освобождает CCA только пока
  `failed_cnt < COLD_FAIL_RELEASE_CAP`. На исчерпании — CCA остаётся, контакт становится
  терминальным для кампании, пишется WARNING. Классо-агностично (ловит все классы ошибок),
  без изменения схемы, escape hatch — `POST /campaigns/{id}/requeue-failed`.

  (2) СПЕЦ-РЕВЬЮ Important — дедуп recovery-пути. `requeue_failed`
  (app/routers/campaigns.py) ре-пендил КАЖДУЮ failed-строку, а кэп намеренно оставляет до 3
  строк на один телефон и pick диспетчера не дедуплицирует → операторское действие, которое
  рекомендует сам WARNING, слало бы N одинаковых опенеров одному человеку. Теперь UPDATE идёт
  по `id IN (SELECT DISTINCT ON (recipient_phone) id … ORDER BY recipient_phone, created_at DESC,
  id DESC)` — ровно одна (самая свежая) строка на получателя, старые витки остаются `failed`
  как аудит-след. Recency-колонка выбрана по модели, не на глаз: `created_at` (server_default
  now(), новая строка на каждый виток enqueue) — в отличие от `finished_at`, который на части
  fail-путей не гарантирован; `id DESC` детерминированно ломает ties при одинаковом timestamp.
  NULL-ловушка DISTINCT ON исключена: `MessageQueue.recipient_phone` NOT NULL (держит либо
  телефон, либо '@username'-ключ), поэтому схлопывания несвязанных строк через NULL-группу нет.

  (3) СПЕЦ-РЕВЬЮ Minor — тесты больше не хардкодят границу: `COLD_FAIL_RELEASE_CAP`
  импортируется, пробы выводятся как `CAP-2` (control, ниже кэпа) и `CAP-1` (ровно на кэпе);
  per-campaign scope-тест сеет `range(COLD_FAIL_RELEASE_CAP)`. Ретюнинг кэпа теперь двигает
  пробу, а не молча перестаёт щупать границу.
verification: |
  ⚠️ НЕ ЗАДЕПЛОЕНО и НЕ ЗАКОММИЧЕНО. Все изменения лежат в рабочем дереве unstaged.
  Деплой и коммит остаются за пользователем (в дереве есть правки параллельного контекста —
  senders.py / telegram.py / test_account_profile.py / test_cr04_* / CLAUDE.md, — которые
  не должны попасть в прод-образ вслепую).

  Self-verified (код + тесты):
  - Targeted subset через test-overlay: **93 passed** (было 92 до этого добора; +1 = новый
    дедуп-тест, регрессий нет). Команда:
    `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
     tests/test_queue_lifecycle_fixes.py tests/test_campaign_lifecycle_fixes.py
     tests/test_campaign_enqueue_worker.py tests/test_queue_campaign_id.py
     tests/test_queue_enqueue.py tests/test_failover.py tests/test_rebalance.py
     tests/test_rotation_campaign.py tests/test_send_campaign.py
     tests/test_campaign_autopause.py -q`
  - 3 регресс-теста на кэп: release ниже кэпа (control), retain на кэпе (loop-breaker),
    кэп скоупится per-campaign (фейлы того же телефона в другой кампании не считаются).
  - 1 новый регресс-тест на дедуп (`test_requeue_failed_dedups_per_recipient`): кампания с
    3 failed-строками одного телефона (cycle-1/2/3, явно разнесённые created_at) + 1 другой
    получатель → `requeued_count == 2`, в pending ровно `cycle-3` и `solo`, `cycle-1/2`
    остаются failed. **Falsification-проверка проведена**: с временно откаченным DISTINCT ON
    тест краснеет (`assert 4 == 2`) → тест реально ловит баг, а не проходит вхолостую.
    Файл после проверки восстановлен байт-в-байт.
  - 3 существующих WR-12a-теста и существующий `test_requeue_failed_repends_items`
    (2 разных телефона → по-прежнему 2) остались зелёными → поведение первого витка и
    обычного recovery не изменено.
  - Прод read-only EXPLAIN ANALYZE COUNT-запроса на худшей паре (1082 строки): 12.5 ms,
    Bitmap Index Scan по idx_message_queue_workspace_campaign_status_scheduled.
    Число round-trip'ов не выросло (был один SELECT has_sent — стал один SELECT counts).
  - Прод-импакт кэпа: пар (campaign, phone) уже на/выше кэпа — 25 (19 done, 1 paused,
    5 running). Все 5 на running-кампании 24658b65 — телефоны, уже вручную запаркованные
    в tg_status='unchecked', т.е. новых блокировок живых контактов деплой не создаёт.
  - Прод-импакт дедупа (read-only, `GROUP BY campaign_id` по failed-строкам) — насколько
    опасен был старый recovery-путь:
      bb654c73 — 1081 failed-строка на **1** получателя  → requeue слал бы 1081 опенер одному человеку, теперь 1
      6d4ba212 —  452 строки на 36 получателей          → 452 → 36
      24658b65 —   21 строка на 6 получателей           → 21 → 6
      0c28f9b0 —   19 строк на 11 получателей           → 19 → 11
      (b234e3cb 79/79, ff6e2d10 43/43, b7cc7d06 5/5 — уже 1:1, поведение не меняется)
  - Миграция НЕ требуется: изменения чисто в коде, схема не тронута.
files_changed:
  - app/services/queue.py: новая константа COLD_FAIL_RELEASE_CAP=3 + ограниченный
    cold-fail CCA release в _fail_item (один COUNT-запрос вместо has_sent, WARNING на кэпе)
  - app/routers/campaigns.py: requeue_failed — UPDATE через
    `id IN (SELECT DISTINCT ON (recipient_phone) … ORDER BY recipient_phone, created_at DESC, id DESC)`
    + docstring с обоснованием recency-колонки и NULL-safety
  - tests/test_queue_lifecycle_fixes.py: 3 регресс-теста WR-15 + хелпер _cold_fail_once;
    границы выведены из импортированного COLD_FAIL_RELEASE_CAP (CAP-2 / CAP-1 / range(CAP))
  - tests/test_campaign_lifecycle_fixes.py: хелпер _seed_failed_cycle +
    test_requeue_failed_dedups_per_recipient

## Specialist Review

Reviewer: pr-review-toolkit code-reviewer (python / async-SQLAlchemy), 2026-08-13.
Verdict: **SUGGEST_CHANGE** — сам фикс корректен, проблема в recovery-пути, который он рекомендует.

Подтверждено корректным (правок не требует):
- **Арифметика кэпа.** `UPDATE MessageQueue` (queue.py:1648) уходит в ту же транзакцию, поэтому
  текущая падающая строка уже `'failed'` к моменту COUNT → входит в `failed_cnt`.
  Эффективный потолок ровно **3** строки на (campaign, phone), не 4.
  Границу пинит тест `tests/test_queue_lifecycle_fixes.py:216` (prior=2 → retain).
- **Сессия/транзакция.** Одна сессия, один `commit()` в конце; SELECT автофлашит добавленный
  `MessageLog`. Ни partial commit, ни detached-state. `.one()` безопасен (COUNT всегда даёт строку).
- **Escape hatch достижим.** `requeue_failed` (app/routers/campaigns.py:860-865) ре-пендит по
  `campaign_id + status='failed'` и CCA вообще не смотрит → контакты с удержанной CCA
  реально восстановимы. Проверено.
- **Перформанс.** Индекса на `(campaign_id, recipient_phone)` нет, но message_queue = 3138 строк
  в проде, и старый `has_sent`-проб имел тот же предикат — не проблема.
- **Тесты.** 11/11 зелёные под test-overlay; per-campaign scoping реально проверяется
  (без скоупинга счёт был бы 4 ≥ CAP и ассерт бы перевернулся).

**СТАТУС: оба пункта ниже ЗАКРЫТЫ (2026-08-13, follow-up).** Important — дедуп по
`recipient_phone` через `DISTINCT ON` в `requeue_failed` + регресс-тест
`test_requeue_failed_dedups_per_recipient` (проверен на падение без фикса).
Minor — константа импортирована, границы выведены как `CAP-2`/`CAP-1`.
Третий пункт (READ COMMITTED race на одновременном фейле двух строк одной пары)
сознательно НЕ закрывался: он ограничен одним лишним витком, дедлока нет, и `FOR UPDATE`
здесь — отдельное решение по строгости, а не часть этого фикса.

Important (confidence 85) — **`requeue-failed` пошлёт дубликаты капнутому контакту.**
`app/routers/campaigns.py:860` ре-пендит *каждую* failed-строку. Кэп намеренно оставляет до 3
failed-строк на один телефон, а pick диспетчера (queue.py:541-560) не дедуплицирует по телефону.
Т.е. операторское действие, которое сам же WARNING (queue.py:1706) и рекомендует, превращает
3 удержанные строки в 3 pending и — если номер стал достижим — в 3 одинаковых опенера одному
человеку. Предложение: дедуп в UPDATE, напр.
`... WHERE id IN (SELECT DISTINCT ON (recipient_phone) id FROM message_queue
   WHERE campaign_id = :cid AND status = 'failed' ORDER BY recipient_phone, created_at DESC)`.

Minor (confidence 80) — **тесты хардкодят границу.** `tests/test_queue_lifecycle_fixes.py:206,219`
передают `prior_failed=1/2` литералами: если CAP потом станет 5, тесты останутся зелёными,
но перестанут щупать границу. Импортировать константу и считать `CAP-2` / `CAP-1`.

Не блокирует, но знать: под READ COMMITTED два воркера, роняющие две строки одной пары
(campaign, phone) одновременно, каждый видит только свой незакоммиченный `'failed'` → оба могут
освободить CCA. Ограничено одним лишним витком, дедлока нет (DELETE сериализуется).
`SELECT ... FOR UPDATE` на CCA-строке закрыл бы это, если нужна строгость.
