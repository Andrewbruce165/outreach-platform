"""Unit-тесты для app/utils/location.phone_location.

Quick task 260708-ej8: вывести страну/регион аккаунта из dial-code его телефона
(без библиотеки `phonenumbers` — user decision D-01). Longest-prefix match по
таблице DIAL_CODES + под-правило для неоднозначного +7 (Russia/Kazakhstan).

Семантика возврата:
  - валидный + известный код  → человекочитаемый label ("Russia", "Ukraine", …)
  - валидный + неизвестный код → "Unknown" (стабильная строка, дружелюбнее для UI)
  - malformed / пусто / None   → None (без падения)
"""

import pytest

from app.utils.location import phone_location


@pytest.mark.parametrize(
    "phone,expected",
    [
        # RU mobile (+79…)
        ("+79001234567", "Russia"),
        # +7 sub-rule: цифра после 7 = 6 или 7 → Kazakhstan
        ("+77011234567", "Kazakhstan"),
        ("+76011234567", "Kazakhstan"),
        # +7 c национальной цифрой 9 → Russia
        ("+79261234567", "Russia"),
        # Ukraine / Belarus
        ("+380501234567", "Ukraine"),
        ("+375291234567", "Belarus"),
        # NANP shared code
        ("+15551234567", "US/Canada"),
        # +1 без абонентских цифр всё равно резолвится (prefix match)
        ("+1", "US/Canada"),
        # UK
        ("+442071234567", "United Kingdom"),
        # Longest-prefix: 998 бьёт 9(нет)/99(нет) — Uzbekistan, не что-то короче
        ("+998901234567", "Uzbekistan"),
        # Longest-prefix: 380 бьёт 3 — Ukraine, не Monaco/прочее на "3"
        ("+380000000", "Ukraine"),
        # CIS соседи
        ("+374991234567", "Armenia"),
        ("+994501234567", "Azerbaijan"),
        ("+995551234567", "Georgia"),
        ("+996771234567", "Kyrgyzstan"),
        ("+992901234567", "Tajikistan"),
        ("+993651234567", "Turkmenistan"),
        ("+373601234567", "Moldova"),
        # Широкий международный охват
        ("+491701234567", "Germany"),
        ("+33612345678", "France"),
        ("+861380013800", "China"),
        ("+911234567890", "India"),
        # Неизвестный код → "Unknown" (стабильная строка, НЕ None)
        ("+9990001", "Unknown"),
        # Malformed / empty / None → None (без падения)
        ("abc", None),
        ("", None),
        ("   ", None),
        (None, None),
        # Нет ведущего '+' → None (E.164 sender-phone всегда с '+')
        ("79001234567", None),
        # Только '+' без цифр → None
        ("+", None),
    ],
)
def test_phone_location(phone, expected):
    assert phone_location(phone) == expected
