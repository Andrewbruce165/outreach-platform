# Phase 21 — User request notes (captured at add-phase, 2026-07-06)

Хочу сделать загрузку аккаунтов в UI через JSON-сессию. Поддерживать массовую загрузку.

Пользователь передал **два файла** (образец одной сессии), лежат в
`/root/apps/aimly/tg-outreach/scratchpad/` (gitignore-scratchpad, в репо не коммитятся):

- `+18646884306.json` — метаданные (vendor-формат, tdesktop-style)
- `+18646884306.session` — **живой Telethon-session (SQLite)**, содержит `auth_key` → это секрет, в git не класть

Т.е. формат поставки = **пара файлов** `<phone>.json` + `<phone>.session`, связанных
через поле `session_file` в JSON (здесь `"+18646884306"` = basename обоих).

---

## Что внутри JSON (образец)

```json
{
  "app_id": 2040,
  "app_hash": "b18441a1ff607e10a989891a5462e627",
  "device": "KVM",
  "sdk": "Windows 10 x64",
  "app_version": "6.8.2 x64",
  "system_lang_pack": "en-US",
  "system_lang_code": "en-US",
  "lang_pack": "tdesktop",
  "lang_code": "en",
  "twoFA": null,
  "role": "",
  "id": null,
  "phone": null,
  "username": null,
  "date_of_birth": null,
  "date_of_birth_integrity": null,
  "is_premium": false,
  "has_profile_pic": false,
  "spamblock": null,
  "register_time": 1783354647,
  "last_check_time": 1783354647,
  "avatar": null,
  "first_name": "",
  "last_name": "",
  "sex": null,
  "proxy": null,
  "ipv6": false,
  "session_file": "+18646884306"
}
```

- `app_id: 2040` / `app_hash: b18441a1…` — публичные API-креды **Telegram Desktop** (не секрет).
- `id/phone/username/first_name/last_name` = null/пусто → метадату по аккаунту вендор не заполнил;
  реальные значения надо тянуть из `get_me()` после коннекта (телефон дополнительно есть в имени файла).
- `twoFA: null` — здесь 2FA-пароля нет, но в других записях поле может быть заполнено (учесть в массовой загрузке).
- `proxy: null` / `ipv6: false` — прокси не задан; в других записях может быть.

## Что внутри .session (реальный разбор образца)

Каноническая **Telethon** SQLite-схема, **session-format version 7** (Telethon 1.42.0 в repo — совместимо):

```
tables: entities, sent_files, sessions, update_state, version
sessions: dc_id=1, server_address=149.154.175.53, port=443, auth_key=256 байт (present), takeout_id=NULL
entities: 0 строк (entity-cache пуст — холодный старт, ср. CLAUDE.md «Telethon entity-cache cold start»)
update_state: пусто
```

→ auth_key валиден, аккаунт на DC1. Это полноценная авторизованная сессия, phone/SMS-онбординг не нужен.

---

## Как система хранит сессии сейчас (сверено с кодом — важно для плана)

1. **Формат хранения ≠ формат поставки.** Мы храним зашифрованную Telethon **StringSession**
   в колонке `senders.session_string` (`encrypt_session()` из `app/services/encryption.py`).
   Вендор даёт SQLite `.session`. **Импорт должен конвертировать:**
   загрузить SQLite-сессию Telethon'ом → `client.session.save()` → StringSession → `encrypt_session` → в БД.
   (Telethon 1.42.0 умеет это напрямую: `TelegramClient(SQLiteSession(path), …)` затем
   `StringSession.save(client.session)` / `client.session.save()`.)

2. **api_id/api_hash — единые глобальные, НЕ per-account.** `make_telegram_client()`
   (`app/services/telegram.py:233`) подставляет `settings.telegram_api_id/telegram_api_hash`
   из конфига для ВСЕХ аккаунтов. Вендор-сессия создана под `app_id=2040`. На уровне MTProto
   auth_key привязан к DC, а не к app_id, поэтому смена app_id обычно не рвёт ключ — но это
   **архитектурное решение фазы** (D-NN): переиспользовать глобальные креды или хранить app_id/app_hash из JSON per-account.

3. **⚠️ Client-fingerprint mismatch — главный риск.** `make_telegram_client` жёстко зашивает
   `_CLIENT_FINGERPRINT` (`telegram.py:152`): `device_model="Desktop"`, `system_version="Windows 10"`,
   `app_version="5.3.1"`, `lang_code="ru"`, `system_lang_code="ru-RU"` + форс `lang_pack="tdesktop"`.
   Вендор-JSON описывает ДРУГОЙ отпечаток: `device="KVM"`, `sdk="Windows 10 x64"`,
   `app_version="6.8.2 x64"`, `lang_code="en"`, `system_lang_code="en-US"`.
   Если реконнектить импортированную сессию нашим захардкоженным отпечатком (app_version 5.3.1, ru-локаль),
   Telegram увидит клиент, отличный от создавшего сессию → риск security-флага / принудительного
   разлогина / терминации сессии. **Решение фазы:** сохранять per-account отпечаток из JSON
   (device/sdk/app_version/lang) и коннектиться им, а не глобальным.

4. **Проверка при импорте.** Перед созданием sender'а: `connect()` → `is_user_authorized()` →
   `get_me()` (заполнить phone/tg_id/username/name), опц. @SpamBot-проверка (`spamblock: null` = неизвестно).
   Битые/невалидные сессии в пачке не должны валить весь батч — отчёт per-file (ok/failed + причина).

5. **Массовость.** Загрузка N пар `(.json + .session)` за раз; матчинг по `session_file`.
   Прокси per-sender pool уже существует — поле `proxy` из JSON можно класть в него.

## Что в JSON есть, а мы сейчас НЕ храним (кандидаты в схему)

app_id/app_hash (per-account), device-отпечаток (device/sdk/app_version/lang_pack/lang_code),
`twoFA` (2FA-пароль!), `proxy`, `ipv6`, register_time, is_premium, has_profile_pic.

## Открытые вопросы для discuss/plan-phase

- Хранить ли app_id/app_hash + device-отпечаток per-account (нужно для безопасного реконнекта) —
  и как это ложится на текущую единственную глобальную пару + захардкоженный `_CLIENT_FINGERPRINT`.
- UI массовой загрузки: drag-n-drop пары файлов? zip? маппинг `.json`↔`.session` по имени.
- Куда девать `twoFA` из JSON (у нас 2FA-пароль хранится/используется в Phase 20 profile-flow).
- Дедуп при импорте (тот же аккаунт уже подключён — по tg_id из get_me()).
