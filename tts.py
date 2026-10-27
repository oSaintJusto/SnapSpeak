#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Kokoro TTS —— 本地离线文字朗读工具 (中文 / 英文 / 多语言)

用法:
    python tts.py "要朗读的文字"
    python tts.py -f article.txt -o article.mp3
    python tts.py "Hello world" -l en -v af_heart
    python tts.py --list-voices
    python tts.py "试音" --list-langs

完全离线,模型只加载一次,长文本自动按句子切块再拼接。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")

# lang code -> (model file, voices file, Kokoro lang tag, is_chinese)
LANGS = {
    "zh": ("kokoro-v1.1-zh.onnx", "voices-v1.1-zh.bin", "z", True),
    "en": ("kokoro-v1.0.onnx", "voices-v1.0.bin", "en-us", False),
}

DEFAULT_VOICE = {"zh": "zf_001", "en": "af_heart"}

SENT_SPLIT = {
    True: re.compile(r"(?<=[。！？；…!?;\n])"),
    False: re.compile(r"(?<=[.!?;])\s+|\n+"),
}


_ENGINE_CACHE: dict = {}


def _load_engine(lang: str, en_mix: bool = True):
    """加载 ONNX 会话 + G2P,返回 (kokoro, g2p, is_zh)。同一进程内复用。"""
    key = (lang, en_mix)
    if key not in _ENGINE_CACHE:
        _ENGINE_CACHE[key] = _build_engine(lang, en_mix)
    return _ENGINE_CACHE[key]


def _build_engine(lang: str, en_mix: bool = True):
    """构建引擎(首次调用约 1~2 秒)。"""
    if lang not in LANGS:
        sys.exit(f"不支持的语言: {lang}。可选: {', '.join(LANGS)}")
    model, voices, _, is_zh = LANGS[lang]
    mp, vp = os.path.join(MODELS, model), os.path.join(MODELS, voices)
    for p in (mp, vp):
        if not os.path.exists(p):
            sys.exit(f"缺少模型文件: {p}")

    if is_zh:
        kokoro = Kokoro(mp, vp, vocab_config=os.path.join(MODELS, "config.json"))
        from misaki import zh

        en_callable = None
        if en_mix:
            # 让中文里夹的英文单词/缩写也能念出来(espeak-ng -> Kokoro 音素集)
            from types import SimpleNamespace

            from misaki.espeak import EspeakFallback

            fb = EspeakFallback(british=False)

            def en_callable(word):
                ph, _ = fb(SimpleNamespace(text=word))
                return ph

        return kokoro, zh.ZHG2P(version="1.1", en_callable=en_callable), True
    return Kokoro(mp, vp), None, False


def _split_long(s: str, max_chars: int, is_zh: bool) -> list[str]:
    """把超长句子切成若干块。英文优先在空格处断,别把单词劈开。"""
    out = []
    while len(s) > max_chars:
        cut = max_chars
        if not is_zh:
            sp = s.rfind(" ", 0, max_chars)
            if sp > max_chars * 0.5:      # 找到合理的位置才用,否则硬切
                cut = sp
        piece = s[:cut].strip()
        if piece:
            out.append(piece)
        s = s[cut:].lstrip()
    if s.strip():
        out.append(s.strip())
    return out


def _chunks(text: str, is_zh: bool, max_chars: int) -> list[str]:
    """按句子切分,再把短句合并成不超过 max_chars 的块。"""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    sents = [s.strip() for s in SENT_SPLIT[is_zh].split(text)]
    sents = [s for s in sents if s]
    out, buf = [], ""
    for s in sents:
        if len(s) > max_chars:  # 超长句子切开,但别切在单词中间
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_split_long(s, max_chars, is_zh))
            continue
        if len(buf) + len(s) > max_chars and buf:
            out.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        out.append(buf)
    return out


def synth(text: str, lang="zh", voice=None, speed=1.0, max_chars=200,
          gap=0.18, verbose=True, en_mix=True):
    """把文本合成为 (float32 波形, 采样率)。"""
    voice = voice or DEFAULT_VOICE[lang]
    t0 = time.time()
    kokoro, g2p, is_zh = _load_engine(lang, en_mix=en_mix)
    if verbose:
        print(f"[engine] {lang} / voice={voice} / 加载 {time.time() - t0:.1f}s",
              file=sys.stderr)

    parts = _chunks(text, is_zh, max_chars)
    if not parts:
        sys.exit("输入文本为空。")

    audio, sr = [], None
    t1 = time.time()
    for i, chunk in enumerate(parts, 1):
        if is_zh:
            phonemes, _ = g2p(chunk)
            if not phonemes:
                continue
            wav, sr = kokoro.create(phonemes, voice=voice, speed=speed,
                                    is_phonemes=True)
        else:
            wav, sr = kokoro.create(chunk, voice=voice, speed=speed,
                                    lang="en-us")
        audio.append(np.asarray(wav, dtype=np.float32))
        if gap > 0:
            audio.append(np.zeros(int(sr * gap), dtype=np.float32))
        if verbose:
            dur = len(wav) / sr
            print(f"  [{i}/{len(parts)}] {dur:5.1f}s  {chunk[:28]}"
                  f"{'…' if len(chunk) > 28 else ''}", file=sys.stderr)

    if not audio:
        sys.exit("合成失败:没有产出音频。")

    full = np.concatenate(audio)
    if verbose:
        el = time.time() - t1
        print(f"[done] {len(full) / sr:.1f}s 音频,耗时 {el:.1f}s "
              f"(RTF {el / (len(full) / sr):.2f})", file=sys.stderr)
    return full, sr


def write_audio(wave, sr, path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".wav", ".flac", ".ogg"):
        sf.write(path, wave, sr)
        return
    if ext in (".mp3", ".m4a", ".aac", ".opus"):
        if not shutil.which("ffmpeg"):
            sys.exit("输出 mp3 需要 ffmpeg,请先 brew install ffmpeg,或改用 .wav")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp = tf.name
        try:
            sf.write(tmp, wave, sr)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
                 "-codec:a", "libmp3lame" if ext == ".mp3" else "aac",
                 "-q:a", "2" if ext == ".mp3" else "4", path],
                check=True)
        finally:
            os.unlink(tmp)
        return
    sf.write(path, wave, sr)


def _read_input(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        if shutil.which("pdftotext"):
            return subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"],
                                  capture_output=True, text=True,
                                  check=True).stdout
        try:
            import pymupdf  # 纯 pip 安装,不依赖 brew
        except ImportError:
            sys.exit("读 PDF 需要 pymupdf: "
                     "venv310/bin/python -m pip install pymupdf")
        with pymupdf.open(path) as doc:
            text = "\n".join(page.get_text() for page in doc)
        if not text.strip():
            sys.exit("这个 PDF 没有文字层(多半是扫描件),需要先做 OCR。")
        return text
    if ext == ".docx":
        try:
            import docx
        except ImportError:
            sys.exit("读 docx 需要 python-docx: "
                     "venv310/bin/python -m pip install python-docx")
        return "\n".join(p.text for p in docx.Document(path).paragraphs)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser(
        description="Kokoro 本地离线 TTS —— 把文字变成语音",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例子:\n"
               "  python tts.py \"你好,世界\"\n"
               "  python tts.py -f book.txt -o book.mp3\n"
               "  python tts.py \"Hello\" -l en -v bm_george -o hi.wav")
    ap.add_argument("text", nargs="?", help="要朗读的文字")
    ap.add_argument("-f", "--file", help="从文件读入 (txt/md/pdf/docx)")
    ap.add_argument("-l", "--lang", default="zh", choices=sorted(LANGS),
                    help="语言 (默认 zh)")
    ap.add_argument("-v", "--voice", help="音色名 (默认 zh=zf_001, en=af_heart)")
    ap.add_argument("-s", "--speed", type=float, default=1.0, help="语速 (默认 1.0)")
    ap.add_argument("-o", "--out", default="output.wav",
                    help="输出文件 (按后缀决定格式, .wav/.mp3/.flac/.m4a)")
    ap.add_argument("--max-chars", type=int, default=200, help="单块最大字数")
    ap.add_argument("--gap", type=float, default=0.18, help="句间静音秒数")
    ap.add_argument("--no-en-mix", action="store_true",
                    help="中文模式下不朗读夹杂的英文单词")
    ap.add_argument("--play", action="store_true", help="生成后直接播放")
    ap.add_argument("--list-voices", action="store_true", help="列出当前语言的音色")
    ap.add_argument("--list-langs", action="store_true", help="列出支持的语言")
    args = ap.parse_args()

    if args.list_langs:
        for k, v in LANGS.items():
            print(f"{k:4s} model={v[0]:24s} default_voice={DEFAULT_VOICE[k]}")
        return
    if args.list_voices:
        kokoro, _, _ = _load_engine(args.lang)
        print("\n".join(sorted(kokoro.get_voices())))
        return

    if args.file:
        text = _read_input(args.file)
    elif args.text:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        ap.error("请提供文字、-f 文件,或用管道传入 stdin")

    wave, sr = synth(text, lang=args.lang, voice=args.voice,
                     speed=args.speed, max_chars=args.max_chars, gap=args.gap,
                     en_mix=not args.no_en_mix)
    write_audio(wave, sr, args.out)
    print(f"[saved] {args.out}  ({os.path.getsize(args.out) / 1e6:.2f} MB)",
          file=sys.stderr)
    if args.play:
        subprocess.run(["afplay", args.out], check=False)


if __name__ == "__main__":
    main()
