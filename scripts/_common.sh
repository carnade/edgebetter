# Shared setup for the EdgeBetter scripts. Sourced, not run.

set -euo pipefail

# Every script works from any directory by resolving the repo root from its own location.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEV_FILES=(-f docker-compose.yml)
PROD_FILES=(-f docker-compose.yml -f docker-compose.prod.yml)

c_red()  { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
c_dim()  { printf '\033[2m%s\033[0m\n' "$*"; }

die() { c_red "error: $*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"
  docker info >/dev/null 2>&1 || die "the docker daemon is not running -- start Docker and try again"
}

require_env() {
  if [[ ! -f .env ]]; then
    c_ylw "No .env found."
    if [[ -f .env.example ]]; then
      c_dim "Copy it and add your Odds API key:"
      c_dim "    cp .env.example .env"
    fi
    die ".env is required (it holds the database password and the Odds API key)"
  fi
}

# Reads a value from .env without sourcing it, so odd characters in the API key or a
# password cannot be executed as shell.
#
# Surrounding whitespace and matching quotes are stripped, because KEY="value" and
# KEY = value are both things people write in a .env and neither means to include the
# quotes or the spaces in the value.
env_value() {
  local key="$1" default="${2-}" line value
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" .env 2>/dev/null | tail -1 || true)"
  if [[ -z "$line" ]]; then
    printf '%s' "$default"
    return
  fi
  value="${line#*=}"
  value="${value#"${value%%[![:space:]]*}"}"   # leading whitespace
  value="${value%"${value##*[![:space:]]}"}"   # trailing whitespace
  if [[ ${#value} -ge 2 ]]; then
    case "$value" in
      \"*\") value="${value:1:${#value}-2}" ;;
      \'*\') value="${value:1:${#value}-2}" ;;
    esac
  fi
  printf '%s' "$value"
}

print_urls() {
  local web_port api_port api_bind
  web_port="$(env_value WEB_PORT 5174)"
  api_port="$(env_value API_PORT 8001)"
  api_bind="$(env_value API_BIND 127.0.0.1)"
  echo
  c_grn "  Web UI    http://localhost:${web_port}"
  c_dim  "  API       http://localhost:${api_port}/api  (bound to ${api_bind})"
  if [[ "$(env_value WEB_BIND 0.0.0.0)" == "0.0.0.0" ]]; then
    local lan
    lan="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)"
    [[ -n "$lan" ]] && c_dim "  On the LAN  http://${lan}:${web_port}"
  fi
  echo
}
