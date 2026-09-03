import json
from datetime import date

from core.config import SplitConfig
from core.metadata import assign_splits, load_records, split_counts, write_records
from models.record import CorpusRecord


def make_records(total: int) -> list[CorpusRecord]:
    return [
        CorpusRecord(
            id=f"record-{index:04d}",
            text=f"teks nomor {index}",
            source="youtube",
            license="unknown",
            crawl_date=date(2026, 9, 4),
            split="train",
        )
        for index in range(total)
    ]


def test_assign_splits_uses_dynamic_80_10_10_counts() -> None:
    records = assign_splits(make_records(500), SplitConfig())

    assert split_counts(records) == {"train": 400, "eval": 50, "test": 50}


def test_assign_splits_is_deterministic() -> None:
    config = SplitConfig(seed="stable-seed")

    first = assign_splits(make_records(37), config)
    second = assign_splits(reversed(make_records(37)), config)

    assert [(record.id, record.split) for record in first] == [
        (record.id, record.split) for record in second
    ]


def test_jsonl_contains_only_catalog_fields(tmp_path) -> None:
    path = tmp_path / "catalog" / "corpus.jsonl"
    records = assign_splits(make_records(1), SplitConfig())

    assert write_records(path, records) == 1
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert set(payload) == {"id", "text", "source", "license", "crawl_date", "split"}
    assert load_records(path) == records
