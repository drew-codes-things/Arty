#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "No .venv here. Run ./setup-linux.sh first."
  exit 1
fi

PY="$(pwd)/.venv/bin/python"
[ -x "$PY" ] || { echo "No venv. Run ./setup-linux.sh" >&2; exit 1; }
exec "$PY" server/arty_server.py
