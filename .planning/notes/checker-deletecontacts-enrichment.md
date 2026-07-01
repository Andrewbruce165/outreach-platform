---
title: Приём DeleteContacts-for-enrichment — обогащение данных лида при резолве телефона
date: 2026-07-01
context: Обзор трёх публичных phone/username-checker'ов (bellingcat/telegram-phone-number-checker, 0xAp0llo/telegram-username-checker-plus, johannawild/Telegram_phone_numbers) по запросу «изучи». Единственный валидный нугет — техника чтения расширенного User-объекта из ответа DeleteContactsRequest. Зафиксировано для будущего lead-scoring.
---

# DeleteContacts-for-enrichment (2026-07-01)

## TL;DR

При phone-import резолве Telegram возвращает `User`, у которого есть поля,
которые мы **сейчас выбрасываем**: `status` (last-seen), `premium`, `verified`,
`fake`, `restricted`, `first_name/last_name`. Это **бесплатные сигналы качества
лида** — объект уже на руках после `ResolvePhoneRequest` / `ImportContactsRequest`.
bellingcat дополнительно читает эти поля из ответа **`DeleteContactsRequest`**
(тип `Updates`), утверждая, что он богаче ответа `ImportContacts`.

**Что это НЕ решает:** false-negatives / троттл чекера. Это ортогонально —
обогащение работает только когда номер и так зарезолвился. Приоритет низкий,
это enhancement, а не фикс.

## Точная механика (из bellingcat/telegram-phone-number-checker main.py)

```python
# 1. ImportContactsRequest → users[]
contacts = await client(functions.contacts.ImportContactsRequest([contact]))
users = contacts.to_dict().get("users", [])

# 2. Если ровно 1 матч — удаляем ПО ЧИСЛОВОМУ id и читаем ответ:
updates_response: types.Updates = await client(
    functions.contacts.DeleteContactsRequest(id=[users[0].get("id")])
)
user = updates_response.users[0]   # ← источник богатых полей

# 3. Достают: id, username, usernames[], first_name, last_name,
#    fake, verified, premium, mutual_contact, bot, restricted,
#    restriction_reason, status (→ last-seen), phone
```

Ключевые детали, которые делают приём корректным:
- **Удаление по числовому `id` (или entity), НЕ по строке-юзернейму.**
  johannawild-репа делала `DeleteContactsRequest(id=[username])` (строка) —
  сломано: поле ждёт `Vector<InputUser>`, cleanup молча не отрабатывает,
  импортированный контакт **остаётся висеть в адресной книге чекера** (утечка PII
  + засорение поведенческого профиля → ускоряет троттл).
- bellingcat обрабатывает edge-case `len(users) > 1` («номер сматчил несколько
  аккаунтов») отдельной веткой-ошибкой.

## Самый ценный сигнал: `status` → last-seen (liveness / lead-quality)

`get_human_readable_user_status(user.status)` мапит:
`UserStatusOnline` / `UserStatusOffline(was_online=<дата>)` /
`UserStatusRecently` / `UserStatusLastWeek` / `UserStatusLastMonth` / `Unknown`.

Для холодного аутрича это скоринг: номер, который резолвится **и** был онлайн
недавно, — горячее лида с «last seen last month». `premium`/`verified` — вторичные
сигналы качества.

**Caveat приватности:** пользователь, скрывший «последнее время входа», всегда
отдаёт `UserStatusRecently`/`Empty` независимо от реальной активности. То есть
last-seen — **мягкий** сигнал, не точная метка. Аналитику на нём строить нельзя
(та же ловушка, что с `is_registered` — см. [[checker-false-negatives]]).

## Что у нас уже есть vs дельта

`app/services/checker.py::resolve_phone_with_fallback`:
- ✅ Удаляем корректно — по entity `DeleteContactsRequest(id=[imported_user])`
  (checker.py:153), не по строке. Лучше johannawild.
- ✅ Primary-путь — `ResolvePhoneRequest`; его `result.users[0]` — тот же тип
  `User`, поля `status/premium/verified` там ТОЖЕ есть.
- ❌ Возвращаем только `{is_registered, telegram_id, username}` (checker.py:157-161).
  `status`, `premium`, `verified`, `fake`, `restricted`, имена — **выбрасываем на
  обоих путях** (ResolvePhone и ImportContacts).
- ⚠️ Ответ `DeleteContactsRequest` (наш cleanup, checker.py:152-155) не читаем —
  результат отбрасывается. bellingcat читает именно его. У нас `User` уже есть из
  Import/Resolve, так что читать Delete-ответ необязательно — достаточно
  вытащить поля из уже полученного `imported_user` / `result.users[0]`.

## Если решим внедрять (эскиз, НЕ реализовано)

1. `resolve_phone_with_fallback` → добавить в возвращаемый dict:
   `last_seen` (маппинг статуса), `is_premium`, `is_verified`, `first_name`.
   Источник — уже полученный `User` (не нужен отдельный Delete-ответ).
2. `_save_cache` + `contacts_cache` — новые колонки под сигналы (нужна миграция
   `NNN_*.sql`, идемпотентная; server_default — см. [[project-orm-default-vs-server-default-drift]]).
3. Использовать `last_seen`/`is_premium` в приоритизации очереди рассылки.

## Обзор трёх репозиториев (для протокола)

| репо | что делает | техника данных | брать? |
|---|---|---|---|
| bellingcat/telegram-phone-number-checker | phone→identity (OSINT CLI, 1 аккаунт) | Delete-ответ (Updates) + delete по id | ✅ только приём обогащения |
| 0xAp0llo/telegram-username-checker-plus | `account.CheckUsernameRequest` — свободен ли @handle (сниппер) | — (другая задача) | ❌ мимо |
| johannawild/Telegram_phone_numbers | phone→username (49 строк, sync) | username из Import; Delete сломан (строка вместо id), результат выброшен | ❌ хуже нас |

Ни один не решает наши реальные боли (устойчивость к троттлу, privacy-резолв через
захваченный `@username`) — у нас это сделано лучше всех трёх (Phase 14/17).
