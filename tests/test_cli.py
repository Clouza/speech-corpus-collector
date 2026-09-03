import main as cli

from collectors.base import CollectionProgress, CollectionSummary
from core.config import AppConfig, SourceConfig, StorageConfig
from main import collect_with_progress


class FakeCollector:
    def __init__(self, config, source_config, progress_callback=None) -> None:
        self.progress_callback = progress_callback

    def collect(self, dry_run=False) -> CollectionSummary:
        assert not dry_run
        assert self.progress_callback is not None
        self.progress_callback(CollectionProgress("Preparing youtube/Judul Video", 0, 1, 0))
        self.progress_callback(CollectionProgress("Processed youtube/Judul Video", 1, 1, 25))
        return CollectionSummary("youtube", discovered=25, collected=25)


def test_collect_with_progress_reports_items_and_records(monkeypatch, tmp_path) -> None:
    config = AppConfig(
        storage=StorageConfig(root=tmp_path / "data"),
        sources={"youtube": SourceConfig(enabled=True, max_videos=1)},
    )
    monkeypatch.setitem(cli.COLLECTORS, "youtube", FakeCollector)

    summaries = collect_with_progress(config, "youtube")

    assert summaries[0].collected == 25


def test_list_sources_command_and_legacy_alias_are_available() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["list-sources"]).command == "list-sources"
    assert parser.parse_args(["sources"]).command == "sources"
