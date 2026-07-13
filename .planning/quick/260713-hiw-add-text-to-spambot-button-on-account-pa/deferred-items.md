# Deferred items — quick task 260713-hiw

## Pre-existing frontend tsc errors (OUT OF SCOPE — not caused by this task)

`bunx tsc --noEmit` reports 3 pre-existing errors, all the same `/login` route
`search`-param mismatch, in files this task did NOT touch (confirmed unchanged
from the committed baseline via `git status --porcelain`):

- `src/routes/__root.tsx:109` — `navigate({ to: "/login" })` missing required `search`
- `src/routes/_authenticated.tsx:15` — `redirect({ to: "/login" })` missing required `search`
- `src/routes/_authenticated/settings.tsx:903` — `navigate({ to: "/login" })` missing required `search`

Root cause: the `/login` route declares a required search param, so every bare
`{ to: "/login" }` navigation/redirect fails typegen. Not introduced here — the
new `SpambotChatPanel.tsx` and the `accounts.tsx` edits compile with zero errors.
Left for a dedicated fix (add the required `search` arg or make it optional on the
`/login` route).
