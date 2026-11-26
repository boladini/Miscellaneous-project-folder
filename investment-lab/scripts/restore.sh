#!/usr/bin/env sh
# Restore into an EMPTY database. Usage: DATABASE_URL=postgresql://.../lab_restore scripts/restore.sh file.dump
set -eu
URL="$(echo "$DATABASE_URL" | sed 's/+psycopg//')"
pg_restore --no-owner --exit-on-error --dbname "$URL" "$1"
echo "restored $1"
