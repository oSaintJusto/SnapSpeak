#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SnapSpeak 核心层 —— 截图 / OCR / 朗读 / 配置,不含任何 UI。

CLI(snap.py)和菜单栏 App(app.py)都调用这里。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ocr as ocr_mod  # noqa: E402
import secret as secret_mod  # noqa: E402
import textfix  # noqa: E402
import translate as translate_mod  # noqa: E402
import tts as tts_mod  # noqa: E402

# 允许用环境变量覆盖(打包自测 / 便携使用)
CONFIG_DIR = (os.environ.get("SNAPSPEAK_CONFIG_DIR")
              or os.path.expanduser("~/Library/Application Support/SnapSpeak"))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Carbon 修饰键常量
CMD, SHIFT, OPTION, CONTROL = 256, 512, 2048, 4096

DEFAULTS = {
    "hotkey": {"keycode": 1, "modifiers": CONTROL | OPTION, "label": "⌃⌥S"},
    "voice_zh": "zf_001",
    "voice_en": "af_heart",
    "speed": 1.0,
    "engine": "kokoro",          # kokoro | say
    "copy_to_clipboard": True,
    "notify": True,
    "auto_play": True,
    "capture_mode": "region",    # region | window
    "translate_en": True,        # 英文时显示中文译文(朗读仍是英文)
    "pin_translation": False,    # 译窗是否常驻
    "hud_pos": None,             # 悬浮面板位置 {x,y}
    "hud_size": None,            # 用户手动缩放过的大小 {w,h}
    "hud_font_size": 12.5,       # 悬浮框里译文的字号
    "history": [],
}

HISTORY_MAX = 12


# ------------------------------------------------------------------ 配置
def load_settings() -> dict:
    data = dict(DEFAULTS)
    data["history"] = []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k in DEFAULTS:
            if k in saved:
                data[k] = saved[k]
        if not isinstance(data["history"], list):
            data["history"] = []
    except (OSError, ValueError):
        pass
    if "hotkey" not in data or not isinstance(data["hotkey"], dict):
        data["hotkey"] = dict(DEFAULTS["hotkey"])
    return data


def save_settings(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ------------------------------------------------------------------ 小工具
def notify(msg: str, subtitle: str = "", title: str = "SnapSpeak"):
    script = f'display notification {json.dumps(msg)} with title {json.dumps(title)}'
    if subtitle:
        script += f" subtitle {json.dumps(subtitle)}"
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


def guess_lang(text: str) -> str:
    cjk = sum(1 for c in text if ocr_mod._is_cjk(c))
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    if cjk == 0:
        return "en"
    return "zh" if cjk * 3 >= latin else "en"


def maybe_translate(text: str, lang: str, cfg: dict, step=None):
    """
    英文 -> 中文译文,**只用于显示**,朗读的仍然是英文原文。

    返回 (译文, 错误信息)。没配 Key 或翻译失败就返回空译文,绝不打断主流程。
    """
    if lang != "en" or not cfg.get("translate_en", True) or not text.strip():
        return "", ""

    key = secret_mod.get_key()
    if not key:
        return "", ""                     # 没配 Key:不翻译,也不报错

    if step:
        step("翻译中…")
    ok, out = translate_mod.translate(text, key)
    return (out, "") if ok else ("", out)


def copy_to_clipboard(text: str) -> bool:
    p = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
    return p.returncode == 0


def play(path: str):
    subprocess.run(["afplay", path], check=False)


# ------------------------------------------------------------------ 截图
def has_screen_permission() -> bool:
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


def open_screen_permission_settings():
    subprocess.run(
        ["open",
         "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"],
        check=False)


def capture(mode: str = "region") -> str | None:
    """截图到临时文件;用户取消返回 None。"""
    fd, path = tempfile.mkstemp(suffix=".png", prefix="snapspeak_")
    os.close(fd)
    cmd = ["screencapture", "-x", "-w" if mode == "window" else "-i", path]
    subprocess.run(cmd, capture_output=True, check=False)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    return path


# ------------------------------------------------------------------ 引擎
def warmup(langs=("zh",)):
    """预加载模型,让第一次快捷键不用等 2 秒。"""
    for lg in langs:
        try:
            tts_mod._load_engine(lg)
        except Exception:
            pass


def voices(lang: str) -> list[str]:
    """列出音色名。直接读 .bin,不加载模型(省 2 秒 + 400MB)。"""
    try:
        info = tts_mod.LANGS[lang]
    except KeyError:
        return []
    path = os.path.join(tts_mod.MODELS, info[1])
    try:
        import numpy as np
        return sorted(np.load(path).files)
    except Exception:
        try:
            kokoro, _, _ = tts_mod._load_engine(lang)
            return sorted(kokoro.get_voices())
        except Exception:
            return []


# ------------------------------------------------------------------ 主流程
def run(settings: dict | None = None, source: str | None = None,
        progress=None, save_history: bool = True,
        defer_play: bool = False) -> dict:
    """
    跑一次「截图 → OCR → 朗读」。

    返回 dict: ok / text / lang / voice / audio / ms / error
    progress(str) 会在各个阶段被调用,用于更新 UI。
    """
    cfg = settings or load_settings()

    def step(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    t0 = time.time()
    tmp_img = None
    try:
        # 1. 取图
        if source:
            img = source
        else:
            step("等您划选屏幕…")
            img = capture(cfg.get("capture_mode", "region"))
            if img is None:
                if not has_screen_permission():
                    return {"ok": False, "error": "permission", "cancelled": False}
                return {"ok": False, "cancelled": True}
            tmp_img = img

        # 2. OCR
        step("识别文字中…")
        rows = ocr_mod.ocr(img)
        if not rows:
            return {"ok": False, "error": "没识别到文字", "cancelled": False}
        # 把 OCR 的硬换行接回句子,并去掉行尾断词连字符
        source_text = textfix.clean_ocr_text("\n".join(r[0] for r in rows))
        lang = guess_lang(source_text)
        t_ocr = time.time() - t0

        # 2.5 英文 -> 中文译文(只用于显示,朗读的仍是英文原文)
        translation, tr_error = maybe_translate(source_text, lang, cfg, step)
        text = source_text

        # 3. 复制 —— 有译文就复制译文(更好用),否则复制原文
        if cfg.get("copy_to_clipboard", True):
            copy_to_clipboard(translation or text)

        # 4. 朗读
        audio = None
        voice = cfg.get("voice_zh") if lang == "zh" else cfg.get("voice_en")
        if cfg.get("auto_play", True):
            speed = float(cfg.get("speed", 1.0))
            if cfg.get("engine") == "say":
                step("朗读中…")
                v = "Tingting" if lang == "zh" else voice or "Samantha"
                subprocess.run(["say", "-v", v, "-r", str(int(180 * speed)), text],
                               check=False)
            else:
                step(f"合成语音中({lang})…")
                wave, sr = tts_mod.synth(text, lang=lang, voice=voice,
                                         speed=speed, verbose=False)
                fd, audio = tempfile.mkstemp(suffix=".wav", prefix="snapspeak_")
                os.close(fd)
                tts_mod.write_audio(wave, sr, audio)
                if defer_play:
                    # 交给调用方播(这样才有暂停/拖进度条);文件也由它清理
                    step("准备播放…")
                else:
                    step("播放中…")
                    play(audio)      # 阻塞到播完
                    try:
                        os.unlink(audio)
                    except OSError:
                        pass
                    audio = None

        ms = int((time.time() - t0) * 1000)
        result = {"ok": True, "text": text, "lang": lang, "voice": voice,
                  "audio": audio, "ms": ms, "ocr_ms": int(t_ocr * 1000),
                  "lines": len(rows),
                  "translation": translation,
                  "translated": bool(translation),
                  "source_text": source_text,
                  "translate_error": tr_error}

        # 5. 历史 —— 只写历史字段,不要把调用方传入的临时覆盖写回磁盘
        if save_history:
            entry = {"text": text, "lang": lang, "voice": voice,
                     "ts": int(time.time())}
            if translation:
                entry["translation"] = translation
            try:
                disk = load_settings()
                hist = [entry] + (disk.get("history") or [])
                disk["history"] = hist[:HISTORY_MAX]
                save_settings(disk)
                cfg["history"] = disk["history"]
            except OSError:
                cfg["history"] = [entry] + (cfg.get("history") or [])[:HISTORY_MAX - 1]

        if cfg.get("notify", True):
            sub = "已复制译文" if translation else (
                "已复制到剪贴板" if cfg.get("copy_to_clipboard", True) else "")
            if tr_error:
                sub = f"翻译失败:{tr_error[:40]}"
            tag = "已翻译 · " if translation else ""
            notify(f"{tag}{len(text)} 字 · {ms} ms", sub)
        return result
    except SystemExit as e:
        return {"ok": False, "error": f"环境缺文件: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if tmp_img:
            try:
                os.unlink(tmp_img)
            except OSError:
                pass


def speak_file(text: str, settings: dict | None = None,
               force_lang: str | None = None, defer_play: bool = False) -> dict:
    """把一段现成的文字念出来(不截图、不 OCR)。"""
    cfg = settings or load_settings()
    lang = force_lang or guess_lang(text)
    voice = cfg.get("voice_zh") if lang == "zh" else cfg.get("voice_en")
    try:
        if cfg.get("engine") == "say":
            subprocess.run(["say", "-v", "Tingting" if lang == "zh" else "Samantha",
                            text], check=False)
            return {"ok": True}
        wave, sr = tts_mod.synth(text, lang=lang, voice=voice,
                                 speed=float(cfg.get("speed", 1.0)), verbose=False)
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="snapspeak_")
        os.close(fd)
        tts_mod.write_audio(wave, sr, path)
        if defer_play:
            return {"ok": True, "audio": path}
        play(path)
        try:
            os.unlink(path)
        except OSError:
            pass
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
