# ТЗ для Lovable — вкладка «Прогрев» (Warmup)

> Paste-ready бриф. Источник дизайна — `15-UI-SPEC.md` (approved), источник API — бэкенд `app/routers/warmup.py` (задеплоен 2026-06-29, openapi уже в `origin/main`).
> Репозиторий: `AGS-Venture-Lab/aimly-tg-outreach` (TanStack Start + React + TS + shadcn, дизайн-система «Pulse» в `src/styles/aimly.css`).

---

## 0. Что делаем

Добавь **одну новую вкладку «Прогрев»** в существующее приложение. Прогрев — это когда Telegram-аккаунты воркспейса переписываются между собой через AI, чтобы безопасно набирать «возраст»/активность без риска бана. Вкладка управляет: master-переключателем прогрева, пулом аккаунтов, контент-настройками и показывает метрики.

**Brownfield-правила (жёстко):**
- Это приложение уже большое и стилизованное. **Ничего нового в дизайн-систему не вносим.** Переиспользуй существующие токены (`aimly.css`), классы (`.btn`, `.card`, `.metric`, `.chip`, `.badge`, `.scroll`, `.sb__item`) и компоненты из `src/components/ui/`.
- **Шаблон для копирования — `src/routes/_authenticated/accounts.tsx`.** Повторяй его структуру (Topbar + `.scroll` body + грид мини-метрик + `.card`/таблица + react-query + sonner-тосты + `ApiError`).
- Тема — **только светлая**, без dark mode.
- **Вся пользовательская копия — на русском** (см. таблицу копирайта ниже, бери дословно).

---

## 1. Wiring (навигация + роут)

- **Навигация:** добавь пункт в `NAV_ITEMS` в `src/components/AppSidebar.tsx`:
  `{ to: "/warmup", label: "Прогрев", icon: Flame }` (иконка lucide `Flame`). Расположи в основной группе рядом с `Accounts`/`Campaigns`.
- **Роут:** новый файл `src/routes/_authenticated/warmup.tsx`,
  `export const Route = createFileRoute("/_authenticated/warmup")({ component: WarmupPage })`.
- После добавления роута TanStack перегенерит `routeTree.gen.ts` (через `vite dev`/build) — руками не править.

---

## 2. Вызовы API (используй существующий хелпер)

Все запросы — через существующий `api()` из `@/lib/api` (он сам добавляет `Authorization: Bearer <supabase token>` и базовый URL). Кэш — `@tanstack/react-query`. Типы можно тянуть из `@/types/api` (`components["schemas"][...]`), сгенерённых из openapi; если типа нет — опиши локальный `type`.

```ts
import { api, ApiError } from "@/lib/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
```

Базовый префикс всех эндпоинтов: **`/api/v1/warmup`**. Все они workspace-scoped (воркспейс берётся из токена — фронту ничего передавать не нужно).

### 2.1 `GET /api/v1/warmup/settings` — настройки + master-флаг
Ответ (всегда с resolved-дефолтами, что реально в действии):
```json
{
  "enabled": false,
  "topics": ["планы на выходные", "...", "…всего 24 темы по умолчанию"],
  "system_prompt": "Ты участвуешь в обычной переписке в Telegram…",
  "language": "ru",
  "tone": null
}
```
`queryKey: ["warmup-settings"]`.

### 2.2 `PUT /api/v1/warmup/settings` — сохранить настройки + master-toggle
Тело (все поля опциональны; пустые → сбрасываются в дефолт):
```json
{ "enabled": true, "topics": ["…"], "system_prompt": "…", "language": "ru", "tone": null }
```
Ответ: `{ "status": "saved", "settings": { …resolved как в GET… } }`.
Используется и для master-toggle (отправляй текущие настройки + новое `enabled`), и для кнопки «Сохранить настройки».

### 2.3 `GET /api/v1/warmup/pool` — все аккаунты воркспейса + статус в прогреве
Ответ:
```json
{
  "senders": [
    {
      "id": "uuid",
      "slug": "sender-79991234567",
      "name": "Иван",
      "phone": "+79991234567",
      "in_pool": true,
      "warmup_active": true,
      "enrolled_at": "2026-06-20T10:00:00+00:00",
      "enrolled_days": 9,
      "level": 3,
      "sent_today": 12,
      "restriction_status": "none",
      "restricted_until": null,
      "warmup_reason": null
    }
  ]
}
```
- `in_pool=false` → аккаунт не добавлен в прогрев (показывай кнопку «Добавить в прогрев»).
- `warmup_active` → греется сейчас или на паузе.
- `restriction_status ∈ {none, spam_limited, frozen, …}`; `warmup_reason` — готовый человекочитаемый текст причины (или `null`). **D-11/D-14: если `warmup_reason != null` — показывай его и красишь статус в `--danger`/`--danger-soft` (для `spam_limited` можно `--warning`), НИКОГДА не серым/молча.**
- `level` (1–5) + `sent_today` → строка интенсивности (см. §4 cap-mapping).
- `queryKey: ["warmup-pool"]`.

### 2.4 `GET /api/v1/warmup/stats` — мини-метрики
Ответ:
```json
{
  "active_accounts": 4,
  "active_sessions": 2,
  "messages_today": 37,
  "sessions_completed_today": 8,
  "accounts": [{ "slug": "...", "name": "...", "sent_today": 9, "enrolled_days": 9, "level": 3 }]
}
```
`queryKey: ["warmup-stats"]`. Мини-метрики строки: **«В прогреве»** (active_accounts), **«Активные сессии»** (active_sessions), **«Сообщений сегодня»** (messages_today), **«Сессий завершено сегодня»** (sessions_completed_today).

### 2.5 Действия (mutations) — после успеха инвалидируй `["warmup-pool"]` и `["warmup-stats"]`
| Действие | Запрос | Ответ | UX |
|---|---|---|---|
| Добавить в прогрев | `POST /api/v1/warmup/pool/{sender_id}` | `201 {status,sender_id,slug}` | тост «Аккаунт добавлен в прогрев» |
| Пауза/возобновить | `PATCH /api/v1/warmup/pool/{sender_id}/toggle` | `{sender_id, warmup_active}` | inline-toggle, без диалога, тост |
| Убрать из прогрева | `DELETE /api/v1/warmup/pool/{sender_id}` | `204` | **AlertDialog-подтверждение** (см. §5) |

### 2.6 (опционально, на будущее) История сессий
`GET /api/v1/warmup/sessions`, `GET /api/v1/warmup/sessions/{id}`, `GET /api/v1/warmup/sessions/{id}/messages` — для будущего экрана истории переписки. В MVP-вкладке не обязательны.

---

## 3. Раскладка вкладки (по образцу accounts.tsx)

```
┌ Topbar ───────────────────────────────────────────────┐
│ «Прогрев аккаунтов»                                     │
│ подзаголовок (см. копирайт)         [Switch master] [CTA «Включить прогрев»] │
└────────────────────────────────────────────────────────┘
.scroll (padding:24)
  ├ Мини-метрики (грид repeat(4,1fr), gap:12) — MiniMetric из accounts.tsx
  ├ .card «Пул прогрева» — таблица аккаунтов (FleetTable-паттерн)
  │    колонки: Аккаунт (name/slug/phone) · Статус (chip) · Уровень (Progress, read-only)
  │             · Сегодня (sent_today/cap) · Действия (kebab MoreHorizontal)
  └ .card «Настройки прогрева» — Textarea (темы, по строке) + Textarea (system_prompt)
       + кнопка «Сохранить настройки» (.btn--primary)
```

**Фокус-точка (визуальная иерархия):** master-CTA «Включить прогрев» + его состояние ON/OFF — единственный доминирующий `--tg-blue` `.btn--primary`, top-right Topbar. Метрики читаются вторыми, таблица — третьей. Всё остальное нейтральное.

**Уровень-бар:** shadcn `Progress` (`level/5*100`), read-only. Рядом строка-подпись интенсивности.

**AI-акцент `--ai-purple`:** только маленький чип «AI» на строке (контент генерится AI). НЕ использовать на кнопках/навигации.

---

## 4. Уровни и дневной лимит (cap)

`/pool` отдаёт `level` (1–5) и `sent_today`, но **не отдаёт сам cap** — маппинг делай на фронте:

| Уровень | Дни в прогреве | Дневной лимит (cap) |
|---|---|---|
| 1 | 0–3 | 10 |
| 2 | 3–7 | 25 |
| 3 | 7–14 | 50 |
| 4 | 14–21 | 80 |
| 5 | 21+ | 120 |

Строка интенсивности (read-only, D-09):
`Уровень {level} из 5 · {sent_today}/{cap} сообщений сегодня · в прогреве {enrolled_days} дн.`

Подпись про автоматику: `Интенсивность растёт автоматически по дням — ручная настройка отключена для безопасности`.

---

## 5. Состояния, копирайт и деструктивные действия

**Состояния:**
- Loading: скелет/спиннер как в accounts.tsx (`isLoading`).
- Error: `Не удалось загрузить прогрев. Обновите страницу или попробуйте позже.` + покажи `ApiError.message` под заголовком.
- Empty (пул пуст): heading `Пул прогрева пуст` + body `Добавьте Telegram-аккаунты в прогрев — они начнут безопасно переписываться между собой через AI. Прогрев изолирован от рассылок: он не трогает ваши кампании и не жжёт лимиты.` + CTA `Добавить аккаунт`.
- Empty (master выключен, но пул есть): `Прогрев выключен. Включите его, чтобы аккаунты начали набирать активность.`

**Копирайт (дословно):**
| Элемент | Текст |
|---|---|
| Nav / вкладка | `Прогрев` |
| Topbar title | `Прогрев аккаунтов` |
| Topbar subtitle | `Аккаунты переписываются между собой через AI — набирают активность без риска бана` |
| Master CTA (OFF→ON) | `Включить прогрев` |
| Master ON / OFF лейбл | `Прогрев включён` / `Прогрев выключен` |
| Добавить аккаунт | `Добавить в прогрев` |
| Пауза / убрать | `Поставить на паузу` / `Убрать из прогрева` |
| Расписание (read-only) | `Прогрев работает 09:00–20:00 МСК` |
| Combine-note | `Аккаунт может одновременно греться и работать в кампании` |
| restriction reason | приходит готовым в `warmup_reason` — показывай как есть |
| Settings save | CTA `Сохранить настройки` · success-тост `Настройки прогрева сохранены` · hint `По умолчанию используются 24 русскоязычные темы` |

**Деструктивные действия — shadcn `AlertDialog`:**
1. Выключить master:
   Title `Выключить прогрев?` · Body `Все аккаунты перестанут набирать активность, пока вы снова не включите прогрев.` · Confirm `Выключить прогрев` (`--danger`) · Cancel `Отмена`.
2. Убрать аккаунт (`DELETE /pool/{id}`):
   Title `Убрать аккаунт из прогрева?` · Body `Аккаунт перестанет переписываться с другими. Его историю прогрева это не удалит.` · Confirm `Убрать аккаунт` (`--danger`) · Cancel `Отмена`.
   (Пауза через `PATCH …/toggle` — обратима, БЕЗ диалога: inline-toggle + тост.)

---

## 6. Компоненты (всё уже есть в `src/components/ui/`)

`Switch` (master + per-account), `AlertDialog` (деструктив), `Progress` (уровень), `Textarea`/`Input` (настройки), `Tooltip` (инфо про ограничения), `Table`/`Card`/`Badge`/`Button`. Тосты — `sonner` `toast`. Иконки lucide: `Flame`/`Activity` (nav), `Power`/`Pause`, `Trash2`, `ShieldAlert` (ограничен), `Bot`/`Sparkles` (AI). Никаких сторонних registry — `components.json.registries = {}`.

---

## 7. Критерии приёмки

- [ ] Вкладка `Прогрев` в сайдбаре, роут `/warmup` открывается.
- [ ] Master-toggle включает/выключает прогрев через `PUT /settings` (выключение — через AlertDialog).
- [ ] Мини-метрики из `/stats`; таблица пула из `/pool`.
- [ ] Add / pause(toggle) / remove работают, после мутации список и метрики обновляются.
- [ ] Ограниченный аккаунт (`warmup_reason != null`) показан с `--danger`/`--warning` + текстом причины, не серым.
- [ ] Строка интенсивности и Progress по уровню (read-only); ручной настройки интенсивности НЕТ.
- [ ] Settings-карточка сохраняет темы + system_prompt через `PUT /settings`.
- [ ] Вся копия — русская, дизайн-токены/компоненты переиспользованы, light-only.
- [ ] `bun run build` проходит без ошибок типов.
```
