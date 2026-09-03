from datetime import date

import pytest
from pydantic import ValidationError

from models.record import CorpusRecord, make_record_id


def test_record_has_exact_catalog_schema() -> None:
    record = CorpusRecord(
        id="abc123",
        text="  contoh teks  ",
        source="youtube",
        license=None,
        crawl_date=date(2026, 9, 4),
        split="train",
    )

    assert record.model_dump(mode="json") == {
        "id": "abc123",
        "text": "contoh teks",
        "source": "youtube",
        "license": "unknown",
        "crawl_date": "2026-09-04",
        "split": "train",
    }


def test_record_rejects_invalid_split() -> None:
    with pytest.raises(ValidationError):
        CorpusRecord(
            id="abc123",
            text="contoh teks",
            source="youtube",
            crawl_date=date(2026, 9, 4),
            split="validation",
        )


def test_record_id_is_deterministic() -> None:
    assert make_record_id("YouTube", "video:1") == make_record_id("youtube", "video:1")
