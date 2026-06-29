# Phase 15: Account Warmup via Inter-Account AI Chat - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-29
**Phase:** 15-account-warmup-via-inter-account-ai-chat
**Areas discussed:** Изоляция от аутрича, Workspace-scoping API, Контролы UI-вкладки, Пул и совмещение с кампаниями, (доп.) Ограничения/контент/статус

---

## Изоляция от аутрича

### Механизм internal-детекции
| Option | Description | Selected |
|--------|-------------|----------|
| «Свой со своим» = internal | telegram_id ∈ senders workspace, не зависит от phone/пула; листенер дропает до AI и не пишет conversations | ✓ |
| Закрыть phone-дыру в кэше | Оставить warmup_pool cache-фильтры, убрать phone-зависимость | |
| Отдельный listener-контур | warmup-аккаунты не подписывают хендлеры основного листенера | |

### Суммарная нагрузка / лимиты
| Option | Description | Selected |
|--------|-------------|----------|
| Общий суточный потолок | warmup учитывает отправки кампании, снижает свой объём | |
| Независимые лимиты | warmup-лимиты и лимиты кампаний раздельны | ✓ |

### Где живут warmup-диалоги
| Option | Description | Selected |
|--------|-------------|----------|
| Только warmup_messages | листенер не создаёт строк в conversations/messages для internal | ✓ |
| Флаг в conversations | писать, но помечать is_internal и исключать на чтении | |

### Гард изоляции
| Option | Description | Selected |
|--------|-------------|----------|
| Да, регресс-тест | доказывает: internal не триггерит AI и не в метриках | ✓ |
| На усмотрение planner | | |

**Notes:** Прямой урок инцидента 2026-06-23/24 (`dashboard-analytics-warmup-pollution.md`) — phone-фильтр течёт при `phone="unknown"`. Решение делает изоляцию детерминированной и доказуемой.

---

## Workspace-scoping API

### Объём рерайта
| Option | Description | Selected |
|--------|-------------|----------|
| AuthDep + workspace scope | рерайт эндпоинтов, форма ответов сохраняется | ✓ |
| Полный редизайн API | новые REST-ресурсы/имена | |

### Старт/стоп на глобальном singleton-воркере
| Option | Description | Selected |
|--------|-------------|----------|
| Флаг в БД per-workspace | warmup_enabled, воркер honors, процесс не перезапускается | ✓ |
| Только per-account toggle | без master-переключателя workspace | |

---

## Контролы UI-вкладки

### Гранулярность
| Option | Description | Selected |
|--------|-------------|----------|
| Master + per-account | master switch workspace + per-account toggle пула | ✓ |
| Только master | один переключатель на workspace | |

### Расписание
| Option | Description | Selected |
|--------|-------------|----------|
| Оставить 09–20 МСК | дефолтное окно, без UI | ✓ |
| Настраиваемое окно/TZ | per-workspace окно + таймзона | |

### Интенсивность
| Option | Description | Selected |
|--------|-------------|----------|
| Авто по дням + показ уровня | безопасный авто-рамп, UI read-only | ✓ |
| Пресет slow/normal/fast | множитель к авто-уровням | |
| Ручной уровень | клиент выбирает 1–5 per-аккаунт | |

---

## Пул и совмещение с кампаниями

### Совмещение
| Option | Description | Selected |
|--------|-------------|----------|
| Да, разрешить | греться и быть в активной кампании одновременно | ✓ |
| Нет, взаимоисключающе | авто-пауза прогрева при кампании | |

### Авто-зачисление новых аккаунтов
| Option | Description | Selected |
|--------|-------------|----------|
| Опционально (ручное) | новый аккаунт НЕ в пуле по умолчанию | ✓ |
| Авто-зачислять | новый аккаунт сразу в пуле | |

---

## Доп. развилки (второй раунд)

### Дыра: restriction_status в выборке пула
| Option | Description | Selected |
|--------|-------------|----------|
| Исключать ограниченные | пропускать restriction_status != 'none' / restricted_until future | ✓ |
| Оставить как есть | не трогать выборку | |

### Контент прогрева
| Option | Description | Selected |
|--------|-------------|----------|
| Оставить захардкоженным | 24 RU-темы + промпт | |
| Настраиваемый per-workspace | темы/язык/тон редактируемы, дефолт = текущие RU | ✓ |

### Per-account статус в вкладке
| Option | Description | Selected |
|--------|-------------|----------|
| Расширенный статус | + restriction_status + последняя ошибка/активность | ✓ |
| Как сейчас | level/sent_today/enrolled_days/active | |

---

## Claude's Discretion

- Схема хранения `warmup_settings` workspace (таблица/строка/JSONB).
- Форма и имена control-эндпоинтов в пределах паттерна Phase 3/4/5.
- Точка установки internal-short-circuit в `listener.py` (до буфера/дебаунса).
- Набор полей и формат «последней ошибки/активности» прогрева.

## Deferred Ideas

- Настраиваемое окно расписания + таймзона прогрева per-workspace (backlog).
- Пресет интенсивности slow/normal/fast или ручной уровень.
- Observability / алерты на здоровье прогрева.
- Auto-pause прогрева при активной кампании (вернуться при риске суммарного объёма).
- Многоязычный UI самой вкладки.
