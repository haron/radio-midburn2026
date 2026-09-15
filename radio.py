"""Two-knob radio: location × epoch stations, switched through static. Design notes in AGENTS.md."""
import atexit
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
from dotenv import load_dotenv
from mpd import MPDClient
from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parent
MUSIC, STATIC = ROOT / "music", ROOT / "static.flac"  # seamless loop built by `make static.flac`
SOCK, CONF = "/tmp/radio-mpd.sock", Path("/tmp/radio-mpd.conf")
EPOCHS, FADE, HOLD, TICK = 3, 0.5, 2.0, 0.02
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
    if mpd_up():
        log.info("mpd already running on %s", SOCK)
    else:
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


class Radio:
    def __init__(self, lib: list[list[Station]]):
        self.lib, self.loc, self.epoch = lib, 0, 0
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
        nv = min(target, v + 100 * TICK / FADE) if target > v else max(target, v - 100 * TICK / FADE)
        if nv == v:
            return
        if name == "static" and v == 0:
            client.play()
        client.setvol(round(nv))
        if name == "static" and nv == 0:
            client.pause()
        self.vol[name] = nv

    def run(self):
        loaded, phase = None, None
        while True:
            with self.lock:
                st, last_turn = self.lib[self.loc][self.epoch], self.last_turn
            tuning = time.monotonic() < last_turn + FADE + HOLD
            if not tuning and st is not loaded:  # music is silent by now: FADE < FADE + HOLD
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
    start_mpd(out)

    radio = Radio(lib)
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
