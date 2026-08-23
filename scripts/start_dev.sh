#!/usr/bin/env bash
#
# Start EdgeBetter for local development: hot reload, live-mounted source, Vite dev server.
# This is the mode to use on the MacBook while working on the code.
#
#   ./scripts/start_dev.sh                start (building anything that changed)
#   ./scripts/start_dev.sh --with-worker  also run the scheduler (see below)
#   ./scripts/start_dev.sh --rebuild      force a clean image rebuild
#   ./scripts/start_dev.sh --logs         start, then follow the logs
#   ./scripts/start_dev.sh --down         stop everything (data is kept)
#
# The scheduler is NOT started by default, because it polls a paid API on a shared key.
# Both machines authenticate as the same account, but each decides what it can afford by
# reading its own api_usage table, so neither can see the other's spending: run the worker
# in both places and the month's quota goes twice as fast with nothing reporting it.
#
# The live host is the one that should be collecting. This laptop gets its data with
# ./scripts/pull_remote.sh, which is also why a local scheduler would fight the copy it
# pulls down. Use --with-worker when you are deliberately testing an ingest.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

REBUILD=0; FOLLOW=0; DOWN=0; WITH_WORKER=0
for arg in "$@"; do
  case "$arg" in
    --rebuild)     REBUILD=1 ;;
    --logs)        FOLLOW=1 ;;
    --down)        DOWN=1 ;;
    --with-worker) WITH_WORKER=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
if [[ $WITH_WORKER -eq 1 ]]; then
  c_ylw "Starting the scheduler too -- this machine will poll the paid API."
  docker compose "${DEV_FILES[@]}" up -d --build
else
  docker compose "${DEV_FILES[@]}" up -d --build db api web
  # Stop it if a previous run left it up, so the default is the same however you got here.
  if docker compose "${DEV_FILES[@]}" ps --status running --services 2>/dev/null | grep -qx worker; then
    docker compose "${DEV_FILES[@]}" stop worker >/dev/null
    c_dim "Stopped the scheduler left running from an earlier session."
  fi
fi

c_dim "Waiting for the api to report healthy..."
for _ in $(seq 1 60); do
  status="$(docker compose "${DEV_FILES[@]}" ps --format json api 2>/dev/null | grep -o '"Health":"[a-z]*"' | head -1 || true)"
  [[ "$status" == *healthy* ]] && break
  sleep 2
done

c_grn "Development stack is up."
print_urls
c_dim "Source is live-mounted: edits to backend/ and frontend/ reload automatically."
if [[ $WITH_WORKER -eq 1 ]]; then
  c_ylw "Scheduler running: this machine is spending from the shared API quota."
else
  c_dim "Scheduler off, so nothing here spends API credits. --with-worker to run it."
  c_dim "Fresh data:  ./scripts/pull_remote.sh"
fi
c_dim "Logs:  docker compose logs -f"

[[ $FOLLOW -eq 1 ]] && docker compose "${DEV_FILES[@]}" logs -f
exit 0
