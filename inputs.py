"""Input layer. Everything the user does becomes one of a few events:

    ("turn", +1 | -1)        knob detent
    ("press",)               knob single press
    ("double",)              knob double press
    ("hold",)                knob held HOLD_S
    ("tap", x, y)            touch tap, logical 320x240 coords
    ("swipe", "left"|"right"|"up"|"down")
    ("touch_hold", x, y)     finger held still

Sources: a rotary encoder with push switch (gpiozero), the CST816D touch
controller on the Waveshare 2" touch module (cst816.py), and a bench control
pipe so every feature can be tested with no hardware at all:

    echo press > ctl          echo turn +1 > ctl       echo hold > ctl
    echo left > ctl           echo tap 100 120 > ctl   echo double > ctl
"""
import logging
import os
import queue
import threading
import time

log = logging.getLogger("pirate.input")


class Inputs:
    def __init__(self, handler):
        self.q = queue.Queue()
        self.handler = handler
        self._keep = []          # gpiozero objects must stay referenced
        threading.Thread(target=self._dispatch, name="input", daemon=True).start()

    def emit(self, *ev):
        self.q.put(ev)

    def _dispatch(self):
        while True:
            ev = self.q.get()
            log.info("input: %s", " ".join(str(x) for x in ev))
            try:
                self.handler(ev)
            except Exception:
                log.exception("handling %s", ev)

    # ---------------------------------------------------------------- knob
    def add_encoder(self, a, b, sw, factory=None, reverse=False, double_s=0.35, hold_s=1.0):
        try:
            from gpiozero import Button, RotaryEncoder
        except ImportError:
            log.warning("gpiozero missing; knob disabled")
            return
        if a is not None and b is not None:
            try:
                enc = RotaryEncoder(a, b, max_steps=0, pin_factory=factory)
                cw, ccw = (-1, 1) if reverse else (1, -1)
                enc.when_rotated_clockwise = lambda: self.emit("turn", cw)
                enc.when_rotated_counter_clockwise = lambda: self.emit("turn", ccw)
                self._keep.append(enc)
            except Exception as e:
                log.warning("encoder on GPIO %s/%s disabled: %s", a, b, e)
        if sw is None:
            return
        try:
            btn = Button(sw, pull_up=True, bounce_time=0.03, hold_time=hold_s, pin_factory=factory)
        except Exception as e:
            log.warning("knob switch on GPIO %s disabled: %s", sw, e)
            return
        st = {"held": False, "clicks": 0, "timer": None}
        lock = threading.Lock()

        def flush():
            with lock:
                n, st["clicks"], st["timer"] = st["clicks"], 0, None
            if n == 1:
                self.emit("press")
            elif n >= 2:
                self.emit("double")

        def on_held():
            with lock:
                st["held"] = True
                if st["timer"]:
                    st["timer"].cancel()
                st["clicks"], st["timer"] = 0, None
            self.emit("hold")

        def on_released():
            with lock:
                if st["held"]:
                    st["held"] = False
                    return
                st["clicks"] += 1
                if st["timer"]:
                    st["timer"].cancel()
                if st["clicks"] >= 2:
                    st["timer"] = None
                    fire = True
                else:
                    st["timer"] = threading.Timer(double_s, flush)
                    st["timer"].daemon = True
                    st["timer"].start()
                    fire = False
            if fire:
                flush()

        btn.when_held = on_held
        btn.when_released = on_released
        self._keep.append(btn)
        log.info("knob: A=%s B=%s switch=%s", a, b, sw)

    # ---------------------------------------------------------------- touch
    def add_touch(self, touch, swipe_px=40, hold_s=0.8):
        """touch: object with read() -> None | (x, y) in logical coords (finger down)."""
        def run():
            down = None            # (x0, y0, t0, last_xy, hold_sent)
            while True:
                p = touch.read()
                now = time.monotonic()
                if p is not None:
                    if down is None:
                        down = [p[0], p[1], now, p, False]
                    else:
                        down[3] = p
                        moved = abs(p[0] - down[0]) + abs(p[1] - down[1])
                        if not down[4] and moved < 15 and now - down[2] >= hold_s:
                            down[4] = True
                            self.emit("touch_hold", down[0], down[1])
                elif down is not None:
                    x0, y0, t0, (x1, y1), held = down
                    down = None
                    dx, dy = x1 - x0, y1 - y0
                    if held:
                        pass
                    elif max(abs(dx), abs(dy)) >= swipe_px:
                        if abs(dx) >= abs(dy):
                            self.emit("swipe", "left" if dx < 0 else "right")
                        else:
                            self.emit("swipe", "up" if dy < 0 else "down")
                    else:
                        self.emit("tap", x0, y0)
                time.sleep(0.02 if down else 0.03)
        threading.Thread(target=run, name="touch", daemon=True).start()

    # ---------------------------------------------------------------- bench pipe
    def add_fifo(self, path):
        try:
            if os.path.exists(path) and not _is_fifo(path):
                os.remove(path)
            if not os.path.exists(path):
                os.mkfifo(path, 0o666)
        except OSError as e:
            log.warning("control pipe %s: %s", path, e)
            return

        def run():
            while True:
                with open(path) as f:               # blocks until a writer opens it
                    for line in f:
                        ev = _parse(line)
                        if ev:
                            self.emit(*ev)
                        elif line.strip():
                            log.info("control pipe: unknown command %r", line.strip())
        threading.Thread(target=run, name="fifo", daemon=True).start()
        log.info("bench control pipe: %s", path)


def _is_fifo(path):
    import stat
    return stat.S_ISFIFO(os.stat(path).st_mode)


def _parse(line):
    w = line.split()
    if not w:
        return None
    c = w[0].lower()
    if c in ("+", "up", "vol+"):
        return ("turn", 1)
    if c in ("-", "down", "vol-"):
        return ("turn", -1)
    if c == "turn" and len(w) > 1:
        return ("turn", 1 if int(w[1]) > 0 else -1)
    if c in ("press", "double", "hold"):
        return (c,)
    if c in ("left", "right"):
        return ("swipe", c)
    if c == "swipe" and len(w) > 1:
        return ("swipe", w[1])
    if c in ("tap", "touch_hold") and len(w) == 3:
        return (c, int(w[1]), int(w[2]))
    return None
