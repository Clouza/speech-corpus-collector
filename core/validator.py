from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from models.record import CorpusRecord


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


def validate_records(records: Iterable[CorpusRecord]) -> ValidationResult:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for line_number, record in enumerate(records, start=1):
        if record.id in seen_ids:
            errors.append(f"record {line_number} has duplicate ID {record.id}")
        seen_ids.add(record.id)
        if not record.text.strip():
            errors.append(f"record {line_number} has empty text")
        if not record.license.strip():
            errors.append(f"record {line_number} has empty license")
    return ValidationResult(not errors, errors)
