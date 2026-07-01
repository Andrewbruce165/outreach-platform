---
phase: quick-260701-px7
status: complete
subsystem: telegram / proxy
tags: [proxy, socks5, decodo, senders, ops]
type: ops-data-change
provides:
  - "Все 16 senders.proxy заполнены Decodo ISP socks5 (порты 10001–10016)"
affects:
  - "prod DB: senders.proxy (16 rows)"
  - "runtime: api + listener переподключены через прокси"
key-files:
  created:
    - .planning/quick/260701-px7-attach-decodo-proxies-all-senders/260701-px7-PLAN.md
    - .planning/quick/260701-px7-attach-decodo-proxies-all-senders/260701-px7-SUMMARY.md
  modified: []
migrations: []
tests: "n/a (ops-задача, изменение данных в проде)"
---

# Summary — 260701-px7

Привязал обновлённый пул Decodo ISP socks5-прокси ко всем 16 аккаунтам `senders`.

## Сделано

- **Бэкап:** `/root/backups/tg-outreach/outreach_20260701_114445.sql.gz`.
- **UPDATE 16:** каждому sender — уникальный порт Decodo (socks5, `isp.decodo.com`),
  детерминированный маппинг по `created_at`:

  | port | slug | аккаунт |
  |------|------|---------|
  | 10001 | sender-8428118140 | us-account-1 |
  | 10002 | sender-8218483045 | ru-account-1 |
  | 10003 | sender-8526195634 | ru-account-2 |
  | 10004 | sender-8349156575 | us-account-2 |
  | 10005 | sender-8071536685 | us-account-3 |
  | 10006 | sender-8537405794 | us-account-4 (paused) |
  | 10007 | sender-8525079460 | us-account-5 |
  | 10008 | sender-8539506204 | ru-account-3 |
  | 10009 | sender-8298649227 | ru-account-4 (paused) |
  | 10010 | sender-8514716383 | ru-account-5 |
  | 10011 | sender-8017533134 | ru-account-6 (checker, paused) |
  | 10012 | sender-7979031303 | us-account-6 (checker, paused) |
  | 10013 | sender-8364639216 | ru-account-7 (checker, paused) |
  | 10014 | sender-7375001431 | barter (Игорь) |
  | 10015 | sender-7867638054 | ca-account-1 |
  | 10016 | sender-8503645757 | ca-account-2 |

  Порты 10017–10020 — резерв.
- **Рестарт** `api` + `listener`.

## Верификация

- `senders.proxy` у всех 16 = socks5 / `isp.decodo.com` / 10001–10016 (SELECT подтвердил).
- Пре-чек: все 20 портов достают Telegram DC2, у каждого уникальный внешний IP.
- **Ground truth:** установленные TCP-соединения листенера идут к Decodo `185.111.111.x`
  на портах 100xx (10001,10003,10004,10005,10007,10010,10014,10015,10016 — активные
  аккаунты), **ноль прямых коннектов к `149.154.x`**. Листенер активно принимает
  апдейты Telegram → прокси-туннель рабочий. Запаркованные (paused/spam_limited) не
  подключены — ожидаемо.

## Заметки

- Внешние IP Decodo — **US ISP**; RU (+7) аккаунты логинятся с US-IP. Осознанно принято
  (единственный рабочий пул; ось доверия — прогрев/здоровье, не страна — Phase 17 D-10).
- Предыдущий РФ-пул отвергнут: не пропускал Telegram (см. PLAN «Контекст»).
- `proxy_pool`-таблица не наполнялась (не требуется для рантайма).

## Revert

`UPDATE senders SET proxy = 'null'::jsonb;` + рестарт, либо restore из бэкапа выше.
