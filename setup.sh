#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

PYTHON=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "ERROR: Python 3.10+ is required."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating .venv..."
  "$PYTHON" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

echo "Setup complete. Edit .env and add GEMINI_API_KEY."
