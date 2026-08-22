#!/usr/bin/env bash
#
# Start EdgeBetter for local development: hot reload, live-mounted source, Vite dev server.
# This is the mode to use on the MacBook while working on the code.
#
#   ./scripts/start_dev.sh              start (building anything that changed)
#   ./scripts/start_dev.sh --rebuild    force a clean image rebuild
#   ./scripts/start_dev.sh --logs       start, then follow the logs
#   ./scripts/start_dev.sh --down       stop everything (data is kept)

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

REBUILD=0; FOLLOW=0; DOWN=0
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=1 ;;
    --logs)    FOLLOW=1 ;;
    --down)    DOWN=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $arg (try --help)" ;;
  esac
done

require_docker
require_env

if [[ $DOWN -eq 1 ]]; then
  c_ylw "Stopping development stack (the database volume is left untouched)..."
  docker compose "${DEV_FILES[@]}" down
  c_grn "Stopped. Your data is still in the pgdata volume."
  exit 0
fi

c_ylw "Starting EdgeBetter [development]"
if [[ $REBUILD -eq 1 ]]; then
  docker compose "${DEV_FILES[@]}" build --no-cache
fi
docker compose "${DEV_FILES[@]}" up -d --build

c_dim "Waiting for the api to report healthy..."
for _ in $(seq 1 60); do
  status="$(docker compose "${DEV_FILES[@]}" ps --format json api 2>/dev/null | grep -o '"Health":"[a-z]*"' | head -1 || true)"
  [[ "$status" == *healthy* ]] && break
  sleep 2
done

c_grn "Development stack is up."
print_urls
c_dim "Source is live-mounted: edits to backend/ and frontend/ reload automatically."
c_dim "Logs:  docker compose logs -f"

[[ $FOLLOW -eq 1 ]] && docker compose "${DEV_FILES[@]}" logs -f
exit 0
