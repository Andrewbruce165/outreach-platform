#!/usr/bin/env python3
"""scripts/check-uispec-endpoints.py
Drift check: every /api/v1/* path mentioned in UI-SPEC §5 must appear in openapi.json.
Exits 1 (with a clear diff) on drift. Exits 0 if UI-SPEC is a subset of shipped routes.

Usage:
    python scripts/check-uispec-endpoints.py lovable-handoff/openapi.json \\
        .planning/phases/05.1-lovable-ui-v1/05.1-UI-SPEC.md
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


# Match /api/v1/<segment>[/<segment>]+ where segments can be:
#   - literal (lowercase letters, digits, hyphens, dots)
#   - path params {x}, {x_y}
#   - query strings stripped off via re.split('?', ...)
ENDPOINT_RE = re.compile(r"/api/v1/[a-z0-9_./\-{}]+", re.IGNORECASE)


def extract_uispec_paths(uispec: str) -> set[str]:
    """Extract canonical paths from UI-SPEC. Strips query strings.
    Normalises {session_id}/{id}/{slug} param names to a wildcard for matching.
    """
    raw = set(ENDPOINT_RE.findall(uispec))
    cleaned: set[str] = set()
    for r in raw:
        # Trim trailing punctuation
        r = r.rstrip(",.;:)\"'`")
        # Strip query string
        r = r.split("?", 1)[0]
        # Normalise path params — backend openapi uses the same {name} syntax
        cleaned.add(r)
    return cleaned


def extract_openapi_paths(spec: dict) -> set[str]:
    return set(spec.get("paths", {}).keys())


def _normalise_param_names(path: str) -> str:
    """Reduce {anything} to {x} so UI-SPEC {session_id} matches openapi {session_id}."""
    return re.sub(r"\{[^}]+\}", "{x}", path)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    openapi_path = Path(sys.argv[1])
    uispec_path = Path(sys.argv[2])

    if not openapi_path.exists():
        print(f"ERROR: {openapi_path} not found", file=sys.stderr)
        return 2
    if not uispec_path.exists():
        print(f"ERROR: {uispec_path} not found", file=sys.stderr)
        return 2

    spec = json.loads(openapi_path.read_text())
    uispec = uispec_path.read_text()

    uispec_paths = extract_uispec_paths(uispec)
    openapi_paths = extract_openapi_paths(spec)

    # Normalise both sides for param-name independence.
    uispec_norm = {_normalise_param_names(p) for p in uispec_paths}
    openapi_norm = {_normalise_param_names(p) for p in openapi_paths}

    missing = uispec_norm - openapi_norm
    if missing:
        print("ERROR: UI-SPEC references endpoints not in openapi.json:", file=sys.stderr)
        for p in sorted(missing):
            print(f"  - {p}", file=sys.stderr)
        print("\nEither (a) implement these endpoints, or (b) patch UI-SPEC + reconciliation.md.",
              file=sys.stderr)
        return 1

    print(f"OK: {len(uispec_norm)} UI-SPEC endpoints all present in openapi.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
