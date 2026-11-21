#!/usr/bin/env bash
# Run from the existing Compose project. Keep the resulting directory private.
set -euo pipefail
umask 077
backup=${1:?Usage: deploy/backup-pro.sh NEW_BACKUP_DIRECTORY}
mkdir -m 700 -- "$backup"
app_running="$(docker compose ps --status running --services app)"
if [[ "$app_running" == "app" ]]; then
    trap 'docker compose start app >&2' EXIT
    docker compose stop app
fi
docker compose exec -T db pg_dump -U peakcut --format=custom --no-owner peakcut > "$backup/peakcut.dump"
docker compose run --rm --no-deps -T --entrypoint sh app -ec 'tar -C "${PEAKCUT_DATA:-/data}" -czf - .' > "$backup/data.tar.gz"
git rev-parse HEAD > "$backup/revision.txt"
if [[ -f .env ]]; then cp -p -- .env "$backup/compose.env"; chmod 600 "$backup/compose.env"; fi
if [[ -d secrets ]]; then tar -czf "$backup/secrets.tar.gz" secrets; fi
(
    cd "$backup"
    files=(peakcut.dump data.tar.gz revision.txt)
    [[ ! -f compose.env ]] || files+=(compose.env)
    [[ ! -f secrets.tar.gz ]] || files+=(secrets.tar.gz)
    sha256sum "${files[@]}" > SHA256SUMS
)
