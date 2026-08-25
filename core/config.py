from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class StorageConfig(BaseModel):
    root: Path = Path("data")


class LanguageConfig(BaseModel):
    audio: str = "id"
    transcript: str = "id"

    @field_validator("audio", "transcript")
    @classmethod
    def require_indonesian(cls, value: str) -> str:
        if value.lower() != "id":
            raise ValueError("only Indonesian language code 'id' is supported")
        return value.lower()


class LicensingConfig(BaseModel):
    allow_unknown: bool = False


class DownloadConfig(BaseModel):
    concurrency: int = Field(default=4, ge=1, le=32)
    timeout_seconds: float = Field(default=60, gt=0)
    retries: int = Field(default=5, ge=0, le=20)
    user_agent: str = "indonesian-corpus-collector/0.1 (+local research corpus acquisition)"


class LimitsConfig(BaseModel):
    max_records_per_source: int | None = Field(default=None, ge=1)


class SourceConfig(BaseModel):
    enabled: bool = True
    dataset_id: str | None = None
    version: str | None = None
    revision: str = "main"
    api_base: str | None = None


class AppConfig(BaseModel):
    storage: StorageConfig = Field(default_factory=StorageConfig)
    language: LanguageConfig = Field(default_factory=LanguageConfig)
    licensing: LicensingConfig = Field(default_factory=LicensingConfig)
    download: DownloadConfig = Field(default_factory=DownloadConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)


def credential(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def load_config(path: Path) -> AppConfig:
    load_dotenv(path.parent / ".env", override=False)
    if not path.is_file():
        raise FileNotFoundError(f"configuration file does not exist: {path}")
    parsed: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("configuration root must be a mapping")
    config = AppConfig.model_validate(parsed)
    if not config.storage.root.is_absolute():
        config.storage.root = (path.parent / config.storage.root).resolve()
    return config
