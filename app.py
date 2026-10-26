#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SnapSpeak —— 菜单栏 App(截图 → OCR → 朗读)

- 常驻菜单栏,点图标或按全局快捷键即可取词
- 设置窗口用 WKWebView 渲染本地 HTML,Python ↔ JS 走 messageHandler
- 模型常驻内存,第一次之后每次取词只要 0.3 秒左右

注意:NSObject 子类里的方法默认会被 pyobjc 当成 ObjC selector,
纯 Python 的辅助方法必须加 @objc.python_method,否则会报参数个数不匹配。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import AppKit
import objc
import WebKit
from Foundation import NSURL
from objc import python_method

import core
import player as player_mod
import secret as secret_mod
import translate as translate_mod
from hotkey import GlobalHotKey

UI_DIR = os.path.join(HERE, "ui")
APP_TITLE = "SnapSpeak"

# 自检模式:设了环境变量就渲染一次界面、存成 PNG 然后退出(开发时用)
SELFTEST = os.environ.get("SNAPSPEAK_SELFTEST")
SELFTEST_DELAY = float(os.environ.get("SNAPSPEAK_SELFTEST_DELAY", "3.0"))


def log(*args):
    """运行时日志(stderr 会被 .app 启动脚本重定向到 ~/Library/Logs/SnapSpeak.log)"""
    print(f"[{time.strftime('%H:%M:%S')}]", *args, file=sys.stderr, flush=True)


def to_py(o):
    """NSDictionary / NSArray -> 原生 Python 对象。"""
    if o is None:
        return None
    if isinstance(o, (str, int, float, bool)):
        return o
    if isinstance(o, dict):
        return {str(k): to_py(v) for k, v in o.items()}
    if hasattr(o, "allKeys") and hasattr(o, "objectForKey_"):
        return {str(k): to_py(o.objectForKey_(k)) for k in o.allKeys()}
    if hasattr(o, "count") and hasattr(o, "objectAtIndex_"):
        return [to_py(o.objectAtIndex_(i)) for i in range(o.count())]
    if isinstance(o, (list, tuple)):
        return [to_py(x) for x in o]
    return o


def voice_label(vid: str) -> str:
    m = {
        "zf_": "中文女声", "zm_": "中文男声",
        "af_": "美式女声", "am_": "美式男声",
        "bf_": "英式女声", "bm_": "英式男声",
        "ef_": "西语女声", "em_": "西语男声",
        "ff_": "法语女声", "hf_": "印地语女声", "hm_": "印地语男声",
        "if_": "意语女声", "im_": "意语男声",
        "jf_": "日语女声", "jm_": "日语男声",
        "pf_": "葡语女声", "pm_": "葡语男声",
    }
    for p, name in m.items():
        if vid.startswith(p):
            return f"{vid} · {name}"
    return vid


# ---------------------------------------------------------------- JS 桥
class Bridge(AppKit.NSObject):
    """接 WKWebView 的 postMessage。

    不显式继承 objc.protocolNamed('WKScriptMessageHandler')——pyobjc 里那会
    造成 metaclass 冲突;WKWebView 只按 respondsToSelector 调用,够用。
    """

    app = objc.ivar()

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        try:
            body = to_py(message.body()) or {}
            if self.app is not None:
                self.app.handle_bridge(body)
        except Exception as e:  # noqa: BLE001
            print("bridge error:", e, file=sys.stderr)


class NavDelegate(AppKit.NSObject):
    app = objc.ivar()

    def webView_didFinishNavigation_(self, webview, navigation):
        if self.app is None:
            return
        self.app.push_state()
        if SELFTEST:
            self.app.schedule_selftest()


# ---------------------------------------------------------------- 主控
class AppDelegate(AppKit.NSObject):
    def init(self):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.cfg = core.load_settings()
        self.window = None
        self.webview = None
        self.bridge = None
        self.nav = None
        self.hotkey = None
        self.status_item = None
        self._running = False
        self._last = None
        self._voices = {"zh": [], "en": []}
        self.hud = None
        self._pending_audio = None
        self._pending_translation = ""
        return self

    # ---------------------------------------------------- 启动
    def applicationDidFinishLaunching_(self, notification):
        AppKit.NSApp.setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory)
        self.setup_status_item()
        self.setup_hotkey()
        threading.Thread(target=self._warmup, daemon=True).start()
        if SELFTEST:
            self.show_window()

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return False

    def application_openURLs_(self, application, urls):
        """支持 snapspeak://run 和 snapspeak://settings,方便脚本/快捷指令调用。"""
        for url in urls:
            host = (url.host() or "").lower()
            log("URL 触发:", host)
            if host == "run":
                self.start_run()
            elif host == "settings":
                self.show_window()

    @python_method
    def _warmup(self):
        for lg in ("zh", "en"):
            self._voices[lg] = [
                {"id": v, "label": voice_label(v)} for v in core.voices(lg)
            ]
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "pushStateOnMain:", None, False)
        core.warmup(("zh",))
        log("模型预热完成")

    # ---------------------------------------------------- 菜单栏
    @python_method
    def setup_status_item(self):
        bar = AppKit.NSStatusBar.systemStatusBar()
        if self.status_item is not None:
            bar.removeStatusItem_(self.status_item)
        self.status_item = bar.statusItemWithLength_(
            AppKit.NSVariableStatusItemLength)
        btn = self.status_item.button()
        img = None
        try:
            img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "text.viewfinder", "SnapSpeak")
        except Exception:
            img = None
        if img is not None:
            img.setTemplate_(True)
            btn.setImage_(img)
        else:
            btn.setTitle_("◍")

        hk = self.cfg.get("hotkey") or {}
        menu = AppKit.NSMenu.alloc().init()

        mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "截图朗读", "triggerCapture:", "")
        mi.setTarget_(self)
        menu.addItem_(mi)

        mi2 = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"快捷键  {hk.get('label') or '未设置'}", None, "")
        mi2.setEnabled_(False)
        menu.addItem_(mi2)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        mi3 = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "设置…", "openSettings:", ",")
        mi3.setTarget_(self)
        menu.addItem_(mi3)

        mi4 = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出", "quitApp:", "q")
        mi4.setTarget_(self)
        menu.addItem_(mi4)

        self.status_item.setMenu_(menu)
        log("菜单栏图标已创建;当前快捷键 =", hk.get("label") or "未设置")

    @objc.IBAction
    def triggerCapture_(self, sender):
        self.start_run()

    @objc.IBAction
    def openSettings_(self, sender):
        self.show_window()

    @objc.IBAction
    def quitApp_(self, sender):
        if self.hud is not None:
            self.hud.stop()
        AppKit.NSApp.terminate_(None)

    # ---------------------------------------------------- 快捷键
    @python_method
    def setup_hotkey(self):
        if self.hotkey is None:
            self.hotkey = GlobalHotKey(self._on_hotkey)
        self._apply_hotkey()

    @python_method
    def _apply_hotkey(self):
        hk = self.cfg.get("hotkey")
        if not hk:
            self.hotkey.unregister()
            return
        ok = self.hotkey.register(int(hk["keycode"]), int(hk["modifiers"]))
        log(f"注册全局快捷键 {hk.get('label')} ->", "成功" if ok else "失败(可能被占用)")
        if not ok:
            core.notify(f"快捷键 {hk.get('label','')} 注册失败,可能被占用",
                        "换一个组合试试")

    @python_method
    def _on_hotkey(self):
        self.start_run()

    # ---------------------------------------------------- 跑一次
    @python_method
    def start_run(self, source=None):
        if self._running:
            return
        self._running = True
        log("开始取词,模式 =", self.cfg.get("capture_mode"))
        self.push_status("取词中…")
        threading.Thread(target=self._worker, args=(source,), daemon=True).start()

    @python_method
    def _worker(self, source):
        def progress(msg):
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "statusFromWorker:", msg, False)

        self._last = core.run(self.cfg, source=source, progress=progress,
                              defer_play=True)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "runFinished:", None, False)

    def statusFromWorker_(self, msg):
        self.push_status(str(msg))

    def runFinished_(self, _):
        self._running = False
        res = self._last or {}
        if res.get("error") == "permission":
            self.push_status("缺少屏幕录制权限")
            core.open_screen_permission_settings()
            self.push_state()
            return
        if res.get("cancelled"):
            self.push_status("已取消")
            return
        if not res.get("ok"):
            self.push_status(res.get("error") or "失败")
            return
        log(f"取词完成 {len(res.get('text',''))} 字 / {res.get('ms',0)} ms"
            f" / {res.get('lang')}"
            + (" / 已翻译" if res.get("translated") else "")
            + (f" / 翻译失败: {res.get('translate_error')}"
               if res.get("translate_error") else ""))
        tag = "译文 " if res.get("translated") else ""
        self.push_status(f"✓ {tag}{len(res.get('text',''))} 字 · {res.get('ms',0)} ms")
        self.push_state()
        translation = res.get("translation") or ""
        if res.get("audio"):
            self.play_audio(res["audio"], translation)
        elif translation:
            self.show_translation_only(translation)

    # ---------------------------------------------------- 设置窗口
    @python_method
    def show_window(self):
        if self.window is None:
            self._build_window()
        self.push_state()
        self.window.makeKeyAndOrderFront_(None)
        log("设置窗口已显示")
        if not SELFTEST:
            AppKit.NSApp.activateIgnoringOtherApps_(True)

    @python_method
    def _build_window(self):
        rect = AppKit.NSMakeRect(0, 0, 760, 700)
        mask = (AppKit.NSWindowStyleMaskTitled
                | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable)
        w = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, mask, AppKit.NSBackingStoreBuffered, False)
        w.setTitle_(APP_TITLE)
        w.setReleasedWhenClosed_(False)
        w.center()
        w.setDelegate_(self)

        conf = WebKit.WKWebViewConfiguration.alloc().init()
        uc = conf.userContentController()
        bridge = Bridge.alloc().init()
        bridge.app = self
        uc.addScriptMessageHandler_name_(bridge, "bridge")
        self.bridge = bridge

        wv = WebKit.WKWebView.alloc().initWithFrame_configuration_(rect, conf)
        wv.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

        nav = NavDelegate.alloc().init()
        nav.app = self
        wv.setNavigationDelegate_(nav)
        self.nav = nav

        self.webview = wv
        self.window = w
        w.setContentView_(wv)

        index = os.path.join(UI_DIR, "index.html")
        wv.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(index), NSURL.fileURLWithPath_(UI_DIR))

    def windowShouldClose_(self, sender):
        sender.orderOut_(None)
        return False

    # ---------------------------------------------------- JS -> Python
    @python_method
    def handle_bridge(self, msg: dict):
        cmd = msg.get("cmd")
        log("bridge <-", cmd)

        if cmd == "ready":
            self.push_state()

        elif cmd == "save":
            patch = msg.get("settings") or {}
            hk = patch.get("hotkey")
            if isinstance(hk, dict):   # JS 数字是 float,统一存成 int
                hk["keycode"] = int(hk.get("keycode", 0))
                hk["modifiers"] = int(hk.get("modifiers", 0))
            self.cfg.update(patch)
            try:
                core.save_settings(self.cfg)
            except OSError as e:
                self.push_status(f"保存失败: {e}")
                return
            if "hotkey" in patch:
                self._apply_hotkey()
                self.setup_status_item()
            if "hud_font_size" in patch and self.hud is not None:
                self.hud.set_font_size(self.cfg.get("hud_font_size", 12.5))
            self.eval_js("window.ui && window.ui.saved();")

        elif cmd == "save_key":
            key = (msg.get("key") or "").strip()
            ok, info = secret_mod.set_key(key)
            if not ok:
                self.push_status(f"保存失败:{info}")
                self.eval_js("window.ui && window.ui.keyResult(false, %s);"
                             % json.dumps(info, ensure_ascii=False))
                return
            self.push_state()
            self.push_status("已保存,正在验证…" if key else "已清除 API Key")
            self.eval_js("window.ui && window.ui.keyResult(true, %s);"
                         % json.dumps(info, ensure_ascii=False))
            if key:
                threading.Thread(target=self._test_key, args=(key,),
                                 daemon=True).start()

        elif cmd == "audition":
            threading.Thread(target=self._audition,
                             args=(msg.get("lang", "zh"), msg.get("voice")),
                             daemon=True).start()

        elif cmd == "run_now":
            self.start_run()

        elif cmd == "respeak":
            text = msg.get("text") or ""
            if text:
                threading.Thread(
                    target=self._respeak_worker,
                    args=(text, msg.get("translation") or ""),
                    daemon=True).start()
                self.push_status("正在朗读历史记录…")

        elif cmd == "open_config":
            os.makedirs(core.CONFIG_DIR, exist_ok=True)
            subprocess.run(["open", core.CONFIG_DIR], check=False)

        elif cmd == "open_permission":
            core.open_screen_permission_settings()

    @python_method
    def _ensure_hud(self):
        if self.hud is None:
            self.hud = player_mod.PlaybackHUD.alloc().init()
            self.hud.set_saved_frame(self.cfg.get("hud_pos"),
                                     self.cfg.get("hud_size"))
            self.hud.set_pinned(bool(self.cfg.get("pin_translation")))
            self.hud.set_font_size(self.cfg.get("hud_font_size", 12.5))
            self.hud.on_pin_changed = self._on_pin_changed
            self.hud.on_geometry_changed = self._on_hud_geometry
        return self.hud

    @python_method
    def play_audio(self, path, translation: str = ""):
        """用可暂停/可拖动的播放器播,并把译文浮在面板里。"""
        self._ensure_hud().show(path, translation)

    @python_method
    def show_translation_only(self, translation: str):
        self._ensure_hud().show_text(translation)

    @python_method
    def _on_hud_geometry(self, rect, resized):
        """面板被拖动/缩放后记住位置和大小。"""
        self.cfg["hud_pos"] = {"x": rect["x"], "y": rect["y"]}
        if resized:
            self.cfg["hud_size"] = {"w": rect["w"], "h": rect["h"]}
        try:
            core.save_settings(self.cfg)
        except OSError:
            pass
        log(f"HUD 几何 x={rect['x']:.0f} y={rect['y']:.0f} "
            f"w={rect['w']:.0f} h={rect['h']:.0f}"
            + (" (手动缩放)" if resized else ""))

    @python_method
    def _on_pin_changed(self, pinned):
        self.cfg["pin_translation"] = bool(pinned)
        try:
            core.save_settings(self.cfg)
        except OSError:
            pass
        log("译文窗 pin =", pinned)

    def playPending_(self, _):
        path = self._pending_audio
        tr = self._pending_translation
        self._pending_audio = None
        self._pending_translation = ""
        if path:
            self.play_audio(path, tr)

    @python_method
    def _test_key(self, key):
        ok, out = translate_mod.translate("Hello, world.", key)
        if ok:
            msg = "✓ Key 可用"
        else:
            msg = f"✗ {out}"
        log("API Key 测试:", msg)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "statusFromWorker:", msg, False)
        self.eval_js("window.ui && window.ui.keyResult(%s, %s);"
                     % ("true" if ok else "false",
                        json.dumps(msg, ensure_ascii=False)))

    @python_method
    def _respeak_worker(self, text, translation=""):
        res = core.speak_file(text, self.cfg, defer_play=True)
        if res.get("audio"):
            self._pending_audio = res["audio"]
            self._pending_translation = translation
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "playPending:", None, False)

    @python_method
    def _audition(self, lang, voice):
        sample = (f"你好,我是 {voice},这是试听效果。" if lang == "zh"
                  else f"Hi, this is {voice}. This is how I sound.")
        cfg = dict(self.cfg)
        cfg["engine"] = "kokoro"
        cfg["voice_zh" if lang == "zh" else "voice_en"] = voice
        cfg["notify"] = False
        cfg["copy_to_clipboard"] = False
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "statusFromWorker:", f"试听 {voice}…", False)
        core.speak_file(sample, cfg, force_lang=lang)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "statusFromWorker:", "试听完毕", False)

    # ---------------------------------------------------- Python -> JS
    def pushStateOnMain_(self, _):
        self.push_state()

    @python_method
    def push_state(self):
        state = {
            "settings": self.cfg,
            "voices_zh": self._voices["zh"],
            "voices_en": self._voices["en"],
            "permission": core.has_screen_permission(),
            "has_key": bool(secret_mod.get_key()),
        }
        self.eval_js("window.ui && window.ui.setState(%s);"
                     % json.dumps(state, ensure_ascii=False))

    @python_method
    def push_status(self, text: str):
        self.eval_js("window.ui && window.ui.setStatus(%s);"
                     % json.dumps(text, ensure_ascii=False))

    @python_method
    def eval_js(self, js: str):
        if self.webview is None:
            return
        self.webview.evaluateJavaScript_completionHandler_(js, None)

    # ---------------------------------------------------- 自检(开发用)
    @python_method
    def schedule_selftest(self):
        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            SELFTEST_DELAY, self, "selftestFire:", None, False)

    def selftestFire_(self, timer):
        def done(image, error):
            try:
                if image is not None:
                    tiff = image.TIFFRepresentation()
                    rep = AppKit.NSBitmapImageRep.imageRepWithData_(tiff)
                    data = rep.representationUsingType_properties_(
                        AppKit.NSBitmapImageFileTypePNG, {})
                    data.writeToFile_atomically_(SELFTEST, True)
                    print(f"[selftest] 已保存 {SELFTEST}", file=sys.stderr)
                else:
                    print(f"[selftest] 没拿到图: {error}", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"[selftest] 失败: {e}", file=sys.stderr)
            finally:
                AppKit.NSApp.terminate_(None)

        if self.webview is None:
            AppKit.NSApp.terminate_(None)
            return

        probe = ("JSON.stringify({"
                 "zh: document.getElementById('voice_zh').options.length,"
                 "en: document.getElementById('voice_en').options.length,"
                 "zh_sel: document.getElementById('voice_zh').value,"
                 "status: document.getElementById('status').textContent,"
                 "perm: document.getElementById('perm').textContent,"
                 "hist: document.querySelectorAll('#history .item').length,"
                 "scroll_h: document.querySelector('main').scrollHeight,"
                 "client_h: document.querySelector('main').clientHeight,"
                 "fits: document.querySelector('main').scrollHeight"
                 " <= document.querySelector('main').clientHeight"
                 "})")
        self.webview.evaluateJavaScript_completionHandler_(
            probe, lambda r, e: print(f"[selftest] 界面状态: {r} {e or ''}",
                                      file=sys.stderr))

        conf = WebKit.WKSnapshotConfiguration.alloc().init()
        self.webview.takeSnapshotWithConfiguration_completionHandler_(conf, done)


def main():
    app = AppKit.NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()


if __name__ == "__main__":
    main()
