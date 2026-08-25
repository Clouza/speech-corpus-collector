from __future__ import annotations

import csv
import tarfile
from pathlib import Path
from typing import Iterable

import pandas as pd
from datasets import Dataset, Features, Value

from collectors.base import BaseCollector, Candidate, CollectionSummary
from core.licenses import resolve_license


FLEURS_FIELDNAMES = (
    "id",
    "filename",
    "raw_transcription",
    "transcription",
    "words",
    "num_samples",
    "gender",
)


def load_fleurs_metadata(metadata_path: Path, split: str) -> list[dict[str, str]]:
    features = Features({field: Value("string") for field in FLEURS_FIELDNAMES})
    frame = pd.read_csv(
        metadata_path,
        sep="\t",
        names=list(FLEURS_FIELDNAMES),
        header=None,
        dtype=str,
        keep_default_na=False,
        quoting=csv.QUOTE_NONE,
    )
    metadata = Dataset.from_pandas(frame, features=features, preserve_index=False)
    rows: list[dict[str, str]] = []
    for index, raw_row in enumerate(metadata):
        row = {field: str(raw_row.get(field) or "") for field in FLEURS_FIELDNAMES}
        if not row["id"] or not row["filename"] or not row["transcription"]:
            raise ValueError(f"invalid FLEURS {split} metadata at row {index + 1}: required field is empty")
        try:
            sample_count = int(row["num_samples"])
        except ValueError as exc:
            raise ValueError(
                f"invalid FLEURS {split} metadata at row {index + 1}: num_samples is not an integer"
            ) from exc
        if sample_count <= 0:
            raise ValueError(f"invalid FLEURS {split} metadata at row {index + 1}: num_samples must be positive")
        row["num_samples"] = str(sample_count)
        rows.append(row)
    return rows


class FleursCollector(BaseCollector):
    key = "fleurs"
    display_name = "Google FLEURS (id_id)"
    dataset_name = "Google FLEURS"
    dataset_version = "FLEURS-R main/id_id"

    def collect(self, dry_run: bool = False) -> CollectionSummary:
        if dry_run:
            summary = CollectionSummary(self.key)
            summary.planned = self.config.limits.max_records_per_source or 1
            return summary
        return super().collect(False)

    def discover(self) -> Iterable[Candidate]:
        repository = self.source_config.dataset_id or "google/fleurs-r"
        base = f"https://huggingface.co/datasets/{repository}/resolve/{self.source_config.revision}/data/id_id"
        raw = self.storage.raw / self.key
        selected = raw / "selected"
        selected.mkdir(parents=True, exist_ok=True)
        license_info = resolve_license("CC-BY-4.0")
        remaining = self.config.limits.max_records_per_source
        for split in ("dev", "train", "test"):
            if remaining == 0:
                return
            self._report(f"Reading FLEURS {split.title()} Metadata")
            metadata_path = raw / f"{split}.tsv"
            if not metadata_path.exists():
                self.downloader.download(f"{base}/{split}.tsv", metadata_path)
            rows = load_fleurs_metadata(metadata_path, split)
            needed = rows[:remaining] if remaining else rows
            audio_archive = raw / f"{split}.tar.gz"
            if not audio_archive.exists():
                self.downloader.download(f"{base}/audio/{split}.tar.gz", audio_archive)
            requested = {row["filename"]: row for row in needed}
            located: dict[str, Path] = {}
            self._report(f"Scanning FLEURS {split.title()} Audio Archive")
            with tarfile.open(audio_archive, "r:gz") as archive:
                for member in archive.getmembers():
                    filename = Path(member.name).name
                    if filename not in requested or not member.isfile():
                        continue
                    output = selected / f"{split}-{filename}"
                    if not output.exists():
                        stream = archive.extractfile(member)
                        if stream is None:
                            continue
                        output.write_bytes(stream.read())
                    located[filename] = output
            for row in needed:
                local = located.get(row["filename"])
                if local is None:
                    self.logger.error("missing FLEURS audio %s", row["filename"])
                    continue
                yield Candidate(
                    source_id=f"{split}:{row['id']}",
                    source_url=f"https://huggingface.co/datasets/{repository}",
                    text=row["transcription"],
                    raw_text=row["raw_transcription"],
                    license_info=license_info,
                    original_filename=row["filename"],
                    local_audio_path=local,
                    split="validation" if split == "dev" else split,
                    extra={
                        "gender": row["gender"],
                        "num_samples": int(row["num_samples"]),
                        "words": row["words"],
                        "dataset_config": "id_id",
                        "distribution": "google/fleurs-r",
                    },
                )
                if remaining is not None:
                    remaining -= 1
