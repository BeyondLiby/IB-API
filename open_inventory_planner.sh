#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST="127.0.0.1"
PORT="${1:-8766}"
URL="http://${HOST}:${PORT}/sell_side_inventory_planner.html"
LOG="/tmp/ib_api_inventory_planner_${PORT}.log"

if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Inventory planner server is already running:"
  echo "${URL}"
  open "${URL}"
  exit 0
fi

echo "Starting inventory planner server..."
echo "Log: ${LOG}"

nohup conda run -n ib python -u -m target_treasury_monitor_clean.cli serve-inventory-planner \
  --directory . \
  --host "${HOST}" \
  --port "${PORT}" \
  >"${LOG}" 2>&1 &

PID="$!"
for _ in $(seq 1 30); do
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Open:"
    echo "${URL}"
    open "${URL}"
    exit 0
  fi
  sleep 0.2
done

echo "Server did not start cleanly. Last log lines:"
tail -40 "${LOG}" || true
echo "Background PID: ${PID}"
exit 1
