# Phase 17: Sender-side resolve ladder - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in 17-CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-30
**Phase:** 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
**Areas discussed:** Лестница резолва, Country-gate, Захват @username, Доверие + кэш, Block/report-rate метрика

---

## Area selection

Presented 4 gray areas (multiSelect). User selected ALL four: Лестница резолва, Country-gate, Захват @username, Доверие + кэш. Block/report-rate (5th open sub-decision) added at the closing gate by user choice.

---

## Лестница резолва на отправителе

### Q: Tier-3 механика — как резолвить номер без захваченного username?
| Option | Description | Selected |
|--------|-------------|----------|
| ResolvePhone → Import-фолбэк | Лёгкий ResolvePhone первым, import только когда пусто (рекомендовано) | |
| Import-only | Сразу ImportContacts, без ResolvePhone | ✓ |

**User's choice:** Import-only → собственный ResolvePhone отправителя убирается полностью (источник 22 провалов). Совпадает с tier-списком ROADMAP (cache→username→import). → D-01, D-02

### Q: Когда пытаться ImportContacts-фолбэк?
| Option | Description | Selected |
|--------|-------------|----------|
| registered ИЛИ suspect-вердикт | import на registered + ненадёжных вердиктах (рекомендовано) | |
| Только если registered | import только на явном registered | ✓ |
| Всегда при пустом ResolvePhone | игнорировать вердикт | |

**User's choice:** Только если registered. → D-03 (suspect-кейс уводится в re-check на слое кэша, D-12, а не на гейте)

### Q: Чистка адресной книги после ImportContacts?
| Option | Description | Selected |
|--------|-------------|----------|
| DeleteContacts после отправки | как у чекера (рекомендовано) | |
| Оставлять как есть | не удалять, книга копится | ✓ |

**User's choice:** Оставлять как есть → D-04 (принят рост книги; периодическая чистка → Deferred).

---

## Country-gate

### Q: К кому применять country-gate?
| Option | Description | Selected |
|--------|-------------|----------|
| К чекеру и отправителю | гейт обоих плеч (рекомендовано) | |
| Только к чекеру | | |
| Только к отправителю | | |

**User's choice (free text):** «пока не применяем country-gate. это наша гипотеза на холодном аккаунте. нужно удалить в памяти что это факт и зафиксировать пока что это гипотеза и потом проверить так ли это»

### Q: Правило сопоставления страны? → **N/A** (не делаем — «фиксируем как гипотезу»)
### Q: Поведение при несовпадении? → **N/A** («пока ничего не делаем»)

**Outcome:** D-10 — country-gate НЕ реализуем; гипотеза к проверке. **Action taken mid-discussion:** memory `project-us-senders-cannot-resolve-ru-phones` reclassified fact→hypothesis (confounded with cold/throttle); MEMORY.md index updated.

---

## Захват @username

### Q: Где хранить захваченный @username?
| Option | Description | Selected |
|--------|-------------|----------|
| Дурабельно на contacts + кэш | отдельная колонка + cache, не затирает CSV-username (рекомендовано) | ✓ |
| В contacts.username (reuse) | смешивает provenance | |
| Только в contacts_cache | теряется после TTL 7д | |

**User's choice:** Дурабельно на contacts + кэш. → D-07

### Q: Когда ResolveUsername падает (username протух)?
| Option | Description | Selected |
|--------|-------------|----------|
| Фолбэк на import-tier | не финализировать not_registered (рекомендовано) | ✓ |
| Финализировать not_registered | пропустить | |

**User's choice:** Фолбэк на import-tier. → D-09

---

## Доверие к вердикту + отравленный кэш

### Q: Import-гейт «только если registered» — как обходиться с ненадёжными вердиктами?
| Option | Description | Selected |
|--------|-------------|----------|
| Опереться на confidence (Phase 14 D-09) | suspect → re-check на гейте; требует завершённого Phase 14 (рекомендовано) | |
| Самодостаточно в Phase 17 | своя проверка надёжности на гейте | |
| Доверять любому вердикту | гейт простой, без confidence-ветки | ✓ |

**User's choice:** Доверять любому вердикту → D-11 (гейт простой). Композиция с D-12 ниже: confidence-обработка уходит на слой чтения кэша, не на гейт. → D-13

### Q: Отравленный кэш (cross-contamination) — ROADMAP «кэш не чистим»?
| Option | Description | Selected |
|--------|-------------|----------|
| Confidence-gated ЧТЕНИЕ | не доверять low-confidence not_registered на чтении → live-перерезолв; не удалять (рекомендовано) | ✓ |
| Строго ничего (purge — ops) | соблюсти роудмап буквально | |
| Purge в scope | расширение scope | |

**User's choice:** Confidence-gated ЧТЕНИЕ. → D-12

### Q: Инцидентные контакты (22 Barter-ВЭД + 176 Igor) — в фазе или ops?
| Option | Description | Selected |
|--------|-------------|----------|
| Ops после деплоя | фаза строит механизм (рекомендовано) | ✓ |
| В рамках фазы | явный акцептанс с живым пулом | |

**User's choice:** Ops после деплоя. → D-14

---

## Block/report-rate метрика

### Q: Обсудить или отложить? → **Обсудить сейчас**

### Q: Что именно трекаем?
| Option | Description | Selected |
|--------|-------------|----------|
| Block-сигнал на send + read-метрика | захват USER_IS_BLOCKED + rate поверх него и sender_restriction_events (рекомендовано) | ✓ |
| Только block-сигнал (без rate) | минимум | |
| Полная метрика + вывод в UI/analytics | пересекается с observability-фазами | |

**User's choice:** Block-сигнал на send + read-метрика. → D-15

### Q: Control-loop?
| Option | Description | Selected |
|--------|-------------|----------|
| Только хранить (read-only) | алерты/auto-pause — отдельная фаза (рекомендовано) | ✓ |
| Auto-pause при высоком rate | контрольная петля, риск ложных срабатываний | |

**User's choice:** Только хранить (read-only). → D-16

---

## Claude's Discretion

- Схема хранения confidence на слое кэша (D-12), имя колонки captured username (D-07), место block-событий (D-15), точный класс ошибки блока + форма rate (D-15), порог «suspect» на чтении (D-12).

## Deferred Ideas

- Проверка гипотезы country-gate (изоляционный тест) — D-10.
- Реквью инцидентных контактов (ops) — D-14.
- Периодическая чистка адресной книги отправителей — следствие D-04.
- Block-rate alerting/auto-pause/UI — D-16.
- Purge отравленного кэша — вне scope (ROADMAP).
