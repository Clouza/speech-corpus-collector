import httpx
import respx

from collectors.tatoeba import TatoebaCollector


@respx.mock
def test_tatoeba_reads_api_license_field(app_config) -> None:
    respx.get("https://api.tatoeba.org/unstable/audios").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "author": "speaker",
                        "license": "CC-BY-4.0",
                        "download_url": "https://example.test/audio.mp3",
                        "sentence": {"id": 2, "text": "Halo", "license": "CC-BY-4.0"},
                    }
                ],
                "paging": {},
            },
        )
    )

    summary = TatoebaCollector(app_config, app_config.sources["tatoeba"]).collect(dry_run=True)

    assert summary.planned == 1
    assert summary.unknown_license == 0
