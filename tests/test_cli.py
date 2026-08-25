from collectors.base import BaseCollector
from main import run_collectors


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
