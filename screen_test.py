#!/usr/bin/env python3
"""Bench test for the screen: colour bars, a border and an arrow pointing UP.

  venv/bin/python screen_test.py            # uses PANEL / ROTATION from display.py
  venv/bin/python screen_test.py 270        # try the other rotation
If the border is complete and the arrow points up, that rotation is right.
"""
import sys
import time

from PIL import Image, ImageDraw, ImageFont

import display as D
from st7789_lite import ST7789

rot = int(sys.argv[1]) if len(sys.argv) > 1 else D.ROTATION
disp = ST7789(panel=D.PANEL, rotation=rot, **D.SCREEN_PINS)
W, H = disp.width, disp.height
print(f"panel {D.PANEL}, rotation {rot}, {W}x{H}, gpiochip{disp.chip_num}")

img = Image.new("RGB", (W, H), "black")
d = ImageDraw.Draw(img)
bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
bw = W // len(bars)
for i, c in enumerate(bars):
    d.rectangle((i * bw, H - 50, (i + 1) * bw - 1, H - 1), fill=c)
d.rectangle((0, 0, W - 1, H - 1), outline=(255, 160, 0), width=4)
f = ImageFont.truetype(D.FONT_B, 22)
d.text((12, 10), f"TOP  rot {rot}", font=f, fill="white")
d.polygon([(W / 2, 50), (W / 2 - 25, 95), (W / 2 + 25, 95)], fill="yellow")
d.rectangle((W / 2 - 8, 95, W / 2 + 8, 140), fill="yellow")
d.text((12, H - 80), "R   G   B   W  bars below", font=ImageFont.truetype(D.FONT, 14), fill="white")
disp.display(img)
print("Showing test pattern for 30 s (Ctrl-C to stop)")
time.sleep(30)
