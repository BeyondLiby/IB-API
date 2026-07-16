#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This launcher is for macOS. On Windows, use .\\open_inventory_planner.ps1."
  exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
HOST="127.0.0.1"
PORT="${1:-8766}"
REFRESH_MINUTES="${REFRESH_MINUTES:-1}"
CLIENT_ID="${IB_CLIENT_ID:-7316}"
LABEL="com.antony.ib-api.inventory-planner-${PORT}"
URL="http://${HOST}:${PORT}/sell_side_inventory_planner.html"
HEALTH_URL="http://${HOST}:${PORT}/inventory-planner-defaults.json"
LOG="/tmp/ib_api_inventory_planner_${PORT}.log"
ERROR_LOG="/tmp/ib_api_inventory_planner_${PORT}.error.log"
PIDFILE="/tmp/ib_api_inventory_planner_${PORT}.pid"

planner_ready() {
  local body
  body="$(curl -fsS --max-time 1 "${HEALTH_URL}" 2>/dev/null || true)"
  [[ "${body}" == *'"products"'* && "${body}" == *'"defaults"'* ]]
}

planner_python_usable() {
  [[ -x "$1" ]] && "$1" -c "import pandas, ib_async" >/dev/null 2>&1
}

if launchctl print "gui/${UID}/${LABEL}" >/dev/null 2>&1; then
  if planner_ready; then
    echo "Inventory planner is already managed by launchd:"
    echo "${URL}"
    open "${URL}"
    exit 0
  fi
  echo "Removing stale planner launchd job before restart..."
  launchctl remove "${LABEL}" >/dev/null 2>&1 || true
fi

if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port ${PORT} is already in use. Stop the existing planner first:"
  echo "./stop_inventory_planner.sh ${PORT}"
  exit 1
fi

if [[ -n "${PLANNER_PYTHON:-}" ]] && planner_python_usable "${PLANNER_PYTHON}"; then
  PYTHON="${PLANNER_PYTHON}"
elif [[ -n "${CONDA_PREFIX:-}" ]] && planner_python_usable "${CONDA_PREFIX}/bin/python"; then
  PYTHON="${CONDA_PREFIX}/bin/python"
elif planner_python_usable "/opt/homebrew/Caskroom/miniconda/base/envs/pylib/bin/python"; then
  PYTHON="/opt/homebrew/Caskroom/miniconda/base/envs/pylib/bin/python"
elif planner_python_usable "${HOME}/miniconda3/envs/ib/bin/python"; then
  PYTHON="${HOME}/miniconda3/envs/ib/bin/python"
elif planner_python_usable "${HOME}/anaconda3/envs/ib/bin/python"; then
  PYTHON="${HOME}/anaconda3/envs/ib/bin/python"
else
  echo "Cannot find a Python environment with pandas and ib_async."
  echo "Set PLANNER_PYTHON, for example:"
  echo "PLANNER_PYTHON=/path/to/python ./open_inventory_planner.sh"
  exit 1
fi

printf -v LAUNCH_COMMAND \
  'cd %q && exec %q %q --refresh-mode scheduled --repeat-minutes %q --serve-planner --planner-host %q --planner-port %q --client-id %q' \
  "${ROOT}" "${PYTHON}" "${ROOT}/refresh_inventory_data.py" \
  "${REFRESH_MINUTES}" "${HOST}" "${PORT}" "${CLIENT_ID}"

rm -f "${PIDFILE}"
echo "Starting launchd planner with US/Eastern date-aware refresh every ${REFRESH_MINUTES} minute(s)..."
echo "Log: ${LOG}"
launchctl submit -l "${LABEL}" -o "${LOG}" -e "${ERROR_LOG}" -- /bin/zsh -lc "${LAUNCH_COMMAND}"

for _ in $(seq 1 50); do
  if planner_ready; then
    echo "Open:"
    echo "${URL}"
    open "${URL}"
    exit 0
  fi
  sleep 0.2
done

echo "Planner did not start cleanly. Last log lines:"
tail -40 "${LOG}" || true
tail -40 "${ERROR_LOG}" || true
launchctl remove "${LABEL}" >/dev/null 2>&1 || true
exit 1
