---
status: resolved
trigger: "На /contacts при перезагрузке страницы стат-карточки (Total/In Telegram/Checking/Not found) сперва показывают неверные цифры, через время — корректные"
created: "2026-06-29"
updated: "2026-06-29"
---

# Debug Session: contacts-stats-flash-wrong

## Symptoms

- **Expected behavior:** Стат-карточки папки (Total / In Telegram / Checking / Not found) сразу показывают корректные значения по всей папке.
- **Actual behavior:** При перезагрузке `/contacts` сперва кратко показываются неверные цифры (скрин 1: Total 5192, In Telegram 41, Checking 0, Not found 159 — сумма категорий 41+0+159 = 200), затем через ~секунды появляются правильные (скрин 2: In Telegram 126, Checking 4135, Not found 931 — сумма 5192).
- **Error messages:** нет.
- **Timeline:** при каждой перезагрузке страницы.
- **Reproduction:** открыть https://aimly-tg-outreach.lovable.app/contacts, перезагрузить, смотреть на верхние 4 карточки.

## Key observation (orchestrator)

- Скрин 1 суммы: 41 + 0 + 159 = **200** = размер первой страницы пагинации (`1–200 of 5,192`).
- Скрин 2 суммы: 126 + 4135 + 931 = **5192** = вся папка.
- Гипотеза-кандидат: карточки сперва рендерятся из первой подгруженной страницы (200 строк), затем заменяются настоящим folder-level агрегатом из API. Возможно фронт считает стату по `items` вместо отдельного aggregate-поля, либо aggregate-запрос приходит позже первой страницы строк.

## Current Focus

- **hypothesis:** CONFIRMED. Стат-карточки считаются на клиенте из `contactsForStats`, который при незавершённой загрузке агрегата падает на первую страницу (200 строк): `const contactsForStats = contactsStatsQ.data ?? contacts;` (contacts.tsx:499).
- **test:** прочитать contacts.tsx + бэкенд folders/contacts роутеры, сопоставить источники цифр.
- **expecting:** найти fallback на page-1 items при ещё-не-загруженном агрегате.
- **next_action:** заменить fallback так, чтобы карточки не считались по первой странице; добавить серверный stats-endpoint для мгновенного корректного агрегата.

## Evidence

- timestamp: 2026-06-29
  checked: frontend src/routes/_authenticated/contacts.tsx
  found: Две query. `contactsQ` (стр. 419-426) тянет ТОЛЬКО текущую страницу (limit=200). `contactsStatsQ` (стр. 428-432) через `fetchAllFolderContacts` постранично выкачивает ВСЮ папку (5192 строки = ~26 запросов по 200). Стр. 499 `const contactsForStats = contactsStatsQ.data ?? contacts;` — пока агрегат грузится, `data===undefined` → fallback на `contacts` = первая страница (200 строк). `stats` useMemo (стр. 515-520) считает inTg/checking/notFound по `contactsForStats`.
  implication: При reload карточки сперва считают по 200 строкам (41+0+159=200), затем перерисовываются на полный агрегат (126+4135+931=5192). Точно совпадает с симптомом.

- timestamp: 2026-06-29
  checked: backend app/routers/folders.py + contacts.py
  found: `/folders` отдаёт только `contact_count` (total), без разбивки по tg_status. `/contacts` поддерживает фильтр `?tg_status=` и пагинацию, но НЕ отдаёт агрегат. Серверного stats-endpoint нет — поэтому фронт выкачивает все строки клиентом.
  implication: Корректный фикс — добавить серверный per-folder stats-endpoint (один COUNT-запрос с GROUP BY tg_status), фронт показывает «—» до его прихода вместо неверного fallback. Это убирает и flash, и тяжёлую выкачку 5192 строк.

- timestamp: 2026-06-29
  checked: DB SELECT tg_status, count(*) FROM contacts GROUP BY tg_status
  found: реальные значения — pending(4305), not_registered(955), registered(179). Классификаторы фронта (isInTelegram/isCheckingTelegram/isNotInTelegram, стр. 164-176) маппят их верно: registered→In Telegram, pending→Checking, not_registered→Not found.
  implication: разбивка по статусам корректна; проблема только в источнике/тайминге, не в маппинге.

## Eliminated

## Resolution

root_cause: Стат-карточки на /contacts вычисляются на клиенте из `contactsForStats = contactsStatsQ.data ?? contacts` (contacts.tsx:499). Пока фоновая выкачка всей папки (`fetchAllFolderContacts`, ~26 страниц для 5192 контактов) не завершилась, `contactsStatsQ.data` === undefined и расчёт падает на `contacts` — первую страницу пагинации (200 строк). Отсюда «вспышка» 41/0/159 (сумма=200) до появления настоящего агрегата 126/4135/931 (сумма=5192).
fix: |
  Двухчастный фикс, источник цифр перенесён на сервер.
  BACKEND (deployed):
    - app/schemas/__init__.py: новая схема FolderStatsResponse {total, in_telegram, checking, not_found}.
    - app/routers/folders.py: новый GET /api/v1/folders/{id}/stats — один SELECT tg_status, COUNT(*) GROUP BY tg_status, бакетирование по тем же правилам, что и фронтовые классификаторы; workspace-scoped, 404 на cross-tenant.
  FRONTEND:
    - src/routes/_authenticated/contacts.tsx: удалён fetchAllFolderContacts (выкачка всех 5192 строк) и fallback `contactsForStats = contactsStatsQ.data ?? contacts`. contactsStatsQ теперь зовёт /folders/{id}/stats. stats = null пока агрегат не загрузился. MiniMetric при value===null рисует placeholder «…» вместо неверных page-1 цифр.
verification: |
  - tests/test_folders.py: +3 теста (breakdown, empty=all-zero, cross-tenant 404). Весь файл 13/13 PASS через test-overlay (реальный Postgres GROUP BY).
  - Frontend `npx tsc --noEmit`: EXIT 0, ошибок нет.
  - Backend rebuilt+deployed: api стартовал чисто, /api/v1/folders/{folder_id}/stats зарегистрирован в openapi.
  - HUMAN-VERIFY CONFIRMED (2026-06-29): фронт запушен в main (commit 124893a, репо aimly-tg-outreach), Cloudflare задеплоил, прод-сборка `npm run build` EXIT 0. Пользователь перезагрузил /contacts — вспышки page-1 цифр больше нет («все ок»).
files_changed:
  - app/schemas/__init__.py (backend)
  - app/routers/folders.py (backend)
  - tests/test_folders.py (backend)
  - src/routes/_authenticated/contacts.tsx (frontend repo: aimly-tg-outreach)
