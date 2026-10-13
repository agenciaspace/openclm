#!/usr/bin/env bash
# Run on the OpenCLM host. No credentials or contract contents are printed.
set -euo pipefail
umask 077
backup_dir="${OPENCLM_BACKUP_DIR:-/root/.local/share/openclm/backups}"
container_name="${OPENCLM_POSTGRES_CONTAINER:-openclm-postgres}"
mkdir -p "$backup_dir"
backup_file="$backup_dir/openclm-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker exec "$container_name" pg_dump -U openclm -d openclm -Fc > "$backup_file.tmp"
docker exec -i "$container_name" pg_restore --list < "$backup_file.tmp" > /dev/null
mv "$backup_file.tmp" "$backup_file"
sha256sum "$backup_file" > "$backup_file.sha256"
# Only completed backups produced by this script are eligible for retention cleanup.
find "$backup_dir" -maxdepth 1 -name 'openclm-*.dump*' -type f -mtime +30 -delete
printf 'OpenCLM backup verified: %s\n' "$backup_file"
# Optional private off-host R2 copy. A failed upload fails the systemd job.
if [ -n "${OPENCLM_BACKUP_BUCKET:-}" ]; then
  : "${OPENCLM_WRANGLER:?Set OPENCLM_WRANGLER for offsite backup}"
  bundle="${backup_file%.dump}.tar.gz"
  : "${OPENCLM_CONFIG_FILE:?Set OPENCLM_CONFIG_FILE for recovery secrets}"
  tar -czf "$bundle" -C "$backup_dir" "$(basename "$backup_file")" "$(basename "$backup_file.sha256")" -C "$(dirname "$OPENCLM_CONFIG_FILE")" "$(basename "$OPENCLM_CONFIG_FILE")"
  "$OPENCLM_WRANGLER" r2 object put "$OPENCLM_BACKUP_BUCKET/daily/$(basename "$bundle")" --file "$bundle" --remote > /dev/null
  find "$backup_dir" -maxdepth 1 -name 'openclm-*.tar.gz' -type f -mtime +30 -delete
  printf 'OpenCLM offsite backup uploaded\n'
fi
