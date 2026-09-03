import json
from email.message import Message
from http import HTTPStatus
from types import SimpleNamespace

import pytest

from api import CorpusRequestHandler
from corpus_api.auth import ApiKeyError, generate_api_key, validate_api_key
from corpus_api.catalog import CatalogError, export_records, list_records, parse_query


SECRET = "test-secret-that-is-long-enough-for-hmac-signing"
NOW = 1_800_000_000


def write_catalog(tmp_path):
    catalog_path = tmp_path / "corpus.jsonl"
    records = [
        {
            "id": "one",
            "text": "Selamat pagi Jakarta",
            "source": "youtube/Channel Satu",
            "license": "CC-BY",
            "crawl_date": "2026-09-01",
            "split": "train",
        },
        {
            "id": "two",
            "text": "Sampai jumpa",
            "source": "movies/Film Satu (2026)",
            "license": "unknown",
            "crawl_date": "2026-09-02",
            "split": "eval",
        },
        {
            "id": "three",
            "text": "Jakarta malam hari",
            "source": "youtube/Channel Dua",
            "license": "CC-BY",
            "crawl_date": "2026-09-03",
            "split": "test",
        },
    ]
    catalog_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return catalog_path


def test_generated_api_key_is_short_unique_and_valid(tmp_path) -> None:
    key_store_path = tmp_path / "api_keys.json"
    first_key = generate_api_key(SECRET, "local-client", key_store_path, now=NOW)
    second_key = generate_api_key(SECRET, "local-client", key_store_path, now=NOW)

    assert first_key != second_key
    assert len(first_key) == 26
    assert first_key.startswith("cps_")
    assert first_key not in key_store_path.read_text(encoding="utf-8")
    claims = validate_api_key(first_key, SECRET, key_store_path, now=NOW)
    assert claims.name == "local-client"
    assert claims.expires_at == NOW + (30 * 24 * 60 * 60)


def test_api_key_rejects_wrong_secret_tampering_and_expiration(tmp_path) -> None:
    key_store_path = tmp_path / "api_keys.json"
    api_key = generate_api_key(
        SECRET,
        "local-client",
        key_store_path,
        expires_in_days=1,
        now=NOW,
    )

    with pytest.raises(ApiKeyError, match="Invalid API Key"):
        validate_api_key(
            api_key,
            "another-secret-that-is-also-long-enough",
            key_store_path,
            now=NOW,
        )
    with pytest.raises(ApiKeyError, match="Invalid API Key"):
        validate_api_key(api_key + "x", SECRET, key_store_path, now=NOW)
    with pytest.raises(ApiKeyError, match="Expired API Key"):
        validate_api_key(
            api_key,
            SECRET,
            key_store_path,
            now=NOW + (24 * 60 * 60),
        )


def test_request_authentication_accepts_bearer_key(monkeypatch, tmp_path) -> None:
    key_store_path = tmp_path / "api_keys.json"
    api_key = generate_api_key(SECRET, "local-client", key_store_path)
    handler = object.__new__(CorpusRequestHandler)
    handler.headers = Message()
    handler.headers["Authorization"] = f"Bearer {api_key}"
    handler.server = SimpleNamespace(
        api_secret=SECRET,
        key_store_path=key_store_path,
    )
    send_error = lambda *args, **kwargs: pytest.fail("Unexpected Authentication Error")
    monkeypatch.setattr(handler, "_send_error", send_error)

    assert handler._authenticate()


def test_request_authentication_rejects_invalid_key(monkeypatch, tmp_path) -> None:
    handler = object.__new__(CorpusRequestHandler)
    handler.headers = Message()
    handler.headers["X-API-Key"] = "invalid"
    handler.server = SimpleNamespace(
        api_secret=SECRET,
        key_store_path=tmp_path / "api_keys.json",
    )
    response = {}

    def capture_error(status, message, **kwargs):
        response.update(status=status, message=message, **kwargs)

    monkeypatch.setattr(handler, "_send_error", capture_error)

    assert not handler._authenticate()
    assert response["status"] == HTTPStatus.UNAUTHORIZED


def test_list_supports_search_filter_and_pagination(tmp_path) -> None:
    catalog_path = write_catalog(tmp_path)
    query = parse_query("q=jakarta&source=youtube&limit=1&offset=1")

    page = list_records(catalog_path, query)

    assert page.total == 2
    assert [record["id"] for record in page.records] == ["three"]
    assert page.limit == 1
    assert page.offset == 1


def test_export_streams_filtered_jsonl(tmp_path) -> None:
    catalog_path = write_catalog(tmp_path)
    query = parse_query("split=eval&license=unknown", export=True)

    exported = b"".join(export_records(catalog_path, query)).decode("utf-8")

    assert [json.loads(line)["id"] for line in exported.splitlines()] == ["two"]


@pytest.mark.parametrize(
    "query_string",
    [
        "split=invalid",
        "limit=0",
        "limit=1001",
        "offset=-1",
        "q=",
        "q=one&q=two",
        "unexpected=value",
    ],
)
def test_invalid_queries_are_rejected(query_string) -> None:
    with pytest.raises(CatalogError):
        parse_query(query_string)


def test_export_rejects_pagination_parameters() -> None:
    with pytest.raises(CatalogError, match="Unknown Query Parameter"):
        parse_query("limit=10", export=True)
