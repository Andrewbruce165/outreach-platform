---
title: Checker false-negatives — диагноз, доказательства, калибровка
date: 2026-06-26
context: Расследование во время /gsd-explore по вопросу «зачекаются ли все 14k или только ~200». Привело к Phase 14 (Reliable Contact Resolution).
---

# Checker false-negatives (2026-06-26)

## TL;DR

Единственный checker-аккаунт `sender-8428118140` получил **теневое ограничение
Telegram contacts-API** за объёмный resolve и начал систематически возвращать
ложноотрицательные («номера нет в Telegram») на **реальных, достижимых** номерах.
Рапортовал **2.5%** живых (53 registered / 2148 проверенных) против настоящих
**~26%** в целом и **~50%+** среди мобильных. Занижение в ~15–20 раз → тысячи
живых лидов молча списывались в мусор.

## Как воспроизвели / доказательства

1. **Конкретный кейс.** `+79519152502` (Алексей, tg_id 1419735849) — система
   пометила `not_registered`. Юзер открывает `t.me/+79519152502` → чат открывается
   (реальный аккаунт). Синхронизации адресной книги нет, в контактах нет, не писал.
2. **Живой сравнительный тест** (`scratchpad/diag_resolve.py`): тот же номер, тот же
   момент —
   - `sender-8428118140` (checker): `resolvePhone` → PhoneNotOccupied, `importContacts` → пусто.
   - `sender-8017533134` (RU) и `sender-7979031303` (US): **оба метода резолвят** id 1419735849.
   → Дело не в приватности номера и не в методе (метод-асимметрии нет — `importContacts`
   НЕ достаёт там, где `resolvePhone` пусто). Разница только в **поведенческом профиле
   аккаунта**: checker весь день льёт bulk-resolve с низким hit-rate → попал под throttle.
3. **Кривая деградации** (`contacts_cache`, по дням): 2026-06-18 hit-rate 6.7% (50/746)
   → 2026-06-26 **0.07%** (1/1407). Подпись накопительного штрафа, не разовый глюк.
4. **Выборка ложноотрицательных** (`scratchpad/sample_fn.py`, n=15): 2/15 помеченных
   `not_registered` оказались живыми (мобильные +79204603399, +79107323222).

## Два режима троттла (важно для дизайна)

| Тип | Триггер | Последствие | Восстановление |
|---|---|---|---|
| Мягкий burst | ~45–50 быстрых резолвов подряд (темп 2–3с) | редкие ложные «нет» | минуты |
| Жёсткий shadow-ban | тысячи/день изо дня в день | почти всё → ложное «нет» (0.07%) | дни |

**Калибровка burst** (`scratchpad/known_live_probe.py`, 49 заведомо-живых через
свежий `sender-8364639216`): **48/49 резолвнулись live**, первый ложный «нет» — на
позиции **49**. Промах подтверждён как наш троттл: `+79885491680` (Владимир) резолвится
с другого аккаунта. Вывод: онсет ~45–50, размытый/стохастичный → **единого «магического
капа» нет, нужен health-probe**.

## Истинная доля живых в списке

`scratchpad/sample_true_rate.py`, 70 случайных из `pending`: **26% живых в целом**,
**~50%+ среди мобильных** (стационарные +73/+74/+78 корректно отсутствуют). Битый
чекер: 2.5%. По 14 489 контактам: реально достижимых ~4000+, чекер отдал бы ~360 →
**~3600+ живых лидов утекали молча**.

## Часть 1 — выполнено вручную 2026-06-26

- Чекер `sender-8428118140` на паузе: `auth_status='restricted'`,
  `restriction_status='spam_limited'`, `lifecycle_status='paused'`, `restricted_until='2030-01-01'`.
  Откат: `UPDATE senders SET auth_status='ok', restriction_status='none', lifecycle_status='active', restricted_until=NULL WHERE slug='sender-8428118140';`
- Удалено **2216** строк `contacts_cache` с `is_registered=false` от этого чекера (registered/true positives оставлены).
- **2110** контактов `not_registered` → `pending` (`tg_checked_at=NULL`). Итог contacts: 14489 pending, 53 registered, 0 not_registered.
- Активных чекеров больше нет → проверка остановлена (безопасное состояние: лучше «неизвестно», чем ложное «нет»). Двинуть 14k нельзя до Phase 14.

## Корневая дыра в коде

`app/services/contact_check_worker.py` выбирает чекер по `role='checker' AND
auth_status='ok'` — НЕ смотрит `restriction_status`/`lifecycle_status`. Поэтому
семантически правильная пометка (`spam_limited`/`paused`) воркер бы не остановила;
пришлось гасить через `auth_status`. Фикс — RESV-05.

## Артефакты

- Скрипты: `scratchpad/diag_resolve.py`, `sample_fn.py`, `sample_true_rate.py`, `known_live_probe.py`
- Health-probe контрольный набор (49 заведомо-живых): `.planning/phases/14-reliable-contact-resolution/control-set-known-live.txt`
- Папка-источник: «Barter_список пещивиков Ромы» `folder_id 4ecdde17-f454-4a1b-b4ba-732fd6b9449f`

## Часть 2 — Live-smoke провал активации (2026-06-26, Phase 14 / 14-04)

После мёржа волн 1–3 (768 тестов GREEN) и деплоя (`docker compose up -d --build api`,
миграция 034 применена, `probe_checker`/`resolve_phone_with_fallback`/`tg_probe_state`
в рантайме) активировали два «здоровых» запаркованных чекера
(`sender-7979031303`, `sender-8364639216`, guard `restriction_status='none'`).

**Результат: тот же throttle-сигнатур.** За первый бёрст воркер дал
`checked=20..30 reg=0 not_reg=20..30 flood=True` по мобильным `+79…` —
**0% registered** (калибровка ждёт ~50% на мобильных, baseline 48/49). То есть
оба «здоровых» чекера тоже отдают FloodWait/пустой resolve по телефону.

**Дыра в коде (gap для gap-closure):** при `flood=True` воркер всё равно
финализировал пустые результаты как `tg_status='not_registered'` с
`tg_confidence='high'`, `tg_probe_state='clean'` — control-probe НЕ выставил ни
одного `sender_restriction_events`, suspect-rollback НЕ сработал. Throttle/flood-
ответ нельзя трактовать как «не зарегистрирован» и нельзя писать high-confidence.

**Откат (prod восстановлен в baseline):** оба чекера ре-паркнуты (0 активных);
api остановлен чтобы заглушить in-memory воркер; откат `UPDATE 50` строк →
`pending` (+ обнуление `tg_*`), `DELETE 50` ложных `contacts_cache`; 49 control
не тронуты. Итог contacts: **not_registered=5 / pending=14484 / registered=53**,
provenance-строк 0. api перезапущен, воркер idle (все чекеры parked).

**Операционная заметка:** `docker exec <c> psql … <<'SQL'` без `-i` НЕ доставляет
heredoc в stdin — UPDATE'ы прошли как no-op (счётчики не менялись, пока воркер
параллельно докидывал ложь). Правильно: `psql -c "…"` или `docker exec -i`.

**Вывод:** Phase 14 НЕ завершён. Нужен gap-closure: (1) flood/throttle-aware
финализация (не писать not_registered/ high-confidence при flood; помечать
чекер restricted и выводить из ротации); (2) разобраться, throttle ли это всего
пула (возможно нужен длинный cooldown или резолв только по @username, см. выше).
