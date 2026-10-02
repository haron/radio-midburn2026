# Radio: design decisions

Two-knob radio (Raspberry Pi, KY-040 encoders): location × epoch stations,
switched through static. Run: `uv run radio.py` (same on Mac and Pi). Settings
are in `.env` (see `.env.example`).

- **No MPD crossfade:** MPD skips crossfades for songs under 20s
  (`MIN_TOTAL_TIME`), and its `crossfade` takes whole seconds. Static plays in a
  second MPD partition (`static`) with its own output. Python ramps both
  partitions' software-mixer volumes over 0.5s, and the seek to the new station
  happens while the static covers it. Static plays nonstop at volume 0 between
  turns, since unpausing reopens the ALSA device (audible lag), and ALSA
  `buffer_time` is 100ms, as the 500ms default delays every volume change.
- **Static loop:** `static.flac` is built from `static.mp3` by
  `make static.flac`. The source clip has \~90ms of silence at its edges, which
  stuttered on every `repeat single` wrap, so the build trims the edges and
  crossfades the tail into the head. It's FLAC because MP3 re-adds encoder
  padding.
- **Live stations:** a station's position comes from the wall clock,
  `(time.time() + crc32(name)) % total`. No per-station state, so switching back
  resumes "live". Each turn restarts the 2s static hold, so the new station
  loads only once the knob stops.
- **Random walk:** after `WALK_IDLE` seconds without activity, or on T, the
  radio hops between random stations (5–10s on each, 2–5s of static). It runs
  in `run()`, so serial and MPD stay on one thread. Any knob or key returns
  to normal mode, continuing from the station the walk stopped on. Startup
  also tunes in to a random station.
- **Library:** `music/<location>/<epoch>/*.mp3`, all sorted by name (use
  `01_Paris`-style prefixes). Only folders with MP3s count. The epoch knob
  spans the union of all locations' epoch folders, so the LED scale is the same for every location. A
  missing location × epoch plays static only. The knobs don't wrap: a turn past
  the first or last position is ignored, with no static.
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
- **Logging:** syslog over UDP broadcast to every interface's broadcast address
  (macOS rejects 255.255.255.255), so there's no log-server address to
  configure. Send errors are ignored, since being offline at the event must not
  stop playback. `rsyslog-radio.conf` is a server example.
- **Keep awake:** `PREVENT_SLEEP=1` runs `caffeinate -w pid` or
  `systemd-inhibit … tail --pid`, which exit together with radio.py.
- **Scale lights over USB serial, not WiFi:** WLED over WiFi lags and needs a
  network the event won't have. It uses WLED's JSON API, not Adalight, because
  WLED keeps a JSON state without a keepalive, so a frame goes out only when the
  knob moves. DTR/RTS are set low before the port opens, or the ESP32's
  auto-reset reboots it. Serial writes happen in `run()`, not `turn()`, to keep
  gpiozero callbacks off the wire.
- **Fail fast:** settings are read with `os.environ[...]`, and there are no
  fallbacks.
- **Python <3.13:** `lgpio` has prebuilt aarch64 wheels (liblgpio linked in)
  only up to cp312. On 3.13 it builds from source and needs swig, Python
  headers and liblgpio, which Debian doesn't package. uv fetches a managed 3.12.
- **systemd service:** `radio.service` is linked from `/opt/radio` by
  `provision.sh` and runs with `Restart=always`. mpd shares its cgroup, so a
  stop kills mpd too. `make deploy` syncs, reloads and restarts it, and
  `make remote` stops it to run in the foreground.
