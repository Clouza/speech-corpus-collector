from collectors.movies import MoviesCollector
from collectors.youtube import YouTubeCollector


COLLECTORS = {
    "youtube": YouTubeCollector,
    "movies": MoviesCollector,
}


__all__ = ["COLLECTORS"]
