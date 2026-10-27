#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
macOS Vision OCR —— 命令行本地文字识别(和 Bob 的离线 OCR 是同一个引擎)

用法:
    python ocr.py 图片.png
    python ocr.py --paste                 识别剪贴板里的图
    python ocr.py 图片.png -l zh-Hans     指定语言优先级
    python ocr.py 图片.png -c 0.5         丢弃置信度 < 0.5 的结果
    python ocr.py 图片.png --json         输出带坐标/置信度的 JSON

退出码: 0 有文字, 1 没识别到, 2 读不到图片
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile

import Quartz
import Vision
from Foundation import NSURL

DEFAULT_LANGS = ["zh-Hans", "zh-Hant", "en-US"]


def _cgimage_from_path(path: str):
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        return None
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def _cgimage_from_bytes(data: bytes):
    src = Quartz.CGImageSourceCreateWithData(data, None)
    if src is None:
        return None
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def _clipboard_bytes():
    """取剪贴板里的图片(PNG 优先,退回 TIFF)。"""
    import AppKit

    pb = AppKit.NSPasteboard.generalPasteboard()
    for t in ("public.png", "public.tiff", "NSPasteboardTypePNG"):
        d = pb.dataForType_(t)
        if d is not None:
            return bytes(d)
    return None


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF)


def _smart_join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if not out:
            out = p
            continue
        if not _is_cjk(out[-1]) and not _is_cjk(p[0]):
            out += " "
        out += p
    return out


def ocr(source: str, langs=None, min_conf: float = 0.0, timeout: float = 60.0):
    """识别图片,返回 [(文本, 置信度, (x, y, w, h)), ...],已按阅读顺序排好。"""
    langs = langs or DEFAULT_LANGS
    if source in ("--paste", "-p"):
        data = _clipboard_bytes()
        if data is None:
            raise FileNotFoundError("剪贴板里没有图片")
        cg = _cgimage_from_bytes(data)
    else:
        cg = _cgimage_from_path(source)
    if cg is None:
        raise FileNotFoundError(f"读不到图片: {source}")

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(list(langs))

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"OCR 失败: {err}")

    items = []
    for obs in (req.results() or []):
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        c = cands[0]
        conf = float(c.confidence())
        text = str(c.string()).strip()
        if not text or conf < min_conf:
            continue
        b = obs.boundingBox()
        items.append((text, conf,
                      (b.origin.x, b.origin.y, b.size.width, b.size.height)))

    # Vision 的 boundingBox 原点在左下角,y 越大越靠上
    items.sort(key=lambda it: -it[2][1])

    lines: list[list] = []
    for it in items:
        if lines:
            ref = lines[-1][0]
            h = max(ref[2][3], it[2][3])
            if abs(ref[2][1] - it[2][1]) < h * 0.55:
                lines[-1].append(it)
                continue
        lines.append([it])

    out = []
    for line in lines:
        line.sort(key=lambda it: it[2][0])
        out.append((_smart_join([x[0] for x in line]),
                    min(x[1] for x in line),
                    line[0][2]))
    return out


def main():
    ap = argparse.ArgumentParser(description="macOS Vision 本地 OCR")
    ap.add_argument("image", nargs="?", help="图片路径;用 --paste 或 - 表示剪贴板")
    ap.add_argument("-p", "--paste", action="store_true", help="识别剪贴板图片")
    ap.add_argument("-l", "--langs", default=",".join(DEFAULT_LANGS),
                    help="语言优先级,逗号分隔")
    ap.add_argument("-c", "--min-confidence", type=float, default=0.0)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--clean", action="store_true",
                    help="整理文本:接回硬换行、去掉行尾断词连字符")
    args = ap.parse_args()

    source = "--paste" if args.paste else args.image
    if source is None:
        ap.error("需要图片路径,或用 -p/--paste")

    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    try:
        rows = ocr(source, langs=langs, min_conf=args.min_confidence)
    except Exception as e:
        print(e, file=sys.stderr)
        return 2

    if not rows:
        print("没有识别到文字", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(
            [{"text": t, "confidence": c,
              "box": {"x": b[0], "y": b[1], "w": b[2], "h": b[3]}}
             for t, c, b in rows],
            ensure_ascii=False, indent=2))
    else:
        joined = "\n".join(t for t, _, _ in rows)
        if args.clean:
            import textfix
            joined = textfix.clean_ocr_text(joined)
        print(joined)
    return 0


if __name__ == "__main__":
    sys.exit(main())
