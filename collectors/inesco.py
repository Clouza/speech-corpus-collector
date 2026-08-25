from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from collectors.base import BaseCollector, Candidate, CollectionSummary
from core.licenses import resolve_license


INESCO_FILENAME = re.compile(
    r"^(?P<speaker>[a-z]+)_(?P<emotion_code>[has])(?P<sentence_id>\d{3})\.wav$",
    re.IGNORECASE,
)
INESCO_EMOTIONS = {"h": "happiness", "a": "anger", "s": "sadness"}


def load_inesco_transcripts(path: Path) -> dict[int, tuple[str, str]]:
    frame = pd.read_excel(path, sheet_name="600 Sentences", dtype=str)
    normalized = {str(column).strip().upper(): column for column in frame.columns}
    required = ("NO", "SENTENCES (INDONESIAN)", "TYPE OF EXPRESSION")
    missing = [column for column in required if column not in normalized]
    if missing:
        raise ValueError(f"INESCO transcript is missing columns: {', '.join(missing)}")
    records: dict[int, tuple[str, str]] = {}
    for row_number, row in frame.iterrows():
        try:
            sentence_id = int(str(row[normalized["NO"]]).strip())
        except ValueError as exc:
            raise ValueError(f"invalid INESCO sentence ID at row {row_number + 2}") from exc
        text = str(row[normalized["SENTENCES (INDONESIAN)"]]).strip()
        emotion = str(row[normalized["TYPE OF EXPRESSION"]]).strip().lower()
        if not text or not emotion:
            raise ValueError(f"empty INESCO transcript field at row {row_number + 2}")
        records[sentence_id] = (text, emotion)
    return records


def parse_inesco_filename(filename: str) -> tuple[str, int, str]:
    match = INESCO_FILENAME.fullmatch(Path(filename).name)
    if not match:
        raise ValueError(f"unrecognized INESCO filename: {filename}")
    emotion_code = match.group("emotion_code").lower()
    return match.group("speaker").lower(), int(match.group("sentence_id")), INESCO_EMOTIONS[emotion_code]


class InescoCollector(BaseCollector):
    key = "inesco"
    display_name = "INESCO Indonesian Expressive Speech Corpus"
    dataset_name = "INESCO"
    dataset_version = "1 (10.17632/hzrznx3xs5.1)"
    dataset_url = "https://data.mendeley.com/datasets/hzrznx3xs5/1"
    default_dataset_id = "hzrznx3xs5"
    default_version = "1"

    def collect(self, dry_run: bool = False) -> CollectionSummary:
        if dry_run:
            summary = CollectionSummary(self.key)
            summary.planned = self.config.limits.max_records_per_source or 2399
            return summary
        return super().collect(False)

    def discover(self) -> Iterable[Candidate]:
        dataset_id = self.source_config.dataset_id or self.default_dataset_id
        version = self.source_config.version or self.default_version
        api_base = f"https://data.mendeley.com/public-api/datasets/{dataset_id}"
        raw = self.storage.raw / self.key / f"version-{version}"
        raw.mkdir(parents=True, exist_ok=True)

        details = self.downloader.request_json("GET", api_base, params={"version": version})
        license_info = resolve_license((details.get("data_licence") or {}).get("short_name"))
        if license_info.identifier != "CC-BY-4.0":
            raise RuntimeError(f"unexpected INESCO dataset license: {license_info.identifier or 'unknown'}")
        (raw / "dataset-details.json").write_text(
            json.dumps(details, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        folders = self.downloader.request_json("GET", f"{api_base}/folders/{version}")
        if not isinstance(folders, list):
            raise RuntimeError("Mendeley returned an invalid INESCO folder index")
        (raw / "folders.json").write_text(
            json.dumps(folders, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        folder_by_id = {folder["id"]: folder for folder in folders}
        parent_ids = {folder.get("parent_id") for folder in folders if folder.get("parent_id")}
        leaf_folders = sorted(
            (folder for folder in folders if folder["id"] not in parent_ids),
            key=lambda folder: folder["name"],
        )

        transcript_folder = next((folder for folder in folders if folder["name"] == "INESCO Dataset"), None)
        if transcript_folder is None:
            raise RuntimeError("INESCO transcript folder is missing")
        transcript_files = self._folder_files(api_base, transcript_folder["id"], version)
        transcript_file = next(
            (item for item in transcript_files if item.get("filename", "").lower().endswith(".xls")),
            None,
        )
        if transcript_file is None:
            raise RuntimeError("INESCO_sentences.xls is missing")
        transcript_path = raw / "INESCO_sentences.xls"
        if not transcript_path.exists():
            self.downloader.download(transcript_file["content_details"]["download_url"], transcript_path)
        transcripts = load_inesco_transcripts(transcript_path)

        for folder in leaf_folders:
            items = self._folder_files(api_base, folder["id"], version)
            (raw / f"files-{folder['name']}.json").write_text(
                json.dumps(items, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            gender = self._folder_gender(folder, folder_by_id)
            for item in items:
                filename = str(item.get("filename") or "")
                if not filename.lower().endswith(".wav"):
                    continue
                try:
                    speaker_id, sentence_id, filename_emotion = parse_inesco_filename(filename)
                except ValueError:
                    self.logger.error("unrecognized INESCO audio filename %s", filename)
                    continue
                transcript = transcripts.get(sentence_id)
                if transcript is None:
                    self.logger.error("missing INESCO transcript sentence_id=%s", sentence_id)
                    continue
                text, emotion = transcript
                if emotion != filename_emotion:
                    self.logger.warning(
                        "INESCO emotion mismatch filename=%s sheet=%s", filename_emotion, emotion
                    )
                content = item.get("content_details") or {}
                download_url = content.get("download_url")
                file_id = str(item.get("id") or "")
                if not download_url or not file_id:
                    self.logger.error("missing INESCO download metadata filename=%s", filename)
                    continue
                yield Candidate(
                    source_id=file_id,
                    source_url=self.dataset_url,
                    text=text,
                    license_info=license_info,
                    original_filename=filename,
                    audio_url=download_url,
                    speaker_id=speaker_id,
                    emotion=emotion,
                    category="expressive speech",
                    extra={
                        "sentence_id": sentence_id,
                        "speaker_code": speaker_id,
                        "speaker_gender": gender,
                        "mendeley_file_id": file_id,
                        "publisher_sha256": content.get("sha256_hash"),
                        "doi": "10.17632/hzrznx3xs5.1",
                    },
                )

    def _folder_files(self, api_base: str, folder_id: str, version: str) -> list[dict[str, Any]]:
        files = self.downloader.request_json(
            "GET",
            f"{api_base}/files",
            params={"folder_id": folder_id, "version": version},
        )
        if not isinstance(files, list):
            raise RuntimeError(f"Mendeley returned an invalid file index for folder {folder_id}")
        return files

    @staticmethod
    def _folder_gender(folder: dict[str, Any], folder_by_id: dict[str, dict[str, Any]]) -> str | None:
        current = folder
        while current.get("parent_id"):
            current = folder_by_id.get(current["parent_id"], {})
            name = str(current.get("name") or "").lower()
            if name in {"female", "male"}:
                return name
        return None
