#!/bin/bash
# One-time setup on the Pi. Run from the repo folder: ./setup.sh
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
CFG=/boot/firmware/config.txt

# I2C for the INA226 battery monitor
sudo raspi-config nonint do_i2c 0

# SPI without MISO: newer kernels let the SPI driver claim GPIO 9 (MISO),
# which is the screen's DC pin. spi0-2cs,no_miso enables SPI and leaves it free.
sudo sed -i 's/^dtparam=spi=on/#dtparam=spi=on/' "$CFG"
grep -q '^dtoverlay=spi0-2cs,no_miso' "$CFG" || echo 'dtoverlay=spi0-2cs,no_miso' | sudo tee -a "$CFG" >/dev/null

sudo apt-get install -y python3-venv python3-gpiozero python3-lgpio python3-numpy \
    python3-spidev i2c-tools fonts-dejavu-core
rm -rf venv
python3 -m venv --system-site-packages venv      # system gpiozero/lgpio/spidev visible
venv/bin/pip install --upgrade pip
venv/bin/pip install pillow python-mpd2 smbus2

# systemd service for the current user and folder
sed -e "s|__USER__|$USER|g" -e "s|__DIR__|$DIR|g" pirate-audio.service \
    | sudo tee /etc/systemd/system/pirate-audio.service >/dev/null
sudo systemctl daemon-reload

echo
echo "Done. Reboot once (sudo reboot) so the SPI change takes effect."
echo "Test by hand:   venv/bin/python display.py"
echo "Run on boot:    sudo systemctl enable --now pirate-audio"
