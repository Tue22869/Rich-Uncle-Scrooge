#!/usr/bin/env bash
# Atomic backup of the SmartFinances SQLite database.
# Usage: place in cron, e.g.  0 */6 * * * /path/to/scripts/backup_db.sh
# Env overrides:  DB_PATH, BACKUP_DIR, RETENTION_DAYS

set -euo pipefail

DB_PATH="${DB_PATH:-$HOME/Rich-Uncle-Scrooge/data/smartfinances.db}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/scrooge}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

LOG_FILE="$BACKUP_DIR/backup.log"
timestamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/smartfinances-${timestamp}.db"

log() {
    echo "$(date +'%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

if [[ ! -f "$DB_PATH" ]]; then
    log "ERROR db_not_found path=$DB_PATH"
    echo "Database file not found: $DB_PATH" >&2
    exit 1
fi

# Prefer sqlite3 .backup — atomic snapshot, safe with a live writer.
# Fall back to cp only if the binary is missing (better than nothing).
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_PATH" ".backup '$target'"
    method="sqlite3"
else
    cp "$DB_PATH" "$target"
    method="cp"
    log "WARN sqlite3_missing fallback=cp"
fi

gzip -- "$target"
final="${target}.gz"
size="$(wc -c < "$final" | tr -d ' ')"

# Rotate: drop backups older than RETENTION_DAYS days.
find "$BACKUP_DIR" -maxdepth 1 -name 'smartfinances-*.db.gz' -mtime "+${RETENTION_DAYS}" -delete

log "OK method=$method file=$(basename "$final") size_bytes=$size"
