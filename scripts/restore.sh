#!/usr/bin/env bash
#
# Restore the EdgeBetter database from a backup produced by ./scripts/backup.sh.
#
#   ./scripts/restore.sh                     restore the newest file in ./backups/
#   ./scripts/restore.sh path/to/dump.sql.gz restore a specific file
#   ./scripts/restore.sh --force <file>       skip the confirmation prompt
#
# THIS REPLACES THE CURRENT DATABASE. Everything presently stored is dropped and replaced
# by the contents of the dump.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

FORCE=0; FILE=""
for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         FILE="$arg" ;;
  esac
done

require_docker
require_env

if [[ -z "$FILE" ]]; then
  FILE="$(ls -1t "$REPO_ROOT/backups"/edgebetter-*.sql.gz 2>/dev/null | head -1 || true)"
  [[ -n "$FILE" ]] || die "no backups found in ./backups -- pass a file explicitly"
  c_dim "Using the newest backup: $FILE"
fi
[[ -f "$FILE" ]] || die "no such file: $FILE"
gzip -t "$FILE" 2>/dev/null || die "$FILE is not a valid gzip file"

PG_USER="$(env_value POSTGRES_USER edgebetter)"
PG_DB="$(env_value POSTGRES_DB edgebetter)"

docker compose "${DEV_FILES[@]}" ps --status running --services 2>/dev/null | grep -qx db \
  || die "the db container is not running -- start the stack first"

if [[ $FORCE -ne 1 ]]; then
  echo
  c_red "This REPLACES the current contents of database '${PG_DB}'."
  c_dim "  from: $FILE"
  c_dim "  size: $(du -h "$FILE" | cut -f1)"
  echo
  read -r -p "Type 'yes' to continue: " reply
  [[ "$reply" == "yes" ]] || { c_ylw "Aborted. Nothing was changed."; exit 1; }
fi

# The api and worker hold open connections, and DROP waits on them. Stopping them first is
# the difference between a clean restore and one that hangs on a lock.
STOPPED=()
for svc in api worker web; do
  if docker compose "${DEV_FILES[@]}" ps --status running --services 2>/dev/null | grep -qx "$svc"; then
    STOPPED+=("$svc")
  fi
done
if [[ ${#STOPPED[@]} -gt 0 ]]; then
  c_dim "Stopping ${STOPPED[*]} while the database is replaced..."
  docker compose "${DEV_FILES[@]}" stop "${STOPPED[@]}" >/dev/null
fi

restart_stopped() {
  if [[ ${#STOPPED[@]} -gt 0 ]]; then
    c_dim "Restarting ${STOPPED[*]}..."
    docker compose "${DEV_FILES[@]}" start "${STOPPED[@]}" >/dev/null || true
  fi
}
trap restart_stopped EXIT

c_ylw "Restoring ${PG_DB} from $(basename "$FILE") ..."
# ON_ERROR_STOP makes psql fail loudly instead of half-restoring and exiting 0.
if ! gunzip -c "$FILE" | docker compose "${DEV_FILES[@]}" exec -T db \
      psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 --quiet >/dev/null; then
  die "restore failed -- the database may be in a partial state, re-run with a good dump"
fi

TABLES="$(docker compose "${DEV_FILES[@]}" exec -T db psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '[:space:]')"
c_grn "Restored. ${TABLES} tables present."
