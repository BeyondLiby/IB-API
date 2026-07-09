#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST="127.0.0.1"
PORT="${1:-8766}"
REFRESH_MINUTES="${REFRESH_MINUTES:-3}"
CLIENT_ID="${IB_CLIENT_ID:-7316}"
URL="http://${HOST}:${PORT}/sell_side_inventory_planner.html"
LOG="/tmp/ib_api_inventory_planner_${PORT}.log"
PIDFILE="/tmp/ib_api_inventory_planner_${PORT}.pid"

if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Inventory planner server is already running:"
  echo "${URL}"
  open "${URL}"
  exit 0
fi

echo "Starting inventory planner server with auto fast refresh every ${REFRESH_MINUTES} minutes..."
echo "Log: ${LOG}"

nohup conda run -n ib python -u refresh_inventory_data.py \
  --refresh-mode fast \
  --repeat-minutes "${REFRESH_MINUTES}" \
  --serve-planner \
  --open-browser \
  --planner-host "${HOST}" \
  --planner-port "${PORT}" \
  --client-id "${CLIENT_ID}" \
  >"${LOG}" 2>&1 &

PID="$!"
echo "${PID}" >"${PIDFILE}"
for _ in $(seq 1 30); do
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Open:"
    echo "${URL}"
    exit 0
  fi
  sleep 0.2
done

echo "Server did not start cleanly. Last log lines:"
tail -40 "${LOG}" || true
echo "Background PID: ${PID}"
exit 1
