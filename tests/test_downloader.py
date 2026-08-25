from pathlib import Path

import httpx
import pytest
import respx

from core.downloader import Downloader


@respx.mock
def test_download_rate_limit_retries_are_bounded(tmp_path: Path, monkeypatch) -> None:
    route = respx.get("https://example.test/audio.wav").mock(return_value=httpx.Response(429))
    statuses: list[str] = []
    downloader = Downloader(2, 1, "test", status_callback=statuses.append)
    monkeypatch.setattr("core.downloader.time.sleep", lambda _: None)

    with pytest.raises(httpx.HTTPStatusError):
        downloader.download("https://example.test/audio.wav", tmp_path / "audio.wav")

    downloader.close()
    assert route.call_count == 2
    assert any("Attempt 2/2" in status for status in statuses)
