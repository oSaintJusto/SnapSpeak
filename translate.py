#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调用 DeepSeek 把英文翻成中文。

只用标准库 urllib,不额外装依赖。
接口是 OpenAI 兼容格式:POST /chat/completions
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

MAX_INPUT_CHARS = 6000

SYSTEM_PROMPT = (
    "你是翻译引擎。把用户提供的英文翻译成自然、流畅的简体中文。"
    "要求:保留专有名词、技术术语、产品名的通行译法;"
    "不要解释、不要加引号、不要输出原文;只输出译文本身。"
    "如果输入里有缩写或型号,保持原样。"
)


def _friendly_error(code: int, body: str) -> str:
    m = {
        400: "请求格式有问题",
        401: "API Key 无效或已过期",
        402: "账户余额不足",
        403: "没有权限(Key 可能被限制)",
        429: "请求太频繁,稍后再试",
        500: "DeepSeek 服务端错误",
        503: "DeepSeek 服务暂不可用",
    }
    base = m.get(code, f"HTTP {code}")
    try:
        detail = json.loads(body).get("error", {}).get("message", "")
    except Exception:
        detail = body[:120]
    return f"{base}: {detail}" if detail else base


def translate(text: str, api_key: str, timeout: float = 20.0,
              model: str = MODEL) -> tuple[bool, str]:
    """返回 (成功?, 译文 或 错误信息)。"""
    text = (text or "").strip()
    if not text:
        return False, "没有可翻译的内容"
    if not api_key:
        return False, "没有配置 API Key"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:MAX_INPUT_CHARS]},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return False, _friendly_error(e.code, body)
    except urllib.error.URLError as e:
        return False, f"网络不通: {e.reason}"
    except TimeoutError:
        return False, "翻译超时"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"

    try:
        data = json.loads(raw)
        out = (data["choices"][0]["message"]["content"] or "").strip()
    except (ValueError, KeyError, IndexError, TypeError):
        return False, f"返回格式异常: {raw[:120]}"
    if not out:
        return False, "DeepSeek 返回了空译文"
    return True, out
