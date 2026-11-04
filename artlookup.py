"""Cover art for sources that don't send any (Bluetooth, radio): look it up by
artist + title in Apple's iTunes Search API and cache it on disk.

- Lookups run in a background thread; the caller gets None until the file exists.
- Hits are cached forever (art_cache/<hash>.jpg), so songs that repeat all season
  (walk-up music) work offline after they've been played once with internet.
- Misses are remembered for an hour so we don't hammer the API.
"""
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from PIL import Image

log = logging.getLogger("pirate.art")

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "art_cache")
MISS_RETRY_S = 3600
SIZE = 600            # iTunes can serve any size; we downscale for the screen anyway
UA = "pirate-audio-display/1.0"

_lock = threading.Lock()
_inflight = set()
_misses = {}          # key -> time of last miss


def _clean(s):
    """Strip things that hurt search matching: (feat. X), [Remastered], - Single, etc."""
    s = re.sub(r"\s*[\(\[][^)\]]*(feat\.|ft\.|remaster|version|edit|live|explicit|clean)[^)\]]*[\)\]]", "", s, flags=re.I)
    s = re.sub(r"\s+-\s+(single|ep|remaster.*)$", "", s, flags=re.I)
    return s.strip()


def _key(artist, title):
    return hashlib.sha1(f"{artist.lower().strip()}|{title.lower().strip()}".encode()).hexdigest()[:20]


def path_for(artist, title):
    return os.path.join(CACHE, _key(artist, title) + ".jpg")


def _save(img_bytes, dest):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    if max(img.size) > SIZE:
        img = img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    os.makedirs(CACHE, exist_ok=True)
    tmp = dest + ".tmp"
    img.save(tmp, "JPEG", quality=90)
    os.replace(tmp, dest)


def _fetch(artist, title, key, dest, url=None):
    try:
        if url:                               # moOde already found it: just download and cache
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=10) as r:
                _save(r.read(), dest)
            log.info("art cached (moOde URL) for %s - %s", artist, title)
            return
        primary = artist.split(",")[0].split("&")[0].strip()
        term = f"{primary} {_clean(title)}"
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
            {"term": term, "media": "music", "entity": "song", "limit": 5})
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            results = json.load(r).get("results", [])
        # prefer a result whose artist matches
        pick = next((x for x in results if primary.lower() in x.get("artistName", "").lower()), None) \
            or (results[0] if results else None)
        if not pick or not pick.get("artworkUrl100"):
            raise LookupError("no match")
        art_url = re.sub(r"/\d+x\d+bb\.", f"/{SIZE}x{SIZE}bb.", pick["artworkUrl100"])
        with urllib.request.urlopen(urllib.request.Request(art_url, headers={"User-Agent": UA}), timeout=10) as r:
            _save(r.read(), dest)
        log.info("art cached (iTunes search) for %s - %s", artist, title)
    except Exception as e:
        log.info("no art for %s - %s (%s)", artist, title, e)
        with _lock:
            _misses[key] = time.time()
    finally:
        with _lock:
            _inflight.discard(key)


def lookup(artist, title, url=None):
    """Path to cached art if we have it; otherwise start a background fetch and return None.
    url: a cover URL someone else already found (e.g. moOde's btmeta.json) - used instead of searching."""
    if not title:
        return None
    key = _key(artist or "", title)
    dest = os.path.join(CACHE, key + ".jpg")
    if os.path.exists(dest):
        return dest
    with _lock:
        if key in _inflight or time.time() - _misses.get(key, 0) < MISS_RETRY_S:
            return None
        _inflight.add(key)
    threading.Thread(target=_fetch, args=(artist or "", title, key, dest, url), daemon=True).start()
    return None
