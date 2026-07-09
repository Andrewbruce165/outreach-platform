---
phase: quick-260709-clq
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/listener.py
  - tests/test_typing_hold.py
autonomous: true
requirements: [QUICK-260709-CLQ]

must_haves:
  truths:
    - "После генерации AI-ответа контакт видит «печатает…» продолжительностью, пропорциональной длине ответа (~3-5 chars/sec), а не мгновенную отправку длинного сообщения"
    - "Короткий ответ всё равно показывает минимум ~4с typing суммарно (генерация + hold)"
    - "Длинный ответ (600+ chars) не задерживает отправку дольше ~40с суммарно"
    - "Время генерации LLM засчитывается в бюджет typing: hold = max(0, target - generation_elapsed)"
    - "DB-сессия НЕ удерживается во время hold-sleep (сессия закрыта до повторного входа в safe_typing)"
    - "Пустой/None ответ — hold не выполняется, поведение как раньше"
  artifacts:
    - path: "app/services/listener.py"
      provides: "TYPING_CPS_MIN/MAX + TYPING_HOLD_MIN/MAX constants, pure compute_typing_hold(), wiring in _send_to_ai"
      contains: "compute_typing_hold"
    - path: "tests/test_typing_hold.py"
      provides: "Unit tests for compute_typing_hold (clamping, elapsed subtraction, never negative)"
  key_links:
    - from: "app/services/listener.py::_send_to_ai"
      to: "compute_typing_hold"
      via: "hold computed after generate_response returns, before client.send_message"
      pattern: "compute_typing_hold"
    - from: "app/services/listener.py::_send_to_ai"
      to: "app/services/telegram.py::safe_typing"
      via: "asyncio.sleep(hold) inside a second safe_typing context, AFTER the AsyncSessionLocal block closes"
      pattern: "async with safe_typing.*\\n.*asyncio\\.sleep"
---

<objective>
Human-like typing duration before AI reply is sent. The typing indicator already exists (`safe_typing` wraps `generate_response`), but gpt-5-mini often finishes in 3-5s, so a long reply lands with superhuman typing speed. Add a length-proportional hold: after generation, keep showing "typing…" until `max(0, target_duration - generation_elapsed)` elapses, where `target_duration = clamp(len(reply) / cps, 4s, 40s)` with randomized cps ∈ [3.0, 5.0].

Purpose: believable human behavior on cold outreach accounts — anti-spam hygiene and contact trust.
Output: modified `app/services/listener.py`, new `tests/test_typing_hold.py`, rebuilt listener container.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@app/services/listener.py (lines 130-160 constants, 290-364 `_send_to_ai` — the integration point)
@app/services/telegram.py (lines 213-254 — `safe_read_ack` + `safe_typing`, DO NOT modify)
@CLAUDE.md (async everywhere, NEVER time.sleep, test-overlay rule)

<interfaces>
From app/services/telegram.py (already imported in listener.py line 26):

```python
@asynccontextmanager
async def safe_typing(client, peer):
    """Show "typing..." on the peer for the duration of the context.
    Telethon auto-renews every ~5s. Never raises. Safe with client/peer=None."""
```

Current `_send_to_ai` shape (listener.py ~309-363):
```python
async with AsyncSessionLocal() as session:
    resolved = await get_context_for_conversation(...)
    await safe_read_ack(client, recipient_id, last_msg_id)
    ...
    async with safe_typing(client, recipient_id):
        reply = await ai_engine.generate_response(...)

if reply and client:
    try:
        sent_message = await client.send_message(recipient_id, reply)
        ...
```

Already imported at module level: `asyncio`, `random`, `time` — no new imports needed.
Debounce constants are class attributes on `TelegramListener` (DEBOUNCE_MIN=40.0, DEBOUNCE_MAX=120.0, MAX_BUFFER_TIME=300.0 at lines 133-136).
</interfaces>

Structural notes:
- `_send_to_ai` runs inside per-conversation debounce asyncio tasks — a per-conversation hold of up to ~40s does NOT block other conversations.
- Debounce (40-120s) already covers the "no instant reply" half; typing hold starts only at generation, NOT during debounce (human is "reading/away" then).
- The DB session must NOT be held during the hold-sleep: the existing `async with AsyncSessionLocal()` block closes after `generate_response`, then a SECOND `safe_typing` context wraps the sleep. Millisecond gap in the indicator between the two contexts is invisible to the contact.
- `git status` shows `app/services/listener.py` may carry pre-existing uncommitted edits (parallel work). Before committing: `git diff app/services/listener.py`, stage ONLY specific files (never `git add -A`), and if unrelated pre-existing hunks exist in listener.py, note them in the commit message rather than reverting them.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: compute_typing_hold + wiring in _send_to_ai</name>
  <files>app/services/listener.py, tests/test_typing_hold.py</files>
  <behavior>
    Pure module-level function in listener.py:
    ```python
    def compute_typing_hold(reply_len: int, elapsed: float, cps: float) -> float:
        """Сколько ещё держать «печатает…» после генерации ответа.

        target = clamp(reply_len / cps, TYPING_HOLD_MIN, TYPING_HOLD_MAX);
        время генерации LLM засчитывается в бюджет.
        """
        target = min(max(reply_len / cps, TYPING_HOLD_MIN), TYPING_HOLD_MAX)
        return max(0.0, target - elapsed)
    ```
    Tests in tests/test_typing_hold.py (pure function, no DB, no mocks):
    - Test 1: 200 chars @ cps=5.0, elapsed=0 → 40.0 (200/5=40, at ceiling)
    - Test 2: 300 chars @ cps=3.0, elapsed=0 → 40.0 (100 clamped to TYPING_HOLD_MAX=40)
    - Test 3: 8 chars @ cps=4.0, elapsed=0 → 4.0 (2s floored to TYPING_HOLD_MIN=4)
    - Test 4: 100 chars @ cps=4.0, elapsed=10.0 → 15.0 (25 - 10, elapsed subtracted)
    - Test 5: 20 chars @ cps=4.0, elapsed=60.0 → 0.0 (never negative — slow generation eats the whole budget)
    - Test 6: 0 chars @ cps=4.0, elapsed=0 → 4.0 (floor applies even to degenerate empty length)
  </behavior>
  <action>
    1. Add module-level constants in app/services/listener.py, placed just above `class TelegramListener` (near the DEBOUNCE_* class attrs they complement), with a short Russian comment matching file style:
       ```python
       # Typing-hold: имитация человеческой скорости набора после генерации LLM.
       # Скорость рандомизируется per-message; floor — даже короткий ответ
       # показывает пару секунд «печатает», ceiling — длинный ответ не стопорит
       # conversation-task дольше ~40с.
       TYPING_CPS_MIN = 3.0    # chars/sec, ≈180 chars/min
       TYPING_CPS_MAX = 5.0    # chars/sec, ≈300 chars/min
       TYPING_HOLD_MIN = 4.0   # сек — минимальная суммарная длительность typing
       TYPING_HOLD_MAX = 40.0  # сек — потолок (600-char ответ не ждёт 2+ мин)
       ```
    2. Add `compute_typing_hold` (module-level, pure, per behavior above). cps is an explicit parameter — randomness stays at the call site, function stays deterministic/testable.
    3. Write tests/test_typing_hold.py FIRST, confirm RED (function absent), then implement, confirm GREEN. Import: `from app.services.listener import compute_typing_hold, TYPING_HOLD_MIN, TYPING_HOLD_MAX`. Use `pytest.approx` for float comparisons.
    4. Wire into `_send_to_ai` (listener.py ~309-347):
       - Set `gen_start = time.monotonic()` immediately before the existing `async with safe_typing(client, recipient_id):` block that wraps `generate_response`. (`time` already imported.)
       - Leave the session block and generation block otherwise untouched (read_ack, resolve, generate — all as-is).
       - After the `async with AsyncSessionLocal()` block closes, inside the existing `if reply and client:` branch, BEFORE `client.send_message`:
         ```python
         # Human-like typing: держим «печатает…» пропорционально длине ответа.
         # Сессия БД уже закрыта — sleep не удерживает соединение.
         cps = random.uniform(TYPING_CPS_MIN, TYPING_CPS_MAX)
         hold = compute_typing_hold(len(reply), time.monotonic() - gen_start, cps)
         if hold > 0:
             logger.info(
                 f"⌨️ Typing hold {hold:.1f}с для {conversation_id[:8]} "
                 f"({len(reply)} chars, cps={cps:.1f})"
             )
             async with safe_typing(client, recipient_id):
                 await asyncio.sleep(hold)
         ```
       - Empty/None reply path: unchanged — the branch is not entered, no hold.
       - NEVER time.sleep (project rule); DO NOT touch app/services/telegram.py; DO NOT touch DEBOUNCE_* or any queue.py intervals (empirical, CLAUDE.md guard).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_typing_hold.py -x -q</automated>
  </verify>
  <done>All 6 unit tests GREEN via test-overlay. `_send_to_ai` measures generation elapsed, computes hold with randomized cps, sleeps inside a second safe_typing context after the DB session block closes, then sends. Empty-reply path unchanged.</done>
</task>

<task type="auto">
  <name>Task 2: Deploy listener + verify clean start + commit</name>
  <files>app/services/listener.py, tests/test_typing_hold.py</files>
  <action>
    1. Sanity-import check before rebuild:
       `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "from app.services.listener import compute_typing_hold; print(compute_typing_hold(200, 5.0, 4.0))"` → expect `35.0` (200/4=50 → clamped to 40 → minus 5 elapsed = 35.0).
    2. Rebuild listener only (telegram.py untouched, api does not need rebuild):
       `cd /root/apps/aimly/tg-outreach && docker compose up -d --build listener`
    3. Verify clean start: `docker logs outreach-platform-listener --tail 30` — no tracebacks, listener connects senders as usual.
    4. Commit: `git diff app/services/listener.py` first (file may carry pre-existing uncommitted hunks from parallel work — if present, keep them and mention in commit body; never revert). Stage ONLY the two files: `git add app/services/listener.py tests/test_typing_hold.py` (never `git add -A`). Commit message: `feat(listener): human-like typing hold proportional to reply length before AI send`.
  </action>
  <verify>
    <automated>docker logs outreach-platform-listener --tail 30 2>&1 | grep -ci "traceback" | grep -q "^0$" && docker ps --filter name=outreach-platform-listener --format "{{.Status}}" | grep -q "Up"</automated>
  </verify>
  <done>Listener container rebuilt and Up with no startup tracebacks; changes committed with file-scoped staging. Live behavior observable on next inbound AI reply: log line `⌨️ Typing hold …` followed by `📤 AI ответил`.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_typing_hold.py` GREEN via test-overlay (never without overlay — prod DB wipe risk).
- `grep -n "compute_typing_hold\|TYPING_HOLD_MAX" app/services/listener.py` shows constants, function, and call site in `_send_to_ai`.
- The hold-sleep is NOT inside the `async with AsyncSessionLocal()` block (visual inspection of the diff).
- Listener container Up, no tracebacks after rebuild.
</verification>

<success_criteria>
- AI replies are preceded by a typing indicator whose total visible duration ≈ clamp(len(reply)/cps, 4s, 40s), with LLM generation time counted toward the budget.
- No time.sleep introduced; no DB session held during the hold; DEBOUNCE_*/queue intervals untouched; telegram.py untouched.
- Targeted tests GREEN; deployed to prod listener.
</success_criteria>

<output>
After completion, create `.planning/quick/260709-clq-show-typing-indicator-to-contact-while-a/260709-clq-SUMMARY.md`
</output>
