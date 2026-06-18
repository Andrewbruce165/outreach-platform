#!/bin/bash
# Daily backup for tg-outreach Postgres — actively-developed schema + customer data.
# Retention: 14 days local; consider adding to weekly_backup.sh for 31-day rotation.
# Installed in root's crontab — see `crontab -l`.
#
# Triggered by the 2026-05-26 incident where `docker compose run --rm api pytest`
# executed conftest's DROP SCHEMA against prod and there was no backup for recovery.
# See /root/.claude/projects/-root/memory/feedback_pytest_drop_schema_prod.md.

set -euo pipefail

DEST_DIR="/root/backups/tg-outreach"
RETENTION_DAYS=14
TS=$(date '+%Y%m%d_%H%M%S')

mkdir -p "$DEST_DIR"
DEST_FILE="$DEST_DIR/outreach_${TS}.sql.gz"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

log "pg_dump outreach_platform -> $DEST_FILE"
docker exec outreach-platform-db pg_dump \
    -U outreach_user -d outreach_platform --no-owner --clean --if-exists \
    | gzip > "$DEST_FILE"

# Sanity check: refuse to keep an empty/tiny dump (signals broken pg_dump).
SIZE=$(stat -c%s "$DEST_FILE")
if [ "$SIZE" -lt 1024 ]; then
    log "ERROR: backup file is suspiciously small (${SIZE}b) — refusing to rotate"
    log "keeping $DEST_FILE for inspection; not deleting older backups"
    exit 1
fi

log "done ($(du -h "$DEST_FILE" | cut -f1))"

log "rotation: removing files older than ${RETENTION_DAYS} days"
find "$DEST_DIR" -maxdepth 1 -type f -name 'outreach_*.sql.gz' -mtime +$RETENTION_DAYS -print -delete

log "daily outreach backup complete"
