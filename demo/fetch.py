"""Download the demo originals, check their SHA-256, and trim/encode them into
demo/.cache/media/<slug>.<ext>. Needs network and ffmpeg; run only to refresh the demo.

    uv run python demo/fetch.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
# Wikimedia asks for an identifying User-Agent on downloads.
UA = "recordings-demo-fetch/0.1 (https://github.com/chendaniely/recordings)"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        out.write(resp.read())


def encode(src: Path, entry: dict, dest: Path) -> None:
    trim = ["-ss", str(entry["start"])] + (["-t", str(entry["duration"])] if entry["duration"] else [])
    if entry["kind"] == "audio":
        codec = ["-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "64k"]
    else:
        codec = ["-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "slow", "-crf", "30",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "1",
                 "-movflags", "+faststart"]
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), *trim,
                    *codec, str(dest)], check=True)


def main() -> int:
    entries = tomllib.loads((HERE / "sources.toml").read_text())["recording"]
    (CACHE / "originals").mkdir(parents=True, exist_ok=True)
    (CACHE / "media").mkdir(parents=True, exist_ok=True)
    for entry in entries:
        original = CACHE / "originals" / f"{entry['slug']}{Path(entry['url']).suffix}"
        if not original.is_file() or sha256(original) != entry["sha256"]:
            print(f"downloading {entry['slug']}", file=sys.stderr)
            download(entry["url"], original)
        if sha256(original) != entry["sha256"]:
            print(f"{entry['slug']}: checksum mismatch, refusing to continue", file=sys.stderr)
            return 1
        dest = CACHE / "media" / f"{entry['slug']}.{entry['ext']}"
        encode(original, entry, dest)
        print(f"wrote {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
