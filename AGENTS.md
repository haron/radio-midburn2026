# Radio: design decisions

Two-knob radio (Raspberry Pi, KY-040 encoders): location × epoch stations,
switched through static. Run: `uv run radio.py` (same on Mac and Pi). Settings
are in `.env` (see `.env.example`).

- **No MPD crossfade:** MPD skips crossfades for songs under 20s
  (`MIN_TOTAL_TIME`), and its `crossfade` takes whole seconds. Static plays in a
  second MPD partition (`static`) with its own output. Python ramps both
  partitions' software-mixer volumes over 0.5s, and the seek to the new station
  happens while the static covers it.
- **Static loop:** `static.flac` is built from `static.mp3` by
  `make static.flac`. The source clip has \~90ms of silence at its edges, which
  stuttered on every `repeat single` wrap, so the build trims the edges and
  crossfades the tail into the head. It's FLAC because MP3 re-adds encoder
  padding.
- **Live stations:** a station's position comes from the wall clock,
  `(time.time() + crc32(name)) % total`. No per-station state, so switching back
  resumes "live". Each turn restarts the 2s static hold, so the new station
  loads only once the knob stops.
- **Library:** `music/<location>/<epoch>/*.mp3`, all sorted by name (use
  `01_Paris`-style prefixes). Exactly 3 epochs per location, checked before
  start, and startup fails on any mismatch. The knobs don't wrap: a turn past
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
