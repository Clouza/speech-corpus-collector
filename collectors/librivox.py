from __future__ import annotations

import csv
import gzip
import tarfile
from pathlib import Path
from typing import Iterable

from collectors.base import BaseCollector, Candidate, CollectionSummary
from core.licenses import resolve_license


class LibriVoxCollector(BaseCollector):
    key = "librivox"
    display_name = "LibriVox Indonesia 1.0"
    dataset_name = "LibriVox Indonesia"
    dataset_version = "1.0"

    def collect(self, dry_run: bool = False) -> CollectionSummary:
        if dry_run:
            summary = CollectionSummary(self.key)
            summary.planned = self.config.limits.max_records_per_source or 1
            return summary
        return super().collect(False)

    def discover(self) -> Iterable[Candidate]:
        repository = self.source_config.dataset_id or "indonesian-nlp/librivox-indonesia"
        base = f"https://huggingface.co/datasets/{repository}/resolve/{self.source_config.revision}/data"
        raw = self.storage.raw / self.key
        raw.mkdir(parents=True, exist_ok=True)
        files = ("metadata_train.csv.gz", "metadata_test.csv.gz", "audio_train.tgz", "audio_test.tgz")
        for name in files:
            target = raw / name
            if not target.exists():
                self.downloader.download(f"{base}/{name}", target)

        rows: list[tuple[str, dict[str, str]]] = []
        for split in ("train", "test"):
            with gzip.open(raw / f"metadata_{split}.csv.gz", "rt", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    language = (row.get("language") or row.get("lang") or "").lower()
                    if language in {"ind", "id", "indonesian"}:
                        rows.append((split, row))
        limit = self.config.limits.max_records_per_source
        if limit:
            rows = rows[:limit]
        requested = {Path(row.get("path") or row.get("audio") or "").as_posix(): (split, row) for split, row in rows}
        requested_by_name = {Path(key).name: key for key in requested}
        extracted = raw / "selected"
        extracted.mkdir(exist_ok=True)
        located: dict[str, Path] = {}
        for split in ("train", "test"):
            with tarfile.open(raw / f"audio_{split}.tgz", "r:gz") as archive:
                for member in archive.getmembers():
                    normalized = Path(member.name).as_posix()
                    matching = normalized if normalized in requested else requested_by_name.get(Path(normalized).name)
                    if not matching or matching in located or not member.isfile():
                        continue
                    output = extracted / f"{split}-{Path(matching).name}"
                    stream = archive.extractfile(member)
                    if stream is None:
                        continue
                    output.write_bytes(stream.read())
                    located[matching] = output
        info = resolve_license("CC0-1.0")
        for path_key, (split, row) in requested.items():
            local = located.get(path_key)
            if not local:
                self.logger.error("missing LibriVox archive member %s", path_key)
                continue
            source_id = path_key or local.name
            yield Candidate(
                source_id=source_id,
                source_url=f"https://huggingface.co/datasets/{repository}",
                text=(row.get("sentence") or row.get("text") or "").strip(),
                license_info=info,
                original_filename=Path(path_key).name,
                local_audio_path=local,
                speaker_id=row.get("reader") or None,
                split=split,
                extra={
                    "language_original": row.get("language"),
                    "upstream_source": "LibriVox",
                    "upstream_license": "Public Domain",
                    "dataset_license": "CC0-1.0",
                    "metadata": row,
                },
            )
