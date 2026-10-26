#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Carbon 全局快捷键 —— 不需要「辅助功能」权限。

用法:
    hk = GlobalHotKey(on_press)
    hk.register(keycode=1, modifiers=6144)   # ⌃⌥S
    hk.unregister()

注册失败(热键被别的 App 占了)时 register() 返回 False。
"""
from __future__ import annotations

import ctypes
import ctypes.util

carbon = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Carbon"))

kEventClassKeyboard = 0x6B657962        # 'keyb'
kEventHotKeyPressed = 5
kEventHotKeyReleased = 6
SIGNATURE = 0x536E5370                  # 'SnSp'

CMD, SHIFT, OPTION, CONTROL = 256, 512, 2048, 4096


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32),
                ("eventKind", ctypes.c_uint32)]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32),
                ("id", ctypes.c_uint32)]


HandlerProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p,
                               ctypes.c_void_p, ctypes.c_void_p)

carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
carbon.GetApplicationEventTarget.argtypes = []

carbon.InstallEventHandler.restype = ctypes.c_int32
carbon.InstallEventHandler.argtypes = [
    ctypes.c_void_p, HandlerProc, ctypes.c_ulong,
    ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]

carbon.RegisterEventHotKey.restype = ctypes.c_int32
carbon.RegisterEventHotKey.argtypes = [
    ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID,
    ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
]

carbon.UnregisterEventHotKey.restype = ctypes.c_int32
carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]


class GlobalHotKey:
    def __init__(self, on_press):
        self._on_press = on_press
        self._ref = ctypes.c_void_p()
        self._proc = HandlerProc(self._handle)   # 必须持有引用,否则被回收
        self._handler_ref = ctypes.c_void_p()
        self._spec = EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
        self._target = carbon.GetApplicationEventTarget()
        self._installed = False
        self._register_handler()

    def _register_handler(self):
        if self._target is None:
            return
        err = carbon.InstallEventHandler(
            self._target, self._proc, 1, ctypes.byref(self._spec),
            None, ctypes.byref(self._handler_ref))
        self._installed = (err == 0)

    def _handle(self, next_handler, event, user_data):
        try:
            self._on_press()
        except Exception:  # 绝不能让异常抛回 C 栈
            pass
        return 0  # noErr

    @property
    def registered(self) -> bool:
        return bool(self._ref.value)

    def register(self, keycode: int, modifiers: int) -> bool:
        """返回 False 表示被占用 / 注册失败。"""
        self.unregister()
        hkid = EventHotKeyID(SIGNATURE, 1)
        err = carbon.RegisterEventHotKey(
            keycode, modifiers, hkid, self._target, 0, ctypes.byref(self._ref))
        if err != 0:
            self._ref = ctypes.c_void_p()
            return False
        return True

    def unregister(self):
        if self._ref.value:
            carbon.UnregisterEventHotKey(self._ref)
            self._ref = ctypes.c_void_p()


def describe(modifiers: int, keycode: int, keyname: str = "") -> str:
    s = ""
    if modifiers & CONTROL:
        s += "⌃"
    if modifiers & OPTION:
        s += "⌥"
    if modifiers & SHIFT:
        s += "⇧"
    if modifiers & CMD:
        s += "⌘"
    return s + keyname
