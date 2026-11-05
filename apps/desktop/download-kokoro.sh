#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
DIR="models/kokoro"
mkdir -p "$DIR"

for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [ -s "$DIR/$f" ]; then
    echo "==> $f already present, skipping"
  else
    echo "==> Downloading $f"
    curl -# -L -o "$DIR/$f" "$BASE/$f"
  fi
done

ls -lh "$DIR"
