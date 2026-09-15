#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
echo "Template globe: http://localhost:${PORT:-8000}/demo/"
exec .venv/bin/python -m uvicorn demo.app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
