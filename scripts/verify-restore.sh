#!/bin/sh
# Restore into a separately named temporary database; never overwrite trading.
set -eu
test "$#" -eq 1
test -f "$1"
target="restore_check_$(date -u +%Y%m%d%H%M%S)"
docker compose exec -T postgres createdb -U trading "$target"
docker compose exec -T postgres pg_restore -U trading -d "$target" --exit-on-error < "$1"
docker compose exec -T postgres psql -U trading -d "$target" -c 'SELECT version_num FROM alembic_version; SELECT count(*) FROM decisions;'
echo "Verified isolated database $target. Remove it manually after review."
