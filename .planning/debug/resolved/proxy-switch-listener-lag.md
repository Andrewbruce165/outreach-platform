---
slug: proxy-switch-listener-lag
status: resolved
trigger: |
  Смена прокси sender'у + до 30 сек лага у listener'а.

  POST /senders/{slug}/assign-proxy (senders.py:886) сразу пишет новый proxy
  в БД и коммитит. А listener держит постоянное соединение на аккаунт
  (self.clients[slug], listener.py:1549) и узнаёт о смене прокси только в
  reconcile-цикле — раз в LISTENER_RECONCILE_INTERVAL (30 секунд по
  умолчанию, listener.py:185). Разрыв старого соединения и переподключение
  на новый прокси происходит только на СЛЕДУЮЩЕМ тике (listener.py:1677-1684).

  Окно риска: с момента смены прокси до следующего reconcile-тика (до 30 сек)
  — listener всё ещё висит на СТАРОМ прокси/IP, а любая отправка сообщения /
  warmup / чекер-батч в этот момент возьмёт из БД уже НОВЫЙ прокси
  (queue.py:944, sender.proxy читается заново на каждый send) → тот же
  аккаунт одновременно с двух IP.
created: 2026-07-13
updated: 2026-07-13
---

# Debug Session: proxy-switch-listener-lag

## Symptoms

- **Expected behavior:** Смена прокси у sender'а (`POST
  /senders/{slug}/assign-proxy`) должна применяться атомарно — listener и
  все пути отправки (queue send, warmup, checker) обязаны переключиться на
  новый прокси одновременно, без окна, где аккаунт одновременно
  "виден" Telegram-у с двух разных IP.
- **Actual behavior:** `assign-proxy` коммитит новый `sender.proxy` в БД
  немедленно, но listener переподключается только на следующем
  reconcile-тике (`LISTENER_RECONCILE_INTERVAL`, по умолчанию 30 сек,
  listener.py:185, reconnect listener.py:1677-1684). Всё это время
  `self.clients[slug]` в listener'е держит **старое** TCP/MTProto
  соединение через старый прокси, а `queue.py:944` и другие send-пути на
  каждый вызов читают `sender.proxy` из БД заново — то есть уже видят
  **новый** прокси. Если в этом окне (до 30 сек) произойдёт исходящая
  отправка, warmup-тик или чекер-батч — один и тот же Telegram-аккаунт
  окажется одновременно активен с двух разных IP.
- **Error messages:** Точного зафиксированного кейса под рукой нет —
  пользователь описывает общий паттерн по коду. Но по факту **инцидент уже
  был** (подтверждено пользователем), и это совпадает с уже задокументированным
  паттерном "used under two different IP addresses simultaneously" →
  auth_key kill (см. memory `project-account-import-vendor-session-death` —
  там причиной был tdata-import reuse чужого auth_key, но механизм
  "aккаунт одновременно с двух IP → Telegram убивает сессию" тот же самый
  класс проблемы).
- **Timeline:** Не регрессия — архитектурный риск, который существует с тех
  пор, как reconcile-цикл листенера завязан на периодический polling
  (`LISTENER_RECONCILE_INTERVAL=30s`), а не на event-driven инвалидацию при
  `assign-proxy`.
- **Reproduction:** `POST /senders/{slug}/assign-proxy` на sender с активным
  listener-соединением, затем — в течение следующих ~30 сек до reconcile —
  любая отправка через очередь / warmup-тик / чекер-батч на этот же sender.

## Current Focus

hypothesis: RESOLVED — root cause + secondary defect both fixed and
  live-verified. Fix A+B (2nd round) + новое жёсткое ограничение
  (NEVER proxy=None) реализованы, unit GREEN, live-verify PASS, прод
  восстановлен в чистое состояние, фикс закоммичен (7cc6928).
test: (done) unit через test-overlay + live assign-proxy 10089↔10017 на
  sender-8623199807 с наблюдением listener-логов и флага.
expecting: (met) single proxy-change detection, clean reconnect на НОВОМ
  прокси, флаг снят только после подтверждённого reconnect, НЕТ loop,
  НЕТ TTL-sweep, НЕТ proxy=None пути.
next_action: none — session resolved.

## Eliminated

<!-- none — первичная гипотеза подтверждена прямым чтением кода -->

## Evidence

- timestamp: 2026-07-13
  checked: senders.py:886-932 (assign_proxy handler)
  found: Хендлер делает ТОЛЬКО `sender.proxy = {...новый...}` +
    `proxy_row.assigned_to_sender_id = sender.id` + `await db.commit()`.
    Никакого сигнала/вызова листенеру, никакого forced-disconnect.
  implication: Новый прокси виден в БД немедленно, listener о нём не знает.

- timestamp: 2026-07-13
  checked: listener.py:185, 1549-1552, 1645-1694 (_reconcile_tick),
    1631-1643 (_disconnect_sender)
  found: Листенер держит persistent `self.clients[slug]` +
    `self._proxy_snapshot[sid]`. Смена прокси детектится ТОЛЬКО в
    `_reconcile_tick` сравнением `desired_proxy != snapshot` (строки
    1679-1689), интервал `LISTENER_RECONCILE_INTERVAL=30s`. При детекте —
    `_disconnect_sender` на этом тике, а reconnect на СЛЕДУЮЩЕМ тике через
    NEW-branch (1660-1666). Два окна: (1) commit→disconnect ≤30с — листенер
    на СТАРОМ прокси; (2) disconnect→reconnect ≤30с — листенер offline.
  implication: Окно двойного IP = окно (1): до 30с листенер держит старый IP.

- timestamp: 2026-07-13
  checked: queue.py:942-944 + telegram.py:346-403 (get_client)
  found: Каждый send делает `get_client(..., proxy=sender.proxy, ...)` —
    читает СВЕЖИЙ `sender.proxy` из БД и открывает ВРЕМЕННОЕ новое
    соединение (connect → операция → disconnect_client). get_client НЕ
    кеширует клиент и НЕ сверяет прокси — просто берёт переданный.
    Warmup/checker идут тем же путём (те же `get_client(proxy=...)`).
  implication: В окне (1) send/warmup/checker открывают соединение на НОВОМ
    IP, пока листенер висит на СТАРОМ → один аккаунт одновременно с 2 IP →
    Telegram auth_key kill (класс «used under two different IP addresses
    simultaneously», ср. memory project-account-import-vendor-session-death).

- timestamp: 2026-07-13
  checked: listener.py:432-471 (get_active_senders), models/__init__.py:74-93
    (Sender ORM)
  found: get_active_senders SELECT'ит один `proxy` столбец; в Sender ORM
    единственный proxy-столбец `proxy JSONB` (models:93) — НЕТ staging/
    pending/desired-proxy колонки, нет флага «switch in progress».
  implication: Любой фикс с раздельным «live vs desired» прокси требует
    новой колонки → миграции.

- timestamp: 2026-07-13 (LIVE-VERIFY on prod)
  checked: Deploy api+listener; mig 062 auto-applied OK (schema_migrations:
    062_sender_proxy_switch_pending @13:07:44); live assign-proxy on active
    non-checker sender-8623199807 (workspace bb96789d), switched proxy port
    10089→10017 via temp wsk_ key.
  found: Steps 3-5 PASS — assign-proxy 200, new port in response;
    proxy_switch_pending_at set in SAME txn (age 224ms); selection gate
    (queue/warmup/checker identical predicate) returns SKIPPED while flag fresh.
  implication: The flag-set + query-gate half of the fix works exactly as
    designed on live prod.

- timestamp: 2026-07-13 (LIVE-VERIFY — FAILURE)
  checked: listener logs for sender-8623199807 (sid f0bb7f25) across reconcile
    ticks after the switch; proxy-changed frequency; flag clear timing;
    TTL-sweep warnings.
  found: SECONDARY DEFECT surfaced. After the switch the listener enters a
    PERMANENT ~30s disconnect/reconnect loop — ONLY this sender logs
    "🔄 [reconcile] proxy changed" every tick (13:17:23, :17:55, :18:25, :18:55,
    :19:26 …). Mechanism: reconcile detects proxy change → _disconnect_sender
    (listener.py:1731) disconnects the client, but the STILL-RUNNING start_client
    while-loop (listener.py:1611-1727) catches run_until_disconnected returning
    and RECONNECTS using the STALE in-memory sender_info["proxy"] (OLD 10089,
    line 1618) — it NEVER re-reads the DB. It re-adds itself to
    _connected_sender_ids (1644) + sets _proxy_snapshot = OLD proxy (1645), so
    reconcile's NEW branch (fresh DB proxy) never fires; desired(10017) !=
    snapshot(10089) forever → loop. Crucially get_me() on this OLD-proxy reconnect
    CLEARS the flag (1662) → send/warmup/checker resume on the NEW proxy (10017)
    while the listener is verifiably still on the OLD proxy (10089). No TTL-sweep
    warning (Step 7 vacuously true — flag keeps being cleared by the flawed
    reconnect, never ages to TTL).
  implication: The fix's CORE INVARIANT ("after reconnect, listener and send-paths
    share one IP") is VIOLATED. clear-on-reconnect (start_client:1658-1665)
    implicitly assumed the reconnect happens via reconcile's NEW branch with a
    FRESH DB proxy; in reality start_client's pre-existing internal reconnect
    preempts it with the stale proxy. So get_me() confirms liveness on the OLD IP,
    clears the flag, and the double-IP window the fix targets is merely DELAYED,
    not closed — plus a permanent reconnect churn on the account. FIX INCOMPLETE.
    Prod restored: sender reverted to original proxy 10089 (DB set to match the
    listener's stale in-memory snapshot → loop stopped without a full listener
    restart), flag NULL, test proxy 10017 freed, temp wsk_ key deleted.

- timestamp: 2026-07-13 (2ND-ROUND FIX + LIVE-VERIFY — PASS)
  checked: Refined fix A per new HARD RULE (never proxy=None). start_client now
    re-reads the CURRENT proxy from the DB on every (re)connect; if the read
    fails OR the proxy is missing/NULL/empty it logs a warning and RETURNS
    (defers to the reconcile supervisor, which re-spawns) — it NEVER connects
    proxy-less or with a stale/default proxy. Deployed api+listener (rebuild),
    mig 062 applied, 69–70 senders connected. Re-ran the live switch on
    sender-8623199807 (sid f0bb7f25) 10089→10017 via temp wsk_dbgproxy key.
  found: FULL PASS. assign-proxy 200 (port 10017), proxy_switch_pending_at set
    in the SAME txn (age 0s). Listener log sequence: 15:27:34 reconcile detected
    "proxy changed" EXACTLY ONCE → _disconnect_sender → start_client internal
    loop reconnected 5s later → 15:27:41 "✅ слушаем сообщения" + telegram_id
    saved (this UPDATE cleared the flag because connected proxy 10017 == DB
    proxy 10017). Post-switch: proxy-changed count = 1 (NO loop), TTL-sweep
    warnings = 0, proxy=None / "назначенный proxy пуст" / "не удалось
    перечитать" warnings = 0. Subsequent reconcile tick 15:28:04 ~proxy=0
    total=69, sender kept doing catch_up (healthy). Reverted 10017→10089 via
    the same endpoint → identical clean reconnect + flag clear (~21s). Prod
    restored to exact baseline: sender on 10089, proxy 10089 assigned, proxy
    10017 FREE, 0 pending flags, temp key deleted.
  implication: The fix's CORE INVARIANT holds — after a switch the listener and
    the send/warmup/checker paths converge on ONE IP (the flag pauses sends
    until the listener is CONFIRMED on the new proxy, and fix A guarantees the
    internal reconnect comes up on the new proxy, killing the permanent loop).
    The new HARD RULE guarantees no code path ever connects an account without
    its assigned proxy. RESOLVED.

## Resolution

root_cause: >
  Смена прокси не атомарна между процессами. `POST /senders/{slug}/assign-proxy`
  (api-процесс) мгновенно коммитит новый `sender.proxy` в БД без какого-либо
  сигнала listener-процессу. Listener держит persistent-соединение на СТАРОМ
  прокси и узнаёт о смене только на reconcile-тике (≤30с,
  LISTENER_RECONCILE_INTERVAL), где сравнивает desired_proxy != snapshot и
  дисконнектит. Все send-пути (queue.py:944, warmup, checker) на КАЖДЫЙ вызов
  читают свежий `sender.proxy` и открывают временное соединение уже на НОВОМ
  IP. В окне между commit и reconcile-disconnect (≤30с) один Telegram-аккаунт
  оказывается одновременно активен с двух IP → Telegram убивает auth_key
  (session death). Инвариант, который надо восстановить: во время перехода
  send-пути и listener ОБЯЗАНЫ использовать один и тот же IP.
fix: >
  Approach A (пользователь выбрал на DECISION checkpoint): durable per-sender флаг
  `senders.proxy_switch_pending_at`, который ставит паузу send/warmup/checker для
  sender'а до подтверждённого listener-reconnect на новом прокси. Закрывает окно
  двойного IP между мгновенным DB-commit нового прокси и отложенным (≤30с)
  reconnect'ом листенера.

  Реализация:
  1. mig 062_sender_proxy_switch_pending.sql — колонка `proxy_switch_pending_at
     TIMESTAMPTZ NULL` (идемпотентно, ADD COLUMN IF NOT EXISTS). ORM-зеркало в
     app/models/__init__.py (create_all-совместимость для тестовой схемы).
  2. Settings: `proxy_switch_pending_ttl_seconds` (default 180 = 6×30с reconcile;
     env PROXY_SWITCH_PENDING_TTL_SECONDS) в app/config.py.
  3. assign-proxy handler (senders.py) — ставит `proxy_switch_pending_at = func.now()`
     в ТОЙ ЖЕ транзакции, что и новый proxy (DB-clock, чтобы сравниваться с NOW() в
     выборках).
  4. Selection paths пропускают sender'а пока флаг активен и младше TTL:
     - queue.py: и в batch-SELECT _tick (277), и в per-send TOCTOU-гейте
       `_check_rate_limits` (перед самой отправкой);
     - warmup.py: `_get_active_pool` SELECT;
     - contact_check_worker.py: LATERAL resolve-выборка чекера, probe-выборка и
       `_eligible_checkers` count.
     Условие везде: `proxy_switch_pending_at IS NULL OR proxy_switch_pending_at <
     NOW() - make_interval(secs => :proxy_switch_ttl)` → это одновременно и skip
     активного флага, и query-side TTL-предохранитель (даже если листенер мёртв,
     после TTL sender снова eligible — не блокируется навечно).
  5. listener.py: (a) на подтверждённом reconnect (после успешного get_me() в
     start_client) очищает флаг в том же UPDATE, что пишет telegram_id — send
     возобновляется сразу, не дожидаясь TTL; (b) TTL-fallback sweep
     `_sweep_stale_proxy_switch_flags()` в начале каждого _reconcile_tick — NULL'ит
     флаги старше TTL (role-agnostic, чистит и чекеров, которых листенер не держит)
     и логирует warning для наблюдаемости.

  2ND ROUND (закрывает secondary defect из 1-го live-verify + новое HARD RULE):
  6. FIX A — listener.start_client перечитывает актуальный proxy из БД на КАЖДОЙ
     (ре)коннект-итерации (SELECT proxy WHERE id=:sid) вместо переиспользования
     stale in-memory sender_info["proxy"]. Snapshot и коннект идут по свежему
     прокси → внутренний reconnect-loop поднимается на НОВОМ прокси, поэтому
     reconcile больше не детектит вечную «proxy changed» (убит permanent loop).
  7. FIX B — clear-on-reconnect UPDATE снимает флаг ТОЛЬКО когда фактически
     подключённый proxy совпадает с текущим sender.proxy в БД
     (`proxy_switch_pending_at = CASE WHEN proxy IS NOT DISTINCT FROM
     CAST(:connected AS jsonb) THEN NULL ELSE proxy_switch_pending_at END`).
     get_me()-успех на СТАРОМ прокси больше не снимает флаг → нет отложенного
     double-IP.
  8. HARD RULE (memory "never probe live sessions without assigned proxy"):
     если re-read прокси упал / прокси отсутствует / NULL / пустой — start_client
     НЕ подключается (никогда proxy=None, никогда stale/default), логирует warning
     и RETURN'ит, отдавая повтор reconcile-супервизору (единый re-spawner, без
     duplicate-task leak). Проверены все ветки фикса (fix A error-путь, fix B
     get_me, sweep) — ни одного пути с proxy=None.
verification: >
  Статический: py_compile всех изменённых модулей OK.
  Тесты (test-overlay, docker-compose.test.yml):
  - tests/test_listener_reconcile.py + tests/test_senders.py — 35 passed (включая
    расширенный test_assign_proxy_from_workspace_pool: флаг выставлен после
    assign-proxy; новый test_sweep_clears_stale_proxy_switch_flag: stale→NULL,
    fresh остаётся).
  - queue/warmup/checker сьюты — 61 passed, 1 FAILED
    (test_warmup_worker.py::test_restricted_sender_excluded). Этот фейл
    PRE-EXISTING и НЕ связан с фиксом: подтверждено git-stash прогоном на исходном
    warmup.py — тест падает идентично (ассертит исключение spam_limited из warmup,
    а код намеренно ВКЛЮЧАЕТ spam_limited в прогрев, warmup.py:213). Мой клауз
    добавляет только фильтр по proxy_switch_pending_at.
  Human-verify (LIVE, 2026-07-13, 1st round): FAILED — фикс был НЕПОЛНЫЙ
  (permanent reconnect-loop на СТАРОМ прокси, флаг снимался на любом get_me()).

  Human-verify (LIVE, 2026-07-13, 2nd round): PASS — фикс ПОЛНЫЙ.
  Unit (test-overlay): test_listener_reconcile.py + test_senders.py = 37 passed
  (вкл. новые test_clear_flag_only_when_connected_proxy_matches_db,
  test_clear_flag_handles_null_proxy, test_sweep_clears_stale_proxy_switch_flag);
  queue/warmup/checker сьюты = 61 passed, 1 pre-existing FAIL
  (test_warmup_worker::test_restricted_sender_excluded — не связан, ассертит
  исключение spam_limited, а warmup намеренно его включает; мой хунк лишь
  добавляет proxy_switch фильтр).
  Live (sender-8623199807, 10089↔10017): assign-proxy 200 + флаг в той же txn;
  reconcile «proxy changed» РОВНО 1 раз; reconnect на НОВОМ прокси за ~7с; флаг
  снят только тогда (connected==DB); 0 reconnect-loop; 0 TTL-sweep; 0 proxy=None.
  Revert 10017→10089 — идентично чисто. Прод восстановлен в точный baseline.
  Закоммичено: 7cc6928 (только 10 fix-файлов; senders.py — частичный stage,
  только proxy-хунки; 2FA-хунки параллельного агента НЕ тронуты).
files_changed:
  - migrations/062_sender_proxy_switch_pending.sql (new)
  - app/models/__init__.py (Sender.proxy_switch_pending_at)
  - app/config.py (proxy_switch_pending_ttl_seconds setting)
  - app/routers/senders.py (assign_proxy stamps flag + func import — partial stage)
  - app/services/queue.py (batch-SELECT + _check_rate_limits gates)
  - app/services/warmup.py (_get_active_pool gate)
  - app/services/contact_check_worker.py (resolve LATERAL + probe + eligible-list gates)
  - app/services/listener.py (fix A DB re-read + never-proxy-None defer; fix B
    conditional clear; _sweep_stale_proxy_switch_flags)
  - tests/test_senders.py (assign-proxy flag assertion)
  - tests/test_listener_reconcile.py (sweep + fix-B clear-guard tests)
