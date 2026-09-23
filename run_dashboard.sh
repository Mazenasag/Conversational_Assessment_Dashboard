#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
export APP_MODE=local

if [ ! -x ".venv/bin/python" ]; then
  echo "No local environment found; running setup first..."
  ./setup.sh
fi

exec .venv/bin/python -m streamlit run app.py --server.port 8501
