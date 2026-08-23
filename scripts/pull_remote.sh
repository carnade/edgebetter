#!/usr/bin/env bash
#
# Copy the live database from the NAS down to this machine for development.
#
#   ./scripts/pull_remote.sh              pull and restore over the local database
#   ./scripts/pull_remote.sh --check      test the connection, transfer nothing
#   ./scripts/pull_remote.sh --keep       download the dump but do not restore it
#   ./scripts/pull_remote.sh --force      skip the confirmation prompt
#
# Why a copy rather than connecting to it directly:
#
# backend/entrypoint.sh runs `alembic upgrade head` on every boot. Point a development
# stack at the live database and simply starting it, while on a branch carrying a new
# migration, rewrites the production schema before you have typed a command. Development
# also means experiments, backfills and the occasional destructive CLI run, none of which
# belong anywhere near the only copy of a season's prop lines -- which cannot be re-fetched,
# because no historical feed for them exists.
#
# So data flows one way, NAS to laptop. Code flows the other way through git.
#
# Requires REMOTE_HOST (and optionally REMOTE_DIR) in .env, and SSH access to the NAS.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

CHECK=0; KEEP=0; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --keep)  KEEP=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $arg (try --help)" ;;
  esac
done

require_docker
require_env

REMOTE_HOST="$(env_value REMOTE_HOST)"
REMOTE_DIR="$(env_value REMOTE_DIR /share/Container/edgebetter)"
[[ -n "$REMOTE_HOST" ]] || die "REMOTE_HOST is not set in .env (e.g. REMOTE_HOST=andreas@nas.local)"

command -v ssh >/dev/null 2>&1 || die "ssh is not installed"

# Checked here rather than left to restore.sh at the end, so a stack that is down costs a
# message instead of a completed download. The natural instinct is to refresh the data
# before starting anything, and that order does not work: the restore needs a database to
# restore into.
if ! docker compose "${DEV_FILES[@]}" ps --status running --services 2>/dev/null | grep -qx db; then
  c_red "The local database is not running, so there is nowhere to restore to."
  c_dim "  Start the stack first, then pull:"
  c_dim "      ./scripts/start_dev.sh"
  c_dim "      ./scripts/pull_remote.sh"
  exit 1
fi

c_dim "Remote: $REMOTE_HOST:$REMOTE_DIR"

# BatchMode keeps this from hanging on a password prompt inside a script; if key auth is
# not set up, failing fast with a clear message beats waiting forever.
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_HOST" true 2>/dev/null; then
  c_red "Cannot reach $REMOTE_HOST over SSH with key authentication."
  c_dim "  Enable SSH on the QNAP, then from this machine:"
  c_dim "      ssh-copy-id $REMOTE_HOST"
  c_dim "  Test it with:  ssh $REMOTE_HOST true"
  exit 1
fi

REMOTE_CHECK="cd '$REMOTE_DIR' 2>/dev/null && docker compose ps --status running --services 2>/dev/null | grep -qx db"
if ! ssh "$REMOTE_HOST" "$REMOTE_CHECK"; then
  die "no running 'db' service found in $REMOTE_DIR on $REMOTE_HOST -- set REMOTE_DIR, or start the stack there"
fi
c_grn "Connection OK and the remote database is running."

if [[ $CHECK -eq 1 ]]; then
  c_dim "--check requested, nothing transferred."
  exit 0
fi

PG_USER="$(env_value POSTGRES_USER edgebetter)"
PG_DB="$(env_value POSTGRES_DB edgebetter)"
mkdir -p "$REPO_ROOT/backups"
OUT="$REPO_ROOT/backups/remote-$(date +%Y-%m-%d_%H%M%S).sql.gz"
TMP="${OUT}.partial"

c_ylw "Dumping the remote database and streaming it here..."
# Dumped inside the remote container and gzipped on that side, so only compressed bytes
# cross the network. The remote database is only ever read.
#
# Deliberately plain ssh with a command rather than scp or rsync. OpenSSH 9.0 switched scp
# to the SFTP protocol, and QTS does not enable the SFTP subsystem by default, so scp fails
# there with "subsystem request failed on channel 0". Piping over ssh needs no subsystem
# and works regardless.
if ! ssh "$REMOTE_HOST" \
      "cd '$REMOTE_DIR' && docker compose exec -T db pg_dump -U '$PG_USER' -d '$PG_DB' --clean --if-exists | gzip" \
      > "$TMP"; then
  rm -f "$TMP"
  die "remote dump failed -- nothing was written"
fi

gzip -t "$TMP" 2>/dev/null || { rm -f "$TMP"; die "the downloaded dump is not valid gzip -- discarded"; }
set +o pipefail
TABLES="$(gunzip -c "$TMP" | grep -c '^CREATE TABLE' || true)"
set -o pipefail
[[ "${TABLES:-0}" -ge 1 ]] || { rm -f "$TMP"; die "the downloaded dump contains no tables -- discarded"; }

mv "$TMP" "$OUT"
c_grn "Downloaded $(basename "$OUT")  ($(du -h "$OUT" | cut -f1), ${TABLES} tables)"

# Prune old pulls. Named remote-* rather than edgebetter-* so they stay distinguishable
# from local backups, and pruned here so backup.sh never deletes a pull it did not make.
KEEP_PULLS="${KEEP_PULLS:-5}"
pruned=0
while IFS= read -r stale; do
  [[ -n "$stale" ]] || continue
  rm -f "$stale"; pruned=$((pruned + 1))
done < <(ls -1t "$REPO_ROOT/backups"/remote-*.sql.gz 2>/dev/null | tail -n +$((KEEP_PULLS + 1)) || true)
[[ $pruned -gt 0 ]] && c_dim "Pruned ${pruned} older pull(s), keeping the newest ${KEEP_PULLS}."

if [[ $KEEP -eq 1 ]]; then
  c_dim "--keep requested; restore it when you want with:"
  c_dim "    ./scripts/restore.sh $OUT"
  exit 0
fi

# restore.sh already stops the services holding connections, prompts before replacing the
# database, and verifies the result. No reason to reimplement any of that here.
if [[ $FORCE -eq 1 ]]; then
  exec "$REPO_ROOT/scripts/restore.sh" --force "$OUT"
fi
exec "$REPO_ROOT/scripts/restore.sh" "$OUT"
