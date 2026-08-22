#!/usr/bin/env bash
#
# Start EdgeBetter in production mode: static frontend behind nginx, no hot reload,
# everything set to restart after a reboot. This is the mode for the NAS.
#
#   ./scripts/start_prod.sh              start (building anything that changed)
#   ./scripts/start_prod.sh --rebuild    force a clean image rebuild
#   ./scripts/start_prod.sh --logs       start, then follow the logs
#   ./scripts/start_prod.sh --down       stop everything (data is kept)
#
# The frontend is compiled into a static bundle here, so a code change needs a rebuild --
# `git pull && ./scripts/start_prod.sh` is the update path.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

REBUILD=0; FOLLOW=0; DOWN=0
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=1 ;;
    --logs)    FOLLOW=1 ;;
    --down)    DOWN=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $arg (try --help)" ;;
  esac
done

require_docker
require_env

if [[ $DOWN -eq 1 ]]; then
  c_ylw "Stopping production stack (the database volume is left untouched)..."
  docker compose "${PROD_FILES[@]}" down
  c_grn "Stopped. Your data is still in the pgdata volume."
  exit 0
fi

# The scheduler is the only thing collecting prop lines, and nothing can backfill them.
if [[ -z "$(env_value THE_ODDS_API_KEY)" ]]; then
  c_ylw "Warning: THE_ODDS_API_KEY is empty in .env."
  c_ylw "Stats and models will work, but no odds or prop lines will be collected."
fi

if [[ "$(env_value POSTGRES_PASSWORD)" == "edgebetter" ]]; then
  c_ylw "Warning: POSTGRES_PASSWORD is still the default. Change it in .env before"
  c_ylw "exposing this host to anything you do not control."
fi

c_ylw "Starting EdgeBetter [production]"
c_dim "Building the frontend bundle -- the first build on a NAS can take several minutes."
if [[ $REBUILD -eq 1 ]]; then
  docker compose "${PROD_FILES[@]}" build --no-cache
fi
docker compose "${PROD_FILES[@]}" up -d --build

c_dim "Waiting for the api to report healthy..."
for _ in $(seq 1 90); do
  status="$(docker compose "${PROD_FILES[@]}" ps --format json api 2>/dev/null | grep -o '"Health":"[a-z]*"' | head -1 || true)"
  [[ "$status" == *healthy* ]] && break
  sleep 2
done

c_grn "Production stack is up."
print_urls
c_dim "Containers restart automatically after a reboot."
c_dim "Update:  git pull && ./scripts/start_prod.sh"
c_dim "Back up: ./scripts/backup.sh"

[[ $FOLLOW -eq 1 ]] && docker compose "${PROD_FILES[@]}" logs -f
exit 0
