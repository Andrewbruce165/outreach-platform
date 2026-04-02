# Coding Conventions

**Analysis Date:** 2026-04-02

## Naming Patterns

**Files:**
- `snake_case.py` for all Python modules (`queue.py`, `ai_engine.py`, `check_contacts.py`)
- Routers named after the resource they serve (`senders.py`, `contexts.py`, `conversations.py`)
- Services named after the system they wrap (`telegram.py`, `encryption.py`, `rotation.py`)
- No test files exist in the codebase

**Functions:**
- `snake_case` for all functions and methods
- `async def` for all database-touching and I/O functions — no sync wrappers
- Private methods prefixed with `_` (e.g., `_run`, `_tick`, `_process_next_for_sender`, `_check_rate_limits`)
- Standalone private helpers prefixed with `_` (e.g., `_set_auth_status`, `_estimate_send_time`)
- Worker class methods follow `_verb_noun` pattern (`_get_long_pause_seconds`, `_fail_item`, `_fire_callback`)

**Variables:**
- `snake_case` for variables and function parameters
- `UPPER_SNAKE_CASE` for module-level constants (e.g., `MIN_SEND_INTERVAL`, `MAX_MSGS_PER_HOUR`, `LONG_PAUSE_MIN_SECS`)
- Private instance attributes prefixed with `_` (e.g., `self._task`, `self._running`, `self._idle_event`)
- DB session parameter always named `db`: `db: AsyncSession`
- Settings instance always named `settings`

**Types/Classes:**
- `PascalCase` for all classes (`QueueWorker`, `AIEngine`, `SessionAuthError`, `ResilientTelegramClient`)
- `PascalCase` for Pydantic models (`SendMessageRequest`, `SenderResponse`, `BatchSendRequest`)
- `PascalCase` for SQLAlchemy models (`Sender`, `MessageQueue`, `AIContext`, `WarmupSession`)
- Python `enum.Enum` subclasses use `PascalCase` names, lowercase values (`QueueItemStatus.pending`, `MessageType.sent`)
- Custom exceptions extend `Exception` with `Error` suffix (`SessionAuthError`)

## Code Style

**Formatting:**
- No Prettier/Black/Ruff config detected — formatting is manual/editor-driven
- 4-space indentation throughout
- Blank lines used to separate logical blocks within functions
- Section separators use `# ── Section name ──────────────` pattern (visible in `queue.py`, `models/__init__.py`)

**Linting:**
- No linter config detected (no `.flake8`, `ruff.toml`, `pyproject.toml`)
- Enforce manually: no `time.sleep()`, no `print()`, no sync `requests`

## Import Organization

**Order observed:**
1. Standard library (`asyncio`, `logging`, `random`, `datetime`, `os`, `typing`)
2. Third-party packages (`fastapi`, `sqlalchemy`, `telethon`, `openai`, `pydantic`, `httpx`)
3. Internal app modules (`from app.config import ...`, `from app.database import ...`, `from app.models import ...`, `from app.schemas import ...`, `from app.services.X import ...`, `from app.routers.X import ...`)

**Grouping:**
- Blank line between standard library and third-party groups
- Blank line between third-party and internal groups
- No blank lines within each group

**Path Aliases:**
- None — all imports use `app.` prefix (e.g., `from app.models import Sender`)
- Lazy imports inside function bodies are used in some routers to avoid circular imports (e.g., `from sqlalchemy.orm import selectinload` inside functions in `senders.py`)

## Error Handling

**Patterns in routers:**
- Return structured error responses (not raise) for expected failures in `/send` and `/send-file`:
  ```python
  return EnqueueResponse(
      success=False,
      queued=False,
      timestamp=datetime.now(timezone.utc),
      error={"code": "SENDER_NOT_FOUND", "message": "..."}
  )
  ```
- Raise `HTTPException` for hard failures (404, 400, 409) in CRUD endpoints like `/senders` and `/send-batch`
- All `HTTPException` details use dicts with `code` and `message` keys when structured, or plain strings for simple cases

**Patterns in services:**
- Raise custom exceptions (`SessionAuthError`, `ValueError`) to propagate failures to callers
- Callers catch specific exception types and convert to HTTP responses
- `try/except Exception` with `logger.error(..., exc_info=True)` as the outer catch-all in long-running workers
- `finally` blocks always used to disconnect Telegram clients: `await telegram_service.disconnect_client(client)`

**FloodWait handling:**
- Telethon `FloodWaitError` caught explicitly throughout `queue.py` and `listener.py`
- Never break retry logic without explicit discussion (empirically tuned)

## Logging

**Framework:**
- Standard library `logging` module throughout
- Each module creates its own logger: `logger = logging.getLogger(__name__)`
- Root logging configured once in `app/main.py` with format `"%(asctime)s - %(name)s - %(levelname)s - %(message)s"`

**Patterns:**
- `logger.info(f"...")` for state transitions and successful operations
- `logger.warning(f"...")` for recoverable issues (rate limits, missing data, partial failures)
- `logger.error(f"...", exc_info=True)` for unexpected exceptions — always include `exc_info=True`
- `logger.debug(f"...")` for high-frequency events (per-message details, timer ticks)
- Emoji prefixes used in `listener.py` log messages for visual scanning (📨, ✅, ⚠️, ❌, 📤)
- API keys and session strings are never logged — only slugs and truncated IDs

**Log content pattern:**
- Always include the entity slug or truncated ID for traceability: `f"[{slug}] ..."` or `f"...{queue_id[:8]}..."`

## Comments

**When to Comment:**
- Module-level docstrings explain the purpose and key constraints of the module (see `queue.py` header with rate limits documented)
- Class docstrings explain the role of the class
- Method docstrings explain algorithm steps for non-obvious logic (see `get_or_assign_sender` in `rotation.py`)
- Inline comments explain the *why* behind hardcoded values: `# conservative: Telegram bans at ~30/h to new contacts`
- Section dividers group related constants: `# ── Rate-limit config ────────────────`

**Docstrings:**
- Used on classes and non-trivial methods
- Plain text style (no Google/NumPy format)
- FastAPI endpoint docstrings appear in `/docs` — write them as user-facing descriptions

**TODO Comments:**
- Format: `# TODO: description` — one instance found in `queue.py:480`
- No issue number tracking

## Function Design

**Size:**
- Service functions are long (50–200+ lines) because they contain complete workflows — not split into helpers
- Router handlers are medium (30–80 lines) with inline DB queries rather than extracted service calls
- Utility functions (`encrypt_session`, `decrypt_session`, `build_proxy_tuple`) are small and focused

**Parameters:**
- DB session always passed as first or explicit keyword parameter: `db: AsyncSession`
- Configuration passed via `get_settings()` — not injected into function signatures
- Functions with many parameters use keyword-only style (no positional `*args`)

**Return Values:**
- Async functions return domain objects, dicts, or Pydantic models — never plain tuples in public APIs
- Internal helpers may return `Optional[int]` or `None` as signals
- Early returns used for guard clauses throughout routers

## Module Design

**Exports:**
- `app/models/__init__.py` contains all SQLAlchemy models — single import point for models
- `app/schemas/__init__.py` contains all Pydantic schemas — single import point for schemas
- Services are imported by name: `from app.services.telegram import telegram_service`
- Routers register themselves: `router = APIRouter(prefix="/api/v1/...", tags=[...])`

**Barrel Files:**
- `app/models/__init__.py` and `app/schemas/__init__.py` act as barrels
- `app/services/__init__.py` and `app/routers/__init__.py` are empty (not used for re-exports)

**Singleton Pattern:**
- Service instances created at module level and imported: `telegram_service = TelegramService()` in `telegram.py`
- Worker instances created at module level: `queue_worker = QueueWorker()` in `queue.py`
- Settings cached with `@lru_cache()` in `config.py`

---

*Convention analysis: 2026-04-02*
*Update when patterns change*
