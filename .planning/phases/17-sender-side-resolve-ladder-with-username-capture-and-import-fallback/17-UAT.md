---
status: partial
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
source: [17-01-SUMMARY.md, 17-02-SUMMARY.md, 17-03-SUMMARY.md, 17-04-SUMMARY.md]
started: 2026-06-30T18:16:49Z
updated: 2026-06-30T18:42:00Z
---

## Current Test

[testing paused — live Igor-base run completed; 3 items (block-rate endpoint, stale-username, recipient-block) still need live conditions]

## Tests

### 1. Cold-start — деплой и загрузка API
expected: docker compose up -d --build api пересобирает и поднимает API без ImportError/трейсбеков (особенно удалённый импорт ResolvePhoneRequest); миграции применяются (0 новых); /docs или /health → 200
result: pass

### 2. Block-rate endpoint
expected: GET /api/v1/senders/{slug}/block-rate возвращает {blocks_7d, sends_7d, block_rate} для своего sender; неизвестный/чужой slug → 404; эндпоинт read-only (ничего не меняет)
result: [pending]

### 3. Резолв отправителя без собственного ResolvePhone
expected: Отправка зарегистрированному RU-мобильному резолвится через ResolveUsername(захваченный @username) или ImportContacts; в логах НЕТ ResolvePhoneRequest со стороны отправителя; сообщение доставлено
result: blocked
blocked_by: prior-phase
reason: "Нужен прогретый RU checker, чтобы пометить контакты 'registered' до того как отправитель сможет их резолвить/слать. Доступный ru-account-4 (sender-8298649227) оказался cold (0/30 на реальных RU-мобильных). Сам код лестницы задеплоен и проверен (ResolvePhone отправителя удалён — 0 активных ссылок)."

### 4. Stale-username fall-through
expected: Контакт с устаревшим/переименованным захваченным @username НЕ финализируется как not_registered — отправитель проваливается в тир ImportContacts (в логах "stale" → fall through to import)
result: [pending]

### 5. Захват блокировки получателя без авто-паузы
expected: Отправка тому, кто заблокировал отправителя, пишет durable event_type='blocked' (block-rate растёт), но НЕ ставит sender на паузу, НЕ флипает restriction_status и НЕ останавливает pending-очередь
result: [pending]

### 6. ЖИВОЙ ТЕСТ: защита данных при cold-чекере (Igor база, 30 моб.)
expected: При резолве cold/throttled чекером его 0-registered результаты НЕ финализируются как not_registered — батч откатывается в suspect/pending, чекер авто-деградирует в spam_limited, ложные contacts_cache строки не переживают (confidence-gate их подавляет)
result: pass
note: "30 чистых +79 моб. из базы Игоря → checked=30 registered=0 not_registered=30 на ru-account-4. Inline 14-05 detector ('anomalous empty-rate 30/30') деградировал чекер в spam_limited (trip#1) и откатил весь батч в pending+suspect — НИ ОДНОГО ложного not_registered не финализировано. Phase 17 (17-02 confidence-gate) + Phase 14 safety-net подтверждены вживую. База восстановлена в исходное состояние, 32 ложные cache-строки вычищены."

### 7. ЖИВОЙ ТЕСТ: здоровый чекер — полный резолв + финализация (Igor база, 30 моб.)
expected: Здоровый чекер с реальным contacts-резолвом даёт реалистичную долю registered (~50% на моб.), проходит control-probe, и его батч ФИНАЛИЗИРУЕТСЯ как high-confidence clean (не откатывается)
result: pass
note: "ca-account-1 (sender-7867638054, +16398525147, +1 CANADA) на тех же 30 чистых +79 моб.: control-probe 3/3, batch checked=30 registered=16 not_registered=14 = 53%, чекер остался здоров (trip 0), батч финализирован high/clean (данные сохранены). ИЗОЛЯЦИОННЫЙ ТЕСТ опроверг country-гипотезу: тёплый +1 CA = 53%, холодный +79 RU = 0%. Решает то прогретость/здоровье, не страна (Phase 17 D-10 подтверждён)."

### 8. ЖИВОЙ ТЕСТ: send-path import-лестница (SRLD-03/04/05) на кампании ff6e2d10
expected: Отправка идёт по 3-tier лестнице (cache → ResolveUsername(захваченный @username) → ImportContacts), собственный ResolvePhone отправителя НЕ вызывается; registered-контакт с захваченным @username доставляется через ResolveUsername
result: pass
note: "Кампания ff6e2d10 (Barter-ВЭД), сендер barter_аккаунт Игоря (sender-7375001431, +79, свежеразбанен). Контролируемый батч 5. Логи: 2 контакта с захваченным @username (Cha3off, Larson7171) → 'not in cache, calling ResolveUsernameRequest' → ДОСТАВЛЕНО (result_message_id set). 3 контакта registered-но-без-username → 'tier-3 ImportContacts (registered, no live username)' → import пуст (phone-private) → 'не зарегистрирован'. НИ ОДНОГО ResolvePhoneRequest — старый класс ошибки 'No user associated (caused by ResolvePhoneRequest)' устранён (D-01). Вывод: захваченный @username = ключ доставки для phone-private registered; сегодняшний username-фикс напрямую повышает reachability. Ограничение (inherent Telegram privacy): registered + phone-private + без @username = недоставляемо любым аккаунтом."

## Summary

total: 8
passed: 4
issues: 1
pending: 3
skipped: 0
blocked: 1

## Gaps

- truth: "Captured @username persists to contacts.tg_username_resolved on the batch checker path (SRLD-01/SRLD-02)"
  status: fixed
  reason: "Live ca-account-1 batch: 0/16 registered got tg_username_resolved (all NULL) despite tg_telegram_id captured. Root cause: resolve_phone_with_fallback captures username (checker.py:113/160) but the batch producer _check_phones_locked drops it — the results.append dict (checker.py:424) has no 'username' key and _save_cache (checker.py:252) takes no username param. Worker _apply_results (contact_check_worker.py:886) is wired to write res.get('username') but always receives None. SRLD-02 test passed because it drove _apply_results directly with a synthetic username, never exercising the real producer."
  severity: major
  test: 7
  root_cause: "Username captured in resolve_phone_with_fallback but not threaded into _check_phones_locked's result dict / _save_cache; integration gap masked by unit test that bypassed the producer."
  artifacts:
    - path: "app/services/checker.py"
      issue: "_check_phones_locked result dict (line ~424) omits 'username'; _save_cache (line ~252) has no username param; cache-hit branch (line ~394) doesn't return username; check_phones docstring (line ~296) omits username key"
  missing: []
  resolution: "FIXED + DEPLOYED + LIVE-CONFIRMED. Threaded username through _check_phones_locked result dict (line 433) + extraction (415) + _save_cache new param storing contacts_cache.username (line 274) + cache-hit branch (401) + cache-read SELECT (249). Added regression test tests/test_checker.py::test_check_phones_batch_carries_username (drives the real producer). 21 checker+worker tests GREEN via test-overlay. Committed 58ea6c3, pushed to origin/main. LIVE CONFIRMATION 2026-07-01 06:56: healthy ca-account-2 (sender-8503645757, +1 CA) batch = 25 registered, 11 got a real tg_username_resolved handle (aleksandra_vlk, gemenr, VDimon76, ...) — vs 0/16 before the fix. VERIFIED working end-to-end."
  debug_session: ""
