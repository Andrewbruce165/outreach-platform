#!/usr/bin/env bash
# scripts/export-handoff.sh
# Regenerate lovable-handoff/openapi.json + types/api.ts + design-source/ from a running FastAPI.
# Exits 0 on success. Idempotent.
#
# Prerequisites:
#   - Docker (for `docker compose up -d api`)
#   - Node 18+ (for `npx -y openapi-typescript@7`)
#   - Python 3.11 (for scripts/check-uispec-endpoints.py)
#   - jq (system util)
#   - rsync (system util)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HANDOFF_DIR="lovable-handoff"
DESIGN_SRC=".planning/phases/05.1-lovable-ui-v1/design-source"

mkdir -p "$HANDOFF_DIR/types"

echo "==> Booting backend (docker compose up -d db api)..."
docker compose up -d db api

echo "==> Waiting for /openapi.json..."
timeout 60 bash -c 'until curl -sf http://localhost:8000/openapi.json > /dev/null; do sleep 1; done'

echo "==> Exporting openapi.json..."
curl -s http://localhost:8000/openapi.json | jq . > "$HANDOFF_DIR/openapi.json"

echo "==> Generating types/api.ts via openapi-typescript..."
npx -y openapi-typescript@7 "$HANDOFF_DIR/openapi.json" -o "$HANDOFF_DIR/types/api.ts"

echo "==> Checking UI-SPEC endpoint drift..."
python scripts/check-uispec-endpoints.py \
    "$HANDOFF_DIR/openapi.json" \
    ".planning/phases/05.1-lovable-ui-v1/05.1-UI-SPEC.md"

echo "==> Copying design-source/..."
rsync -a --delete "$DESIGN_SRC/" "$HANDOFF_DIR/design-source/"

echo "==> Handoff bundle ready at $HANDOFF_DIR/"
echo "    Files:"
ls -1 "$HANDOFF_DIR/"

# Note: do NOT 'docker compose down' here — caller may want the stack up.
