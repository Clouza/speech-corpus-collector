from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator


class StorageConfig(BaseModel):
    root: Path = Path("data")


class SplitConfig(BaseModel):
    train: float = Field(default=0.8, ge=0, le=1)
    eval: float = Field(default=0.1, ge=0, le=1)
    test: float = Field(default=0.1, ge=0, le=1)
    seed: str = "indonesian-corpus-v1"

    @model_validator(mode="after")
    def validate_ratios(self) -> SplitConfig:
        if abs(self.train + self.eval + self.test - 1.0) > 1e-9:
            raise ValueError("split ratios must total 1.0")
        if not self.seed.strip():
            raise ValueError("split seed must not be empty")
        return self


class DownloadConfig(BaseModel):
    timeout_seconds: float = Field(default=60, gt=0)
    retries: int = Field(default=5, ge=0, le=20)
    user_agent: str = "indonesian-corpus-collector/0.2"


class LimitsConfig(BaseModel):
    max_records_per_source: int | None = Field(default=None, ge=1)


class SourceConfig(BaseModel):
    enabled: bool = True
    api_base: str | None = None
    search_query: str | None = None
    channel_id: str | None = None
    language: str = "id"
    max_videos: int = Field(default=10, ge=1, le=500)
    max_subtitles: int = Field(default=10, ge=1, le=500)
    include_auto_captions: bool = False

    @field_validator("language")
    @classmethod
    def require_indonesian(cls, value: str) -> str:
        if value.strip().lower() != "id":
            raise ValueError("only Indonesian language code 'id' is supported")
        return "id"


class AppConfig(BaseModel):
    storage: StorageConfig = Field(default_factory=StorageConfig)
    splits: SplitConfig = Field(default_factory=SplitConfig)
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
