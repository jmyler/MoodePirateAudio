"""Bluetooth / AirPlay connection control for a shared speaker.

- connected(): [(mac, name)] of connected Bluetooth devices
- kick(): disconnect the current Bluetooth device and block it for HOLD_S
  so it can't immediately auto-reconnect; also drops an AirPlay sender
- pairing_mode(seconds): discoverable + pairable for a while, then hidden again
- hide(): not discoverable (paired devices can still connect)

Blocks are recorded in a state file and lifted on startup, so a crash can
never leave someone's phone permanently blocked.
"""
import json
import logging
import os
import re
import subprocess
import threading
import time

log = logging.getLogger("pirate.bt")

HOLD_S = 5 * 60
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_blocks.json")
# moOde's helper for restarting a renderer; drops the current AirPlay sender
AIRPLAY_RESTART = ["/var/www/util/restart-renderer.php", "--airplay"]

_MAC = re.compile(r"Device ([0-9A-F:]{17}) (.*)")
_lock = threading.Lock()
_timers = {}


def _ctl(*args, timeout=8):
    """Run bluetoothctl non-interactively (sudo -n: never prompt)."""
    r = subprocess.run(["sudo", "-n", "bluetoothctl", *args],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        log.warning("bluetoothctl %s: %s", " ".join(args), (r.stderr or r.stdout).strip()[:200])
    return r.stdout


def connected():
    out = _ctl("devices", "Connected")
    return [(m.group(1), m.group(2).strip()) for m in _MAC.finditer(out)]


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
    _ctl("unblock", mac)
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
        _ctl("disconnect", mac)
        _ctl("block", mac)
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
    _ctl("discoverable", "off")


def pairing_mode(seconds, on_done=None):
    _ctl("pairable", "on")
    _ctl("discoverable", "on")

    def end():
        hide()
        if on_done:
            on_done()
    t = threading.Timer(seconds, end)
    t.daemon = True
    t.start()
    return t


def alias():
    """The name phones see for this speaker (adapter alias)."""
    m = re.search(r"Alias:\s*(.+)", _ctl("show"))
    return m.group(1).strip() if m else ""
