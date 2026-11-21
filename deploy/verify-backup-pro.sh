#!/usr/bin/env bash
# Restore to a disposable database with no network or production mounts.
set -euo pipefail
backup=${1:?Usage: deploy/verify-backup-pro.sh BACKUP_DIRECTORY}
(cd "$backup" && sha256sum -c SHA256SUMS)
tar -tzf "$backup/data.tar.gz" > /dev/null
if [[ -f "$backup/secrets.tar.gz" ]]; then tar -tzf "$backup/secrets.tar.gz" > /dev/null; fi
instance="$(docker run -d --rm --network none -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=peakcut postgres:16-alpine)"
trap 'docker stop "$instance" >/dev/null' EXIT
for ((n=0; n<60; n++)); do
    if docker exec "$instance" pg_isready -h 127.0.0.1 -U postgres -d peakcut >/dev/null; then break; fi
    sleep 1
done
docker exec -i "$instance" pg_restore --exit-on-error --no-owner --no-acl -U postgres -d peakcut < "$backup/peakcut.dump"
docker exec "$instance" psql -X -v ON_ERROR_STOP=1 -U postgres -d peakcut -c 'SELECT count(*) AS users_restored FROM users;'
