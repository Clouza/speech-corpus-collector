from __future__ import annotations

from typing import Iterable
from urllib.parse import parse_qs, urlparse

from collectors.base import BaseCollector, Candidate
from core.licenses import LicenseInfo, resolve_license


class TatoebaCollector(BaseCollector):
    key = "tatoeba"
    display_name = "Tatoeba Indonesian Audio"
    dataset_name = "Tatoeba Indonesian Sentence Audio"
    dataset_version = "API v1/unstable"

    def discover(self) -> Iterable[Candidate]:
        base = (self.source_config.api_base or "https://api.tatoeba.org").rstrip("/")
        after: str | None = None
        while True:
            page_limit = 100
            params: dict[str, str | int] = {"lang": "ind", "limit": page_limit}
            if after:
                params["after"] = after
            response = self.downloader.request_json("GET", f"{base}/unstable/audios", params=params)
            rows = response.get("data", [])
            if not rows:
                return
            for row in rows:
                sentence = row.get("sentence") or {}
                audio_id = str(row.get("id", ""))
                info = resolve_license(row.get("licence"))
                if info.status == "unknown":
                    info = LicenseInfo(info.identifier, info.url, None, None, None, "prohibited")
                sentence_license = resolve_license(sentence.get("license"))
                attribution = row.get("attribution_url") or f"https://tatoeba.org/en/audio/index/{audio_id}"
                yield Candidate(
                    source_id=audio_id,
                    source_url=attribution,
                    text=sentence.get("text", ""),
                    license_info=info,
                    original_filename=f"{audio_id}.mp3",
                    audio_url=row.get("download_url") or f"{base}/v1/audios/{audio_id}/file",
                    speaker_name=row.get("author"),
                    extra={
                        "sentence_id": sentence.get("id"),
                        "audio_id": row.get("id"),
                        "audio_contributor": row.get("author"),
                        "attribution_url": attribution,
                        "text_license": sentence_license.identifier,
                        "text_license_url": sentence_license.url,
                    },
                )
            paging = response.get("paging", {})
            next_page = paging.get("next") or paging.get("after")
            if isinstance(next_page, str) and next_page.startswith("http"):
                after = parse_qs(urlparse(next_page).query).get("after", [None])[0]
            else:
                after = str(next_page or rows[-1].get("id", ""))
            if len(rows) < page_limit or not after:
                return
