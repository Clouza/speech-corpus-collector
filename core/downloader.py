from __future__ import annotations

import logging
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx


class Downloader:
    def __init__(
        self,
        timeout_seconds: float,
        retries: int,
        user_agent: str,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.retries = retries
        self.status_callback = status_callback
        self.logger = logging.getLogger("collector.downloader")
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

    def close(self) -> None:
        self.client.close()

    def _report(self, message: str) -> None:
        if self.status_callback is not None:
            self.status_callback(message)

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc or url

    @staticmethod
    def _format_size(byte_count: int) -> str:
        if byte_count >= 1024**3:
            return f"{byte_count / 1024**3:.2f} GB"
        if byte_count >= 1024**2:
            return f"{byte_count / 1024**2:.1f} MB"
        if byte_count >= 1024:
            return f"{byte_count / 1024:.1f} KB"
        return f"{byte_count} B"

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
        host = self._host(url)
        for attempt in range(self.retries + 1):
            response: httpx.Response | None = None
            try:
                self._report(f"Connecting to {host}")
                response = self.client.request(method, url, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                self._report(f"Connected to {host} (HTTP {response.status_code})")
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt >= self.retries:
                    raise
                delay = self._retry_delay(response, attempt)
                self.logger.warning(
                    "request retry method=%s host=%s attempt=%s/%s delay=%.1f error=%s",
                    method,
                    host,
                    attempt + 2,
                    self.retries + 1,
                    delay,
                    exc,
                )
                self._report(
                    f"Request Failed; Retrying {host} in {delay:.0f}s "
                    f"(Attempt {attempt + 2}/{self.retries + 1})"
                )
                time.sleep(delay)
        raise RuntimeError("request retry loop ended unexpectedly") from last_error

    def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        return self._request(method, url, **kwargs).json()

    def download(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        host = self._host(url)
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            existing_size = partial.stat().st_size if partial.is_file() else 0
            headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}
            response: httpx.Response | None = None
            try:
                self._report(f"Connecting to {host} for {destination.name}")
                with self.client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        response.read()
                        response.raise_for_status()
                    response.raise_for_status()

                    append = existing_size > 0 and response.status_code == 206
                    downloaded = existing_size if append else 0
                    content_length = int(response.headers.get("Content-Length", "0") or 0)
                    total = downloaded + content_length if content_length else None
                    mode = "ab" if append else "wb"
                    last_reported_bytes = downloaded
                    last_reported_at = time.monotonic()
                    self._report(self._download_message(destination.name, downloaded, total))

                    with partial.open(mode) as handle:
                        for chunk in response.iter_bytes(1024 * 1024):
                            handle.write(chunk)
                            downloaded += len(chunk)
                            now = time.monotonic()
                            if downloaded - last_reported_bytes >= 8 * 1024 * 1024 or now - last_reported_at >= 1:
                                self._report(self._download_message(destination.name, downloaded, total))
                                last_reported_bytes = downloaded
                                last_reported_at = now

                partial.replace(destination)
                self._report(f"Downloaded {destination.name} ({self._format_size(destination.stat().st_size)})")
                self.logger.info("download result=success host=%s path=%s", host, destination)
                return destination
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt >= self.retries:
                    raise
                delay = self._retry_delay(response, attempt)
                self.logger.warning(
                    "download retry host=%s file=%s attempt=%s/%s delay=%.1f error=%s",
                    host,
                    destination.name,
                    attempt + 2,
                    self.retries + 1,
                    delay,
                    exc,
                )
                self._report(
                    f"Download Failed; Retrying {destination.name} in {delay:.0f}s "
                    f"(Attempt {attempt + 2}/{self.retries + 1})"
                )
                time.sleep(delay)

        raise RuntimeError("download retry loop ended unexpectedly") from last_error

    def _download_message(self, filename: str, downloaded: int, total: int | None) -> str:
        downloaded_text = self._format_size(downloaded)
        if total:
            percentage = min(downloaded / total * 100, 100)
            return f"Downloading {filename}: {downloaded_text}/{self._format_size(total)} ({percentage:.0f}%)"
        return f"Downloading {filename}: {downloaded_text}"
