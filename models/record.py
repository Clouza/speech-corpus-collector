from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


CorpusSplit = Literal["train", "eval", "test"]


def make_record_id(source: str, source_id: str) -> str:
    normalized = f"{source.strip().lower()}:{source_id.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


class CorpusRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    source: str
    license: str = "unknown"
    crawl_date: date
    split: CorpusSplit

    @field_validator("id", "text", "source")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("license", mode="before")
    @classmethod
    def normalize_license(cls, value: object) -> str:
        if value is None or not str(value).strip():
            return "unknown"
        return str(value).strip()
