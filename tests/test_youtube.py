import json

from collectors.youtube import YouTubeCollector, parse_caption_json


def test_parse_youtube_caption_json(tmp_path) -> None:
    path = tmp_path / "caption.json3"
    path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "tStartMs": 1000,
                        "dDurationMs": 2000,
                        "segs": [{"utf8": "Halo "}, {"utf8": "Indonesia"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert parse_caption_json(path)[0].text == "Halo Indonesia"


def test_discover_uses_explicit_video_source_and_reports_progress(tmp_path) -> None:
    caption_path = tmp_path / "caption.json3"
    caption_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "tStartMs": 1000,
                        "dDurationMs": 2000,
                        "segs": [{"utf8": "Halo Indonesia"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    collector = YouTubeCollector.__new__(YouTubeCollector)
    collector._require_runtime = lambda: None
    collector._search_videos = lambda: [
        {"id": "video-123", "snippet": {"title": "Judul &amp; Video"}}
    ]
    collector._download_caption = lambda video_id: (caption_path, "manual")
    events = []
    collector._report = lambda *args: events.append(args)

    candidates = list(collector.discover())

    assert candidates[0].source == "youtube/Judul & Video"
    assert events[-1][1:] == (1, 1, 1)
