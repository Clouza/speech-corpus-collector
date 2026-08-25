from __future__ import annotations

from pathlib import Path
from typing import Iterable

from collectors.base import Candidate
from collectors.mdc import MdcArchiveCollector, read_delimited
from core.licenses import resolve_license


class CommonVoiceCollector(MdcArchiveCollector):
    key = "common-voice"
    display_name = "Mozilla Common Voice 26.0 Indonesian"
    dataset_name = "Mozilla Common Voice Scripted Speech Indonesian"
    dataset_version = "26.0 (2026-06-12)"

    def discover(self) -> Iterable[Candidate]:
        root = self.obtain_archive()
        seen: set[str] = set()
        self._report("Reading Common Voice Metadata")
        metadata_files = []
        for split in ("train", "dev", "test", "validated", "other", "invalidated"):
            metadata_files.extend(root.rglob(f"{split}.tsv"))
        if not metadata_files:
            raise RuntimeError("Common Voice archive contains no recognized TSV metadata")
        self._report("Indexing Common Voice Audio Files")
        audio_index = {path.name: path for path in root.rglob("*.mp3")}
        license_info = resolve_license("CC0-1.0")
        for metadata_file in metadata_files:
            split = metadata_file.stem
            for row in read_delimited(metadata_file):
                clip_name = row.get("path", "").strip()
                sentence = row.get("sentence", "").strip()
                if not clip_name or not sentence or clip_name in seen:
                    continue
                seen.add(clip_name)
                audio_path = audio_index.get(Path(clip_name).name)
                if audio_path is None:
                    self.logger.error("missing Common Voice clip %s", clip_name)
                    continue
                yield Candidate(
                    source_id=clip_name,
                    source_url="https://mozilladatacollective.com/datasets/cmqinqwef00xonr07z4vbfovw",
                    text=sentence,
                    license_info=license_info,
                    original_filename=clip_name,
                    local_audio_path=audio_path,
                    speaker_id=row.get("client_id") or None,
                    split=split,
                    extra={
                        "sentence_id": row.get("sentence_id"),
                        "age": row.get("age"),
                        "gender": row.get("gender"),
                        "accents": row.get("accents"),
                        "variant": row.get("variant"),
                        "locale": row.get("locale", "id"),
                        "up_votes": row.get("up_votes"),
                        "down_votes": row.get("down_votes"),
                    },
                )
