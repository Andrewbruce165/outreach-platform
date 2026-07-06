# Phase 21 — User request notes (captured at add-phase, 2026-07-06)

Хочу сделать загрузку аккаунтов в UI через JSON-сессию. Поддерживать массовую загрузку.

Пример одной сессии (vendor-формат, tdesktop-style метаданные; `session_file` указывает на парный файл сессии, именованный по номеру телефона):

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

Наблюдения для планирования (не решения):

- JSON сам по себе не содержит auth-ключа — это метаданные. Вендоры обычно поставляют пару: `.json` + `.session` (Telethon SQLite) либо tdata. Нужно выяснить у пользователя, какой именно формат сессии идёт в паре (Telethon `.session` наиболее вероятен, раз `session_file` — имя файла по телефону).
- `app_id: 2040` / `app_hash` — это API-креды Telegram Desktop; текущий онбординг использует свои. При импорте сессии надо подключаться с **теми же** app_id/app_hash/device/sdk, с которыми сессия была создана, иначе риск разлогина/бана.
- Поля `twoFA`, `proxy`, `ipv6` — могут быть заполнены в других записях; массовая загрузка должна их принимать.
- Массовая загрузка = несколько таких JSON (+ session-файлы) за один раз через UI.
