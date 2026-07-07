---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 01
type: tdd
wave: 1
depends_on: []
files_modified:
  - app/services/variation.py
  - tests/test_variation.py
autonomous: true
requirements: [D-09, D-10, D-11, D-15, D-16]
must_haves:
  truths:
    - "D-09: vary() inserts only zero-width codepoints U+200B / U+200C / U+2060 plus occasional space-jitter (NBSP U+00A0 / narrow-no-break U+202F); U+200D (emoji joiner) is NEVER emitted; homoglyphs are NEVER used"
    - "D-10/D-14 invisibility invariant: strip_invisible(vary(x)) == x for Latin, Cyrillic, emoji, URL, @mention and markdown-delimiter fixtures (stripping removes {U+200B,U+200C,U+2060} and maps {U+00A0,U+2009,U+202F} back to a normal space)"
    - "D-16: two independent vary(x) calls on a multi-word text produce byte-different output (each send freshly unique, no shared seed)"
    - "D-09 safe-spans: vary() never inserts inside a URL, bare domain, email, @mention, #hashtag, digit run, or emoji/combining grapheme — insertion happens ONLY between two adjacent alphabetic letters of a plain word"
    - "D-15 green corridor: insertion count stays within ~1-3 per ~10 words with a hard cap of 20 insertions per message regardless of length"
    - "D-11: variation is a pure stdlib function (no DB, no I/O, no network) so the worker can call it cheaply; documented as defense-in-depth only, NOT a deliverability guarantee"
  artifacts:
    - path: "app/services/variation.py"
      provides: "pure function vary(text: str) -> str and helper strip_invisible(s: str) -> str"
      contains: "def vary("
      min_lines: 40
    - path: "tests/test_variation.py"
      provides: "RED-first unit tests: invisibility, uniqueness, safe-spans, density-cap, U+200D-absent"
      min_lines: 60
  key_links:
    - from: "app/services/variation.py::vary"
      to: "app/services/queue.py (worker, Plan 24-06 consumer)"
      via: "from app.services.variation import vary"
      pattern: "def vary\\("
---

<objective>
Build the invisible anti-spam text-variation primitive: a pure, stateless function `vary(text) -> str` that makes each outgoing campaign opener byte-unique WITHOUT changing what the recipient reads (D-09/D-10/D-16). This is the only genuinely new algorithm in the phase; everything else wires existing primitives together.

Purpose: defeat naive byte-exact bulk-dedup (D-11 accepted risk — defense-in-depth, NOT a deliverability guarantee).
Output: `app/services/variation.py` (`vary` + `strip_invisible`) fully covered by pure-function tests. No DB, no Telethon — testable in isolation, so this runs in Wave 1 with zero dependencies.

CRITICAL for the executor — codepoint hygiene: NEVER type a raw invisible glyph anywhere (plan/code/tests/commits). Represent every invisible codepoint in Python as `chr(0xXXXX)` (pure ASCII hex) — e.g. `chr(0x200B)` — NOT as a literal glyph and NOT as a backslash-u escape (both are error-prone here). In prose use `U+200B`. Verify with `grep -P '[\x{200b}\x{200c}\x{200d}\x{2060}\x{00a0}\x{2009}\x{202f}]'` returning nothing on both files.
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
<!-- Contract the worker (Plan 24-06) will consume. Codepoints via chr(0xXXXX) ONLY. -->
```python
# app/services/variation.py
_ZW = (chr(0x200B), chr(0x200C), chr(0x2060))   # ZWSP, ZWNJ, WORD JOINER (D-09)
_SPACE_JITTER = (chr(0x00A0), chr(0x202F))       # NBSP, NARROW NO-BREAK SPACE (D-09/D-10)
# U+200D ZERO WIDTH JOINER is DELIBERATELY ABSENT (emoji joiner — D-09 excludes it)

def vary(text: str) -> str: ...          # returns a byte-unique, visually-identical copy
def strip_invisible(s: str) -> str: ...  # inverse used by tests + any debug tooling
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: RED — invisibility / uniqueness / safe-span / density tests</name>
  <read_first>
    - .planning/phases/24-.../24-RESEARCH.md §"Variation Algorithm" (codepoint table, insertion rules, green-corridor density) and §"Code Examples" (strip_invisible + assertions)
    - .planning/phases/24-.../24-VALIDATION.md (Per-Task Verification Map rows VAR-INVIS/UNIQUE/SAFE/density)
    - tests/test_send_campaign.py (existing pure/near-pure test style, assertion idioms)
  </read_first>
  <behavior>
    - strip_invisible(vary(x)) == x for fixtures: plain Latin, plain Cyrillic ("Здравствуйте, Иван"), emoji (base emoji is fine as a visible glyph), URL ("см. https://example.com/path тут"), @mention ("пиши @ivan_ceo"), markdown delims ("_текст_ и *жир* и `код`").
    - vary(x) byte-different across calls: over 5 renders of a >=20-word paragraph, at least 2 distinct outputs (avoids 1-in-a-billion collision flakiness).
    - Safe spans: for URL/@mention/#hashtag/email/digit fixtures, assert NO codepoint from _ZW or _SPACE_JITTER appears inside the protected substring ("https://example.com" byte-identical in output; digit run "+79991234567" untouched).
    - No U+200D: assert `chr(0x200D) not in vary(x)` for every fixture.
    - Density cap: for a 400-word text, count of chars in _ZW present in the output <= 20 (D-15 hard cap).
    - Emoji integrity: build a ZWJ-family fixture as `"\U0001F468" + chr(0x200D) + "\U0001F469" + chr(0x200D) + "\U0001F467"` and assert strip_invisible(vary(fixture)) == fixture — the existing joiner is preserved (strip removes only _ZW, NOT U+200D) and no new insertion lands inside the grapheme.
  </behavior>
  <action>
    Create tests/test_variation.py. Pure module — no DB fixtures (do NOT use async_db_session). Express every invisible codepoint via `chr(0xXXXX)` — no glyphs, no backslash-u. Define the fixtures above. Define a local reference stripper to cross-check the module's own strip_invisible:
    ```python
    _ZW = {chr(0x200B), chr(0x200C), chr(0x2060)}
    def _ref_strip(s):
        s = "".join(c for c in s if c not in _ZW)
        for j in (chr(0x00A0), chr(0x2009), chr(0x202F)):
            s = s.replace(j, " ")
        return s
    ```
    Tests: test_invisible_roundtrip (parametrized over fixtures asserting `_ref_strip(vary(x)) == x` AND `variation.strip_invisible(vary(x)) == x`), test_unique_bytes (5-render distinctness on a long paragraph), test_safe_spans (protected substrings byte-identical in output), test_no_zwj (`chr(0x200D)` absent from every output), test_density_cap (count of _ZW chars in output <= 20 on 400 words), test_emoji_family_preserved.
    These MUST fail initially (module absent) — RED state. Import inside the test body (or guarded) so collection does not ImportError.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_variation.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `tests/test_variation.py` exists and contains `def test_invisible_roundtrip`, `def test_unique_bytes`, `def test_safe_spans`, `def test_no_zwj`, `def test_density_cap`
    - `grep -c 'chr(0x200B)' tests/test_variation.py` >= 1 AND file references `chr(0x200C)`, `chr(0x2060)`, `chr(0x00A0)`, `chr(0x202F)`
    - `grep -P '[\x{200b}\x{200c}\x{200d}\x{2060}\x{00a0}\x{2009}\x{202f}]' tests/test_variation.py` returns NOTHING (no raw invisible glyphs — chr() only)
    - Running the command shows the tests FAIL/ERROR on missing module (RED); collection succeeds
  </acceptance_criteria>
  <done>tests/test_variation.py collects and fails only because app/services/variation.py does not yet exist; the file contains zero raw invisible glyphs.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: GREEN — implement vary() + strip_invisible()</name>
  <read_first>
    - app/services/template.py (module style: stdlib-only, docstring conventions to mirror)
    - .planning/phases/24-.../24-RESEARCH.md §"Variation Algorithm" (insertion rules verbatim) and "Don't Hand-Roll" (~40 lines stdlib; NO homoglyph/spintax lib)
    - tests/test_variation.py (the contract just written)
  </read_first>
  <action>
    Create app/services/variation.py using ONLY stdlib (`re`, `random`). EVERY invisible codepoint via `chr(0xXXXX)` — no glyphs, no backslash-u. Module docstring: state D-11 (defense-in-depth vs naive byte-dedup, NOT a deliverability guarantee) and D-09/D-10 codepoint rationale.
    Constants:
    ```python
    _ZW = (chr(0x200B), chr(0x200C), chr(0x2060))   # ZWSP, ZWNJ, WORD JOINER (D-09)
    _SPACE_JITTER = (chr(0x00A0), chr(0x202F))       # NBSP, NARROW NO-BREAK SPACE (D-09/D-10)
    _MAX_INSERTIONS = 20                              # hard per-message cap (D-15)
    _PROTECT_RE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\S+|[@#]\w+", re.IGNORECASE)
    _LETTER = "a-zA-Zа-яА-ЯёЁ"
    ```
    `vary(text)`:
    1. Build a set of protected character indices from `_PROTECT_RE.finditer(text)` (every index in each match span).
    2. Collect eligible gap positions i (insert BETWEEN text[i-1] and text[i]) where text[i-1] and text[i] are BOTH in `_LETTER` AND neither i-1 nor i is protected. Letter-letter-only inherently skips markdown delimiters, digits, emoji/combining pairs and spaces (per RESEARCH).
    3. Density: if eligible empty → return text unchanged (safe no-op). Else target = min(_MAX_INSERTIONS, max(1, round(len(eligible) * random.uniform(0.10, 0.20)))) (≈1-3 per 10 words, D-15).
    4. Pick `target` distinct positions via random.sample; at each splice random.choice(_ZW). Regenerated per call (no shared seed) → D-16 uniqueness.
    5. Space-jitter (occasional, D-10): for regular spaces flanked by letters on both sides and outside protected spans, replace ~10% (random) with random.choice(_SPACE_JITTER). These don't count toward the ZW cap and strip back to a space.
    NEVER emit `chr(0x200D)`. Pure — no DB, no I/O.
    `strip_invisible(s)`: remove every char in `_ZW`; then replace each of `chr(0x00A0)`, `chr(0x2009)`, `chr(0x202F)` with a normal space. Exact inverse the tests assert (note: does NOT remove U+200D — preserves emoji joiners).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_variation.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/variation.py` contains `def vary(` and `def strip_invisible(`
    - `grep -c 'chr(0x200B)' app/services/variation.py` >= 1 AND file references `chr(0x200C)`, `chr(0x2060)`, `chr(0x00A0)`, `chr(0x202F)` and NEVER `chr(0x200D)` (grep for `0x200D` returns nothing)
    - `grep -P '[\x{200b}\x{200c}\x{200d}\x{2060}\x{00a0}\x{2009}\x{202f}]' app/services/variation.py` returns NOTHING (chr() only, no raw glyphs)
    - grep shows NO third-party import (no `homoglyph`, no `spintax`, no `emoji` package)
    - `pytest tests/test_variation.py` exits 0 (all GREEN)
  </acceptance_criteria>
  <done>All test_variation.py tests pass; strip_invisible(vary(x))==x holds, two renders differ, safe spans and density cap respected, U+200D never emitted, module contains zero raw invisible glyphs.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_variation.py -x` GREEN.
- Manual sanity (optional): `python -c "from app.services.variation import vary,strip_invisible as s; x='Здравствуйте Иван как ваши дела сегодня'; print(vary(x)!=vary(x), s(vary(x))==x)"` prints `True True`.
</verification>

<success_criteria>
`vary()` is a pure stdlib function proven byte-unique per call and strip-invisible-equal to the input, never touching URLs/@mentions/emoji/markdown/digits, never emitting U+200D, capped at 20 insertions (D-09/D-10/D-11/D-15/D-16). Consumable by Plan 24-06 via `from app.services.variation import vary`.
</success_criteria>

<output>
After completion, create `.planning/phases/24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation/24-01-SUMMARY.md`.
</output>
