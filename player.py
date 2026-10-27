#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
朗读时的悬浮面板 —— 暂停 / 拖进度 / 显示中文译文 / 钉住 / 拖动 / 缩放。

用 AVAudioPlayer 代替 afplay,因为子进程没法暂停和定位。

    hud = PlaybackHUD.alloc().init()
    hud.set_saved_frame(pos, size)        # 恢复上次的位置和大小
    hud.on_pin_changed = lambda pinned: ...
    hud.on_geometry_changed = lambda rect, resized: ...
    hud.show(path, translation="中文译文")

交互:
    · 面板任意空白处按住拖动 → 移动(位置会被记住)
    · 右下角小斜纹按住拖动   → 缩放(大小会被记住)
"""
from __future__ import annotations

import os

import AppKit
import objc
from AVFoundation import AVAudioPlayer
from Foundation import NSURL
from objc import python_method

W_DEFAULT = 460
GRAB_H = 14.0              # 顶部拖动把手区高度
BAR_H = 56                 # 有播放控件时控制条高度
BAR_H_TEXT = 30            # 纯译文模式控制条高度
BOTTOM_MARGIN = 64
MAX_TEXT_H = 300.0         # 自适应时译文区最高多少
PAD = 12.0
MIN_W = 320.0
GRIP = 16.0


def _fmt(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60}:{t % 60:02d}"


def _symbol(name: str, point: float):
    try:
        img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            name, None)
        if img is None:
            return None
        cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            point, AppKit.NSFontWeightSemibold)
        return img.imageWithSymbolConfiguration_(cfg)
    except Exception:
        return None


def _white(a: float):
    return AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, a)


# ---------------------------------------------------------------- 自定义视图
class HUDPanel(AppKit.NSPanel):
    """无边框面板默认 canBecomeKeyWindow = NO,文字就选不中。
    显式允许它成为 key(配合 becomesKeyOnlyIfNeeded,只在选中文字时才抢)。"""

    def canBecomeKeyWindow(self):
        return True


class HUDContentView(AppKit.NSView):
    """按住拖动整个窗口。"""

    hud = objc.ivar()

    def drawRect_(self, rect):
        """顶部画一条把手,提示这里能按住拖动。"""
        hud = self.hud
        if hud is None:
            return
        b = self.bounds()
        pw, ph = 40.0, 4.0
        x = (b.size.width - pw) / 2.0
        y = b.size.height - 10.0
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(x, y, pw, ph), ph / 2.0, ph / 2.0)
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22).set()
        path.fill()

    def mouseDown_(self, event):
        self._start = AppKit.NSEvent.mouseLocation()
        self._origin = self.hud.panel.frame().origin

    def mouseDragged_(self, event):
        cur = AppKit.NSEvent.mouseLocation()
        x = self._origin.x + (cur.x - self._start.x)
        y = self._origin.y + (cur.y - self._start.y)
        self.hud.panel.setFrameOrigin_((x, y))

    def mouseUp_(self, event):
        self.hud.report_geometry(resized=False)


class ResizeGrip(AppKit.NSView):
    """右下角缩放把手。"""

    hud = objc.ivar()

    def drawRect_(self, rect):
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.35).set()
        path = AppKit.NSBezierPath.bezierPath()
        b = self.bounds()
        for i, off in enumerate((3.0, 7.0, 11.0)):
            path.moveToPoint_((b.size.width - off, 1.0))
            path.lineToPoint_((b.size.width - 1.0, off))
        path.setLineWidth_(1.2)
        path.stroke()

    def mouseDown_(self, event):
        self._start = AppKit.NSEvent.mouseLocation()
        self._frame = self.hud.panel.frame()

    def mouseDragged_(self, event):
        cur = AppKit.NSEvent.mouseLocation()
        dx = cur.x - self._start.x
        dy = cur.y - self._start.y
        f = self._frame
        bar = self.hud.chrome_height()
        vis = AppKit.NSScreen.mainScreen().visibleFrame()
        w = max(MIN_W, f.size.width + dx)
        w = min(w, vis.size.width - 40.0)
        h = max(bar + 30.0, f.size.height - dy)
        h = min(h, vis.size.height - 20.0)
        # 按住右下角缩放时,顶部边缘保持不动
        y = f.origin.y + (f.size.height - h)
        self.hud.panel.setFrame_display_(
            AppKit.NSMakeRect(f.origin.x, y, w, h), True)
        self.hud.relayout()

    def mouseUp_(self, event):
        self.hud.report_geometry(resized=True)


# ---------------------------------------------------------------- 主类
class PlaybackHUD(AppKit.NSObject):
    def init(self):
        self = objc.super(PlaybackHUD, self).init()
        if self is None:
            return None
        self.panel = None
        self.player = None
        self.timer = None
        self.path = None
        self.slider = None
        self.play_btn = None
        self.pin_btn = None
        self.close_btn = None
        self.time_label = None
        self.text_label = None
        self.grip = None
        self.pinned = False
        self._controls_visible = True
        self._finished = False
        self._on_done = None
        self._saved_pos = None
        self._saved_size = None
        self.font_size = 12.5
        self.on_pin_changed = None
        self.on_geometry_changed = None
        return self

    # ------------------------------------------------------------ 对外
    @python_method
    def set_saved_frame(self, pos, size):
        """pos/size 是 {'x','y'} / {'w','h'} 形式的 dict 或 None。"""
        self._saved_pos = pos if isinstance(pos, dict) else None
        self._saved_size = size if isinstance(size, dict) else None

    @python_method
    def set_font_size(self, pt: float):
        """调整译文区字号;正在显示的话会立刻重排(高度没手动缩过就自适应)。"""
        try:
            self.font_size = max(8.0, min(28.0, float(pt)))
        except (TypeError, ValueError):
            return
        if self.text_label is not None:
            self.text_label.setFont_(
                AppKit.NSFont.systemFontOfSize_(self.font_size))
        if self.panel is not None and not self.text_label.isHidden():
            self._set_translation(self.text_label.stringValue())

    @python_method
    def bar_height(self):
        return BAR_H if self._controls_visible else BAR_H_TEXT

    @python_method
    def chrome_height(self):
        """顶部把手 + 控制条 —— 文字区之外的部分。"""
        return GRAB_H + self.bar_height()

    @python_method
    def show(self, path: str, translation: str = "", on_done=None):
        if self.panel is None:
            self._build()

        self._teardown_player()

        url = NSURL.fileURLWithPath_(path)
        player, err = AVAudioPlayer.alloc().initWithContentsOfURL_error_(url, None)
        if player is None:
            print(f"[hud] 播放器初始化失败: {err}")
            try:
                os.unlink(path)
            except OSError:
                pass
            return False

        self.player = player
        self.path = path
        self._on_done = on_done
        self._finished = False
        player.setDelegate_(self)
        player.prepareToPlay()

        dur = float(player.duration() or 0)
        self.slider.setMinValue_(0.0)
        self.slider.setMaxValue_(max(dur, 0.01))
        self.slider.setDoubleValue_(0.0)

        self._set_controls_visible(True)
        self._set_translation(translation)
        self._refresh_time()
        self._set_play_icon(True)

        self.panel.orderFrontRegardless()      # 不抢焦点
        player.play()

        self.timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.15, self, "tick:", None, True)
        return True

    @python_method
    def show_text(self, translation: str):
        """不朗读、只显示译文(比如关了自动朗读)。"""
        if self.panel is None:
            self._build()
        self._teardown_player()
        self._set_controls_visible(False)
        self._set_translation(translation)
        self.panel.orderFrontRegardless()
        return True

    @python_method
    def stop(self):
        self._teardown_player()
        if self.panel is not None:
            self.panel.orderOut_(None)

    @python_method
    def set_pinned(self, pinned: bool):
        self.pinned = bool(pinned)
        self._sync_pin_icon()

    @python_method
    def report_geometry(self, resized: bool):
        if self.panel is None or not self.on_geometry_changed:
            return
        f = self.panel.frame()
        rect = {"x": float(f.origin.x), "y": float(f.origin.y),
                "w": float(f.size.width), "h": float(f.size.height)}
        self._saved_pos = {"x": rect["x"], "y": rect["y"]}
        if resized:
            self._saved_size = {"w": rect["w"], "h": rect["h"]}
        try:
            self.on_geometry_changed(rect, resized)
        except Exception:
            pass

    # ------------------------------------------------------------ 播放器
    @python_method
    def _teardown_player(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        if self.player is not None:
            try:
                self.player.stop()
                self.player.setDelegate_(None)
            except Exception:
                pass
            self.player = None
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.path = None
        self._finished = False

    @python_method
    def _set_play_icon(self, playing: bool):
        img = _symbol("pause.fill" if playing else "play.fill", 15)
        if img is not None:
            self.play_btn.setImage_(img)

    @python_method
    def _sync_pin_icon(self):
        if self.pin_btn is None:
            return
        img = _symbol("pin.fill" if self.pinned else "pin", 13)
        if img is not None:
            self.pin_btn.setImage_(img)
        self.pin_btn.setContentTintColor_(
            AppKit.NSColor.systemBlueColor() if self.pinned else _white(0.55))

    @python_method
    def _refresh_time(self):
        if self.player is None:
            return
        cur = float(self.player.currentTime() or 0)
        dur = float(self.player.duration() or 0)
        self.time_label.setStringValue_(f"{_fmt(cur)} / {_fmt(dur)}")
        dragging = False
        try:
            dragging = bool(self.slider.cell().isHighlighted())
        except Exception:
            pass
        if not dragging:
            self.slider.setDoubleValue_(cur)

    @python_method
    def _set_controls_visible(self, visible: bool):
        self._controls_visible = bool(visible)
        for w in (self.play_btn, self.slider, self.time_label):
            if w is not None:
                w.setHidden_(not visible)

    # ------------------------------------------------------------ 布局
    @python_method
    def _measure_text_h(self, width: float) -> float:
        txt = self.text_label.stringValue()
        if not txt:
            return 0.0
        cell = self.text_label.cell()
        cell.setWraps_(True)
        size = cell.cellSizeForBounds_(
            AppKit.NSMakeRect(0, 0, max(width, 40.0), 100000))
        return float(size.height)

    @python_method
    def _set_translation(self, translation: str):
        """换译文后重新定尺寸:没人手动缩过就自适应,缩过就尊重用户的尺寸。"""
        has_text = bool(translation)
        self.text_label.setStringValue_(translation or "")
        self.text_label.setHidden_(not has_text)

        w = float(self._saved_size["w"]) if self._saved_size else float(W_DEFAULT)
        if self._saved_size:
            h = float(self._saved_size["h"])
        else:
            text_h = self._measure_text_h(w - 2 * PAD)
            text_h = min(text_h, MAX_TEXT_H) if has_text else 0.0
            h = self.chrome_height() + (text_h + 18.0 if text_h else 0.0)

        x, y = self._default_origin(w, h)
        self.panel.setFrame_display_(AppKit.NSMakeRect(x, y, w, h), False)
        self.relayout()

    @python_method
    def _default_origin(self, w: float, h: float):
        vis = AppKit.NSScreen.mainScreen().visibleFrame()
        if self._saved_pos:
            x = float(self._saved_pos["x"])
            y = float(self._saved_pos["y"])
            # 换过显示器 / 分辨率变了也要能看见
            x = min(max(x, vis.origin.x + 8), vis.origin.x + vis.size.width - w - 8)
            y = min(max(y, vis.origin.y + 8), vis.origin.y + vis.size.height - h - 8)
            return x, y
        return (vis.origin.x + (vis.size.width - w) / 2,
                vis.origin.y + BOTTOM_MARGIN)

    @python_method
    def relayout(self):
        """按当前面板尺寸重排所有控件。"""
        if self.panel is None:
            return
        f = self.panel.frame()
        w, h = float(f.size.width), float(f.size.height)
        bar_h = self.bar_height()
        bar_y = h - GRAB_H - bar_h
        centre = bar_y + (bar_h - 28) / 2

        self.play_btn.setFrame_(AppKit.NSMakeRect(12, centre, 28, 28))
        self.pin_btn.setFrame_(AppKit.NSMakeRect(w - 62, centre, 28, 28))
        self.close_btn.setFrame_(
            AppKit.NSMakeRect(w - 26, bar_y + (bar_h - 16) / 2, 16, 16))
        self.time_label.setFrame_(AppKit.NSMakeRect(w - 158, bar_y + 20, 84, 16))
        slider_w = max(w - 216.0, 60.0)
        self.slider.setFrame_(AppKit.NSMakeRect(50, bar_y + 19, slider_w, 18))

        text_h = max(h - self.chrome_height() - 18.0, 0.0)
        self.text_label.setFrame_(
            AppKit.NSMakeRect(PAD, 10.0, max(w - 2 * PAD, 40.0), text_h))
        self.grip.setFrame_(AppKit.NSMakeRect(w - GRIP - 4, 3, GRIP, GRIP))

    # ------------------------------------------------------------ 构建
    @python_method
    def _build(self):
        mask = (AppKit.NSWindowStyleMaskBorderless
                | AppKit.NSWindowStyleMaskNonactivatingPanel)
        w0 = float(self._saved_size["w"]) if self._saved_size else float(W_DEFAULT)
        h0 = float(self._saved_size["h"]) if self._saved_size else float(BAR_H)
        panel = HUDPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, w0, h0), mask,
            AppKit.NSBackingStoreBuffered, False)
        # 浮在所有 App 普通窗口之上(floating level 的层级判定是全局的),
        # 同时允许出现在全屏 App 和所有 Space 上,不随 App 失活而消失。
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)

        content = HUDContentView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, w0, h0))
        content.hud = self
        panel.setContentView_(content)
        content.setWantsLayer_(True)
        layer = content.layer()
        if layer is not None:
            layer.setBackgroundColor_(
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.13, 0.94).CGColor())
            layer.setCornerRadius_(14.0)
            layer.setMasksToBounds_(True)

        # 播放 / 暂停
        btn = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 28, 28))
        btn.setBordered_(False)
        btn.setImagePosition_(AppKit.NSImageOnly)
        btn.setTarget_(self)
        btn.setAction_("togglePlay:")
        btn.setContentTintColor_(AppKit.NSColor.whiteColor())
        self.play_btn = btn
        self._set_play_icon(True)
        content.addSubview_(btn)

        # 进度
        slider = AppKit.NSSlider.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 250, 18))
        slider.setMinValue_(0.0)
        slider.setMaxValue_(1.0)
        slider.setContinuous_(True)
        slider.setTarget_(self)
        slider.setAction_("seek:")
        self.slider = slider
        content.addSubview_(slider)

        # 时间
        label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 84, 16))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setAlignment_(AppKit.NSTextAlignmentRight)
        label.setTextColor_(_white(0.75))
        label.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
            11.0, AppKit.NSFontWeightRegular))
        label.setStringValue_("0:00 / 0:00")
        self.time_label = label
        content.addSubview_(label)

        # 钉住
        pin = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 28, 28))
        pin.setBordered_(False)
        pin.setImagePosition_(AppKit.NSImageOnly)
        pin.setTarget_(self)
        pin.setAction_("togglePin:")
        self.pin_btn = pin
        self._sync_pin_icon()
        content.addSubview_(pin)

        # 关闭
        close = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 16, 16))
        close.setBordered_(False)
        close.setTarget_(self)
        close.setAction_("closeHud:")
        cimg = _symbol("xmark", 11)
        if cimg is not None:
            close.setImage_(cimg)
        else:
            close.setTitle_("✕")
        close.setContentTintColor_(_white(0.55))
        self.close_btn = close
        content.addSubview_(close)

        # 译文(穿透鼠标,方便拖动)
        text = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(PAD, 10, w0 - 2 * PAD, 16))
        text.setBezeled_(False)
        text.setDrawsBackground_(False)
        text.setEditable_(False)
        text.setSelectable_(True)     # 可以选中、⌘C 复制
        text.setAlignment_(AppKit.NSTextAlignmentLeft)
        text.setTextColor_(_white(0.92))
        text.setFont_(AppKit.NSFont.systemFontOfSize_(self.font_size))
        text.setStringValue_("")
        text.setHidden_(True)
        self.text_label = text
        content.addSubview_(text)

        # 缩放把手
        grip = ResizeGrip.alloc().initWithFrame_(
            AppKit.NSMakeRect(w0 - GRIP - 4, 3, GRIP, GRIP))
        grip.hud = self
        self.grip = grip
        content.addSubview_(grip)

        self.panel = panel

    # ------------------------------------------------------------ 动作
    def togglePlay_(self, sender):
        if self.player is None:
            return
        if self.player.isPlaying():
            self.player.pause()
            self._set_play_icon(False)
        else:
            if self._finished:
                self.player.setCurrentTime_(0.0)
                self.slider.setDoubleValue_(0.0)
                self._finished = False
            self.player.play()
            self._set_play_icon(True)
            if self.timer is None:
                self.timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.15, self, "tick:", None, True)

    def seek_(self, sender):
        if self.player is None:
            return
        self.player.setCurrentTime_(float(sender.doubleValue()))
        self._finished = False
        self._refresh_time()

    def togglePin_(self, sender):
        self.pinned = not self.pinned
        self._sync_pin_icon()
        if self.on_pin_changed:
            try:
                self.on_pin_changed(self.pinned)
            except Exception:
                pass

    def closeHud_(self, sender):
        self.stop()

    def tick_(self, timer):
        if self.player is not None:
            self._refresh_time()

    def audioPlayerDidFinishPlaying_successfully_(self, player, flag):
        self._finished = True
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        try:
            player.setCurrentTime_(0.0)
        except Exception:
            pass
        self.slider.setDoubleValue_(0.0)
        self.time_label.setStringValue_(
            f"0:00 / {_fmt(float(player.duration() or 0))}")
        self._set_play_icon(False)

        if not self.pinned:
            self.stop()
        if self._on_done:
            try:
                self._on_done()
            except Exception:
                pass
