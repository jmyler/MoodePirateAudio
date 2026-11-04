#!/usr/bin/env python3
"""Show the cached boot splash as early as possible in boot (pirate-splash.service).
display.py saves splash.png each time it starts and takes over the screen when ready."""
import os
import sys

from PIL import Image

HOME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HOME)
from display import PANEL, ROTATION, SCREEN_PINS, SPLASH_CACHE  # noqa: E402  (config only)
from st7789_lite import ST7789  # noqa: E402

if os.path.exists(SPLASH_CACHE):
    ST7789(panel=PANEL, rotation=ROTATION, **SCREEN_PINS).display(Image.open(SPLASH_CACHE))
