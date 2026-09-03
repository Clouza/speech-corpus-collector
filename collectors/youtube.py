from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from collectors.base import BaseCollector, Candidate, CollectorUnavailable, make_source_label
from core.config import credential


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
WHITESPACE = re.compile(r"\s+")
HTML_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class CaptionSegment:
    start_ms: int
    end_ms: int
    text: str


class CaptionUnavailable(RuntimeError):
    pass


def parse_caption_json(path: Path) -> list[CaptionSegment]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError(f"invalid YouTube caption document: {path.name}")
    segments: list[CaptionSegment] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        start_ms = event.get("tStartMs")
        duration_ms = event.get("dDurationMs")
        parts = event.get("segs")
        if not isinstance(start_ms, int) or not isinstance(duration_ms, int) or duration_ms <= 0:
            continue
        if not isinstance(parts, list):
            continue
        raw_text = "".join(
            str(part.get("utf8") or "")
            for part in parts
            if isinstance(part, dict)
        )
        text = WHITESPACE.sub(" ", HTML_TAG.sub("", html.unescape(raw_text))).strip()
        if not text:
            continue
        segment = CaptionSegment(start_ms, start_ms + duration_ms, text)
        if segments and segments[-1] == segment:
            continue
        segments.append(segment)
    return segments


def _api_error_message(error: httpx.HTTPStatusError) -> str:
    status = error.response.status_code
    reason = ""
    try:
        details = error.response.json().get("error", {}).get("errors", [])
        reason = str(details[0].get("reason") or "") if details else ""
    except (AttributeError, ValueError):
        pass
    if status in {401, 403} and reason in {"quotaExceeded", "dailyLimitExceeded"}:
        return "YouTube API Quota Exceeded"
    if status in {400, 401, 403}:
        return "YouTube API Authentication Failed; Verify YOUTUBE_API_KEY"
    return f"YouTube API Request Failed with HTTP {status}"


class YouTubeCollector(BaseCollector):
    key = "youtube"
    display_name = "YouTube Indonesian Captions"
    credentials = ("YOUTUBE_API_KEY",)

    def planned_items(self) -> int:
        return self.config.limits.max_records_per_source or self.source_config.max_videos

    def discover(self):
        self._require_runtime()
        self._report("Discovering YouTube Videos")
        videos = self._search_videos()
        yielded = 0
        failures: list[str] = []
        total_videos = len(videos)
        self._report("Preparing YouTube Captions", 0, total_videos, yielded)
        for video_number, video in enumerate(videos, start=1):
            video_id = str(video["id"])
            snippet = video.get("snippet") or {}
            title = str(snippet.get("title") or "").strip()
            source = make_source_label(self.key, title, video_id)
            self._report(
                f"Preparing {source}",
                video_number - 1,
                total_videos,
                yielded,
            )
            try:
                caption_path, caption_source = self._download_caption(video_id)
                segments = parse_caption_json(caption_path)
            except CaptionUnavailable as exc:
                self.logger.info("YouTube video skipped video_id=%s reason=%s", video_id, exc)
                self._report(
                    f"Skipped {source}",
                    video_number,
                    total_videos,
                    yielded,
                )
                continue
            except Exception as exc:
                failures.append(f"{video_id}: {exc}")
                self.logger.warning("YouTube video skipped video_id=%s error=%s", video_id, exc)
                self._report(
                    f"Failed {source}",
                    video_number,
                    total_videos,
                    yielded,
                )
                continue
            for segment in segments:
                yielded += 1
                yield Candidate(
                    source_id=f"{video_id}:{segment.start_ms}:{segment.end_ms}:{caption_source}",
                    text=segment.text,
                    license="CC-BY-3.0",
                    source=source,
                )
            self._report(
                f"Processed {source}",
                video_number,
                total_videos,
                yielded,
            )
        if videos and yielded == 0 and failures:
            raise RuntimeError(f"No YouTube Captions Could Be Prepared; First Failure: {failures[0]}")

    @staticmethod
    def _require_runtime() -> None:
        try:
            import yt_dlp  # noqa: F401
        except ImportError as exc:
            raise CollectorUnavailable("yt-dlp Is Required for the YouTube Collector") from exc

    def _search_videos(self) -> list[dict[str, Any]]:
        api_key = credential("YOUTUBE_API_KEY")
        if not api_key:
            raise CollectorUnavailable("YOUTUBE_API_KEY Is Required for YouTube Discovery")
        query = (self.source_config.search_query or "bahasa Indonesia").strip()
        channel_id = (self.source_config.channel_id or "").strip()
        headers = {"X-Goog-Api-Key": api_key}
        video_ids: list[str] = []
        next_page_token: str | None = None
        while len(video_ids) < self.source_config.max_videos:
            params: dict[str, str | int] = {
                "part": "id",
                "type": "video",
                "maxResults": min(50, self.source_config.max_videos - len(video_ids)),
                "relevanceLanguage": "id",
                "regionCode": "ID",
                "videoCaption": "closedCaption",
                "videoLicense": "creativeCommon",
            }
            if query:
                params["q"] = query
            if channel_id:
                params["channelId"] = channel_id
            if next_page_token:
                params["pageToken"] = next_page_token
            response = self._api_request("search", headers, params)
            items = response.get("items") or []
            for item in items:
                video_id = str((item.get("id") or {}).get("videoId") or "")
                if video_id and video_id not in video_ids:
                    video_ids.append(video_id)
                    if len(video_ids) >= self.source_config.max_videos:
                        break
            next_page_token = response.get("nextPageToken")
            if not next_page_token or not items:
                break
        videos: list[dict[str, Any]] = []
        for offset in range(0, len(video_ids), 50):
            response = self._api_request(
                "videos",
                headers,
                {
                    "part": "snippet,status",
                    "id": ",".join(video_ids[offset : offset + 50]),
                    "maxResults": 50,
                },
            )
            for item in response.get("items") or []:
                status = item.get("status") or {}
                if status.get("license") != "creativeCommon":
                    continue
                videos.append(item)
        raw_path = self.storage.raw_source(self.key) / "video-index.json"
        raw_path.write_text(
            json.dumps(videos, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return videos

    def _api_request(
        self,
        resource: str,
        headers: dict[str, str],
        params: dict[str, str | int],
    ) -> dict[str, Any]:
        try:
            response = self.downloader.request_json(
                "GET",
                f"{YOUTUBE_API_BASE}/{resource}",
                headers=headers,
                params=params,
            )
        except httpx.HTTPStatusError as exc:
            raise CollectorUnavailable(_api_error_message(exc)) from exc
        if not isinstance(response, dict):
            raise RuntimeError(f"YouTube Returned an Invalid {resource.title()} Response")
        return response

    def _download_caption(self, video_id: str) -> tuple[Path, str]:
        from yt_dlp import YoutubeDL

        video_directory = self.storage.raw_source(self.key) / video_id
        video_directory.mkdir(parents=True, exist_ok=True)
        existing = self._cached_caption(video_directory, video_id)
        if existing is not None:
            source = "automatic" if ".auto." in existing.name else "manual"
            return existing, source
        source_url = f"https://www.youtube.com/watch?v={video_id}"
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": self.config.download.timeout_seconds,
            "retries": self.config.download.retries,
        }
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=False)
        caption_language, caption_source = self._select_caption_track(info)
        if caption_language is None or caption_source is None:
            raise CaptionUnavailable("no eligible Indonesian caption track")
        download_options = {
            **options,
            "outtmpl": str(video_directory / f"{video_id}.%(ext)s"),
            "writesubtitles": caption_source == "manual",
            "writeautomaticsub": caption_source == "automatic",
            "subtitleslangs": [caption_language],
            "subtitlesformat": "json3",
        }
        with YoutubeDL(download_options) as downloader:
            downloader.download([source_url])
        caption_path = video_directory / f"{video_id}.{caption_language}.json3"
        if not caption_path.is_file():
            raise RuntimeError("yt-dlp Did Not Produce the Expected Caption File")
        if caption_source == "automatic":
            marked = caption_path.with_name(caption_path.name.removesuffix(".json3") + ".auto.json3")
            caption_path.replace(marked)
            caption_path = marked
        return caption_path, caption_source

    def _cached_caption(self, directory: Path, video_id: str) -> Path | None:
        candidates = sorted(directory.glob(f"{video_id}.*.json3"))
        manual = next((path for path in candidates if ".auto." not in path.name), None)
        if manual is not None:
            return manual
        if self.source_config.include_auto_captions:
            return next((path for path in candidates if ".auto." in path.name), None)
        return None

    def _select_caption_track(self, info: dict[str, Any]) -> tuple[str | None, str | None]:
        language = self._indonesian_track(info.get("subtitles") or {})
        if language:
            return language, "manual"
        if self.source_config.include_auto_captions:
            language = self._indonesian_track(info.get("automatic_captions") or {})
            if language:
                return language, "automatic"
        return None, None

    @staticmethod
    def _indonesian_track(tracks: dict[str, Any]) -> str | None:
        for language in ("id", "id-ID", "id-orig"):
            if language in tracks:
                return language
        return next(
            (language for language in sorted(tracks) if language.lower().startswith("id-")),
            None,
        )
