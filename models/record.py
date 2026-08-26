from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def make_record_id(source: str, source_id: str) -> str:
    """Create a compact deterministic identifier without leaking source text."""
    normalized = f"{source.strip().lower()}:{source_id.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


class CorpusRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    source: str
    dataset_name: str
    dataset_version: str
    source_id: str
    source_url: str
    audio_available: bool = True
    audio_path: str | None = None
    audio_format: str | None = None
    audio_duration_seconds: float | None = None
    audio_language: str | None = "id"
    transcript_language: str = "id"
    text: str
    raw_text: str | None = None
    speaker_id: str | None = None
    speaker_name: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    split: str | None = None
    category: str | None = None
    emotion: str | None = None
    license: str
    license_url: str
    license_status: str
    commercial_use_allowed: bool | None = None
    attribution_required: bool | None = None
    share_alike_required: bool | None = None
    original_filename: str
    sha256: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamps(self) -> CorpusRecord:
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("start_ms and end_ms must be provided together")
        if self.start_ms is not None and (self.start_ms < 0 or self.end_ms <= self.start_ms):
            raise ValueError("timestamps must satisfy start_ms >= 0 and end_ms > start_ms")
        audio_fields = (self.audio_path, self.audio_format, self.audio_language, self.sha256)
        if self.audio_available and any(value is None for value in audio_fields):
            raise ValueError("audio fields are required when audio_available is true")
        if not self.audio_available and any(value is not None for value in audio_fields):
            raise ValueError("audio fields must be null when audio_available is false")
        if not self.audio_available and self.audio_duration_seconds is not None:
            raise ValueError("audio duration must be null when audio_available is false")
        return self
