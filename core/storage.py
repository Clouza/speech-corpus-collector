from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any


MANIFEST_STATES = ("pending", "downloaded", "validated", "failed", "skipped")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Storage:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.audio = self.root / "audio"
        self.transcripts = self.root / "transcripts"
        self.raw = self.root / "raw"
        self.metadata = self.root / "metadata"
        self.source_metadata = self.metadata / "sources"
        self.manifests = self.root / "manifests"
        for directory in (
            self.audio,
            self.transcripts,
            self.raw,
            self.metadata,
            self.source_metadata,
            self.manifests,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def audio_path(self, source: str, record_id: str, extension: str) -> Path:
        safe_extension = extension.lower().lstrip(".") or "bin"
        directory = self.audio / source
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{record_id}.{safe_extension}"

    def transcript_path(self, source: str, record_id: str) -> Path:
        directory = self.transcripts / source
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{record_id}.txt"


class Manifest:
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self._lock = Lock()
        self._data: dict[str, Any] = {"source": source, "items": {}}
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("items"), dict):
                self._data = loaded

    def status(self, source_id: str) -> str | None:
        item = self._data["items"].get(source_id)
        return item.get("status") if isinstance(item, dict) else None

    def set(self, source_id: str, status: str, **details: Any) -> None:
        if status not in MANIFEST_STATES:
            raise ValueError(f"invalid manifest status: {status}")
        with self._lock:
            current = self._data["items"].get(source_id, {})
            self._data["items"][source_id] = {**current, **details, "status": status}

    def counts(self) -> dict[str, int]:
        counts = {state: 0 for state in MANIFEST_STATES}
        for item in self._data["items"].values():
            status = item.get("status")
            if status in counts:
                counts[status] += 1
        return counts

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
