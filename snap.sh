#!/bin/bash
# snap.sh —— 截图 → OCR → 朗读
#
#   ./snap.sh                 划一块屏幕,识别,念出来
#   ./snap.sh -w              点一个窗口
#   ./snap.sh -p              识别剪贴板里的图
#   ./snap.sh -f 图片.png     识别已有图片
#   ./snap.sh --no-play       只识别不朗读(顺便复制到剪贴板)

set -u
BASE="$(cd "$(dirname "$0")" && pwd)"
PY="$BASE/.venv/bin/python"
[ -x "$PY" ] || { echo "找不到 Python 环境,请先运行 ./setup.sh" >&2; exit 1; }
exec "$PY" "$BASE/snap.py" "$@"
