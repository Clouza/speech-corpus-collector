import httpx
import pytest

from collectors.base import CollectorUnavailable
from collectors.movies import (
    MoviesCollector,
    normalize_subtitle_text,
    opensubtitles_error_message,
    parse_subtitle,
)
from core.config import SourceConfig
from core.storage import Storage


class FakeDownloader:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def make_collector(response=None) -> MoviesCollector:
    collector = MoviesCollector.__new__(MoviesCollector)
    collector.downloader = FakeDownloader(response)
    collector.logger = type("Logger", (), {"info": lambda *args, **kwargs: None})()
    return collector


def test_parse_srt(tmp_path) -> None:
    path = tmp_path / "movie.srt"
    path.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n<i>Apa kabar?</i>\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nBaik, terima kasih.\n",
        encoding="utf-8",
    )

    assert parse_subtitle(path) == ["Apa kabar?", "Baik, terima kasih."]


def test_parse_webvtt(tmp_path) -> None:
    path = tmp_path / "movie.vtt"
    path.write_text(
        "WEBVTT\n\nintro\n00:00:01.000 --> 00:00:03.000\nHalo dunia\n",
        encoding="utf-8",
    )

    assert parse_subtitle(path) == ["Halo dunia"]


def test_parse_ass(tmp_path) -> None:
    path = tmp_path / "movie.ass"
    path.write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\i1}Halo{\\i0}\\Nsemua\n",
        encoding="utf-8",
    )

    assert parse_subtitle(path) == ["Halo semua"]


def test_normalize_subtitle_text_removes_markup() -> None:
    assert normalize_subtitle_text("<b>Halo</b> &amp; selamat datang") == "Halo & selamat datang"


def test_movie_title_uses_title_and_year() -> None:
    attributes = {
        "feature_details": {
            "title": "Judul Film",
            "year": 2026,
        }
    }

    assert MoviesCollector._movie_title(attributes) == "Judul Film (2026)"


def test_authenticate_requests_token_and_uses_returned_base_url(monkeypatch) -> None:
    monkeypatch.setenv("OPENSUBTITLES_USERNAME", "collector-user")
    monkeypatch.setenv("OPENSUBTITLES_PASSWORD", "secret")
    collector = make_collector(
        [
            {
                "token": "runtime-token",
                "base_url": "vip-api.opensubtitles.com",
                "user": {"allowed_downloads": 20},
            },
            {"data": {"remaining_downloads": 17}},
        ]
    )

    api_base, headers = collector._authenticate(
        "https://api.opensubtitles.com/api/v1",
        "api-key",
    )

    assert api_base == "https://vip-api.opensubtitles.com/api/v1"
    assert headers["Authorization"] == "Bearer runtime-token"
    assert collector._remaining_downloads == 17
    assert collector.downloader.calls == [
        (
            "POST",
            "https://api.opensubtitles.com/api/v1/login",
            {
                "headers": {"Api-Key": "api-key", "Content-Type": "application/json"},
                "json": {"username": "collector-user", "password": "secret"},
            },
        ),
        (
            "GET",
            "https://vip-api.opensubtitles.com/api/v1/infos/user",
            {
                "headers": {
                    "Api-Key": "api-key",
                    "Content-Type": "application/json",
                    "Authorization": "Bearer runtime-token",
                }
            },
        ),
    ]


def test_authenticate_uses_anonymous_limits_without_account_credentials(monkeypatch) -> None:
    monkeypatch.delenv("OPENSUBTITLES_USERNAME", raising=False)
    monkeypatch.delenv("OPENSUBTITLES_PASSWORD", raising=False)
    collector = make_collector()

    api_base, headers = collector._authenticate(
        "https://api.opensubtitles.com/api/v1",
        "api-key",
    )

    assert api_base == "https://api.opensubtitles.com/api/v1"
    assert headers == {"Api-Key": "api-key", "Content-Type": "application/json"}
    assert collector._remaining_downloads == 5
    assert collector.downloader.calls == []


def test_authenticate_rejects_partial_account_credentials(monkeypatch) -> None:
    monkeypatch.setenv("OPENSUBTITLES_USERNAME", "collector-user")
    monkeypatch.delenv("OPENSUBTITLES_PASSWORD", raising=False)
    collector = make_collector()

    with pytest.raises(CollectorUnavailable, match="Must Be Provided Together"):
        collector._authenticate("https://api.opensubtitles.com/api/v1", "api-key")


def test_empty_query_uses_latest_movie_discovery(tmp_path) -> None:
    response = {"data": [{"id": "subtitle-1"}, {"id": "subtitle-2"}]}
    collector = make_collector(response)
    collector.source_config = SourceConfig(search_query="", max_subtitles=1)
    collector.storage = Storage(tmp_path / "data")

    results = collector._search_subtitles(
        "https://api.opensubtitles.com/api/v1",
        {"Api-Key": "api-key"},
    )

    assert results == [{"id": "subtitle-1"}]
    assert collector.downloader.calls == [
        (
            "GET",
            "https://api.opensubtitles.com/api/v1/discover/latest",
            {
                "headers": {"Api-Key": "api-key"},
                "params": {"language": "id", "type": "movie"},
            },
        )
    ]


def test_short_search_query_is_rejected_before_request(tmp_path) -> None:
    collector = make_collector(None)
    collector.source_config = SourceConfig(search_query="it")
    collector.storage = Storage(tmp_path / "data")

    with pytest.raises(CollectorUnavailable, match="at Least 3 Characters"):
        collector._search_subtitles(
            "https://api.opensubtitles.com/api/v1",
            {"Api-Key": "api-key"},
        )

    assert collector.downloader.calls == []


def test_api_error_exposes_provider_quota_message() -> None:
    request = httpx.Request("POST", "https://api.opensubtitles.com/api/v1/download")
    response = httpx.Response(
        403,
        request=request,
        json={"message": "You have downloaded your allowed subtitles for 24h"},
    )
    error = httpx.HTTPStatusError("forbidden", request=request, response=response)

    assert opensubtitles_error_message(error).startswith("OpenSubtitles Download Quota Reached")


def test_api_error_identifies_download_quota() -> None:
    request = httpx.Request("POST", "https://api.opensubtitles.com/api/v1/download")
    response = httpx.Response(
        403,
        request=request,
        json={"message": "Download quota exceeded"},
    )
    error = httpx.HTTPStatusError("forbidden", request=request, response=response)

    assert opensubtitles_error_message(error).startswith(
        "OpenSubtitles Download Quota Reached"
    )
