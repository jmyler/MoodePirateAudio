# Pirate Audio with Moode Audio

This guide configures a Pirate Audio screen (ST7789-based) to display AirPlay track information (title, artist, album art) on a fresh Moode Audio 9.1.5 install running on a Raspberry Pi Zero 2 W. These steps start from a fresh install and resolve dependency and stability issues encountered along the way.

## Prerequisites

- Fresh Moode Audio 9.1.5 installed via Raspberry Pi Imager.
- SSH enabled (`ssh pi@moode.local`, default password: `moodeaudio`).
- SPI interface enabled:
  ```bash
  sudo raspi-config
  # Navigate to Interface Options > SPI > Enable > Reboot
## Notes
- Replace [yourpiuser] with your username.

- st7789 requires pip due to Bookworm’s restrictions—apt won’t work.




# Installation Steps

1. Update the system
   ```bash
   sudo apt update && sudo apt upgrade -y

2. Install Base Python Dependencies
   ```bash
   sudo apt install python3-pip python3-pil python3-mpd -y
Installs pip3 for Python package management, python3-pil (Pillow) for image processing, and python3-mpd (python-mpd2) for MPD client support globally.

3. Create virtual environemnt
   ```bash
   python3 -m venv ~/pirate-audio/venv
   source ~/pirate-audio/venv/bin/activate
   pip install --upgrade pip
  Creates an isolated Python environment to avoid "externally managed environment" error in Raspberry Pi OS Bookworm.

 4. Install Required Python Libraries in the Virtual Environment
     ```bash
     pip install st7789 Pillow python-mpd2
  Installs:
st7789 (1.0.1) for the Pirate Audio display driver.

Pillow (11.1.0) for image handling.

python-mpd2 (3.1.1) for MPD communication.

5. Verify the installation
   ```bash
   pip list | grep -i -E "st7789|pillow|python-mpd"
You should should see the three libraries installed
Pillow      11.1.0
python-mpd2 3.1.1
st7789      1.0.1

6. Deploy the Display Script
   Create and populate display.py with the following script to read AirPlay metadata and update the screen:
   ```bash
   nano ~/pirate-audio/display.py
  Place the contents of display.py file in the repo here
  Exit and save

7. Save and make executable
   ```bash
   chmod +x ~/pirate-audio/display.py

8. Start
   ```bash
   python ~/pirate-audio/display.py &


# Run on boot

1. Create startup script
   ```bash
   nano ~/pirate-audio/run.sh
   ```
   ```bash
   #!/bin/bash
   sudo pkill -f "python.*display.py" 2>/dev/null
   source /home/[yourpiuser]/pirate-audio/venv/bin/activate
   exec sudo /home/[yourpiuser]/pirate-audio/venv/bin/python /home/[yourpiuser]/pirate-audio/display.py >> /home/[yourpiuser]/display.log 2>&1
  ```
  chmod +x ~/pirate-audio/run.sh
  ```
2. Set up Systemd Service
   ```bash
   sudo nano /etc/systemd/system/pirate-audio.service
   ```
   ```
   [Unit]
   Description=Pirate Audio Display Service
   After=network.target shairport-sync.service
   Wants=shairport-sync.service

   [Service]
   Type=simple
   ExecStart=/bin/bash /home/[yourpiuser]/pirate-audio/run.sh
   WorkingDirectory=/home/[yourpiuser]/pirate-audio
   StandardOutput=file:/home/[yourpiuser]/display.log
   StandardError=file:/home/[yourpiuser]/display.log
   Restart=on-failure
   RestartSec=10
   User=[youruser]
   Group=[youruser]
   ExecStartPre=/bin/sleep 15

   [Install]
   WantedBy=multi-user.target

  Enable and start:
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable pirate-audio.service
  sudo systemctl start pirate-audio.service
  ```
3. Reboot and verify
   ```bash
   sudo rebot

4.

# Troubleshooting
I'll fill this out as issues arrise and post the solutions

- **FileNotFoundError for spidev**:
  ```bash
  sudo raspi-config
  # Interface Options > SPI > Enable > Reboot
