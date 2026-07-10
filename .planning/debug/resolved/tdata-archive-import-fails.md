---
status: resolved
trigger: "Пользователь не может загрузить архив новых Telegram-аккаунтов через bulk-import — вендор прислал tdata-формат вместо .session+.json"
created: 2026-07-10T00:00:00Z
updated: 2026-07-10T00:00:00Z
goal: find_and_fix
symptoms_prefilled: true
---

## Current Focus

hypothesis: RESOLVED root cause. Вариант C выполнен: 10 tdata → .session сконвертированы offline (opentele, изолированный py3.11 контейнер), preview green (matched=10), все 10 сессий ЖИВЫ (get_me через Decodo proxy). UX-фикс задеплоен + закоммичен (3ddc469).
test: liveness-проба 10/10 alive; targeted+full account_import тесты (19 passed) через test-overlay
expecting: реальный импорт (создание senders) — НЕОБРАТИМО → human-verify checkpoint
next_action: CHECKPOINT (human-verify) — показать 10 живых аккаунтов, ждать подтверждения на реальное создание senders

## Symptoms

expected: Загрузка архива через UI bulk-import создаёт 10 sender-аккаунтов.
actual: Импорт не проходит («не могу загрузить архив»).
errors: неизвестны — искать в логах api
reproduction: POST архива 10сшаа.zip на bulk-import endpoint
started: 2026-07-10, файл появился ~07:23 UTC. Предыдущий инцидент (07-07) — архивы с .session; сейчас tdata.

## Eliminated

## Evidence

- timestamp: 2026-07-10
  checked: структура 10сшаа.zip (60 members)
  found: 10 аккаунтов, каждый = `+<phone>/tdata/` с файлами `key_datas` (388B), `D877F783D5D3EF8Cs` (348B), `D877F783D5D3EF8C/maps` (68B). НЕТ ни одного `.session` или `.json`.
  implication: формат — Telegram Desktop tdata, не Telethon .session + vendor .json.

- timestamp: 2026-07-10
  checked: app/services/account_import.py::unpack_and_pair (строки 182-194)
  found: члены архива группируются ТОЛЬКО по расширению `.json`/`.session` ("any other extension is ignored"). tdata-файлы (`key_datas`, `D877F783D5D3EF8Cs`, `maps`) не имеют этих расширений.
  implication: все члены tdata-архива молча игнорируются → distinct basenames = пустое множество.

- timestamp: 2026-07-10
  checked: реальный прогон `unpack_and_pair(raw)` в контейнере outreach-platform-api на 10сшаа.zip
  found: `matched 0 unpaired 0 malformed 0` — все списки пусты.
  implication: preview возвращает HTTP 200 с пустым recognized-set. Не ошибка, не 500 — просто "0 аккаунтов распознано". Пользователь видит это как "не могу загрузить архив" (нечего импортировать).

- timestamp: 2026-07-10
  checked: магические байты key_datas + наличие opentele
  found: `key_datas` начинается с `TDF$` (Telegram Desktop File-формат) — подтверждает tdata. `opentele` НЕ установлен (ModuleNotFoundError). В requirements.txt только `telethon==1.42.0`.
  implication: конвертация tdata→StringSession требует новой зависимости (opentele) + новой ветки парсинга — это ФИЧА, не однострочный фикс.

- timestamp: 2026-07-10
  checked: логи api-контейнера с 07:00 UTC
  found: контейнер перезапущен ~07:24:56 (Up 56s на момент проверки) — логи запроса-preview за ~07:23 уже ротированы/утеряны. Видно только рестарт воркера импорта.
  implication: точный текст ошибки из логов недоступен, но воспроизведение в контейнере даёт полную картину — логи не нужны.

- timestamp: 2026-07-10 (Variant C — conversion)
  checked: opentele-конвертация tdata → .session
  found: opentele 1.15.1 ломается на Python 3.13 (host) и на py3.11-slim без PyQt5-зависимости (libglib2.0-0). Решено изолированным образом `tdata-convert` (python:3.11-slim + libglib2.0-0 + opentele). `TDesktop(tdata).ToTelethon(UseCurrentSession)` работает OFFLINE (переиспользует auth_key десктопа, без connect). Все 10 → .session 28672B, dc_id=1.
  implication: конвертация чисто-offline, сеть не трогается — низкий риск.

- timestamp: 2026-07-10 (Variant C — preview green)
  checked: converted.zip (10 .session + 10 минимальных .json `{"session_file","phone"}`) через unpack_and_pair + sqlite_to_string_session в api-контейнере
  found: matched=10, unpaired=0, malformed=0; convertible sessions 10/10.
  implication: сконвертированный архив полностью распознаётся существующим импортёром.

- timestamp: 2026-07-10 (Variant C — liveness)
  checked: get_me по каждому из 10 через продуктовый make_telegram_client + Decodo socks5 proxy (отдельный free-порт на аккаунт, asyncio.sleep 4-8с, per-account timeout 30с)
  found: 10/10 ЖИВЫ и authorized. phone_from_tg совпадает с именем папки. tg_id получены, username=null у всех, 9/10 premium. Продуктовый api_id/api_hash + desktop-auth_key связка работает (как и на .session-архивах 07-07).
  implication: аккаунты реальны и готовы к импорту. Реальное создание senders — необратимо → checkpoint.

- timestamp: 2026-07-10 (Task 2 — UX-фикс)
  checked: app/services/account_import.py — новый UnsupportedArchiveError (422 UNSUPPORTED_ARCHIVE) + _looks_like_tdata; raise в unpack_and_pair когда нет ни одной .json/.session пары
  found: 19/19 account_import тестов passed (test-overlay), +2 новых. api пересобран+задеплоен, чистый старт. Оригинальный 10сшаа.zip теперь → 422 с внятным русским сообщением вместо немого matched=0. Commit 3ddc469.
  implication: Task 2 закрыт и в проде.

## Resolution

root_cause: |
  Импортёр bulk-account-import (app/services/account_import.py::unpack_and_pair) по дизайну
  распознаёт аккаунты ТОЛЬКО как пары `<base>.json` + `<base>.session` (группировка строго по
  расширению файла, строки 188-194 — "any other extension is ignored"). Вендор прислал архив в
  формате Telegram Desktop **tdata** (папки `+<phone>/tdata/` с `key_datas`/`D877F783D5D3EF8Cs`/
  `D877F783D5D3EF8C/maps`, магия `TDF$`), где нет ни `.session`, ни `.json`. Все члены архива
  игнорируются → recognized-set пуст (matched=0/unpaired=0/malformed=0), импортировать нечего.
  Это НЕ регрессия и НЕ баг в коде — импортёр никогда не поддерживал tdata; это отсутствующая
  фича формата. Фикс 07-07 (proxy=list + tmp_auth_key) относился к .session-архивам и здесь ни при чём.
fix: |
  Вариант C (одобрен пользователем) + UX-фикс:
  (1) Разовая out-of-product конвертация tdata → Telethon .session: scripts/tdata_to_session.py
  через opentele в изолированном py3.11-контейнере (opentele тянет свой telethon и ломается на
  3.13 — НЕ в проде/requirements). Конвертация offline (ToTelethon(UseCurrentSession)). Сгенерены
  минимальные .json, упакованы в zip, импортированы штатным путём (staging+job+items → живой
  AccountImportWorker → import_one_account: connect via pool proxy → get_me → dedup → create sender).
  (2) UX-фикс в app/services/account_import.py: UnsupportedArchiveError (422 UNSUPPORTED_ARCHIVE) +
  _looks_like_tdata; unpack_and_pair теперь бросает внятную ошибку когда нет ни одной .json/.session
  пары (с tdata-специфичным сообщением при детекте) вместо немого matched=0. Роутер маппит через
  существующий ImportZipError→HTTP. +2 regression-теста. Commit 3ddc469, api пересобран+задеплоен.
verification: |
  - opentele-конвертация: 10/10 tdata → .session (dc_id=1, 28672B).
  - preview на converted.zip: matched=10/unpaired=0/malformed=0; sqlite_to_string_session 10/10.
  - liveness (get_me через Decodo socks5 proxy): 10/10 alive+authorized, phone совпал, 9/10 premium.
  - реальный импорт (job ecc46772, role=sender): 10/10 result=imported, все senders active/auth_status=ok,
    каждому назначен отдельный proxy_pool row (free pool 24→14). Дублей нет (tg_id/phone уникальны).
  - UX-фикс: оригинальный 10сшаа.zip → 422 UNSUPPORTED_ARCHIVE с русским сообщением; 19/19 тестов green.
files_changed:
  - app/services/account_import.py (UnsupportedArchiveError + _looks_like_tdata + raise в unpack_and_pair)
  - tests/test_account_import.py (+2 regression-теста)
  - scripts/tdata_to_session.py (one-off tdata→session конвертер)
next_step: |
  Свежие US-аккаунты (sender-8741723333 … sender-8735089760) созданы active — перед боевой
  рассылкой им нужен ПРОГРЕВ (warmup). Это вне рамок данного дебага, отдельная задача.
