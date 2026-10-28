"""Figure out what moOde is playing, whatever the source.

moOde already tracks the active renderer in its settings DB (aplactive,
btactive, spotactive, ...). We follow those flags and pull metadata from:
  - AirPlay:   /var/local/www/aplmeta.json  (written by moOde's own reader,
               so we never touch the shairport pipe and never steal its data)
  - Bluetooth: BlueZ over D-Bus (busctl): device name + AVRCP track info
  - Spotify:   /var/local/www/spotmeta.json if moOde writes one, else label only
  - Library / radio / UPnP: MPD
"""
import io
import json
import logging
import os
import sqlite3
import subprocess
import threading
import time

from PIL import Image

log = logging.getLogger("pirate.sources")

DB = "file:/var/local/www/db/moode-sqlite3.db?mode=ro"
WWW = "/var/local/www"
APLMETA = f"{WWW}/aplmeta.json"
SPOTMETA = f"{WWW}/spotmeta.json"

# active flag -> label (first match wins, in this order)
RENDERERS = [("aplactive", "AirPlay"), ("btactive", "Bluetooth"), ("spotactive", "Spotify"),
             ("slactive", "Squeezelite"), ("paactive", "Plexamp"), ("rbactive", "Roon"),
             ("qbzactive", "Qobuz"), ("inpactive", "Input"), ("rxactive", "Multiroom")]
# service flag -> label, for the idle screen's list of available inputs
SERVICES = [("airplaysvc", "AirPlay"), ("btsvc", "Bluetooth"), ("spotifysvc", "Spotify"),
            ("upnpsvc", "UPnP"), ("slsvc", "Squeezelite"), ("pasvc", "Plexamp")]


def _flags():
    con = sqlite3.connect(DB, uri=True, timeout=2)
    try:
        rows = con.execute("select param, value from cfg_system where param like '%active' "
                           "or param like '%svc'").fetchall()
    finally:
        con.close()
    return {k: str(v) for k, v in rows}


def _pick(d, *names):
    """Case-insensitive lookup of the first non-empty key."""
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, "", "Unknown"):
            return str(v)
    return ""


def _cover_path(url):
    """moOde stores cover URLs relative to its web root (e.g. imagesw/airplay-covers/x.jpg)."""
    if not url:
        return None
    url = url.split("?")[0]
    for cand in (url, os.path.join(WWW, url.lstrip("/")), os.path.join("/var/www", url.lstrip("/"))):
        if os.path.isfile(cand):
            return cand
    return None


def _load_art(path, size):
    try:
        return Image.open(path).convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    except Exception as e:
        log.warning("cover art %s: %s", path, e)
        return None


def _busctl_json(*args):
    out = subprocess.run(["busctl", "--system", "--json=short", *args],
                         capture_output=True, text=True, timeout=3)
    if out.returncode:
        return None
    return json.loads(out.stdout)


def bluetooth_info():
    """(device_name, title, artist, album, playing) for the connected phone, or None."""
    try:
        tree = subprocess.run(["busctl", "--system", "--list", "tree", "org.bluez"],
                              capture_output=True, text=True, timeout=3).stdout.split()
    except Exception:
        return None
    players = [p for p in tree if "/player" in p and p.count("/") == 5]
    devices = [p for p in tree if p.count("/") == 4 and "/dev_" in p]
    name = ""
    for dev in devices:
        c = _busctl_json("get-property", "org.bluez", dev, "org.bluez.Device1", "Connected")
        if c and c.get("data"):
            a = _busctl_json("get-property", "org.bluez", dev, "org.bluez.Device1", "Alias")
            name = a["data"] if a else dev.rsplit("dev_", 1)[-1].replace("_", ":")
            break
    if not players:
        return (name, "", "", "", False) if name else None
    p = players[0]
    track = _busctl_json("get-property", "org.bluez", p, "org.bluez.MediaPlayer1", "Track") or {}
    status = _busctl_json("get-property", "org.bluez", p, "org.bluez.MediaPlayer1", "Status") or {}
    t = {k: v.get("data") for k, v in (track.get("data") or {}).items()}
    return (name, t.get("Title", "") or "", t.get("Artist", "") or "", t.get("Album", "") or "",
            status.get("data") == "playing")


class SourceWatcher(threading.Thread):
    daemon = True

    def __init__(self, state, art_size):
        super().__init__(name="sources")
        self.S, self.art_size = state, art_size
        self._meta_mtime = {}
        self._art_key = None
        self._mpd = None

    # -- helpers
    def _json_meta(self, path):
        try:
            m = os.stat(path).st_mtime
        except OSError:
            return None, False
        changed = self._meta_mtime.get(path) != m
        self._meta_mtime[path] = m
        try:
            with open(path) as f:
                return json.load(f), changed
        except (OSError, ValueError):
            return None, False

    def _set_art_from_path(self, path, url=""):
        if path == self._art_key:
            return
        if path is None:
            if url:   # moOde gave a cover URL but the file isn't there (yet) - retry next tick
                if self._art_key != ("missing", url):
                    log.warning("cover not found yet: %s", url)
                    self._art_key = ("missing", url)
                return
            self._art_key = None
            self.S.set(art=None)
            return
        art = _load_art(path, self.art_size)
        if art is not None:       # only remember it once it actually loaded
            self._art_key = path
        self.S.set(art=art)

    def _mpd_info(self):
        import mpd
        if self._mpd is None:
            c = mpd.MPDClient()
            c.timeout = 5
            c.connect("localhost", 6600)
            self._mpd = c
        st = self._mpd.status()
        song = self._mpd.currentsong() or {}
        return st.get("state"), song

    def _mpd_art(self, uri):
        key = "mpd:" + uri
        if key == self._art_key:
            return
        self._art_key = key
        art = None
        for cmd in ("readpicture", "albumart"):
            try:
                r = getattr(self._mpd, cmd)(uri)
                if r and r.get("binary"):
                    art = Image.open(io.BytesIO(r["binary"])).convert("RGB").resize(
                        (self.art_size, self.art_size), Image.Resampling.LANCZOS)
                    break
            except Exception:
                continue
        self.S.set(art=art)

    # -- main loop
    def run(self):
        while True:
            try:
                self.tick()
            except Exception as e:
                log.warning("source poll: %s", e)
                if self._mpd is not None:
                    try:
                        self._mpd.disconnect()
                    except Exception:
                        pass
                    self._mpd = None
            time.sleep(1)

    def tick(self):
        flags = _flags()
        inputs = [lbl for k, lbl in SERVICES if flags.get(k) == "1"]
        active = next((lbl for k, lbl in RENDERERS if flags.get(k) == "1"), None)
        self.S.set(inputs=tuple(inputs))

        if active == "AirPlay":
            meta, _ = self._json_meta(APLMETA)
            meta = meta or {}
            ps = _pick(meta, "playstate").lower()
            self.S.set(source="AirPlay", device="", playing=not ps.startswith(("pause", "stop")),
                       title=_pick(meta, "title"), artist=_pick(meta, "artist"),
                       album=_pick(meta, "album"))
            url = _pick(meta, "cover_url", "coverurl", "cover", "cover_art")
            self._set_art_from_path(_cover_path(url), url)
            return

        if active == "Bluetooth":
            bt = bluetooth_info()
            name, title, artist, album, playing = bt or ("", "", "", "", True)
            self.S.set(source="Bluetooth", device=name, playing=playing or not title,
                       title=title, artist=artist, album=album)
            self._set_art_from_path(None)
            return

        if active == "Spotify":
            meta, _ = self._json_meta(SPOTMETA)
            meta = meta or {}
            self.S.set(source="Spotify", device="", playing=True,
                       title=_pick(meta, "title", "name"), artist=_pick(meta, "artist", "artists"),
                       album=_pick(meta, "album"))
            self._set_art_from_path(_cover_path(_pick(meta, "cover_url", "coverurl", "cover")))
            return

        if active:  # other renderers: label only
            self.S.set(source=active, device="", playing=True, title="", artist="", album="")
            self._set_art_from_path(None)
            return

        state, song = self._mpd_info()
        if state in ("play", "pause"):
            radio = song.get("file", "").startswith("http")
            self.S.set(source="Radio" if radio else "Library", device="", playing=state == "play",
                       title=song.get("title") or song.get("name") or os.path.basename(song.get("file", "")),
                       artist=song.get("artist", "") or (song.get("name", "") if radio else ""),
                       album=song.get("album", ""))
            if not radio and song.get("file"):
                self._mpd_art(song["file"])
            else:
                self._set_art_from_path(None)
        else:
            self.S.set(source=None, device="", playing=False, title="", artist="", album="")
            self._set_art_from_path(None)
