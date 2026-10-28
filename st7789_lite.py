"""Minimal ST7789 driver (spidev + lgpio) for the Pirate Audio 240x240 LCD.

Drop-in for the parts of Pimoroni's st7789 API that display.py uses, without
gpiodevice. Init sequence and pixel packing match Pimoroni's library.
"""
import time

import lgpio
import numpy as np
import spidev


def find_gpiochip():
    """Number of the SoC's main GPIO controller (label 'pinctrl-...').

    Newer kernels don't guarantee it is gpiochip0; on some it's the small
    firmware expander instead, and requesting GPIO 9 there fails with EINVAL.
    """
    import glob
    for path in sorted(glob.glob("/dev/gpiochip*")):
        n = int(path.replace("/dev/gpiochip", ""))
        try:
            h = lgpio.gpiochip_open(n)
        except lgpio.error:
            continue
        try:
            _, lines, name, label = lgpio.gpio_get_chip_info(h)
        finally:
            lgpio.gpiochip_close(h)
        if label.startswith("pinctrl") and lines >= 28:
            return n
    return 0


class ST7789:
    def __init__(self, port=0, cs=1, dc=9, backlight=13, rotation=90,
                 spi_speed_hz=80_000_000, width=240, height=240, invert=True, gpiochip=None,
                 x_off=0, y_off=0):
        self.x_off, self.y_off = x_off, y_off
        if gpiochip is None:
            gpiochip = find_gpiochip()
        self.w, self.h, self.rot = width, height, rotation
        self.spi = spidev.SpiDev(port, cs)
        self.spi.mode = 0
        self.spi.max_speed_hz = spi_speed_hz
        self.chip_num = gpiochip
        self.chip = lgpio.gpiochip_open(gpiochip)
        self.dc = dc
        lgpio.gpio_claim_output(self.chip, dc, 0)
        self.bl = backlight
        if backlight is not None:
            lgpio.gpio_claim_output(self.chip, backlight, 0)
        self._init(invert)
        self.set_backlight(1)

    # -- low level
    def _send(self, data, is_data):
        lgpio.gpio_write(self.chip, self.dc, 1 if is_data else 0)
        if isinstance(data, int):
            data = [data]
        self.spi.writebytes2(bytes(data) if not isinstance(data, (bytes, bytearray)) else data)

    def cmd(self, c, *args):
        self._send(c, False)
        if args:
            self._send(list(args), True)

    def _init(self, invert):
        self.cmd(0x01); time.sleep(0.15)                      # SWRESET
        self.cmd(0x36, 0x70)                                   # MADCTL
        self.cmd(0xB2, 0x0C, 0x0C, 0x00, 0x33, 0x33)           # FRMCTR2
        self.cmd(0x3A, 0x05)                                   # COLMOD 16-bit
        self.cmd(0xB7, 0x14)                                   # GCTRL
        self.cmd(0xBB, 0x37)                                   # VCOMS
        self.cmd(0xC0, 0x2C)                                   # LCMCTRL
        self.cmd(0xC2, 0x01)                                   # VDVVRHEN
        self.cmd(0xC3, 0x12)                                   # VRHS
        self.cmd(0xC4, 0x20)                                   # VDVS
        self.cmd(0xD6, 0xA1)
        self.cmd(0xC6, 0x0F)                                   # FRCTRL2
        self.cmd(0xD0, 0xA4, 0xA1)                             # PWCTRL1
        self.cmd(0xE0, 0xD0, 0x04, 0x0D, 0x11, 0x13, 0x2B, 0x3F, 0x54, 0x4C, 0x18, 0x0D, 0x0B, 0x1F, 0x23)
        self.cmd(0xE1, 0xD0, 0x04, 0x0C, 0x11, 0x13, 0x2C, 0x3F, 0x44, 0x51, 0x2F, 0x1F, 0x1F, 0x20, 0x23)
        self.cmd(0x21 if invert else 0x20)                     # INVON/OFF
        self.cmd(0x11); time.sleep(0.12)                       # SLPOUT
        self.cmd(0x29); time.sleep(0.1)                        # DISPON

    # -- public API
    def set_backlight(self, value):
        if self.bl is not None:
            lgpio.gpio_write(self.chip, self.bl, 1 if value else 0)

    def display(self, image):
        # Pirate Audio's 240x240 panel sits at RAM origin with MADCTL 0x70 (no offset)
        x0, y0 = self.x_off, self.y_off
        x1, y1 = x0 + self.w - 1, y0 + self.h - 1
        self.cmd(0x2A, x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF)  # CASET
        self.cmd(0x2B, y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF)  # RASET
        self.cmd(0x2C)                                         # RAMWR
        px = np.rot90(np.asarray(image.convert("RGB")), self.rot // 90).astype("uint16")
        rgb565 = ((px[..., 0] & 0xF8) << 8) | ((px[..., 1] & 0xFC) << 3) | (px[..., 2] >> 3)
        self._send(rgb565.astype(">u2").tobytes(), True)
