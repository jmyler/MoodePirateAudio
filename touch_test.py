#!/usr/bin/env python3
"""Touch bench test: prints raw (native) and mapped (screen) coordinates.

Stop the service first (sudo systemctl stop pirate-audio), then touch each
corner. Mapped coordinates should be about (0,0) top-left and (319,239)
bottom-right. If they're mirrored or swapped, set TOUCH_MAP in display.py
(e.g. TOUCH_MAP = lambda x, y: (y, x)).
"""
import time

from display import TOUCH_MAP, TOUCH_PINS
from cst816 import CST816
from st7789_lite import find_gpiochip

import lgpio

chip = lgpio.gpiochip_open(find_gpiochip())
t = CST816(rst=TOUCH_PINS.get("rst"), gpio_chip=chip, mapping=TOUCH_MAP)
print(f"touch controller found (chip id 0x{t.chip_id:02x}). Touch the screen, Ctrl+C to quit.")
last = None
while True:
    r = t.raw()
    if r and r[0]:
        cur = (r[1], r[2])
        if cur != last:
            print(f"raw x={r[1]:3d} y={r[2]:3d}   ->  screen {t.map(*cur)}")
            last = cur
    time.sleep(0.03)
