"""CSV-импорт: parse_preview, suggest_mapping, apply_import.

Phase 2 (CONT-01, CONT-05):
- parse_preview: bytes → {columns, sample_rows, delimiter, encoding, looks_like_no_header}
- suggest_mapping: column names → contact field heuristic (англ/рус алиасы, case-insensitive)
- apply_import: bytes + mapping → list of valid contact dicts + summary of skips

Контракт apply_import:
- Возвращает rows_to_insert (готовых к INSERT) и skipped_invalid_reasons (для UI).
- НЕ делает INSERT в БД — это делает роутер для атомарности с удалением CsvImport row.
- НЕ делает dedup-проверку — это тоже роутер через UNIQUE constraint на INSERT.

NB: НЕ используем pandas — stdlib `csv` достаточно (1-10k строк max в v1).
NB: НЕ используем phonenumbers — pure regex normalizer в app/utils/phone.

См. .planning/phases/02-tg-accounts-contacts/02-RESEARCH.md §"CSV Import Pitfalls"
для полного списка edge cases (BOM, delimiters, encoding, кавычки, no-header heuristic).
"""

import csv
import io
import logging
import re

from app.utils.names import normalize_full_name
from app.utils.phone import normalize_to_e164

logger = logging.getLogger(__name__)

# RESEARCH Pitfall 3: Excel "Save As CSV UTF-8" → utf-8-sig (с BOM); legacy
# Russian Excel "Save As CSV (MS-DOS)" → cp1251. Two-step fallback покрывает 99% v1.
ENCODING_FALLBACKS = ["utf-8-sig", "cp1251"]

# D-07: алиасы для suggest_mapping. Лучше иметь явный whitelist чем regex —
# UI шлёт явные suggested_mapping, юзер может подправить в форме.
_COLUMN_ALIASES = {
    "phone": [
        "phone", "phones", "tel", "tel.", "mobile", "телефон", "телефоны",
        "номер", "тел",
    ],
    "username": [
        "username", "user", "юзернейм", "tg", "telegram", "tg_username",
    ],
    "full_name": [
        "name", "имя", "fio", "фио", "full_name", "fullname",
        "имя_фамилия", "имя фамилия",
    ],
    "source": [
        "source", "источник", "src", "origin",
    ],
}

_PHONE_LIKE_RE = re.compile(r"^[+\d][\d\s()-]{6,}$")

# Canonical (non-custom) target fields accepted in a mapping VALUE.
_CANONICAL_FIELDS = frozenset({"phone", "username", "full_name", "source"})


def parse_preview(file_bytes: bytes, max_rows: int = 50) -> dict:
    """Парсит CSV — возвращает первые ~max_rows строк + heuristic mapping.

    Returns:
        {
          "columns": list[str],
          "sample_rows": list[dict],   # max_rows dicts
          "delimiter": str,             # detected или ","
          "encoding": str,              # утилитное "utf-8-sig" или "cp1251"
          "looks_like_no_header": bool, # heuristic: first row выглядит как телефоны
        }

    Raises:
        ValueError("EMPTY_FILE") — если файл пуст
        ValueError("INVALID_ENCODING") — если ни одна из ENCODING_FALLBACKS не подошла
    """
    if not file_bytes:
        raise ValueError("EMPTY_FILE")

    text: str | None = None
    used_encoding: str | None = None
    for enc in ENCODING_FALLBACKS:
        try:
            text = file_bytes.decode(enc)
            used_encoding = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None or used_encoding is None:
        raise ValueError("INVALID_ENCODING")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        raise ValueError("EMPTY_FILE")

    headers = [h.strip() for h in rows[0]]
    sample_rows: list[dict] = []
    # rows[1:max_rows+1] — берём ровно max_rows data-rows (header — отдельно).
    for r in rows[1 : max_rows + 1]:
        # Pad-out shorter rows (некоторые экспортеры срезают trailing запятые).
        row_padded = list(r) + [""] * (len(headers) - len(r))
        sample_rows.append(
            {headers[i]: row_padded[i].strip() for i in range(len(headers))}
        )

    return {
        "columns": headers,
        "sample_rows": sample_rows,
        "delimiter": delimiter,
        "encoding": used_encoding,
        "looks_like_no_header": _heuristic_no_header(headers),
    }


def _heuristic_no_header(headers: list[str]) -> bool:
    """Если все непустые ячейки first row выглядят как телефоны — вероятно нет header'а.

    Heuristic не строгая — UI просит юзера явно подтвердить через тоггл
    "Первая строка — это заголовки?". Возвращает True только когда
    100% непустых ячеек проходят PHONE_LIKE_RE.
    """
    if not headers:
        return False
    non_empty = [h for h in headers if h]
    if not non_empty:
        return False
    phone_like_count = sum(1 for h in non_empty if _PHONE_LIKE_RE.match(h))
    return phone_like_count == len(non_empty)


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    """Heuristic: column name → contact field. Returns `{col_idx_str: field_name}`.

    Field name = 'phone' | 'username' | 'full_name' | 'source'.
    Custom-поля (`custom.<key>`) юзер задаёт сам через UI — мы возвращаем
    только канонические алиасы.
    """
    result: dict[str, str] = {}
    for idx, col in enumerate(columns):
        col_norm = col.lower().strip()
        for field, aliases in _COLUMN_ALIASES.items():
            if col_norm in aliases:
                result[str(idx)] = field
                break
    return result


def _is_valid_field(field: str) -> bool:
    """Mapping VALUE sanity: канонический field или непустой `custom.<key>`."""
    if field in _CANONICAL_FIELDS:
        return True
    if field.startswith("custom."):
        return bool(field[len("custom.") :].strip())
    return False


def resolve_mapping(
    mapping: dict[str, str], headers: list[str]
) -> tuple[list[tuple[int, str]], list[str], list[str]]:
    """Резолвит ключи mapping в индексы колонок — tolerant + loud.

    Канонический контракт — ключ = индекс колонки строкой ("0", "1", …).
    Но UI исторически присылал ключ = ИМЯ колонки (bug 2026-08-11: все такие
    записи молча выбрасывались, см. .planning/debug/resolved/
    csv-contact-mapping-only-username-saved.md). Поэтому принимаем оба
    key-space'а, а всё что не резолвится — возвращаем наверх, НЕ глотаем.

    Returns:
        (resolved, unresolved_keys, unknown_fields)
        resolved: [(col_idx, field), …] — порядок как в mapping, дубликаты убраны
        unresolved_keys: ключи, не совпавшие ни с индексом, ни с именем колонки
        unknown_fields: "<key>=<field>" для невалидных field-имён
    """
    by_name: dict[str, list[int]] = {}
    for idx, header in enumerate(headers):
        by_name.setdefault(header.strip().lower(), []).append(idx)

    resolved: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    unresolved_keys: list[str] = []
    unknown_fields: list[str] = []

    for raw_key, field in mapping.items():
        key = str(raw_key).strip()
        if not _is_valid_field(field):
            unknown_fields.append(f"{key}={field}")
            continue

        col_idx: int | None = None
        # 1) канонический путь — ключ как индекс колонки
        try:
            candidate = int(key)
        except ValueError:
            candidate = None
        if candidate is not None and 0 <= candidate < len(headers):
            col_idx = candidate
        else:
            # 2) fallback — ключ как ИМЯ колонки (case-insensitive, trimmed)
            hits = by_name.get(key.lower())
            if hits:
                col_idx = hits[0]
                if len(hits) > 1:
                    logger.warning(
                        "[csv_import] mapping key %r matches %d duplicate columns "
                        "%s — using the first (index %d). Send index-keyed mapping "
                        "to disambiguate.",
                        key,
                        len(hits),
                        hits,
                        col_idx,
                    )

        if col_idx is None:
            unresolved_keys.append(key)
            continue

        pair = (col_idx, field)
        if pair not in seen:
            seen.add(pair)
            resolved.append(pair)

    return resolved, unresolved_keys, unknown_fields


def apply_import(
    file_bytes: bytes,
    mapping: dict[str, str],
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> dict:
    """Применяет mapping к CSV — возвращает структуру для роутера.

    Args:
        file_bytes: байты CSV из csv_imports.file_data
        mapping: {col_key: field_name}, где col_key — индекс колонки строкой
                 ("0", "1", …; канонический контракт) ИЛИ имя колонки из header'а
                 (tolerant fallback), field_name ∈
                 {'phone', 'username', 'full_name', 'source', 'custom.<key>'}
        delimiter: detected в parse_preview, переиспользуется здесь
        encoding: detected в parse_preview ("utf-8-sig" или "cp1251")

    Returns:
        {
          "rows_to_insert": list[dict],         # phone уже нормализован в E.164
          "skipped_invalid": int,
          "skipped_invalid_reasons": list[dict],  # [{row, reason, value}]
          "total": int,                         # total data rows (header не считаем)
          "unresolved_mapping_keys": list[str], # ключи, не найденные в CSV
          "unknown_mapping_fields": list[str],  # невалидные field-имена
        }

    Raises:
        ValueError("MAPPING_INVALID: ...") — если ни phone, ни username не замаплены,
            либо если ключ, несущий phone/username, не резолвится в колонку CSV
    """
    mapped_fields = set(mapping.values())
    has_phone = "phone" in mapped_fields
    has_username = "username" in mapped_fields
    if not (has_phone or has_username):
        raise ValueError(
            "MAPPING_INVALID: at least one of phone/username must be mapped"
        )

    text = file_bytes.decode(encoding)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if len(rows) < 2:
        return {
            "rows_to_insert": [],
            "skipped_invalid": 0,
            "skipped_invalid_reasons": [],
            "total": 0,
            "unresolved_mapping_keys": [],
            "unknown_mapping_fields": [],
        }

    headers = [h.strip() for h in rows[0]]
    resolved, unresolved_keys, unknown_fields = resolve_mapping(mapping, headers)

    # LOUD: раньше нерезолвящийся ключ молча пропускался (`continue`) и юзер
    # получал 202 с пустыми полями. Теперь — warning в логах + payload наверх.
    if unresolved_keys:
        logger.warning(
            "[csv_import] %d mapping key(s) did not match any CSV column "
            "(columns=%s, unresolved=%s) — those fields will NOT be imported",
            len(unresolved_keys),
            headers,
            unresolved_keys,
        )
    if unknown_fields:
        logger.warning(
            "[csv_import] %d mapping entr(ies) target an unknown field: %s",
            len(unknown_fields),
            unknown_fields,
        )

    resolved_fields = {field for _, field in resolved}
    if not ("phone" in resolved_fields or "username" in resolved_fields):
        # phone/username есть в mapping (проверено выше), но его ключ не резолвится
        # в колонку CSV → fail loudly вместо импорта пустых контактов.
        raise ValueError(
            "MAPPING_INVALID: the column mapped to phone/username does not match "
            f"any CSV column (unresolved keys: {unresolved_keys}, "
            f"csv columns: {headers})"
        )

    data_rows = rows[1:]
    rows_to_insert: list[dict] = []
    skipped_invalid_reasons: list[dict] = []

    for row_idx, raw_row in enumerate(data_rows, start=2):  # header — это row 1
        record = {
            "phone": None,
            "username": None,
            "full_name": None,
            "source": None,
            "custom": {},
        }
        for col_idx, field in resolved:
            if col_idx >= len(raw_row):
                continue
            value = raw_row[col_idx].strip()
            if not value:
                continue
            if field == "phone":
                record["phone"] = value  # нормализация ниже
            elif field == "username":
                record["username"] = value.lstrip("@")
            elif field == "full_name":
                # Title-case at the import boundary so DB holds the display form.
                record["full_name"] = normalize_full_name(value)
            elif field == "source":
                record["source"] = value
            elif field.startswith("custom."):
                custom_key = field[len("custom.") :]
                if custom_key:
                    record["custom"][custom_key] = value

        # Нормализация phone
        if record["phone"]:
            normalized = normalize_to_e164(record["phone"])
            if normalized is None:
                skipped_invalid_reasons.append(
                    {
                        "row": row_idx,
                        "reason": "invalid_phone",
                        "value": record["phone"],
                    }
                )
                continue
            record["phone"] = normalized

        # Хотя бы phone или username должен быть валиден
        if not record["phone"] and not record["username"]:
            skipped_invalid_reasons.append(
                {"row": row_idx, "reason": "no_phone_no_username", "value": ""}
            )
            continue

        rows_to_insert.append(record)

    return {
        "rows_to_insert": rows_to_insert,
        "skipped_invalid": len(skipped_invalid_reasons),
        "skipped_invalid_reasons": skipped_invalid_reasons,
        "total": len(data_rows),
        "unresolved_mapping_keys": unresolved_keys,
        "unknown_mapping_fields": unknown_fields,
    }
