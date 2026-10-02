# /// script
# requires-python = ">=3.12"
# dependencies = ["yt-dlp"]
# ///
"""Download songs.json into music/<location>/<epoch>/NN_<artist> - <title>.mp3 (first YouTube search hit), then
remove mp3s that are no longer in songs.json. Needs ffmpeg."""
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from yt_dlp import YoutubeDL

ROOT = Path(__file__).resolve().parent


def download(path: Path, query: str):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    opts = {"format": "bestaudio/best", "outtmpl": str(path.with_suffix(".%(ext)s")), "noplaylist": True,
            "quiet": True, "noprogress": True, "no_warnings": True,
            # same as `yt-dlp --cookies-from-browser safari --js-runtimes node --remote-components ejs:github`
            "cookiesfrombrowser": ("safari", None, None, None), "js_runtimes": {"node": {"path": None}},
            "remote_components": ["ejs:github"],
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]}
    with YoutubeDL(opts) as ydl:
        if ydl.download([f"ytsearch1:{query}"]):
            raise RuntimeError(f"download failed: {query}")
    print(path.relative_to(ROOT))


jobs = [(ROOT / "music" / loc / ep / re.sub(r'[/\\:*?"<>|]', "_", f"{i:02}_{s['artist']} - {s['title']}.mp3"),
         f"{s['artist']} - {s['title']} official audio")
        for loc, eps in json.loads((ROOT / "songs.json").read_text()).items()
        for ep, songs in eps.items() for i, s in enumerate(songs, 1)]
with ThreadPoolExecutor(max_workers=4) as pool:
    for f in [pool.submit(download, *j) for j in jobs]:
        f.result()
keep = {unicodedata.normalize("NFC", str(path)) for path, _ in jobs}  # macOS may store names decomposed (NFD)
for stale in sorted(p for p in (ROOT / "music").glob("*/*/*.mp3") if unicodedata.normalize("NFC", str(p)) not in keep):
    stale.unlink()
    print("removed", stale.relative_to(ROOT))
