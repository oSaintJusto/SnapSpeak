#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 SnapSpeak 的 App 图标(1024 PNG + .iconset 全套尺寸)。"""
import os
import sys

from PIL import Image, ImageDraw

OUT = sys.argv[1] if len(sys.argv) > 1 else "build/icon"
os.makedirs(OUT, exist_ok=True)

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角矩形背景 + 渐变
radius = 228
grad = Image.new("RGB", (S, S))
gd = ImageDraw.Draw(grad)
top, bot = (58, 160, 255), (0, 92, 220)
for y in range(S):
    t = y / (S - 1)
    gd.line([(0, y), (S, y)],
            fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius, fill=255)
img.paste(grad, (0, 0), mask)
d = ImageDraw.Draw(img)

# 声波柱
heights = [0.30, 0.54, 0.40, 0.74, 0.46, 0.28]
width = 82
gap = 46
total = len(heights) * width + (len(heights) - 1) * gap
x = (S - total) / 2
cy = S / 2
for h in heights:
    bh = h * 560
    d.rounded_rectangle(
        [x, cy - bh / 2, x + width, cy + bh / 2],
        radius=width / 2, fill=(255, 255, 255, 255))
    x += width + gap

master = os.path.join(OUT, "icon_1024.png")
img.save(master)
print("wrote", master)

iconset = os.path.join(OUT, "SnapSpeak.iconset")
os.makedirs(iconset, exist_ok=True)
specs = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
         (256, 1), (256, 2), (512, 1), (512, 2)]
for size, scale in specs:
    px = size * scale
    name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
    img.resize((px, px), Image.LANCZOS).save(os.path.join(iconset, name))
print("wrote iconset:", iconset)
