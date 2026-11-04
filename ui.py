"""Ambient Glass UI for the 320x240 screen.

Pure drawing: render(m) turns a snapshot of the app state (a dict, see
display.py's snapshot()) into a 320x240 RGB image. No I/O here, so every screen
can be previewed off the Pi (see preview.py).

Design rules (keep them, they're what makes it feel like one product):
  - Background: blurred album art while something is playing, otherwise the one
    brand backdrop (dark slate, single blue glow).
  - The same status bar on every screen.
  - One glass-card style for every panel; popups are frosted cards over
    whatever screen is up.
  - Accent colours mean one thing each: blue = Bluetooth/pairing,
    green = battery OK/charging, amber/red = warnings and "disconnect".

Everything is drawn 2x supersampled and scaled down for smooth edges.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, SS = 320, 240, 2
ART = 168                       # album art size on screen (logical px)
ART_PX = ART * SS               # size sources.py should deliver art at

# ---------------------------------------------------------------- tokens
WHITE = (255, 255, 255)
T1, T2, T3 = (255, 255, 255, 240), (255, 255, 255, 165), (255, 255, 255, 110)
GLASS_FILL, GLASS_LINE = (255, 255, 255, 26), (255, 255, 255, 34)
SEL_FILL = (255, 255, 255, 64)
R_CARD = 14
BRAND_BASE, BRAND_GLOW = (14, 16, 22), (70, 110, 255)
BLUE, GREEN, AMBER, RED = (10, 132, 255), (52, 211, 120), (255, 184, 0), (255, 69, 58)
GREY = (120, 124, 135)

# ---------------------------------------------------------------- fonts
_FONT_DIRS = ["/usr/share/fonts/opentype/inter/", "/usr/share/fonts/truetype/inter/"]
_DEJAVU = {"Bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
           "Regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"}
_font_cache = {}


def F(weight, size, display=False):
    """Inter (or Inter Display for big text); falls back to DejaVu if fonts-inter is missing."""
    key = (weight, size, display)
    if key not in _font_cache:
        names = ([f"InterDisplay-{weight}.otf"] if display else []) + [f"Inter-{weight}.otf"]
        f = None
        for d in _FONT_DIRS:
            for n in names:
                if os.path.exists(d + n):
                    f = ImageFont.truetype(d + n, int(size * SS))
                    break
            if f:
                break
        if f is None:
            fb = _DEJAVU["Bold" if weight in ("Bold", "SemiBold", "Black", "ExtraBold") else "Regular"]
            f = ImageFont.truetype(fb, int(size * SS * 0.92))
        _font_cache[key] = f
    return _font_cache[key]


def s(*v):
    return tuple(int(round(x * SS)) for x in v)


# ---------------------------------------------------------------- text helpers
def fit(d, t, f, w):
    t = t or ""
    if d.textlength(t, font=f) <= w * SS:
        return t
    while t and d.textlength(t + "…", font=f) > w * SS:
        t = t[:-1]
    return t.rstrip() + "…"


def wrap(d, t, f, w, n):
    words, lines, cur = (t or "").split(), [], ""
    for wd in words:
        c = (cur + " " + wd).strip()
        if d.textlength(c, font=f) <= w * SS or not cur:
            cur = c
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    if len(lines) > n:
        lines = lines[:n]
        lines[-1] = fit(d, lines[-1] + " …", f, w)
    return [fit(d, ln, f, w) for ln in lines]


# ---------------------------------------------------------------- shapes & glyphs
def glass(d, box, r=R_CARD, fill=GLASS_FILL, line=GLASS_LINE):
    d.rounded_rectangle(s(*box), r * SS, fill=fill, outline=line, width=SS)


def ring(d, center, r, width, frac, color, track=(255, 255, 255, 40), start=-90):
    cx, cy = center
    box = s(cx - r, cy - r, cx + r, cy + r)
    d.arc(box, 0, 360, fill=track, width=int(width * SS))
    frac = max(0.0, min(1.0, frac))
    if frac > 0:
        d.arc(box, start, start + 360 * frac, fill=color, width=int(width * SS))
        for ang in (start, start + 360 * frac):                 # round caps
            a = math.radians(ang)
            rr = r - width / 2
            px, py = cx + rr * math.cos(a), cy + rr * math.sin(a)
            d.ellipse(s(px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=color)


def batt_icon(d, x, y, frac, color, w=24, h=11, outline=(255, 255, 255, 200)):
    d.rounded_rectangle(s(x, y, x + w, y + h), 3 * SS, outline=outline, width=int(1.3 * SS))
    d.rounded_rectangle(s(x + w + 1, y + 3.5, x + w + 3, y + h - 3.5), 1 * SS, fill=outline)
    frac = max(0.0, min(1.0, frac))
    if frac > 0.02:
        d.rounded_rectangle(s(x + 2, y + 2, x + 2 + (w - 4) * frac, y + h - 2), 2 * SS, fill=color)


def bt_glyph(d, cx, cy, size, color, width=3):
    h, q = size / 2, size / 4
    pts = [(cx - q, cy - q), (cx + q, cy + q), (cx, cy + h), (cx, cy - h), (cx + q, cy - q), (cx - q, cy + q)]
    d.line([s(*p) for p in pts], fill=color, width=int(width * SS), joint="curve")


def airplay_glyph(d, cx, cy, size, color):
    d.rounded_rectangle(s(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 4), 2 * SS,
                        outline=color, width=int(1.4 * SS))
    d.polygon([s(cx - size / 3, cy + size / 2), s(cx + size / 3, cy + size / 2), s(cx, cy + size / 8)], fill=color)


def speaker_glyph(d, cx, cy, sz, c):
    d.polygon([s(cx - sz * .5, cy - sz * .2), s(cx - sz * .25, cy - sz * .2), s(cx + sz * .05, cy - sz * .45),
               s(cx + sz * .05, cy + sz * .45), s(cx - sz * .25, cy + sz * .2), s(cx - sz * .5, cy + sz * .2)], fill=c)
    for r in (.28, .5):
        d.arc(s(cx + sz * .05 - sz * r, cy - sz * r, cx + sz * .05 + sz * r, cy + sz * r), -45, 45,
              fill=c, width=int(1.6 * SS))


def bolt(d, cx, cy, sz, c):
    k = sz / 2
    d.polygon([s(cx + .15 * k, cy - k), s(cx - .55 * k, cy + .1 * k), s(cx - .05 * k, cy + .1 * k),
               s(cx - .15 * k, cy + k), s(cx + .55 * k, cy - .1 * k), s(cx + .05 * k, cy - .1 * k)], fill=c)


def pause_glyph(d, cx, cy, sz, c):
    w = sz * .28
    d.rounded_rectangle(s(cx - sz * .4, cy - sz / 2, cx - sz * .4 + w, cy + sz / 2), 1.5 * SS, fill=c)
    d.rounded_rectangle(s(cx + sz * .4 - w, cy - sz / 2, cx + sz * .4, cy + sz / 2), 1.5 * SS, fill=c)


def next_glyph(d, cx, cy, sz, c):
    h = sz / 2
    d.polygon([s(cx - h, cy - h * .8), s(cx, cy), s(cx - h, cy + h * .8)], fill=c)
    d.polygon([s(cx - h * .2, cy - h * .8), s(cx + h * .8, cy), s(cx - h * .2, cy + h * .8)], fill=c)
    d.rectangle(s(cx + h * .8, cy - h * .8, cx + h, cy + h * .8), fill=c)


def eq_glyph(d, x, ybase, c, scale=1.0):
    for k, hgt in enumerate((6, 11, 4, 9)):
        d.rounded_rectangle(s(x + k * 5 * scale, ybase - hgt * scale, x + (k * 5 + 3) * scale, ybase),
                            1.5 * SS, fill=c)


def chip_glyph(d, cx, cy, sz, c):
    r = sz / 2
    d.rounded_rectangle(s(cx - r * .6, cy - r * .6, cx + r * .6, cy + r * .6), 2 * SS, outline=c, width=int(1.6 * SS))
    for k in (-0.3, 0.0, 0.3):
        for a, b in ((-r, -r * .6), (r * .6, r)):
            d.line(s(cx + k * r, cy + a, cx + k * r, cy + b), fill=c, width=int(1.4 * SS))
            d.line(s(cx + a, cy + k * r, cx + b, cy + k * r), fill=c, width=int(1.4 * SS))


def plus_glyph(d, cx, cy, sz, c, w=3):
    r = sz / 2
    d.line(s(cx - r, cy, cx + r, cy), fill=c, width=int(w * SS))
    d.line(s(cx, cy - r, cx, cy + r), fill=c, width=int(w * SS))


def power_glyph(d, cx, cy, sz, c, w=3):
    r = sz / 2
    d.arc(s(cx - r, cy - r, cx + r, cy + r), -55, 235, fill=c, width=int(w * SS))
    d.line(s(cx, cy - r - 1, cx, cy), fill=c, width=int(w * SS))


def wifi_bars(d, x, y, n, c):
    for k in range(4):
        hgt = 4 + k * 3
        d.rounded_rectangle(s(x + k * 5, y - hgt, x + k * 5 + 3, y), 1 * SS, fill=c if k < n else (255, 255, 255, 50))


def icon(d, kind, cx, cy, sz, c):
    """Named glyphs used by popups and menu tiles."""
    if kind == "bt":
        bt_glyph(d, cx, cy, sz, c, max(2, sz / 9))
    elif kind == "bt_off":
        bt_glyph(d, cx, cy, sz, c, max(2, sz / 9))
        d.line(s(cx - sz * .5, cy + sz * .5, cx + sz * .5, cy - sz * .5), fill=c, width=int(max(2, sz / 9) * SS))
    elif kind == "airplay":
        airplay_glyph(d, cx, cy, sz, c)
    elif kind == "plus":
        plus_glyph(d, cx, cy, sz * .8, c)
    elif kind == "music":
        eq_glyph(d, cx - 9 * sz / 22, cy + 6 * sz / 22, c, sz / 22)
    elif kind == "battery":
        batt_icon(d, cx - sz * .5, cy - sz * .23, 0.7, c, w=sz, h=sz * .46, outline=c)
    elif kind == "system":
        chip_glyph(d, cx, cy, sz, c)
    elif kind == "help":
        d.text(s(cx, cy), "?", font=F("Bold", sz * 1.05, True), fill=c, anchor="mm")
    elif kind == "next":
        next_glyph(d, cx, cy, sz * .8, c)
    elif kind == "bolt":
        bolt(d, cx, cy, sz, c)
    elif kind == "warn":
        d.text(s(cx, cy), "!", font=F("Bold", sz * 1.1, True), fill=c, anchor="mm")
    elif kind == "power":
        power_glyph(d, cx, cy, sz * .8, c)
    elif kind == "speaker":
        speaker_glyph(d, cx, cy, sz, c)
    elif kind == "pause":
        pause_glyph(d, cx, cy, sz * .8, c)


# ---------------------------------------------------------------- backgrounds (cached)
_cache = {}


def _cached(key, make, owner=None):
    """Memoise expensive layers. `owner` (the art image) is kept alive alongside the
    result so its id() can't be reused by a different image while cached."""
    hit = _cache.get(key)
    if hit is None or hit[0] is not owner:
        if len(_cache) > 12:
            _cache.clear()
        hit = _cache[key] = (owner, make())
    return hit[1]


def brand_bg():
    def make():
        im = Image.new("RGB", (W * SS, H * SS), BRAND_BASE)
        g = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse(s(-90, -120, 230, 150), fill=BRAND_GLOW + (95,))
        g = g.filter(ImageFilter.GaussianBlur(60 * SS))
        im.paste(g, (0, 0), g)
        return im
    return _cached("brand", make).copy()


def art_bg(art):
    def make():
        small = art.resize((32, 24), Image.Resampling.BILINEAR)
        bg = small.resize((W * SS, H * SS), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(14 * SS))
        bg = Image.blend(bg, Image.new("RGB", bg.size), 0.5)
        v = Image.new("L", bg.size, 0)
        ImageDraw.Draw(v).ellipse(s(-80, -60, W + 80, H + 60), fill=255)
        v = v.filter(ImageFilter.GaussianBlur(40 * SS))
        return Image.composite(bg, Image.blend(bg, Image.new("RGB", bg.size), 0.6), v)
    return _cached(("bg", id(art)), make, art).copy()


def _shadow(size, box, radius, blur=10, alpha=170, offset=(0, 6)):
    sh = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(s(box[0] + offset[0], box[1] + offset[1], box[2] + offset[0],
                                           box[3] + offset[1]), radius * SS, fill=(0, 0, 0, alpha))
    return sh.filter(ImageFilter.GaussianBlur(blur * SS))


def art_layer(art, dimmed):
    """Rounded cover + drop shadow on a transparent full-screen layer."""
    def make():
        lay = _shadow((W * SS, H * SS), (14, 34, 14 + ART, 34 + ART), 14)
        a = art.resize((ART * SS, ART * SS), Image.Resampling.LANCZOS)
        if dimmed:
            a = Image.blend(a, Image.new("RGB", a.size), 0.35)
        mask = Image.new("L", a.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, a.size[0] - 1, a.size[1] - 1), 14 * SS, fill=255)
        lay.paste(a, s(14, 34), mask)
        return lay
    return _cached(("art", id(art), dimmed), make, art)


def dim(im, a=0.4):
    return Image.blend(im, Image.new("RGB", im.size), a)


def frosted(im, box, r=18, tint=(18, 20, 28, 165)):
    """Frosted glass: blur what's behind the card, then tint it."""
    x0, y0, x1, y1 = s(*box)
    region = im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(10 * SS))
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, region.size[0] - 1, region.size[1] - 1), r * SS, fill=255)
    im.paste(region, (x0, y0), mask)
    d = ImageDraw.Draw(im, "RGBA")
    d.rounded_rectangle((x0, y0, x1, y1), r * SS, fill=tint, outline=GLASS_LINE, width=SS)
    return d


# ---------------------------------------------------------------- formatting
def batt_col(soc):
    return GREEN if soc > 0.2 else AMBER if soc > 0.1 else RED


def fmt_dur(hours):
    if hours is None:
        return None
    if hours > 99:
        return "99+ h"
    m = int(round(hours * 60))
    return f"{m // 60} h {m % 60:02d} m" if m >= 60 else f"{m} m"


def fmt_uptime(sec):
    m = int(sec // 60)
    if m < 60:
        return f"{m} m"
    h = m // 60
    return f"{h} h {m % 60} m" if h < 48 else f"{h // 24} days"


# ---------------------------------------------------------------- shared pieces
STATUS_H = 30          # status bar height (also the touch target for the menu)


def status(d, m):
    src, dev = m.get("source"), m.get("device")
    if src:
        label = src + (f" · {dev}" if dev else "")
        if src == "AirPlay":
            airplay_glyph(d, 18, 16, 10, T1)
            x = 29
        elif src == "Bluetooth":
            bt_glyph(d, 18, 16, 10, T1, 2)
            x = 29
        else:
            x = 14
    else:
        label, x = m.get("bt_name") or "Speaker", 14
    d.text(s(x, 16), fit(d, label, F("SemiBold", 11), 228 - x), font=F("SemiBold", 11), fill=T2, anchor="lm")
    b = m.get("batt")
    if b:
        soc = b["soc"]
        low = soc <= 0.2
        col = batt_col(soc) if low else T1
        batt_icon(d, 280, 10.5, soc, col)
        if b.get("charging"):
            bolt(d, 292, 16, 9, (20, 20, 24) if soc > 0.5 else WHITE)
        d.text(s(274, 16), f"{round(soc * 100)}%", font=F("SemiBold", 11), fill=col, anchor="rm")


def _art_placeholder(d, m):
    glass(d, (14, 34, 14 + ART, 34 + ART), 14)
    cx, cy = 14 + ART / 2, 34 + ART / 2
    if m.get("source") == "AirPlay":
        airplay_glyph(d, cx, cy, 44, T2)
    elif m.get("source") == "Bluetooth":
        bt_glyph(d, cx, cy, 52, T2, 5)
    else:
        eq_glyph(d, cx - 20, cy + 14, T2, 2.6)


# ---------------------------------------------------------------- screens
def scr_now(m):
    art, paused = m.get("art"), not m.get("playing")
    if art is not None:
        im = art_bg(art)
        im.paste(art_layer(art, paused), (0, 0), art_layer(art, paused))
        d = ImageDraw.Draw(im, "RGBA")
    else:
        im = brand_bg()
        d = ImageDraw.Draw(im, "RGBA")
        _art_placeholder(d, m)
    status(d, m)
    x, cw = 194, 114
    title = m.get("title") or (m.get("device") or m.get("source") or "")
    sub = m.get("artist") if m.get("title") else ("Connected" if m.get("source") == "Bluetooth" else "Playing")
    y = 46
    tf = F("Bold", 21, True)
    lines = wrap(d, title, tf, cw, 3)
    if len(lines) == 3 or any(d.textlength(ln, font=tf) > cw * SS for ln in lines):
        tf = F("Bold", 18, True)
        lines = wrap(d, title, tf, cw, 3)
    for ln in lines:
        d.text(s(x, y), ln, font=tf, fill=WHITE)
        y += 25 if tf.size >= 21 * SS else 22
    for ln in wrap(d, sub, F("Medium", 13), cw, 2):
        d.text(s(x, y + 4), ln, font=F("Medium", 13), fill=T2)
        y += 17
    if m.get("album"):
        d.text(s(x, 192), fit(d, m["album"], F("Regular", 11), cw), font=F("Regular", 11), fill=T3)
    if paused and m.get("title"):
        cx, cy = 14 + ART / 2, 34 + ART / 2
        d.ellipse(s(cx - 22, cy - 22, cx + 22, cy + 22), fill=(0, 0, 0, 120))
        pause_glyph(d, cx, cy, 16, WHITE)
        pause_glyph(d, 18, 223, 9, T2)
        d.text(s(30, 223), "Paused", font=F("SemiBold", 10), fill=T2, anchor="lm")
    elif not m.get("_overlay_bottom"):
        eq_glyph(d, 14, 228, T2)
    return im


def scr_idle(m):
    im = brand_bg()
    d = ImageDraw.Draw(im, "RGBA")
    status(d, m)
    name = m.get("bt_name") or "Speaker"
    inputs = m.get("inputs") or ()
    bt_on = "Bluetooth" in inputs or not inputs
    if bt_on:
        cx, cy = 252, 92
        pulse = 0.5 + 0.5 * math.sin(m.get("t", 0) * 2.2)
        if m.get("bt_visible", True):
            for r, a in ((46, 18 + 20 * pulse), (36, 40 + 25 * pulse)):
                d.ellipse(s(cx - r, cy - r, cx + r, cy + r), outline=BLUE + (int(a + 30),), width=int(1.5 * SS))
        d.ellipse(s(cx - 26, cy - 26, cx + 26, cy + 26), fill=BLUE if m.get("bt_visible", True) else GREY)
        bt_glyph(d, cx, cy, 24, WHITE, 3)
        d.text(s(18, 50), "READY TO CONNECT", font=F("SemiBold", 10), fill=BLUE + (255,))
        nf = F("Bold", 28, True)
        if d.textlength(name, font=nf) > 190 * SS:
            nf = F("Bold", 20, True)
        d.text(s(18, 64), fit(d, name, nf, 196), font=nf, fill=WHITE)
        d.text(s(18, 102), "Open Bluetooth on your phone", font=F("Medium", 12), fill=T2)
        d.text(s(18, 118), "and choose this name", font=F("Medium", 12), fill=T2)
    else:
        d.text(s(18, 64), "Ready", font=F("Bold", 34, True), fill=WHITE)
    if "AirPlay" in inputs:
        glass(d, (12, 170, 308, 226), 12)
        airplay_glyph(d, 34, 198, 14, T1)
        d.text(s(52, 190), "Or use AirPlay" if bt_on else "Play with AirPlay", font=F("SemiBold", 12), fill=T1, anchor="lm")
        ap = m.get("airplay_name") or name
        d.text(s(52, 207), fit(d, f"Control Center → Music → {ap}", F("Medium", 10), 246),
               font=F("Medium", 10), fill=T2, anchor="lm")
    return im


def _tiles(d, x0, y0, tiles, cols=2, tw=70, th=95, gap=6):
    for k, (lab, val) in enumerate(tiles):
        x = x0 + (k % cols) * (tw + gap)
        y = y0 + (k // cols) * (th + 4)
        glass(d, (x, y, x + tw, y + th))
        d.text(s(x + 11, y + 16), lab, font=F("Medium", 10), fill=T2)
        vf = F("Bold", 16, True)
        if d.textlength(val, font=vf) > (tw - 16) * SS:
            vf = F("Bold", 13, True)
        d.text(s(x + 11, y + 70), val, font=vf, fill=WHITE)


def scr_battery(m):
    b = m.get("batt")
    im = art_bg(m["art"]) if (m.get("art") is not None and m.get("playing")) else brand_bg()
    d = ImageDraw.Draw(im, "RGBA")
    status(d, m)
    if not b:
        glass(d, (12, 32, 308, 226))
        d.text(s(160, 120), "No battery monitor", font=F("SemiBold", 14), fill=T2, anchor="mm")
        return im
    soc, charging = b["soc"], b.get("charging")
    glass(d, (12, 32, 152, 226))
    col = GREEN if charging else batt_col(soc)
    if charging:
        pulse = 0.5 + 0.5 * math.sin(m.get("t", 0) * 2.5)
        d.arc(s(82 - 64, 118 - 64, 82 + 64, 118 + 64), 0, 360, fill=GREEN + (int(25 + 45 * pulse),), width=3 * SS)
    ring(d, (82, 118), 56, 10, soc, col, track=(255, 255, 255, 38))
    pct = f"{round(soc * 100)}"
    if charging:
        bolt(d, 82, 92, 16, GREEN)
        d.text(s(82, 120), pct, font=F("Bold", 34, True), fill=WHITE, anchor="mm")
        d.text(s(82, 145), "charging", font=F("Medium", 10), fill=T2, anchor="mm")
        full = fmt_dur(b.get("full_hrs"))
        foot = f"Full in ~{full}" if full else ("Full" if soc >= 0.995 else "Charging")
    else:
        d.text(s(82, 112), pct, font=F("Bold", 38, True), fill=WHITE, anchor="mm")
        d.text(s(82, 138), "percent" if b.get("synced") else "estimate", font=F("Medium", 10), fill=T2, anchor="mm")
        left = fmt_dur(b.get("hrs"))
        foot = f"{left} left" if left else "Measuring…"
    d.text(s(82, 206), foot, font=F("SemiBold", 12), fill=T1, anchor="mm")
    v, i = b["v"], b["i"]
    if charging:
        tiles = [("Input", f"{-v * i:.0f} W"), ("Current", f"{-i:.2f} A"),
                 ("Voltage", f"{v:.1f} V"), ("Gauge", "Synced" if b.get("synced") else "Estimate")]
    else:
        tiles = [("Voltage", f"{v:.1f} V"), ("Current", f"{max(i, 0):.2f} A"),
                 ("Power", f"{max(v * i, 0):.0f} W"), ("Peak", f"{b.get('peak', 0):.1f} A")]
    _tiles(d, 160, 32, tiles)
    return im


def scr_system(m):
    sy = m.get("sys") or {}
    im = brand_bg()
    d = ImageDraw.Draw(im, "RGBA")
    status(d, m)
    temp = sy.get("temp")
    uv_now, uv_seen = sy.get("uv_now"), sy.get("uv_seen")
    hot = temp is not None and temp >= 75
    if uv_now:
        dot, msg = RED, "Low supply voltage"
    elif hot:
        dot, msg = AMBER, "Running hot"
    elif uv_seen:
        dot, msg = AMBER, "Voltage dipped earlier"
    else:
        dot, msg = GREEN, "All good"
    d.text(s(14, 44), "System", font=F("Bold", 15, True), fill=WHITE, anchor="lm")
    tw = d.textlength(msg, font=F("SemiBold", 10)) / SS
    d.ellipse(s(306 - tw - 14, 40, 306 - tw - 6, 48), fill=dot)
    d.text(s(306, 44), msg, font=F("SemiBold", 10), fill=T2, anchor="rm")

    rssi = sy.get("rssi")
    bars = 0 if rssi is None else 4 if rssi > -55 else 3 if rssi > -67 else 2 if rssi > -75 else 1
    tiles = [
        ("CPU temp", f"{temp:.0f}°C" if temp is not None else "--", ("bar", (temp or 0) / 85.0)),
        ("Power supply", "Low!" if uv_now else "OK", ("note", "dipped since boot" if uv_seen else "no under-voltage")),
        ("Wi-Fi", sy.get("ssid") or ("Hotspot" if sy.get("hotspot") else "Off"),
         ("wifi", bars, f"{rssi} dBm" if rssi is not None else "")),
        ("Address", sy.get("ip") or "--", ("note", sy.get("host", ""))),
        ("Uptime", fmt_uptime(sy.get("uptime", 0)), ("note", "since power on")),
        ("Bluetooth", "Visible" if m.get("bt_visible") else "Hidden",
         ("note", "phones can pair" if m.get("bt_visible") else "phone connected")),
    ]
    for k, (lab, val, extra) in enumerate(tiles):
        x = 12 + (k % 3) * 100
        y = 60 + (k // 3) * 84
        glass(d, (x, y, x + 96, y + 78), 12)
        d.text(s(x + 10, y + 16), lab, font=F("Medium", 10), fill=T2, anchor="lm")
        vf = F("Bold", 15, True)
        if d.textlength(val, font=vf) > 78 * SS:
            vf = F("Bold", 12, True)
        warn = (lab == "CPU temp" and hot) or (lab == "Power supply" and uv_now)
        d.text(s(x + 10, y + 44), fit(d, val, vf, 80), font=vf, fill=(AMBER if warn else WHITE), anchor="lm")
        if extra[0] == "bar":
            d.rounded_rectangle(s(x + 10, y + 63, x + 86, y + 67), 2 * SS, fill=(255, 255, 255, 40))
            frac = max(0.0, min(1.0, extra[1]))
            d.rounded_rectangle(s(x + 10, y + 63, x + 10 + 76 * frac, y + 67), 2 * SS,
                                fill=AMBER if hot else GREEN)
        elif extra[0] == "wifi":
            wifi_bars(d, x + 10, y + 68, extra[1], WHITE)
            d.text(s(x + 34, y + 64), extra[2], font=F("Medium", 9), fill=T3, anchor="lm")
        else:
            d.text(s(x + 10, y + 65), fit(d, extra[1], F("Medium", 9), 80), font=F("Medium", 9), fill=T3, anchor="lm")
    return im


def scr_splash(m):
    im = brand_bg()
    d = ImageDraw.Draw(im, "RGBA")
    t = m.get("t", 0)
    kind = m.get("splash_kind", "boot")
    if kind == "boot":
        ring(d, (160, 100), 30, 3, 0.28, WHITE, track=(255, 255, 255, 30), start=(t * 220) % 360)
        speaker_glyph(d, 158, 100, 24, WHITE)
    else:
        ring(d, (160, 100), 30, 3, 1.0, WHITE, track=(255, 255, 255, 30))
        power_glyph(d, 160, 100, 22, WHITE)
    d.text(s(160, 156), m.get("splash_title") or m.get("bt_name") or "Speaker", font=F("Bold", 20, True),
           fill=WHITE, anchor="mm")
    d.text(s(160, 180), m.get("splash_sub", "Starting up…"), font=F("Medium", 11), fill=T2, anchor="mm")
    return im


# ---------------------------------------------------------------- overlays
POPUP_BOX = (34, 66, 286, 174)


def ov_popup(im, p):
    im = dim(im, 0.35)
    d = frosted(im, POPUP_BOX)
    col = p.get("color", BLUE)
    d.ellipse(s(52, 92, 108, 148), fill=tuple(col[:3]) + (p.get("icon_alpha", 255),))
    icon(d, p.get("icon", "bt"), 80, 120, 26, WHITE)
    small = p.get("small", "")
    has3 = bool(p.get("note"))
    y1, y2 = (100, 126) if has3 else (106, 132)
    d.text(s(122, y1), fit(d, small, F("Medium", 12), 152), font=F("Medium", 12), fill=T2, anchor="lm")
    big = p.get("big", "")
    bf = F("Bold", 24, True)
    if d.textlength(big, font=bf) > 152 * SS:
        bf = F("Bold", 18, True)
    d.text(s(122, y2), fit(d, big, bf, 152), font=bf, fill=WHITE, anchor="lm")
    if has3:
        d.text(s(122, 150), fit(d, p["note"], F("Medium", 10), 152), font=F("Medium", 10), fill=T3, anchor="lm")
    return im


def ov_volume(im, vol):
    level, cap = vol
    d = frosted(im, (40, 176, 280, 222), r=23)
    speaker_glyph(d, 64, 199, 18, WHITE)
    x0, x1 = 86, 234
    d.rounded_rectangle(s(x0, 195, x1, 203), 4 * SS, fill=(255, 255, 255, 45))
    if level > 0:
        d.rounded_rectangle(s(x0, 195, x0 + (x1 - x0) * level / 100, 203), 4 * SS, fill=WHITE)
    if cap and cap < 100:
        xc = x0 + (x1 - x0) * cap / 100
        d.line(s(xc, 191, xc, 207), fill=(255, 255, 255, 140), width=int(1.5 * SS))
    d.text(s(262, 199), f"{level}", font=F("Bold", 14, True), fill=WHITE, anchor="mm")
    return im


def ov_pairing(im, left, name, other):
    d = frosted(im, (12, 172, 308, 226), r=14)
    d.rounded_rectangle(s(12, 172, 308, 226), 14 * SS, outline=BLUE + (200,), width=int(1.5 * SS))
    ring(d, (38, 199), 13, 3, left[1], BLUE, track=(255, 255, 255, 40))
    d.text(s(38, 199), f"{left[0] // 60}:{left[0] % 60:02d}", font=F("SemiBold", 7), fill=T1, anchor="mm")
    d.text(s(62, 190), "Another phone can pair", font=F("SemiBold", 12), fill=WHITE, anchor="lm")
    sub = f"Choose “{name}”" + (f" · {other} stays on" if other else "")
    d.text(s(62, 208), fit(d, sub, F("Medium", 10), 236), font=F("Medium", 10), fill=T2, anchor="lm")
    return im


# Menu: 3 x 2 grid of big tiles (finger-sized on a 2" screen); same geometry for touch hit-testing
MENU_GRID = dict(x0=14, y0=46, cols=3, rows=2, tw=92, th=86, gap=8)
MENU_CLOSE = (262, 8, 314, 40)


def menu_tile_box(k):
    g = MENU_GRID
    x = g["x0"] + (k % g["cols"]) * (g["tw"] + g["gap"])
    y = g["y0"] + (k // g["cols"]) * (g["th"] + g["gap"])
    return (x, y, x + g["tw"], y + g["th"])


def menu_hit(x, y):
    """Touch hit-test: tile index, 'close', or None."""
    if MENU_CLOSE[0] <= x <= MENU_CLOSE[2] and MENU_CLOSE[1] <= y <= MENU_CLOSE[3]:
        return "close"
    for k in range(MENU_GRID["cols"] * MENU_GRID["rows"]):
        x0, y0, x1, y1 = menu_tile_box(k)
        if x0 - 3 <= x <= x1 + 3 and y0 - 3 <= y <= y1 + 3:
            return k
    return None


def ov_menu(im, menu):
    im = dim(im, 0.45)
    d = frosted(im, (6, 6, 314, 234), r=18)
    d.text(s(20, 24), menu.get("title", "Menu"), font=F("Bold", 15, True), fill=WHITE, anchor="lm")
    cx, cy = 290, 24
    d.ellipse(s(cx - 11, cy - 11, cx + 11, cy + 11), fill=(255, 255, 255, 30))
    d.line(s(cx - 4, cy - 4, cx + 4, cy + 4), fill=T1, width=int(1.6 * SS))
    d.line(s(cx - 4, cy + 4, cx + 4, cy - 4), fill=T1, width=int(1.6 * SS))
    for k, it in enumerate(menu["items"]):
        x0, y0, x1, y1 = menu_tile_box(k)
        sel = k == menu.get("sel")
        enabled = it.get("enabled", True)
        glass(d, (x0, y0, x1, y1), 12, fill=SEL_FILL if sel else GLASS_FILL,
              line=(255, 255, 255, 230) if sel else GLASS_LINE)
        col = it.get("color", (255, 255, 255))
        if enabled:
            d.ellipse(s(x0 + 10, y0 + 10, x0 + 40, y0 + 40), fill=tuple(col[:3]) + (255 if it.get("color") else 40,))
        icon(d, it["icon"], x0 + 25, y0 + 25, 16, WHITE if enabled else T3)
        label = it["confirm"] if it.get("confirming") else it["label"]
        lf = F("SemiBold", 11)
        lines = wrap(d, label, lf, x1 - x0 - 18, 2)
        ly = y1 - 12 - 15 * (len(lines) - 1)
        for ln in lines:
            fill = (RED + (255,)) if it.get("confirming") else (T1 if enabled else T3)
            d.text(s(x0 + 10, ly), ln, font=lf, fill=fill, anchor="lm")
            ly += 15
    return im


def ov_help(im, touch):
    im = dim(im, 0.5)
    d = frosted(im, (6, 6, 314, 234), r=18)
    d.text(s(20, 24), "Controls", font=F("Bold", 15, True), fill=WHITE, anchor="lm")
    d.text(s(300, 24), "tap or press knob to close" if touch else "press knob to close", font=F("Medium", 9), fill=T3, anchor="rm")
    knob = [("Turn", "Volume"), ("Press", "Play / pause"), ("Press twice", "Next track"), ("Hold", "Menu")]
    screen = [("Tap", "Play / pause"), ("Swipe", "Change screen"), ("Tap top bar", "Menu"),
              ("Tap a popup", "Dismiss")]
    cols = [("Knob", knob)] + ([("Screen", screen)] if touch else [])
    cw = 292 / len(cols)
    for c, (head, rows) in enumerate(cols):
        x0 = 14 + c * cw
        glass(d, (x0, 44, x0 + cw - 8, 226), 12)
        d.text(s(x0 + 12, 60), head.upper(), font=F("SemiBold", 9), fill=T3, anchor="lm")
        for r, (a, b) in enumerate(rows):
            y = 84 + r * 36
            d.text(s(x0 + 12, y), a, font=F("Medium", 10), fill=T2, anchor="lm")
            d.text(s(x0 + 12, y + 15), b, font=F("SemiBold", 12), fill=T1, anchor="lm")
    return im


# ---------------------------------------------------------------- entry point
def render(m):
    """m: snapshot dict from display.py. Returns a 320x240 RGB image."""
    if m.get("splash_kind"):
        im = scr_splash(m)
    else:
        view = m.get("view", "now")
        pairing = m.get("pairing_left")
        vol = m.get("volume")
        m = dict(m, _overlay_bottom=bool(pairing or vol))
        if view == "battery":
            im = scr_battery(m)
        elif view == "system":
            im = scr_system(m)
        elif m.get("source"):
            im = scr_now(m)
        else:
            im = scr_idle(m)
        if pairing and m.get("source"):
            im = ov_pairing(im, pairing, m.get("bt_name") or "Speaker", m.get("device"))
        if vol and not (m.get("menu") or m.get("help")):
            im = ov_volume(im, vol)
        if m.get("popup"):
            im = ov_popup(im, m["popup"])
        if m.get("menu"):
            im = ov_menu(im, m["menu"])
        if m.get("help"):
            im = ov_help(im, m.get("touch"))
    return im.resize((W, H), Image.Resampling.LANCZOS)
