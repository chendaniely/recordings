"""Stored outputs, keyed by recording, note type and model: the same answer every time, with
no network. Demo mode and tests use it (spec §8.4, §17)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from recordings.backends.base import BackendError, Completion, CompletionRequest, model_slug
from recordings.models import TranscriptPayload


class CannedBackend:
    name = "canned"

    def __init__(self, root: Path, aliases: Mapping[str, str] | None = None) -> None:
        self.root = Path(root)
        # Demo outputs are filed by slug ("jfk-rice"); aliases map a recording ID to it.
        self.aliases = dict(aliases or {})

    @classmethod
    def from_dir(cls, root: Path) -> CannedBackend:
        path = Path(root) / "aliases.json"
        aliases = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return cls(root, aliases)

    def _key(self, recording_id: str) -> str:
        return self.aliases.get(recording_id, recording_id)

    def transcribe(
        self, audio: Path, *, recording_id: str, vocabulary: Sequence[str] = ()
    ) -> TranscriptPayload:
        path = self.root / "transcripts" / f"{self._key(recording_id)}.json"
        if not path.is_file():
            raise BackendError(f"no canned transcript for {recording_id} ({path})")
        return TranscriptPayload.model_validate_json(path.read_text(encoding="utf-8"))

    def complete(self, request: CompletionRequest) -> Completion:
        key = self._key(request.recording_id)
        path = self.root / "notes" / key / f"{request.note_type}--{model_slug(request.model)}.md"
        if not path.is_file():
            raise BackendError(f"no canned notes for {request.recording_id} ({path})")
        return Completion(text=path.read_text(encoding="utf-8"), model=request.model, backend=self.name)
