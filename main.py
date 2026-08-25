from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from collectors import COLLECTORS
from collectors.base import CollectionSummary
from core.config import AppConfig, load_config
from core.logging import configure_logging
from core.metadata import export_records, load_records
from core.storage import Manifest, Storage
from core.validator import validate_record

console = Console()
RunProgressCallback = Callable[[str, str, str], None]


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


def run_collectors(
    config: AppConfig,
    requested: str,
    dry_run: bool,
    progress_callback: RunProgressCallback | None = None,
) -> list[CollectionSummary]:
    summaries: list[CollectionSummary] = []
    logger = logging.getLogger("collector")
    for key in selected_sources(config, requested):
        started_at = time.monotonic()
        if progress_callback is not None:
            progress_callback(key, "running", "Starting Collection")
        logger.info("source collection started source=%s", key)
        source_config = config.sources.get(source_config_key(key))
        if source_config is None:
            summary = CollectionSummary(key, error="Source Is Missing from Configuration")
            summary.duration_seconds = time.monotonic() - started_at
            summaries.append(summary)
            if progress_callback is not None:
                progress_callback(key, "failed", summary.error)
            continue
        try:
            collector = COLLECTORS[key](
                config,
                source_config,
                progress_callback=(
                    (lambda message, source=key: progress_callback(source, "running", message))
                    if progress_callback is not None
                    else None
                ),
            )
            summary = collector.collect(dry_run=dry_run)
        except Exception as exc:
            summary = CollectionSummary(key, error=str(exc))
        summary.duration_seconds = time.monotonic() - started_at
        summaries.append(summary)
        state, detail = collection_outcome(summary, dry_run)
        logger.info(
            "source collection completed source=%s state=%s duration=%.1f downloaded=%s validated=%s skipped=%s failed=%s error=%s",
            key,
            state,
            summary.duration_seconds,
            summary.downloaded,
            summary.validated,
            summary.skipped,
            summary.failed,
            summary.error or "",
        )
        if progress_callback is not None:
            progress_callback(key, state, detail)
    return summaries


def collection_outcome(summary: CollectionSummary, dry_run: bool = False) -> tuple[str, str]:
    if summary.error:
        return "failed", summary.error
    if summary.failed:
        detail = f"{summary.failed:,} Record(s) Failed"
        if summary.failure_examples:
            detail += f"; {summary.failure_examples[0]}"
        return ("failed" if summary.validated == 0 else "partial"), detail
    if dry_run:
        return "success", f"{summary.planned:,} Record(s) Planned"
    if summary.downloaded == 0 and summary.unknown_license and summary.unknown_license == summary.skipped:
        return "empty", "No Eligible Records with a Known License"
    if summary.downloaded == 0 and summary.skipped:
        return "success", f"No New Records; {summary.skipped:,} Already Processed or Skipped"
    if summary.discovered == 0:
        return "empty", "No Candidates Discovered"
    return "success", f"{summary.validated:,} Record(s) Validated"


def format_duration(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def collect_with_progress(config: AppConfig, requested: str, dry_run: bool) -> list[CollectionSummary]:
    source_keys = selected_sources(config, requested)
    state_styles = {
        "queued": ("[dim]-[/dim]", "[dim]Queued[/dim]"),
        "running": ("[cyan]..[/cyan]", ""),
        "success": ("[green]OK[/green]", ""),
        "empty": ("[yellow]--[/yellow]", ""),
        "partial": ("[yellow]![/yellow]", ""),
        "failed": ("[red]X[/red]", ""),
    }
    progress = Progress(
        TextColumn("{task.fields[icon]}"),
        TextColumn("[bold]{task.description}[/bold]", justify="left"),
        TextColumn("{task.fields[status]}", justify="left"),
        TimeElapsedColumn(),
        console=console,
        expand=True,
    )
    tasks = {
        key: progress.add_task(
            key.replace("-", " ").title(),
            total=1,
            completed=0,
            start=False,
            icon=state_styles["queued"][0],
            status=state_styles["queued"][1],
        )
        for key in source_keys
    }
    started_sources: set[str] = set()

    def update_progress(source: str, state: str, detail: str) -> None:
        task_id = tasks[source]
        if state == "running" and source not in started_sources:
            progress.start_task(task_id)
            started_sources.add(source)
        icon, default_status = state_styles[state]
        progress.update(
            task_id,
            completed=1 if state in {"success", "empty", "partial", "failed"} else 0,
            icon=icon,
            status=escape(detail) if detail else default_status,
        )

    with progress:
        return run_collectors(config, requested, dry_run, progress_callback=update_progress)


def print_collection_summary(summaries: list[CollectionSummary], dry_run: bool = False) -> None:
    table = Table(title="Collection Summary", box=box.ASCII, expand=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Result", no_wrap=True)
    table.add_column("Records", overflow="fold", ratio=2)
    table.add_column("Duration", justify="right", no_wrap=True)
    table.add_column("Details", overflow="fold", ratio=3)
    for item in summaries:
        state, details = collection_outcome(item, dry_run)
        result = {
            "failed": "[red]Failed[/red]",
            "partial": "[yellow]Partial[/yellow]",
            "empty": "[yellow]No Records[/yellow]",
            "success": "[green]Success[/green]",
        }[state]
        if dry_run:
            records = f"Planned {item.planned:,}"
        else:
            records = (
                f"Seen {item.discovered:,}; Downloaded {item.downloaded:,}; Validated {item.validated:,}; "
                f"Skipped {item.skipped:,}; Failed {item.failed:,}; "
                f"Unknown License {item.unknown_license:,}"
            )
        table.add_row(
            item.source,
            result,
            records,
            format_duration(item.duration_seconds),
            escape(details),
        )
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
        summaries = collect_with_progress(config, args.source, args.dry_run)
        print_collection_summary(summaries, args.dry_run)
        if not args.dry_run:
            command_export(config)
        return 1 if any(summary.error or summary.failed for summary in summaries) else 0
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
