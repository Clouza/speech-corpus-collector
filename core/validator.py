from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import soundfile as sf

from core.storage import sha256_file
from models.record import CorpusRecord


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    duration_seconds: float | None = None


def validate_record(
    record: CorpusRecord,
    storage_root: Path,
    allow_unknown: bool = False,
    precomputed_sha256: str | None = None,
) -> ValidationResult:
    errors: list[str] = []
    if not record.text.strip():
        errors.append("transcript is empty")
    if record.audio_available and record.audio_language != "id":
        errors.append("audio language is not Indonesian")
    if record.transcript_language != "id":
        errors.append("transcript language is not Indonesian")
    if not record.source_id.strip():
        errors.append("source ID is empty")
    if not record.source_url.strip():
        errors.append("source URL is empty")
    if record.license_status != "known" and not allow_unknown:
        errors.append("license is unknown or prohibited")
    if record.start_ms is not None and (record.start_ms < 0 or record.end_ms is None or record.end_ms <= record.start_ms):
        errors.append("timestamps are invalid")

    duration: float | None = None
    if not record.audio_available:
        return ValidationResult(not errors, errors, None)

    audio_path = Path(record.audio_path or "")
    if not audio_path.is_absolute():
        audio_path = storage_root / audio_path
    if not audio_path.is_file():
        errors.append("audio file does not exist")
    elif audio_path.stat().st_size <= 0:
        errors.append("audio file is empty")
    else:
        try:
            info = sf.info(str(audio_path))
            duration = float(info.duration)
            if duration <= 0:
                errors.append("audio duration is invalid")
        except Exception:
            errors.append("audio cannot be opened")
        actual_hash = precomputed_sha256 or sha256_file(audio_path)
        if not record.sha256:
            errors.append("SHA-256 is empty")
        elif actual_hash != record.sha256:
            errors.append("SHA-256 does not match audio file")
    return ValidationResult(not errors, errors, duration)
