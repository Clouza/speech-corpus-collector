from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs


DEFAULT_LIMIT = 100
MAXIMUM_LIMIT = 1000
MAXIMUM_OFFSET = 1_000_000
MAXIMUM_QUERY_LENGTH = 200
FILTER_FIELDS = frozenset({"q", "split", "source", "license"})
LIST_FIELDS = FILTER_FIELDS | {"limit", "offset"}


class CatalogError(ValueError):
    """Raised when the corpus catalog cannot be read safely."""


class QueryError(CatalogError):
    """Raised when corpus query parameters are invalid."""


@dataclass(frozen=True)
class CorpusQuery:
    search: str | None = None
    split: str | None = None
    source: str | None = None
    license: str | None = None
    limit: int = DEFAULT_LIMIT
    offset: int = 0


@dataclass(frozen=True)
class CorpusPage:
    records: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


def parse_query(query_string: str, *, export: bool = False) -> CorpusQuery:
    try:
        parameters = parse_qs(
            query_string,
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=20,
        )
    except ValueError as exc:
        raise QueryError("Invalid Query Parameters") from exc

    allowed_fields = FILTER_FIELDS if export else LIST_FIELDS
    unknown_fields = sorted(set(parameters) - allowed_fields)
    if unknown_fields:
        raise QueryError(f"Unknown Query Parameter: {unknown_fields[0]}")
    duplicates = sorted(key for key, values in parameters.items() if len(values) != 1)
    if duplicates:
        raise QueryError(f"Query Parameter Must Be Unique: {duplicates[0]}")

    split = _optional_text(parameters, "split", maximum_length=10)
    if split is not None and split not in {"train", "eval", "test"}:
        raise QueryError("Split Must Be train, eval, or test")

    return CorpusQuery(
        search=_optional_text(parameters, "q", maximum_length=MAXIMUM_QUERY_LENGTH),
        split=split,
        source=_optional_text(parameters, "source", maximum_length=MAXIMUM_QUERY_LENGTH),
        license=_optional_text(parameters, "license", maximum_length=MAXIMUM_QUERY_LENGTH),
        limit=(
            DEFAULT_LIMIT
            if export
            else _integer_parameter(parameters, "limit", DEFAULT_LIMIT, 1, MAXIMUM_LIMIT)
        ),
        offset=(
            0
            if export
            else _integer_parameter(parameters, "offset", 0, 0, MAXIMUM_OFFSET)
        ),
    )


def list_records(catalog_path: Path, query: CorpusQuery) -> CorpusPage:
    records: list[dict[str, Any]] = []
    total = 0
    for record, _ in iter_catalog(catalog_path):
        if not record_matches(record, query):
            continue
        if query.offset <= total < query.offset + query.limit:
            records.append(record)
        total += 1
    return CorpusPage(
        records=records,
        total=total,
        limit=query.limit,
        offset=query.offset,
    )


def export_records(catalog_path: Path, query: CorpusQuery) -> Iterator[bytes]:
    for record, raw_line in iter_catalog(catalog_path):
        if record_matches(record, query):
            yield raw_line.encode("utf-8") + b"\n"


def iter_catalog(catalog_path: Path) -> Iterator[tuple[dict[str, Any], str]]:
    if not catalog_path.is_file():
        raise CatalogError("Corpus Catalog Does Not Exist")
    try:
        with catalog_path.open("r", encoding="utf-8") as catalog:
            for line_number, raw_line in enumerate(catalog, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CatalogError(
                        f"Corpus Catalog Contains Invalid JSON on Line {line_number}"
                    ) from exc
                if not isinstance(record, dict):
                    raise CatalogError(
                        f"Corpus Catalog Contains a Non-Object on Line {line_number}"
                    )
                yield record, line
    except OSError as exc:
        raise CatalogError("Corpus Catalog Cannot Be Read") from exc


def record_matches(record: Mapping[str, Any], query: CorpusQuery) -> bool:
    if query.split is not None and record.get("split") != query.split:
        return False

    if query.license is not None:
        record_license = str(record.get("license", "")).casefold()
        if record_license != query.license.casefold():
            return False

    if query.source is not None:
        record_source = str(record.get("source", "")).casefold()
        expected_source = query.source.casefold()
        if record_source != expected_source and not record_source.startswith(
            f"{expected_source}/"
        ):
            return False

    if query.search is not None:
        search = query.search.casefold()
        text = str(record.get("text", "")).casefold()
        source = str(record.get("source", "")).casefold()
        if search not in text and search not in source:
            return False
    return True


def _optional_text(
    parameters: Mapping[str, Sequence[str]],
    name: str,
    *,
    maximum_length: int,
) -> str | None:
    if name not in parameters:
        return None
    value = parameters[name][0].strip()
    if not value:
        raise QueryError(f"Query Parameter Must Not Be Empty: {name}")
    if len(value) > maximum_length:
        raise QueryError(f"Query Parameter Is Too Long: {name}")
    return value


def _integer_parameter(
    parameters: Mapping[str, Sequence[str]],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if name not in parameters:
        return default
    try:
        value = int(parameters[name][0])
    except ValueError as exc:
        raise QueryError(f"Query Parameter Must Be an Integer: {name}") from exc
    if not minimum <= value <= maximum:
        raise QueryError(
            f"Query Parameter Must Be Between {minimum} and {maximum}: {name}"
        )
    return value
