from __future__ import annotations

from pathlib import Path


class Storage:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.raw = self.root / "raw"
        self.catalog = self.root / "catalog"
        self.catalog_path = self.catalog / "corpus.jsonl"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.catalog.mkdir(parents=True, exist_ok=True)

    def raw_source(self, source: str) -> Path:
        directory = self.raw / source
        directory.mkdir(parents=True, exist_ok=True)
        return directory
