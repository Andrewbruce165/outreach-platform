---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 03
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/telegram.py
  - tests/test_send_file_blob.py
autonomous: true
requirements: [D-06, D-07, D-08]
must_haves:
  truths:
    - "D-08: send_file accepts a file_bytes blob source that writes straight to a temp file (skipping the httpx URL download); the existing URL path is preserved (not deleted) so file_url still works when file_bytes is None"
    - "D-06: send_file gains force_document: bool = True (default preserves TODAY's behavior for any existing caller); passing force_document=False makes Telethon auto-media (photo->photo, video->video, else document) because the temp file keeps the ORIGINAL filename extension (suffix = os.path.splitext(file_name)[1])"
    - "D-07: caption >1024 chars reuses the EXISTING overflow branch unchanged — file sent without caption + full text as a separate follow-up message; nothing lost"
    - "Backwards-compat: the current worker call (file_url=..., no file_bytes, no force_document) behaves byte-identically to today (force_document=True, URL download)"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "send_file with Optional file_url + new file_bytes + force_document params; blob->temp branch; force_document passthrough"
      contains: "file_bytes: Optional[bytes]"
    - path: "tests/test_send_file_blob.py"
      provides: "Telethon-mocked tests: blob->temp with correct suffix, force_document arg passthrough, default preserved, overflow reused"
      min_lines: 60
  key_links:
    - from: "app/services/queue.py worker (Plan 24-06)"
      to: "app/services/telegram.py::send_file"
      via: "send_file(file_bytes=blob, file_name=att.file_name, force_document=False, ...)"
      pattern: "def send_file\\("
---

<objective>
Extend the delivery primitive `send_file` so the campaign opener can send a DB-blob as auto-media (photo arrives as a photo, D-06) sourced from bytes rather than a URL (D-08), while the 1024-caption overflow stays exactly as-is (D-07). Defaults are chosen so ANY existing caller is byte-identical to today.

Purpose: give Plan 24-06 (worker) a blob+auto-media entry point without touching queue intervals or the URL path.
Output: `send_file` signature + body extension in telegram.py + Telethon-mocked unit tests.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-CONTEXT.md
@.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-RESEARCH.md

<interfaces>
<!-- Current send_file at app/services/telegram.py:910-1003. Target signature: -->
```python
async def send_file(
    self,
    client: TelegramClient,
    phone: str,
    recipient_name: Optional[str],
    file_url: Optional[str] = None,       # CHANGED: was required positional; now optional
    file_name: Optional[str] = None,
    caption: Optional[str] = None,
    sender_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    file_bytes: Optional[bytes] = None,   # NEW: DB-blob source (D-08)
    force_document: bool = True,          # NEW: default True = unchanged behavior (D-06)
) -> dict:
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: RED — Telethon-mocked send_file tests (blob, auto-media, default, overflow)</name>
  <read_first>
    - app/services/telegram.py:910-1084 (send_file: contact resolve, URL download at 948-956, temp suffix at 959-962, overflow at 964-991, client.send_file at 977-983 with hardcoded force_document=True, error map at 1005-1080)
    - tests/test_send_campaign.py (how the suite mocks the Telethon client / TelegramService; AsyncMock idioms)
    - .planning/phases/24-.../24-VALIDATION.md (send_file rows: blob roundtrip, default-preserved, overflow)
  </read_first>
  <behavior>
    - Blob path: calling send_file(file_bytes=b"\xff\xd8...jpgbytes", file_name="promo.jpg", caption="Привет", force_document=False, ...) writes the bytes to a temp file whose suffix is ".jpg" and calls client.send_file(peer, tmp_path, caption="Привет", file_name="promo.jpg", force_document=False). NO httpx GET is issued.
    - Default preserved: send_file(file_url="http://x/y.pdf", ...) with NO force_document arg → client.send_file called with force_document=True (today's behavior) AND the httpx download path is used.
    - Overflow (D-07): caption of length 1500 with file_bytes → client.send_file called with caption=None AND a SECOND client.send_message(peer, <full 1500 text>) is issued.
    - Guard: send_file with both file_url=None and file_bytes=None → returns a structured error (no crash), does not call client.send_file.
    - Contact-not-registered path (existing) still returns RECIPIENT_NOT_IN_TELEGRAM.
  </behavior>
  <action>
    Create tests/test_send_file_blob.py. Mock the Telethon client with AsyncMock (client.send_file returns an object with .id; client.send_message AsyncMock). Mock TelegramService.resolve_contact / check_contact to return {"is_registered": True, "telegram_id": 123, "access_hash": 456, "from_cache": False}. Patch httpx.AsyncClient to assert it is NOT entered on the blob path (e.g. via a MagicMock that raises if .get is called). Assert the temp path passed to client.send_file ends with ".jpg" on the blob+jpg case, and force_document is the expected value in each case. These MUST fail until Task 2 lands the signature/body (file_bytes not yet a param).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send_file_blob.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `tests/test_send_file_blob.py` contains `def test_blob_source_auto_media`, `def test_url_default_force_document_true`, `def test_caption_overflow_followup`, `def test_no_source_returns_error`
    - Tests assert on `force_document` kwarg passed to the mocked `client.send_file` and on the temp-file suffix
    - Command shows RED (missing file_bytes param / behavior) before Task 2
  </acceptance_criteria>
  <done>RED tests exist that pin blob-source, auto-media flag, default-preservation and overflow reuse.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: GREEN — implement file_bytes source + force_document passthrough</name>
  <read_first>
    - app/services/telegram.py:910-1003 (the exact block being edited)
    - .planning/phases/24-.../24-RESEARCH.md §6 "send_file extension" + Pitfall 1 (temp suffix is load-bearing) + Pitfall 4 (1024 split on the string as-passed)
  </read_first>
  <action>
    Edit app/services/telegram.py::send_file:
    1. Signature: make `file_url: Optional[str] = None` and add `file_bytes: Optional[bytes] = None` and `force_document: bool = True` (exactly as the interfaces block). Update the docstring.
    2. Early guard: if `file_bytes is None and not file_url` → return {"success": False, "error": {"code": "SEND_FAILED", "message": "no file source"}} before the Telethon call.
    3. Source branch: keep the existing filename fallback (949-951) for the URL path. Replace the download+temp block so:
       - if `file_bytes is not None`: `file_data = file_bytes` (skip httpx entirely); `file_name` is required from the caller (attachment always supplies it) — if missing, default "file".
       - else: existing httpx download (unchanged).
       Then the SAME temp-suffix write (`suffix = os.path.splitext(file_name)[1] or ""`; NamedTemporaryFile) — do NOT change this; it is what makes auto-media work (Pitfall 1).
    4. Caption/overflow (D-07): leave the CAPTION_LIMIT=1024 branch EXACTLY as-is (line 964-974). The worker (24-06) passes the already-varied caption, so len() counts the varied string (Pitfall 4 accepted).
    5. Replace the hardcoded `force_document=True` at line 982 with `force_document=force_document`.
    6. Everything else (error mapping 1005-1080, temp unlink in finally, return shape) unchanged.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send_file_blob.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n 'file_bytes: Optional\[bytes\] = None' app/services/telegram.py` matches; `grep -n 'force_document: bool = True' app/services/telegram.py` matches
    - `grep -n 'force_document=force_document' app/services/telegram.py` matches (hardcoded True removed from the send_file call)
    - `grep -n 'file_url: Optional\[str\] = None' app/services/telegram.py` matches (now optional)
    - `pytest tests/test_send_file_blob.py` exits 0 (all GREEN)
  </acceptance_criteria>
  <done>send_file sends a blob as auto-media with the correct extension, preserves the URL path + force_document=True default, reuses the overflow branch, and guards the no-source case.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_send_file_blob.py -x` GREEN.
- Regression spot-check: `pytest -k send_file -q` (existing send_file tests, if any) still GREEN — default behavior unchanged.
</verification>

<success_criteria>
send_file has a blob source + auto-media flag with defaults that preserve today's URL/force_document=True behavior; the 1024 overflow branch is reused verbatim (D-06/D-07/D-08). Ready for the worker to call with file_bytes + force_document=False.
</success_criteria>

<output>
After completion, create `.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-03-SUMMARY.md`.
</output>
