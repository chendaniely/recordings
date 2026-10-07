import json
from pathlib import Path

import pytest

from recordings.backends import Backend, BackendError, CannedBackend, CompletionRequest, model_slug

RID = "20261006T140003-0700_3fa91c2e"


def canned(tmp_path: Path) -> Path:
    root = tmp_path / "canned"
    (root / "transcripts").mkdir(parents=True)
    (root / "transcripts" / "jfk.json").write_text(json.dumps(
        {"language": "en", "segments": [{"start": 0, "end": 2, "text": "We choose"}]}))
    (root / "notes" / "jfk").mkdir(parents=True)
    (root / "notes" / "jfk" / "conference-talk--claude-opus-5-5.md").write_text("# Notes\n")
    (root / "aliases.json").write_text(json.dumps({RID: "jfk"}))
    return root


def test_it_satisfies_the_backend_protocol(tmp_path):
    backend: Backend = CannedBackend.from_dir(canned(tmp_path))
    assert backend.name == "canned"


def test_transcribe_returns_the_stored_transcript_by_alias(tmp_path):
    backend = CannedBackend.from_dir(canned(tmp_path))
    payload = backend.transcribe(Path("unused.mp3"), recording_id=RID)
    assert payload.segments[0].text == "We choose"


def test_complete_returns_stored_notes_and_the_model_asked(tmp_path):
    backend = CannedBackend.from_dir(canned(tmp_path))
    req = CompletionRequest(prompt="ignored", model="claude-opus-5-5", recording_id=RID,
                            note_type="conference-talk")
    out = backend.complete(req)
    assert (out.text, out.model, out.backend) == ("# Notes\n", "claude-opus-5-5", "canned")


def test_missing_canned_output_is_a_clear_error(tmp_path):
    backend = CannedBackend.from_dir(canned(tmp_path))
    with pytest.raises(BackendError, match="no canned notes"):
        backend.complete(CompletionRequest("p", "qwen3.6-35b-a3b", RID, "conference-talk"))
    with pytest.raises(BackendError, match="no canned transcript"):
        backend.transcribe(Path("x"), recording_id="20200101T000000+0000_00000000")


def test_model_slug_is_filename_safe():
    assert model_slug("mlx-community/whisper large") == "mlx-community_whisper_large"
