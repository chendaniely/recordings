"""One interface for every model backend: Spark, Claude, Canned (spec §8.4)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from recordings.models import TranscriptPayload


@dataclass(frozen=True)
class CompletionRequest:
    prompt: str
    model: str
    recording_id: str
    note_type: str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str  # the model that actually answered, which may differ from the one asked
    backend: str


class BackendError(RuntimeError):
    """A backend could not produce an output. Always loud, never a silent truncation."""


@runtime_checkable
class Backend(Protocol):
    name: str

    def transcribe(
        self, audio: Path, *, recording_id: str, vocabulary: Sequence[str] = ()
    ) -> TranscriptPayload: ...

    def complete(self, request: CompletionRequest) -> Completion: ...


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9.@+_-]+", "_", model).strip("_")
