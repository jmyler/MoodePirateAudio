#!/usr/bin/env python3
"""Bench check for the INA226: prints voltage, shunt mV, current and power.

Run:  ~/pirate-audio/venv/bin/python ~/pirate-audio/ina_test.py
"""
import time
from ina226 import INA226

ina = INA226(bus=1, addr=0x40, shunt_ohms=0.002)
print("INA226 found. Ctrl-C to stop.\n   V        shunt mV     A        W")
peak = 0.0
while True:
    v, mv = ina.voltage(), ina.shunt_mv()
    i = ina.current()
    peak = max(peak, i)
    print(f"{v:7.3f}   {mv:+9.4f}   {i:+7.3f}   {v*i:+7.1f}   (peak {peak:.2f} A)")
    time.sleep(0.5)
