"""Name normalization for contact imports.

2026-05-26: campaign templates render `Hi {{full_name}}!` — when contacts arrive
with lowercase names ("polina", "andrew"), the rendered message looks
unprofessional. Normalize at the import boundary, not at render time, so the
database holds the display form once.
"""

from typing import Optional


def normalize_full_name(raw: Optional[str]) -> Optional[str]:
    """Title-case a full name string for storage.

    Rules:
    - None / empty / whitespace-only → None (caller decides default).
    - Capitalize each whitespace-separated word ("polina ivanova" → "Polina Ivanova").
    - Preserve hyphens and apostrophes inside words ("jean-luc" → "Jean-Luc",
      "o'brien" → "O'Brien"). str.title() handles these correctly.
    - Already-formed names with internal capitalization (e.g. "McDonald",
      "PolinaCEO") get nuked by title() — but that's an acceptable trade-off
      for v1; the alternative is a much more complex parser. If a user
      insists on "PolinaCEO" they should fix the contact directly.
    - Trim leading/trailing whitespace.

    Examples:
        "polina"        → "Polina"
        "  polina  "    → "Polina"
        "polina ivanova"→ "Polina Ivanova"
        "jean-luc"      → "Jean-Luc"
        "o'brien"       → "O'Brien"
        ""              → None
        None            → None
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    return cleaned.title()
