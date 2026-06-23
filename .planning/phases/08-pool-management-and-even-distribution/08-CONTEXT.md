# Phase 8: Pool Management and Even Distribution - Context

**Gathered:** 2026-06-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Дать кампании реальный пул из **≥2 аккаунтов** и управлять им на лету. Конкретно:
- `POST /campaigns/{id}/senders` (attach) и `DELETE /campaigns/{id}/senders/{sid}` (detach) — рост/сжатие пула;
- фронт-панель управления пулом на странице кампании (репо `aimly-tg-outreach`);
- подтверждение/усиление равномерной раздачи least-loaded по пулу + лёгкий ребаланс при добавлении аккаунта на ходу.

**Вне scope (живёт в других фазах):**
- Автоматический failover не-контактированных задач при фризе sender'а → **Phase 9** (Cold-Contact Failover).
- Здоровье пула в ответе кампании / бейдж во фронте → **Phase 10** (Pool Visibility).
- Failover **активных** диалогов на другой аккаунт — non-goal всего блока (ломает континуити).

</domain>

<decisions>
## Implementation Decisions

### Attach/detach lifecycle (Q1)
- **D-01:** attach и detach разрешены на статусах **draft / paused / running** — пул можно менять на ходу, без обязательной паузы.
- **D-02:** attach валидирует нового sender'а через существующие хелперы: `_validate_workspace_owns_senders` (workspace-изоляция) **и** sender-lock — нельзя прицепить аккаунт, который уже привязан к ДРУГОЙ running-кампании этого workspace. Конфликт → переиспользовать контракт `_check_sender_lock` (409 со списком `{sender_id, campaign_id, campaign_name}`, как на `/start`).
- **D-03:** **min-pool guard:** нельзя отцепить **последний** sender у running-кампании (осталось бы 0 → кампания зависает). Detach последнего у running → **409**. Для draft пустой пул допустим (консистентно с create, где `sender_ids` default `[]`).

### Detach семантика (Q2) — guard, без авто-реассайна
- **D-04:** detach **блокируется (409)**, если у отцепляемого sender'а есть **неотправленный cold pending** в этой кампании (queue-строки `status='pending'`, никогда не отправлялись, диалог не начат). Сообщение: предложить поставить кампанию на pause или дождаться слива pending.
- **D-05:** **активные диалоги** (по контактам этого sender'а уже начат диалог / был ответ) **не зависят от членства в пуле** — продолжают отвечать (replies gated by `ai_enabled`/manager-takeover, не pool-membership). Detach их не трогает.
- **D-06:** Автоматический перенос cold backlog отцепленного sender'а на здоровый пул **НЕ делаем в Phase 8** — это Phase 9. Граница намеренная.
- **⚠ Implication:** на активно работающей running-кампании sender почти всегда имеет pending, поэтому D-04 на практике означает «detach живого sender'а на running обычно требует pause/ожидания». Это осознанный trade-off ради чистой границы с Phase 9.

### Rebalance при attach (Q3) — лёгкий ребаланс
- **D-07:** контакты назначаются на sender'а **на enqueue** (sticky `campaign_contact_assignments`), не лениво при отправке. Значит least-loaded сам по себе НЕ догрузит новый аккаунт, если папка уже полностью заэнкьюена.
- **D-08:** при attach в running-кампанию выполнять **лёгкий ребаланс**: перенести часть **неотправленных cold pending** с перегруженных senders на новый, чтобы распределение приблизилось к least-loaded. Переносим только un-sent cold pending; **активные диалоги не трогаем**; обновлять `campaign_contact_assignments` синхронно с queue-строками.
- **D-09:** точный алгоритм ребаланса (порог «перегружен», cap размера батча, переиспользование `_pick_least_loaded` vs новый проход) — деталь research/plan. Интент зафиксирован: целевое — ровный split, операция идемпотентна и безопасна под нагрузкой.

### Frontend (Q4)
- **D-10:** управление пулом — **отдельная панель «Senders / Пул» на странице кампании** (не только визард). Работает и для draft, и для running.
- **D-11:** панель: мультиселект/chips добавляемых аккаунтов, add/remove, показ **locked-аккаунтов** (заняты другой running-кампанией — из `attached_senders[].locked_by_campaign_name`), отображение ошибок 409 (sender-locked / min-pool / detach-blocked) человекочитаемо.
- **D-12:** существующий выбор senders в визарде создания остаётся; панель — это управление пулом уже созданной кампании.

### Claude's Discretion
- Точные коды/тела ошибок (envelope) — следовать существующему стилю ошибок в `campaigns.py`.
- Алгоритм ребаланса (D-09).
- Раскладка/компоненты фронт-панели — по существующему дизайн-языку `aimly-tg-outreach`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Дизайн фичи (главный источник)
- `.planning/proposals/sender-pool-resilience.md` — полный дизайн блока Sender Pool Resilience. **Phase 8 = «Phase B» этого документа** (B1 attach/detach, B2 frontend multi-select, B3 even spread + optional rebalance). См. §«What already works (do NOT rebuild)», §«Gaps» п.4, §«Open decisions».

### Roadmap
- `.planning/ROADMAP.md` §«Phase 8» (строки ~215-223) — goal, depends on Phase 7. Non-goals блока — строка ~245.

### Backend код (reuse)
- `app/routers/campaigns.py` — `_validate_workspace_owns_senders` (L141), `_build_attached_senders` (L194), `_check_sender_lock` (L275), `_campaign_to_response` (L228); эндпоинты start/pause/resume (sender-lock уже вызывается на L621/L671). Сюда добавляются 2 новых эндпоинта.
- `app/schemas/__init__.py` — `CampaignSenderAttach` (L566), `CampaignCreate.sender_ids` (L587), `CampaignUpdate` note про sender_ids НЕ через PATCH (L622-625).
- `app/services/rotation.py` — `get_or_assign_sender` / `_pick_least_loaded` (least-loaded раздача, candidate filter ~L112-125; после Phase 7 содержит `restriction_status='none'`).
- `app/services/queue.py` — worker `_tick` (L155-243, round-robin всех eligible), per-sender skip restricted (~L401), PEER_FLOOD pause (~L733).

### Кодовая карта
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INTEGRATIONS.md` — общая структура двух репо (backend + frontend) как единой системы.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_validate_workspace_owns_senders` (campaigns.py:141) — готовая workspace-валидация sender_ids; вызвать на attach.
- `_check_sender_lock` (campaigns.py:275) — готовая проверка «sender занят другой running-кампанией»; переиспользовать на attach, тот же 409-контракт что и start/resume.
- `_build_attached_senders` (campaigns.py:194) — уже отдаёт `locked_by_campaign_id/name`; фронт-панель строится на этих данных.
- `rotation.get_or_assign_sender` / `_pick_least_loaded` — основа для лёгкого ребаланса (D-08).
- M2M-таблицы `campaign_senders` + sticky `campaign_contact_assignments` — менять их и есть суть attach/detach/ребаланса.

### Established Patterns
- Sender-lock: один аккаунт ≠ в двух running-кампаниях одновременно (enforced на start/resume). Attach обязан соблюдать тот же инвариант.
- Раздача sticky на enqueue, не lazy — отсюда необходимость ребаланса (D-07/D-08).
- Replies не gated пулом/restriction — только `ai_enabled`/manager-takeover (отсюда D-05).
- Миграции: raw SQL `NNN_short.sql`, идемпотентные, авто-applier. **Скорее всего миграций нет** — `campaign_senders` уже существует; проверить на plan.

### Integration Points
- 2 новых эндпоинта в `app/routers/campaigns.py`.
- Фронт-панель в репо `aimly-tg-outreach` (страница кампании) → дёргает новые эндпоинты + читает `attached_senders`.
- Возможный апдейт `lovable-handoff/openapi.json` для новых эндпоинтов.

</code_context>

<specifics>
## Specific Ideas

- Сейчас у ВСЕХ 4 кампаний ровно 1 sender — фича впервые даёт реальный пул ≥2.
- Контракт ошибок attach/detach должен переиспользовать существующий sender-lock 409 (список конфликтов), а не изобретать новый.

</specifics>

<deferred>
## Deferred Ideas

- **Авто-реассайн cold backlog при detach/фризе** → Phase 9 (Cold-Contact Failover). В Phase 8 detach с pending просто блокируется (D-04/D-06).
- **Здоровье пула (N active / K limited until T) + бейдж** → Phase 10 (Pool Visibility).
- **Cross-campaign load awareness** (sender в 2 кампаниях) — non-goal блока; sender-lock и так запрещает 2 running.

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 8-pool-management-and-even-distribution*
*Context gathered: 2026-06-23*
