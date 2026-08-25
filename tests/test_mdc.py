import httpx

from collectors.mdc import mdc_access_error


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
