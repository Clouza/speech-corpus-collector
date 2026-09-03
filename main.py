from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from collectors import COLLECTORS
from collectors.base import CollectionProgress, CollectionSummary
from core.config import AppConfig, load_config
from core.logging import configure_logging
from core.metadata import assign_splits, load_records, split_counts, write_records
from core.storage import Storage
from core.validator import validate_records


console = Console()
ProgressCallback = Callable[[str, CollectionProgress], None]


def selected_sources(config: AppConfig, requested: str) -> list[str]:
    if requested != "all":
        if requested not in COLLECTORS:
            raise ValueError(f"unknown source: {requested}")
        return [requested]
    return [
        key
        for key in COLLECTORS
        if config.sources.get(key) is not None and config.sources[key].enabled
    ]


def run_collectors(
    config: AppConfig,
    requested: str,
    dry_run: bool,
    progress_callback: ProgressCallback | None = None,
) -> list[CollectionSummary]:
    summaries: list[CollectionSummary] = []
    for key in selected_sources(config, requested):
        source_config = config.sources.get(key)
        if source_config is None:
            summaries.append(CollectionSummary(key, error="Source Is Missing from Configuration"))
            continue
        collector = COLLECTORS[key](
            config,
            source_config,
            progress_callback=(
                (lambda event, source=key: progress_callback(source, event))
                if progress_callback is not None
                else None
            ),
        )
        summary = collector.collect(dry_run=dry_run)
        summaries.append(summary)
        if progress_callback is not None:
            progress_callback(
                key,
                CollectionProgress(
                    message=summary.error or "Completed",
                    collected_records=summary.collected,
                    finished=True,
                ),
            )
    return summaries


def collect_with_progress(config: AppConfig, requested: str) -> list[CollectionSummary]:
    source_keys = selected_sources(config, requested)
    progress = Progress(
        TextColumn("[bold]{task.description}[/bold]", justify="left"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TextColumn("{task.fields[records]}", justify="right"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        TextColumn("{task.fields[status]}", justify="left"),
        console=console,
        expand=True,
    )
    tasks = {}
    record_counts = {key: 0 for key in source_keys}
    for key in source_keys:
        source_config = config.sources.get(key)
        total = 1
        if source_config is not None:
            total = source_config.max_videos if key == "youtube" else source_config.max_subtitles
        tasks[key] = progress.add_task(
            key.title(),
            total=total,
            records="0 Records",
            status="Queued",
        )

    def update(source: str, event: CollectionProgress) -> None:
        task_id = tasks[source]
        record_counts[source] = max(record_counts[source], event.collected_records)
        values: dict[str, object] = {
            "records": f"{record_counts[source]:,} Records",
            "status": escape(event.message),
        }
        if event.total_items is not None:
            values["total"] = max(event.total_items, 1)
        if event.completed_items is not None:
            values["completed"] = event.completed_items
        if event.finished:
            task = progress.tasks[task_id]
            values["completed"] = task.total or 1
        progress.update(task_id, **values)
        if event.finished:
            progress.stop_task(task_id)

    with progress:
        return run_collectors(config, requested, dry_run=False, progress_callback=update)


def print_collection_summary(summaries: list[CollectionSummary], dry_run: bool) -> None:
    table = Table(title="Collection Summary", box=box.ASCII, expand=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Result", no_wrap=True)
    table.add_column("Collection", justify="right")
    table.add_column("Details")
    for summary in summaries:
        if summary.error:
            result = "[red]Failed[/red]"
        elif summary.failed:
            result = "[yellow]Partial[/yellow]"
        else:
            result = "[green]Success[/green]"
        records = (
            f"Up to {summary.planned:,} Source Items"
            if dry_run
            else f"Collected {summary.collected:,}; Skipped {summary.skipped:,}; Failed {summary.failed:,}"
        )
        details = summary.error or "; ".join(summary.notices) or "Completed"
        table.add_row(summary.source, result, records, escape(details))
    console.print(table)


def command_collect(config: AppConfig, requested: str, dry_run: bool) -> int:
    summaries = (
        run_collectors(config, requested, dry_run=True)
        if dry_run
        else collect_with_progress(config, requested)
    )
    print_collection_summary(summaries, dry_run)
    if dry_run:
        return 1 if any(summary.error for summary in summaries) else 0
    storage = Storage(config.storage.root)
    existing = load_records(storage.catalog_path)
    by_id = {record.id: record for record in existing}
    for summary in summaries:
        for record in summary.records:
            by_id[record.id] = record
    records = assign_splits(by_id.values(), config.splits)
    result = validate_records(records)
    if not result.valid:
        for error in result.errors[:10]:
            console.print(f"[red]Validation Failed:[/red] {escape(error)}")
        return 1
    count = write_records(storage.catalog_path, records)
    counts = split_counts(records)
    console.print(f"Catalog: {storage.catalog_path}")
    console.print(
        f"Records: {count:,} | Train: {counts['train']:,} | "
        f"Eval: {counts['eval']:,} | Test: {counts['test']:,}"
    )
    return 1 if any(summary.error or summary.failed for summary in summaries) else 0


def command_sources(config: AppConfig) -> None:
    table = Table(title="Available Sources", box=box.ASCII)
    table.add_column("Source")
    table.add_column("Enabled")
    table.add_column("Required Credentials")
    table.add_column("Optional Credentials")
    for key, collector_type in COLLECTORS.items():
        configured = config.sources.get(key)
        table.add_row(
            key,
            "Yes" if configured is not None and configured.enabled else "No",
            ", ".join(collector_type.credentials) or "None",
            ", ".join(collector_type.optional_credentials) or "None",
        )
    console.print(table)


def command_status(config: AppConfig) -> int:
    storage = Storage(config.storage.root)
    records = load_records(storage.catalog_path)
    counts = split_counts(records)
    source_counts: dict[str, int] = {}
    for record in records:
        source = record.source.partition("/")[0]
        source_counts[source] = source_counts.get(source, 0) + 1
    console.print(f"Catalog: {storage.catalog_path}")
    console.print(f"Records: {len(records):,}")
    console.print(
        f"Train: {counts['train']:,} | Eval: {counts['eval']:,} | Test: {counts['test']:,}"
    )
    for source, count in sorted(source_counts.items()):
        console.print(f"{source.title()}: {count:,}")
    return 0


def command_validate(config: AppConfig) -> int:
    storage = Storage(config.storage.root)
    records = load_records(storage.catalog_path)
    result = validate_records(records)
    if result.valid:
        console.print(f"Validated: {len(records):,} Records")
        return 0
    for error in result.errors:
        console.print(f"[red]Validation Failed:[/red] {escape(error)}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Indonesian Text Corpora")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Configuration File Path")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "list-sources",
        aliases=["sources"],
        help="List Available Sources",
    )
    collect = subcommands.add_parser("collect", help="Collect Corpus Records")
    collect.add_argument("source", choices=[*COLLECTORS, "all"])
    collect.add_argument("--dry-run", action="store_true", help="Show Planned Collection")
    subcommands.add_parser("status", help="Show Catalog Statistics")
    subcommands.add_parser("validate", help="Validate Catalog Records")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except Exception as exc:
        console.print(f"[red]Configuration Error:[/red] {escape(str(exc))}")
        return 2
    configure_logging(args.config.parent / "logs")
    if args.command in {"list-sources", "sources"}:
        command_sources(config)
        return 0
    if args.command == "collect":
        return command_collect(config, args.source, args.dry_run)
    if args.command == "status":
        return command_status(config)
    if args.command == "validate":
        return command_validate(config)
    return 2


if __name__ == "__main__":
    sys.exit(main())
