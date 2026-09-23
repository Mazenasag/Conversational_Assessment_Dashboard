#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

PYTHON=""
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "ERROR: Python was not found."
  exit 1
fi

echo "Static dashboard: http://localhost:8080/"
exec "$PYTHON" -m http.server 8080 -d frontend
