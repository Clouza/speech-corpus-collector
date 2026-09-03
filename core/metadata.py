from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from core.config import SplitConfig
from models.record import CorpusRecord, CorpusSplit


SPLIT_NAMES: tuple[CorpusSplit, ...] = ("train", "eval", "test")


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


def _split_counts(total: int, config: SplitConfig) -> dict[CorpusSplit, int]:
    ratios = {"train": config.train, "eval": config.eval, "test": config.test}
    exact = {name: total * ratio for name, ratio in ratios.items()}
    counts = {name: int(value) for name, value in exact.items()}
    remainder = total - sum(counts.values())
    order = sorted(
        SPLIT_NAMES,
        key=lambda name: (exact[name] - counts[name], ratios[name], -SPLIT_NAMES.index(name)),
        reverse=True,
    )
    for name in order[:remainder]:
        counts[name] += 1
    return counts


def assign_splits(records: Iterable[CorpusRecord], config: SplitConfig) -> list[CorpusRecord]:
    by_id = {record.id: record for record in records}
    ordered = sorted(
        by_id.values(),
        key=lambda record: hashlib.sha256(
            f"{config.seed}:{record.id}".encode("utf-8")
        ).digest(),
    )
    counts = _split_counts(len(ordered), config)
    train_boundary = counts["train"]
    eval_boundary = counts["train"] + counts["eval"]
    assigned: list[CorpusRecord] = []
    for index, record in enumerate(ordered):
        if index < train_boundary:
            split: CorpusSplit = "train"
        elif index < eval_boundary:
            split = "eval"
        else:
            split = "test"
        assigned.append(record.model_copy(update={"split": split}))
    return sorted(assigned, key=lambda record: record.id)


def write_records(path: Path, records: Iterable[CorpusRecord]) -> int:
    materialized = list(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in materialized:
            handle.write(record.model_dump_json() + "\n")
    temporary.replace(path)
    return len(materialized)


def split_counts(records: Iterable[CorpusRecord]) -> dict[str, int]:
    counts = {name: 0 for name in SPLIT_NAMES}
    for record in records:
        counts[record.split] += 1
    return counts
