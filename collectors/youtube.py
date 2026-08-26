from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

from collectors.base import BaseCollector, Candidate, CollectionSummary, CollectorUnavailable
from core.config import credential
from core.licenses import resolve_license
from models.record import make_record_id


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_LICENSE = resolve_license("CC-BY-3.0")
WHITESPACE = re.compile(r"\s+")
HTML_TAG = re.compile(r"<[^>]+>")
AUDIO_UNAVAILABLE_NOTICE = "Audio Unavailable: ffmpeg Was Not Found; Collected Transcripts Only"


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
        end_ms = start_ms + duration_ms
        if segments and segments[-1].start_ms == start_ms and segments[-1].text == text:
            continue
        segments.append(CaptionSegment(start_ms, end_ms, text))
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
        return "YouTube API Quota Exceeded; Wait for Quota Reset or Use Another Authorized Project"
    if status in {400, 401, 403}:
        return f"YouTube API Authentication Failed{f' ({reason})' if reason else ''}; Verify YOUTUBE_API_KEY"
    return f"YouTube API Request Failed with HTTP {status}{f' ({reason})' if reason else ''}"


class YouTubeCollector(BaseCollector):
    key = "youtube"
    display_name = "YouTube Indonesian Creative Commons"
    dataset_name = "YouTube Indonesian Creative Commons Videos"
    dataset_version = "YouTube Data API v3"
    credentials = ("YOUTUBE_API_KEY",)

    def collect(self, dry_run: bool = False) -> CollectionSummary:
        self._collect_audio = self._ffmpeg_available()
        if dry_run:
            summary = CollectionSummary(self.key)
            summary.planned = self.config.limits.max_records_per_source or self.source_config.max_videos
            if not self._collect_audio:
                summary.notices.append(AUDIO_UNAVAILABLE_NOTICE)
            return summary
        summary = super().collect(dry_run=False)
        if not self._collect_audio:
            summary.notices.append(AUDIO_UNAVAILABLE_NOTICE)
        return summary

    def discover(self) -> Iterable[Candidate]:
        self._require_runtime()
        collect_audio = getattr(self, "_collect_audio", self._ffmpeg_available())
        if not collect_audio:
            self._report(AUDIO_UNAVAILABLE_NOTICE)
            self.logger.warning("ffmpeg not found; collecting YouTube transcripts without audio")
        videos = self._search_videos()
        yielded = 0
        failures: list[str] = []
        for video in videos:
            video_id = video["id"]
            self._report(f"Preparing YouTube Video: {video_id}")
            try:
                audio_path, caption_path, caption_source = self._download_video_assets(
                    video_id,
                    include_audio=collect_audio,
                )
                segments = parse_caption_json(caption_path)
            except CaptionUnavailable as exc:
                self.logger.info("YouTube video skipped video_id=%s reason=%s", video_id, exc)
                continue
            except Exception as exc:
                self.logger.warning("YouTube video skipped video_id=%s error=%s", video_id, exc)
                failures.append(f"{video_id}: {exc}")
                continue
            if not segments:
                self.logger.warning("YouTube video has no usable Indonesian captions video_id=%s", video_id)
                continue

            snippet = video.get("snippet") or {}
            content_details = video.get("contentDetails") or {}
            source_url = f"https://www.youtube.com/watch?v={video_id}"
            audio_storage_id = make_record_id(self.key, video_id)
            for segment_number, segment in enumerate(segments, start=1):
                yielded += 1
                yield Candidate(
                    source_id=f"{video_id}:{segment.start_ms}:{segment.end_ms}",
                    source_url=source_url,
                    text=segment.text,
                    license_info=YOUTUBE_LICENSE,
                    original_filename=f"{video_id}.flac" if audio_path else caption_path.name,
                    local_audio_path=audio_path,
                    audio_storage_id=audio_storage_id if audio_path else None,
                    speaker_id=str(snippet.get("channelId") or "") or None,
                    speaker_name=str(snippet.get("channelTitle") or "") or None,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    category="online video",
                    audio_available=audio_path is not None,
                    audio_unavailable_reason=None if audio_path else "ffmpeg not available",
                    extra={
                        "video_id": video_id,
                        "video_title": snippet.get("title"),
                        "channel_id": snippet.get("channelId"),
                        "channel_title": snippet.get("channelTitle"),
                        "published_at": snippet.get("publishedAt"),
                        "default_audio_language": snippet.get("defaultAudioLanguage"),
                        "duration": content_details.get("duration"),
                        "caption_source": caption_source,
                        "caption_file": caption_path.name,
                        "segment_number": segment_number,
                        "youtube_license": "creativeCommon",
                        "audio_available": audio_path is not None,
                    },
                )
        if videos and yielded == 0 and failures:
            raise RuntimeError(f"No YouTube Records Could Be Prepared; First Failure: {failures[0]}")
        if videos and yielded == 0:
            self.logger.info("YouTube discovery found videos but no eligible Indonesian caption segments")

    def _require_runtime(self) -> None:
        try:
            import yt_dlp  # noqa: F401
        except ImportError as exc:
            raise CollectorUnavailable(
                "yt-dlp Is Required; Install Project Dependencies Before Running the YouTube Collector"
            ) from exc

    @staticmethod
    def _ffmpeg_available() -> bool:
        return shutil.which("ffmpeg") is not None

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
            remaining = self.source_config.max_videos - len(video_ids)
            params: dict[str, str | int] = {
                "part": "id",
                "type": "video",
                "maxResults": min(50, remaining),
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
                    "part": "snippet,status,contentDetails",
                    "id": ",".join(video_ids[offset : offset + 50]),
                    "maxResults": 50,
                },
            )
            for item in response.get("items") or []:
                status = item.get("status") or {}
                snippet = item.get("snippet") or {}
                audio_language = str(snippet.get("defaultAudioLanguage") or "").lower()
                if status.get("license") != "creativeCommon":
                    continue
                if audio_language and audio_language != "id" and not audio_language.startswith("id-"):
                    continue
                videos.append(item)

        raw_path = self.storage.raw / self.key / "video-index.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
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
            raise RuntimeError(f"YouTube returned an invalid {resource} response")
        return response

    def _download_video_assets(
        self,
        video_id: str,
        include_audio: bool,
    ) -> tuple[Path | None, Path, str]:
        from yt_dlp import YoutubeDL

        video_directory = self.storage.raw / self.key / video_id
        video_directory.mkdir(parents=True, exist_ok=True)
        audio_path = video_directory / f"{video_id}.flac"
        existing_caption = self._cached_caption(video_directory, video_id)
        if existing_caption is not None and (not include_audio or audio_path.is_file()):
            source = "automatic" if ".auto." in existing_caption.name else "manual"
            return audio_path if include_audio else None, existing_caption, source

        source_url = f"https://www.youtube.com/watch?v={video_id}"
        inspect_options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": self.config.download.timeout_seconds,
            "retries": self.config.download.retries,
        }
        with YoutubeDL(inspect_options) as downloader:
            info = downloader.extract_info(source_url, download=False)
        caption_language, caption_source = self._select_caption_track(info)
        if caption_language is None:
            raise CaptionUnavailable("no eligible Indonesian caption track")

        output_template = str(video_directory / f"{video_id}.%(ext)s")
        audio_exists = audio_path.is_file()
        download_audio = include_audio and not audio_exists
        download_options = {
            **inspect_options,
            "skip_download": not download_audio,
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "writesubtitles": caption_source == "manual",
            "writeautomaticsub": caption_source == "automatic",
            "subtitleslangs": [caption_language],
            "subtitlesformat": "json3",
            "postprocessors": (
                [] if not download_audio else [{"key": "FFmpegExtractAudio", "preferredcodec": "flac"}]
            ),
        }
        with YoutubeDL(download_options) as downloader:
            downloader.download([source_url])

        caption_path = video_directory / f"{video_id}.{caption_language}.json3"
        if include_audio and not audio_path.is_file():
            raise RuntimeError("yt-dlp did not produce the expected FLAC audio")
        if not caption_path.is_file():
            raise RuntimeError("yt-dlp did not produce the expected caption file")
        if caption_source == "automatic":
            marked_caption = caption_path.with_name(
                caption_path.name.removesuffix(".json3") + ".auto.json3"
            )
            caption_path.replace(marked_caption)
            caption_path = marked_caption
        return audio_path if include_audio else None, caption_path, caption_source

    def _cached_caption(self, video_directory: Path, video_id: str) -> Path | None:
        candidates = sorted(video_directory.glob(f"{video_id}.*.json3"))
        manual = next((path for path in candidates if ".auto." not in path.name), None)
        if manual is not None:
            return manual
        if self.source_config.include_auto_captions:
            return next((path for path in candidates if ".auto." in path.name), None)
        return None

    def _select_caption_track(self, info: dict[str, Any]) -> tuple[str | None, str | None]:
        manual = info.get("subtitles") or {}
        language = self._indonesian_track(manual)
        if language:
            return language, "manual"
        if self.source_config.include_auto_captions:
            automatic = info.get("automatic_captions") or {}
            language = self._indonesian_track(automatic)
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
