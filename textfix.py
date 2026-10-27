#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OCR 文本整理 —— 修复「跨行被切成两半」的问题。

截图里的正文经常是硬换行的,而且 PDF / LaTeX 还会在行尾加断词连字符:

    ... for direct robotic con-
    trol, yet their performance ...

如果原样念,`con` 和 `trol` 会被当成两个词。这里做两件事:

1. **去断词**:行尾连字符 + 下一行小写字母 → 接回一个词
2. **重排硬换行**:同一段里的断行接成一句(否则 TTS 会一行一行念,句中断气)

难点在于 `con-trol`(断词)和 `test-time`(真连字符)长得一模一样。
这里用系统词典 `/usr/share/dict/words` 判断:

    拼起来是个词        → 去掉连字符      control / insufficient / physical
    两半各自都是词       → 保留连字符      test-time / fine-tuning / real-time
    都不是              → 去掉连字符      relationships / SpatialHarness

没有词典时退化成「一律去掉连字符」,仍然比不处理强得多。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

DICT_PATHS = ("/usr/share/dict/words", "/usr/dict/words")

# 行尾连字符(含常见变体:U+2010 / U+2011 / 软连字符)
HYPHEN_END = re.compile(r"[\w)\]]\s*[-\u2010\u2011\u00ad]$")

# 句末:遇到这些就不再往下接
SENT_END = re.compile(r"[.!?\u3002\uff01\uff1f\u2026:]['\"”’)）\]]*\s*$")

# 列表 / 项目符号 / 小标题编号,不该和前一行粘在一起
ITEM_START = re.compile(
    r"^\s*(?:[-•*·▪◦‣]|\d+[.)]\s|\d+\u3001|[（(]\d+[)）]|[一二三四五六七八九十]+[、.])"
)

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
WS = re.compile(r"[ \t\u00a0]{2,}")


@lru_cache(maxsize=1)
def _dictionary() -> frozenset:
    for p in DICT_PATHS:
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    return frozenset(line.strip().lower() for line in f if line.strip())
            except OSError:
                continue
    return frozenset()


def _is_word(token: str) -> bool:
    d = _dictionary()
    if not d or not token:
        return False
    return token.lower() in d


def _strip_punct(token: str) -> str:
    return token.strip(".,;:!?'\"“”‘’()[]{}")


def _is_cjk(ch: str) -> bool:
    return bool(ch) and bool(CJK.match(ch))


def _sep(a: str, b: str) -> str:
    """中文之间不加空格,其余加一个空格。"""
    if not a or not b:
        return ""
    if _is_cjk(a[-1]) or _is_cjk(b[0]):
        return ""
    return " "


def _merge_hyphen(prev: str, nxt: str) -> str:
    """处理行尾连字符,决定接成 control 还是 test-time。"""
    head = prev.rstrip()[:-1]           # 去掉那个连字符
    m = re.search(r"[^\s]*$", head)     # 连字符所在的"词"
    first = _strip_punct(m.group(0)) if m else ""
    second = _strip_punct(re.match(r"[^\s]*", nxt).group(0)) if nxt else ""

    # 前半截本身已经是复合词(state-of- + the-art),保留连字符
    if "-" in first:
        return head + "-" + nxt

    # 下一行以大写字母开头(Open- / AI),按真连字符处理
    if nxt[:1].isupper():
        return head + "-" + nxt

    if first and second:
        if _is_word(first + second):
            return head + nxt                      # con + trol -> control
        if _is_word(first) and _is_word(second):
            return head + "-" + nxt                # test + time -> test-time
    return head + nxt                              # 保底:去掉连字符


# ---------------------------------------------------------------- 希腊字母
# Vision 没有希腊语模型(supportedRecognitionLanguages 里没有 el-GR),
# π 一类符号会被强行塞成拉丁字符。实测:
#     π0.5   -> "T0.5" / "to.5"
#     π0     -> "TO"
# 下面只挑「上下文足够明确」的模式修,避免误伤正常英文:
# 字母紧跟 ".数字" 在英文里几乎不存在,所以这条是安全的。
# 注意:Vision 有时给出的是 "70.5"(数字零),和真实的 70.5 无法区分,不做处理。
GREEK_RULES = (
    (re.compile(r"\b[Tt]0\.(\d)"), "π0.\\1"),      # T0.5  -> π0.5
    (re.compile(r"\b[Tt][Oo]\.(\d)"), "π0.\\1"),   # to.5  -> π0.5
    (re.compile(r"\b[Nn][Oo]\.(\d)"), "π0.\\1"),   # no.5  -> π0.5
    (re.compile(r"\b[Tt][Oo]\b(?=\s*[（(])"), "π0"),  # TO(   -> π0(
    (re.compile(r"\b[Nn][Oo]\b(?=\s*[（(])"), "π0"),  # no(   -> π0(
)


def repair_greek(text: str) -> str:
    """把被认成拉丁字母的 π 修回来(π 在 espeak 里能正确读作 "pi")。"""
    if not text:
        return text
    for pattern, repl in GREEK_RULES:
        text = pattern.sub(repl, text)
    return text


def apply_literal(text: str, pairs) -> str:
    """用户自定义的字面替换,按顺序执行。pairs 是 [(from, to), ...]。"""
    if not text or not pairs:
        return text
    for item in pairs:
        try:
            a, b = item[0], item[1]
        except (TypeError, IndexError, KeyError):
            continue
        if a:
            text = text.replace(str(a), str(b))
    return text


def clean_ocr_text(text: str, fix_greek: bool = True,
                   replacements=()) -> str:
    """把 OCR 出来的多行文本整理成通顺的段落。"""
    if not text or "\n" not in text:
        one = WS.sub(" ", text) if text else text
        if fix_greek:
            one = repair_greek(one)
        return apply_literal(one, replacements)

    lines = text.split("\n")
    out: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if not out:
            out.append(line)
            continue
        prev = out[-1]
        if not prev.strip() or not line.strip():   # 空行 = 段落分隔
            out.append(line)
            continue

        nxt = line.lstrip()
        if ITEM_START.match(nxt):                  # 列表项各自成行
            out.append(line)
            continue

        if HYPHEN_END.search(prev) and nxt[:1].isalnum():
            out[-1] = _merge_hyphen(prev, nxt)
            continue

        if SENT_END.search(prev):                  # 上一行已经是句末
            out.append(line)
            continue

        out[-1] = prev + _sep(prev, nxt) + nxt     # 同一段内的硬换行

    result = WS.sub(" ", "\n".join(out))
    if fix_greek:
        result = repair_greek(result)
    return apply_literal(result, replacements)
