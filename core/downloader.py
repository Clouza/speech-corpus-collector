from __future__ import annotations

import logging
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx


class Downloader:
    def __init__(self, timeout_seconds: float, retries: int, user_agent: str) -> None:
        self.retries = retries
        self.logger = logging.getLogger("collector.downloader")
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

    def close(self) -> None:
        self.client.close()

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), 120.0)
                except ValueError:
                    try:
                        return max(0.0, min((parsedate_to_datetime(retry_after) - parsedate_to_datetime(response.headers["Date"])).total_seconds(), 120.0))
                    except (KeyError, TypeError, ValueError):
                        pass
        return min(2**attempt, 30.0)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            response: httpx.Response | None = None
            try:
                response = self.client.request(method, url, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt >= self.retries:
                    raise
                delay = self._retry_delay(response, attempt)
                self.logger.warning("retry method=%s url=%s attempt=%s delay=%.1f", method, url, attempt + 1, delay)
                time.sleep(delay)
        raise RuntimeError("request retry loop ended unexpectedly") from last_error

    def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        return self._request(method, url, **kwargs).json()

    def download(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        existing_size = partial.stat().st_size if partial.is_file() else 0
        headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}
        with self.client.stream("GET", url, headers=headers) as response:
            if response.status_code == 429:
                response.read()
                time.sleep(self._retry_delay(response, 0))
                return self.download(url, destination)
            response.raise_for_status()
            append = existing_size > 0 and response.status_code == 206
            mode = "ab" if append else "wb"
            with partial.open(mode) as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
        partial.replace(destination)
        self.logger.info("download result=success url=%s path=%s", url, destination)
        return destination
