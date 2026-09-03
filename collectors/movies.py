from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import httpx

from collectors.base import BaseCollector, Candidate, CollectorUnavailable, make_source_label
from core.config import credential


HTML_TAG = re.compile(r"<[^>]+>")
ASS_TAG = re.compile(r"\{[^}]*}")
WHITESPACE = re.compile(r"\s+")
TIMESTAMP = re.compile(r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[,.]\d{3}\s*-->")
MICRODVD = re.compile(r"^\{\d+}\{\d+}(.*)$")
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*]+')


class OpenSubtitlesQuotaReached(CollectorUnavailable):
    pass


def opensubtitles_error_message(error: httpx.HTTPStatusError) -> str:
    status = error.response.status_code
    details = ""
    try:
        payload = error.response.json()
        message = payload.get("message")
        errors = payload.get("errors")
        if message:
            details = str(message)
        elif isinstance(errors, list):
            details = "; ".join(str(item) for item in errors)
        elif errors:
            details = str(errors)
    except (AttributeError, ValueError):
        details = error.response.text.strip()
    normalized = details.casefold()
    suffix = f": {details}" if details else ""
    if any(
        marker in normalized
        for marker in ("quota", "download limit", "downloaded your allowed")
    ):
        return f"OpenSubtitles Download Quota Reached{suffix}"
    if status == 429:
        retry_after = error.response.headers.get("Retry-After")
        retry_suffix = f"; Retry After {retry_after} Seconds" if retry_after else ""
        return f"OpenSubtitles Rate Limit Reached{retry_suffix}{suffix}"
    if status in {401, 403}:
        return f"OpenSubtitles Authentication or Permission Failed{suffix}"
    if status == 400:
        return f"OpenSubtitles Rejected the Request{suffix}"
    return f"OpenSubtitles Request Failed with HTTP {status}{suffix}"


def normalize_subtitle_text(value: str) -> str:
    value = value.replace("\\N", " ").replace("\\n", " ").replace("|", " ")
    value = ASS_TAG.sub("", value)
    value = HTML_TAG.sub("", html.unescape(value))
    return WHITESPACE.sub(" ", value).strip()


def parse_subtitle(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    suffix = path.suffix.lower()
    if suffix in {".ass", ".ssa"}:
        return _parse_ass(text)
    if suffix == ".sub":
        return _parse_microdvd(text)
    if suffix in {".srt", ".vtt"}:
        return _parse_timed_blocks(text)
    raise ValueError(f"unsupported subtitle format: {suffix or '<none>'}")


def _parse_timed_blocks(text: str) -> list[str]:
    cues: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp_index = next((index for index, line in enumerate(lines) if TIMESTAMP.match(line)), None)
        if timestamp_index is None:
            continue
        value = normalize_subtitle_text(" ".join(lines[timestamp_index + 1 :]))
        if value:
            cues.append(value)
    return cues


def _parse_ass(text: str) -> list[str]:
    cues: list[str] = []
    in_events = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.casefold() == "[events]":
            in_events = True
            continue
        if in_events and stripped.startswith("["):
            break
        if not in_events or not stripped.casefold().startswith("dialogue:"):
            continue
        fields = stripped.split(",", 9)
        if len(fields) != 10:
            continue
        value = normalize_subtitle_text(fields[9])
        if value:
            cues.append(value)
    return cues


def _parse_microdvd(text: str) -> list[str]:
    cues: list[str] = []
    for line in text.splitlines():
        match = MICRODVD.match(line.strip())
        if not match:
            continue
        value = normalize_subtitle_text(match.group(1))
        if value:
            cues.append(value)
    return cues


class MoviesCollector(BaseCollector):
    key = "movies"
    display_name = "Indonesian Movie Subtitles"
    credentials = ("OPENSUBTITLES_API_KEY",)
    optional_credentials = ("OPENSUBTITLES_USERNAME", "OPENSUBTITLES_PASSWORD")

    def planned_items(self) -> int:
        return self.config.limits.max_records_per_source or self.source_config.max_subtitles

    def discover(self):
        api_key = credential("OPENSUBTITLES_API_KEY")
        if not api_key:
            raise CollectorUnavailable(
                "OPENSUBTITLES_API_KEY Is Required for Movie Subtitle Discovery"
            )
        api_base = (self.source_config.api_base or "https://api.opensubtitles.com/api/v1").rstrip("/")
        self._report("Authenticating OpenSubtitles")
        api_base, headers = self._authenticate(api_base, api_key)
        if self._authenticated:
            self._report(
                f"OpenSubtitles Quota: {self._remaining_downloads} Downloads Remaining"
            )
        else:
            self._report("OpenSubtitles Anonymous Limit: Up to 5 Downloads per Day")
        self._report("Discovering Movie Subtitles")
        subtitles = self._search_subtitles(api_base, headers)
        failures: list[str] = []
        yielded = 0
        total_subtitles = len(subtitles)
        self._report("Preparing Movie Subtitles", 0, total_subtitles, yielded)
        for subtitle_number, item in enumerate(subtitles, start=1):
            subtitle_id = str(item.get("id") or "")
            attributes = item.get("attributes") or {}
            files = attributes.get("files") or []
            if not subtitle_id or not files:
                self._report(
                    "Skipped Incomplete Movie Subtitle",
                    subtitle_number,
                    total_subtitles,
                    yielded,
                )
                continue
            file_info = files[0]
            file_id = file_info.get("file_id")
            if not file_id:
                self._report(
                    "Skipped Movie Subtitle Without a File",
                    subtitle_number,
                    total_subtitles,
                    yielded,
                )
                continue
            source = make_source_label(self.key, self._movie_title(attributes), subtitle_id)
            self._report(
                f"Preparing {source}",
                subtitle_number - 1,
                total_subtitles,
                yielded,
            )
            try:
                subtitle_path = self._download_subtitle(
                    api_base,
                    headers,
                    subtitle_id,
                    int(file_id),
                    str(file_info.get("file_name") or f"{file_id}.srt"),
                )
                cues = parse_subtitle(subtitle_path)
            except OpenSubtitlesQuotaReached as exc:
                if yielded == 0:
                    raise
                self.logger.warning("movie subtitle collection stopped reason=%s", exc)
                self._report(str(exc), subtitle_number - 1, total_subtitles, yielded)
                break
            except CollectorUnavailable:
                raise
            except Exception as exc:
                failures.append(f"{subtitle_id}: {exc}")
                self.logger.warning("movie subtitle skipped subtitle_id=%s error=%s", subtitle_id, exc)
                self._report(
                    f"Failed {source}",
                    subtitle_number,
                    total_subtitles,
                    yielded,
                )
                continue
            license_name = str(attributes.get("license") or "unknown").strip() or "unknown"
            for cue_number, cue in enumerate(cues, start=1):
                yielded += 1
                yield Candidate(
                    source_id=f"{subtitle_id}:{file_id}:{cue_number}",
                    text=cue,
                    license=license_name,
                    source=source,
                )
            self._report(
                f"Processed {source}",
                subtitle_number,
                total_subtitles,
                yielded,
            )
        if subtitles and yielded == 0 and failures:
            raise RuntimeError(f"No Movie Subtitles Could Be Prepared; First Failure: {failures[0]}")

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {
            "Api-Key": api_key,
            "Content-Type": "application/json",
        }

    def _authenticate(self, api_base: str, api_key: str) -> tuple[str, dict[str, str]]:
        headers = self._headers(api_key)
        self._remaining_downloads: int | None = 5
        self._authenticated = False
        username = credential("OPENSUBTITLES_USERNAME")
        password = credential("OPENSUBTITLES_PASSWORD")
        if bool(username) != bool(password):
            raise CollectorUnavailable(
                "OPENSUBTITLES_USERNAME and OPENSUBTITLES_PASSWORD Must Be Provided Together"
            )
        if not username or not password:
            self.logger.info("OpenSubtitles authentication skipped; using 5 anonymous downloads per day")
            return api_base, headers
        response = self._request_json(
            "POST",
            f"{api_base}/login",
            headers=headers,
            json={"username": username, "password": password},
        )
        if not isinstance(response, dict) or not response.get("token"):
            raise CollectorUnavailable("OpenSubtitles Login Did Not Return an Authentication Token")
        authenticated_headers = {
            **headers,
            "Authorization": f"Bearer {response['token']}",
        }
        authenticated_api_base = self._authenticated_api_base(response.get("base_url"), api_base)
        self._authenticated = True
        user = response.get("user") or {}
        allowed_downloads = user.get("allowed_downloads")
        if isinstance(allowed_downloads, int):
            self._remaining_downloads = allowed_downloads
        try:
            user_info = self._request_json(
                "GET",
                f"{authenticated_api_base}/infos/user",
                headers=authenticated_headers,
            )
            remaining = (user_info.get("data") or {}).get("remaining_downloads")
            if isinstance(remaining, int):
                self._remaining_downloads = remaining
        except CollectorUnavailable as exc:
            self.logger.warning("OpenSubtitles quota lookup failed error=%s", exc)
        self.logger.info(
            "OpenSubtitles authenticated remaining_downloads=%s",
            self._remaining_downloads,
        )
        return authenticated_api_base, authenticated_headers

    @staticmethod
    def _authenticated_api_base(value: object, fallback: str) -> str:
        base_url = str(value or "").strip().rstrip("/")
        if not base_url:
            return fallback
        if "://" not in base_url:
            base_url = f"https://{base_url}"
        if not base_url.endswith("/api/v1"):
            base_url = f"{base_url}/api/v1"
        return base_url

    @staticmethod
    def _movie_title(attributes: dict[str, Any]) -> str:
        details = attributes.get("feature_details") or {}
        title = str(details.get("title") or details.get("movie_name") or "").strip()
        year = str(details.get("year") or "").strip()
        if title and year and year not in title:
            return f"{title} ({year})"
        return title or str(attributes.get("release") or "").strip()

    def _search_subtitles(self, api_base: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        maximum = self.source_config.max_subtitles
        query = (self.source_config.search_query or "").strip()
        if query and len(query) < 3:
            raise CollectorUnavailable(
                "OpenSubtitles Search Query Must Contain at Least 3 Characters"
            )
        if not query:
            response = self._request_json(
                "GET",
                f"{api_base}/discover/latest",
                headers=headers,
                params={"language": self.source_config.language, "type": "movie"},
            )
            if not isinstance(response, dict):
                raise RuntimeError("OpenSubtitles Returned an Invalid Latest Response")
            raw_path = self.storage.raw_source(self.key) / "latest.json"
            raw_path.write_text(
                json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            items = response.get("data") or []
            if not isinstance(items, list):
                raise RuntimeError("OpenSubtitles Latest Response Has Invalid Data")
            return [item for item in items if isinstance(item, dict)][:maximum]
        results: list[dict[str, Any]] = []
        page = 1
        while len(results) < maximum:
            params: dict[str, str | int] = {
                "languages": self.source_config.language,
                "order_by": "download_count",
                "order_direction": "desc",
                "page": page,
            }
            if query:
                params["query"] = query
            response = self._request_json(
                "GET",
                f"{api_base}/subtitles",
                headers=headers,
                params=params,
            )
            if not isinstance(response, dict):
                raise RuntimeError("OpenSubtitles Returned an Invalid Search Response")
            raw_path = self.storage.raw_source(self.key) / f"search-page-{page}.json"
            raw_path.write_text(
                json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            items = response.get("data") or []
            if not isinstance(items, list) or not items:
                break
            results.extend(item for item in items if isinstance(item, dict))
            total_pages = int((response.get("total_pages") or page))
            if page >= total_pages:
                break
            page += 1
        return results[:maximum]

    def _download_subtitle(
        self,
        api_base: str,
        headers: dict[str, str],
        subtitle_id: str,
        file_id: int,
        original_filename: str,
    ) -> Path:
        raw_directory = self.storage.raw_source(self.key) / subtitle_id
        raw_directory.mkdir(parents=True, exist_ok=True)
        cached = next(raw_directory.glob(f"{file_id}-*"), None)
        if cached is not None and cached.is_file():
            return cached
        if self._remaining_downloads is not None and self._remaining_downloads <= 0:
            raise OpenSubtitlesQuotaReached("OpenSubtitles Download Quota Reached")
        response = self._request_json(
            "POST",
            f"{api_base}/download",
            headers=headers,
            json={"file_id": file_id},
        )
        if not isinstance(response, dict) or not response.get("link"):
            raise RuntimeError("OpenSubtitles Did Not Return a Download Link")
        remaining = response.get("remaining")
        if isinstance(remaining, int):
            self._remaining_downloads = remaining
        elif self._remaining_downloads is not None:
            self._remaining_downloads -= 1
        response_filename = str(response.get("file_name") or original_filename)
        safe_filename = INVALID_FILENAME.sub("_", Path(response_filename).name).strip(". ")
        if not safe_filename:
            safe_filename = f"{file_id}.srt"
        destination = raw_directory / f"{file_id}-{safe_filename}"
        try:
            return self.downloader.download(str(response["link"]), destination)
        except httpx.HTTPStatusError as exc:
            self._raise_request_error(exc)
        raise RuntimeError("OpenSubtitles Download Ended Unexpectedly")

    def _request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            return self.downloader.request_json(method, url, **kwargs)
        except httpx.HTTPStatusError as exc:
            self._raise_request_error(exc)

    @staticmethod
    def _raise_request_error(error: httpx.HTTPStatusError) -> None:
        message = opensubtitles_error_message(error)
        if "Quota Reached" in message:
            raise OpenSubtitlesQuotaReached(message) from error
        raise CollectorUnavailable(message) from error
