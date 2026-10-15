"""Measuring media at ingest (spec §9.1.1): its decoded duration, sample rate and channel count.

The duration is always measured on decoded audio, never taken from a container's claim, which for
mp3 and m4a is an estimate from the bitrate:
- **WAV:** the standard library's `wave` counts its frames (method `wave`).
- **Anything else,** or a WAV that `wave` can't read: ffmpeg decodes the first audio stream to
  16 kHz mono PCM and the samples are counted (method `decoded`). That is how audio-router measured
  it: its stages/audio.py decodes to 16 kHz mono WAV first (at 64612df). ffprobe gives the source's
  own sample rate and channel count, and whether it carries video. Both come from ffmpeg, which the
  image installs (https://ffmpeg.org/ffmpeg.html, https://ffmpeg.org/ffprobe.html).

Without ffmpeg and ffprobe the answer is None, never a guess, and `recordings doctor` reports them
missing. `MediaProbe.method` says how a duration was measured, and the import counts each method.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AUDIO_SUFFIXES = frozenset({".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"})
VIDEO_SUFFIXES = frozenset({".mov", ".webm", ".mkv", ".ogv"})
CONTAINER_SUFFIXES = frozenset({".mp4"})  # audio or video: its streams decide
MEDIA_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES | CONTAINER_SUFFIXES
DECODE_RATE = 16_000  # Hz, mono, 16-bit PCM: what audio-router decoded to before measuring


@dataclass(frozen=True)
class MediaProbe:
    duration_ms: int | None
    sample_rate: int | None
    channels: int | None
    has_video: bool = False
    method: str = ""  # how duration_ms was measured: "wave" or "decoded"


def _int(value: object) -> int | None:
    try:
        number = int(float(value))  # ffprobe prints numbers as strings
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _wave(path: Path) -> MediaProbe | None:
    """Count the frames actually there, never the header's claim (§9.1.1): a file that holds
    less than it declares, or nothing at all, isn't measured here and falls back to the decode."""
    try:
        with wave.open(str(path), "rb") as w:
            rate, declared, channels = w.getframerate(), w.getnframes(), w.getnchannels()
            frame_size = channels * w.getsampwidth()
            frames = 0
            while chunk := w.readframes(1 << 14):
                frames += len(chunk) // frame_size
    except (wave.Error, EOFError, OSError, RuntimeError, ValueError, ZeroDivisionError):
        return None  # wave raises RuntimeError for a chunk that overruns the file
    if not rate or not frame_size or frames == 0 or frames != declared:
        return None
    return MediaProbe(
        duration_ms=round(frames * 1000 / rate),
        sample_rate=rate,
        channels=channels or None,
        method="wave",
    )


def _streams(exe: str, path: Path, timeout: float) -> list[dict] | None:
    try:
        proc = subprocess.run(
            [
                exe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):  # ValueError: bad JSON or bad UTF-8
        return None
    if not isinstance(data, dict):
        return None
    return [s for s in data.get("streams") or [] if isinstance(s, dict)]


def decoded_ms(path: Path, *, ffmpeg: str = "ffmpeg", timeout: float = 600) -> int | None:
    """Decode the first audio stream to 16 kHz mono PCM and count its samples. The PCM streams
    through a pipe and is only counted, so a long recording never sits in memory or on disk.
    `timeout` is how long the decode may go without producing any audio: one that stalls is
    killed (None), and a long recording that keeps decoding is never cut short."""
    exe = shutil.which(ffmpeg)
    if exe is None:
        return None
    try:
        proc = subprocess.Popen(
            [
                exe,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(DECODE_RATE),
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    last = time.monotonic()
    done = threading.Event()

    def watch() -> None:
        while not done.wait(min(timeout, 1.0)):
            if time.monotonic() - last > timeout:
                proc.kill()
                return

    threading.Thread(target=watch, daemon=True).start()
    total = 0
    try:
        while chunk := proc.stdout.read(1 << 16):
            total += len(chunk)
            last = time.monotonic()
        code = proc.wait()
    finally:
        done.set()
        proc.stdout.close()
    if code != 0 or total < 2:
        return None
    return round((total // 2) * 1000 / DECODE_RATE)


def probe(
    path: Path, *, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg", timeout: float = 600
) -> MediaProbe | None:
    path = Path(path)
    if path.suffix.lower() == ".wav":
        measured = _wave(path)
        if measured is not None:
            return measured
    exe = shutil.which(ffprobe)
    if exe is None:
        return None
    streams = _streams(exe, path, timeout)
    audio = next((s for s in streams or [] if s.get("codec_type") == "audio"), None)
    if audio is None:
        return None  # unreadable, or no audio stream to decode
    duration_ms = decoded_ms(path, ffmpeg=ffmpeg, timeout=timeout)
    if duration_ms is None:
        return None
    return MediaProbe(
        duration_ms=duration_ms,
        sample_rate=_int(audio.get("sample_rate")),
        channels=_int(audio.get("channels")),
        has_video=any(s.get("codec_type") == "video" for s in streams),
        method="decoded",
    )


def media_kind(path: Path, probed: MediaProbe | None) -> Literal["audio", "video"] | None:
    suffix = Path(path).suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in CONTAINER_SUFFIXES:
        return "video" if probed is not None and probed.has_video else "audio"
    return None
