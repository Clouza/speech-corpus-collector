# Indonesian Corpus Collector

Collect Indonesian speech datasets from legitimate public distributions, preserve provenance and licensing, normalize metadata, validate audio, and store everything locally. This project performs dataset acquisition only; it does not train models.

## Quick Start

Requirements: Python 3.12 or newer, Git, and `ffmpeg` available on `PATH`. Python 3.14 is supported with the pinned binary `pyarrow` dependency.

```powershell
git clone <repository-url>
Set-Location indonesian-corpus-collector

py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --only-binary=pyarrow -r requirements.txt

Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
python main.py sources
python main.py collect all --dry-run
```

For development and tests, use `python -m pip install -e ".[dev]"` after installing `requirements.txt`.

If the activated shell cannot find an installed package, run the virtual environment explicitly:

```powershell
.\.venv\Scripts\python.exe main.py sources
```

## Credentials and Configuration

The only supported credential is:

```dotenv
MDC_API_KEY=
```

It is required for Common Voice, Podcast Homostoria, and Podcast Hari Minggoean. Create the key in Mozilla Data Collective and accept the terms on each dataset page before collecting; a valid key alone can still receive HTTP 403 until those terms are accepted.

Copy `config.example.yaml` to `config.yaml`. To perform a small collection, set:

```yaml
limits:
  max_records_per_source: 3
```

Unknown licenses remain blocked by default. Keep `licensing.allow_unknown: false` unless you have independently reviewed the source.

## Commands

| Command | Purpose |
| --- | --- |
| `python main.py sources` | List collectors, availability, configuration, and required credentials. |
| `python main.py collect <source>` | Collect one source, for example `fleurs` or `inesco`. |
| `python main.py collect all` | Run every enabled collector; one source failure does not stop the others. |
| `python main.py collect all --dry-run` | Show the planned collection without downloading audio. |
| `python main.py status` | Summarize manifests and locally collected records. |
| `python main.py validate` | Revalidate the existing local corpus. |
| `python main.py export` | Rebuild combined JSONL and Parquet metadata. |

You may collect sources one at a time or use `all`. Repeated runs resume from manifests and skip already validated files.

## Included Sources

| CLI Name | Dataset | License | Access |
| --- | --- | --- | --- |
| `common-voice` | Mozilla Common Voice Indonesian | CC0-1.0 | `MDC_API_KEY` and accepted dataset terms |
| `fleurs` | Google FLEURS `id_id` | CC BY 4.0 | Public Hugging Face distribution |
| `inesco` | Indonesian Expressive Speech Corpus | CC BY 4.0 | Public Mendeley Data distribution |
| `homostoria` | Podcast Homostoria | CC BY-SA 4.0 | `MDC_API_KEY` and accepted dataset terms |
| `hari-minggoean` | Podcast Hari Minggoean | CC BY-SA 4.0 | `MDC_API_KEY` and accepted dataset terms |
| `librivox` | LibriVox Indonesia | Public Domain | Public dataset distribution |
| `tatoeba` | Tatoeba Indonesian audio | Per-item | Public API; only explicitly reusable audio is downloaded |

Large archive-based sources may download an upstream archive before applying a record limit. A source that becomes unavailable fails gracefully and does not trigger a fallback scraper.

## Output

```text
data/
├── audio/<source>/<record_id>.<ext>
├── transcripts/<source>/<record_id>.txt
├── raw/<source>/
├── manifests/<source>.json
└── metadata/
    ├── sources/<source>.jsonl
    ├── records.jsonl
    └── records.parquet
logs/collector-YYYY-MM-DD.log
```

Audio is never stored in a database. Raw source metadata is retained, and deterministic record IDs, SHA-256 hashes, and manifests provide deduplication and resume support.

## Adding a New Source

Use the following workflow so the new collector inherits storage, validation, licensing, resume, and export behavior.

1. Verify the current official API, repository, download endpoint, dataset version, and license. Do not add brittle scraping or bypass authentication, access controls, rate limits, or usage terms. If automation is not permitted, document the source as manual/unavailable instead.
2. Add `collectors/<source_key>.py`. Subclass `BaseCollector`, define its identity fields, and implement `discover()` to yield `Candidate` objects.
3. Add the import and CLI name to `COLLECTORS` in `collectors/__init__.py`.
4. Add the source under `sources` in `config.example.yaml`. CLI names use hyphens, while YAML keys use underscores, such as `example-source` and `example_source`.
5. If authentication is genuinely required, list the environment variable in the collector's `credentials` tuple, read it through the existing credential helper, and add only its empty name to `.env.example`. Never hardcode or log secrets.
6. Resolve every item's stated license through `core/licenses.py`. Add a known license mapping only when its explicit terms are verified. Unknown or prohibited licenses must remain rejected by default.
7. Add mocked tests for parsing, normalization, license filtering, dry-run behavior, manifest resume/deduplication, and source failure handling.
8. Run the verification commands below with a low record limit, then run the same collection again to confirm idempotency.

A minimal collector looks like this:

```python
from collections.abc import Iterable

from collectors.base import BaseCollector, Candidate
from core.licenses import resolve_license


class ExampleSourceCollector(BaseCollector):
    key = "example-source"
    display_name = "Example Source Indonesian"
    dataset_name = "Example Source"
    dataset_version = "1.0"
    credentials = ("EXAMPLE_TOKEN",)  # omit when public

    def discover(self) -> Iterable[Candidate]:
        license_info = resolve_license("CC-BY-4.0")
        for item in self._load_official_index():
            yield Candidate(
                source_id=str(item["id"]),
                source_url=item["attribution_url"],
                text=item["transcript"],
                license_info=license_info,
                original_filename=item["filename"],
                audio_url=item["audio_url"],
                speaker_id=item.get("speaker_id"),
                extra={"upstream_metadata": item},
            )
```

Each candidate requires `source_id`, `source_url`, non-empty `text`, `license_info`, `original_filename`, and one audio input: `audio_url`, `audio_bytes`, or `local_audio_path`. Put timestamps, speaker, split, category, and emotion in their dedicated fields; keep remaining source metadata in `extra`.

Register it in `collectors/__init__.py`:

```python
from collectors.example_source import ExampleSourceCollector

COLLECTORS["example-source"] = ExampleSourceCollector
```

Add its configuration:

```yaml
sources:
  example_source:
    enabled: true
    dataset_id: "publisher/dataset"
    version: "1.0"
```

Verify the integration:

```powershell
python main.py sources
python main.py collect example-source --dry-run
python main.py collect example-source
python main.py validate
python main.py export
python -m pytest
```

`BaseCollector` supplies stable record IDs, license enforcement, deterministic storage, hashing, manifests, deduplication, validation, transcripts, and normalized source metadata. The source module should focus on official discovery and accurate field mapping.

## Licensing

Every record retains its original source, URL, dataset version, retrieval time, license, and license attributes. Combining datasets does not replace or change their individual licenses. The recorded flags mirror explicit source license terms and are not legal advice.

## Troubleshooting

- `ModuleNotFoundError: rich`: the command is using a different Python installation; activate `.venv` or use `.\.venv\Scripts\python.exe` explicitly.
- `Failed to build pyarrow`: upgrade `pip`, then install with `python -m pip install --only-binary=pyarrow -r requirements.txt`.
- MDC HTTP 401: check `MDC_API_KEY`. MDC HTTP 403: sign in and accept that dataset's terms.
- Inspect the newest file under `logs/` for source, record, retry, license, and validation errors.
