#!/bin/bash
# speak —— 一行命令把文字念出来
#
#   ./speak.sh "今天天气不错"
#   ./speak.sh -f 文章.txt
#   ./speak.sh -f 论文.pdf -o 论文.mp3        # 存成文件,不直接播
#   ./speak.sh "Hello" -l en -v bm_george
#
# 不加 -o 时生成到 out/speak.mp3 并立即播放。

set -u
BASE="$(cd "$(dirname "$0")" && pwd)"
PY="$BASE/.venv/bin/python"
[ -x "$PY" ] || { echo "找不到 Python 环境,请先运行 ./setup.sh" >&2; exit 1; }

has_out=0
for arg in "$@"; do
  case "$arg" in
    -o|--out|--out=*) has_out=1 ;;
  esac
done

mkdir -p "$BASE/out"

# 纯信息类命令:只打印,不合成也不播放
case " $* " in
  *" --list-voices "*|*" --list-langs "*|*" -h "*|*" --help "*)
    exec "$PY" "$BASE/tts.py" "$@" ;;
esac

if [ $# -eq 0 ]; then
  echo "用法: ./speak.sh \"要朗读的文字\" | -f 文件 [-l zh|en] [-v 音色] [-s 语速]"
  echo "      ./speak.sh --list-voices -l zh    # 看所有音色"
  exit 1
fi

if [ "$has_out" = "1" ]; then
  exec "$PY" "$BASE/tts.py" "$@"
fi

out="$BASE/out/speak.mp3"
"$PY" "$BASE/tts.py" "$@" -o "$out" && exec afplay "$out"
