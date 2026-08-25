import httpx
from pathlib import Path

from collectors.mdc import build_audio_index, mdc_access_error


def http_error(status_code: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/download")
    response = httpx.Response(status_code, request=request, headers=headers)
    return httpx.HTTPStatusError("request failed", request=request, response=response)


def test_mdc_forbidden_explains_terms_acceptance() -> None:
    message = mdc_access_error(http_error(403), "https://example.test/dataset")

    assert "accept this dataset's terms" in message
    assert "https://example.test/dataset" in message


def test_mdc_rate_limit_includes_retry_after() -> None:
    message = mdc_access_error(http_error(429, {"Retry-After": "60"}), "https://example.test/dataset")

    assert "retry after 60 seconds" in message


def test_audio_index_matches_filename_with_or_without_extension(tmp_path: Path) -> None:
    audio_path = tmp_path / "homostoria_01.mp3"
    audio_path.touch()

    index = build_audio_index([audio_path])

    assert index["homostoria_01.mp3"] == audio_path
    assert index["homostoria_01"] == audio_path


def test_audio_index_does_not_guess_ambiguous_stems(tmp_path: Path) -> None:
    mp3_path = tmp_path / "episode.mp3"
    wav_path = tmp_path / "episode.wav"

    index = build_audio_index([mp3_path, wav_path])

    assert "episode" not in index
    assert index["episode.mp3"] == mp3_path
    assert index["episode.wav"] == wav_path
