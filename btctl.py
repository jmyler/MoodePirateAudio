"""Bluetooth / AirPlay connection control for a shared speaker.

- connected(): [(mac, name)] of connected Bluetooth devices
- kick(): disconnect the current Bluetooth device and block it for HOLD_S
  so it can't immediately auto-reconnect; also drops an AirPlay sender
- set_visible(on): discoverable + pairable, or hidden (paired phones can still connect)
- player(cmd): Play / Pause / Next / Previous on the connected phone (AVRCP)

Blocks are recorded in a state file and lifted on startup, so a crash can
never leave someone's phone permanently blocked.
"""
import json
import logging
import os
import subprocess
import threading
import time

log = logging.getLogger("pirate.bt")

HOLD_S = 5 * 60
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_blocks.json")
# moOde's helper for restarting a renderer; drops the current AirPlay sender
AIRPLAY_RESTART = ["/var/www/util/restart-renderer.php", "--airplay"]

_lock = threading.Lock()
_timers = {}

# All calls go straight to BlueZ over D-Bus (busctl). Never use bluetoothctl here:
# every bluetoothctl invocation registers itself as the default pairing agent and
# then exits, which knocks out moOde's agent and makes pairing hang on the phone.


_read_needs_root = None     # learned on first read: does BlueZ let us read without sudo?


def _busctl(*args, timeout=6, root=True):
    """Run busctl. Writes always use sudo -n; reads skip sudo when BlueZ allows it,
    so the journal isn't flooded with a sudo log line every second."""
    cmd = ["busctl", "--system", "--json=short", *args]
    if root:
        cmd = ["sudo", "-n"] + cmd
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        if root:
            log.warning("busctl %s: %s", " ".join(args[:4]), (r.stderr or r.stdout).strip()[:200])
        return None
    out = r.stdout.strip()
    return json.loads(out) if out else {}


def _read(*args):
    global _read_needs_root
    if _read_needs_root is not True:
        r = _busctl(*args, root=False)
        if r is not None:
            _read_needs_root = False
            return r
        if _read_needs_root is None:
            log.info("BlueZ reads need sudo on this system")
        _read_needs_root = True
    return _busctl(*args, root=True)


def _objects():
    """{path: {interface: {prop: value}}} for everything BlueZ manages."""
    r = _read("call", "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    if not r:
        return {}
    data = r["data"][0] if isinstance(r.get("data"), list) else r.get("data", {})
    return {path: {iface: {k: v.get("data") for k, v in props.items()} for iface, props in ifaces.items()}
            for path, ifaces in data.items()}


def adapter_path(objs=None):
    objs = objs if objs is not None else _objects()
    for path, ifaces in objs.items():
        if "org.bluez.Adapter1" in ifaces:
            return path
    return "/org/bluez/hci0"


def _dev_path(mac, objs=None):
    objs = objs if objs is not None else _objects()
    for path, ifaces in objs.items():
        d = ifaces.get("org.bluez.Device1")
        if d and d.get("Address", "").upper() == mac.upper():
            return path
    return f"{adapter_path(objs)}/dev_{mac.upper().replace(':', '_')}"


def _set_adapter(prop, sig, value):
    _busctl("set-property", "org.bluez", adapter_path(), "org.bluez.Adapter1", prop, sig, value)


def connected():
    """[(mac, name)] of connected Bluetooth devices."""
    out = []
    for path, ifaces in _objects().items():
        d = ifaces.get("org.bluez.Device1")
        if d and d.get("Connected"):
            out.append((d.get("Address", ""), d.get("Alias") or d.get("Name") or d.get("Address", "")))
    return out


def _block(mac, on):
    _busctl("set-property", "org.bluez", _dev_path(mac), "org.bluez.Device1", "Blocked", "b",
            "true" if on else "false")


def _load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(d):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, STATE)


def _unblock(mac):
    _block(mac, False)
    with _lock:
        d = _load()
        d.pop(mac, None)
        _save(d)
        _timers.pop(mac, None)
    log.info("unblocked %s", mac)


def startup_cleanup():
    """Lift any blocks left over from a previous run."""
    for mac in list(_load()):
        _unblock(mac)


def kick():
    """Disconnect + temporarily block the connected phone(s). Returns names kicked."""
    names = []
    for mac, name in connected():
        _busctl("call", "org.bluez", _dev_path(mac), "org.bluez.Device1", "Disconnect")
        _block(mac, True)
        with _lock:
            d = _load()
            d[mac] = {"name": name, "until": time.time() + HOLD_S}
            _save(d)
            t = threading.Timer(HOLD_S, _unblock, args=(mac,))
            t.daemon = True
            _timers[mac] = t
            t.start()
        names.append(name)
        log.info("kicked %s (%s) for %d s", name, mac, HOLD_S)
    return names


def kick_airplay():
    if os.path.exists(AIRPLAY_RESTART[0]):
        subprocess.run(["sudo", "-n", "php", *AIRPLAY_RESTART], capture_output=True, timeout=20)
        return True
    log.warning("%s not found; can't drop AirPlay sender", AIRPLAY_RESTART[0])
    return False


def hide():
    _set_adapter("Discoverable", "b", "false")


def set_visible(on):
    """Bluetooth-first speaker behaviour: discoverable/pairable when on, hidden when off.
    Phones that have already paired can always reconnect."""
    if on:
        _set_adapter("Pairable", "b", "true")
        _set_adapter("DiscoverableTimeout", "u", "0")
    _set_adapter("Discoverable", "b", "true" if on else "false")


def is_visible():
    objs = _objects()
    return bool(objs.get(adapter_path(objs), {}).get("org.bluez.Adapter1", {}).get("Discoverable"))


def _player_path(objs=None):
    """MediaPlayer1 object of the connected phone (AVRCP), or None."""
    objs = objs if objs is not None else _objects()
    devs = [p for p, i in objs.items() if i.get("org.bluez.Device1", {}).get("Connected")]
    for path, ifaces in objs.items():
        if "org.bluez.MediaPlayer1" in ifaces and any(path.startswith(d + "/") for d in devs):
            return path
    return None


def player(cmd):
    """Send an AVRCP command to the connected phone: Play, Pause, Next, Previous.
    Returns True if a player was found and the call went through."""
    path = _player_path()
    if not path:
        return False
    return _busctl("call", "org.bluez", path, "org.bluez.MediaPlayer1", cmd) is not None


def alias():
    """The name phones see for this speaker (adapter alias)."""
    objs = _objects()
    a = objs.get(adapter_path(objs), {}).get("org.bluez.Adapter1", {})
    return a.get("Alias") or a.get("Name") or ""
