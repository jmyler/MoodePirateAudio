                                                                                                                   #!/usr/bin/env python3
from st7789 import ST7789
from PIL import Image, ImageDraw, ImageFont
import mpd
import os
import time
import base64
import io

display = ST7789(
    port=0, cs=1, dc=9, backlight=13,
    spi_speed_hz=80 * 1000 * 1000,
    width=240, height=240
)

font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)

METADATA_PIPE = "/tmp/shairport-sync-metadata"

def get_mpd_info():
    try:
        client = mpd.MPDClient()
        client.connect("localhost", 6600)
        status = client.status()
        song = client.currentsong() or {}
        client.close()
        return status.get("state", "stopped"), song.get("title", "Unknown"), song.get("artist", "Unknown"), None
    except Exception as e:
        return "error", "MPD Error", str(e), None

def get_airplay_info():
    if not os.path.exists(METADATA_PIPE):
        return "stopped", "No Renderer", "Inactive", None
    try:
        with open(METADATA_PIPE, "r") as pipe:
            data = ""
            start_time = time.time()
            while time.time() - start_time < 0.5:  # Shortened read time
                line = pipe.readline()
                if line:
                    data += line
            if not data:
                return "play", "AirPlay Active", "Waiting", None
            track, artist, artwork = "Unknown", "Unknown", None
            items = data.split("</item>")
            for item in items:
                if "<code>6d696e6d</code>" in item:  # minm = track title
                    start = item.find("<data encoding=\"base64\">") + 23
                    end = item.find("</data>", start)
                    if start != -1 and end != -1:
                        track_data = item[start:end].strip()
                        track = base64.b64decode(track_data).decode("utf-8", errors="ignore") or "Unknown"
                elif "<code>61736172</code>" in item:  # asar = artist
                    start = item.find("<data encoding=\"base64\">") + 23
                    end = item.find("</data>", start)
                    if start != -1 and end != -1:
                        artist_data = item[start:end].strip()
                        artist = base64.b64decode(artist_data).decode("utf-8", errors="ignore") or "Unknown"
                elif "<code>50494354</code>" in item:  # PICT = artwork
                    start = item.find("<data encoding=\"base64\">") + 23
                    end = item.find("</data>", start)
                    if start != -1 and end != -1:
                        artwork_data = item[start:end].strip()
                        try:
                            artwork_bytes = base64.b64decode(artwork_data)
                            artwork = Image.open(io.BytesIO(artwork_bytes)).resize((150, 150), Image.Resampling.LANCZOS)
                        except:
                            artwork = None
            return "play", track, artist, artwork
    except Exception as e:
        return "error", "AirPlay Error", str(e), None

previous_track, previous_artist, previous_artwork = None, None, None

while True:
    if os.path.exists(METADATA_PIPE):
        state, track, artist, artwork = get_airplay_info()
    else:
        state, track, artist, _ = get_mpd_info()

    if track != previous_track or artist != previous_artist or (artwork is not None and artwork != previous_artwork):
        previous_track = track
        previous_artist = artist
        previous_artwork = artwork

        img = Image.new("RGB", (240, 240), "BLACK")
        if previous_artwork:
            img.paste(previous_artwork, (45, 5))  # Center the larger artwork
        draw = ImageDraw.Draw(img)
        draw.text((10, 180), f"{previous_track}", font=font, fill="WHITE")
        draw.text((10, 200), f"{previous_artist}", font=font, fill="WHITE")
        display.display(img)

    time.sleep(0.1)  # Faster loop for real-time updates
