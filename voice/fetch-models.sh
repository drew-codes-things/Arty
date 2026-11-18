#!/bin/bash
set -euo pipefail

TARGET=${1:-/opt/ai-arty/models}
SMART_TURN_URL=https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx
SMART_TURN_SHA=2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f
SILERO_URL=https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/src/silero_vad/data/silero_vad.onnx
SILERO_SHA=1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3
MOONSHINE_URL=https://download.moonshine.ai/model/tiny-streaming-en/quantized_26_11_02
MOONSHINE_DIR=moonshine-tiny-streaming-en
MOONSHINE_FILES=(
  "adapter.ort c5f9f7cc98c27a965d8e200816f7e5c07b892712bb30cf07444bbde4ffd38199"
  "cross_kv.ort 5f68900da4ea20f40fbe67bbec36399f0d47faedbd7198b29abd9dc5f472ef9f"
  "decoder_kv.ort 6564f2c9a19de44195a53e336e07d05108f14babab57105d623bb68b79572d75"
  "encoder.ort 617e403b2aae48f2474e0b148bf835deb9510bc8692efeabfe27203959a3256e"
  "frontend.model.ort 5662679f0e5758646d8eade3bc4b5d9ebda875feb6e696c0cc5f1a08fea2219e"
  "frontend.weights.ort 55c91d7efab9f7506d7d753131cba72145ff016be3447319fe414a31e13f25b4"
  "streaming_config.json 74fe5ddebd63b17caf59e8a3b18c17547ff7bce1642050edbb1c3962674f8950"
  "tokenizer.bin 6884b35fd6377d4c4d32336a0bc152f36b64d1e45b6503683cdc238250a8472d"
)

fetch() {
  local url=$1 sha=$2 name=$3
  if [ -f "$TARGET/$name" ] && echo "$sha  $TARGET/$name" | sha256sum -c --quiet - 2>/dev/null; then
    echo "$name already in place"
    return
  fi
  local download
  download=$(mktemp)
  curl -fsSL -o "$download" "$url"
  if ! echo "$sha  $download" | sha256sum -c --quiet -; then
    rm -f "$download"
    echo "$name did not match its pinned SHA-256, not installed" >&2
    exit 1
  fi
  install -m 644 "$download" "$TARGET/$name"
  rm -f "$download"
  echo "$name installed"
}

mkdir -p "$TARGET/$MOONSHINE_DIR"
fetch "$SMART_TURN_URL" "$SMART_TURN_SHA" smart-turn-v3.2-cpu.onnx
fetch "$SILERO_URL" "$SILERO_SHA" silero_vad.onnx
for entry in "${MOONSHINE_FILES[@]}"; do
  read -r file sha <<< "$entry"
  fetch "$MOONSHINE_URL/$file" "$sha" "$MOONSHINE_DIR/$file"
done
