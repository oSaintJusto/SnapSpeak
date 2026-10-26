#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API Key 的存取 —— 存在 macOS 钥匙串里,不落到明文配置文件。

命令行里也能直接看/改:
    security find-generic-password -a snapspeak -s "SnapSpeak DeepSeek" -w
"""
from __future__ import annotations

import os
import subprocess

SERVICE = "SnapSpeak DeepSeek"
ACCOUNT = "snapspeak"
ENV_VAR = "DEEPSEEK_API_KEY"


def get_key() -> str:
    """钥匙串优先,其次环境变量。取不到返回空串。"""
    try:
        p = subprocess.run(
            ["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
            capture_output=True, text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get(ENV_VAR, "").strip()


def set_key(key: str) -> tuple[bool, str]:
    """写入钥匙串;key 为空表示删除。"""
    key = (key or "").strip()
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-a", ACCOUNT, "-s", SERVICE],
            capture_output=True, timeout=10)
        if not key:
            return True, "已清除"
        # -U 覆盖同名项,-A 允许任意程序读取(避免每次弹钥匙串授权框)
        p = subprocess.run(
            ["security", "add-generic-password", "-a", ACCOUNT, "-s", SERVICE,
             "-w", key, "-U", "-A"],
            capture_output=True, text=True, timeout=10)
        if p.returncode != 0:
            return False, (p.stderr or "写入钥匙串失败").strip()[:200]
        return True, "已保存"
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:200]


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:6]}…{key[-4:]}"
