#!/usr/bin/env bash
# Nightly backup: PostgreSQL custom-format dump + private uploads archive, with retention.
# Run as the vision_hrms user from cron, e.g.:  15 1 * * *  /srv/vision_hrms/deploy/backup.sh
# Requires ~/.pgpass (chmod 600) for a read-only backup role — never put passwords in this script.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/vision_hrms}"
PRIVATE_MEDIA_ROOT="${PRIVATE_MEDIA_ROOT:-/var/lib/vision_hrms/private}"
DB_NAME="${DB_NAME:-vision_hrms}"
DB_USER="${DB_BACKUP_USER:-vision_hrms_backup}"
DB_HOST="${DB_HOST:-127.0.0.1}"
RETAIN_DAYS="${RETAIN_DAYS:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"

umask 077
mkdir -p "$BACKUP_DIR"

pg_dump -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -Fc -Z 6 -f "$BACKUP_DIR/db-$STAMP.dump"
tar -C "$(dirname "$PRIVATE_MEDIA_ROOT")" -czf "$BACKUP_DIR/private-$STAMP.tar.gz" "$(basename "$PRIVATE_MEDIA_ROOT")"

# Verify the dump is readable before pruning old copies
pg_restore --list "$BACKUP_DIR/db-$STAMP.dump" > /dev/null

find "$BACKUP_DIR" -type f \( -name 'db-*.dump' -o -name 'private-*.tar.gz' \) -mtime +"$RETAIN_DAYS" -delete
echo "Backup complete: $BACKUP_DIR/db-$STAMP.dump"
# Copy $BACKUP_DIR to off-server, encrypted storage (company policy). Backups contain personal data.
