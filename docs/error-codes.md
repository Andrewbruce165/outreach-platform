# Backend error codes → UI strings (en-US)

Backend uses HTTPException with `detail: {code, message}` envelope (Phase 1 D-04 / Phase 4 / Phase 5 pattern). Frontend maps `code` → friendly English; never displays raw `message` (often English internal-speak).

| Backend code | HTTP | UI string | Notes |
|---|---|---|---|
| `TOKEN_EXPIRED` | 401 | "Your session expired. Sign in again." | Auto-redirect to /login |
| `TOKEN_INVALID` | 401 | "Your session expired. Sign in again." | Same handling as TOKEN_EXPIRED |
| `WORKSPACE_NOT_FOUND` | 404 | "Workspace not found" | Should not occur post-login |
| `CAMPAIGN_NOT_FOUND` | 404 | "Campaign not found" | Cross-workspace also returns this (silent isolation per Phase 1 D-04) |
| `SENDER_NOT_FOUND` | 404 | "Account not found" | UI calls them "accounts", not "senders" |
| `AGENT_NOT_FOUND` | 404 | "Agent not found" | |
| `FOLDER_NOT_FOUND` | 404 | "Folder not found" | |
| `CONVERSATION_NOT_FOUND` | 404 | "Conversation not found" | |
| `INVALID_TRANSITION` | 409 | "Can't change from {from} to {to}" | Surface `from`/`to` from detail |
| `SENDER_LOCK_CONFLICT` | 409 | "Sender {name} is locked by campaign {other}. Stop that campaign to free the sender." | UI-SPEC §8.3 |
| `NO_SENDERS_ATTACHED` | 422 | "Attach at least one account before launching" | Builder step 3 validation hint |
| `UNKNOWN_EVENT` | 400 | "Internal: unknown telemetry event" | Debug-only; should not reach user — log to console.warn |
| `ID_REQUIRED` | 422 | "Missing id parameter" | Used by /analytics/funnel + /analytics/llm |
| `INVALID_PHONE` | 422 | "Phone number is invalid. Use +1 415 555 2810 format." | Onboarding step 1 |
| (generic 5xx) | 5xx | "Server is unreachable. Retry." | Sonner toast with Retry action |

Reference: when in doubt, fall back to UI-SPEC §8.3 copywriting contract.
