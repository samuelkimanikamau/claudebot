"""Minimal logging setup shared by the CLI and the running bot.

By default logs go to stderr (foreground runs; journald under systemd).
When ``CLAUDEBOT_LOG_FILE`` is truthy — the launchd service sets it, since
launchd's Standard*Path redirects never rotate — logs go to a size-capped
rotating file at ``<state_dir>/logs/claudebot.log`` instead, and stderr is
left for output the logging system can't capture (startup crashes).
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from claudebot.core.paths import log_dir

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3  # worst case ≈ 20 MB on disk


def file_logging_enabled() -> bool:
    return os.environ.get("CLAUDEBOT_LOG_FILE", "").strip().lower() in ("1", "true", "yes")


def setup_logging(level: str = "INFO") -> None:
    handler: logging.Handler
    if file_logging_enabled():
        logs = log_dir()
        logs.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            logs / "claudebot.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        datefmt = "%Y-%m-%d %H:%M:%S"  # files span days; stderr has journald/terminal context
    else:
        handler = logging.StreamHandler(sys.stderr)
        datefmt = "%H:%M:%S"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt=datefmt,
        handlers=[handler],
        force=True,
    )
    # These libraries are chatty at INFO/DEBUG; keep the journal readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
