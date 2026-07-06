# Phase 21: Bulk Telegram account import via session JSON upload in UI - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-06
**Phase:** 21-bulk-telegram-account-import-via-session-json-upload-in-ui
**Areas discussed:** Client fingerprint & API creds, twoFA handling, Bulk upload UX, Validation & start state

---

## Client fingerprint & API credentials

First question (where to store app_id/app_hash + fingerprint):

| Option | Description | Selected |
|--------|-------------|----------|
| Per-account from JSON | Store app_id/app_hash + fingerprint on sender row; global as fallback | |
| Device fingerprint only | Store device fingerprint per-account, api_id/api_hash global | |
| Keep global | Nothing per-account, all via _CLIENT_FINGERPRINT | |

**User's response:** Counter-question — "the vendor says he does NOT carry app_id/app_hash in his own projects, he just loads the sessions and it works. What are the risks for us if we go with option 1?"

**Claude's risk analysis:** Verified global api_id = `3273…` (not tdesktop 2040); vendor JSON app_id=2040 is the public Telegram Desktop id (identical across all vendor accounts). Vendor's practice empirically proves app_id changes don't log out an authorized session (auth_key lives at DC level). Risks of full option 1: storing app_id/app_hash per-account gives ~zero benefit (public, identical), adds regression risk to the hot `make_telegram_client` path used by all workers, fragments app_id in the pool. The real fix is the device/locale fingerprint (ru→en mismatch on +1 numbers), not app_id. `lang_pack` (the only field that triggers termination) is already tdesktop everywhere.

Refined question (what to carry per-account given vendor confirmation):

| Option | Description | Selected |
|--------|-------------|----------|
| Device fingerprint per-account | Store device/app_version/lang per-account; api_id global; lang_pack tdesktop | ✓ |
| Carry nothing (like vendor) | Imported connect via global fingerprint; leaves ru/en mismatch | |
| Full option 1 | Store app_id/app_hash + fingerprint; connect imported under 2040 | |

**User's choice:** Device fingerprint per-account (recommended)
**Notes:** api_id/api_hash stay global (D-03); global _CLIENT_FINGERPRINT becomes fallback for the 13 existing phone-onboarded senders (D-02).

---

## twoFA handling

| Option | Description | Selected |
|--------|-------------|----------|
| Ignore entirely | Don't read/store; session already authorized, matches Phase 20 D-03 | |
| Boolean has_2fa only | Store flag, not password | |
| Show in report once | Display but don't store | |

**User's response (first pass):** "Store it on our side, so if it's needed somewhere we can retrieve it." → contradicts Phase 20 D-03.

Follow-up (how to store + Phase 20 interaction):

| Option | Description | Selected |
|--------|-------------|----------|
| Fernet + Phase 20 auto-fill | Encrypted column; profile form auto-fills current-password for imported | ✓ |
| Fernet, don't touch Phase 20 | Store encrypted for future; wiring deferred | |

**User's choice:** Fernet + Phase 20 auto-fill (recommended)
**Notes:** Deliberate deviation from Phase 20 D-03, scoped to imported accounts (bulk purchased accounts, manual entry doesn't scale). Encrypted at rest (Fernet), never logged/returned (D-05/D-06/D-07).

---

## Bulk upload UX

| Option | Description | Selected |
|--------|-------------|----------|
| ZIP archive | One ZIP of all pairs; backend unzips + matches by basename | ✓ |
| Multi-file drag-n-drop | N separate files; match pairs by name | |
| Both | Support ZIP and drag-n-drop | |

**User's choice:** ZIP archive (recommended)
**Notes:** Match `.json`↔`.session` by basename (session_file field). One POST, simpler for Lovable frontend (D-08).

---

## Validation & start state (batch of 4)

**Q1 — Batch mode (sync vs background):**

| Option | Selected |
|--------|----------|
| Background + status polling | ✓ |
| Synchronous | |

→ D-09: import job + job_id, frontend polls progress.

**Q2 — Start lifecycle_status:**

| Option | Selected |
|--------|----------|
| paused | |
| warmup | |
| active | ✓ |

→ D-13: imported accounts start active. Accepted risk: no warmup before sending.

**Q3 — Dedup by tg_id:**

| Option | Selected |
|--------|----------|
| Skip + report | ✓ |
| Update session | |
| Error on item | |

→ D-14: skip + report "already connected".

**Q4 — Proxy:**

| Option | Selected |
|--------|----------|
| JSON → else pool | ✓ |
| Always from pool | |

→ D-15: JSON proxy if present, else free ProxyPool entry.

**Follow-up — @SpamBot probe at import:**

| Option | Selected |
|--------|----------|
| Yes, part of import | |
| No, only connect+get_me | ✓ |

→ D-11: no SpamBot probe; reconcile picks up restrictions later.

---

## Claude's Discretion

- Import-job / status schema and per-file report structure.
- Disk staging for SQLite→StringSession conversion + cleanup.
- Exact new column names; discrete columns vs JSONB for fingerprint.
- ZIP size limit / max accounts per batch.

## Deferred Ideas

- @SpamBot probe at import (rejected — reconcile handles it).
- Bulk account profile editing (backlog Phase 999.1).
- Warmup/paused start for imported accounts (declined in favor of active — revisit if flagged).
