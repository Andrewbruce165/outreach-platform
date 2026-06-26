# Service Conflict Investigation — Warmup Process Grabbing Aimly Sessions

**Date:** 2026-06-24  
**Investigator:** Claude (gsd-debugger)

---

## Executive Summary

The `telegram-api` project (old production, `/root/apps/telegram-api/`) has a warmup worker that is **actively connecting to and sending messages through the same 13 Telegram accounts** that the aimly project (`/root/apps/aimly/tg-outreach/`) manages. Both databases contain identical phone numbers for all 13 senders. The warmup process in `telegram-api` is running live — it fired 3 sessions in the last few hours and has 6 active sessions with upcoming fire times.

The aimly listener started 25 minutes ago and connected all 12 active senders. Both listeners are holding open MTProto connections to the same accounts simultaneously.

---

## Hypothesis → Test → Evidence

### H1: A warmup *container* is running from one of the 3 projects
**Test:** `docker ps | grep -i warmup`  
**Result:** No warmup container found.  
**Conclusion:** ELIMINATED. Warmup is not a separate container — it runs as an async background task inside the API process (`main.py` starts `warmup_worker.start()` at lifespan startup).

### H2: The warmup worker runs inside the API container of the old `telegram-api` project
**Test:** Read `/root/apps/telegram-api/app/main.py` and `/root/apps/telegram-api/app/services/warmup.py`
```
# main.py (telegram-api)
from app.services.warmup import warmup_worker
warmup_worker.start()   # line 36, lifespan startup
```
**Result:** Confirmed. `telegram-api` container starts a `WarmupWorker` asyncio task every time it boots.

**Test:** Check logs for recent activity  
```
2026-06-24 15:26:45 — Warmup [logist6 → blah] (3/6)
2026-06-24 15:29:22 — Warmup [logist_9 → checker_5] (5/7)
2026-06-24 15:45:28 — Warmup [sender8 → logist_10] (7/9)
```
**Result:** The warmup worker fired 3 times in the last 2 hours. It is live.

### H3: The aimly project also starts a warmup worker
**Test:** Read `/root/apps/aimly/tg-outreach/app/main.py`
```python
from app.services.warmup import warmup_worker
warmup_worker.start()   # line 54
```
**Result:** Yes — aimly also starts a warmup worker. However, `warmup_pool` in the aimly DB has **0 active entries**, so the aimly warmup worker currently does nothing (no pairs to match). Not the conflicting process.

### H4: Session files overlap between directories
**Test:** `find /root/apps/telegram-api /root/apps/outreach-platform /root/apps/aimly/tg-outreach -name "*.session"`  
**Result:** Zero `.session` files found anywhere.  
**Conclusion:** ELIMINATED. Both projects use Telethon `StringSession` (session string stored encrypted in PostgreSQL), not filesystem SQLite session files.

### H5: Docker volumes are shared between projects
**Test:** Inspect mounts for all relevant containers  
```
outreach-platform-api  → no mounts
outreach-platform-listener → no mounts
telegram-api           → /var/run/docker.sock only
telegram-listener      → no mounts
outreach-platform-db   → tg-outreach_postgres_data (named, isolated)
telegram-api-db        → telegram-api_postgres_data (named, isolated)
```
**Result:** No shared volumes. Each project has its own isolated named volume.  
**Conclusion:** ELIMINATED. The conflict is not at the volume level.

### H6: The two projects share the same physical Telegram accounts
**Test:** Compare phone numbers in both databases

| telegram-api DB (slug) | aimly DB (slug) | Phone |
|---|---|---|
| sender_1 | sender-8428118140 | +16018728956 |
| checker_6 | sender-7979031303 | +16166369072 |
| checker_5 | sender-8525079460 | +16167476576 |
| blah | sender-8537405794 | +16184955130 |
| checker_3 | sender-8071536685 | +16184955131 |
| checker_4 | sender-8349156575 | +16185468137 |
| logist6 | sender-8017533134 | +79584148809 |
| sender8 | sender-8364639216 | +79586008602 |
| logist_10 | sender-8514716383 | +79586037351 |
| sender_7 | sender-8526195634 | +79587859646 |
| logist_8 | sender-8539506204 | +79587860771 |
| logist_9 | sender-8298649227 | +79587863152 |
| sender_6 | sender-8218483045 | +79587869196 |

**Result:** 13/13 phone numbers are IDENTICAL. All senders are the same physical Telegram accounts, registered in two separate databases under different slugs.

**Conclusion: ROOT CAUSE CONFIRMED.**

---

## Root Cause

The `telegram-api` project (old AGS Foods internal tool, running as `telegram-api` container) has **13 Telegram accounts registered in its database** (`telegram_followup` DB). The aimly project was bootstrapped by copying/migrating these same accounts — all 13 are re-registered in the aimly DB (`outreach_platform`).

Both projects run services that create Telethon `StringSession` connections to these accounts **simultaneously**:

1. **`telegram-api` container** — warmup worker is active with 6 live sessions, fired at 15:26, 15:29, 15:45 today. Uses `telegram-api` DB session strings.

2. **`outreach-platform-listener`** container — connects all 12 active senders at startup (started 25 min ago). Uses aimly DB session strings.

3. **`telegram-listener`** (old AGS) — connects the same accounts for incoming message monitoring. Uses `telegram-api` DB session strings.

**Result:** Up to 3 simultaneous Telethon MTProto connections per Telegram account:
- One from `telegram-listener` (persistent, monitoring)
- One from `telegram-api` warmup worker (periodic, sending)
- One from `outreach-platform-listener` (persistent, monitoring)

Telegram's MTProto protocol allows multiple concurrent sessions from the same account on the same device type. However, when two concurrent sessions both receive updates, they fight over `pts`/`qts`/`date` counters (`GetDifference` calls visible in aimly listener debug logs). The real damage is when the **warmup worker sends messages** through the `telegram-api` session strings — those messages appear in conversations that the aimly listener is monitoring, triggering AI responses from the aimly side to what are actually warmup-to-warmup messages.

Additionally: the `telegram-api` warmup pairs `logist_*` and `checker_*` accounts, but aimly re-registered those same accounts as `sender-XXXXXXXXXX`. Warmup messages sent from `logist_8` (telegram-api slug) will appear as incoming messages in the aimly listener's monitoring of `sender-8539506204` (same phone, different slug), potentially triggering AI reply logic.

---

## Running State (as of 2026-06-24 ~16:10 UTC)

```
Container: telegram-api        DB: telegram_followup    Warmup: ACTIVE (6 sessions)
Container: telegram-listener   DB: telegram_followup    Listener: ACTIVE (13 senders)
Container: outreach-platform-listener  DB: outreach_platform  Listener: ACTIVE (12 senders)
Container: outreach-platform-api       DB: outreach_platform  Warmup worker: running but 0 in pool
```

Active warmup sessions in telegram-api DB:
```
sender_1  ↔ checker_3  — next fire: 2026-06-25 06:10 UTC
logist_8  ↔ checker_4  — next fire: 2026-06-24 16:13 UTC  ← IMMINENT
sender_6  ↔ sender_7   — next fire: 2026-06-25 07:15 UTC
logist6   ↔ blah       — next fire: 2026-06-24 17:17 UTC  ← today
sender8   ↔ logist_10  — next fire: 2026-06-24 16:25 UTC  ← IMMINENT
logist_9  ↔ checker_5  — next fire: 2026-06-24 16:21 UTC  ← IMMINENT
```

---

## What Needs to Be Stopped

### To fully isolate the aimly project (cleanest):

**Option A — Stop everything from telegram-api (recommended):**
```bash
cd /root/apps/telegram-api && docker compose stop
```
This stops `telegram-api`, `telegram-listener`, and `telegram-api-db`. The old AGS system goes offline. Safe if AGS production has migrated to the aimly platform.

**Option B — Stop only the warmup (if AGS listener must stay up):**
The warmup is embedded in the `telegram-api` API process — there is no separate container. You'd need to either:
- Comment out `warmup_worker.start()` in `/root/apps/telegram-api/app/main.py` and rebuild
- Or pause all warmup pool entries in the telegram-api DB:
  ```sql
  docker exec telegram-api-db psql -U telegram_user -d telegram_followup -c \
    "UPDATE warmup_pool SET is_active = false;"
  ```
  This stops new warmup sessions but leaves the listener running.

**Option C — Deactivate active sessions immediately:**
```sql
docker exec telegram-api-db psql -U telegram_user -d telegram_followup -c \
  "UPDATE warmup_sessions SET status = 'paused' WHERE status = 'active';"
```
This stops the 3 imminent fires today without touching the listener.

### Scope of damage to assess:
- Check aimly conversations for unexpected AI replies triggered by warmup messages
- The warmup messages were going between accounts that aimly knows as `sender-*` slugs — look for conversations where both parties are aimly senders

---

## Network / Volume Isolation (Not the Problem)

- Docker networks are completely isolated: `telegram-api_default` vs `tg-outreach_default`
- No shared volumes between the two stacks
- The conflict is purely at the **Telegram MTProto level** — both projects authenticate the same phone numbers against Telegram's servers simultaneously using separately-stored but identical session credentials
