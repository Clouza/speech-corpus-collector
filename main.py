from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from collectors import COLLECTORS
from collectors.base import CollectionSummary
from core.config import AppConfig, load_config
from core.logging import configure_logging
from core.metadata import export_records, load_records
from core.storage import Manifest, Storage
from core.validator import validate_record

console = Console()


def source_config_key(cli_key: str) -> str:
    return cli_key.replace("-", "_")


def selected_sources(config: AppConfig, requested: str) -> list[str]:
    if requested != "all":
        if requested not in COLLECTORS:
            raise ValueError(f"unknown source: {requested}")
        return [requested]
    return [
        key for key in COLLECTORS
        if config.sources.get(source_config_key(key)) and config.sources[source_config_key(key)].enabled
    ]


def run_collectors(config: AppConfig, requested: str, dry_run: bool) -> list[CollectionSummary]:
    summaries: list[CollectionSummary] = []
    for key in selected_sources(config, requested):
        source_config = config.sources.get(source_config_key(key))
        if source_config is None:
            summaries.append(CollectionSummary(key, error="source is missing from configuration"))
            continue
        try:
            collector = COLLECTORS[key](config, source_config)
            summaries.append(collector.collect(dry_run=dry_run))
        except Exception as exc:
            summaries.append(CollectionSummary(key, error=str(exc)))
    return summaries


def print_collection_summary(summaries: list[CollectionSummary], dry_run: bool = False) -> None:
    table = Table(title="Collection Summary")
    table.add_column("Source")
    table.add_column("Result")
    table.add_column("Downloaded", justify="right")
    table.add_column("Validated", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Unknown License", justify="right")
    if dry_run:
        table.add_column("Planned", justify="right")
    for item in summaries:
        result = f"FAILED - {item.error}" if item.error else "SUCCESS"
        row = [item.source, result, str(item.downloaded), str(item.validated), str(item.skipped), str(item.failed), str(item.unknown_license)]
        if dry_run:
            row.append(str(item.planned))
        table.add_row(*row)
    console.print(table)


def command_sources(config: AppConfig) -> None:
    table = Table(title="Available Collectors")
    table.add_column("Source")
    table.add_column("Enabled")
    table.add_column("Availability")
    table.add_column("Credentials")
    for key, collector_type in COLLECTORS.items():
        configured = config.sources.get(source_config_key(key))
        table.add_row(
            key,
            "Yes" if configured and configured.enabled else "No",
            collector_type.availability.title() if collector_type.availability == "available" else collector_type.availability,
            ", ".join(collector_type.credentials) or "None",
        )
    console.print(table)


def command_validate(config: AppConfig) -> int:
    storage = Storage(config.storage.root)
    records = []
    for source_file in storage.source_metadata.glob("*.jsonl"):
        records.extend(load_records(source_file))
    valid = failed = unknown = 0
    total_seconds = 0.0
    for record in records:
        result = validate_record(record, storage.root, config.licensing.allow_unknown)
        if result.valid:
            valid += 1
            total_seconds += result.duration_seconds or 0
        else:
            failed += 1
            console.print(f"[red]Validation Failed[/red] {record.record_id}: {', '.join(result.errors)}")
        if record.license_status != "known":
            unknown += 1
    console.print(f"Validated: {valid:,}")
    console.print(f"Failed: {failed:,}")
    console.print(f"Unknown License: {unknown:,}")
    console.print(f"Total Audio: {total_seconds / 3600:.2f} Hours")
    return 1 if failed else 0


def command_status(config: AppConfig) -> None:
    storage = Storage(config.storage.root)
    table = Table(title="Corpus Status")
    table.add_column("Source")
    for state in ("pending", "downloaded", "validated", "failed", "skipped"):
        table.add_column(state.replace("_", " ").title(), justify="right")
    for key in COLLECTORS:
        manifest = Manifest(storage.manifests / f"{key}.json", key)
        counts = manifest.counts()
        table.add_row(key, *(str(counts[state]) for state in ("pending", "downloaded", "validated", "failed", "skipped")))
    console.print(table)


def command_export(config: AppConfig) -> None:
    storage = Storage(config.storage.root)
    count, jsonl, parquet = export_records(storage.source_metadata, storage.metadata)
    console.print(f"Exported {count:,} Records")
    console.print(f"JSONL: {jsonl}")
    console.print(f"Parquet: {parquet}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect License-Aware Indonesian Speech Corpora")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Configuration file path")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("sources", help="List available collectors")
    collect = subcommands.add_parser("collect", help="Collect one or all sources")
    collect.add_argument("source", choices=[*COLLECTORS, "all"])
    collect.add_argument("--dry-run", action="store_true", help="Show planned work without downloads")
    subcommands.add_parser("validate", help="Validate existing records")
    subcommands.add_parser("status", help="Show manifest statistics")
    subcommands.add_parser("export", help="Rebuild combined metadata")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except Exception as exc:
        console.print(f"[red]Configuration Error:[/red] {exc}")
        return 2
    configure_logging(args.config.parent / "logs")
    if args.command == "sources":
        command_sources(config)
        return 0
    if args.command == "collect":
        summaries = run_collectors(config, args.source, args.dry_run)
        print_collection_summary(summaries, args.dry_run)
        if not args.dry_run:
            command_export(config)
        return 1 if any(summary.error for summary in summaries) else 0
    if args.command == "validate":
        return command_validate(config)
    if args.command == "status":
        command_status(config)
        return 0
    if args.command == "export":
        command_export(config)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
