"""Minimal INA226 driver + LiFePO4 coulomb counter.

Current is computed from the raw shunt voltage (2.5 uV/LSB) and the shunt
resistance, so the chip's calibration register isn't needed. Trim with
CURRENT_GAIN against a clamp meter if the reading is a few % off.
"""
import json
import os
import time

from smbus2 import SMBus

REG_CONFIG, REG_SHUNT, REG_BUS = 0x00, 0x01, 0x02
REG_MFG_ID, REG_DIE_ID = 0xFE, 0xFF

# AVG=64, VBUSCT=1.1 ms, VSHCT=1.1 ms, continuous shunt+bus  (~140 ms per reading)
CONFIG_WORD = (0b011 << 9) | (0b100 << 6) | (0b100 << 3) | 0b111


class INA226:
    def __init__(self, bus=1, addr=0x40, shunt_ohms=0.002, current_gain=1.0, sign=1):
        self.bus = SMBus(bus)
        self.addr = addr
        self.shunt = shunt_ohms
        self.gain = current_gain
        self.sign = sign  # flip to -1 if discharge reads negative
        mfg, die = self._read(REG_MFG_ID), self._read(REG_DIE_ID)
        if mfg != 0x5449 or die != 0x2260:
            raise RuntimeError(f"Not an INA226 at 0x{addr:02x} (mfg 0x{mfg:04x}, die 0x{die:04x})")
        self._write(REG_CONFIG, CONFIG_WORD)

    def _read(self, reg):
        hi, lo = self.bus.read_i2c_block_data(self.addr, reg, 2)
        return (hi << 8) | lo

    def _write(self, reg, val):
        self.bus.write_i2c_block_data(self.addr, reg, [(val >> 8) & 0xFF, val & 0xFF])

    def shunt_mv(self):
        raw = self._read(REG_SHUNT)
        if raw & 0x8000:
            raw -= 1 << 16
        return raw * 0.0025  # 2.5 uV/LSB -> mV

    def voltage(self):
        return self._read(REG_BUS) * 0.00125  # 1.25 mV/LSB

    def current(self):
        """Amps; positive = discharging (after `sign`)."""
        return self.sign * self.gain * (self.shunt_mv() / 1000.0) / self.shunt


# Approximate resting voltage per cell -> SOC, only used for a first guess.
LFP_CURVE = [(2.80, 0), (3.00, 5), (3.10, 8), (3.20, 12), (3.25, 20), (3.27, 30),
             (3.30, 50), (3.32, 70), (3.35, 90), (3.40, 99), (3.45, 100)]


def soc_from_voltage(cell_v):
    if cell_v <= LFP_CURVE[0][0]:
        return 0.0
    for (v0, s0), (v1, s1) in zip(LFP_CURVE, LFP_CURVE[1:]):
        if cell_v <= v1:
            return (s0 + (s1 - s0) * (cell_v - v0) / (v1 - v0)) / 100.0
    return 1.0


class BatteryGauge:
    """Coulomb counter with full-charge sync and a low-voltage floor."""

    def __init__(self, capacity_ah, cells, state_file,
                 full_cell_v=3.55, full_tail_a=0.5, full_hold_s=30,
                 empty_cell_v=3.00, empty_hold_s=30):
        self.cap = capacity_ah
        self.cells = cells
        self.state_file = state_file
        self.full_v, self.full_tail, self.full_hold = full_cell_v * cells, full_tail_a, full_hold_s
        self.empty_v, self.empty_hold = empty_cell_v * cells, empty_hold_s
        self.remaining = None
        self.synced = False      # True once we've seen a real full charge
        self.avg_draw = 0.0      # EMA of discharge current (A)
        self.v = self.i = 0.0
        self._last = None
        self._full_since = self._empty_since = None
        self._last_save = 0
        self._load()

    def _load(self):
        try:
            with open(self.state_file) as f:
                st = json.load(f)
            self.remaining = float(st["remaining_ah"])
            self.synced = bool(st.get("synced", False))
        except (OSError, ValueError, KeyError):
            pass

    def save(self):
        if self.remaining is None:
            return
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"remaining_ah": round(self.remaining, 4), "synced": self.synced,
                       "saved": time.time()}, f)
        os.replace(tmp, self.state_file)
        self._last_save = time.monotonic()

    def update(self, v, i):
        now = time.monotonic()
        self.v, self.i = v, i
        if self.remaining is None:  # first boot: rough guess from voltage
            self.remaining = soc_from_voltage(v / self.cells) * self.cap
        if self._last is not None:
            dt = now - self._last
            self.remaining -= i * dt / 3600.0
            if i > 0:
                a = min(1.0, dt / 120.0)  # ~2 min time constant
                self.avg_draw += a * (i - self.avg_draw)
        self._last = now

        # full-charge sync: high voltage + small charge current, held
        if v >= self.full_v and abs(i) < self.full_tail:
            self._full_since = self._full_since or now
            if now - self._full_since >= self.full_hold:
                self.remaining, self.synced = self.cap, True
        else:
            self._full_since = None
        # low-voltage floor: sagging below empty under load means we're nearly out
        if v <= self.empty_v:
            self._empty_since = self._empty_since or now
            if now - self._empty_since >= self.empty_hold:
                self.remaining = min(self.remaining, 0.05 * self.cap)
        else:
            self._empty_since = None

        self.remaining = max(0.0, min(self.cap, self.remaining))
        if now - self._last_save > 60:
            self.save()

    @property
    def soc(self):
        return 0.0 if self.remaining is None else self.remaining / self.cap

    @property
    def hours_left(self):
        if self.remaining is None or self.avg_draw < 0.05 or self.i < 0:
            return None
        return self.remaining / self.avg_draw
