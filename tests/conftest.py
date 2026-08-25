from pathlib import Path

import pytest

from core.config import AppConfig


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate({
        "storage": {"root": str(tmp_path / "data")},
        "language": {"audio": "id", "transcript": "id"},
        "licensing": {"allow_unknown": False},
        "download": {"concurrency": 2, "timeout_seconds": 2, "retries": 1},
        "limits": {"max_records_per_source": 2},
        "sources": {
            "common_voice": {"enabled": True}, "fleurs": {"enabled": True},
            "librivox": {"enabled": True}, "homostoria": {"enabled": True},
            "hari_minggoean": {"enabled": True}, "inesco": {"enabled": True},
            "tatoeba": {"enabled": True},
        },
    })
