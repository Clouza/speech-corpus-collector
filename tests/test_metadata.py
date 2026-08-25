from pathlib import Path

from core.metadata import append_record, load_record_ids
from models.record import CorpusRecord


def make_record(record_id: str) -> CorpusRecord:
    return CorpusRecord(
        record_id=record_id,
        source="test",
        dataset_name="Test",
        dataset_version="1",
        source_id=record_id,
        source_url=f"https://example.test/{record_id}",
        audio_path=f"audio/{record_id}.wav",
        audio_format="wav",
        text="Halo dunia",
        license="CC0-1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        license_status="known",
        original_filename=f"{record_id}.wav",
        sha256="abc",
    )


def test_append_record_reuses_and_updates_known_ids(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "records.jsonl"
    known_ids: set[str] = set()

    monkeypatch.setattr(
        "core.metadata.load_record_ids",
        lambda _: (_ for _ in ()).throw(AssertionError("metadata reloaded")),
    )

    assert append_record(path, make_record("one"), known_record_ids=known_ids)
    assert not append_record(path, make_record("one"), known_record_ids=known_ids)
    assert append_record(path, make_record("two"), known_record_ids=known_ids)
    assert known_ids == {"one", "two"}
    assert load_record_ids(path) == {"one", "two"}
