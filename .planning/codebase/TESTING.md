# Testing Patterns

**Analysis Date:** 2026-04-02

## Test Framework

**Runner:**
- No test framework installed or configured
- No `pytest`, `unittest`, `jest`, or any other test runner found in `requirements.txt`
- No `pytest.ini`, `conftest.py`, `jest.config.*`, or `vitest.config.*` present

**Assertion Library:**
- None — no test infrastructure exists

**Run Commands:**
```bash
# No test commands available
# requirements.txt contains no test dependencies
```

## Test File Organization

**Location:**
- No test files exist (no `test_*.py`, `*_test.py`, `*.test.*`, `*.spec.*` files found)
- No `tests/` or `__tests__/` directories

**Naming:**
- No established naming convention — tests do not exist yet

**Structure:**
```
app/                    # Source code — no tests alongside
  routers/              # No *.test.py files
  services/             # No *.test.py files
  models/               # No *.test.py files
  schemas/              # No *.test.py files
```

## Test Structure

**Suite Organization:**
- Not established — no tests exist

**Patterns:**
- Not established

## Mocking

**Framework:**
- Not established

**What to Mock (when tests are added):**
- Telegram API calls (`telethon.TelegramClient`) — live calls must never run in tests
- Database session (`AsyncSession`) — use async test DB or mock
- OpenAI client (`openai.AsyncOpenAI`) — mock to avoid real API calls
- `httpx.AsyncClient` — mock for callback/webhook calls
- `subprocess.run` — mock for Docker restart calls in `app/routers/senders.py`
- Time/datetime — mock for working-hours logic in `app/services/queue.py`

**What NOT to Mock:**
- `app/services/encryption.py` — pure functions, test with real inputs
- Pydantic schema validation — test with real model instantiation
- `app/services/rotation.py` logic — test with in-memory or test DB

## Fixtures and Factories

**Test Data:**
- No fixtures or factories exist yet

**Recommended patterns when adding tests:**
```python
# Factory for Sender model
def make_sender(**overrides) -> dict:
    return {
        "slug": "test-sender",
        "name": "Test Sender",
        "phone": "+79001234567",
        "session_string": "encrypted_session",
        "is_active": True,
        "role": "sender",
        **overrides
    }
```

**Location:**
- No established location — suggest `tests/fixtures/` for shared data and factory functions inline in test files

## Coverage

**Requirements:**
- None enforced — no coverage tooling configured
- No CI pipeline checks coverage

**View Coverage:**
```bash
# Not configured — add pytest-cov to requirements when tests are introduced
# pytest --cov=app --cov-report=html
```

## Test Types

**Unit Tests:**
- Not present
- Priority candidates: `app/services/encryption.py`, `app/services/rotation.py`, Pydantic schema validators, `QueueWorker._is_working_hours()`, `QueueWorker._next_working_window()`

**Integration Tests:**
- Not present
- Priority candidates: queue enqueue/dequeue flow, sender CRUD endpoints, AI context CRUD

**E2E Tests:**
- Not present
- Telegram live integration is manually tested against real accounts

## Common Patterns

**Async Testing (when added):**
```python
import pytest
import pytest_asyncio

@pytest.mark.asyncio
async def test_enqueue_message():
    # arrange
    # act
    result = await enqueue_message(db=mock_db, ...)
    # assert
    assert result["queue_id"] is not None
```

**Error Testing (when added):**
```python
import pytest

def test_encrypt_decrypt_roundtrip():
    original = "test_session_string"
    encrypted = encrypt_session(original)
    assert decrypt_session(encrypted) == original

async def test_sender_not_found_returns_error_response():
    response = await send_message(request_with_missing_sender, db=mock_db)
    assert response.success is False
    assert response.error["code"] == "SENDER_NOT_FOUND"
```

**Snapshot Testing:**
- Not used

## Priority Areas for First Tests

The following modules contain pure or nearly-pure logic most suitable for initial test coverage:

1. `app/services/encryption.py` — `encrypt_session` / `decrypt_session` roundtrip
2. `app/schemas/__init__.py` — Pydantic model validation (required fields, validators, `model_validator`)
3. `app/services/queue.py` — `_is_working_hours()`, `_next_working_window()`, `_estimate_send_time()`
4. `app/services/rotation.py` — `get_or_assign_sender()` with mocked DB
5. `app/routers/send.py` — Endpoint logic with mocked DB and service layer

## Adding Test Infrastructure

To add tests, install these packages:
```
pytest==8.x
pytest-asyncio==0.23.x
pytest-mock==3.x
httpx              # already in requirements.txt, use AsyncClient as test client
```

Place test files either:
- Co-located: `app/services/encryption_test.py` alongside source
- Separate tree: `tests/unit/services/test_encryption.py`

Recommend separate tree (`tests/`) to avoid shipping test code in Docker image.

---

*Testing analysis: 2026-04-02*
*Update when test patterns change*
