import hashlib
import json
from pathlib import Path

from core.storage import Manifest, Storage, sha256_file


def test_hashing(tmp_path: Path) -> None:
    path = tmp_path / "audio.bin"
    path.write_bytes(b"speech")
    assert sha256_file(path) == hashlib.sha256(b"speech").hexdigest()


def test_manifest_round_trip_and_counts(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = Manifest(path, "test")
    manifest.set("1", "pending")
    manifest.set("1", "validated", sha256="abc")
    manifest.flush()
    reloaded = Manifest(path, "test")
    assert reloaded.status("1") == "validated"
    assert reloaded.counts()["validated"] == 1
    assert json.loads(path.read_text())["items"]["1"]["sha256"] == "abc"


def test_deterministic_audio_path(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    assert storage.audio_path("test", "abc", ".WAV") == tmp_path / "audio" / "test" / "abc.wav"
