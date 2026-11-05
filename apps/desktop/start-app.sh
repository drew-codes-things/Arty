#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/app"

if [ ! -d node_modules ]; then
  echo "Installing app dependencies..."
  npm install
fi

exec npm start
