# Radio

A two-knob radio. One knob picks the location, the other picks the epoch, and every switch tunes through static. Stations are always "live": come back to one later and it has moved on.

Enclosure model: `Radio.stl`. Source: <https://cad.onshape.com/documents/3f97c19d22cdfbb1ff89ef73/w/63b62facf99d46d81c1500a9/e/cb74c49569cd1aa5f598bcb6>

## Setup

```sh
brew install mpd ffmpeg mp3gain uv # Mac
sudo apt install mpd ffmpeg && curl -LsSf https://astral.sh/uv/install.sh | sh # Raspberry Pi
sudo systemctl disable --now mpd mpd.socket # Pi: the stock mpd service would hold the sound card
cp .env.example .env
```

## Music

```
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
- Every location needs exactly 3 epoch folders, and each needs at least one MP3. Startup fails otherwise.
- `make normalize` evens out loudness (needs `mp3gain`).

## Run

```sh
make run
```

- Keyboard (in a terminal): ←/→ or A/D switch location, ↑/↓ or W/S switch epoch. The knobs stop at the first and last position.
- On the Pi, the KY-040 encoders work as well. Wire CLK/DT to the BCM pins in `.env`, `+` to 3.3V, and GND to GND.
- The same code and config run on Mac and Pi. The audio output is detected automatically.
- mpd keeps running after you exit. Stop it with `pkill -f radio-mpd.conf`.

## Settings (`.env`)

| Key | Meaning |
| --- | --- |
| `PREVENT_SLEEP` | `1` keeps the machine awake while the radio runs |
| `LOG_LEVEL` | `INFO`, or `DEBUG` to also log every MPD command |
| `ENC_LOC_A/B`, `ENC_EPOCH_A/B` | encoder CLK/DT pins (Pi only) |

## Logs

- Printed to the terminal.
- Broadcast as syslog over UDP 514 to the local network. On the log server, copy `rsyslog-radio.conf` to `/etc/rsyslog.d/` and restart rsyslog. Logs land in `/var/log/radio.log`.
- mpd's own log: `/tmp/radio-mpd.log`.

Design decisions: `AGENTS.md`.
