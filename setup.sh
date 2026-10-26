#!/bin/bash
# 一键准备开发环境
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PY=""
for c in python3.12 python3.11 python3.10; do
  command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -n "$PY" ] || { echo "需要 Python 3.10 / 3.11 / 3.12(misaki 不支持 3.13+)"; exit 1; }
echo "==> 使用 $($PY -V)"

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
echo "==> 安装依赖"
.venv/bin/python -m pip install -r requirements.txt

echo "==> 下载模型"
bash "$DIR/download_models.sh"

echo
echo "==> 完成。接下来:"
echo "    ./speak.sh \"你好\"             # 试试朗读"
echo "    ./snap.sh                      # 试试划屏取词"
echo "    ./build_app.sh                 # 打包成 ~/Applications/SnapSpeak.app"
