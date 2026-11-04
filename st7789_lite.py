"""Minimal ST7789 driver (spidev + lgpio).

Panels:
  "pirate"     - Pimoroni Pirate Audio 1.3" 240x240 (Pimoroni init, MADCTL 0x70)
  "waveshare2" - Waveshare 2" 240x320 IPS module (Waveshare init, has a RST pin);
                 used in landscape as 320x240

Images are drawn in the *logical* orientation (e.g. 320x240) and rotated in
numpy to the panel's native 240-wide RAM layout, so the layout code never
cares which way the panel is mounted: flip ROTATION between 90 and 270.
"""
import time

import lgpio
import numpy as np
import spidev


def find_gpiochip():
    """Number of the SoC's main GPIO controller (label 'pinctrl-...')."""
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


PANELS = {
    # native RAM window (cols x rows), MADCTL, init sequence
    "pirate": dict(native=(240, 240), madctl=0x70, init=[
        (0xB2, 0x0C, 0x0C, 0x00, 0x33, 0x33), (0x3A, 0x05), (0xB7, 0x14), (0xBB, 0x37),
        (0xC0, 0x2C), (0xC2, 0x01), (0xC3, 0x12), (0xC4, 0x20), (0xD6, 0xA1), (0xC6, 0x0F),
        (0xD0, 0xA4, 0xA1),
        (0xE0, 0xD0, 0x04, 0x0D, 0x11, 0x13, 0x2B, 0x3F, 0x54, 0x4C, 0x18, 0x0D, 0x0B, 0x1F, 0x23),
        (0xE1, 0xD0, 0x04, 0x0C, 0x11, 0x13, 0x2C, 0x3F, 0x44, 0x51, 0x2F, 0x1F, 0x1F, 0x20, 0x23)]),
    "waveshare2": dict(native=(240, 320), madctl=0x00, init=[
        (0x3A, 0x05), (0xB2, 0x0C, 0x0C, 0x00, 0x33, 0x33), (0xB7, 0x35), (0xBB, 0x1F),
        (0xC0, 0x2C), (0xC2, 0x01), (0xC3, 0x12), (0xC4, 0x20), (0xC6, 0x0F), (0xD0, 0xA4, 0xA1),
        (0xE0, 0xD0, 0x08, 0x11, 0x08, 0x0C, 0x15, 0x39, 0x33, 0x50, 0x36, 0x13, 0x14, 0x29, 0x2D),
        (0xE1, 0xD0, 0x08, 0x10, 0x08, 0x06, 0x06, 0x39, 0x44, 0x51, 0x0B, 0x16, 0x14, 0x2F, 0x31)]),
}


class ST7789:
    def __init__(self, panel="pirate", port=0, cs=1, dc=9, backlight=13, rst=None, rotation=90,
                 spi_speed_hz=40_000_000, invert=True, gpiochip=None, x_off=0, y_off=0, **_ignored):
        self.p = PANELS[panel]
        self.nw, self.nh = self.p["native"]
        self.rot = rotation
        self.x_off, self.y_off = x_off, y_off
        # logical size as seen by the layout code
        self.width, self.height = (self.nw, self.nh) if rotation in (0, 180) else (self.nh, self.nw)
        if gpiochip is None:
            gpiochip = find_gpiochip()
        self.spi = spidev.SpiDev(port, cs)
        self.spi.mode = 0
        self.spi.max_speed_hz = spi_speed_hz
        self.chip_num = gpiochip
        self.chip = lgpio.gpiochip_open(gpiochip)
        self.dc, self.bl, self.rst = dc, backlight, rst
        lgpio.gpio_claim_output(self.chip, dc, 0)
        if backlight is not None:
            lgpio.gpio_claim_output(self.chip, backlight, 0)
        if rst is not None:
            lgpio.gpio_claim_output(self.chip, rst, 1)
        self._invert = invert
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
        if self.rst is not None:                                # hardware reset pulse
            lgpio.gpio_write(self.chip, self.rst, 1); time.sleep(0.01)
            lgpio.gpio_write(self.chip, self.rst, 0); time.sleep(0.01)
            lgpio.gpio_write(self.chip, self.rst, 1); time.sleep(0.12)
        self.cmd(0x01); time.sleep(0.15)                        # SWRESET
        self.cmd(0x36, self.p["madctl"])                        # MADCTL
        for c in self.p["init"]:
            self.cmd(*c)
        self.cmd(0x21 if invert else 0x20)                      # INVON/OFF
        self.cmd(0x11); time.sleep(0.12)                        # SLPOUT
        self.cmd(0x29); time.sleep(0.1)                         # DISPON

    def refresh_config(self):
        """Re-send the panel setup without a reset (no flicker). If a power dip reset the
        controller, this brings it back; on a healthy panel it changes nothing."""
        self.cmd(0x11)                                          # SLPOUT (no-op if awake)
        self.cmd(0x36, self.p["madctl"])
        for c in self.p["init"]:
            self.cmd(*c)
        self.cmd(0x21 if self._invert else 0x20)
        self.cmd(0x29)                                          # DISPON

    # -- public API
    def set_backlight(self, value):
        if self.bl is not None:
            lgpio.gpio_write(self.chip, self.bl, 1 if value else 0)

    def display(self, image):
        x0, y0 = self.x_off, self.y_off
        x1, y1 = x0 + self.nw - 1, y0 + self.nh - 1
        self.cmd(0x2A, x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF)  # CASET
        self.cmd(0x2B, y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF)  # RASET
        self.cmd(0x2C)                                          # RAMWR
        px = np.rot90(np.asarray(image.convert("RGB")), self.rot // 90)
        if px.shape[:2] != (self.nh, self.nw):
            raise ValueError(f"image is {image.size}, panel expects {self.width}x{self.height}")
        px = px.astype("uint16")
        rgb565 = ((px[..., 0] & 0xF8) << 8) | ((px[..., 1] & 0xFC) << 3) | (px[..., 2] >> 3)
        self._send(rgb565.astype(">u2").tobytes(), True)
