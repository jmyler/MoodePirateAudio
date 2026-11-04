"""CST816D capacitive touch controller (Waveshare 2" touch LCD module), polled over I2C.

The panel reports in its native portrait orientation (240 x 320). read() maps
that to the logical landscape screen the UI draws on. If taps land in the wrong
place, run touch_test.py, note the raw numbers in each corner, and set
TOUCH_MAP in display.py.
"""
import logging
import time

from smbus2 import SMBus

log = logging.getLogger("pirate.touch")

ADDR = 0x15
REG_DATA = 0x01          # gesture, fingers, xh, xl, yh, yl
REG_CHIP_ID = 0xA7
REG_DIS_AUTOSLEEP = 0xFE


class CST816:
    def __init__(self, bus=1, addr=ADDR, rst=None, gpio_chip=None, native=(240, 320), mapping="rot90"):
        self.addr = addr
        self.nw, self.nh = native
        self.mapping = mapping
        if rst is not None and gpio_chip is not None:          # hardware reset wakes it up
            import lgpio
            lgpio.gpio_claim_output(gpio_chip, rst, 1)
            lgpio.gpio_write(gpio_chip, rst, 0); time.sleep(0.01)
            lgpio.gpio_write(gpio_chip, rst, 1); time.sleep(0.06)
        self.bus = SMBus(bus)
        self.chip_id = self.bus.read_byte_data(addr, REG_CHIP_ID)   # raises OSError if absent
        try:
            self.bus.write_byte_data(addr, REG_DIS_AUTOSLEEP, 0x01)  # stay awake: no missed first taps
        except OSError:
            pass
        log.info("touch controller 0x%02x (chip id 0x%02x), mapping %s", addr, self.chip_id, mapping)

    def raw(self):
        """(fingers, x, y) in native coords, or None if the read failed."""
        try:
            g, n, xh, xl, yh, yl = self.bus.read_i2c_block_data(self.addr, REG_DATA, 6)
        except OSError:
            return None
        return n & 0x0F, ((xh & 0x0F) << 8) | xl, ((yh & 0x0F) << 8) | yl

    def read(self):
        r = self.raw()
        if not r or r[0] == 0:
            return None
        _, x, y = r
        return self.map(x, y)

    def map(self, x, y):
        """Native portrait -> logical landscape. Matches st7789_lite's np.rot90 rotation."""
        m = self.mapping
        if m == "rot90":          # ROTATION = 90
            return (self.nh - 1 - y, x)
        if m == "rot270":         # ROTATION = 270
            return (y, self.nw - 1 - x)
        if callable(m):
            return m(x, y)
        return (x, y)
