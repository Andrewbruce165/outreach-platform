# Phase brief: Checker probe over-firing burns the resolve pool (drain throttle)

**Date:** 2026-06-29
**Status:** observed in prod during the first real drain after the Phase-14 gap-closure deploy
**Companion notes:** `.planning/notes/checker-false-negatives.md` (original shadow-ban diagnosis), `.planning/notes/checker-pool-throttle-spike.md` (14-06 read-only spike, conditional GO)

---

## 1. Problem statement

The first real drain of a cleaned mobile-only base (~5.4k contacts) confirmed the resolution
pipeline is **data-safe** but **throttle-bound and self-burning**: every checker — fresh or
previously-burned — trips the Telegram contacts-API throttle after only ~50–76 live resolves,
gets pulled, auto-recovers ~15 min later, and trips again. The drain crawls and churns, and the
accounts are being slowly burned **by our own health probe**, not by the resolve workload.

The data layer holds: all throttled batches roll back to `pending`, and every finalized
`not_registered` (95 of them) is `high`/`clean` from a healthy checker. No poisoning. The problem
is throughput + account longevity, not correctness.

## 2. Root cause (precise)

`app/services/contact_check_worker.py::_run` runs every `poll_interval` (default **5s**):
```
await self._recover_checkers()
await self._probe_cycle()     # <-- fires EVERY 5s
await self._tick()
```

`_probe_cycle` (line ~450) selects every eligible checker and calls `probe_checker`, which
resolves a 3-number control sample **live** against Telegram. Two defects compound:

1. **Probe runs every loop cycle (~5s).** For a continuously-active checker that is 3 live
   resolves / 5s ≈ **36 resolves/min of pure probe load** — on top of the real resolve batches.
   The probe meant to *detect* the throttle is itself a major contributor to *causing* it.

2. **The probe gate ignores `checker_rest_until`.** Its WHERE clause (lines ~465-471) checks
   `restriction_status='none'`, `lifecycle_status<>'paused'`, `restricted_until`, but **NOT**
   the Plan-14-07 post-batch rest column `checker_rest_until`. So a checker that is "resting"
   after a batch (correctly excluded from the resolve `_tick` LATERAL) is **still probed every
   5s**. The 14-07 rest only rests the resolve path, not the probe path → the rest is defeated.

Consequence: when a checker enters the trip→cooldown→recover→trip cycle, the probe hammers it
~every tick. Measured today: the two previously-burned checkers logged **~4,267 probe batches each
(≈12,800 live resolves/account/day)** — exactly the "thousands/day → hard shadow-ban" load that
killed the original checker `sender-8428118140`. Stably-active fresh checkers logged only ~85-97
probes/day, so the runaway is triggered by *cycling*; but any checker that trips once enters the
cycle.

## 3. Evidence (prod, 2026-06-29 ~07:20 UTC)

Drain progress: **109 registered / 95 not_registered / 5,235 pending** (~53% registered among
resolved — the healthy mobile rate the calibration expects, vs the broken 2.5% throttle signature).

Per-checker batches **today**:

| checker | role | probe batches (3#) | real batches (30#) | outcome |
|---|---|---|---|---|
| sender-7979031303 | old (14-04-burned) | **4267** | 2 | trip-cycle, now parked 2030 |
| sender-8364639216 | old (14-04-burned) | **4266** | 2 | trip-cycle, now parked 2030 |
| sender-8349156575 | fresh | 97 | 4 | tripped after ~76 resolves (spam_limited) |
| sender-8071536685 | fresh | 85 | 3 | still healthy / active |
| sender-8428118140 | original shadow-ban | 0 | 0 | parked 2030 |

Trip signature in logs: a 30-phone batch returns `checked=30 registered=0 not_registered=30
flood_wait=False` immediately after a 3-number control probe returns `3/3 registered`. Since the
base is ~53% registered, a true-random 0/30 is ~`0.47^30` ≈ impossible → the 0/30 is a real
soft-throttle, correctly caught by the inline anomaly detector (`_is_throttle_signal`, Plan 14-05)
and rolled back to `pending`.

No `FROZEN` / `Unauthorized` / banned / real `FloodWait` anywhere — **the accounts are alive at the
Telegram level** (valid sessions, `auth_status='ok'`, probes resolve). `spam_limited` is our own
reversible contacts-resolve flag, not a Telegram ban.

## 4. Current prod state (what this session left)

- **Checkers:** `8071536685` active+healthy (in rotation). `8349156575` auto-recovering (will
  rejoin ~07:48 and likely trip again). `7979031303`, `8364639216`, `8428118140` parked to 2030
  (`spam_limited`/`paused`) — manually parked this session to stop the probe-loop burn.
- **Drain:** still running on the 1 healthy checker; data-safe but slow/churny.
- **Backup:** `outreach_20260626_170215.sql.gz` (pre-purge). Base re-uploaded by user after purge.
- Consider pausing the drain (park all / stop worker) until the fix ships, so the probe loop stops
  burning the remaining fresh checker(s).

## 5. What is already built — DO NOT rebuild (Phase 14)

- **Inline flood/throttle-aware finalization (14-05):** `_is_throttle_signal` → suspect batch rolls
  back to `pending`, never finalizes `not_registered`/`high`/`clean`; degrades checker inline. This
  WORKS and is the reason the data stayed clean. Keep it; lean on it more (it is free — it reads the
  real batch, no extra resolves).
- **Benign post-batch rest (14-07):** `senders.checker_rest_until` + `contact_check_rest_seconds`
  (default 300s). Correct idea, but (a) too short and (b) NOT honored by the probe path — see §2.
- **Restriction-gated selection, suspect-rollback, confidence/source (`tg_confidence`/`tg_resolved_by`/
  `tg_probe_state`), 49-number control set, daily-cap, burst-cap.** All in place.
- Knobs (`app/config.py`): `contact_check_burst_cap=30`, `pace_low/high=2.0/3.5s`,
  `cooldown_seconds=900`, `daily_cap=400`, `poll_interval=5s` (env), `rest_seconds=300`.

## 6. Candidate solution directions (for planning — not decided)

1. **Stop the probe from running every tick.** Probe a checker at most every N minutes, or only
   on a trigger (e.g. once on wake-from-rest, or only when a batch looks borderline). The inline
   anomaly signal (14-05) already detects throttle for free on every real batch — the active probe
   is largely redundant and should be rare.
2. **Probe gate must respect `checker_rest_until`** (and the post-batch rest should cover the probe
   path too) — a resting checker must be fully idle, not probed.
3. **Count probe resolves against the budget.** `daily_cap`/burst accounting appears to ignore probe
   load, so the probe silently blows the per-account budget.
4. **Longer / escalating cooldown on repeated trips** — don't auto-recover a checker in 15 min just
   to trip it again; back off (e.g. exponential) so a cycling checker rests for hours, not minutes.
5. **Longer post-batch rest**, tuned to the real onset window (5 min is too short; the onset window
   is clearly longer than 5 min). Consider resting after a cumulative count across batches, not just
   per-batch.
6. **Throughput is checker-count-bound.** Each account sustainably yields ~(onset)/(rest window)
   resolves; total = sum across healthy accounts. Plan for enough fresh checker accounts + a rest
   long enough to keep each under its onset. Parallel-vs-sequential does not change total throughput.

## 7. Open questions

- What is the true per-account onset and the rest window needed to reset it? (The 14-06 spike showed
  ~47-49 on a rested account; today fresh tripped at ~76 — but the probe load muddies this. Re-measure
  once the probe is fixed.)
- Are the previously-burned accounts (`7979031303`, `8364639216`) permanently lower-ceiling, or do
  they recover after days of full rest? (Spike said burn is reversible; verify.)
- Do we need more checker accounts to hit an acceptable drain time for ~5k+ bases?
- Is the active control-probe needed at all once inline detection + a sane cooldown are in place?

## 8. Safety invariants to preserve

- Never finalize `not_registered` from a throttled/suspect checker — roll back to `pending`.
- The 49 control rows (folder `4ecdde17-…`, "Barter_список пещивиков Ромы") must stay `registered`.
- Tests only via the test-overlay; migrations idempotent + auto-applied; never `down -v`.
