"""Phase 24 Plan 01: pure-function tests for the invisible anti-spam text-variation
primitive `app/services/variation.py` (`vary` + `strip_invisible`).

D-09/D-10/D-11/D-15/D-16 contract:
- vary(x) inserts ONLY zero-width codepoints {U+200B, U+200C, U+2060} plus occasional
  space-jitter {U+00A0, U+202F}; U+200D (emoji joiner) is NEVER emitted; no homoglyphs.
- strip_invisible(vary(x)) == x  (invisibility invariant) for Latin/Cyrillic/emoji/URL/
  @mention/markdown fixtures.
- Two independent vary(x) calls on a multi-word text produce byte-different output.
- Insertion only between two adjacent alphabetic letters of a plain word — never inside
  a URL, bare domain, email, @mention, #hashtag, digit run, or emoji/combining grapheme.
- Density stays within the green corridor with a hard cap of 20 insertions per message.

CRITICAL: this file MUST contain zero raw invisible glyphs. Every invisible codepoint is
written as chr(0xXXXX) (pure ASCII hex). Emoji below use \\U escapes (visible glyphs, not
invisible controls) so they are safe.

Pure module — no DB fixtures (do NOT use async_db_session). The module import happens
inside each test body so collection does not ImportError while the module is absent (RED).
"""

import pytest

# Insertion alphabet + space-jitter set — chr() only, never raw glyphs.
_ZW = {chr(0x200B), chr(0x200C), chr(0x2060)}          # ZWSP, ZWNJ, WORD JOINER
_JITTER = (chr(0x00A0), chr(0x2009), chr(0x202F))       # NBSP, THIN SPACE, NARROW NBSP
_ZWJ = chr(0x200D)                                       # ZERO WIDTH JOINER — must never appear


def _ref_strip(s: str) -> str:
    """Local reference stripper — cross-checks the module's own strip_invisible."""
    s = "".join(c for c in s if c not in _ZW)
    for j in _JITTER:
        s = s.replace(j, " ")
    return s


# Visually-identical fixtures. Emoji use \\U escapes (visible glyphs, not controls).
_LATIN = "Hello dear friend how are you doing today my good old buddy"
_CYRILLIC = "Здравствуйте, Иван"
_EMOJI = "Привет " + "\U0001F44B" + " как дела сегодня друг"
_URL = "см. https://example.com/path тут подробнее"
_MENTION = "пиши мне @ivan_ceo прямо сейчас пожалуйста"
_MARKDOWN = "_текст_ и *жир* и `код` вот так вот"

_ROUNDTRIP_FIXTURES = [
    pytest.param(_LATIN, id="latin"),
    pytest.param(_CYRILLIC, id="cyrillic"),
    pytest.param(_EMOJI, id="emoji"),
    pytest.param(_URL, id="url"),
    pytest.param(_MENTION, id="mention"),
    pytest.param(_MARKDOWN, id="markdown"),
]


@pytest.mark.parametrize("fixture", _ROUNDTRIP_FIXTURES)
def test_invisible_roundtrip(fixture):
    """D-10/D-14: strip_invisible(vary(x)) == x — recipient reads the identical text.

    Checked twice: against a local reference stripper AND the module's own
    strip_invisible (they must agree and both reverse vary()).
    """
    from app.services import variation

    out = variation.vary(fixture)
    assert _ref_strip(out) == fixture
    assert variation.strip_invisible(out) == fixture


def test_unique_bytes():
    """D-16: independent vary() calls on a multi-word paragraph are byte-distinct.

    5 renders of a >=20-word text must yield at least 2 distinct outputs (guards the
    1-in-a-billion collision that could flake a strict !=).
    """
    from app.services import variation

    paragraph = (
        "Здравствуйте уважаемый Иван мы предлагаем вам выгодные условия поставки "
        "подсолнечника в этом сезоне пишите нам сегодня чтобы обсудить детали сделки прямо"
    )
    assert len(paragraph.split()) >= 20
    renders = {variation.vary(paragraph) for _ in range(5)}
    assert len(renders) >= 2


def test_safe_spans():
    """D-09 safe-spans: no invisible char is spliced inside a protected substring.

    URL, bare mid-sentence domain, email, @mention, #hashtag and a digit run must all
    survive byte-identical in the output.
    """
    from app.services import variation

    # Protocol URL.
    assert "https://example.com" in variation.vary("зайдите на https://example.com прямо сейчас")
    # BARE domain — Telegram auto-links agsventurelab.com even without http/www; an
    # insertion inside the label would break the auto-link.
    assert "agsventurelab.com" in variation.vary("пишите на agsventurelab.com сегодня")
    # Email.
    assert "sales@example.com" in variation.vary("почта sales@example.com для связи")
    # @mention and #hashtag.
    assert "@ivan_ceo" in variation.vary("пиши мне @ivan_ceo как удобно тебе")
    assert "#подсолнечник" in variation.vary("тема #подсолнечник очень актуальна сейчас")
    # Digit run / phone.
    assert "+79991234567" in variation.vary("звоните +79991234567 в рабочее время")


@pytest.mark.parametrize("fixture", _ROUNDTRIP_FIXTURES)
def test_no_zwj(fixture):
    """D-09: U+200D (emoji joiner) is NEVER emitted by vary()."""
    from app.services import variation

    assert _ZWJ not in variation.vary(fixture)


def test_density_cap():
    """D-15: hard cap of 20 zero-width insertions per message regardless of length."""
    from app.services import variation

    long_text = " ".join(["слово"] * 400)
    out = variation.vary(long_text)
    inserted = sum(1 for c in out if c in _ZW)
    assert inserted <= 20


def test_emoji_family_preserved():
    """Emoji integrity: an existing ZWJ-family grapheme survives strip+vary unchanged.

    strip_invisible removes only {U+200B, U+200C, U+2060} — NOT U+200D — so the joiner is
    preserved, and vary() lands no insertion inside the grapheme cluster.
    """
    from app.services import variation

    family = "\U0001F468" + chr(0x200D) + "\U0001F469" + chr(0x200D) + "\U0001F467"
    assert variation.strip_invisible(variation.vary(family)) == family
