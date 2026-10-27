#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
截图 → OCR → 朗读,一条命令搞定。

    ./snap.sh                     划一块屏幕,识别,念出来
    ./snap.sh -w                  点一个窗口
    ./snap.sh -p                  识别剪贴板里的图
    ./snap.sh -f 图片.png         识别已有图片
    ./snap.sh --no-play           只识别,不朗读(顺便复制到剪贴板)

选项:
    --engine kokoro|say   朗读引擎(kokoro 好听但慢 ~2s,say 秒出但机械)
    --no-copy             识别后不覆盖剪贴板
    -l zh|en              强制语言(默认按内容自动判断)
    -v 音色  -s 语速
    -o out.mp3            同时把语音存成文件
    --keep-image          保留截图
    --raw                 原样输出,不打印任何提示信息
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import Quartz  # noqa: E402

import ocr as ocr_mod  # noqa: E402
import textfix  # noqa: E402
import tts as tts_mod  # noqa: E402

TITLE = "截图朗读"


# ------------------------------------------------------------------ 小工具
def notify(msg: str, subtitle: str = ""):
    """右上角弹一条通知。失败就算了,不影响主流程。"""
    script = f'display notification {json.dumps(msg)} with title {json.dumps(TITLE)}'
    if subtitle:
        script += f" subtitle {json.dumps(subtitle)}"
    subprocess.run(["osascript", "-e", script],
                   capture_output=True, check=False)


def has_screen_permission() -> bool:
    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True  # 老系统没有这个 API,只能硬着头皮上


def ask_screen_permission():
    print("""[!] 还没有「屏幕录制」权限,截图会失败。

    系统设置 → 隐私与安全性 → 屏幕录制
    打开你运行这个命令的终端(App),然后**完全退出并重开**终端。

    已经帮你打开设置面板了。""", file=sys.stderr)
    subprocess.run(
        ["open",
         "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"],
        check=False)


def capture(mode: str) -> str | None:
    """截图,返回临时文件路径;用户取消或被拒绝返回 None。"""
    fd, path = tempfile.mkstemp(suffix=".png", prefix="snap_")
    os.close(fd)
    cmd = ["screencapture", "-x"]
    cmd.append("-w" if mode == "window" else "-i")
    cmd.append(path)

    subprocess.run(cmd, capture_output=True, check=False)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    return path


def guess_lang(text: str) -> str:
    """有中文就按中文念(中文模型能读夹杂的英文)。"""
    cjk = sum(1 for c in text if ocr_mod._is_cjk(c))
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    if cjk == 0:
        return "en"
    return "zh" if cjk * 3 >= latin else "en"


def copy_to_clipboard(text: str):
    p = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
    return p.returncode == 0


def speak_say(text: str, voice: str | None = None, rate: int | None = None):
    cmd = ["say", text]
    if voice:
        cmd[1:1] = ["-v", voice]
    if rate:
        cmd[1:1] = ["-r", str(rate)]
    subprocess.run(cmd, check=False)


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(
        description="截图 → OCR → 朗读",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-w", "--window", action="store_true", help="截取某个窗口")
    ap.add_argument("-f", "--file", help="直接识别这张图片")
    ap.add_argument("-p", "--paste", action="store_true", help="识别剪贴板里的图片")
    ap.add_argument("-l", "--lang", choices=["zh", "en"], help="强制语言")
    ap.add_argument("-v", "--voice", help="音色")
    ap.add_argument("-s", "--speed", type=float, default=1.0, help="语速")
    ap.add_argument("-o", "--out", help="把语音另存为文件")
    ap.add_argument("--engine", choices=["kokoro", "say"], default="kokoro",
                    help="kokoro=音质好;say=系统语音,秒出")
    ap.add_argument("--no-play", action="store_true", help="不朗读,只识别")
    ap.add_argument("--no-copy", action="store_true", help="不复制到剪贴板")
    ap.add_argument("--keep-image", action="store_true", help="保留截图文件")
    ap.add_argument("--raw", action="store_true", help="只输出文字,不打印提示")
    args = ap.parse_args()

    def log(*a):
        if not args.raw:
            print(*a, file=sys.stderr)

    t_start = time.time()

    # ---- 1. 拿图 ----
    tmp_img = None
    if args.file:
        img = args.file
        if not os.path.exists(img):
            log(f"[x] 找不到图片: {img}")
            return 2
    elif args.paste:
        img = "--paste"
    else:
        img = capture("window" if args.window else "region")
        if img is None:
            if not has_screen_permission():
                ask_screen_permission()
                return 3
            log("[·] 已取消")
            return 130
        tmp_img = img

    try:
        # ---- 2. OCR ----
        try:
            rows = ocr_mod.ocr(img)
        except FileNotFoundError as e:
            log(f"[x] {e}")
            if args.paste:
                log("    剪贴板里要先有一张图(Cmd+Shift+Ctrl+4 截图会自动进剪贴板)")
            return 2
        t_ocr = time.time()

        if not rows:
            log("[x] 没识别到文字")
            notify("没识别到文字")
            return 1

        text = textfix.clean_ocr_text("\n".join(r[0] for r in rows),
                                      fix_greek=True)
        lang = args.lang or guess_lang(text)

        # ---- 3. 输出 + 复制 ----
        print(text)
        if not args.no_copy and copy_to_clipboard(text):
            log(f"[✓] 识别 {len(text)} 字 · {len(rows)} 行 · "
                f"{t_ocr - t_start:.2f}s · 已复制到剪贴板")
        else:
            log(f"[✓] 识别 {len(text)} 字 · {len(rows)} 行 · "
                f"{t_ocr - t_start:.2f}s")

        if args.no_play:
            notify(f"识别 {len(text)} 字,已复制")
            return 0

        # ---- 4. 朗读 ----
        if args.engine == "say":
            speak_say(text, voice=args.voice or ("Tingting" if lang == "zh" else "Samantha"),
                      rate=int(180 * args.speed))
            return 0

        voice = args.voice or tts_mod.DEFAULT_VOICE[lang]
        log(f"[·] {lang} / {voice} / 合成中…")
        wave, sr = tts_mod.synth(text, lang=lang, voice=voice,
                                 speed=args.speed, verbose=False)

        out = args.out
        if out is None:
            fd, out = tempfile.mkstemp(suffix=".wav", prefix="snapspeak_")
            os.close(fd)
        tts_mod.write_audio(wave, sr, out)
        log(f"[✓] 语音 {len(wave) / sr:.1f}s · 总计 {time.time() - t_start:.2f}s")
        notify(f"识别 {len(text)} 字,正在朗读")

        subprocess.run(["afplay", out], check=False)

        if args.out is None:
            try:
                os.unlink(out)
            except OSError:
                pass
        return 0
    finally:
        if tmp_img and not args.keep_image:
            try:
                os.unlink(tmp_img)
            except OSError:
                pass
        elif tmp_img:
            log(f"[·] 截图留在: {tmp_img}")


if __name__ == "__main__":
    sys.exit(main())
