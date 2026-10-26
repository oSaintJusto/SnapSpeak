#!/bin/bash
# 下载 Kokoro 模型(约 720 MB)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
M="$DIR/models"
mkdir -p "$M"
REL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"

fetch() {
  local name="$1" url="$2"
  [ -s "$M/$name" ] && { echo "  已存在 $name"; return; }
  echo "  下载 $name"
  for i in 1 2 3 4 5; do
    curl -L --fail --retry 5 --retry-all-errors --retry-delay 2 -m 900 --tlsv1.2 \
         -o "$M/$name.part" "$url" -sS && mv "$M/$name.part" "$M/$name" && return 0
    sleep 2
  done
  echo "  失败: $name"; return 1
}

echo "==> 中文模型"
fetch kokoro-v1.1-zh.onnx   "$REL/kokoro-v1.1-zh.onnx"
fetch voices-v1.1-zh.bin    "$REL/voices-v1.1-zh.bin"
echo "==> 英文模型"
fetch kokoro-v1.0.onnx      "$REL/kokoro-v1.0.onnx"
fetch voices-v1.0.bin       "$REL/voices-v1.0.bin"
echo "==> 中文音素表"
fetch config.json "https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/raw/main/config.json"

echo "==> 完成,共 $(du -sh "$M" | cut -f1)"
