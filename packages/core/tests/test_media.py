import io
import json
import os
import wave

from recordings import media
from recordings.media import MediaProbe, media_kind, probe


def _wav(path, seconds=1.5, rate=22050, channels=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * int(seconds * rate))
    path.write_bytes(buf.getvalue())
    return path


def _stand_ins(tmp_path, monkeypatch, *, pcm_bytes, streams):
    """Stand-ins for ffprobe and ffmpeg on PATH. ffprobe prints what its `-of json` prints, with a
    container duration of 61.234 s that must never be used; ffmpeg "decodes" to `pcm_bytes` bytes
    of 16 kHz mono 16-bit PCM."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    answer = {"streams": streams, "format": {"duration": "61.234"}}
    (bin_dir / "ffprobe").write_text(f"#!/bin/sh\necho '{json.dumps(answer)}'\n", encoding="utf-8")
    (bin_dir / "ffmpeg").write_text(f"#!/bin/sh\nhead -c {pcm_bytes} /dev/zero\n", encoding="utf-8")
    for exe in bin_dir.iterdir():
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_a_wav_file_is_measured_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert probe(_wav(tmp_path / "a.wav")) == MediaProbe(duration_ms=1500, sample_rate=22050,
                                                         channels=2, method="wave")


def test_anything_else_needs_ffmpeg_and_is_never_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    (tmp_path / "a.mp3").write_bytes(b"ID3 not really")
    assert probe(tmp_path / "a.mp3") is None
    (tmp_path / "broken.wav").write_bytes(b"RIFF")
    assert probe(tmp_path / "broken.wav") is None


def test_the_duration_is_decoded_never_the_containers_claim(tmp_path, monkeypatch):
    # why: §9.1.1. reconcile's timing check compares Plaud's duration against this one, and an
    # mp3's or m4a's container duration is only an estimate. 64000 bytes of PCM is 2000 ms.
    _stand_ins(tmp_path, monkeypatch, pcm_bytes=64000,
               streams=[{"codec_type": "audio", "sample_rate": "44100", "channels": 1}])
    (tmp_path / "a.m4a").write_bytes(b"not really")
    assert probe(tmp_path / "a.m4a") == MediaProbe(duration_ms=2000, sample_rate=44100,
                                                   channels=1, has_video=False, method="decoded")


def test_a_failed_decode_or_a_file_with_no_audio_is_not_measured(tmp_path, monkeypatch):
    _stand_ins(tmp_path, monkeypatch, pcm_bytes=0,
               streams=[{"codec_type": "audio", "sample_rate": "44100", "channels": 1}])
    (tmp_path / "a.m4a").write_bytes(b"not really")
    assert probe(tmp_path / "a.m4a") is None
    (tmp_path / "bin" / "ffprobe").write_text(
        "#!/bin/sh\necho '{\"streams\": [{\"codec_type\": \"video\"}]}'\n", encoding="utf-8")
    assert probe(tmp_path / "a.m4a") is None


def test_the_kind_comes_from_the_suffix_and_for_mp4_from_the_streams(tmp_path):
    assert media_kind(tmp_path / "a.m4a", None) == "audio"
    assert media_kind(tmp_path / "a.MOV", None) == "video"
    assert media_kind(tmp_path / "a.mp4", MediaProbe(1, 1, 1, has_video=True)) == "video"
    assert media_kind(tmp_path / "a.mp4", MediaProbe(1, 1, 1)) == "audio"
    assert media_kind(tmp_path / "a.txt", None) is None
