from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.config import AppConfig, SourceConfig
from core.downloader import Downloader
from core.storage import Storage
from models.record import CorpusRecord, make_record_id


@dataclass(frozen=True)
class Candidate:
    source_id: str
    text: str
    license: str = "unknown"
    source: str | None = None


@dataclass(frozen=True)
class CollectionProgress:
    message: str
    completed_items: int | None = None
    total_items: int | None = None
    collected_records: int = 0
    finished: bool = False


ProgressCallback = Callable[[CollectionProgress], None]


def make_source_label(source: str, title: object, fallback: str) -> str:
    normalized_title = re.sub(r"\s+", " ", html.unescape(str(title or ""))).strip()
    return f"{source}/{normalized_title or fallback.strip()}"


@dataclass
class CollectionSummary:
    source: str
    discovered: int = 0
    collected: int = 0
    skipped: int = 0
    failed: int = 0
    planned: int = 0
    error: str | None = None
    duration_seconds: float = 0.0
    failure_examples: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    records: list[CorpusRecord] = field(default_factory=list, repr=False)


class CollectorUnavailable(RuntimeError):
    pass


class BaseCollector:
    key = "base"
    display_name = "Base"
    availability = "available"
    credentials: tuple[str, ...] = ()
    optional_credentials: tuple[str, ...] = ()

    def __init__(
        self,
        config: AppConfig,
        source_config: SourceConfig,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.config = config
        self.source_config = source_config
        self.progress_callback = progress_callback
        self.storage = Storage(config.storage.root)
        self.logger = logging.getLogger(f"collector.{self.key}")
        self.downloader = Downloader(
            config.download.timeout_seconds,
            config.download.retries,
            config.download.user_agent,
            status_callback=lambda message: self._report(message),
        )

    def _report(
        self,
        message: str,
        completed_items: int | None = None,
        total_items: int | None = None,
        collected_records: int = 0,
        finished: bool = False,
    ) -> None:
        if self.progress_callback is not None:
            self.progress_callback(
                CollectionProgress(
                    message=message,
                    completed_items=completed_items,
                    total_items=total_items,
                    collected_records=collected_records,
                    finished=finished,
                )
            )

    def discover(self) -> Iterable[Candidate]:
        raise NotImplementedError

    def planned_items(self) -> int:
        return self.config.limits.max_records_per_source or 0

    def collect(self, dry_run: bool = False) -> CollectionSummary:
        summary = CollectionSummary(self.key)
        started_at = time.monotonic()
        try:
            if dry_run:
                summary.planned = self.planned_items()
                return summary
            limit = self.config.limits.max_records_per_source
            known_ids: set[str] = set()
            crawl_date = datetime.now(UTC).date()
            for candidate in self.discover():
                summary.discovered += 1
                if limit is not None and summary.collected >= limit:
                    break
                text = candidate.text.strip()
                source_id = candidate.source_id.strip()
                if not text or not source_id:
                    summary.skipped += 1
                    continue
                record_id = make_record_id(self.key, source_id)
                if record_id in known_ids:
                    summary.skipped += 1
                    continue
                known_ids.add(record_id)
                summary.records.append(
                    CorpusRecord(
                        id=record_id,
                        text=text,
                        source=candidate.source or self.key,
                        license=candidate.license,
                        crawl_date=crawl_date,
                        split="train",
                    )
                )
                summary.collected += 1
        except Exception as exc:
            summary.error = str(exc)
            self.logger.exception("source collection failed")
        finally:
            summary.duration_seconds = time.monotonic() - started_at
            self.downloader.close()
        return summary
    @staticmethod
    def add_failure(summary: CollectionSummary, source_id: str, error: str) -> None:
        summary.failed += 1
        if len(summary.failure_examples) < 3:
            summary.failure_examples.append(f"{source_id}: {error}")
