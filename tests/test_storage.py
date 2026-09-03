from core.storage import Storage


def test_storage_creates_only_raw_and_catalog_directories(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    assert storage.raw.is_dir()
    assert storage.catalog.is_dir()
    assert {path.name for path in storage.root.iterdir()} == {"raw", "catalog"}
    assert storage.catalog_path == storage.catalog / "corpus.jsonl"
