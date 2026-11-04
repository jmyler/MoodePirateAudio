# Pirate Audio + moOde: now playing, battery & connection control

A small screen for a moOde-based portable speaker. It shows what's playing
and who's connected, tracks a LiFePO4 battery, and lets you manage
who's connected with buttons on the box.

Built for a Raspberry Pi running **moOde 10.x (Trixie, 64-bit)**. Two
hardware setups are supported (set `PANEL` at the top of `display.py`):

| | Screen | DAC | Buttons |
|---|---|---|---|
| **Current build** (`PANEL = "waveshare2"`) | Waveshare 2" 240×320 ST7789 IPS, used landscape (320×240) | Raspberry Pi DAC+ (PCM5122) | Panel-mount, wired to the DAC+ passthrough header |
| **Original** (`PANEL = "pirate"`) | Pimoroni Pirate Audio Line-Out, 240×240 | PCM5100A on the same board | The board's A/B/X/Y |

Tested on a Pi 3B+ (Waveshare + DAC+) and a Pi Zero 2 W (Pirate Audio).

## Features

- **Now playing for any moOde source.** It follows moOde's own
  active-renderer flags:
  - **AirPlay:** title, artist and cover art from moOde's
    `/var/local/www/aplmeta.json`. The script never reads the shairport
    metadata pipe, because moOde already reads it and two readers on one
    pipe each get random pieces of the data. That was the cause of the old
    "AirPlay is flaky" problem.
  - **Bluetooth:** title, artist, album, play state and cover art from
    moOde's `/var/local/www/btmeta.json`, plus the connected phone's name
    from BlueZ. moOde finds the cover by looking the song up in Apple's
    catalog; the display downloads it once and keeps it in `art_cache/`, so
    songs you've played before show art **offline** (play your walk-up
    playlist once at home and the whole set is cached).
  - **Radio:** "Artist - Title" stream titles get art the same way.
  - **Spotify / other renderers:** shows the source label.
  - **Library / radio / UPnP:** from MPD, with embedded cover art.
- **Who's connected:** a full-screen "Connected: Maddux's iPhone" splash
  when a phone connects, and a matching one on disconnect.
- **Shared-speaker controls:**
  - **Hold A (2 s) to kick.** Disconnects the current Bluetooth phone and
    blocks it for 5 minutes so it can't grab the speaker right back. Also
    drops an active AirPlay sender. Blocks are saved to a file and lifted on
    restart, so nobody stays blocked by accident.
  - **Hold B (2 s) for pairing mode.** The speaker is discoverable for up
    to 2 minutes, with a countdown and the name to look for on screen. The
    window closes as soon as a phone connects. Otherwise it stays hidden
    from new devices; phones that have already paired can still connect.
- **Battery (INA226 coulomb counter):** percentage, runtime left,
  voltage/current/power and peak current. It syncs to 100% on a full
  charge and keeps its count across restarts.
- **Short presses:** A = screen on/off, B = now playing ↔ battery detail,
  X/Y = volume.

## Files

| File | Purpose |
|---|---|
| `display.py` | Main program: rendering, buttons, battery thread |
| `sources.py` | Works out what moOde is playing and gets the metadata |
| `btctl.py` | Bluetooth kick/block, pairing mode, AirPlay drop |
| `st7789_lite.py` | Minimal ST7789 driver (spidev + lgpio) for both panels; avoids `gpiodevice` |
| `artlookup.py` | Downloads and caches cover art (moOde's URL, or an iTunes search as a fallback) |
| `screen_test.py` | Bench test: border, "up" arrow and colour bars, to check wiring and rotation |
| `ina226.py` | INA226 driver and LiFePO4 battery gauge |
| `ina_test.py` | Bench test for the INA226 |
| `setup.sh` | One-time install (packages, venv, SPI fix, service) |
| `pirate-audio.service` | systemd unit template (filled in by `setup.sh`) |
| `legacy/` | The original scripts, kept for reference |

## Install

1. Flash **moOde 10.x** with Raspberry Pi Imager. In Edit Settings, set
   the hostname, user/password, Wi-Fi (2.4 GHz for a Zero 2 W) and
   **enable SSH**.
2. In the moOde web UI:
   - **Configure → Audio → Named I2S device:** **Raspberry Pi DAC+** (it is
     auto-detected from its ID EEPROM, so no `dtoverlay` line appears), or
     **HiFiBerry DAC** for the Pirate Audio.
   - **Configure → Renderers:** turn on **AirPlay** and **Bluetooth**.
   - In the Bluetooth settings, **turn off pairing confirmation.**
     Otherwise moOde waits for someone to click "accept" in its web UI and
     the phone times out. The box's own pairing mode controls when new
     phones can pair.
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

Bluetooth control talks to BlueZ directly over D-Bus (`busctl` through
`sudo -n`). It deliberately **never runs `bluetoothctl`**: every
`bluetoothctl` invocation registers itself as the pairing agent and then
exits, which knocks out moOde's agent and makes pairing hang on the phone.
The user that Imager creates has passwordless sudo by default; check with
`sudo -n true && echo ok`.

While testing on the bench, keep the service from grabbing the screen pins
with `sudo systemctl disable --now pirate-audio`.

## Configuration

Settings are at the top of `display.py`:

- `CAPACITY_AH`, `CELLS`: battery pack (default 8S, 25.6 V LiFePO4)
- `SHUNT_OHMS`, `CURRENT_GAIN`, `CURRENT_SIGN`: INA226 shunt calibration
- `PANEL`, `ROTATION` (90 or 270), `SCREEN_PINS`: which screen and how it's mounted
- `BUTTONS`: GPIO pins (A=5, B=6, X=16, Y=24, the Pirate Audio's pins)
- `HIDE_BY_DEFAULT`: set to `False` to stay discoverable all the time
- `PAIR_S`, `LONG_PRESS_S`, `PAIR_HINT`
- `btctl.HOLD_S`: how long a kicked phone stays blocked

Optional: put a 240×240 `jm_logo.jpeg` next to `display.py` to use as the
idle screen.

## Wiring (current build)

Everything plugs into the Raspberry Pi DAC+'s passthrough header. Physical
pin numbers: pin 1 is at the SD-card end, and odd pins are the inner row.

**Waveshare 2" screen**

| Screen | Pi pin | GPIO |
|---|---|---|
| VCC | 17 | 3.3 V |
| GND | 20 | GND |
| DIN | 19 | 10 (MOSI) |
| CLK | 23 | 11 (SCLK) |
| CS | 26 | 7 (CE1) |
| DC | 21 | 9 |
| RST | 13 | 27 |
| BL | 33 | 13 |

**Buttons:** each button goes between its GPIO and GND. The internal
pull-ups are enabled, so a jumper touched to GND works as a button on the
bench.

| Button | GPIO | Pi pin |
|---|---|---|
| A (screen / kick) | 5 | 29 |
| B (view / pairing) | 6 | 31 |
| X (vol +) | 16 | 36 |
| Y (vol −) | 24 | 18 |

None of these pins are used by the DAC+ (I2S on 18–21, I2C, GPIO 22 mute).

## Wiring the INA226

| INA226 | Pi (physical pin) |
|---|---|
| VCC | 3.3 V (1) |
| GND | GND (9), also tied to battery − |
| SDA | GPIO 2 (3) |
| SCL | GPIO 3 (5) |
| IN+ / IN− | inline with battery + (high side) |
| VBUS | battery + |

`i2cdetect -y 1` should show the INA226 at **0x40** (and the DAC+ at 0x4c
or `UU`). Run
`venv/bin/python ina_test.py` to check the readings.

## Gotchas this fixes

- **"Invalid argument" claiming GPIO 9:** on newer kernels the SPI driver
  owns MISO (GPIO 9), which the Pirate Audio uses for the screen's DC line.
  `setup.sh` replaces `dtparam=spi=on` with `dtoverlay=spi0-2cs,no_miso`.
  moOde can rewrite `config.txt` when you change audio settings, so if the
  screen stops working, check that this line is still there.
- **Pimoroni's `st7789` / `gpiodevice` failing on Trixie:** this project
  uses its own small driver (`st7789_lite.py`) instead.
- **AirPlay metadata missing or random:** read moOde's `aplmeta.json`,
  not the pipe.
- **moOde rewrote `config.txt` and the screen died:** changing the audio
  device in moOde can drop the `spi0-2cs,no_miso` line. Check with
  `grep -n spi /boot/firmware/config.txt` after any audio change.
- **Screen backlight on but nothing drawn:** a data wire is on the wrong
  pin, usually off by a row. Run `pinout` on the Pi for a labelled map.
- **"GPIO busy":** the service is already running and holding the pins.
- **Bluetooth pairing times out on the phone:** moOde's pairing
  confirmation is on (see Install), or something ran `bluetoothctl` and
  displaced moOde's agent. Reboot to bring the agent back.
- **SD card verify failures in Imager:** try a different card reader
  before blaming the card.
