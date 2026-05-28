---
title: Кастомные переменные при загрузке базы контактов
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: CVAR-01
related_phases: [v2]
---

## Идея

При CSV-импорте пользователь может задать имена для дополнительных колонок (помимо `phone/name/username/source`). Они складываются в `contacts.custom` JSONB и доступны в шаблонах через `{{custom.X}}`.

**Бэкенд уже готов на 80%** — нужна UX-обвязка.

## Что уже работает

- `contacts.custom` — JSONB поле (Phase 02 plan 02-04)
- `app/services/template.py` поддерживает `{{custom.X}}` рендеринг с empty-fallback sentinel (Phase 04 plan 04-04)
- Russian aliases уже есть для дефолтных полей (`{{имя}}`, `{{юзернейм}}`, `{{телефон}}`, `{{источник}}`, `{{компания}}`)

## Скоуп — что нужно дополнить

- **CSV-импорт UI:**
  - После парсинга показать таблицу `column_name → field_mapping`
  - Известные mapping: `phone/email/name/username/source` подсказывать автоматически
  - Unmapped колонки — дать пользователю переназвать (например, `Company → custom.company`)
  - Валидация: имена кастомных полей snake_case latin (для использования в `{{custom.X}}`)
- **Agent / Campaign редактор:**
  - Автодетект available variables для выбранной папки контактов:
    `SELECT DISTINCT jsonb_object_keys(custom) FROM contacts WHERE folder_id = X`
  - Показать как chips/autocomplete в textarea с шаблоном
- **Template validation:**
  - При сохранении шаблона с `{{custom.foo}}` — проверить что `foo` существует хотя бы у одного контакта в target папке
  - Warning если нет; preview с заглушкой типа `[пусто]`
- **Empty fallback** уже есть в `render_template` (sentinel-based, Phase 04 plan 04-04) — пустая переменная не оставляет двойных пробелов и dangling-пунктуации

## Зачем

1. Клиент с базой `phone, name, company, role, last_contacted_at` не может использовать `company/role` в шаблонах без CVAR — а это **самый востребованный способ персонализации**
2. Конкуренты (lemlist/instantly) дают любые custom fields из CSV — без этого мы выглядим примитивно
3. Резко повышает персонализацию первого сообщения — это влияет на reply rate сильнее любых других оптимизаций

## Зависимости

- `contacts.custom` уже есть (Phase 02 plan 02-04)
- `template.py` уже умеет (Phase 04 plan 04-04)
- Только UX-обвязка + autocomplete + validation

## Альтернатива

Hardcoded набор полей (`phone, name, username, source, company, role, location`) — проще, но негибко. Клиенты с нестандартными колонками будут жаловаться.

## Открытые вопросы

- Лимит на количество custom-полей per contact (например, 20)?
- Что делать с пустыми custom-значениями — empty string или null (сейчас sentinel-fallback в template.py)?
- Connection с AIUX-01: AI-ассистент по черновику может предлагать `{{custom.X}}` подстановки на основе available variables
