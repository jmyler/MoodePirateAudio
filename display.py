#!/usr/bin/env python3
"""Now playing, battery and connection control for a moOde speaker.

Screens (ui.py draws them): now playing / idle, battery (charging), system.
Controls (inputs.py): a rotary knob with push switch, the touch screen, and a
bench control pipe (`echo press > ctl`) for testing without hardware.

    Knob:   turn = volume, press = play/pause, press twice = next track,
            hold = menu (disconnect phone, add a phone, screens, controls help)
    Touch:  tap = play/pause, swipe = change screen, tap top bar = menu
"""
import collections
import logging
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time

import ui

# ------------------------------------------------------------------ config
HOME = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HOME, "battery_state.json")
SPLASH_CACHE = os.path.join(HOME, "splash.png")     # shown by splash.py early in boot
CTL_FIFO = os.path.join(HOME, "ctl")                 # bench control: echo press > ctl

# Battery (INA226)
BATTERY_ENABLED = True
CAPACITY_AH = 6.0          # Battery Hookup 25.6 V 6 Ah pack
CELLS = 8                  # 25.6 V LiFePO4 = 8S
SHUNT_OHMS = 0.002         # R002
CURRENT_GAIN = 1.0         # trim against a clamp meter
CURRENT_SIGN = 1           # -1 if discharge shows negative in ina_test.py
LOW_ALERTS = (20, 10)      # % thresholds for the low-battery popup

# Screen
PANEL = "waveshare2"       # Waveshare 2" 240x320, used landscape
ROTATION = 90              # 90 or 270 - flip if the picture is upside down
SCREEN_PINS = dict(port=0, cs=1, dc=9, backlight=13, rst=27)

# Knob (rotary encoder with push switch). Not fitted on this build: the touch screen and the
# phone do everything. To add one later, wire it to GPIO 5/6/16 and use dict(a=5, b=6, sw=16).
KNOB = dict(a=None, b=None, sw=None)
KNOB_REVERSE = False       # True if turning clockwise turns the volume down
KNOB_HOLD_S = 1.0          # hold this long for the menu
DOUBLE_S = 0.35            # second press within this = double press

# Touch (Waveshare 2" capacitive touch module, CST816D on I2C 0x15)
TOUCH = "auto"             # "auto" = use it if found, True, or False
TOUCH_PINS = dict(int=4, rst=17)
TOUCH_MAP = "rot90" if ROTATION == 90 else "rot270"     # see touch_test.py

# Volume (moOde)
VOL_STEP = 2               # per knob detent
VOL_TOUCH_STEP = 5         # per tap on the screen's volume buttons
CONTROLS_S = 5             # touch control bar hides after this long untouched
VOL_MAX = 100              # cap, e.g. 85 so a borrowed phone can't run it flat out
VOL_SH = "/var/www/util/vol.sh"

# Join screen: the Wi-Fi QR code uses the password NetworkManager has stored.
# Set this to override it (or "" for an open network); None = read it from the Pi.
WIFI_QR_PSK = None

# Behaviour
PAIR_S = 120               # "Add a phone" window while someone is connected
POPUP_S = 4
VOL_SHOW_S = 1.6
MENU_TIMEOUT_S = 15
HOT_C = 75                 # CPU temperature warning

# Power button + Pololu soft-latching power switch (see the wiring page). The button turns the
# box on through the Pololu board; while running, holding it powers off cleanly, and the Pi's
# last act (dtoverlay=gpio-poweroff,gpiopin=26) cuts the power.
POWER_BUTTON = 23          # GPIO the button is read on (through the diode); None = not fitted
POWER_HOLD_S = 2.0         # hold this long to power off, so a bump in the bag can't do it
AUTO_OFF_MIN = 30          # power off after this long with nothing connected or playing; None = never
CRITICAL_CELL_V = 2.95     # below this per cell (held 20 s, not charging) power off before the BMS cuts
HIST_BUCKET_S = 30         # session graph: one point per 30 s ...
HIST_POINTS = 480          # ... for the last 4 hours
VIEW_TIMEOUT_S = 60        # info screens go back to now playing after this long untouched

# Backlight: full while playing; dims when nothing's happening
BRIGHT_FULL = 1.0
BRIGHT_DIM = 0.4
DIM_PAUSED_S = 120         # connected but paused/stopped
DIM_IDLE_S = 60            # nobody connected
OFF_IDLE_S = 600           # backlight off when nobody's connected for this long

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pirate")


# ------------------------------------------------------------------ shared state
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.dirty = threading.Event()
        # playback (sources.py)
        self.source = None
        self.device = ""
        self.inputs = ()
        self.playing = False
        self.title = self.artist = self.album = ""
        self.art = None
        self.airplay_name = ""
        self.ready = False
        # battery / system
        self.batt = None
        self.sys = {}
        # UI
        self.view = "now"
        self.popup = None              # (dict, expires)
        self.menu = None               # dict(sel, opened, confirm_until)
        self.help = False
        self.vol_show = None           # (level, expires)
        self.pairing_until = 0.0
        self.bt_name = ""
        self.bt_visible = False
        self.touch = False
        self.activity = time.monotonic()
        self.last_input = time.monotonic()
        self.phones = []               # paired phones (btctl.devices())
        self.phone_sel = 0
        self.phone_focus = False       # knob has moved the highlight on the Phones screen
        self.join = {}                 # Wi-Fi / moOde details for the QR codes
        self.stats = {}                # session stats
        self.controls_until = 0.0      # touch control bar on Now playing
        self.power_hold = 0.0          # when the power button went down (0 = not held)
        self.shutting_down = False
        self.vol_level = 0

    def set(self, **kw):
        changed = False
        with self.lock:
            for k, v in kw.items():
                if getattr(self, k) is not v and getattr(self, k) != v:
                    setattr(self, k, v)
                    changed = True
        if changed:
            self.dirty.set()

    def poke(self):
        """User did something: full brightness, redraw."""
        self.activity = time.monotonic()
        self.dirty.set()


S = State()
VIEWS = ("now", "battery", "stats", "phones", "join", "system")


def power_off(reason):
    """Clean shutdown. With the Pololu switch fitted, the gpio-poweroff overlay cuts the power at the end."""
    if S.shutting_down:
        return
    log.info("powering off: %s", reason)
    S.set(shutting_down=True, menu=None, help=False, popup=None)
    S.poke()

    def go():
        time.sleep(1.5)                          # let the "Powering off" screen draw
        if not _run(["sudo", "-n", "systemctl", "poweroff"], timeout=10):
            log.error("poweroff failed (needs passwordless sudo)")
            S.set(shutting_down=False)
    threading.Thread(target=go, daemon=True).start()


def popup(icon, color, small, big, note="", secs=POPUP_S, **kw):
    S.set(popup=(dict(icon=icon, color=color, small=small, big=big, note=note, **kw), time.monotonic() + secs))
    S.poke()


# ------------------------------------------------------------------ playback control
def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("%s: %s", cmd[0], e)
        return False


_airplay_ctl = None     # D-Bus remote-control name for shairport-sync, once found


def _airplay(cmd):
    """shairport-sync's remote control (needs its D-Bus interface; not every build has it)."""
    global _airplay_ctl
    tries = [_airplay_ctl] if _airplay_ctl else [
        ("org.gnome.ShairportSync", "/org/gnome/ShairportSync", "org.gnome.ShairportSync.RemoteControl"),
        ("org.mpris.MediaPlayer2.ShairportSync", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player")]
    for name, path, iface in tries:
        if _run(["sudo", "-n", "busctl", "--system", "call", name, path, iface, cmd], timeout=3):
            _airplay_ctl = (name, path, iface)
            return True
    return False


def play_pause():
    src, playing = S.source, S.playing
    import btctl
    ok = False
    if src == "Bluetooth":
        ok = btctl.player("Pause" if playing else "Play")
    elif src == "AirPlay":
        ok = _airplay("PlayPause")
    elif src in ("Library", "Radio"):
        ok = _run(["mpc", "-q", "toggle"])
    if ok:
        S.set(playing=not playing)               # optimistic; sources.py confirms within a second
    elif src:
        popup("pause", (255, 255, 255), "Can't control", src, "Pause from the phone", icon_alpha=40)


def prev_track():
    src = S.source
    import btctl
    ok = False
    if src == "Bluetooth":
        ok = btctl.player("Previous")
    elif src == "AirPlay":
        ok = _airplay("Previous")
    elif src in ("Library", "Radio"):
        ok = _run(["mpc", "-q", "prev"])
    if not ok and src:
        popup("next", (255, 255, 255), "Can't go back", src, "Use the phone", icon_alpha=40)


def next_track():
    src = S.source
    import btctl
    ok = False
    if src == "Bluetooth":
        ok = btctl.player("Next")
    elif src == "AirPlay":
        ok = _airplay("Next")
    elif src in ("Library", "Radio"):
        ok = _run(["mpc", "-q", "next"])
    if not ok and src:
        popup("next", (255, 255, 255), "Can't skip", src, "Skip from the phone", icon_alpha=40)


class Volume(threading.Thread):
    """Knob turns move a target; one worker applies the latest value (vol.sh is slow-ish)."""
    daemon = True

    def __init__(self):
        super().__init__(name="volume")
        self.target = None
        self.last_step = 0.0
        self.cv = threading.Condition()

    @staticmethod
    def current():
        try:
            con = sqlite3.connect("file:/var/local/www/db/moode-sqlite3.db?mode=ro", uri=True, timeout=2)
            try:
                return int(con.execute("select value from cfg_system where param='volknob'").fetchone()[0])
            finally:
                con.close()
        except Exception:
            return 50

    def step(self, n, size=VOL_STEP):
        with self.cv:
            recent = time.monotonic() - self.last_step < 3
            base = self.target if (self.target is not None and recent) else self.current()
            self.target = max(0, min(VOL_MAX, base + n * size))
            level = self.target
            self.last_step = time.monotonic()
            self.cv.notify()
        S.set(vol_level=level)
        if not (S.controls_until > time.monotonic()):       # the control bar shows volume itself
            S.set(vol_show=(level, time.monotonic() + VOL_SHOW_S))
        S.poke()

    def run(self):
        applied = None
        while True:
            with self.cv:
                while self.target == applied:
                    self.cv.wait()
                want = self.target
            if not _run(["sudo", "-n", VOL_SH, str(want)]):
                log.warning("volume: %s %s failed", VOL_SH, want)
            applied = want


VOL = Volume()


# ------------------------------------------------------------------ Bluetooth visibility & pairing
def end_pairing():
    S.set(pairing_until=0.0)


def start_pairing():
    S.set(pairing_until=time.monotonic() + PAIR_S, bt_visible=True)
    try:
        import btctl
        btctl.set_visible(True)
    except Exception as e:
        log.warning("pairing: %s", e)


def kick():
    import btctl
    names = btctl.kick()
    CONN.suppress.update(names)
    if S.source == "AirPlay" and btctl.kick_airplay():
        names.append("AirPlay")
    if names:
        popup("bt_off", (255, 255, 255), "Disconnected", ", ".join(names),
              f"Can't reconnect for {btctl.HOLD_S // 60} min", icon_alpha=40)
    else:
        popup("bt_off", (255, 255, 255), "Nothing connected", "—", icon_alpha=40)


class _Suppress:
    """Set of names that expires: skip the generic 'Disconnected' popup for ~10 s."""
    def __init__(self):
        self.d = {}

    def add(self, name):
        if name:
            self.d[name] = time.monotonic() + 10

    def update(self, names):
        for n in names:
            self.add(n)

    def __contains__(self, name):
        return self.d.get(name, 0) > time.monotonic()


class ConnWatcher(threading.Thread):
    """Connect/disconnect popups, and the Bluetooth-first visibility rule:
    discoverable whenever nothing is connected or playing, hidden otherwise
    (so nobody can grab the speaker mid-song), plus the "Add a phone" window."""
    daemon = True

    def __init__(self):
        super().__init__(name="conn")
        self.known = None
        self.suppress = _Suppress()      # names we just disconnected ourselves (own popup shown)
        self.visible = None
        self.last_assert = 0
        self.refresh = threading.Event()

    def run(self):
        import btctl
        while True:
            try:
                now = dict(btctl.connected())
                if self.known is not None:
                    for mac, name in now.items():
                        if mac not in self.known:
                            log.info("connected: %s (%s)", name, mac)
                            popup("bt", ui.BLUE, "Connected", name)
                            if S.pairing_until > time.monotonic():
                                end_pairing()
                    for mac, name in self.known.items():
                        if mac not in now and name not in self.suppress:
                            log.info("disconnected: %s (%s)", name, mac)
                            popup("bt_off", (255, 255, 255), "Disconnected", name, icon_alpha=40)
                self.known = now
                S.set(phones=btctl.devices())
                want = (not now and S.source is None) or S.pairing_until > time.monotonic()
                if want != self.visible or time.monotonic() - self.last_assert > 60:
                    btctl.set_visible(want)
                    self.visible, self.last_assert = want, time.monotonic()
                    S.set(bt_visible=want)
            except Exception as e:
                log.debug("conn watch: %s", e)
            self.refresh.wait(2)
            self.refresh.clear()


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
        self.alerted = set()
        self.was_charging = False
        self._crit_since = None
        # session stats (since this program started, i.e. since power-on in normal use)
        self.t0 = time.monotonic()
        self.used_ah = 0.0
        self.hist = collections.deque(maxlen=HIST_POINTS)     # (monotonic, avg amps) per bucket
        self._bucket = [0.0, 0, time.monotonic()]             # sum, count, start
        self._last_t = None

    def alerts(self, soc, charging):
        pct = soc * 100
        for th in LOW_ALERTS:
            if charging or pct > th + 5:
                self.alerted.discard(th)
            elif pct <= th and th not in self.alerted:
                self.alerted.update(t for t in LOW_ALERTS if t >= th)
                left = ui.fmt_dur(self.g.hours_left)
                popup("battery", ui.RED if th <= 10 else ui.AMBER, "Battery low",
                      f"{round(pct)}%" + (f" · ~{left}" if left else ""), "Plug in the charger", secs=8)
        v = getattr(self.g, "v", 0.0)
        if not charging and 2.5 * CELLS < v < CRITICAL_CELL_V * CELLS:
            self._crit_since = self._crit_since or time.monotonic()
            if time.monotonic() - self._crit_since > 20 and not S.shutting_down:
                popup("battery", ui.RED, "Battery empty", "Powering off", "Charge before next use", secs=10)
                threading.Timer(8, power_off, args=("battery empty",)).start()
                self._crit_since = time.monotonic() + 3600      # don't repeat
        else:
            self._crit_since = None
        if self.was_charging and not charging and pct >= 99:
            popup("bolt", ui.GREEN, "Battery", "Fully charged")
        self.was_charging = charging

    def run(self):
        last_push = 0
        while True:
            try:
                v, i = self.ina.voltage(), self.ina.current()
                self.g.update(v, i)
                self.peak = max(self.peak, i)
                now = time.monotonic()
                if self._last_t is not None and i > 0:
                    self.used_ah += i * (now - self._last_t) / 3600
                self._last_t = now
                b = self._bucket
                b[0] += max(i, 0.0)
                b[1] += 1
                if now - b[2] >= HIST_BUCKET_S:
                    self.hist.append((now, b[0] / b[1]))
                    self._bucket = [0.0, 0, now]
                if now - last_push >= 2:
                    last_push = now
                    charging = i < -0.1 and v > 2.5 * CELLS
                    soc = self.g.soc
                    full = ((1 - soc) * CAPACITY_AH / -i) if charging else None
                    S.set(batt=dict(v=v, i=i, soc=soc, hrs=self.g.hours_left, synced=self.g.synced,
                                    peak=self.peak, avg=self.g.avg_draw, charging=charging, full_hrs=full))
                    self.alerts(soc, charging)
                    run_h = (now - self.t0) / 3600
                    hist = [(now - ts, a) for ts, a in self.hist]
                    if self._bucket[1]:
                        hist.append((0.0, self._bucket[0] / self._bucket[1]))
                    S.set(stats=dict(uptime=now - self.t0, used_ah=self.used_ah,
                                     used_pct=100 * self.used_ah / CAPACITY_AH,
                                     avg=self.used_ah / run_h if run_h > 0.01 else max(i, 0.0),
                                     peak=self.peak, hist=hist))
            except OSError as e:
                log.warning("INA226 read: %s", e)
                time.sleep(2)
            time.sleep(0.5)


# ------------------------------------------------------------------ system health
class SysThread(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__(name="system")
        self.warned = {}

    @staticmethod
    def _out(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _join(self, sy):
        """SSID + password of the Wi-Fi the Pi is on (home network or its own hotspot), for the QR code.
        NetworkManager only reveals the password to root, hence sudo -n."""
        j = dict(ssid=sy.get("ssid", ""), hotspot=sy.get("hotspot"), ip=sy.get("ip"), host=sy.get("host"))
        if not j["ssid"]:
            return j
        if WIFI_QR_PSK is not None:
            j["psk"] = WIFI_QR_PSK
            return j
        name = ""
        for ln in self._out(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"]).splitlines():
            n, _, dev = ln.rpartition(":")
            if dev == "wlan0":
                name = n.replace("\\:", ":")
        if name:
            psk = self._out(["sudo", "-n", "nmcli", "-s", "-g", "802-11-wireless-security.psk",
                             "connection", "show", name]).strip()
            j["psk"] = psk          # "" = open network
        return j

    def _warn(self, key, *args, **kw):
        if time.monotonic() - self.warned.get(key, -1e9) > 600:
            self.warned[key] = time.monotonic()
            popup(*args, secs=8, **kw)

    def run(self):
        last_net = 0
        while True:
            sy = dict(S.sys)
            try:
                with open("/sys/class/thermal/thermal_zone0/temp") as f:
                    sy["temp"] = int(f.read()) / 1000
            except (OSError, ValueError):
                sy["temp"] = None
            thr = self._out(["vcgencmd", "get_throttled"]).strip()
            try:
                bits = int(thr.split("=")[1], 16)
                sy["uv_now"], sy["uv_seen"] = bool(bits & 0x1), bool(bits & 0x10000)
            except (IndexError, ValueError):
                pass
            try:
                with open("/proc/uptime") as f:
                    sy["uptime"] = float(f.read().split()[0])
            except OSError:
                pass
            if S.view in ("system", "join") or time.monotonic() - last_net > 60:
                last_net = time.monotonic()
                info = self._out(["iw", "dev", "wlan0", "info"])
                sy["hotspot"] = "type AP" in info
                sy["ssid"] = next((ln.split("ssid", 1)[1].strip() for ln in info.splitlines()
                                   if ln.strip().startswith("ssid ")), "")
                sy["rssi"] = None
                if not sy["hotspot"]:
                    for ln in self._out(["iw", "dev", "wlan0", "link"]).splitlines():
                        if "signal:" in ln:
                            try:
                                sy["rssi"] = int(ln.split("signal:")[1].split()[0])
                            except (IndexError, ValueError):
                                pass
                ips = self._out(["hostname", "-I"]).split()
                sy["ip"] = ips[0] if ips else ""
                sy["host"] = socket.gethostname() + ".local"
                S.set(join=self._join(sy))
            S.set(sys=sy)
            if sy.get("temp") and sy["temp"] >= HOT_C:
                self._warn("hot", "warn", ui.AMBER, "Running hot", f"{sy['temp']:.0f}°C", "Shade the box or open the lid")
            if sy.get("uv_now"):
                self._warn("uv", "warn", ui.RED, "Power supply", "Voltage low", "Check the 5 V wiring")
            time.sleep(5)


# ------------------------------------------------------------------ menu
def _view_tile(m, view, icon, label):
    here = m.get("view", "now") == view
    return dict(id="view", view=view, icon="music" if here else icon, label="Now playing" if here else label)


def menu_items(m):
    """Main menu for the current state (also used by preview.py)."""
    items = []
    if m.get("source") == "Bluetooth" and m.get("device"):
        items.append(dict(id="kick", icon="bt_off", color=ui.RED, label=f"Disconnect {m['device']}",
                          confirm="Confirm disconnect"))
    elif m.get("source") == "AirPlay":
        items.append(dict(id="kick", icon="bt_off", color=ui.RED, label="Stop AirPlay", confirm="Confirm stop"))
    else:
        items.append(dict(id="kick", icon="bt_off", label="No phone connected", enabled=False))
    pairing = bool(m.get("pairing_left"))
    items.append(dict(id="pair", icon="plus", color=ui.BLUE, label="Stop pairing" if pairing else "Add a phone"))
    items.append(_view_tile(m, "phones", "phone", "Phones"))
    items.append(_view_tile(m, "join", "qr", "Join Wi-Fi"))
    items.append(_view_tile(m, "battery", "battery", "Battery"))
    items.append(_view_tile(m, "system", "system", "System"))
    items.append(dict(id="help", label="Controls", header=True))
    return items


def phone_items(dev):
    """Action sheet for one paired phone."""
    items = []
    if dev.get("connected"):
        items.append(dict(id="disconnect", icon="bt_off", color=ui.RED, label="Disconnect",
                          confirm="Confirm disconnect"))
    elif dev.get("blocked"):
        items.append(dict(id="unblock", icon="bt", color=ui.BLUE, label="Unblock now"))
    items.append(dict(id="forget", icon="x", color=ui.RED, label="Forget phone", confirm="Confirm forget"))
    items.append(dict(id="cancel", icon="x", label="Cancel"))
    return items


def _phone(mac):
    return next((p for p in S.phones if p["mac"] == mac), None)


def current_menu_items(m=None):
    menu = S.menu or {}
    if menu.get("kind") == "phone":
        dev = _phone(menu.get("mac"))
        return phone_items(dev) if dev else [dict(id="cancel", icon="x", label="Cancel")]
    return menu_items(m if m is not None else snapshot(with_menu=False))


def _bt(fn, *args):
    def run():
        try:
            import btctl
            getattr(btctl, fn)(*args)
            CONN.refresh.set()
        except Exception as e:
            log.warning("bluetooth %s: %s", fn, e)
    threading.Thread(target=run, daemon=True).start()


def _menu_select(k):
    items = current_menu_items()
    if not (0 <= k < len(items)) or not items[k].get("enabled", True):
        return
    it = items[k]
    if it.get("confirm") and S.menu.get("confirm_until", 0) < time.monotonic():   # first press arms it
        S.menu["confirm_until"] = time.monotonic() + 3
        S.menu["sel"] = k
        S.dirty.set()
        return
    menu = S.menu
    S.set(menu=None)
    i = it["id"]
    if i == "kick":
        threading.Thread(target=kick, daemon=True).start()
    elif i == "pair":
        end_pairing() if S.pairing_until > time.monotonic() else start_pairing()
    elif i == "view":
        S.set(view="now" if S.view == it["view"] else it["view"])
    elif i == "help":
        S.set(help=True)
    elif i == "disconnect":
        dev = _phone(menu.get("mac")) or {}
        CONN.suppress.add(dev.get("name", ""))
        _bt("disconnect", menu["mac"])
        popup("bt_off", (255, 255, 255), "Disconnected", dev.get("name", ""),
              "Can't reconnect for 5 min", icon_alpha=40)
    elif i == "unblock":
        _bt("unblock", menu["mac"])
    elif i == "forget":
        dev = _phone(menu.get("mac")) or {}
        CONN.suppress.add(dev.get("name", ""))
        _bt("forget", menu["mac"])
        popup("x", (255, 255, 255), "Forgotten", dev.get("name", ""), "It will need to pair again", icon_alpha=40)


def open_menu():
    S.set(menu=dict(kind="main", sel=0, opened=time.monotonic(), confirm_until=0), help=False)


def open_phone_sheet(i):
    if 0 <= i < len(S.phones):
        S.set(phone_sel=i, menu=dict(kind="phone", mac=S.phones[i]["mac"], sel=0,
                                     opened=time.monotonic(), confirm_until=0), help=False)


# ------------------------------------------------------------------ input handling
SCREEN = {"bright": 1.0}


def handle(ev):
    kind = ev[0]
    asleep = SCREEN["bright"] <= 0.0
    S.poke()
    S.last_input = time.monotonic()
    if asleep and kind in ("tap", "swipe", "touch_hold"):
        return                                   # first touch only wakes the screen
    if kind == "turn" and not S.menu and not S.help and S.view == "phones" and S.phones:
        S.set(phone_sel=(S.phone_sel + ev[1]) % len(S.phones), phone_focus=True)    # knob picks a phone
        return
    if kind == "turn" and not S.menu:
        S.set(popup=None)                        # the volume bar takes over from any popup
        VOL.step(ev[1])
        return

    if S.help:                                   # any press/tap closes the controls card
        if kind in ("press", "double", "hold", "tap", "touch_hold"):
            S.set(help=False)
        return

    if S.menu:
        S.menu["opened"] = time.monotonic()
        items = current_menu_items()
        n = len(items)
        if kind == "turn":
            S.menu["sel"] = (S.menu.get("sel", 0) + ev[1]) % n
            S.menu["confirm_until"] = 0
            S.dirty.set()
        elif kind == "press":
            _menu_select(S.menu.get("sel", 0))
        elif kind in ("hold", "touch_hold"):
            S.set(menu=None)
        elif kind == "tap":
            hit = ui.menu_hit(ev[1], ev[2], items)
            if hit == "close":
                S.set(menu=None)
            elif hit is not None:
                if hit != S.menu.get("sel"):
                    S.menu["confirm_until"] = 0
                S.menu["sel"] = hit
                _menu_select(hit)
        return

    if kind == "tap" and S.popup and S.popup[1] > time.monotonic():
        S.set(popup=None)
        return
    if S.view == "phones" and kind == "press" and S.phones:
        open_phone_sheet(S.phone_sel)
        return
    if S.view == "phones" and kind == "tap" and ev[2] >= ui.STATUS_H:
        hit = ui.phones_hit(ev[1], ev[2], len(S.phones), S.phone_sel)
        if hit is not None:
            open_phone_sheet(hit)
        return
    if kind == "press":
        play_pause()
    elif kind == "double":
        next_track()
    elif kind in ("hold", "touch_hold"):
        open_menu()
    elif kind == "swipe" and ev[1] in ("left", "right"):
        i = VIEWS.index(S.view) if S.view in VIEWS else 0
        S.set(view=VIEWS[(i + (1 if ev[1] == "left" else -1)) % len(VIEWS)], phone_focus=False, controls_until=0.0)
    elif kind == "tap":
        if ev[2] < ui.STATUS_H:
            open_menu()
        elif S.view == "now" and S.source:
            tap_now_playing(ev[1], ev[2])


def tap_now_playing(x, y):
    """First tap shows the control bar; taps on it run its buttons; a tap elsewhere hides it."""
    now = time.monotonic()
    if S.controls_until <= now:
        S.set(controls_until=now + CONTROLS_S, vol_level=Volume.current(), vol_show=None)
        return
    hit = ui.controls_hit(x, y)
    if hit is None:
        S.set(controls_until=0.0)
        return
    S.set(controls_until=now + CONTROLS_S)
    if hit == "play":
        play_pause()
    elif hit == "next":
        next_track()
    elif hit == "prev":
        prev_track()
    elif hit == "vol_up":
        VOL.step(1, VOL_TOUCH_STEP)
    elif hit == "vol_down":
        VOL.step(-1, VOL_TOUCH_STEP)


# ------------------------------------------------------------------ rendering
def snapshot(with_menu=True):
    now = time.monotonic()
    with S.lock:
        m = dict(source=S.source, device=S.device, playing=S.playing, title=S.title, artist=S.artist,
                 album=S.album, art=S.art, batt=S.batt, view=S.view, bt_name=S.bt_name,
                 airplay_name=S.airplay_name, bt_visible=S.bt_visible, inputs=S.inputs,
                 sys=S.sys, touch=S.touch, t=now, help=S.help, phones=list(S.phones),
                 phone_sel=min(S.phone_sel, max(0, len(S.phones) - 1)), phone_focus=S.phone_focus or S.touch,
                 join=S.join, stats=S.stats, now_wall=time.time(),
                 knob=any(v is not None for v in KNOB.values()))
        if S.power_hold:
            m["power_hold"] = min(1.0, (now - S.power_hold) / POWER_HOLD_S)
        if S.shutting_down:
            m.update(splash_kind="off", splash_title=S.bt_name or "Speaker", splash_sub="Powering off…")
        if S.controls_until > now:
            m["controls"] = dict(playing=S.playing, volume=S.vol_level, cap=VOL_MAX)
        if S.popup and S.popup[1] > now:
            m["popup"] = S.popup[0]
        if S.vol_show and S.vol_show[1] > now:
            m["volume"] = (S.vol_show[0], VOL_MAX)
        if S.pairing_until > now:
            left = int(S.pairing_until - now)
            m["pairing_left"] = (left, left / PAIR_S)
        menu = S.menu
    if m["view"] == "now" and not m["source"] and (m["batt"] or {}).get("charging"):
        m["view"] = "battery"                    # idle + plugged in: show charging
    if menu and with_menu:
        if menu.get("kind") == "phone":
            dev = _phone(menu.get("mac")) or {}
            items = current_menu_items(m)
            title = dev.get("name", "Phone")
            sub = ("Connected" if dev.get("connected") else "Blocked for a few minutes" if dev.get("blocked")
                   else "Paired") if dev else "No longer paired"
        else:
            items = menu_items(m)
            title = f"{m['device']} connected" if m.get("device") else (m.get("bt_name") or "Menu")
            sub = ""
        sel = menu.get("sel", 0)
        if menu.get("confirm_until", 0) > now and 0 <= sel < len(items):
            items[sel]["confirming"] = True
        m["menu"] = dict(items=items, sel=sel, title=title, subtitle=sub)
    return m


def target_brightness(m):
    idle_for = time.monotonic() - S.activity
    if (m.get("popup") or m.get("menu") or m.get("help") or m.get("volume") or m.get("pairing_left")
            or m.get("controls") or m.get("power_hold") is not None or m.get("splash_kind")):
        return BRIGHT_FULL
    if idle_for < 10 or (m["source"] and m["playing"]):
        return BRIGHT_FULL
    if m["source"]:
        return BRIGHT_DIM if idle_for > DIM_PAUSED_S else BRIGHT_FULL
    if idle_for > OFF_IDLE_S:
        return 0.0
    return BRIGHT_DIM if idle_for > DIM_IDLE_S else BRIGHT_FULL


def animating(m):
    """Screens with motion get redrawn on a timer."""
    if m.get("power_hold") is not None:
        return 0.1
    if m.get("pairing_left") or m.get("popup") or m.get("volume") or m.get("controls"):
        return 0.5
    if m["view"] == "battery" and (m["batt"] or {}).get("charging"):
        return 0.5
    if m["view"] == "now" and not m["source"] and m.get("bt_visible"):
        return 0.5
    return None


def save_splash(name):
    try:
        img = ui.render(dict(splash_kind="boot", splash_title=name, splash_sub="Starting up…", t=0.3))
        img.save(SPLASH_CACHE + ".tmp.png")
        os.replace(SPLASH_CACHE + ".tmp.png", SPLASH_CACHE)
    except Exception as e:
        log.debug("splash cache: %s", e)


# ------------------------------------------------------------------ main
def main():
    from st7789_lite import ST7789
    display = ST7789(panel=PANEL, rotation=ROTATION, **SCREEN_PINS)
    if (display.width, display.height) != (ui.W, ui.H):
        raise SystemExit(f"ROTATION {ROTATION} gives {display.width}x{display.height}; UI expects {ui.W}x{ui.H}")
    log.info("display up on /dev/gpiochip%d", display.chip_num)
    boot_t = time.monotonic()
    try:
        import btctl
        S.set(bt_name=btctl.alias())
    except Exception as e:
        log.warning("bluetooth name: %s", e)

    def splash(sub, kind="boot"):
        display.display(ui.render(dict(splash_kind=kind, splash_title=S.bt_name or "Speaker",
                                       splash_sub=sub, t=time.monotonic() - boot_t)))
    splash("Starting up…")

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
        try:      # system going down (not just a service restart): leave a goodbye on screen
            state = subprocess.run(["systemctl", "is-system-running"], capture_output=True, text=True,
                                   timeout=1).stdout.strip()
        except Exception:
            state = ""
        if state == "stopping":
            splash("Safe to switch off when the screen goes dark", kind="off")
            display.set_backlight(1)
        else:
            display.set_backlight(0)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    from sources import SourceWatcher
    SourceWatcher(S, ui.ART_PX).start()
    SysThread().start()
    VOL.start()
    try:
        import btctl
        btctl.startup_cleanup()
        CONN.start()
    except Exception as e:
        log.error("bluetooth control disabled: %s", e)
    save_splash(S.bt_name or "Speaker")

    # ---- inputs
    from inputs import Inputs
    inp = Inputs(handle)
    try:
        from gpiozero.pins.lgpio import LGPIOFactory
        factory = LGPIOFactory(chip=display.chip_num)
    except Exception as e:
        log.warning("lgpio pin factory: %s (using gpiozero default)", e)
        factory = None
    inp.add_encoder(KNOB.get("a"), KNOB.get("b"), KNOB.get("sw"), factory, KNOB_REVERSE, DOUBLE_S, KNOB_HOLD_S)
    if TOUCH:
        try:
            from cst816 import CST816
            t = CST816(rst=TOUCH_PINS.get("rst"), gpio_chip=display.chip, mapping=TOUCH_MAP)
            inp.add_touch(t)
            S.set(touch=True)
        except Exception as e:
            (log.info if TOUCH == "auto" else log.error)("no touch controller: %s", e)
    inp.add_fifo(CTL_FIFO)
    if POWER_BUTTON is not None:
        try:
            from gpiozero import Button
            pb = Button(POWER_BUTTON, pull_up=True, bounce_time=0.05, hold_time=POWER_HOLD_S, pin_factory=factory)

            def pressed():
                S.set(power_hold=time.monotonic())
                S.poke()
            pb.when_pressed = pressed
            pb.when_released = lambda: S.set(power_hold=0.0)
            pb.when_held = lambda: power_off("power button")
            inp._keep.append(pb)
            log.info("power button on GPIO %d (hold %.0f s to power off)", POWER_BUTTON, POWER_HOLD_S)
        except Exception as e:
            log.warning("power button disabled: %s", e)

    # ---- wait briefly for moOde before leaving the splash
    while not S.ready and time.monotonic() - boot_t < 20:
        splash("Starting up…")
        time.sleep(0.25)

    S.poke()
    last_refresh = time.monotonic()
    last_key = None
    while True:
        m = snapshot()
        # expire transient UI
        if S.popup and S.popup[1] <= m["t"]:
            S.set(popup=None)
        if S.menu and m["t"] - S.menu.get("opened", 0) > MENU_TIMEOUT_S:
            S.set(menu=None)
        if S.view != "now" and not S.menu and m["t"] - S.last_input > VIEW_TIMEOUT_S:
            S.set(view="now", phone_focus=False)
        # anything new playing counts as activity (wakes a dimmed screen)
        key = (m["source"], m["title"], m["playing"], m["device"])
        if key != last_key:
            last_key = key
            S.activity = time.monotonic()
        # auto power-off when nothing has happened for a long time
        if AUTO_OFF_MIN and not S.shutting_down:
            idle = (not m["source"] and not (CONN.known or {}) and not (m["batt"] or {}).get("charging")
                    and not m.get("pairing_left"))
            idle_for = time.monotonic() - S.activity if idle else 0
            left = AUTO_OFF_MIN * 60 - idle_for
            if idle and left <= 0:
                power_off("idle")
            elif idle and left <= 60:
                # set directly: popup() would count as activity and cancel the countdown
                S.set(popup=(dict(icon="power", color=(255, 255, 255), icon_alpha=40, small="Nothing playing",
                                  big=f"Off in {int(left)} s", note="Tap or connect to stay on"),
                             time.monotonic() + 2))
        # backlight
        want = target_brightness(m)
        if want != SCREEN["bright"]:
            display.set_backlight(want)
            SCREEN["bright"] = want
        # recover from a power-dip reset of the panel
        if m["t"] - last_refresh > 30:
            last_refresh = m["t"]
            try:
                display.refresh_config()
            except Exception as e:
                log.warning("screen refresh: %s", e)
        if want > 0:
            t0 = time.monotonic()
            display.display(ui.render(snapshot()))
            log.debug("frame %.0f ms", (time.monotonic() - t0) * 1000)
        S.dirty.clear()
        S.dirty.wait(timeout=animating(m) or 5)
        time.sleep(0.05)       # coalesce bursts of updates


if __name__ == "__main__":
    main()
