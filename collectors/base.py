from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from core.config import AppConfig, SourceConfig
from core.downloader import Downloader
from core.licenses import LicenseInfo, download_allowed
from core.metadata import append_record, load_records, replace_records
from core.storage import Manifest, Storage, sha256_file
from core.validator import validate_record
from models.record import CorpusRecord, make_record_id


@dataclass
class Candidate:
    source_id: str
    source_url: str
    text: str
    license_info: LicenseInfo
    original_filename: str
    audio_url: str | None = None
    audio_bytes: bytes | None = None
    local_audio_path: Path | None = None
    audio_storage_id: str | None = None
    raw_text: str | None = None
    speaker_id: str | None = None
    speaker_name: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    split: str | None = None
    category: str | None = None
    emotion: str | None = None
    audio_available: bool = True
    audio_unavailable_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionSummary:
    source: str
    discovered: int = 0
    downloaded: int = 0
    validated: int = 0
    skipped: int = 0
    failed: int = 0
    unknown_license: int = 0
    transcript_only: int = 0
    planned: int = 0
    error: str | None = None
    duration_seconds: float = 0.0
    failure_examples: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


class CollectorUnavailable(RuntimeError):
    pass


class BaseCollector:
    key = "base"
    display_name = "Base"
    dataset_name = ""
    dataset_version = ""
    availability = "available"
    credentials: tuple[str, ...] = ()

    def __init__(
        self,
        config: AppConfig,
        source_config: SourceConfig,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.source_config = source_config
        self.progress_callback = progress_callback
        self.storage = Storage(config.storage.root)
        self.manifest = Manifest(self.storage.manifests / f"{self.key}.json", self.key)
        self.logger = logging.getLogger(f"collector.{self.key}")
        self.downloader = Downloader(
            config.download.timeout_seconds,
            config.download.retries,
            config.download.user_agent,
            status_callback=progress_callback,
        )

    def _report(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def discover(self) -> Iterable[Candidate]:
        raise NotImplementedError

    def collect(self, dry_run: bool = False) -> CollectionSummary:
        summary = CollectionSummary(self.key)
        limit = self.config.limits.max_records_per_source
        examined = 0
        discovered = 0
        metadata_path = self.storage.source_metadata / f"{self.key}.jsonl"
        known_records: dict[str, CorpusRecord] = {}
        known_record_ids: set[str] = set()
        pending_replacements: dict[str, CorpusRecord] = {}
        last_progress_at = 0.0
        try:
            if not dry_run:
                self._report("Loading Existing Metadata")
                known_records = {record.record_id: record for record in load_records(metadata_path)}
                known_record_ids = set(known_records)
            self._report("Discovering Candidates")
            for candidate in self.discover():
                if limit is not None and examined >= limit:
                    break
                discovered += 1
                if not download_allowed(candidate.license_info, self.config.licensing.allow_unknown):
                    summary.unknown_license += 1
                    summary.skipped += 1
                    if not dry_run:
                        self.manifest.set(candidate.source_id, "skipped", reason="unknown license")
                    if summary.unknown_license <= 3 or summary.unknown_license % 100 == 0:
                        self.logger.info(
                            "license rejection source_id=%s license=%s rejected_count=%s",
                            candidate.source_id,
                            candidate.license_info.identifier,
                            summary.unknown_license,
                        )
                    last_progress_at = self._report_candidate_progress(
                        summary,
                        discovered,
                        last_progress_at,
                        force=discovered == 1,
                    )
                    continue
                examined += 1
                record_id = make_record_id(self.key, candidate.source_id)
                extension = (
                    Path(candidate.original_filename).suffix.lstrip(".") or "bin"
                    if candidate.audio_available
                    else None
                )
                destination = None
                if candidate.audio_available and extension is not None:
                    audio_id = candidate.audio_storage_id or record_id
                    destination = self.storage.audio_path(self.key, audio_id, extension)
                current_status = self.manifest.status(candidate.source_id)
                existing_record = known_records.get(record_id)
                audio_complete = (
                    candidate.audio_available
                    and destination is not None
                    and destination.exists()
                    and existing_record is not None
                    and existing_record.audio_available
                )
                transcript_complete = not candidate.audio_available and existing_record is not None
                already_complete = (
                    current_status == "validated"
                    and (audio_complete or transcript_complete)
                )
                if already_complete:
                    summary.skipped += 1
                    last_progress_at = self._report_candidate_progress(
                        summary,
                        discovered,
                        last_progress_at,
                        force=discovered == 1,
                    )
                    continue
                if dry_run:
                    summary.planned += 1
                    last_progress_at = self._report_candidate_progress(
                        summary,
                        discovered,
                        last_progress_at,
                        force=discovered == 1,
                    )
                    continue
                self.manifest.set(candidate.source_id, "pending", record_id=record_id)
                try:
                    digest: str | None = None
                    relative_path: str | None = None
                    if candidate.audio_available:
                        if destination is None:
                            raise ValueError("candidate audio destination is unavailable")
                        if not destination.exists():
                            if candidate.audio_bytes is not None:
                                destination.write_bytes(candidate.audio_bytes)
                            elif candidate.local_audio_path is not None:
                                shutil.copy2(candidate.local_audio_path, destination)
                            elif candidate.audio_url:
                                self.downloader.download(candidate.audio_url, destination)
                            else:
                                raise ValueError("candidate has no audio payload or URL")
                        digest = sha256_file(destination)
                        self.manifest.set(candidate.source_id, "downloaded", record_id=record_id, sha256=digest)
                        relative_path = destination.relative_to(self.storage.root).as_posix()
                    record = CorpusRecord(
                        record_id=record_id,
                        source=self.key,
                        dataset_name=self.dataset_name,
                        dataset_version=self.dataset_version,
                        source_id=candidate.source_id,
                        source_url=candidate.source_url,
                        audio_available=candidate.audio_available,
                        audio_path=relative_path,
                        audio_format=extension.lower() if extension else None,
                        audio_language="id" if candidate.audio_available else None,
                        transcript_language="id",
                        text=candidate.text,
                        raw_text=candidate.raw_text,
                        speaker_id=candidate.speaker_id,
                        speaker_name=candidate.speaker_name,
                        start_ms=candidate.start_ms,
                        end_ms=candidate.end_ms,
                        split=candidate.split,
                        category=candidate.category,
                        emotion=candidate.emotion,
                        license=candidate.license_info.identifier,
                        license_url=candidate.license_info.url,
                        license_status=candidate.license_info.status,
                        commercial_use_allowed=candidate.license_info.commercial_use_allowed,
                        attribution_required=candidate.license_info.attribution_required,
                        share_alike_required=candidate.license_info.share_alike_required,
                        original_filename=candidate.original_filename,
                        sha256=digest,
                        retrieved_at=datetime.now(UTC),
                        extra=candidate.extra,
                    )
                    result = validate_record(
                        record,
                        self.storage.root,
                        self.config.licensing.allow_unknown,
                        precomputed_sha256=digest,
                    )
                    record.audio_duration_seconds = (
                        (candidate.end_ms - candidate.start_ms) / 1000
                        if candidate.audio_available
                        and candidate.start_ms is not None
                        and candidate.end_ms is not None
                        else result.duration_seconds
                    )
                    self.storage.transcript_path(self.key, record_id).write_text(candidate.text.strip() + "\n", encoding="utf-8")
                    if (
                        result.valid
                        and existing_record is not None
                        and not existing_record.audio_available
                        and record.audio_available
                    ):
                        pending_replacements[record_id] = record
                    elif existing_record is None:
                        append_record(metadata_path, record, known_record_ids=known_record_ids)
                    known_records[record_id] = record
                    self._append_raw(candidate)
                    if candidate.audio_available:
                        summary.downloaded += 1
                    else:
                        summary.transcript_only += 1
                    if result.valid:
                        summary.validated += 1
                        manifest_details: dict[str, Any] = {
                            "record_id": record_id,
                            "audio_available": candidate.audio_available,
                        }
                        if digest:
                            manifest_details["sha256"] = digest
                        if candidate.audio_unavailable_reason:
                            manifest_details["audio_unavailable_reason"] = candidate.audio_unavailable_reason
                        self.manifest.set(candidate.source_id, "validated", **manifest_details)
                    else:
                        summary.failed += 1
                        self.manifest.set(candidate.source_id, "failed", record_id=record_id, errors=result.errors)
                        self._add_failure_example(summary, candidate.source_id, "; ".join(result.errors))
                        self.logger.error("validation failure record_id=%s errors=%s", record_id, result.errors)
                except Exception as exc:
                    summary.failed += 1
                    self.manifest.set(candidate.source_id, "failed", record_id=record_id, error=str(exc))
                    self._add_failure_example(summary, candidate.source_id, str(exc))
                    self.logger.exception("collection failure record_id=%s", record_id)
                last_progress_at = self._report_candidate_progress(
                    summary,
                    discovered,
                    last_progress_at,
                    force=discovered == 1,
                )
        except Exception as exc:
            summary.error = str(exc)
            self.logger.exception("source collection failed")
        finally:
            summary.discovered = discovered
            if pending_replacements:
                try:
                    replace_records(metadata_path, pending_replacements)
                except Exception as exc:
                    summary.error = f"Failed to Upgrade Transcript-Only Metadata: {exc}"
                    self.logger.exception("metadata upgrade failed")
            self._report("Saving Manifest")
            self.manifest.flush()
            self.downloader.close()
        return summary

    def _report_candidate_progress(
        self,
        summary: CollectionSummary,
        discovered: int,
        last_progress_at: float,
        force: bool = False,
    ) -> float:
        now = time.monotonic()
        if force or discovered % 100 == 0 or now - last_progress_at >= 1:
            self._report(
                f"Processing Candidates: {discovered:,} Seen, "
                f"{summary.validated:,} Validated, {summary.skipped:,} Skipped, {summary.failed:,} Failed"
            )
            return now
        return last_progress_at

    @staticmethod
    def _add_failure_example(summary: CollectionSummary, source_id: str, error: str) -> None:
        if len(summary.failure_examples) < 3:
            summary.failure_examples.append(f"{source_id}: {error}")

    def _append_raw(self, candidate: Candidate) -> None:
        path = self.storage.raw / self.key / "source-metadata.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "source_id": candidate.source_id,
            "source_url": candidate.source_url,
            "original_filename": candidate.original_filename,
            "text": candidate.text,
            "raw_text": candidate.raw_text,
            "audio_available": candidate.audio_available,
            "audio_unavailable_reason": candidate.audio_unavailable_reason,
            "extra": candidate.extra,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
