from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

from core.config import Settings, get_settings


_console = Console()


class StructuredFormatter(logging.Formatter):
    """JSON-like structured formatter for file logs."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        import time

        log_entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key.startswith("lfm_"):
                log_entry[key[4:]] = value
        return json.dumps(log_entry, ensure_ascii=False, default=str)


def _setup_file_handler(settings: Settings) -> logging.Handler:
    log_file = settings.logs_dir / "lfm.log"
    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(StructuredFormatter())
    handler.setLevel(logging.DEBUG)
    return handler


def _setup_console_handler(settings: Settings) -> logging.Handler:
    handler = RichHandler(
        console=_console,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    level = getattr(logging, settings.log_level, logging.INFO)
    handler.setLevel(level)
    return handler


def setup_logger() -> logging.Logger:
    settings = get_settings()
    logger = logging.getLogger("lfm")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    logger.addHandler(_setup_console_handler(settings))
    logger.addHandler(_setup_file_handler(settings))

    sys.excepthook = _make_excepthook(logger)
    return logger


def _make_excepthook(logger: logging.Logger):
    def excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
            extra={"lfm_error_type": exc_type.__name__},
        )
    return excepthook


def get_logger(name: str | None = None) -> logging.Logger:
    base = logging.getLogger("lfm")
    if not base.handlers:
        setup_logger()
    if name:
        return logging.getLogger(f"lfm.{name}")
    return base
