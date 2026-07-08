#!/usr/bin/env bash
# monitor-evolution-errors.sh — Alert on new ERRORs in Evolution outbound 1:1 messages.
#
# The Evolution API writes to MessageUpdate (NOT Message.status — PENDING lies).
# An ERROR in MessageUpdate means the message was never delivered / never
# acknowledged by the recipient's device. This is the earliest signal of a
# WhatsApp delivery problem (LID addressing, session expiry, etc.).
#
# Run this every 5 minutes via cron. If any ERROR appears in the last 15-minute
# window, it logs them and optionally fires an alert webhook.
#
# Usage:
#   ./scripts/monitor-evolution-errors.sh                     # print to stdout
#   ALERT_WEBHOOK=https://hooks.slack.com/... ./scripts/...   # also POST to webhook
#   QUIET=1 ./scripts/...                                     # only output on ERROR

set -euo pipefail

# Resolve the repo root (works from any cwd and from cron).
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Source DB credentials
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env" 2>/dev/null || true
set +a

# --- config ---------------------------------------------------------------
LOOKBACK_MINUTES="${LOOKBACK_MINUTES:-15}"
THRESHOLD="${THRESHOLD:-0}"         # alert if count > THRESHOLD
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"   # optional — POST JSON here on alert
QUIET="${QUIET:-}"
# --------------------------------------------------------------------------

# Build psql connection string
PGPASSWORD="${POSTGRES_PASSWORD:-}"
export PGPASSWORD
PGHOST="${CANTE_POSTGRES_HOST:-127.0.0.1}"
PGPORT="${CANTE_POSTGRES_PORT:-5432}"
PGUSER="${POSTGRES_USER:-cante}"
PGDB="${POSTGRES_DB:-cante}"

# MessageUpdate has no timestamp column, but joins to Message via messageId.
# Message.messageTimestamp is a Unix epoch (integer).
SQL="
SELECT count(*),
       array_agg(DISTINCT substr(mu.\"remoteJid\",1,30)) AS jids,
       array_agg(DISTINCT mu.\"keyId\") AS key_ids
FROM evolution_api.\"MessageUpdate\" mu
JOIN evolution_api.\"Message\" m ON m.id = mu.\"messageId\"
WHERE mu.status = 'ERROR'
  AND to_timestamp(m.\"messageTimestamp\") > now() - interval '$LOOKBACK_MINUTES minutes';
"

# Run query
result="$(docker exec cante-cds-postgres \
  psql -U "$PGUSER" -d "$PGDB" -tAc "$SQL" 2>&1)" || {
  echo "[ERROR] monitor-evolution-errors: psql failed: $result" >&2
  exit 1
}

# Parse: count | jids | key_ids (pipe-separated from psql)
count=$(echo "$result" | cut -d'|' -f1 | tr -d ' ')
jids=$(echo "$result" | cut -d'|' -f2 | tr -d ' ')
key_ids=$(echo "$result" | cut -d'|' -f3 | tr -d ' ')

if [ -z "$count" ] || [ "$count" -eq 0 ]; then
  [ -z "$QUIET" ] && echo "[OK] No ERRORs in last ${LOOKBACK_MINUTES}min — Evolution outbound healthy."
  exit 0
fi

# --- Alert -----------------------------------------------------------------
msg="[ALERT] Evolution MessageUpdate ERRORs: $count in last ${LOOKBACK_MINUTES}min!
  JIDs: $jids
  Keys: $key_ids"

echo "$msg" >&2

# Optional: POST to a webhook (Slack, Discord, custom)
if [ -n "$ALERT_WEBHOOK" ]; then
  payload="{\"text\":\"$msg\"}"
  curl -s -X POST -H "Content-Type: application/json" -d "$payload" "$ALERT_WEBHOOK" \
    >/dev/null 2>&1 || true
fi

exit 1
