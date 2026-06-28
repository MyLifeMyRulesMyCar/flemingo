#!/usr/bin/env python3
# core/logging_config.py
# Centralized logging setup. Call setup_logging() once, as early as
# possible (top of api/app.py, daemon entrypoints, tools/*), before any
# other Flemingo module is imported and starts logging.
#
# Replaces the print() calls scattered across core/*.py and daemon.py.
# Those are being migrated incrementally - new code should use
# `logging.getLogger(__name__)` rather than print() from here on.

import logging
import logging.handlers
import os

DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
DEFAULT_LOG_FILE = os.path.join(DEFAULT_LOG_DIR, "flemingo.log")

_configured = False


def setup_logging(level=None, log_file=None, max_bytes=5 * 1024 * 1024, backup_count=5):
    """
    Configure the root logger with a console handler (human-readable,
    matches the existing emoji/print style) and a rotating file handler
    (plain, timestamped - this is what you'll actually grep through
    after a field unit misbehaves overnight).

    Safe to call more than once; only configures on the first call.

    Args:
        level: logging level name or int. Defaults to env var
            FLEMINGO_LOG_LEVEL, falling back to "INFO".
        log_file: path to the log file. Defaults to env var
            FLEMINGO_LOG_FILE, falling back to <repo_root>/logs/flemingo.log.
        max_bytes: rotate after the file reaches this size.
        backup_count: number of rotated files to keep.
    """
    global _configured
    if _configured:
        return

    level = level or os.getenv("FLEMINGO_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    log_file = log_file or os.getenv("FLEMINGO_LOG_FILE", DEFAULT_LOG_FILE)
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        ))
        root.addHandler(file_handler)
    except OSError as e:
        # Don't let a permissions/disk problem on the log file take down
        # the whole app - fall back to console-only and say so loudly.
        logging.getLogger(__name__).warning(
            f"Could not open log file '{log_file}' ({e}) - logging to console only"
        )

    # Quiet down noisy third-party loggers so the file doesn't fill up
    # with werkzeug's per-request access lines.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(__name__).info(f"Logging configured (level={logging.getLevelName(level)}, file={log_file})")
