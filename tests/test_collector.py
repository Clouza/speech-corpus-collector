import wave
from pathlib import Path

from collectors.base import BaseCollector, Candidate
from core.licenses import resolve_license


def wav_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)
    return path.read_bytes()


class DummyCollector(BaseCollector):
    key = "dummy"
    dataset_name = "Dummy"
    dataset_version = "1"

    def __init__(self, *args, payload: bytes, **kwargs):
        super().__init__(*args, **kwargs)
        self.payload = payload

    def discover(self):
        yield Candidate("1", "https://example.test/1", "Halo", resolve_license("CC0"), "one.wav", audio_bytes=self.payload)


def test_dry_run_does_not_write_audio(app_config, tmp_path: Path) -> None:
    summary = DummyCollector(app_config, app_config.sources["tatoeba"], payload=wav_bytes(tmp_path)).collect(dry_run=True)
    assert summary.planned == 1
    assert not list((app_config.storage.root / "audio").rglob("*.wav"))


def test_repeated_collection_is_idempotent(app_config, tmp_path: Path) -> None:
    payload = wav_bytes(tmp_path)
    first = DummyCollector(app_config, app_config.sources["tatoeba"], payload=payload).collect()
    second = DummyCollector(app_config, app_config.sources["tatoeba"], payload=payload).collect()
    assert first.validated == 1
    assert second.downloaded == 0
    assert second.skipped == 1
    assert len(list((app_config.storage.root / "audio" / "dummy").glob("*.wav"))) == 1
