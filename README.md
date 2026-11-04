# moOde speaker screen: now playing, battery & connection control

A small screen for a battery-powered moOde speaker. It shows what's playing
and who's connected, tracks a LiFePO4 battery, and lets you control the
speaker from a touch screen on the box.

Built for a Raspberry Pi running **moOde 10.x (Trixie, 64-bit)**:

| Part | Used here |
|---|---|
| Pi | Raspberry Pi 3B+ |
| DAC | Raspberry Pi DAC+ (PCM5122), with GPIO passthrough header |
| Screen | Waveshare 2" 240×320 ST7789 IPS, used landscape (320×240). The capacitive-touch version (CST816D) adds touch; the plain one works without it |
| Battery monitor | INA226 module with a 2 mΩ shunt |
| Power | Mean Well SD-15B-5 (25.6 V pack → 5 V) |

The project started on a Pimoroni Pirate Audio (240×240). That version is in
the git history (and `legacy/`); the current UI needs the 320×240 screen.

## Features

- **Ambient Glass UI.** Blurred album art behind everything while music
  plays, a single dark brand backdrop otherwise. Every screen uses the same
  status bar and glass cards. Run `python3 preview.py` on any computer to
  render every screen to `preview_sheet.png`.
- **Bluetooth first.** Like any Bluetooth speaker, the box is discoverable
  whenever nothing is connected or playing, and the idle screen tells people
  which name to pick. It hides as soon as someone connects, so nobody can grab
  it mid-song. Phones that have already paired can always reconnect.
- **Now playing for any moOde source**, following moOde's own
  active-renderer flags:
  - **AirPlay:** title, artist and cover art from moOde's
    `/var/local/www/aplmeta.json`. The script never reads the shairport
    metadata pipe, because moOde already reads it and two readers on one
    pipe each get random pieces of the data.
  - **Bluetooth:** title, artist, album, play state and cover art from
    moOde's `/var/local/www/btmeta.json`, plus the phone's name from BlueZ.
    Covers are downloaded once and cached in `art_cache/`, so songs you've
    played before show art **offline**.
  - **Radio:** "Artist - Title" stream titles get art the same way.
  - **Library / radio / UPnP:** from MPD, with embedded cover art.
- **Playback control.** Play/pause and next track go to the phone over
  AVRCP (Bluetooth), to MPD, or to shairport-sync's remote control when
  moOde's build has it (otherwise the box says to use the phone).
- **Menu:** disconnect the current phone (it's blocked for 5 minutes so it
  can't grab the speaker right back), let a second phone pair while one is
  connected, jump to any screen, and a controls cheat sheet.
- **Phones screen:** every paired phone, connected one first, with blocked
  phones and their minutes left. Pick one to disconnect, unblock it early, or
  **forget** it (it has to pair again), which keeps a shared speaker from being
  grabbed by every phone that ever paired.
- **Join screen:** two QR codes. One joins the Wi-Fi the speaker is on (its
  own hotspot at the field, or your home network) so phones can use AirPlay;
  the other opens the moOde web UI. The Wi-Fi password comes from
  NetworkManager (`sudo nmcli`), or set `WIFI_QR_PSK`.
- **Session stats:** time on, Ah and % of the pack used, average and peak
  draw, and a graph of current draw over the last few hours.
- **Popups** for phone connected/disconnected, low battery (20 % and 10 %),
  fully charged, CPU running hot and Pi under-voltage.
- **Battery (INA226 coulomb counter):** ring gauge, runtime left,
  voltage/current/power/peak. With the charger connected it switches to a
  charging view with input watts and time to full. It syncs to 100 % on a full
  charge and keeps its count across restarts.
- **System screen:** CPU temperature, power-supply health, Wi-Fi/hotspot,
  IP address, uptime and Bluetooth visibility.
- **Backlight dimming:** full brightness while playing; dims when paused or
  idle, and turns off after 10 minutes with nobody connected. Any event wakes
  it (a touch on a dark screen only wakes it).
- **Boot splash** early in boot (`pirate-splash.service`), and a "safe to
  switch off" screen on `sudo poweroff`.

## Controls

Everything is on the touch screen (the phone that's playing handles volume too):

| Do this | What happens |
|---|---|
| Tap the song on Now playing | A control bar slides up: volume − / +, previous, play/pause, next. It hides after 5 s |
| Swipe left / right | Change screen |
| Tap the top bar, or hold a finger down | Menu: disconnect, add a phone, Phones, Join Wi-Fi, Battery, System, Controls help |
| Tap a popup | Dismiss it |
| Tap ✕ | Close the menu |

Screens, in swipe order: **Now playing → Battery → Session stats → Phones →
Join → System.** Info screens go back to Now playing after a minute untouched.
Disconnecting and forgetting a phone need a second tap to confirm.

An optional rotary knob is still supported (turn = volume, press = play/pause,
press twice = next, hold = menu): wire it to GPIO 5/6/16 and set
`KNOB = dict(a=5, b=6, sw=16)` in `display.py`.

For the Join screen to be useful away from home, set up moOde's hotspot
(Configure → Network) and check once that it comes up when no known Wi-Fi is
in range.

**Bench control pipe.** Every control can be tested without the touch screen
fitted:

```bash
cd ~/pirate-audio
echo "tap 160 120" > ctl   # also: left, right, hold, press (play/pause), +, -
```

## Files

| File | Purpose |
|---|---|
| `display.py` | Main program and all settings: state, menu, input handling, backlight, battery/system threads |
| `ui.py` | Draws every screen (pure PIL, no hardware) |
| `inputs.py` | Knob (rotary encoder + switch), touch gestures, bench control pipe |
| `cst816.py` | CST816D touch controller driver |
| `sources.py` | Works out what moOde is playing and gets the metadata |
| `btctl.py` | Bluetooth visibility, kick/block, AVRCP play/pause/next, AirPlay drop |
| `st7789_lite.py` | Minimal ST7789 driver (spidev + lgpio) with PWM backlight |
| `artlookup.py` | Downloads and caches cover art |
| `ina226.py` | INA226 driver and LiFePO4 battery gauge |
| `splash.py` | Early-boot splash (a cached image from the last run) |
| `preview.py` | Renders every screen with sample data to `preview_sheet.png` |
| `screen_test.py`, `touch_test.py`, `ina_test.py` | Bench tests |
| `setup.sh` | One-time install (packages, venv, SPI fix, services) |
| `pirate-audio.service`, `pirate-splash.service` | systemd unit templates |

## Install

1. Flash **moOde 10.x** with Raspberry Pi Imager. In Edit Settings, set
   the hostname, user/password and Wi-Fi, and **enable SSH**.
2. In the moOde web UI:
   - **Configure → Audio → Named I2S device:** **Raspberry Pi DAC+** (it is
     auto-detected from its ID EEPROM, so no `dtoverlay` line appears).
   - **Configure → Renderers:** turn on **AirPlay** and **Bluetooth**. The
     Bluetooth name you set there is the name the screen tells people to pick.
   - In the Bluetooth settings, **turn off pairing confirmation.**
     Otherwise moOde waits for someone to click "accept" in its web UI and
     the phone times out. This program decides when the box is discoverable.
3. On the Pi:
   ```bash
   git clone https://github.com/jmyler/MoodePirateAudio ~/pirate-audio
   cd ~/pirate-audio && ./setup.sh && sudo reboot
   ```
4. Check the screen wiring and rotation, then test by hand and enable at boot:
   ```bash
   cd ~/pirate-audio && venv/bin/python screen_test.py      # add 270 to try the other rotation
   venv/bin/python display.py
   sudo systemctl enable --now pirate-audio
   journalctl -u pirate-audio -f
   ```

Updating an existing install: `git pull`, then run `./setup.sh` again (it
adds `fonts-inter` and the splash service), then
`sudo systemctl restart pirate-audio`.

Bluetooth control talks to BlueZ directly over D-Bus (`busctl`, writes through
`sudo -n`). It deliberately **never runs `bluetoothctl`**: every
`bluetoothctl` invocation registers itself as the pairing agent and then
exits, which knocks out moOde's agent and makes pairing hang on the phone.
Volume goes through moOde's `/var/www/util/vol.sh` so the web UI stays in sync.
The user that Imager creates has passwordless sudo by default; check with
`sudo -n true && echo ok`.

While testing on the bench, keep the service from grabbing the screen pins
with `sudo systemctl disable --now pirate-audio`.

## Configuration

Settings are at the top of `display.py`:

- `CAPACITY_AH`, `CELLS`: battery pack (default 8S 25.6 V, 6 Ah)
- `SHUNT_OHMS`, `CURRENT_GAIN`, `CURRENT_SIGN`: INA226 shunt calibration
- `LOW_ALERTS`: low-battery popup thresholds
- `PANEL`, `ROTATION` (90 or 270), `SCREEN_PINS`
- `KNOB`, `KNOB_REVERSE`, `KNOB_HOLD_S`, `DOUBLE_S`
- `TOUCH` (`"auto"`, `True`, `False`), `TOUCH_PINS`, `TOUCH_MAP`
- `VOL_STEP`, `VOL_MAX` (a cap, e.g. 85)
- `PAIR_S`: how long "Add a phone" stays open
- `BRIGHT_DIM`, `DIM_PAUSED_S`, `DIM_IDLE_S`, `OFF_IDLE_S`
- `HOT_C`: CPU temperature warning
- `btctl.HOLD_S`: how long a disconnected phone stays blocked

## Wiring

Everything plugs into the Raspberry Pi DAC+'s passthrough header (a GPIO
screw-terminal board on top makes the shared I2C and ground connections
easy). Physical pin 1 is at the SD-card end; odd pins are the inner row.

**Screen (Waveshare 2", touch or non-touch)**

| Screen | Pi pin | GPIO |
|---|---|---|
| VCC | 17 | 3.3 V |
| GND | 20 | GND |
| DIN | 19 | 10 (MOSI) |
| CLK | 23 | 11 (SCLK) |
| CS | 26 | 7 (CE1) |
| DC | 21 | 9 |
| RST | 13 | 27 |
| BL | 33 | 13 (PWM) |
| TP_SDA | 3 | 2 (shared I2C) |
| TP_SCL | 5 | 3 (shared I2C) |
| TP_INT | 7 | 4 |
| TP_RST | 11 | 17 |

**Knob (optional, not fitted by default).** Common pin(s) to GND; internal
pull-ups are used.

| Knob | GPIO | Pi pin |
|---|---|---|
| A | 5 | 29 |
| B | 6 | 31 |
| Switch | 16 | 36 |

**INA226**

| INA226 | Pi (physical pin) |
|---|---|
| VCC | 3.3 V (1) |
| GND | GND (9), also tied to battery − |
| SDA | GPIO 2 (3) |
| SCL | GPIO 3 (5) |
| IN+ / IN− | inline with battery + (high side) |
| VBUS | battery + |

**5 V in:** Mean Well 5 V to pins 2 **and** 4, ground to two GND pins. One
pin of each is not enough for a Pi 3B+ (under-voltage, screen resets).

**Pins in use:** GPIO 0/1 (DAC+ EEPROM), 2/3 (I2C: DAC+, INA226, touch),
4, 5, 6, 7, 8 (claimed by `spi0-2cs`), 9, 10, 11, 13, 16, 17, 18–21 (I2S),
22 (DAC+ mute, reserved), 27. **Free:** 12, 14, 15, 23, 24, 25, 26. 23 and 26
are earmarked for a soft-latching power switch, and 24 for amp mute.

`i2cdetect -y 1` should show the INA226 at **0x40**, the touch controller at
**0x15** (it may only answer after a touch or a reset) and the DAC+ at 0x4c
or `UU`.

## Gotchas this fixes

- **"Invalid argument" claiming GPIO 9:** on newer kernels the SPI driver
  owns MISO (GPIO 9), which is the screen's DC line. `setup.sh` replaces
  `dtparam=spi=on` with `dtoverlay=spi0-2cs,no_miso`.
- **moOde rewrote `config.txt` and the screen died:** changing the audio
  device in moOde can drop the `spi0-2cs,no_miso` line. Check with
  `grep -n spi /boot/firmware/config.txt` after any audio change.
- **Pimoroni's `st7789` / `gpiodevice` failing on Trixie:** this project
  uses its own small driver (`st7789_lite.py`) instead.
- **AirPlay metadata missing or random:** read moOde's `aplmeta.json`,
  not the pipe.
- **Screen backlight on but nothing drawn:** a data wire is on the wrong
  pin, usually off by a row. Run `pinout` on the Pi for a labelled map.
- **Screen blanks or the Pi reboots under load:** check
  `vcgencmd get_throttled`; anything but `0x0` means the 5 V feed is
  sagging (see Wiring). The System screen shows this too.
- **"GPIO busy":** the service is already running and holding the pins.
- **Bluetooth pairing times out on the phone:** moOde's pairing
  confirmation is on (see Install), or something ran `bluetoothctl` and
  displaced moOde's agent. Reboot to bring the agent back.
- **Taps land in the wrong place:** run `venv/bin/python touch_test.py`
  and set `TOUCH_MAP`.
- **SD card verify failures in Imager:** try a different card reader
  before blaming the card.
