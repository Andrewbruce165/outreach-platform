"""Dial-code → country/location lookup for Telegram account cards.

Quick task 260708-ej8: показать менеджеру страну/регион каждого подключённого
аккаунта, выведя её из dial-code телефона (`Sender.phone`, E.164 с ведущим `+`).

NB: НЕ используем библиотеку `phonenumbers` (user decision D-01; та же политика
уже действует в app/utils/phone.py — 5+ MB данных, медленный старт, overkill для
v1). Чистый Python + небольшая таблица кодов достаточно для CIS-фокусной базы.

Семантика возврата (важно для UI):
  - валидный '+' + известный код  → человекочитаемый label ("Russia", "Ukraine")
  - валидный '+' + неизвестный код → "Unknown" (стабильная строка дружелюбнее
                                     для UI, чем None)
  - malformed / пусто / None       → None (никогда не падаем; None зарезервирован
                                     строго под некорректный ввод)
"""

import re
from typing import Optional

_DIGITS_ONLY = re.compile(r"^\d+$")

# Dial code (цифры, без '+') → человекочитаемый label.
# Bias в сторону клиентской базы платформы (CIS), плюс широкий международный охват.
# +7 (Russia/Kazakhstan) резолвится под-правилом ниже, поэтому его тут нет.
DIAL_CODES = {
    # --- CIS / соседи (первичная аудитория аутрича) ---
    "380": "Ukraine",
    "375": "Belarus",
    "374": "Armenia",
    "994": "Azerbaijan",
    "995": "Georgia",
    "996": "Kyrgyzstan",
    "992": "Tajikistan",
    "993": "Turkmenistan",
    "998": "Uzbekistan",
    "373": "Moldova",
    "372": "Estonia",
    "371": "Latvia",
    "370": "Lithuania",
    # --- NANP ---
    "1": "US/Canada",
    # --- Европа ---
    "44": "United Kingdom",
    "49": "Germany",
    "33": "France",
    "39": "Italy",
    "34": "Spain",
    "31": "Netherlands",
    "48": "Poland",
    "351": "Portugal",
    "30": "Greece",
    "40": "Romania",
    "420": "Czechia",
    "43": "Austria",
    "41": "Switzerland",
    "46": "Sweden",
    "47": "Norway",
    "358": "Finland",
    "45": "Denmark",
    "353": "Ireland",
    "32": "Belgium",
    "36": "Hungary",
    # --- Ближний Восток / Африка ---
    "90": "Turkey",
    "971": "UAE",
    "972": "Israel",
    "20": "Egypt",
    "27": "South Africa",
    # --- Азия / Океания ---
    "91": "India",
    "86": "China",
    "81": "Japan",
    "82": "South Korea",
    "62": "Indonesia",
    "60": "Malaysia",
    "65": "Singapore",
    "66": "Thailand",
    "84": "Vietnam",
    "63": "Philippines",
    "92": "Pakistan",
    "61": "Australia",
    "64": "New Zealand",
    # --- Латинская Америка ---
    "55": "Brazil",
    "52": "Mexico",
    "54": "Argentina",
}

# Максимальная длина dial-кода в таблице (для longest-prefix перебора).
_MAX_CODE_LEN = max(len(code) for code in DIAL_CODES)


def phone_location(phone: Optional[str]) -> Optional[str]:
    """Вывести страну/регион из dial-code телефона E.164.

    Правила:
    1. Ввод обязан начинаться с '+' и содержать только цифры после него
       (E.164 sender-phone всегда с '+'). Иначе → None (malformed/empty guard,
       та же идея, что в phone.py — валидный phone начинается с '+').
    2. +7 (неоднозначный Russia/Kazakhstan): смотрим первую национальную цифру
       (сразу после '7'): '6' или '7' → Kazakhstan, иначе → Russia. Это
       приблизительная эвристика (KZ mobile ranges начинаются с 6/7, RU mobile
       с 9); точнее без полной таблицы номеров не разложить.
    3. Longest-prefix match по DIAL_CODES: пробуем самые длинные кандидаты-
       префиксы первыми (3-значные, потом 2-, потом 1-значные), чтобы "+380"
       бил "+3", а "+998" бил "+9".
    4. +1 → "US/Canada" (общий NANP — штаты/провинции не разбираем).
    5. Нет совпадения → "Unknown" (стабильная строка для UI). None строго под
       malformed/empty ввод.

    Examples:
        phone_location("+79001234567")  → "Russia"
        phone_location("+77011234567")  → "Kazakhstan"
        phone_location("+380501234567") → "Ukraine"
        phone_location("+15551234567")  → "US/Canada"
        phone_location("+998901234567") → "Uzbekistan"
        phone_location("+9990001")      → "Unknown"
        phone_location("abc")           → None
        phone_location(None)            → None
    """
    if not phone:
        return None
    stripped = phone.strip()
    if not stripped.startswith("+"):
        return None
    digits = stripped[1:]
    if not digits or not _DIGITS_ONLY.match(digits):
        return None

    # +7 sub-rule: Russia vs Kazakhstan по первой национальной цифре.
    if digits.startswith("7"):
        national_first = digits[1] if len(digits) > 1 else ""
        return "Kazakhstan" if national_first in ("6", "7") else "Russia"

    # Longest-prefix match: длинные коды первыми.
    for length in range(_MAX_CODE_LEN, 0, -1):
        prefix = digits[:length]
        if prefix in DIAL_CODES:
            return DIAL_CODES[prefix]

    return "Unknown"
