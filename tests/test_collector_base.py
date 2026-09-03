from collectors.base import make_source_label


def test_make_source_label_normalizes_title() -> None:
    assert make_source_label("youtube", "  Judul\nVideo  ", "fallback") == "youtube/Judul Video"


def test_make_source_label_falls_back_to_media_id() -> None:
    assert make_source_label("movies", "", "movie-123") == "movies/movie-123"
