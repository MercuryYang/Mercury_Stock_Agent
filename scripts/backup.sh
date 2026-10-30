#!/bin/sh
set -eu
umask 077
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T postgres pg_dump -U trading -d trading -Fc > "backups/trading-$stamp.dump"
test -s "backups/trading-$stamp.dump"
echo "Saved backups/trading-$stamp.dump; encrypt before off-host transfer."
