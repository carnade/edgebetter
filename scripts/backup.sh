#!/usr/bin/env bash
#
# Dump the EdgeBetter database to a compressed file.
#
#   ./scripts/backup.sh                 write to ./backups/
#   ./scripts/backup.sh /path/to/dir    write somewhere else
#   KEEP=30 ./scripts/backup.sh         keep the 30 most recent (default 14)
#
# Why this exists: pgdata is a *named Docker volume*, not a folder in this repo. Cloning
# the repo somewhere else gives you the code and an empty database. This dump is the only
# thing that moves your collected data between machines -- and prop lines in particular
# cannot be re-fetched, since there is no historical feed for them.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

case "${1-}" in
  -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_docker
require_env

BACKUP_DIR="${1:-$REPO_ROOT/backups}"
KEEP="${KEEP:-14}"
mkdir -p "$BACKUP_DIR"

PG_USER="$(env_value POSTGRES_USER edgebetter)"
PG_DB="$(env_value POSTGRES_DB edgebetter)"

if ! docker compose "${DEV_FILES[@]}" ps --status running --services 2>/dev/null | grep -qx db; then
  die "the db container is not running -- start the stack first"
fi

STAMP="$(date +%Y-%m-%d_%H%M%S)"
OUT="$BACKUP_DIR/edgebetter-${STAMP}.sql.gz"
TMP="${OUT}.partial"

c_ylw "Dumping ${PG_DB} ..."
# --clean --if-exists makes the dump self-contained: restoring it over an existing database
# drops the old objects first rather than colliding with them.
if ! docker compose "${DEV_FILES[@]}" exec -T db \
      pg_dump -U "$PG_USER" -d "$PG_DB" --clean --if-exists | gzip > "$TMP"; then
  rm -f "$TMP"
  die "pg_dump failed -- nothing was written"
fi

# A dump that fails midway still exits 0 through a pipe, so the result is verified rather
# than trusted: it must be valid gzip and must actually contain tables.
if ! gzip -t "$TMP" 2>/dev/null; then
  rm -f "$TMP"; die "the dump is not valid gzip -- discarded"
fi

# pipefail is deliberately relaxed for this count. `grep -c` reads the whole stream, but
# any early-exiting reader here would SIGPIPE gunzip and make a perfectly good dump look
# like a failed one -- which is exactly what an earlier version of this check did.
set +o pipefail
TABLES="$(gunzip -c "$TMP" | grep -c '^CREATE TABLE' || true)"
set -o pipefail
if [[ "${TABLES:-0}" -lt 1 ]]; then
  rm -f "$TMP"; die "the dump contains no tables -- discarded"
fi

mv "$TMP" "$OUT"
SIZE="$(du -h "$OUT" | cut -f1)"
c_grn "Wrote $OUT  (${SIZE}, ${TABLES} tables)"

# Prune old backups, newest kept. Written as a read loop rather than `mapfile` because
# macOS still ships bash 3.2, where mapfile does not exist.
if [[ "$KEEP" -gt 0 ]]; then
  pruned=0
  while IFS= read -r stale; do
    [[ -n "$stale" ]] || continue
    rm -f "$stale"
    pruned=$((pruned + 1))
  done < <(ls -1t "$BACKUP_DIR"/edgebetter-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) || true)
  [[ $pruned -gt 0 ]] && c_dim "Pruned ${pruned} backup(s), keeping the newest ${KEEP}."
fi

c_dim "Restore with:  ./scripts/restore.sh $OUT"
