# Radio

A two-knob radio. One knob picks the location, the other picks the epoch, and
every switch tunes through static. Stations are always “live”: come back to one
later and it has moved on.

Enclosure model: `Radio.stl`. Source:
<https://cad.onshape.com/documents/3f97c19d22cdfbb1ff89ef73/w/63b62facf99d46d81c1500a9/e/cb74c49569cd1aa5f598bcb6>

## Cities

London, Paris, Berlin, Rome, Kraków, Moscow, New York, Los Angeles, Tokyo, Tel
Aviv, Buenos Aires, Havana, Deli

## Music download

[Install UV](https://docs.astral.sh/uv/getting-started/installation/). Then:

``` sh
uvx --with curl_cffi yt-dlp --cookies-from-browser chrome --js-runtimes node --remote-components ejs:github --no-warnings https://youtu.be/-bWo0ky8xAs
```

Change `chrome` to your browser of choice.

## Setup

``` sh
brew install mpd ffmpeg mp3gain uv # Mac
sudo apt install mpd ffmpeg && curl -LsSf https://astral.sh/uv/install.sh | sh # Raspberry Pi
sudo systemctl disable --now mpd mpd.socket # Pi: the stock mpd service would hold the sound card
cp .env.example .env
```

## Music

``` text
music/
  01_Paris/
    1_30s/*.mp3
    2_60s/*.mp3
    3_90s/*.mp3
  02_London/
    ...
static.mp3
```

- Locations and epochs are ordered by folder name, so use numeric prefixes.
- The epoch knob spans every epoch folder with MP3s in any location; a location
  missing one plays static there. Folders without MP3s are ignored. Startup
  fails otherwise.
- `make normalize` evens out loudness (needs `mp3gain`).

## Run

``` sh
make run
```

- Keyboard (in a terminal): ←/→ or A/D switch location, ↑/↓ or W/S switch epoch,
  T starts the random walk. The knobs stop at the first and last position.
- On the Pi, the KY-040 encoders work as well. Wire CLK/DT to the BCM pins in
  `.env`, `+` to 3.3V, and GND to GND.
- The same code and config run on Mac and Pi. The audio output is detected
  automatically.
- mpd keeps running after you exit and is restarted on the next run. Stop it
  with `pkill -f radio-mpd.conf`.

## Scale lights

The selected location and epoch light up on one addressable strip, driven by an
ESP32 running WLED and plugged into the Pi (or Mac) over USB. No WiFi is
involved.

- WLED setup: Config → Sync Interfaces → Serial baud `115200`; LED Preferences →
  type `WS281x` (RGB, not RGBW: a wrong type shifts LEDs along the strip),
  length covers the highest index in `.env`, brightness limiter on with 500 mA
  (powered from the Pi’s USB), “Turn LEDs on after power up” off and boot preset
  `0`; WiFi Setup → AP opens `Never`, so it doesn’t broadcast a hotspot at the
  event.
- Find the port with `ls /dev/cu.*` (Mac) or `ls /dev/serial/by-id/` (Pi) and
  put it in `WLED_PORT`. The by-id path survives replugging.
- The Pi needs a proper 5.1V 2.5A supply: on undervoltage
  (`cat /sys/class/hwmon/hwmon1/in0_lcrit_alarm` is `1`) the ESP32 reboots in a
  loop and the port keeps disappearing.
- On the Pi the user needs the port’s group (`dialout`):
  `sudo usermod -aG dialout $USER`, then log in again. Startup fails if the port
  is missing, not writable, or WLED doesn’t answer.

## Dial

`make radio.pdf` builds the printed dial (60×20 cm plus 3 mm bleed) from
`scale.py`; `make radio.png` is a preview. Build on a Mac: it needs Helvetica
Neue.

## Settings (`.env`)

| Key | Meaning |
|:---|:---|
| `PREVENT_SLEEP` | `1` keeps the machine awake while the radio runs |
| `LOG_LEVEL` | `INFO`, or `DEBUG` to also log every MPD command |
| `WALK_IDLE` | seconds of no activity before the random walk: a random station for 5–10s, then 2–5s of static, until a knob or key is touched |
| `STATIC_FADE` | seconds of crossfade between music and static |
| `STATIC_HOLD` | seconds of pure static after the last turn, must be \> 0; the new station starts `STATIC_FADE + STATIC_HOLD` after it |
| `ENC_LOC_A/B`, `ENC_EPOCH_A/B` | encoder CLK/DT pins (Pi only) |
| `WLED_PORT` | serial port of the WLED controller, empty turns the scale lights off |
| `LED_LOCATIONS`, `LED_EPOCHS` | first LED of each range, one per location and one per epoch, e.g. `0,5,10` |
| `LED_LOCATIONS_LEN`, `LED_EPOCHS_LEN` | LEDs per location / per epoch |
| `LED_ON`, `LED_OFF` | `RRGGBB` colors of the selected position and the rest |

## Logs

- Printed to the terminal.
- Broadcast as syslog over UDP 514 to the local network. On the log server, copy
  `rsyslog-radio.conf` to `/etc/rsyslog.d/` and restart rsyslog. Logs land in
  `/var/log/radio.log`.
- mpd’s own log: `/tmp/radio-mpd.log`.

Design decisions: `AGENTS.md`.

## Songs

`songs.json`: location → epoch (`1_early`, `2_middle`, `3_modern`) → 3–7 songs.
`uv run download.py` fetches the missing ones into `music/` (first YouTube
search hit, needs ffmpeg) and removes the ones no longer listed.
[archive](https://drive.google.com/file/d/1AcYtEuLTQwYhTQlg3ZpNMd7gsZxThM4N/view?usp=drive_link)
