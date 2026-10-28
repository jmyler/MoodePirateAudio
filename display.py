#!/usr/bin/env python3
"""Pirate Audio (ST7789) now-playing + battery display for moOde.

- Sources: follows moOde's own active-renderer flags (see sources.py):
  AirPlay, Bluetooth, Spotify, other renderers, then MPD library/radio.
- Battery: INA226 coulomb counter in a thread; status bar + idle screen.
- Buttons: A = screen on/off, B = now playing <-> battery detail, X/Y = volume.
"""
import logging
import os
import signal
import subprocess
import sys
import threading
import time

from PIL import Image, ImageDraw, ImageFont

# ------------------------------------------------------------------ config
HOME = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(HOME, "jm_logo.jpeg")          # optional idle image
STATE_FILE = os.path.join(HOME, "battery_state.json")

BATTERY_ENABLED = True
CAPACITY_AH = 20.0        # <-- set to your Battery Hookup pack's capacity
CELLS = 8                 # 25.6 V LiFePO4 = 8S
SHUNT_OHMS = 0.002        # R002
CURRENT_GAIN = 1.0        # trim against a clamp meter
CURRENT_SIGN = 1          # -1 if discharge shows negative in ina_test.py
LOW_PCT = 20

BUTTONS = {"A": 5, "B": 6, "X": 16, "Y": 24}     # older Pimoroni docs list Y as 20
VOL_UP = ["/var/www/util/vol.sh", "-up", "2"]     # verify on your moOde version
VOL_DN = ["/var/www/util/vol.sh", "-dn", "2"]

LONG_PRESS_S = 2.0        # hold this long for the long-press action
PAIR_S = 120              # pairing-mode window
PAIR_HINT = "Hold B to pair a new phone"
HIDE_BY_DEFAULT = True    # not discoverable unless pairing mode is on
TOAST_S = 4

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W = H = 240
ART = 160

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pirate")


# ------------------------------------------------------------------ shared state
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.dirty = threading.Event()
        self.source = None            # "AirPlay" | "Bluetooth" | "Library" | ... | None
        self.device = ""              # e.g. connected Bluetooth phone name
        self.inputs = ()              # enabled renderers, for the idle screen
        self.playing = False
        self.title = self.artist = self.album = ""
        self.art = None
        self.batt = None              # dict from battery thread
        self.view = "now"             # "now" | "battery"
        self.screen_on = True
        self.toast = None             # (line1, line2, rgb, expires_monotonic)
        self.pairing_until = 0.0
        self.bt_name = ""             # what phones see in their Bluetooth list

    def set(self, **kw):
        with self.lock:
            changed = False
            for k, v in kw.items():
                if getattr(self, k) is not v and getattr(self, k) != v:
                    setattr(self, k, v)
                    changed = True
        if changed:
            self.dirty.set()


S = State()


def toast(line1, line2="", color=(70, 140, 255), secs=TOAST_S):
    S.set(toast=(line1, line2, color, time.monotonic() + secs))


# ------------------------------------------------------------------ connection watcher
class ConnWatcher(threading.Thread):
    """Announce Bluetooth connects/disconnects on screen."""
    daemon = True

    def __init__(self):
        super().__init__(name="conn")
        self.known = None
        self.suppress = set()   # names we just kicked (their own toast already shown)

    def run(self):
        import btctl
        while True:
            try:
                now = dict(btctl.connected())
                if self.known is not None:
                    for mac, name in now.items():
                        if mac not in self.known:
                            log.info("connected: %s (%s)", name, mac)
                            toast("Connected", name)
                    for mac, name in self.known.items():
                        if mac not in now and name not in self.suppress:
                            log.info("disconnected: %s (%s)", name, mac)
                            toast("Disconnected", name, (150, 150, 150))
                    self.suppress.clear()
                self.known = now
            except Exception as e:
                log.debug("conn watch: %s", e)
            time.sleep(2)


CONN = ConnWatcher()


# ------------------------------------------------------------------ battery
class BatteryThread(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__(name="battery")
        from ina226 import INA226, BatteryGauge
        self.ina = INA226(shunt_ohms=SHUNT_OHMS, current_gain=CURRENT_GAIN, sign=CURRENT_SIGN)
        self.g = BatteryGauge(CAPACITY_AH, CELLS, STATE_FILE)
        self.peak = 0.0

    def run(self):
        last_push = 0
        while True:
            try:
                v, i = self.ina.voltage(), self.ina.current()
                self.g.update(v, i)
                self.peak = max(self.peak, i)
                now = time.monotonic()
                if now - last_push >= 2:
                    last_push = now
                    S.set(batt=dict(v=v, i=i, soc=self.g.soc, hrs=self.g.hours_left,
                                    synced=self.g.synced, peak=self.peak, avg=self.g.avg_draw))
            except OSError as e:
                log.warning("INA226 read: %s", e)
                time.sleep(2)
            time.sleep(0.5)


# ------------------------------------------------------------------ rendering
def font(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, size)


F_SM, F_MD, F_LG, F_XL = font(14), font(18), font(20, True), font(56, True)
LOGO_IMG = None
if os.path.exists(LOGO):
    try:
        LOGO_IMG = Image.open(LOGO).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
    except Exception:
        pass


SOURCE_ICON = {}   # room for per-source glyphs later


def fit(draw, text, f, width):
    if draw.textlength(text, font=f) <= width:
        return text
    while text and draw.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text + "…"


def batt_color(b):
    return (230, 60, 50) if b["soc"] * 100 <= LOW_PCT else (80, 200, 110)


def fmt_hrs(h):
    if h is None:
        return ""
    return f"{int(h)}h{int((h % 1) * 60):02d}" if h < 100 else ">99h"


def draw_status(d, b):
    if not b:
        return
    pct = round(b["soc"] * 100)
    col = batt_color(b)
    # icon
    d.rectangle((6, 6, 38, 20), outline=(200, 200, 200), width=2)
    d.rectangle((39, 10, 42, 16), fill=(200, 200, 200))
    d.rectangle((9, 9, 9 + int(26 * b["soc"]), 17), fill=col)
    txt = f"{'' if b['synced'] else '~'}{pct}%"
    if b["i"] < -0.1:
        txt += " ⚡"
    d.text((48, 4), txt, font=F_MD, fill=(235, 235, 235))
    right = fmt_hrs(b["hrs"])
    if right:
        d.text((W - 6 - d.textlength(right, font=F_MD), 4), right, font=F_MD, fill=(170, 170, 170))


def render():
    with S.lock:
        src, playing, title, artist, art = S.source, S.playing, S.title, S.artist, S.art
        b, view, device, inputs = S.batt, S.view, S.device, S.inputs
        tst, pair_until, bt_name = S.toast, S.pairing_until, S.bt_name
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    now = time.monotonic()

    if tst and now < tst[3]:
        l1, l2, col, _ = tst
        d.rectangle((0, 0, W - 1, H - 1), outline=col, width=4)
        d.text(((W - d.textlength(l1, font=F_LG)) / 2, 80), l1, font=F_LG, fill=col)
        l2 = fit(d, l2, F_LG, W - 20)
        d.text(((W - d.textlength(l2, font=F_LG)) / 2, 120), l2, font=F_LG, fill="white")
        return img
    img = _render_main(img, d, src, playing, title, artist, art, b, view, device, inputs)
    if pair_until > now:
        d = ImageDraw.Draw(img)
        left = int(pair_until - now)
        msg = f"Pairing mode  {left // 60}:{left % 60:02d}"
        sub = fit(d, f"Look for \u201c{bt_name}\u201d" if bt_name else "Open Bluetooth on your phone",
                  F_SM, W - 12)
        d.rectangle((0, H - 46, W, H), fill=(20, 60, 160))
        d.text(((W - d.textlength(msg, font=F_SM)) / 2, H - 42), msg, font=F_SM, fill="white")
        d.text(((W - d.textlength(sub, font=F_SM)) / 2, H - 22), sub, font=F_SM, fill=(200, 220, 255))
    return img


def _render_main(img, d, src, playing, title, artist, art, b, view, device, inputs):

    if view == "battery" and b:
        d.text((10, 8), "Battery", font=F_LG, fill="white")
        pct = f"{round(b['soc'] * 100)}%"
        d.text(((W - d.textlength(pct, font=F_XL)) / 2, 40), pct, font=F_XL, fill=batt_color(b))
        rows = [("Voltage", f"{b['v']:.2f} V"),
                ("Current", f"{b['i']:+.2f} A"),
                ("Power", f"{b['v'] * b['i']:+.0f} W"),
                ("Avg draw", f"{b['avg']:.2f} A"),
                ("Peak", f"{b['peak']:.1f} A"),
                ("Runtime", fmt_hrs(b["hrs"]) or "--")]
        for n, (k, v) in enumerate(rows):
            y = 110 + n * 21
            d.text((14, y), k, font=F_SM, fill=(160, 160, 160))
            d.text((W - 14 - d.textlength(v, font=F_MD), y - 2), v, font=F_MD, fill="white")
        if not b["synced"]:
            d.text((14, H - 16), "~ estimate until first full charge", font=font(11), fill=(130, 130, 130))
        return img

    if not src:   # idle
        if LOGO_IMG:
            img.paste(LOGO_IMG)
            d = ImageDraw.Draw(img)
        else:
            msg = "Ready"
            d.text(((W - d.textlength(msg, font=F_XL)) / 2, 80), msg, font=F_XL, fill=(150, 150, 150))
        d.rectangle((0, H - 48, W, H), fill=(0, 0, 0))
        if inputs:
            line = fit(d, "  ·  ".join(inputs), F_SM, W - 16)
            d.text(((W - d.textlength(line, font=F_SM)) / 2, H - 44), line, font=F_SM, fill=(140, 140, 140))
        if "Bluetooth" in inputs:
            hint = fit(d, PAIR_HINT, F_SM, W - 16)
            d.text(((W - d.textlength(hint, font=F_SM)) / 2, H - 22), hint, font=F_SM, fill=(110, 160, 255))
        if b:
            d.rectangle((0, 0, W, 26), fill=(0, 0, 0))
            draw_status(d, b)
        return img

    if not title:  # a source is active but has no track info (BT without AVRCP, Roon, ...)
        draw_status(d, b)
        label = SOURCE_ICON.get(src, "") + src
        d.text(((W - d.textlength(label, font=F_LG)) / 2, 95), label, font=F_LG, fill="white")
        sub = device or ("Connected" if src == "Bluetooth" else "Playing")
        sub = fit(d, sub, F_MD, W - 16)
        d.text(((W - d.textlength(sub, font=F_MD)) / 2, 125), sub, font=F_MD, fill=(170, 170, 170))
        return img

    draw_status(d, b)
    if art is not None:
        img.paste(art, ((W - ART) // 2, 28))
    else:
        d.rectangle(((W - ART) // 2, 28, (W + ART) // 2, 28 + ART), outline=(60, 60, 60), width=2)
        d.text((W / 2 - 8, 28 + ART / 2 - 14), "♪", font=font(28), fill=(90, 90, 90))
    badge = src if not device else f"{src} · {device}"
    badge = fit(d, badge, font(12), ART - 8)
    bw = d.textlength(badge, font=font(12)) + 10
    bx = (W - ART) // 2 + 4
    d.rounded_rectangle((bx, 28 + ART - 20, bx + bw, 28 + ART - 4), radius=4, fill=(0, 0, 0))
    d.text((bx + 5, 28 + ART - 19), badge, font=font(12), fill=(220, 220, 220))
    d.text((8, 192), fit(d, title, F_LG, W - 16), font=F_LG, fill="white")
    d.text((8, 216), fit(d, artist, F_MD, W - 16), font=F_MD, fill=(180, 180, 180))
    if not playing:
        d.text((W - 26, 192 - 24), "❚❚", font=F_SM, fill=(200, 200, 200))
    return img


# ------------------------------------------------------------------ buttons
def setup_buttons(display):
    try:
        from gpiozero import Button
    except ImportError:
        log.warning("gpiozero not available; buttons disabled")
        return []

    def toggle_screen():
        with S.lock:
            S.screen_on = not S.screen_on
            on = S.screen_on
        display.set_backlight(1 if on else 0)
        S.dirty.set()

    def toggle_view():
        with S.lock:
            S.view = "battery" if S.view == "now" else "now"
        S.dirty.set()

    def run(cmd):
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    import btctl

    def kick():
        S.set(screen_on=True)
        display.set_backlight(1)
        names = btctl.kick()
        CONN.suppress.update(names)
        if S.source == "AirPlay" and btctl.kick_airplay():
            names.append("AirPlay sender")
        if names:
            toast("Disconnected", ", ".join(names), (230, 90, 60))
        else:
            toast("Nothing connected", "", (150, 150, 150))

    def pairing():
        if S.pairing_until > time.monotonic():      # long-press again = cancel
            btctl.hide()
            S.set(pairing_until=0.0)
            toast("Pairing off", "", (150, 150, 150), 2)
            return
        btctl.pairing_mode(PAIR_S, on_done=lambda: S.set(pairing_until=0.0))
        S.set(bt_name=btctl.alias() or S.bt_name)
        S.set(pairing_until=time.monotonic() + PAIR_S)

    short = {"A": toggle_screen, "B": toggle_view,
             "X": lambda: run(VOL_UP), "Y": lambda: run(VOL_DN)}
    long = {"A": kick, "B": pairing}
    try:
        from gpiozero.pins.lgpio import LGPIOFactory
        factory = LGPIOFactory(chip=display.chip_num)
    except Exception as e:
        log.warning("lgpio pin factory: %s (using gpiozero default)", e)
        factory = None
    btns = []
    held = {}
    for name, pin in BUTTONS.items():
        try:
            b = Button(pin, pull_up=True, bounce_time=0.05, hold_time=LONG_PRESS_S,
                       pin_factory=factory)
        except Exception as e:
            log.warning("button %s (GPIO %d) disabled: %s", name, pin, e)
            continue
        held[name] = False

        def on_held(name=name):
            held[name] = True
            if name in long:
                threading.Thread(target=long[name], daemon=True).start()

        def on_released(name=name):
            if not held[name]:
                threading.Thread(target=short[name], daemon=True).start()
            held[name] = False

        b.when_held = on_held
        b.when_released = on_released
        btns.append(b)
    return btns


# ------------------------------------------------------------------ main
def main():
    from st7789_lite import ST7789
    display = ST7789(port=0, cs=1, dc=9, backlight=13, rotation=90,
                     spi_speed_hz=80 * 1000 * 1000, width=W, height=H)
    log.info("display up on /dev/gpiochip%d", display.chip_num)

    batt = None
    if BATTERY_ENABLED:
        try:
            batt = BatteryThread()
            batt.start()
        except Exception as e:
            log.error("battery monitor disabled: %s", e)

    def shutdown(*_):
        if batt:
            batt.g.save()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    from sources import SourceWatcher
    SourceWatcher(S, ART).start()
    try:
        import btctl
        btctl.startup_cleanup()
        if HIDE_BY_DEFAULT:
            btctl.hide()
        S.set(bt_name=btctl.alias())
        CONN.start()
    except Exception as e:
        log.error("bluetooth control disabled: %s", e)
    buttons = setup_buttons(display)  # noqa: F841  (keep references alive)

    S.dirty.set()
    while True:
        busy = (S.toast and time.monotonic() < S.toast[3] + 0.5) or S.pairing_until > time.monotonic()
        S.dirty.wait(timeout=1 if busy else 5)
        S.dirty.clear()
        if S.screen_on:
            display.display(render())
        time.sleep(0.15)   # coalesce bursts of updates


if __name__ == "__main__":
    main()
