#!/usr/bin/env bash
# monitor-health.sh — Health-check all cante-cds services + Evolution instance state.
#
# Checks:
#   1. Each service's healthz endpoint responds 200.
#   2. Evolution instance state == "open".
#   3. Evolution instance count ≤ 3 (proliferation early-warning).
#
# Run every 5 min via cron. Exits non-zero on any failure so cron can email.
#
# Usage:
#   ./scripts/monitor-health.sh
#   ALERT_WEBHOOK=https://hooks.slack.com/... ./scripts/monitor-health.sh
#   QUIET=1 ./scripts/monitor-health.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env" 2>/dev/null || true
set +a

EVOLUTION_API_KEY="${EVOLUTION_API_KEY:-}"
EVOLUTION_PORT="${CANTE_EVOLUTION_PORT:-8088}"
API_PORT="${CANTE_API_PORT:-8000}"
INGRESS_PORT="${CANTE_INGRESS_PORT:-8001}"
MOCK_PORT="${CANTE_MOCK_PORT:-9000}"
INSTANCE_NAME="${EVOLUTION_INSTANCE_NAME:-cante35192830041502bc4b}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"
QUIET="${QUIET:-}"

EVOLUTION_URL="http://127.0.0.1:${EVOLUTION_PORT}"
FAILURES=()
OKS=()

# --- helpers -----------------------------------------------------------------
check_http() {
  local label="$1" url="$2"
  if curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 "$url" \
     | grep -qE '^2[0-9][0-9]$'; then
    OKS+=("$label")
  else
    FAILURES+=("$label ($url)")
  fi
}

alert() {
  local msg="$1"
  echo "$msg" >&2
  if [ -n "$ALERT_WEBHOOK" ]; then
    curl -s -X POST -H "Content-Type: application/json" \
      -d "{\"text\":\"$msg\"}" "$ALERT_WEBHOOK" >/dev/null 2>&1 || true
  fi
}

# --- 1. Healthz endpoints ---------------------------------------------------
check_http "ingress"      "http://127.0.0.1:${INGRESS_PORT}/healthz"
check_http "api"          "http://127.0.0.1:${API_PORT}/healthz"
check_http "evolution"    "${EVOLUTION_URL}/"
check_http "mock-backend" "http://127.0.0.1:${MOCK_PORT}/healthz"

# --- 2. Evolution instance state ---------------------------------------------
if [ -n "$EVOLUTION_API_KEY" ]; then
  state_resp="$(curl -s -H "apikey:${EVOLUTION_API_KEY}" \
    --connect-timeout 5 --max-time 10 \
    "${EVOLUTION_URL}/instance/connectionState/${INSTANCE_NAME}" 2>&1)" || true

  instance_state="$(echo "$state_resp" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('instance',{}).get('state','unknown'))
except: print('parse_error')
" 2>/dev/null)" || instance_state="parse_error"

  if [ "$instance_state" = "open" ]; then
    OKS+=("evolution:instance_state=$instance_state")
  else
    FAILURES+=("evolution:instance_state=$instance_state (expected 'open')")
  fi

  # --- 3. Instance proliferation -----------------------------------------------
  instances_json="$(curl -s -H "apikey:${EVOLUTION_API_KEY}" \
    --connect-timeout 5 --max-time 10 \
    "${EVOLUTION_URL}/instance/fetchInstances" 2>&1)" || true

  instance_count="$(echo "$instances_json" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    if isinstance(d,list):
        print(len([i for i in d if isinstance(i,dict)]))
    else:
        print(-1)
except: print(-1)
" 2>/dev/null)" || instance_count=-1

  if [ "$instance_count" -eq -1 ]; then
    FAILURES+=("evolution:instance_count=parse_error")
  elif [ "$instance_count" -le 3 ]; then
    OKS+=("evolution:instance_count=$instance_count")
  else
    FAILURES+=("evolution:instance_count=$instance_count (>3 — possible proliferation)")
  fi
else
  FAILURES+=("evolution:EVOLUTION_API_KEY not set")
fi

# --- Report ------------------------------------------------------------------
if [ ${#FAILURES[@]} -gt 0 ]; then
  alert "[ALERT] cante-cds health checks: ${#FAILURES[@]} FAILURES
  Failed: ${FAILURES[*]}
  OK: ${OKS[*]}"
  exit 1
fi

[ -z "$QUIET" ] && echo "[OK] cante-cds healthy: ${OKS[*]}"
exit 0
