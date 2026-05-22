"""Phase 4 D-19 / C-02 / C-03: Mustache-style template rendering for campaign messages.

Supported variables (case-insensitive):
- {{name}} / {{имя}}      → contact.full_name
- {{username}} / {{юзернейм}} → '@' + contact.username (empty if no username)
- {{phone}} / {{телефон}} → contact.phone
- {{source}} / {{источник}} → contact.source
- {{custom.X}}            → contact.custom[X]  (X is English-only)
- {{компания}}            → contact.custom['company']  (alias to custom.company)

Spaces inside braces OK: {{ name }} works.
Missing variables → empty string + logger.warning (D-19; strict mode deferred to v2).
No Mustache filters ({{name | upper}}) per C-03.
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Regex per C-03: allows spaces inside braces, supports Cyrillic letters,
# dot notation for custom.X (English keys only inside the dotted suffix).
TEMPLATE_VAR_RE = re.compile(
    r"\{\{\s*([a-zA-Zа-яА-Я_][a-zA-Zа-яА-Я_0-9]*(?:\.[a-zA-Z_0-9]+)?)\s*\}\}",
    re.IGNORECASE | re.UNICODE,
)

# C-02 — Russian alias table (lowercase keys → canonical English var name).
RUSSIAN_ALIASES = {
    "имя": "name",
    "юзернейм": "username",
    "телефон": "phone",
    "источник": "source",
    "компания": "custom.company",
}


def _resolve(var_name: str, contact: dict[str, Any]) -> Optional[str]:
    """Resolve {{var}} → contact field value. Returns None if missing/unknown.

    Args:
        var_name: variable name from inside braces (already lowercased).
        contact: dict with contact fields (full_name, username, phone, source, custom).

    Returns: string value or None when variable cannot be resolved.
    """
    # Apply Russian alias (lowercase keys).
    if var_name in RUSSIAN_ALIASES:
        var_name = RUSSIAN_ALIASES[var_name]

    # custom.X dotted notation.
    if "." in var_name:
        prefix, _, key = var_name.partition(".")
        if prefix != "custom":
            return None
        custom = contact.get("custom") or {}
        value = custom.get(key)
        return None if value is None or value == "" else str(value)

    if var_name == "name":
        v = contact.get("full_name")
        return None if not v else str(v)
    if var_name == "username":
        v = contact.get("username")
        if not v:
            return None
        # Prepend @ if missing.
        v_str = str(v)
        return v_str if v_str.startswith("@") else f"@{v_str}"
    if var_name == "phone":
        v = contact.get("phone")
        return None if not v else str(v)
    if var_name == "source":
        v = contact.get("source")
        return None if not v else str(v)

    return None


def render_template(
    template: str,
    contact: dict[str, Any],
    *,
    campaign_id: str = "?",
    phone: str = "?",
) -> str:
    """Render Mustache-style template with contact fields.

    Args:
        template: string with {{var}} placeholders.
        contact: dict with full_name / username / phone / source / custom.
        campaign_id: for warning logs (no semantic effect on render).
        phone: for warning logs (no semantic effect on render).

    Returns: rendered string (missing vars → '' + warning per D-19).
    """
    if not template:
        return ""

    def replacer(match: re.Match) -> str:
        raw = match.group(1)
        # Case-insensitive lookup — lower before resolve.
        value = _resolve(raw.lower(), contact)
        if value is None:
            logger.warning(
                "Template variable {{%s}} missing for contact phone=%s campaign=%s",
                raw, phone, campaign_id,
            )
            return ""
        return value

    return TEMPLATE_VAR_RE.sub(replacer, template)
