# Phase 9: Cold-Contact Failover - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-24
**Phase:** 9-cold-contact-failover
**Areas discussed:** Триггер failover, Предикат safe-to-failover, Анти-dogpile, Hard-freeze и fallback

---

## Триггер failover (когда/где)

| Option | Description | Selected |
|--------|-------------|----------|
| Inline-хелпер при фризе | Один shared `failover_cold_backlog(sender_id)`, вызывается сразу после флага restricted + паузы pending в обоих путях (queue.py PEER_FLOOD + listener antispam). Нулевая задержка, без нового воркера, DRY. Минус: не подхватит senders, замёрзших до деплоя. | ✓ |
| Периодический sweep | Отдельный воркер/хук в reconcile-тике периодически ищет pending на restricted и переносит. Ловит ВСЕ случаи, идемпотентен. Минус: задержка = интервал тика, новый компонент. | |
| Гибрид | Inline как основной + лёгкий safety-net sweep. Больше кода, максимальная надёжность. | |

**User's choice:** Inline-хелпер при фризе (рекомендованный).
**Notes:** Осознанно без safety-net sweep — лишний движущийся компонент. Влияет на выбор cap-стратегии (см. Анти-dogpile).

---

## Предикат «safe-to-failover» (что переносим)

| Option | Description | Selected |
|--------|-------------|----------|
| Строгий: ноль истории | pending + нет sent/processing по (campaign, phone) + нет Conversation по (workspace, phone). | |
| Шире: диалог без исходящих | Как строгий, но также переносить если Conversation есть, но в ней 0 сообщений (создана, но пустая). | ✓ |
| Простой: всё pending | Довериться инварианту «pending = холодный первый контакт» и переносить все pending без доп-проверок. | |

**User's choice:** Шире: диалог без исходящих.
**Notes:** Пустой Conversation = всё ещё холодный контакт, безопасно переносить. Континуити ломается только при реальном обмене сообщениями. `messages_log` (message_type incoming/outgoing) — якорь проверки «0 сообщений».

---

## Анти-dogpile / распределение

| Option | Description | Selected |
|--------|-------------|----------|
| Per-item least-loaded, без cap | Per-item get_or_assign_sender → ровный спред; scheduled_at=NOW; CCA синхронно. Cap не нужен — rate-limiter (4/20/150) тротлит на отправке. Нет орфанов. | ✓ |
| + per-receiver day-headroom cap | Перестать грузить приёмника при исчерпании 150/день; овэрфлоу paused. При inline-триггере → орфаны, нужен sweep. | |
| + жёсткий batch cap N | Переносить ≤ N за событие, остальное paused. Тот же орфан-риск. | |

**User's choice:** Per-item least-loaded, без cap (рекомендованный).
**Notes:** Согласовано с inline-триггером — никаких орфанов. Существующий per-sender rate-limiter — естественный тротл.

---

## Hard-freeze и fallback

### Hard-freeze (объём срабатывания)

| Option | Description | Selected |
|--------|-------------|----------|
| Да, оба | Хелпер срабатывает на spam_limited И frozen/banned. С забаненного аккаунта cold backlog вообще никогда не уйдёт → перенос важнее. | ✓ |
| Только soft | Failover только для spam_limited; frozen/banned ждёт appeal (текущее поведение). | |

**User's choice:** Да, оба.

### Fallback (нет здоровых приёмников)

| Option | Description | Selected |
|--------|-------------|----------|
| Оставить paused | get_or_assign_sender не нашёл кандидата → строки остаются paused на замёрзшем (ждут reconcile-resume). Failover best-effort. Логировать «некуда переносить». | ✓ |
| Лог + флаг «застряло» | Как выше + пометить кампанию/событие для видимости (перекликается с Phase 10). Больше работы сейчас. | |

**User's choice:** Оставить paused (рекомендованный).
**Notes:** Failover best-effort — ничего не теряется и не падает в failed. Видимость застрявшего → Phase 10.

---

## Claude's Discretion

- Точная SQL-форма предиката «0 сообщений» (по phone vs conversation_id, учёт message_type).
- Транзакционные границы + идемпотентность хелпера (по образцу Phase 8 rebalance, FOR UPDATE SKIP LOCKED).
- Имя/местоположение `failover_cold_backlog` (новый failover.py vs rotation.py/queue.py).
- Формат и уровень лог-сообщений.
- Деривация требований фазы (FAIL-0x) на этапе research/plan.

## Deferred Ideas

- Safety-net sweep (для senders замёрзших до деплоя / осевшего позже pending) — отвергнут в этой фазе.
- Per-receiver day-headroom cap / жёсткий batch-cap — отвергнуты (rate-limiter тротлит, cap плодит орфанов).
- Видимость «cold backlog застрял — нет здоровых аккаунтов» (флаг/бейдж) → Phase 10 (Pool Visibility).
</content>
