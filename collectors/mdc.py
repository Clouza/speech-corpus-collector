from __future__ import annotations

import csv
import io
import tarfile
from pathlib import Path
from typing import Iterable

import httpx

from collectors.base import BaseCollector, Candidate, CollectionSummary, CollectorUnavailable
from core.config import credential
from core.licenses import resolve_license
from models.record import make_record_id


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"unsafe archive member: {member.name}")
        handle.extractall(destination, filter="data")


def build_audio_index(audio_paths: Iterable[Path]) -> dict[str, Path]:
    candidates: dict[str, set[Path]] = {}
    for audio_path in audio_paths:
        for key in (audio_path.name.casefold(), audio_path.stem.casefold()):
            candidates.setdefault(key, set()).add(audio_path)
    return {
        key: next(iter(paths))
        for key, paths in candidates.items()
        if len(paths) == 1
    }


def parse_time_ms(value: str) -> int:
    cleaned = value.strip().replace(",", ".")
    if ":" not in cleaned:
        return round(float(cleaned) * 1000)
    parts = [float(part) for part in cleaned.split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return round(seconds * 1000)


def mdc_access_error(error: httpx.HTTPStatusError, dataset_url: str) -> str:
    status = error.response.status_code
    if status == 401:
        return "MDC authentication failed; verify MDC_API_KEY in .env and create a current key in Profile > API"
    if status == 403:
        return (
            "MDC access denied; sign in and accept this dataset's terms before running the collector again: "
            f"{dataset_url}"
        )
    if status == 429:
        retry_after = error.response.headers.get("Retry-After")
        suffix = f"; retry after {retry_after} seconds" if retry_after else ""
        return f"MDC rate limit exceeded{suffix}"
    return f"MDC API request failed with HTTP {status}"


class MdcArchiveCollector(BaseCollector):
    credentials = ("MDC_API_KEY",)

    def collect(self, dry_run: bool = False) -> CollectionSummary:
        if dry_run:
            summary = CollectionSummary(self.key)
            summary.planned = 1
            return summary
        return super().collect(dry_run=False)

    def obtain_archive(self) -> Path:
        token = credential("MDC_API_KEY")
        if not token:
            raise CollectorUnavailable("MDC_API_KEY is required; accept the dataset terms in Mozilla Data Collective first")
        dataset_id = self.source_config.dataset_id
        if not dataset_id:
            raise ValueError(f"dataset_id is required for {self.key}")
        headers = {"Authorization": f"Bearer {token}"}
        self._report("Checking Dataset Access")
        details = self.downloader.request_json(
            "GET", f"https://mozilladatacollective.com/api/datasets/{dataset_id}", headers=headers
        )
        dataset_url = details.get("datasetUrl") or f"https://mozilladatacollective.com/datasets/{dataset_id}"
        try:
            response = self.downloader.request_json(
                "POST", f"https://mozilladatacollective.com/api/datasets/{dataset_id}/download", headers=headers
            )
        except httpx.HTTPStatusError as exc:
            raise CollectorUnavailable(mdc_access_error(exc, dataset_url)) from exc
        filename = response.get("filename") or f"{dataset_id}.tar.gz"
        archive = self.storage.raw / self.key / filename
        if not archive.exists():
            self.downloader.download(response["downloadUrl"], archive)
        details_path = self.storage.raw / self.key / "dataset-details.json"
        details_path.write_text(__import__("json").dumps(details, indent=2, sort_keys=True), encoding="utf-8")
        extracted = self.storage.raw / self.key / "extracted"
        marker = extracted / ".complete"
        if not marker.exists():
            self._report(f"Extracting {archive.name}")
            safe_extract_tar(archive, extracted)
            marker.write_text("ok\n", encoding="utf-8")
        else:
            self._report("Using Previously Extracted Archive")
        return extracted


class MdcTimestampedPodcastCollector(MdcArchiveCollector):
    dataset_url = ""

    def discover(self) -> Iterable[Candidate]:
        root = self.obtain_archive()
        self._report("Reading Podcast Transcripts")
        tables = list(root.rglob("*.tsv")) + list(root.rglob("*.csv"))
        if not tables:
            raise RuntimeError(f"{self.display_name} archive contains no TSV/CSV transcript")
        self._report("Indexing Podcast Audio Files")
        audio_index = build_audio_index(
            path
            for extension in ("*.mp3", "*.wav", "*.flac", "*.ogg")
            for path in root.rglob(extension)
        )
        license_info = resolve_license("CC-BY-SA-4.0")
        seen: set[str] = set()
        segment_number = 0
        for table in tables:
            for row in read_delimited(table):
                normalized = {key.strip().lower().replace("_", " "): value for key, value in row.items()}
                audio_name = (
                    normalized.get("audio file")
                    or normalized.get("audio")
                    or normalized.get("file")
                    or normalized.get("filename")
                    or normalized.get("path")
                )
                text = (
                    normalized.get("text")
                    or normalized.get("transcription")
                    or normalized.get("transcript")
                    or normalized.get("sentence")
                    or ""
                ).strip()
                if not audio_name or not text:
                    continue
                audio_name = audio_name.strip()
                audio_path = audio_index.get(Path(audio_name).name.casefold())
                if audio_path is None:
                    self.logger.error("missing podcast audio %s", audio_name)
                    continue
                start = normalized.get("start") or normalized.get("start time")
                end = normalized.get("end") or normalized.get("end time")
                if bool(start) != bool(end):
                    self.logger.error("incomplete podcast timestamps audio=%s table=%s", audio_name, table.name)
                    continue
                start_ms = parse_time_ms(start) if start else None
                end_ms = parse_time_ms(end) if end else None
                source_id = f"{audio_name}:{start_ms}:{end_ms}" if start_ms is not None else audio_name
                if source_id in seen:
                    continue
                seen.add(source_id)
                segment_number += 1
                yield Candidate(
                    source_id=source_id,
                    source_url=self.dataset_url,
                    text=text,
                    license_info=license_info,
                    original_filename=audio_name,
                    local_audio_path=audio_path,
                    audio_storage_id=make_record_id(self.key, audio_name),
                    speaker_id=normalized.get("speaker id") or normalized.get("speaker") or None,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    extra={
                        "episode_audio_identifier": audio_name,
                        "transcript_file": table.name,
                        "segment_number": segment_number,
                    },
                )


def read_delimited(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters="\t,;")
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))
