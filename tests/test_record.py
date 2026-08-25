import pytest

from models.record import CorpusRecord, make_record_id


def base_record() -> dict:
    return {
        "record_id": "abc", "source": "test", "dataset_name": "Test", "dataset_version": "1",
        "source_id": "one", "source_url": "https://example.test/one", "audio_path": "audio/test/abc.wav",
        "audio_format": "wav", "text": "Halo", "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "license_status": "known",
        "original_filename": "one.wav", "sha256": "0" * 64,
    }


def test_record_id_is_stable_and_source_scoped() -> None:
    assert make_record_id("fleurs", "train:1") == make_record_id("fleurs", "train:1")
    assert make_record_id("fleurs", "train:1") != make_record_id("tatoeba", "train:1")


def test_timestamp_validation() -> None:
    with pytest.raises(ValueError):
        CorpusRecord.model_validate(base_record() | {"start_ms": 100, "end_ms": 100})


def test_extra_preserves_source_metadata() -> None:
    record = CorpusRecord.model_validate(base_record() | {"extra": {"speaker_age": "twenties"}})
    assert record.extra["speaker_age"] == "twenties"
