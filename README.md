# Pirate Audio + moOde: now playing, battery & connection control

A small screen for a moOde-based portable speaker. It shows what's playing
and who's connected, tracks a LiFePO4 battery, and lets you manage
who's connected with buttons on the box.

Built for a Raspberry Pi running **moOde 10.x (Trixie, 64-bit)** with a
Pimoroni **Pirate Audio Line-Out** (ST7789 240×240 screen, 4 buttons,
PCM5100A DAC). Any ST7789 SPI screen and I2S DAC should work with small
config changes.

## Features

- **Now playing for any moOde source.** It follows moOde's own
  active-renderer flags:
  - **AirPlay:** title, artist and cover art from moOde's
    `/var/local/www/aplmeta.json`. The script never reads the shairport
    metadata pipe, because moOde already reads it and two readers on one
    pipe each get random pieces of the data. That was the cause of the old
    "AirPlay is flaky" problem.
  - **Bluetooth:** the connected phone's name, plus title and artist over
    AVRCP, read through BlueZ.
  - **Spotify / other renderers:** shows the source label.
  - **Library / radio / UPnP:** from MPD, with embedded cover art.
- **Who's connected:** a full-screen "Connected: Maddux's iPhone" splash
  when a phone connects, and a matching one on disconnect.
- **Shared-speaker controls:**
  - **Hold A (2 s) to kick.** Disconnects the current Bluetooth phone and
    blocks it for 5 minutes so it can't grab the speaker right back. Also
    drops an active AirPlay sender. Blocks are saved to a file and lifted on
    restart, so nobody stays blocked by accident.
  - **Hold B (2 s) for pairing mode.** The speaker is discoverable for
    2 minutes, and the screen shows a countdown and the name to look for.
    Otherwise it stays hidden from new devices; phones that have already
    paired can still connect.
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
| `st7789_lite.py` | Minimal ST7789 driver (spidev + lgpio); avoids `gpiodevice` |
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
   - **Configure → Audio → Named I2S device:** **HiFiBerry DAC** for the
     Pirate Audio (or **Raspberry Pi DAC+** for that board).
   - **Configure → Renderers:** turn on **AirPlay** and **Bluetooth**.
3. On the Pi:
   ```bash
   git clone https://github.com/jmyler/MoodePirateAudio ~/pirate-audio
   cd ~/pirate-audio && ./setup.sh && sudo reboot
   ```
4. Test by hand, then enable at boot:
   ```bash
   cd ~/pirate-audio && venv/bin/python display.py
   sudo systemctl enable --now pirate-audio
   journalctl -u pirate-audio -f
   ```

Kick and pairing mode run `bluetoothctl` through `sudo -n`. The user that
Imager creates has passwordless sudo by default. Check with
`sudo -n true && echo ok`.

## Configuration

Settings are at the top of `display.py`:

- `CAPACITY_AH`, `CELLS`: battery pack (default 8S, 25.6 V LiFePO4)
- `SHUNT_OHMS`, `CURRENT_GAIN`, `CURRENT_SIGN`: INA226 shunt calibration
- `BUTTONS`: GPIO pins (Pirate Audio: A=5, B=6, X=16, Y=24)
- `HIDE_BY_DEFAULT`: set to `False` to stay discoverable all the time
- `PAIR_S`, `LONG_PRESS_S`, `PAIR_HINT`
- `btctl.HOLD_S`: how long a kicked phone stays blocked

Optional: put a 240×240 `jm_logo.jpeg` next to `display.py` to use as the
idle screen.

## Wiring the INA226

| INA226 | Pi (physical pin) |
|---|---|
| VCC | 3.3 V (1) |
| GND | GND (6), also tied to battery − |
| SDA | GPIO 2 (3) |
| SCL | GPIO 3 (5) |
| IN+ / IN− | inline with battery + (high side) |
| VBUS | battery + |

`i2cdetect -y 1` should show the INA226 at **0x40**. Run
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
- **SD card verify failures in Imager:** try a different card reader
  before blaming the card.
