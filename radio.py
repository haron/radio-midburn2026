"""Two-knob radio: location × epoch stations, switched through static. Design notes in AGENTS.md."""
import atexit
import grp
import json
import logging
import logging.handlers
import os
import random
import re
import socket
import subprocess
import sys
import termios
import threading
import time
import tty
import zlib
from dataclasses import dataclass
from pathlib import Path

import psutil
import serial
from dotenv import load_dotenv
from mpd import MPDClient
from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parent
MUSIC, STATIC = ROOT / "music", ROOT / "static.flac"  # seamless loop built by `make static.flac`
SOCK, CONF = "/tmp/radio-mpd.sock", Path("/tmp/radio-mpd.conf")
TICK = 0.02
WALK_PLAY, WALK_STATIC = (7, 15), (1.5, 5)  # random walk: seconds on a station, then of static
FLICKER, FLICKER_MIN = 0.06, 0.15  # selected LEDs during static: seconds per frame, lowest brightness
PI_MODEL = Path("/proc/device-tree/model")
ON_PI = PI_MODEL.exists() and "Raspberry Pi" in PI_MODEL.read_text()
KEYS = {"\x1b[D": ("location", -1), "a": ("location", -1), "\x1b[C": ("location", 1), "d": ("location", 1),
        "\x1b[A": ("epoch", 1), "w": ("epoch", 1), "\x1b[B": ("epoch", -1), "s": ("epoch", -1)}

log = logging.getLogger("radio")


class BroadcastSyslog(logging.handlers.SysLogHandler):
    """Syslog to each interface's broadcast address (macOS rejects 255.255.255.255); re-read per record."""

    def __init__(self):
        super().__init__(("255.255.255.255", 514))
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.ident = "radio: "

    def emit(self, record):
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and a.broadcast:
                    self.address = (a.broadcast, 514)
                    super().emit(record)

    def handleError(self, record):
        pass  # no network (offline at the event) must not break playback


@dataclass
class Station:
    name: str
    tracks: list[tuple[Path, float]]

    def live(self) -> tuple[int, float]:
        """(track index, offset) that is 'on air' right now; stations run on the wall clock."""
        total = sum(d for _, d in self.tracks)
        pos = (time.time() + zlib.crc32(self.name.encode()) % total) % total
        for i, (_, d) in enumerate(self.tracks):
            if pos < d or i == len(self.tracks) - 1:
                return i, min(pos, d)
            pos -= d


def scan() -> tuple[list[str], list[str], list[list[Station | None]]]:
    """Locations, epochs (union over all locations) and lib[loc][epoch]; None where a location lacks the epoch.
    Only folders with mp3 files count."""
    if not STATIC.is_file():
        sys.exit(f"missing {STATIC}, run `make static.flac`")
    dirs = {d: sorted(d.glob("*.mp3")) for d in MUSIC.glob("*/*") if d.is_dir()}
    dirs = {d: ts for d, ts in dirs.items() if ts}
    if not dirs:
        sys.exit(f"no mp3 files in {MUSIC}/<location>/<epoch>/")
    locs, epochs = (sorted({d.parts[i] for d in dirs}) for i in (-2, -1))
    lib = [[Station(f"{loc}/{ep}", [(t, MP3(t).info.length) for t in dirs[d]]) if (d := MUSIC / loc / ep) in dirs
            else None for ep in epochs] for loc in locs]
    return locs, epochs, lib


def output_type() -> str:
    if sys.platform == "darwin":
        return "osx"
    return "pipewire" if Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"), "pipewire-0").exists() else "alsa"


def mpd_up() -> bool:
    with socket.socket(socket.AF_UNIX) as s:  # a stale socket file stays behind after mpd dies
        return s.connect_ex(SOCK) == 0


def start_mpd(out: str):
    buf = '\tbuffer_time "100000"\n' if out == "alsa" else ""  # MPD's 500ms default delays every volume change
    outputs = "".join(f'audio_output {{\n\ttype "{out}"\n\tname "{n}"\n\tmixer_type "software"\n{buf}}}\n'
                      for n in ("music", "static"))
    CONF.write_text(f'bind_to_address "{SOCK}"\nlog_file "/tmp/radio-mpd.log"\n'
                    f'connection_timeout "31536000"\n{outputs}')  # our clients may sit idle for hours
    # an old mpd may hold a dead audio device (macOS: output switched or slept), so always start a fresh one
    old = [p for p in psutil.process_iter(["cmdline"]) if str(CONF) in (p.info["cmdline"] or [])]
    for p in old:
        p.terminate()
    if psutil.wait_procs(old, timeout=5)[1]:
        sys.exit(f"old mpd didn't exit: {[p.pid for p in old]}")
    if old:
        log.info("stopped old mpd %s", [p.pid for p in old])
    subprocess.run(["mpd", str(CONF)], check=True, start_new_session=True)  # survive our Ctrl+C
    for _ in range(50):  # the daemon returns before its socket is bound
        if mpd_up():
            break
        time.sleep(0.1)
    else:
        sys.exit(f"mpd didn't create {SOCK}, see /tmp/radio-mpd.log")
    log.info("mpd started with %s", CONF)


def keep_awake():
    pid = str(os.getpid())
    cmd = (["caffeinate", "-ims", "-w", pid] if sys.platform == "darwin" else
           ["systemd-inhibit", "--what=idle:sleep", "--who=radio", "--why=playing", "tail", f"--pid={pid}", "-f", "/dev/null"])
    proc = subprocess.Popen(cmd)
    time.sleep(0.3)
    if proc.poll() is not None:
        sys.exit(f"keep-awake failed: {' '.join(cmd)} exited with {proc.returncode}")
    log.info("keep-awake on: %s", " ".join(cmd))


def connect(partition: str | None = None) -> MPDClient:
    client = MPDClient()
    client.connect(SOCK)
    if partition:
        if partition not in {p["partition"] for p in client.listpartitions()}:
            client.newpartition(partition)
        client.partition(partition)
    client.moveoutput(partition or "music")
    return client


class Leds:
    """WLED on USB serial, JSON API. WLED keeps the state, so a frame is sent only when the selection changes."""

    def __init__(self, port: str, locations: int, epochs: int):
        self.locs, self.epochs = led_ranges("LED_LOCATIONS"), led_ranges("LED_EPOCHS")
        if (len(self.locs), len(self.epochs)) != (locations, epochs):
            sys.exit(f"LED_LOCATIONS has {len(self.locs)} ranges for {locations} locations, "
                     f"LED_EPOCHS has {len(self.epochs)} for {epochs} epochs")
        self.spans = []  # adjacent ranges merged: native-USB WLED (ESP32-C3/S3) drops JSON over its 256-byte RX buffer
        for a, b in sorted(self.locs + self.epochs):
            if self.spans and self.spans[-1][1] == a:
                self.spans[-1] = (self.spans[-1][0], b)
            else:
                self.spans.append((a, b))
        self.on, self.off = (os.environ[k] for k in ("LED_ON", "LED_OFF"))
        if not all(re.fullmatch(r"[0-9A-Fa-f]{6}", c) for c in (self.on, self.off)):
            sys.exit(f"LED_ON/LED_OFF must be RRGGBB hex, got {self.on}/{self.off}")
        if not Path(port).exists():
            sys.exit(f"WLED not found at {port}")
        if not os.access(port, os.R_OK | os.W_OK):  # checks this process's groups: a fresh usermod needs a re-login
            group = grp.getgrgid(os.stat(port).st_gid).gr_name
            sys.exit(f"no read/write access to {port}: run `sudo usermod -aG {group} $USER` and log in again")
        self.ser = serial.Serial(baudrate=115200, timeout=1, write_timeout=1)
        self.ser.port, self.ser.dtr, self.ser.rts = port, False, False  # set before open, or the ESP32 auto-resets
        self.ser.open()
        for _ in range(5):  # retries cover a board that rebooted on open anyway
            self.ser.reset_input_buffer()
            self.ser.write(b'{"v":true}\n')
            if b'"on":' in self.ser.read_until(b'"on":'):
                break
        else:
            sys.exit(f"no WLED reply on {port}: check Config > Sync Interfaces > Serial baud 115200")
        log.info("leds on: WLED at %s, locations %s, epochs %s", port, self.locs, self.epochs)

    def show(self, loc: int, epoch: int, flicker: bool):
        (la, lb), (ea, eb) = self.locs[loc], self.epochs[epoch]
        on = self.on
        if flicker:
            k = random.uniform(FLICKER_MIN, 1)
            on = "".join(f"{round(int(on[i:i + 2], 16) * k):02X}" for i in (0, 2, 4))
        dim = [x for a, b in self.spans for x in (a, b, self.off)]
        seg = {"id": 0, "fx": 0, "i": dim + [la, lb, on, ea, eb, on]}
        self.ser.write(json.dumps({"on": True, "seg": [seg]}).encode() + b"\n")


def led_ranges(key: str) -> list[tuple[int, int]]:
    """KEY='0,6', KEY_LEN='6' -> [(0, 6), (6, 12)], end-exclusive like WLED's "i"."""
    n = int(os.environ[f"{key}_LEN"])
    return [(a, a + n) for a in map(int, os.environ[key].split(","))]


class Radio:
    def __init__(self, locs: list[str], epochs: list[str], lib: list[list[Station | None]], leds: Leds | None):
        self.locs, self.epochs, self.lib, self.leds = locs, epochs, lib, leds
        self.loc, self.epoch = self.random_station(None)  # every start tunes in somewhere new
        self.fade, self.hold = float(os.environ["STATIC_FADE"]), float(os.environ["STATIC_HOLD"])
        if not (self.fade > 0 and self.hold > 0):  # hold > 0 keeps the station load inside the silence
            sys.exit(f"STATIC_FADE and STATIC_HOLD must be > 0, got {self.fade}/{self.hold}")
        self.walk_idle = float(os.environ["WALK_IDLE"])
        now = time.monotonic()
        self.tune_until = now + self.fade + self.hold  # power-on tunes in through static
        self.last_activity, self.walking, self.walk_next = now, False, 0.0
        self.lock = threading.Lock()
        self.music, self.static = connect(), connect("static")
        for c in (self.music, self.static):
            c.stop()
            c.clear()
            c.setvol(0)
        self.music.repeat(1), self.music.single(0), self.music.consume(0), self.music.crossfade(0)
        self.static.add(f"file://{STATIC}"), self.static.repeat(1), self.static.single(1)
        self.static.play()  # never paused: resuming reopens the ALSA device, which delays the static
        self.vol = {"music": 0.0, "static": 0.0}

    def activity(self):
        """Any knob or key: back to normal mode."""
        with self.lock:
            self.last_activity = time.monotonic()
            if self.walking:
                self.walking = False
                log.info("random walk off")

    def start_walk(self):
        with self.lock:
            if not self.walking:
                self.walking, self.walk_next = True, time.monotonic()
                log.info("random walk on")

    def random_station(self, exclude: tuple[int, int] | None) -> tuple[int, int]:
        return random.choice([(li, ei) for li, row in enumerate(self.lib) for ei, st in enumerate(row)
                              if st and (li, ei) != exclude])

    def walk_step(self, now: float):
        """Under the lock: jump to a random other station, through WALK_STATIC seconds of static."""
        self.loc, self.epoch = self.random_station((self.loc, self.epoch))
        self.tune_until = now + self.fade + random.uniform(*WALK_STATIC)
        self.walk_next = self.tune_until + random.uniform(*WALK_PLAY)
        log.info("random walk -> %s", self.name())

    def turn(self, knob: str, delta: int):
        self.activity()
        with self.lock:
            attr, count = ("loc", len(self.locs)) if knob == "location" else ("epoch", len(self.epochs))
            new = getattr(self, attr) + delta
            if not 0 <= new < count:  # no wrapping: turning past an end does nothing, not even static
                log.info("%s %+d ignored, already at %s", knob, delta, self.name())
                return
            setattr(self, attr, new)
            self.tune_until = time.monotonic() + self.fade + self.hold
            name = self.name()
        log.info("%s %+d -> %s%s", knob, delta, name, "" if self.lib[self.loc][self.epoch] else " (no station)")

    def name(self) -> str:
        return f"{self.locs[self.loc]}/{self.epochs[self.epoch]}"

    def load(self, st: Station):
        i, offset = st.live()
        self.music.clear()
        for path, _ in st.tracks:
            self.music.add(f"file://{path}")
        self.music.seek(i, f"{offset:.3f}")
        log.info("tuned %s: track %d/%d %r @ %d:%02d", st.name, i + 1, len(st.tracks), st.tracks[i][0].name,
                 offset // 60, offset % 60)

    def ramp(self, name: str, client: MPDClient, target: int):
        v = self.vol[name]
        nv = min(target, v + 100 * TICK / self.fade) if target > v else max(target, v - 100 * TICK / self.fade)
        if nv == v:
            return
        client.setvol(round(nv))
        self.vol[name] = nv

    def run(self):
        loaded, phase, lit, flick_at = None, None, None, 0.0
        while True:
            now = time.monotonic()
            if not self.walking and now > self.last_activity + self.walk_idle:
                self.start_walk()
            with self.lock:
                if self.walking and now >= self.walk_next:
                    self.walk_step(now)
                st, tune_until, pos = self.lib[self.loc][self.epoch], self.tune_until, (self.loc, self.epoch)
            # no station at this location and epoch: static only
            tuning = st is None or now < tune_until
            # the scale follows the knob right away, not after the static, and flickers while it lasts
            if self.leds and ((pos, tuning) != lit or tuning and now >= flick_at):
                self.leds.show(*pos, tuning)
                lit, flick_at = (pos, tuning), now + FLICKER
            if not tuning and st is not loaded:  # music is silent by now: fade < fade + hold
                self.load(st)
                loaded = st
            self.ramp("music", self.music, 0 if tuning else 100)
            self.ramp("static", self.static, 100 if tuning else 0)
            m = self.vol["music"]
            new_phase = ("fading out" if m else "static") if tuning else (f"playing {st.name}" if m == 100 else "fading in")
            if new_phase != phase:
                log.info(new_phase)
                phase = new_phase
            time.sleep(TICK)

    def stop(self):
        self.music.stop()
        self.static.stop()


def keyboard(radio: Radio):
    fd = sys.stdin.fileno()
    atexit.register(termios.tcsetattr, fd, termios.TCSADRAIN, termios.tcgetattr(fd))
    tty.setcbreak(fd)

    def read():
        while True:
            for key in re.findall(r"\x1b\[[A-D]|[^\x1b]", os.read(fd, 64).decode(errors="ignore")):
                if (k := key if len(key) > 1 else key.lower()) == "t":
                    radio.start_walk()
                elif k in KEYS:
                    radio.turn(*KEYS[k])
                else:
                    radio.activity()

    threading.Thread(target=read, daemon=True).start()
    log.info("keyboard on: <-/-> or A/D location, up/down or W/S epoch, T random walk")


def encoders(radio: Radio) -> list:
    from gpiozero import RotaryEncoder

    encs = []
    for knob, env in (("location", "ENC_LOC"), ("epoch", "ENC_EPOCH")):
        enc = RotaryEncoder(int(os.environ[f"{env}_A"]), int(os.environ[f"{env}_B"]))
        enc.when_rotated_clockwise = lambda k=knob: radio.turn(k, 1)
        enc.when_rotated_counter_clockwise = lambda k=knob: radio.turn(k, -1)
        encs.append(enc)
    log.info("encoders on: location %s/%s, epoch %s/%s", os.environ["ENC_LOC_A"], os.environ["ENC_LOC_B"],
             os.environ["ENC_EPOCH_A"], os.environ["ENC_EPOCH_B"])
    return encs


def main():
    load_dotenv(ROOT / ".env")
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    syslog = BroadcastSyslog()
    syslog.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=os.environ["LOG_LEVEL"], handlers=[console, syslog])

    locs, epochs, lib = scan()
    for loc, stations in zip(locs, lib):
        log.info("library %s: %s", loc, ", ".join(f"{ep} ({len(st.tracks) if st else 0} tracks)"
                                                   for ep, st in zip(epochs, stations)))
    out = output_type()
    log.info("platform %s, raspberry pi: %s, mpd output: %s", sys.platform, ON_PI, out)
    if os.environ["PREVENT_SLEEP"] == "1":
        keep_awake()
    else:
        log.info("keep-awake off")
    if port := os.environ["WLED_PORT"]:
        leds = Leds(port, len(locs), len(epochs))
    else:
        leds = None
        log.info("leds off: WLED_PORT is empty")
    start_mpd(out)

    radio = Radio(locs, epochs, lib, leds)
    if sys.stdin.isatty():
        keyboard(radio)
    encs = encoders(radio) if ON_PI else []  # noqa: F841 keep references alive
    try:
        radio.run()
    except KeyboardInterrupt:
        log.info("stopping")
        radio.stop()


if __name__ == "__main__":
    main()
