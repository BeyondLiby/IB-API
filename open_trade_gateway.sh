#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This launcher is for macOS. Run open_trade_gateway.py directly on other systems."
  exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-paper}"
ACCOUNT="${IB_ACCOUNT:-}"
HOST="${IB_HOST:-127.0.0.1}"
CLIENT_ID="${IB_TRADE_CLIENT_ID:-7321}"
MAX_ORDER_QUANTITY="${IB_MAX_ORDER_QUANTITY:-10}"
MAX_PREVIEW_QUANTITY="${IB_MAX_PREVIEW_QUANTITY:-100}"
MINIMUM_RESERVE_FUNDS="${IB_MINIMUM_RESERVE_FUNDS:-1000}"

if [[ "${MODE}" != "paper" && "${MODE}" != "live" ]]; then
  echo "Usage: $0 [paper|live]"
  exit 1
fi
if [[ -z "${ACCOUNT}" ]]; then
  echo "Set IB_ACCOUNT explicitly before starting the trade gateway."
  exit 1
fi

planner_python_usable() {
  [[ -x "$1" ]] && "$1" -c "import ib_async" >/dev/null 2>&1
}

if [[ -n "${PLANNER_PYTHON:-}" ]] && planner_python_usable "${PLANNER_PYTHON}"; then
  PYTHON="${PLANNER_PYTHON}"
elif [[ -n "${CONDA_PREFIX:-}" ]] && planner_python_usable "${CONDA_PREFIX}/bin/python"; then
  PYTHON="${CONDA_PREFIX}/bin/python"
elif planner_python_usable "/opt/homebrew/Caskroom/miniconda/base/envs/pylib/bin/python"; then
  PYTHON="/opt/homebrew/Caskroom/miniconda/base/envs/pylib/bin/python"
elif planner_python_usable "${HOME}/miniconda3/envs/ib/bin/python"; then
  PYTHON="${HOME}/miniconda3/envs/ib/bin/python"
else
  echo "Cannot find a Python environment with ib_async. Set PLANNER_PYTHON."
  exit 1
fi

if [[ "${MODE}" == "paper" ]]; then
  IB_PORT="${IB_PORT:-4002}"
  EXPECTED="PAPER ${ACCOUNT}"
else
  IB_PORT="${IB_PORT:-4001}"
  EXPECTED="LIVE ${ACCOUNT}"
fi

echo "Mode: ${MODE^^}"
echo "IB endpoint: ${HOST}:${IB_PORT}"
echo "Max real order quantity: ${MAX_ORDER_QUANTITY}"
echo "Minimum post-trade reserve: ${MINIMUM_RESERVE_FUNDS}"
echo "Type exactly '${EXPECTED}' to start the order-capable process:"
read -r CONFIRMATION
if [[ "${CONFIRMATION}" != "${EXPECTED}" ]]; then
  echo "Confirmation did not match. Trade gateway was not started."
  exit 1
fi

ARGS=(
  "${ROOT}/open_trade_gateway.py"
  --mode "${MODE}"
  --ib-host "${HOST}"
  --ib-port "${IB_PORT}"
  --client-id "${CLIENT_ID}"
  --account "${ACCOUNT}"
  --max-order-quantity "${MAX_ORDER_QUANTITY}"
  --max-preview-quantity "${MAX_PREVIEW_QUANTITY}"
  --minimum-reserve-funds "${MINIMUM_RESERVE_FUNDS}"
  --enable-order-transmission
)
if [[ "${MODE}" == "live" ]]; then
  ARGS+=(--live-account-confirm "${ACCOUNT}")
fi

cd "${ROOT}"
exec "${PYTHON}" "${ARGS[@]}"
