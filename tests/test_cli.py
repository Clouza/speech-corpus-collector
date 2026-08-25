from collectors.base import BaseCollector
from main import collection_outcome, run_collectors


class BrokenCollector(BaseCollector):
    key = "broken"

    def discover(self):
        raise RuntimeError("publisher unavailable")


class EmptyCollector(BaseCollector):
    key = "empty"

    def discover(self):
        return iter(())


def test_collector_error_isolation(app_config, monkeypatch) -> None:
    import main
    monkeypatch.setattr(main, "COLLECTORS", {"inesco": BrokenCollector, "tatoeba": EmptyCollector})
    summaries = run_collectors(app_config, "all", False)
    assert len(summaries) == 2
    assert summaries[0].error == "publisher unavailable"
    assert summaries[1].error is None


def test_progress_reports_source_failure(app_config, monkeypatch) -> None:
    import main

    events: list[tuple[str, str, str]] = []
    monkeypatch.setattr(main, "COLLECTORS", {"inesco": BrokenCollector})

    summaries = run_collectors(app_config, "all", False, progress_callback=lambda *event: events.append(event))

    assert summaries[0].error == "publisher unavailable"
    assert events[0] == ("inesco", "running", "Starting Collection")
    assert events[-1] == ("inesco", "failed", "publisher unavailable")


def test_unknown_license_only_outcome_is_explicit() -> None:
    from collectors.base import CollectionSummary

    summary = CollectionSummary("tatoeba", skipped=10, unknown_license=10)

    assert collection_outcome(summary) == ("empty", "No Eligible Records with a Known License")
