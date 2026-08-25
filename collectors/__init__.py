from collectors.common_voice import CommonVoiceCollector
from collectors.fleurs import FleursCollector
from collectors.hari_minggoean import HariMinggoeanCollector
from collectors.homostoria import HomostoriaCollector
from collectors.inesco import InescoCollector
from collectors.librivox import LibriVoxCollector
from collectors.tatoeba import TatoebaCollector

COLLECTORS = {
    "common-voice": CommonVoiceCollector,
    "fleurs": FleursCollector,
    "librivox": LibriVoxCollector,
    "homostoria": HomostoriaCollector,
    "hari-minggoean": HariMinggoeanCollector,
    "inesco": InescoCollector,
    "tatoeba": TatoebaCollector,
}

__all__ = ["COLLECTORS"]
