"""Unit-тесты для app/utils/phone.normalize_to_e164.

Phase 2 (CONT-05): нормализация phone в каноническую E.164 форму перед
записью в contacts.phone и применением UNIQUE (workspace_id, phone).

См. .planning/phases/02-tg-accounts-contacts/02-RESEARCH.md §"Phone Normalization — E.164"
для полного списка edge-кейсов.
"""

import pytest

from app.utils.phone import normalize_to_e164


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Каноническая RU
        ("+79001234567", "+79001234567"),
        # RU leading-8 heuristic (популярный российский формат)
        ("89001234567", "+79001234567"),
        # RU без + (юзер ввёл "просто цифры")
        ("79001234567", "+79001234567"),
        # Форматирование с +
        ("+7 (900) 123-45-67", "+79001234567"),
        # Форматирование + RU leading-8
        ("8 (900) 123-45-67", "+79001234567"),
        # Украина — НЕ применяем RU heuristic
        ("+380501234567", "+380501234567"),
        # Казахстан — 11 digits starting with +77 (НЕ ломаем)
        ("+77001234567", "+77001234567"),
        # США с форматированием
        ("+1 415 555 1212", "+14155551212"),
        # Различные мусорные / некорректные значения
        ("abc", None),
        ("", None),
        ("   ", None),
        # Too short (< 7 digits после плюса)
        ("+1234", None),
        # Too long (> 15 digits)
        ("+1234567890123456", None),
        # None on input
        (None, None),
        # +0 — too short
        ("+0", None),
    ],
)
def test_normalize_to_e164(raw, expected):
    assert normalize_to_e164(raw) == expected
