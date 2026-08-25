from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from models.record import CorpusRecord


def load_records(path: Path) -> list[CorpusRecord]:
    if not path.is_file():
        return []
    records: list[CorpusRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(CorpusRecord.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"invalid record at {path}:{line_number}") from exc
    return records


def append_record(path: Path, record: CorpusRecord) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if any(existing.record_id == record.record_id for existing in load_records(path)):
        return False
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")
    return True


def export_records(source_directory: Path, output_directory: Path) -> tuple[int, Path, Path]:
    by_id: dict[str, CorpusRecord] = {}
    for source_file in sorted(source_directory.glob("*.jsonl")):
        for record in load_records(source_file):
            by_id[record.record_id] = record
    records = [by_id[key] for key in sorted(by_id)]
    output_directory.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_directory / "records.jsonl"
    parquet_path = output_directory / "records.parquet"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")
    rows = [record.model_dump(mode="json") for record in records]
    frame = pd.DataFrame(rows)
    if "extra" in frame.columns:
        frame["extra"] = frame["extra"].map(lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True))
    frame.to_parquet(parquet_path, index=False)
    return len(records), jsonl_path, parquet_path
