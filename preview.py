#!/usr/bin/env python3
"""Render every screen with sample data to preview_sheet.png - no Pi needed.

    python3 preview.py            # contact sheet of all screens
"""
import random

from PIL import Image, ImageDraw, ImageFilter

import ui


def sample_cover():
    rnd = random.Random(3)
    n = 600
    im = Image.new("RGB", (n, n))
    d = ImageDraw.Draw(im)
    c1, c2, c3 = (255, 120, 40), (120, 20, 120), (255, 210, 70)
    for y in range(n):
        t = y / n
        d.line([(0, y), (n, y)], fill=tuple(int(c1[k] * (1 - t) + c2[k] * t) for k in range(3)))
    for _ in range(7):
        r = rnd.randint(60, 220)
        x, y = rnd.randint(0, n), rnd.randint(0, n)
        d.ellipse((x - r, y - r, x + r, y + r), fill=c3)
    im = im.filter(ImageFilter.GaussianBlur(28))
    ImageDraw.Draw(im).ellipse((150, 150, 450, 450), outline=(255, 255, 255), width=10)
    return im.resize((ui.ART_PX, ui.ART_PX))


ART = sample_cover()
BATT = dict(v=26.2, i=1.84, soc=0.72, hrs=2.4, synced=True, peak=4.6, avg=1.7, charging=False, full_hrs=None)
BASE = dict(source="Bluetooth", device="JPhone", playing=True, title="Industry Baby",
            artist="Lil Nas X & Jack Harlow", album="Montero", art=ART, batt=BATT, view="now",
            bt_name="JS Speaker", airplay_name="JS Speaker", bt_visible=False,
            inputs=("AirPlay", "Bluetooth"), t=0.0, touch=True,
            sys=dict(temp=54.2, uv_now=False, uv_seen=False, ssid="Home Wi-Fi", rssi=-58,
                     ip="192.168.1.42", host="moode.local", uptime=7980))
import math as _m
PHONES = [dict(mac="A", name="JPhone", connected=True, blocked=False, blocked_until=0),
          dict(mac="B", name="Maddux's iPad", connected=False, blocked=False, blocked_until=0),
          dict(mac="C", name="Coach Mike's Galaxy", connected=False, blocked=True, blocked_until=240),
          dict(mac="D", name="JP's iPhone", connected=False, blocked=False, blocked_until=0),
          dict(mac="E", name="Pixel 8", connected=False, blocked=False, blocked_until=0)]
BASE["phones"], BASE["now_wall"] = PHONES, 0
BASE["join"] = dict(ssid="JS Speaker", hotspot=True, psk="walkup2026", ip="10.42.0.1", host="moode.local")
BASE["stats"] = dict(uptime=9000, used_ah=3.4, used_pct=57, avg=1.36, peak=4.6,
                     hist=[(9000 - k * 30, 1.2 + 0.6 * _m.sin(k / 9) + (1.8 if k % 37 == 0 else 0))
                           for k in range(300)])
IDLE = dict(BASE, source=None, device="", playing=False, title="", artist="", album="", art=None,
            bt_visible=True)


def menu(sel=0, confirming=False):
    from display import menu_items          # same builder the app uses
    items = menu_items(dict(BASE))
    if confirming:
        items[0]["confirming"] = True
    return dict(title="JPhone connected", items=items, sel=sel)


def phone_sheet(dev, sel=0, confirming=False):
    from display import phone_items
    items = phone_items(dev)
    if confirming:
        items[sel]["confirming"] = True
    return dict(title=dev["name"], subtitle="Connected" if dev["connected"] else "Paired", items=items, sel=sel)


def screens():
    pop = lambda **p: dict(BASE, popup=p)
    return [
        ("Everyday", [
            ("Idle (Bluetooth first)", IDLE),
            ("Now playing", BASE),
            ("Paused", dict(BASE, playing=False)),
            ("Volume (turn knob)", dict(BASE, volume=(62, 85))),
        ]),
        ("Menu & help (hold knob / tap top bar)", [
            ("Menu", dict(BASE, menu=menu(0))),
            ("Disconnect: confirm", dict(BASE, menu=menu(0, True))),
            ("Controls (touch fitted)", dict(BASE, help=True)),
            ("Controls (knob only)", dict(BASE, help=True, touch=False)),
        ]),
        ("Popups", [
            ("Phone connects", pop(icon="bt", color=ui.BLUE, small="Connected", big="JPhone")),
            ("After a kick", pop(icon="bt_off", color=(255, 255, 255), icon_alpha=40, small="Disconnected",
                                 big="JPhone", note="Can't reconnect for 5 min")),
            ("Low battery", dict(BASE, batt=dict(BATT, soc=0.12, hrs=0.4),
                                 popup=dict(icon="battery", color=ui.RED, small="Battery low",
                                            big="12% · ~24 m"))),
            ("Pair another phone", dict(BASE, pairing_left=(102, 0.85))),
        ]),
        ("New: phones, join, session stats", [
            ("Phones (knob highlight)", dict(BASE, view="phones", phone_sel=1, phone_focus=True)),
            ("Phone actions", dict(BASE, view="phones", menu=phone_sheet(PHONES[0]))),
            ("Forget: confirm", dict(BASE, view="phones", menu=phone_sheet(PHONES[1], 0, True))),
            ("Menu: Controls pill selected", dict(BASE, menu=menu(6))),
        ]),
        ("", [
            ("Join (hotspot)", dict(BASE, view="join")),
            ("Session stats", dict(BASE, view="stats")),
            ("Phones (none paired)", dict(IDLE, view="phones", phones=[])),
            ("Stats just started", dict(BASE, view="stats", stats=dict(uptime=40, used_ah=0.01, used_pct=0.2,
                                                                       avg=1.1, peak=1.4, hist=[]))),
        ]),
        ("Battery & system (swipe)", [
            ("Battery", dict(BASE, view="battery")),
            ("Charging (idle)", dict(IDLE, view="battery",
                                      batt=dict(BATT, soc=0.58, i=-2.1, v=27.4, charging=True, full_hrs=1.6))),
            ("System", dict(BASE, view="system", bt_visible=False)),
            ("Boot splash", dict(IDLE, splash_kind="boot", splash_sub="Starting up…", t=0.4)),
        ]),
        ("Edge cases", [
            ("No art yet / no metadata", dict(BASE, art=None, title="", artist="", album="")),
            ("Long title", dict(BASE, title="Don't Stop Me Now (Remastered 2011 Version)",
                                artist="Queen", art=None, source="AirPlay", device="")),
            ("Estimate + system warning", dict(BASE, view="system",
                                               sys=dict(BASE["sys"], temp=78.0, uv_seen=True))),
            ("Not synced battery", dict(IDLE, view="battery", batt=dict(BATT, synced=False, hrs=None))),
        ]),
    ]


if __name__ == "__main__":
    rows = screens()
    SC, PAD, HEAD, LAB, COLS = 2, 20, 60, 34, 4
    sheet = Image.new("RGB", (PAD + COLS * (ui.W * SC + PAD), HEAD + len(rows) * (LAB + ui.H * SC + PAD)),
                      (236, 236, 240))
    sd = ImageDraw.Draw(sheet)
    f = lambda w, n: ui.F(w, n / ui.SS)
    sd.text((PAD, 14), "MoodePirateAudio - every screen", font=f("Bold", 28), fill=(20, 20, 24))
    for r, (rowname, items) in enumerate(rows):
        y = HEAD + r * (LAB + ui.H * SC + PAD)
        sd.text((PAD, y + 2), rowname, font=f("SemiBold", 20), fill=(30, 30, 36))
        for c, (name, m) in enumerate(items):
            x = PAD + c * (ui.W * SC + PAD)
            sd.text((x + ui.W * SC, y + 8), name, font=f("Medium", 14), fill=(110, 110, 120), anchor="ra")
            img = ui.render(m)
            assert img.size == (ui.W, ui.H)
            sheet.paste(img.resize((ui.W * SC, ui.H * SC), Image.Resampling.NEAREST), (x, y + LAB))
    sheet.save("preview_sheet.png")
    print("wrote preview_sheet.png")
