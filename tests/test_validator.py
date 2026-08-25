import wave
from pathlib import Path

from core.storage import sha256_file
from core.validator import validate_record
from models.record import CorpusRecord


def make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 1600)


def record_for(path: Path) -> CorpusRecord:
    return CorpusRecord(
        record_id="abc", source="test", dataset_name="Test", dataset_version="1", source_id="1",
        source_url="https://example.test/1", audio_path=str(path), audio_format="wav", text="Halo dunia",
        license="CC0-1.0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        license_status="known", original_filename="one.wav", sha256=sha256_file(path),
    )


def test_valid_audio_record(tmp_path: Path) -> None:
    path = tmp_path / "one.wav"
    make_wav(path)
    result = validate_record(record_for(path), tmp_path)
    assert result.valid
    assert result.duration_seconds == 0.1


def test_empty_transcript_and_missing_audio_fail(tmp_path: Path) -> None:
    record = CorpusRecord(
        record_id="abc", source="test", dataset_name="Test", dataset_version="1", source_id="1",
        source_url="https://example.test/1", audio_path=str(tmp_path / "missing.wav"), audio_format="wav", text=" ",
        license="", license_url="", license_status="unknown", original_filename="one.wav", sha256="",
    )
    result = validate_record(record, tmp_path)
    assert not result.valid
    assert "audio file does not exist" in result.errors
    assert "transcript is empty" in result.errors


def test_precomputed_hash_avoids_hashing_audio_again(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "one.wav"
    make_wav(path)
    record = record_for(path)

    monkeypatch.setattr("core.validator.sha256_file", lambda _: (_ for _ in ()).throw(AssertionError("rehash")))

    result = validate_record(record, tmp_path, precomputed_sha256=record.sha256)

    assert result.valid
