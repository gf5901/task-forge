"""
Shared logging configuration with rotating file handlers.

Call configure() once at process startup (in run_task.py, run_web.py, etc.).
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 10 MB per file, keep 3 backups → max ~30 MB per log stream
_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "3"))
_FMT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure(log_file=None, level=None):
    # type: (Optional[str], Optional[str]) -> None
    """Configure root logger with a rotating file handler + stderr stream handler.

    Args:
        log_file: Path to the log file. Defaults to PROJECT_ROOT/runner.log.
        level: Log level string (e.g. "INFO"). Defaults to LOG_LEVEL env var or INFO.
    """
    level_str = level or os.getenv("LOG_LEVEL", "INFO")
    numeric_level = getattr(logging, level_str.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Avoid duplicate handlers if called more than once
    if root.handlers:
        return

    fmt = logging.Formatter(_FMT)

    # Rotating file handler
    path = Path(log_file) if log_file else PROJECT_ROOT / "runner.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(str(path), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler so output also appears in systemd journal / terminal
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)
