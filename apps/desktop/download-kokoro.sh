#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
DIR="models/kokoro"
mkdir -p "$DIR"

MODEL="kokoro-v1.0.onnx"
if [ "${1:-}" = "light" ]; then
  MODEL="kokoro-v1.0.int8.onnx"
fi

for f in "$MODEL" voices-v1.0.bin; do
  if [ -s "$DIR/$f" ]; then
    echo "==> $f already present, skipping"
  else
    echo "==> Downloading $f"
    curl -# -L -o "$DIR/$f" "$BASE/$f"
  fi
done

ls -lh "$DIR"
