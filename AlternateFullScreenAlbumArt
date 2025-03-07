#!/usr/bin/env python3
from st7789 import ST7789
from PIL import Image, ImageDraw, ImageFont
import mpd
import os
import time
import base64
import io
import logging

# Setup logging
logging.basicConfig(filename='/home/jmyler/pirate-audio/display-metadata.log', level=logging.DEBUG)

display = ST7789(
    port=0, cs=1, dc=9, backlight=13,
    spi_speed_hz=80 * 1000 * 1000,
    width=240, height=240
)

font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
METADATA_PIPE = "/tmp/shairport-sync-metadata"

# Load the logo image for screensaver and fallback
try:
    screensaver_img = Image.open("/home/jmyler/pirate-audio/jm_logo.jpeg").resize((240, 240), Image.Resampling.LANCZOS)
    fallback_img = screensaver_img.copy()  # Use logo as fallback if art fails
except Exception as e:
    logging.error(f"Failed to load screensaver image: {e}")
    screensaver_img = Image.new("RGB", (240, 240), "BLACK")
    fallback_img = screensaver_img

def get_mpd_info():
    try:
        client = mpd.MPDClient()
        client.connect("localhost", 6600)
        status = client.status()
        song = client.currentsong() or {}
        client.close()
        return status.get("state", "stopped"), song.get("title", "Unknown"), song.get("artist", "Unknown"), None
    except Exception as e:
        logging.error(f"MPD Error: {e}")
        return "error", "MPD Error", str(e), None

def get_airplay_info():
    if not os.path.exists(METADATA_PIPE):
        return "stopped", "No Renderer", "Inactive", None
    try:
        with open(METADATA_PIPE, "r") as pipe:
            data = ""
            start_time = time.time()
            while time.time() - start_time < 0.5:
                line = pipe.readline()
                if line:
                    data += line
            if not data:
                return "play", "AirPlay Active", "Waiting", None
            track, artist, artwork = "Unknown", "Unknown", None
            items = data.split("</item>")
            for item in items:
                if "<code>6d696e6d</code>" in item:
                    start = item.find("<data encoding=\"base64\">") + 23
                    end = item.find("</data>", start)
                    if start != -1 and end != -1:
                        track_data = item[start:end].strip()
                        track = base64.b64decode(track_data).decode("utf-8", errors="ignore") or "Unknown"
                elif "<code>61736172</code>" in item:
                    start = item.find("<data encoding=\"base64\">") + 23
                    end = item.find("</data>", start)
                    if start != -1 and end != -1:
                        artist_data = item[start:end].strip()
                        artist = base64.b64decode(artist_data).decode("utf-8", errors="ignore") or "Unknown"
                elif "<code>50494354</code>" in item:
                    start = item.find("<data encoding=\"base64\">") + 23
                    end = item.find("</data>", start)
                    if start != -1 and end != -1:
                        artwork_data = item[start:end].strip()
                        try:
                            artwork_bytes = base64.b64decode(artwork_data)
                            artwork = Image.open(io.BytesIO(artwork_bytes)).resize((240, 240), Image.Resampling.LANCZOS)
                        except Exception as e:
                            logging.error(f"Failed to load artwork: {e}")
                            artwork = fallback_img
            return "play", track, artist, artwork
    except Exception as e:
        logging.error(f"AirPlay Error: {e}")
        return "error", "AirPlay Error", str(e), None

def fetch_metadata_with_retry(retry_count=3, delay=0.5):
    for attempt in range(retry_count):
        state, track, artist, artwork = get_airplay_info()
        if track != "Unknown" or artist != "Unknown":
            return state, track, artist, artwork
        time.sleep(delay)
    return state, track, artist, artwork

def draw_screensaver():
    return screensaver_img

# Track state
previous_track, previous_artist, previous_artwork = None, None, None
last_update_time = 0
last_song_change_time = 0
last_valid_state_time = 0
show_text_duration = 5
grace_period_duration = 5
scroll_speed = 0.1
scroll_offset = 0
current_display_mode = "idle"

while True:
    try:
        current_time = time.time()

        # Get playback info
        state, track, artist, artwork = fetch_metadata_with_retry()

        logging.debug(f"State: {state}, Track: {track}, Artist: {artist}, Artwork: {bool(artwork)}")

        # Check for idle state
        is_idle = (state == "stopped" or (track == "Unknown" and artist == "Unknown")) and (current_time - last_valid_state_time) > grace_period_duration

        # Detect song change or playback resume
        if not is_idle and (track != previous_track or artist != previous_artist or artwork != previous_artwork):
            logging.debug(f"Song change detected: {track} by {artist}")
            previous_track = track
            previous_artist = artist
            previous_artwork = artwork
            last_update_time = current_time
            last_song_change_time = current_time
            last_valid_state_time = current_time
            current_display_mode = "text_and_art"
            scroll_offset = 0  # Reset scroll if track changes

        # Decide what to display
        if is_idle:
            if current_display_mode != "idle":
                display.display(draw_screensaver())
                current_display_mode = "idle"
                logging.debug("Switched to screensaver")
        elif artwork:
            if current_display_mode == "text_and_art" and (current_time - last_song_change_time) >= show_text_duration:
                display.display(artwork)
                current_display_mode = "full_art"
                logging.debug("Switched to full-screen art")
            else:
                img = Image.new("RGB", (240, 240), "BLACK")
                img.paste(artwork, (0, 0))
                draw = ImageDraw.Draw(img)
                track_bbox = draw.textbbox((0, 0), previous_track, font=font)
                track_width = track_bbox[2] - track_bbox[0]
                if track_width > 210:
                    scroll_offset -= 2
                    if abs(scroll_offset) > track_width:
                        scroll_offset = 240
                    draw.text((scroll_offset, 180), previous_track, font=font, fill="WHITE")
                else:
                    draw.text((10, 180), previous_track, font=font, fill="WHITE")
                draw.text((10, 200), previous_artist, font=font, fill="WHITE")
                display.display(img)
                current_display_mode = "text_and_art"
                logging.debug("Displaying text and art")
        else:
            if current_display_mode != "idle":
                display.display(draw_screensaver())
                current_display_mode = "idle"
                logging.debug("No artwork, switched to screensaver")

        time.sleep(0.1)
    except Exception as e:
        logging.exception("Exception in main loop")
