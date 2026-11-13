#!/usr/bin/env bash
# Builds Arty as an AppImage.
#
# The AppImage is self-contained: it carries its own Python 3.12, the backend's
# dependencies, the Kokoro voice, the Whisper model and a starter config, so
# it runs on a machine that has never seen this repo. The one thing it can't
# carry is PortAudio (sudo apt install libportaudio2), which is a system
# library on Linux.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

command -v rsync >/dev/null || { echo "rsync is needed to build." >&2; exit 1; }

case "$(uname -m)" in
  x86_64)  PY_ARCH="x86_64";  BUILD_ARCH="x64";   UNPACKED="linux-unpacked" ;;
  aarch64) PY_ARCH="aarch64"; BUILD_ARCH="arm64"; UNPACKED="linux-arm64-unpacked" ;;
  *) echo "Can't build for $(uname -m): only x86_64 and aarch64." >&2; exit 1 ;;
esac

PY_VERSION="3.12.14"
PY_RELEASE="20260901"
PY_ASSET="cpython-${PY_VERSION}+${PY_RELEASE}-${PY_ARCH}-unknown-linux-gnu-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${PY_ASSET}"

WHISPER_MODEL="base.en"
CACHE="app/build/cache"
STAGE="app/build/backend"
PY="$STAGE/python/bin/python3.12"
SITE="$STAGE/python/lib/python3.12/site-packages"
HF_CLI_AGENT_FILES=(system skills extensions _skills spaces)
HF_SKILLS_BLOCK_START="^# Skills directories for AI agents"

strip_hf_agent_tooling() {
  local hub="$SITE/huggingface_hub"
  local metadata=("$SITE"/huggingface_hub-*.dist-info/METADATA)
  local file
  for file in "${HF_CLI_AGENT_FILES[@]}"; do
    rm -f "$hub/cli/$file.py"
  done
  rm -f "$STAGE/python/bin/hf"
  sed -i "/$HF_SKILLS_BLOCK_START/,/^\$/d" "$hub/constants.py"
  sed -i '/^$/,$d' "${metadata[@]}"
  for file in "${HF_CLI_AGENT_FILES[@]}"; do
    [ ! -e "$hub/cli/$file.py" ] || { echo "Couldn't remove huggingface_hub/cli/$file.py." >&2; exit 1; }
  done
  if grep -q "_SKILLS_" "$hub/constants.py" || grep -q '^$' "${metadata[@]}"; then
    echo "The bundled huggingface_hub still has its agent tooling (constants.py or METADATA)." >&2
    exit 1
  fi
}

if [ ! -d models/kokoro ] || [ ! -s models/kokoro/kokoro-v1.0.onnx ]; then
  echo "No Kokoro voice model. Run ./download-kokoro.sh first." >&2
  exit 1
fi

mkdir -p "$CACHE"
if [ ! -s "$CACHE/$PY_ASSET" ]; then
  echo "==> Downloading a relocatable Python $PY_VERSION"
  curl -# -fL --retry 3 -o "$CACHE/$PY_ASSET" "$PY_URL"
fi

echo "==> Staging the backend"
rm -rf "$STAGE"
mkdir -p "$STAGE"
tar -xzf "$CACHE/$PY_ASSET" -C "$STAGE"

echo "==> Installing backend dependencies"
"$PY" -m pip install --quiet --no-cache-dir --upgrade pip
"$PY" -m pip install --quiet --no-cache-dir -r server/requirements-linux.txt

echo "==> Fetching Whisper $WHISPER_MODEL"
mkdir -p "$STAGE/models/whisper"
"$PY" - "$STAGE/models/whisper/$WHISPER_MODEL" <<'PYEOF'
import sys
from huggingface_hub import snapshot_download

snapshot_download(
    f"Systran/faster-whisper-{sys.argv[1].rsplit('/', 1)[-1]}",
    local_dir=sys.argv[1],
    allow_patterns=["*.json", "*.txt", "*.bin"],
)
PYEOF

echo "==> Trimming what the app will never run"
rm -rf "$STAGE/models/whisper/$WHISPER_MODEL/.cache"
rm -rf "$STAGE/python/include" "$STAGE/python/share"
rm -rf "$STAGE"/python/lib/tcl* "$STAGE"/python/lib/libtcl* "$STAGE"/python/lib/libtk*
rm -rf "$STAGE"/python/lib/python3.12/{test,idlelib,tkinter,turtledemo,lib2to3,ensurepip}
rm -rf "$STAGE"/python/lib/python3.12/site-packages/{pip,setuptools,wheel}
rm -rf "$STAGE"/python/lib/python3.12/site-packages/{pip,setuptools,wheel}-*.dist-info
find "$STAGE/python" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/python" -name '*.pyc' -delete 2>/dev/null || true
strip_hf_agent_tooling

echo "==> Copying the backend and its assets"
rsync -a --exclude '__pycache__' --exclude 'tests' --exclude '.pytest_cache' --exclude '.mypy_cache' --exclude '.ruff_cache' server "$STAGE/"
rsync -a models/kokoro "$STAGE/models/"
cp arty_config.example.yaml "$STAGE/arty_config.example.yaml"

echo "==> Packing the app"
( cd app && npx electron-builder --linux dir "--$BUILD_ARCH" )

echo "==> Building the AppImage"
( cd app && npx electron-builder --linux AppImage "--$BUILD_ARCH" --prepackaged "dist/$UNPACKED" )

APPIMAGE="$(ls -t app/dist/Arty-*.AppImage 2>/dev/null | head -1 || true)"

echo
echo "Done."
if [ -n "$APPIMAGE" ]; then
  echo "  $APPIMAGE ($(du -h "$APPIMAGE" | cut -f1))"
  echo "  Run it with: $APPIMAGE"
else
  echo "  No AppImage was produced." >&2
fi
