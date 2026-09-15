"""Two-knob radio: location × epoch stations, switched through static. Design notes in AGENTS.md."""
import atexit
import grp
import json
import logging
import logging.handlers
import os
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
EPOCHS, TICK = 3, 0.02
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


def scan() -> list[list[Station]]:
    if not STATIC.is_file():
        sys.exit(f"missing {STATIC}, run `make static.flac`")
    lib = []
    for loc in sorted(p for p in MUSIC.iterdir() if p.is_dir()):
        epochs = sorted(p for p in loc.iterdir() if p.is_dir())
        if len(epochs) != EPOCHS:
            sys.exit(f"{loc.name}: expected {EPOCHS} epochs, found {[e.name for e in epochs]}")
        lib.append([Station(f"{loc.name}/{ep.name}", [(t, MP3(t).info.length) for t in sorted(ep.glob("*.mp3"))])
                    for ep in epochs])
        for st in lib[-1]:
            if not st.tracks:
                sys.exit(f"{st.name}: no mp3 files")
    if not lib:
        sys.exit(f"no locations in {MUSIC}")
    return lib


def output_type() -> str:
    if sys.platform == "darwin":
        return "osx"
    return "pipewire" if Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"), "pipewire-0").exists() else "alsa"


def mpd_up() -> bool:
    with socket.socket(socket.AF_UNIX) as s:  # a stale socket file stays behind after mpd dies
        return s.connect_ex(SOCK) == 0


def start_mpd(out: str):
    outputs = "".join(f'audio_output {{\n\ttype "{out}"\n\tname "{n}"\n\tmixer_type "software"\n}}\n'
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

    def __init__(self, port: str, locations: int):
        self.locs, self.epochs = led_ranges("LED_LOCATIONS"), led_ranges("LED_EPOCHS")
        if (len(self.locs), len(self.epochs)) != (locations, EPOCHS):
            sys.exit(f"LED_LOCATIONS has {len(self.locs)} ranges for {locations} locations, "
                     f"LED_EPOCHS has {len(self.epochs)} for {EPOCHS} epochs")
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

    def show(self, loc: int, epoch: int):
        (la, lb), (ea, eb) = self.locs[loc], self.epochs[epoch]
        dim = [x for a, b in self.locs + self.epochs for x in (a, b + 1, self.off)]
        seg = {"id": 0, "fx": 0, "i": dim + [la, lb + 1, self.on, ea, eb + 1, self.on]}
        self.ser.write(json.dumps({"on": True, "seg": [seg]}).encode() + b"\n")


def led_ranges(key: str) -> list[tuple[int, int]]:
    """'0-5,6-11' -> [(0, 5), (6, 11)], inclusive."""
    return [tuple(map(int, r.split("-"))) for r in os.environ[key].split(",")]


class Radio:
    def __init__(self, lib: list[list[Station]], leds: Leds | None):
        self.lib, self.leds, self.loc, self.epoch = lib, leds, 0, 0
        self.fade, self.hold = float(os.environ["STATIC_FADE"]), float(os.environ["STATIC_HOLD"])
        if not (self.fade > 0 and self.hold > 0):  # hold > 0 keeps the station load inside the silence
            sys.exit(f"STATIC_FADE and STATIC_HOLD must be > 0, got {self.fade}/{self.hold}")
        self.last_turn = time.monotonic()  # power-on tunes in through static
        self.lock = threading.Lock()
        self.music, self.static = connect(), connect("static")
        for c in (self.music, self.static):
            c.stop()
            c.clear()
            c.setvol(0)
        self.music.repeat(1), self.music.single(0), self.music.consume(0), self.music.crossfade(0)
        self.static.add(f"file://{STATIC}"), self.static.repeat(1), self.static.single(1)
        self.vol = {"music": 0.0, "static": 0.0}

    def turn(self, knob: str, delta: int):
        with self.lock:
            attr, count = ("loc", len(self.lib)) if knob == "location" else ("epoch", EPOCHS)
            new = getattr(self, attr) + delta
            if not 0 <= new < count:  # no wrapping: turning past an end does nothing, not even static
                log.info("%s %+d ignored, already at %s", knob, delta, self.lib[self.loc][self.epoch].name)
                return
            setattr(self, attr, new)
            self.last_turn = time.monotonic()
            name = self.lib[self.loc][self.epoch].name
        log.info("%s %+d -> %s", knob, delta, name)

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
        if name == "static" and v == 0:
            client.play()
        client.setvol(round(nv))
        if name == "static" and nv == 0:
            client.pause()
        self.vol[name] = nv

    def run(self):
        loaded, phase, lit = None, None, None
        while True:
            with self.lock:
                st, last_turn, pos = self.lib[self.loc][self.epoch], self.last_turn, (self.loc, self.epoch)
            if self.leds and pos != lit:  # the scale follows the knob right away, not after the static
                self.leds.show(*pos)
                lit = pos
            tuning = time.monotonic() < last_turn + self.fade + self.hold
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
                if (k := key if len(key) > 1 else key.lower()) in KEYS:
                    radio.turn(*KEYS[k])

    threading.Thread(target=read, daemon=True).start()
    log.info("keyboard on: <-/-> or A/D location, up/down or W/S epoch")


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

    lib = scan()
    for stations in lib:
        log.info("library %s", ", ".join(f"{st.name} ({len(st.tracks)} tracks)" for st in stations))
    out = output_type()
    log.info("platform %s, raspberry pi: %s, mpd output: %s", sys.platform, ON_PI, out)
    if os.environ["PREVENT_SLEEP"] == "1":
        keep_awake()
    else:
        log.info("keep-awake off")
    if port := os.environ["WLED_PORT"]:
        leds = Leds(port, len(lib))
    else:
        leds = None
        log.info("leds off: WLED_PORT is empty")
    start_mpd(out)

    radio = Radio(lib, leds)
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
