# Radio: design decisions

Two-knob radio (Raspberry Pi, KY-040 encoders): location × epoch stations,
switched through static. Run: `uv run radio.py` (same on Mac and Pi). Settings
are in `.env` (see `.env.example`).

- **No MPD crossfade:** MPD skips crossfades for songs under 20s
  (`MIN_TOTAL_TIME`), and its `crossfade` takes whole seconds. Static plays in a
  second MPD partition (`static`) with its own output. Python ramps both
  partitions’ software-mixer volumes over 0.5s, and the seek to the new station
  happens while the static covers it. Static plays nonstop at volume 0 between
  turns, since unpausing reopens the ALSA device (audible lag), and ALSA
  `buffer_time` is 100ms, as the 500ms default delays every volume change.
- **Static loop:** MPD plays `static.flac` on `repeat single`, built from
  `static.mp3` by `make static.flac` (gitignored). The MP3 can’t loop as is: its
  ~1s silent edges and encoder padding stutter on every wrap. The build trims
  the edges, crossfades the tail into the head and adds 9dB. Trim points and
  gain are hardcoded for this clip, so re-measure them (`silencedetect`,
  `astats`) when replacing it.
- **Live stations:** a station’s position comes from the wall clock,
  `(time.time() + crc32(name)) % total`. No per-station state, so switching back
  resumes “live”. Each turn restarts the 2s static hold, so the new station
  loads only once the knob stops.
- **Random walk:** after `WALK_IDLE` seconds without activity, or on T, the
  radio hops between random stations (10–20s on each, 2–7s of static). It runs
  in `run()`, so serial and MPD stay on one thread. Any knob or key returns to
  normal mode, continuing from the station the walk stopped on. Startup also
  tunes in to a random station.
- **Library:** `music/<location>/<epoch>/*.mp3`, all sorted by name (use
  `01_Paris`-style prefixes). Only folders with MP3s count. The epoch knob spans
  the union of all locations’ epoch folders, so the LED scale is the same for
  every location. A missing location × epoch plays static only. The knobs don’t
  wrap: a turn past the first or last position is ignored, with no static.
- **MPD without a database:** unix socket only (`/tmp/radio-mpd.sock`), so
  tracks can be added as `file://` URIs. No music_directory, no `update`.
- **Platform auto-detect:** radio.py writes `/tmp/radio-mpd.conf` on start.
  Output is `osx` on Mac, `pipewire` if its socket exists, otherwise `alsa`.
  Encoders are used on a Pi, the keyboard whenever stdin is a TTY.
- **mpd lifecycle:** restarted on every run, since a long-lived mpd on macOS
  keeps a dead CoreAudio device after the output changes (`OSStatus 560947818` =
  `!obj`). Started detached (new session), so Ctrl+C reaches only radio.py,
  which stops playback through it. `connection_timeout` is huge because the
  clients sit idle between turns.
- **Logging:** syslog over UDP broadcast to every interface’s broadcast address
  (macOS rejects 255.255.255.255), so there’s no log-server address to
  configure. Send errors are ignored, since being offline at the event must not
  stop playback. `rsyslog-radio.conf` is a server example.
- **Keep awake:** `PREVENT_SLEEP=1` runs `caffeinate -w pid` or
  `systemd-inhibit … tail --pid`, which exit together with radio.py.
- **Scale lights over USB serial, not WiFi:** WLED over WiFi lags and needs a
  network the event won’t have. It uses WLED’s JSON API, not Adalight, because
  WLED keeps a JSON state without a keepalive, so a frame goes out only when the
  knob moves. DTR/RTS are set low before the port opens, or the ESP32’s
  auto-reset reboots it. Serial writes happen in `run()`, not `turn()`, to keep
  gpiozero callbacks off the wire. A native-USB ESP32 (C3/S3) drops JSON over
  its 256-byte RX buffer, so adjacent LED ranges go out merged and the ranges
  should be contiguous. During static the selected LEDs flicker, with a frame
  every 60ms.
- **Dial:** `scale.py` builds `radio.svg` from the `music/` folders, so the city
  order matches the knob. Cities are evenly spaced, so each tick can sit over
  its LED pair. Epoch labels come from `EPOCHS` in `scale.py`, not from folder
  names (the folders stay `1_early`…). Labels drop diacritics. Text is turned
  into curves by `rsvg-convert -f svg`, and the PNG and PDF are rendered from
  that file, so the preview is exactly what gets printed. The curves depend on
  the local fonts, so build on a Mac. The PDF’s 3 mm bleed is filled by the
  background rect, which cairo makes larger than the page.
- **Fail fast:** settings are read with `os.environ[...]`, and there are no
  fallbacks.
- **Python \<3.13:** `lgpio` has prebuilt aarch64 wheels (liblgpio linked in)
  only up to cp312. On 3.13 it builds from source and needs swig, Python headers
  and liblgpio, which Debian doesn’t package. uv fetches a managed 3.12.
- **systemd service:** `radio.service` is linked from `/opt/radio` by
  `provision.sh` and runs with `Restart=always`. mpd shares its cgroup, so a
  stop kills mpd too. `make deploy` syncs, reloads and restarts it, and
  `make remote` stops it to run in the foreground.
- **Pi never sleeps or hangs:** `provision.sh` masks the sleep targets, so it
  doesn’t rely on Armbian’s `sleep.conf.d`. It also turns on systemd’s hardware
  watchdog (`RuntimeWatchdogSec=15s`, BCM2835) and `kernel.panic=10`, so a
  frozen or panicked kernel reboots and the service comes back by itself.
  `PREVENT_SLEEP` is for the Mac. WiFi powersave is already off in Armbian’s
  NetworkManager config, and the ESP32’s USB port isn’t autosuspended.
- **Health check:** `make healthcheck` reads the firmware flags with `vcgencmd`
  (`libraspberrypi-bin`), since Armbian’s mainline kernel has no `get_throttled`
  in sysfs. The 3B+ hits its 60°C soft limit near idle even with a heatsink, so
  `provision.sh` sets `temp_soft_limit=70` (the maximum), and the script treats
  the soft limit as a warning only.
