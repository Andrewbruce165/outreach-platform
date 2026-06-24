# Phase 9: Cold-Contact Failover - Context

**Gathered:** 2026-06-24
**Status:** Ready for planning

<domain>
## Phase Boundary

При фризе sender'а перекинуть его **не-контактированные** pending-задачи очереди (никогда не отправлялись, диалог не начат) на здоровые аккаунты пула через `get_or_assign_sender`; **идущие диалоги остаются на своём аккаунте** и продолжают отвечать (континуити). Это «Phase C» документа `.planning/proposals/sender-pool-resilience.md`.

**Вне scope (живёт в других фазах / non-goal блока):**
- Failover **активных** диалогов на другой аккаунт — non-goal всего блока (ломает континуити; ждут свой аккаунт).
- Здоровье пула (N активно / K на паузе до T) + бейдж во фронте → **Phase 10** (Pool Visibility).
- Cross-campaign load awareness (sender в 2 кампаниях) — non-goal блока (sender-lock и так запрещает 2 running).
- Режим «затихать и на ответах» при мягком лимите — дефолт = продолжаем отвечать.

</domain>

<decisions>
## Implementation Decisions

### Триггер failover (когда/где)
- **D-01:** Один **shared-хелпер** `failover_cold_backlog(sender_id)` (рабочее имя), вызывается **inline сразу после** того как sender помечен restricted и его pending поставлен на паузу. Нулевая задержка, без нового воркера, DRY через одну функцию.
- **D-02:** Точки вызова — **все** пути фриза, которые паузят pending:
  - `queue.py` PEER_FLOOD блок (~L733) — после UPDATE `restriction_status='spam_limited'` + pause pending.
  - `queue.py` ACCOUNT_FROZEN блок (~L776) — после UPDATE `restriction_status='frozen'` + pause pending (см. D-07).
  - `listener.py::_handle_antispam_signal` (~L881, Phase 7 rewrite) — после pause pending + флага spam_limited.
- **D-03:** **Осознанное ограничение:** inline-триггер НЕ подхватывает senders, замёрзших до деплоя, и pending, осевшее позже. Safety-net sweep НЕ делаем в этой фазе (отвергли — лишний движущийся компонент). Если понадобится — отдельная задача.

### Предикат «safe-to-failover» (что переносим)
- **D-04:** Переносим queue-строку, если выполняется ВСЁ:
  1. `status='pending'` AND `item_type=message` (холодный первый контакт);
  2. нет ни одной `'sent'`/`'processing'` queue-строки по (`campaign_id`, `recipient_phone`) — этому контакту в этой кампании ещё не отправляли;
  3. **диалог не начат**: нет `conversations`-строки по (`workspace_id`, `contact_phone`) **ИЛИ** строка есть, но в `messages_log` нет ни одной строки по (`workspace_id`, `recipient_phone`) (пустой диалог — создан, но 0 сообщений).
- **D-05:** Вариант «шире, чем строгий ноль истории» выбран осознанно: пустой Conversation (создан, но без сообщений) — это всё ещё холодный контакт, его безопасно перенести. Континуити ломается только если уже был обмен сообщениями.
- **D-06 (discretion на plan):** точная SQL-форма проверки «0 сообщений» (`NOT EXISTS messages_log` по phone vs. по conversation_id; учитывать ли `message_type` incoming/outgoing отдельно) — деталь research/plan; интент: «ни одного отправленного И ни одного полученного сообщения по этому контакту».

### Hard-freeze vs soft (объём срабатывания)
- **D-07:** Failover применяется к **обоим** состояниям restriction, которые паузят cold backlog: **soft** `spam_limited` (PEER_FLOOD / antispam-сигнал) **и** **hard** `frozen` (ACCOUNT_FROZEN / banned). Логика: с забаненного/frozen аккаунта холодный backlog вообще никогда не уйдёт → перенос тем важнее.
- **D-08:** Правило «отвечать с того же аккаунта» (континуити) к **hard** не относится (replies тоже падают) — но это про **активные диалоги**, которые мы и так не трогаем. Для **cold** backlog hard и soft эквивалентны: перенести на здоровых.

### Анти-dogpile / распределение (C2)
- **D-09:** Для **каждой** safe-строки заново вызывать `get_or_assign_sender` / `_pick_least_loaded` → ровный спред по ВСЕМ здоровым аккаунтам пула. Кандидат-фильтр rotation уже исключает restricted (`restriction_status='none'` + active/ok, Phase 7) — замёрзший sender сам собой не попадёт в приёмники.
- **D-10:** Перенос = сменить `sender_id` queue-строки на нового + `scheduled_at=NOW()` (status остаётся `pending`) + **синхронно обновить** sticky `campaign_contact_assignments` (тот же инвариант, что у Phase 8 ребаланса D-08).
- **D-11:** **Без явного cap** (ни per-receiver day-headroom, ни жёсткого batch-N). Обоснование: существующий per-sender rate-limiter (4/мин, 20/час, 150/день) — это естественный тротл на **отправке**; перенесённые строки просто встают в очередь приёмника и сливаются в его темпе. Cap при inline-триггере создавал бы орфанов (овэрфлоу вернулся бы только когда замёрзший сам оживёт), а safety-net sweep мы не делаем (D-03).
- **D-12:** **Логировать** что перенесено: сколько строк, с какого sender'а, на каких приёмников (для аудита; перекликается с Phase 10 visibility, но без новых полей/эндпоинтов здесь).

### Fallback: нет здоровых приёмников
- **D-13:** Если `get_or_assign_sender` не находит кандидата (пул=1, или все senders limited/frozen) → строки **остаются paused** на замёрзшем sender'е (текущее поведение) — ждут reconcile-resume, когда свой аккаунт оживёт. Failover = **best-effort**, ничего не теряется и не падает в `failed`. Залогировать «некуда переносить».

### Claude's Discretion
- Точная SQL-форма предиката «0 сообщений» (D-06).
- Транзакционные границы хелпера (атомарный UPDATE queue + CCA), идемпотентность под параллельным воркером (по образцу Phase 8 rebalance: `FOR UPDATE SKIP LOCKED`).
- Сигнатура/имя `failover_cold_backlog`, где он живёт (новый `app/services/failover.py` vs. в `rotation.py`/`queue.py`).
- Формат лог-сообщений (D-12) и уровень.
- Деривация требований фазы (FAIL-0x) — на этапе research/plan, как делали для Phase 7/8.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Дизайн фичи (главный источник)
- `.planning/proposals/sender-pool-resilience.md` — полный дизайн блока. **Phase 9 = «Phase C»** этого документа (C1 предикат un-contacted, C2 анти-dogpile). См. §«Finalised freeze policy», §«What already works (do NOT rebuild)», §«Gaps» п.3, §«Open decisions» (C1/C2 — закрыты в этом CONTEXT).

### Roadmap
- `.planning/ROADMAP.md` §«Phase 9: Cold-Contact Failover» — goal, depends on Phase 8. §«Non-goals (v1 этого блока)» (~строка 245) — failover активных диалогов = non-goal.

### Прошлая фаза (граница 8↔9)
- `.planning/phases/08-pool-management-and-even-distribution/08-CONTEXT.md` — D-04/D-06 (detach с pending блокировался, авто-реассайн отложен в Phase 9), D-08 (паттерн синхронного апдейта CCA+queue при ребалансе — переиспользовать здесь).

### Backend код (reuse / точки врезки)
- `app/services/queue.py` — PEER_FLOOD блок (~L733-774), ACCOUNT_FROZEN блок (~L776-812), per-sender skip restricted (~L401). Сюда добавляются вызовы хелпера (D-02).
- `app/services/listener.py` — `_handle_antispam_signal` (~L881-957, Phase 7), restriction-reconcile loop (~L1352-1449, авто-resume по `restricted_until`). Точка врезки antispam-пути (D-02).
- `app/services/rotation.py` — `get_or_assign_sender` (L35), `_pick_least_loaded` (L198), candidate-фильтр (~L112-126, уже содержит `restriction_status='none'`). Основа распределения (D-09).
- `app/models/__init__.py` — `MessageQueue` (L190: status/recipient_phone/campaign_id/sender_id/scheduled_at), `Conversation` (L248: contact_phone/campaign_id/status), `MessageLog`/`messages_log` (L108: recipient_phone/message_type/workspace_id — якорь для «диалог без сообщений»), `CampaignContactAssignment` (L554), `CampaignSender` (L535).
- `app/services/rebalance.py` (Phase 8) — `rebalance_on_attach` campaign-scoped even-split с `FOR UPDATE SKIP LOCKED` + CCA-sync + идемпотентностью. **Ближайший аналог** для транзакционного паттерна failover-переноса.

### Кодовая карта
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INTEGRATIONS.md` — общая структура.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `rotation.get_or_assign_sender` / `_pick_least_loaded` — готовый least-loaded выбор по здоровому пулу; вызывать per-item (D-09). Кандидат-фильтр уже исключает restricted senders.
- `app/services/rebalance.py::rebalance_on_attach` (Phase 8) — образец для транзакционного, идемпотентного переноса queue-строк + синхронного CCA (FOR UPDATE SKIP LOCKED). Failover — очень близкая операция.
- Restriction-reconcile loop (listener.py) — авто-resume оставшегося paused (fallback D-13) уже работает, ничего добавлять не нужно.

### Established Patterns
- **Replies не gated пулом/restriction** — только `ai_enabled`/manager-takeover. Поэтому активные диалоги перенос не трогает, они продолжают отвечать (домен-инвариант, основа non-goal).
- **Раздача sticky на enqueue** (campaign_contact_assignments), не lazy при отправке → перенос обязан обновлять CCA синхронно с queue-строкой (D-10).
- **Per-sender rate-limiter** (4/20/150) — естественный тротл на отправке; перенесённые items не дают мгновенного всплеска (основа решения «без cap», D-11).
- Миграции: raw SQL `NNN_short.sql`, идемпотентные, авто-applier. **Скорее всего миграций нет** — failover работает на существующих колонках (`sender_id`, `restriction_status`, `scheduled_at`); проверить на plan.

### Integration Points
- 2-3 точки вызова хелпера в `queue.py` (PEER_FLOOD, ACCOUNT_FROZEN) + `listener.py` (antispam) — D-02.
- Новый хелпер (вероятно `app/services/failover.py`) дёргает `rotation.get_or_assign_sender` и пишет в `message_queue` + `campaign_contact_assignments`.
- Бэкенд-only фаза — фронта/эндпоинтов не добавляем (видимость пула = Phase 10).

</code_context>

<specifics>
## Specific Ideas

- Триггер инцидента всего блока: кампания b7cc7d06 зависла на 0 pending — 37 контактов терминально `failed` antispam-сигналом без авто-возобновления (см. proposal §Trigger). Phase 7 убрал терминальный `failed`; Phase 9 закрывает «оставшийся backlog не ждёт recovery, а уходит на здоровых».
- Failover намеренно **best-effort**: лучше перенести часть и продолжить, чем падать или терять задачи (D-13).
- «Шире» предикат (пустой Conversation тоже переносим, D-05) выбран сознательно поверх строгого «ноль истории».

</specifics>

<deferred>
## Deferred Ideas

- **Safety-net sweep** (периодический воркер/reconcile-хук, подбирающий senders замёрзших до деплоя и осевшее позже pending) — отвергнут в этой фазе (D-03). Кандидат на отдельную задачу, если inline-покрытия не хватит на практике.
- **Per-receiver day-headroom cap / жёсткий batch-cap** — отвергнуты (D-11), т.к. rate-limiter уже тротлит, а cap при inline-триггере плодит орфанов. Вернуться, только если появится safety-net sweep.
- **Видимость «cold backlog застрял — нет здоровых аккаунтов»** (флаг/бейдж) → **Phase 10** (Pool Visibility). Здесь только лог (D-12), без новых полей/эндпоинтов.

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 9-cold-contact-failover*
*Context gathered: 2026-06-24*
</content>
</invoke>
