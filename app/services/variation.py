"""Phase 24 (D-09/D-10/D-11/D-15/D-16): invisible anti-spam text variation.

`vary(text)` returns a byte-unique but visually-identical copy of an outgoing campaign
opener by splicing zero-width codepoints between adjacent letters, plus occasional
near-invisible space-jitter. `strip_invisible(s)` is the exact inverse (used by tests and
any debug tooling) so the recipient always reads the untouched text.

Codepoint rationale (D-09/D-10), verified against Unicode + Telegram client behaviour:
- U+200B ZERO WIDTH SPACE, U+200C ZERO WIDTH NON-JOINER, U+2060 WORD JOINER — truly
  zero-width between Latin/Cyrillic letters; the insertion alphabet.
- U+00A0 NO-BREAK SPACE, U+202F NARROW NO-BREAK SPACE — render as (near-)normal spaces;
  used as occasional space-jitter substitutes.
- U+200D ZERO WIDTH JOINER is the *emoji joiner* and is DELIBERATELY NEVER emitted here —
  inserting it can merge/alter emoji sequences (D-09 excludes it).
- Homoglyphs / spintax are intentionally NOT used (D-09) — this stays ~stdlib-only.

Effectiveness honesty (D-11, MEDIUM confidence): this reliably defeats naive byte-exact
bulk-dedup only. It is DEFENSE-IN-DEPTH, NOT a deliverability guarantee — Telegram's real
anti-spam is behavioural/ML (rate, timing, stranger-volume, reports), handled elsewhere.

Pure function: no DB, no I/O, no network — cheap for the queue worker (Plan 24-06) to call
per send via `from app.services.variation import vary`.
"""

import random
import re

# Insertion alphabet (D-09) — expressed via chr(0xXXXX), never as raw glyphs.
_ZW = (chr(0x200B), chr(0x200C), chr(0x2060))   # ZWSP, ZWNJ, WORD JOINER
# Occasional space-jitter (D-09/D-10). NBSP + NARROW NO-BREAK SPACE only.
_SPACE_JITTER = (chr(0x00A0), chr(0x202F))
# U+200D ZERO WIDTH JOINER is DELIBERATELY ABSENT (emoji joiner — D-09 excludes it).

_MAX_INSERTIONS = 20                             # hard per-message cap (D-15)
_JITTER_PROB = 0.10                              # fraction of eligible spaces jittered (D-10)
_DENSITY_LOW = 0.10                              # ~1-3 insertions per ~10 words (D-15)
_DENSITY_HIGH = 0.20

# Protect protocol/www URLs, emails, @mentions, #hashtags AND bare mid-sentence domains:
# Telegram auto-links a bare domain like agsventurelab.com even with no http/www prefix, so
# an insertion inside its label would break the auto-link. The bare-domain arm uses a
# CONSERVATIVE TLD allowlist so ordinary words with dots (e.g. "т.е.") are NOT over-protected.
_PROTECT_RE = re.compile(
    r"https?://\S+|www\.\S+|\S+@\S+\.\S+|[@#]\w+"
    r"|\b[\w-]+\.(?:com|ru|net|org|io|dev|app|me|рф)\b",
    re.IGNORECASE,
)

# Insertion happens ONLY between two adjacent letters — this inherently skips markdown
# delimiters, digits, spaces and emoji/combining pairs (D-09 insertion rule).
_LETTER_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]")


def _is_letter(ch: str) -> bool:
    return bool(_LETTER_RE.match(ch))


def _protected_indices(text: str) -> set[int]:
    """Every character index covered by a protected span (URL/domain/email/@/#)."""
    protected: set[int] = set()
    for m in _PROTECT_RE.finditer(text):
        protected.update(range(m.start(), m.end()))
    return protected


def vary(text: str) -> str:
    """Return a byte-unique, visually-identical copy of ``text`` (D-16/D-10).

    Splices random zero-width chars between adjacent letters of plain words (never inside
    a URL, bare domain, email, @mention, #hashtag, digit run or emoji grapheme), capped at
    ``_MAX_INSERTIONS`` (D-15), plus occasional near-invisible space-jitter. Regenerated
    per call with no shared seed → each send is freshly unique. Never emits U+200D.

    Pure: no DB, no I/O. ``strip_invisible(vary(x)) == x`` for all inputs.
    """
    if not text:
        return text

    protected = _protected_indices(text)

    # Eligible gaps: insert BETWEEN text[i-1] and text[i] where both are letters and
    # neither index is inside a protected span.
    eligible = [
        i
        for i in range(1, len(text))
        if _is_letter(text[i - 1])
        and _is_letter(text[i])
        and (i - 1) not in protected
        and i not in protected
    ]

    if eligible:
        target = min(
            _MAX_INSERTIONS,
            max(1, round(len(eligible) * random.uniform(_DENSITY_LOW, _DENSITY_HIGH))),
        )
        chosen = set(random.sample(eligible, target))
    else:
        chosen = set()

    out: list[str] = []
    n = len(text)
    for i, ch in enumerate(text):
        if i in chosen:
            out.append(random.choice(_ZW))
        # Space-jitter: only spaces flanked by letters on both sides, outside protected
        # spans, replaced with a small random probability. These strip back to a space.
        if (
            ch == " "
            and 0 < i < n - 1
            and _is_letter(text[i - 1])
            and _is_letter(text[i + 1])
            and i not in protected
            and random.random() < _JITTER_PROB
        ):
            out.append(random.choice(_SPACE_JITTER))
        else:
            out.append(ch)
    return "".join(out)


def strip_invisible(s: str) -> str:
    """Exact inverse of :func:`vary` — recover the original readable text.

    Removes every insertion codepoint in ``_ZW`` and maps each space-jitter codepoint
    (NBSP U+00A0, THIN SPACE U+2009, NARROW NO-BREAK SPACE U+202F) back to a normal space.
    Deliberately does NOT remove U+200D, so pre-existing emoji joiners are preserved.
    """
    if not s:
        return s
    zw = set(_ZW)
    result = "".join(ch for ch in s if ch not in zw)
    for j in (chr(0x00A0), chr(0x2009), chr(0x202F)):
        result = result.replace(j, " ")
    return result
