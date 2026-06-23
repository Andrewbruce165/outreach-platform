# Phase 8: Pool Management and Even Distribution - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-23
**Phase:** 08-pool-management-and-even-distribution
**Areas discussed:** Attach/detach на running, Судьба работы при detach, Ребаланс при attach на ходу, UX мультиселекта во фронте

---

## Attach scope (Q1)

| Option | Description | Selected |
|--------|-------------|----------|
| running тоже (gated) | Разрешить на draft/paused/running; attach gated sender-lock+workspace, detach gated min-pool | ✓ |
| только draft/paused | На running менять пул нельзя, нужна пауза | |

**User's choice:** running тоже (gated)
**Notes:** Совпадает с proposal §Open decisions B1 ("likely yes, gated by lock checks").

---

## Detach семантика (Q2)

| Option | Description | Selected |
|--------|-------------|----------|
| Guard, без реассайна | Блокировать detach (409) если есть неотправленный cold pending; активные диалоги продолжают; авто-реассайн → Phase 9 | ✓ |
| Реассайн при detach | Перекидывать cold un-contacted pending на пул через get_or_assign_sender | |
| Hard detach | Просто убрать из пула; pending зависает | |

**User's choice:** Guard, без реассайна
**Notes:** Чёткая граница с Phase 9 (failover). Implication: detach живого sender'а на running обычно потребует pause/ожидания — осознанный trade-off.

---

## Ребаланс при attach (Q3)

| Option | Description | Selected |
|--------|-------------|----------|
| Лёгкий ребаланс | Перенести часть неотправленных cold pending на новый sender, выровнять до least-loaded | ✓ |
| Без ребаланса | Новый sender берёт только будущие enqueue; при заэнкьюенной папке простаивает | |

**User's choice:** Лёгкий ребаланс
**Notes:** Назначения sticky на enqueue → least-loaded сам не догрузит новый аккаунт. Поэтому ребаланс не «опционален» как в proposal, а нужен. Точный алгоритм — на plan.

---

## Frontend (Q4)

| Option | Description | Selected |
|--------|-------------|----------|
| Панель на детали кампании | Отдельный блок Senders/Пул: мультиселект, add/remove, показ locked-аккаунтов; работает для draft и running | ✓ |
| Только в визарде | Выбор senders только при создании/редактировании draft; running — только через API | |

**User's choice:** Панель на детали кампании
**Notes:** Репо aimly-tg-outreach. Строится на существующем attached_senders[].locked_by_campaign_name.

---

## Claude's Discretion

- Точные коды/тела ошибок (envelope) — по стилю campaigns.py.
- Алгоритм лёгкого ребаланса (порог, cap, переиспользование _pick_least_loaded).
- Раскладка/компоненты фронт-панели — по дизайн-языку aimly-tg-outreach.

## Deferred Ideas

- Авто-реассайн cold backlog при detach/фризе → Phase 9.
- Здоровье пула + бейдж → Phase 10.
- Cross-campaign load awareness → non-goal блока.
