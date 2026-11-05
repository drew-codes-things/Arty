#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

[ -d .venv ] || { echo "No .venv here. Run ./setup-linux.sh first." >&2; exit 1; }

CURRENT="$(.venv/bin/python -c 'import sys; print(sys.prefix)')"
if [ "$CURRENT" != "$ROOT/.venv" ]; then
  echo "Interpreter reports $CURRENT but we are in $ROOT. Recreate with ./setup-linux.sh" >&2
  exit 1
fi

STALE="$(grep -rhoE '/[A-Za-z0-9_./ -]+/\.venv' .venv/bin/activate 2>/dev/null | head -1 || true)"
if [ -z "$STALE" ] || [ "$STALE" = "$ROOT/.venv" ]; then
  echo "venv paths already correct ($ROOT/.venv)"
  exit 0
fi

OLD="${STALE%/.venv}"
echo "Rewriting $OLD -> $ROOT"
n=0
for f in .venv/bin/*; do
  [ -f "$f" ] || continue
  file "$f" | grep -qE 'text|script' || continue
  if grep -q "$OLD" "$f" 2>/dev/null; then
    sed -i "s|$OLD|$ROOT|g" "$f"; n=$((n+1))
  fi
done
[ -f .venv/pyvenv.cfg ] && sed -i "s|$OLD|$ROOT|g" .venv/pyvenv.cfg
echo "Repaired $n script(s). pip/uvicorn/activate should work again."
