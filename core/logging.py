from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from rich.logging import RichHandler


def configure_logging(log_directory: Path) -> Path:
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / f"collector-{datetime.now():%Y-%m-%d}.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = RichHandler(show_time=False, show_path=False, rich_tracebacks=True)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return log_path
