# Indonesian Corpus Collector

Collect Indonesian text from YouTube captions and movie subtitles. The collector stores raw source files for provenance and produces one compact JSONL catalog for training, evaluation, and API consumption.

The project does not download audio, generate transcripts, or train models.

Legacy collector modules remain in the repository for reference, but only `youtube` and `movies` are registered and supported by the active pipeline.

## Requirements

- Python 3.12 or newer
- A YouTube Data API v3 key
- An OpenSubtitles REST API key
- Optional OpenSubtitles account credentials for authenticated download limits

## Quick Start

1. Create and activate the virtual environment. Skip this step when one is already active.

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies and create the local configuration files.

   ```powershell
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   Copy-Item config.example.yaml config.yaml
   ```

3. Fill in `.env`.

   ```dotenv
   YOUTUBE_API_KEY=
   OPENSUBTITLES_API_KEY=
   OPENSUBTITLES_USERNAME=
   OPENSUBTITLES_PASSWORD=
   ```

   `OPENSUBTITLES_USERNAME` and `OPENSUBTITLES_PASSWORD` are optional. When both are present, the collector requests a bearer token automatically and keeps it only in memory. API keys and passwords must never be committed.

4. Review source queries, limits, and split ratios in `config.yaml`, then verify and collect.

   ```powershell
   python .\main.py list-sources
   python .\main.py sources # legacy alias
   python .\main.py collect all
   ```

The catalog is written to `data/catalog/corpus.jsonl`.

## Other Commands

```powershell
python .\main.py collect youtube
python .\main.py collect movies
python .\main.py collect all --dry-run
python .\main.py status
python .\main.py validate
```

The collector is idempotent at catalog level. Existing records are merged by deterministic ID, then every record is assigned a fresh deterministic split using the configured ratios and seed.

## Output

```text
data/
├── raw/
│   ├── youtube/
│   └── movies/
└── catalog/
    └── corpus.jsonl
```

YouTube JSON3 captions and movie subtitle files remain in `raw`. No audio, per-record transcript, manifest, source metadata, or Parquet output is created.

Each line of `corpus.jsonl` contains exactly these fields:

```json
{"id":"abc123","text":"gue tadi ke sana bareng temen","source":"youtube/Judul Video","license":"CC-BY-3.0","crawl_date":"2026-09-04","split":"train"}
```

| Field | Meaning |
| --- | --- |
| `id` | Deterministic 24-character record identifier |
| `text` | Normalized caption or subtitle text |
| `source` | Explicit media label, such as `youtube/Judul Video` or `movies/Judul Film (2026)` |
| `license` | Source-provided license, otherwise `unknown` |
| `crawl_date` | UTC collection date in `YYYY-MM-DD` format |
| `split` | `train`, `eval`, or `test` |

## Dynamic Splits

The default split configuration is:

```yaml
splits:
  train: 0.8
  eval: 0.1
  test: 0.1
  seed: "indonesian-corpus-v1"
```

Counts use a largest-remainder allocation, so all records are assigned even when the total does not divide cleanly. For 500 records, the catalog contains exactly 400 train, 50 eval, and 50 test records. The seed makes the shuffle reproducible.

## Collection Progress

Live collection displays one progress row per source with the processed video or subtitle count, percentage, generated record count, elapsed time, estimated remaining time, and current media title. The estimate becomes more accurate after at least one source item has completed.

Dry-run reports the configured maximum source items. It does not guess the final record count because the number of caption or subtitle cues is only known after each raw subtitle has been parsed.

## Source Behavior

### YouTube

The YouTube collector uses the official Data API to discover Creative Commons videos with Indonesian captions. `yt-dlp` downloads only the selected JSON3 caption track; media and audio are always skipped. Manual captions are preferred. Automatic captions are used only when `include_auto_captions` is enabled.

### Movies

The movies collector uses the current [OpenSubtitles.com REST API](https://opensubtitles.stoplight.io/docs/opensubtitles-api/e3750fd63a100-getting-started). Search is restricted to Indonesian subtitles and ordered by download count. Supported raw subtitle formats are SRT, WebVTT, ASS/SSA, and MicroDVD SUB.

When `search_query` is empty, the collector uses the latest-movie discovery endpoint. A non-empty query must contain at least three characters.

The [OpenSubtitles free allowance](https://opensubtitles.tawk.help/article/about-the-api) is currently 5 downloads per 24 hours without an account or 20 downloads per 24 hours with a free account. When account credentials are configured, the collector reads the account's actual remaining quota and stops cleanly when it is exhausted.

OpenSubtitles does not consistently expose a reusable license identifier for every subtitle. When no license is present in the response, catalog records use `"license":"unknown"`.

Movie entries without a downloadable, supported subtitle are skipped because this project does not transcribe audio.

## Proof-of-Concept Corpus API

This optional read-only API exposes `data/catalog/corpus.jsonl` without changing the collector workflow. It uses the existing project dependencies and Python standard library.

### Configuration

Set a private random value of at least 32 characters in `.env`. A local value is already present in this repository's ignored `.env`; `.env.example` intentionally leaves it blank.

```dotenv
CORPUS_API_SECRET=replace-with-at-least-32-random-characters
```

Generated key hashes and expiration metadata are stored in the ignored `data/api_keys.json` registry. Plaintext keys are never stored.

### Generate an API Key

Generate a short key with 128 bits of randomness. The result uses the 26-character format `cps_<22-random-characters>` and is printed only once.

```powershell
python .\api.py generate-key --name local-client --expires-in-days 30
```

The default lifetime is 30 days and the maximum is 365 days. The server reads the registry for every request, so newly generated keys work immediately without restarting it.

### Run the API

Run the API from the repository root. It listens on localhost port `8765` by default.

```powershell
python .\api.py serve
```

Use another interface, port, catalog, or key registry when needed:

```powershell
python .\api.py serve --host 0.0.0.0 --port 8765 --catalog .\data\catalog\corpus.jsonl --key-store .\data\api_keys.json
```

### Authentication and Endpoints

Send the generated key as a Bearer token. Authentication and expiration validation happen when a protected endpoint is requested.

```powershell
$apiKey = python .\api.py generate-key --name local-client
$headers = @{ Authorization = "Bearer $apiKey" }
```

| Method | Endpoint | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | No | Check API availability |
| `GET` | `/corpus` | Bearer | List, filter, and search records |
| `GET` | `/corpus/export` | Bearer | Stream matching records as JSONL |

`/corpus` accepts `limit` (default `100`, maximum `1000`), `offset`, `q`, `split`, `source`, and `license`. Search is case-insensitive across `text` and `source`. The `source` filter accepts either a complete source value or a source prefix such as `youtube`. Other filters are exact matches.

```powershell
Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:8765/corpus?limit=25&offset=0"
Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:8765/corpus?q=jakarta&split=train&source=youtube"
Invoke-WebRequest -Headers $headers -Uri "http://127.0.0.1:8765/corpus/export?split=train&source=youtube" -OutFile .\corpus-export.jsonl
```

### Proof-of-Concept Limits

This API is not intended to replace a production API gateway. Rotating `CORPUS_API_SECRET` invalidates every registered key. Use HTTPS through a trusted reverse proxy before any non-local deployment, and never commit `.env`, generated keys, or the key registry.
