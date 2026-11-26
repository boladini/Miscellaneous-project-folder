#!/usr/bin/env sh
# Daily logical backup. Usage: DATABASE_URL=postgresql://... scripts/backup.sh [outdir]
set -eu
OUT="${1:-./backups}"; mkdir -p "$OUT"
URL="$(echo "$DATABASE_URL" | sed 's/+psycopg//')"
F="$OUT/lab-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner "$URL" > "$F"
echo "$F"
