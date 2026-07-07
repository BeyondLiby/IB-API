#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8766}"

PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN || true)"
if [ -z "${PIDS}" ]; then
  echo "No inventory planner server is listening on port ${PORT}."
  exit 0
fi

echo "Stopping inventory planner server on port ${PORT}: ${PIDS}"
kill ${PIDS}

for _ in $(seq 1 20); do
  if ! lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Stopped."
    exit 0
  fi
  sleep 0.2
done

echo "Server did not stop after SIGTERM; forcing..."
kill -9 ${PIDS} 2>/dev/null || true
echo "Stopped."
