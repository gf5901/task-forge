"""
Structured pipeline activity log.

Appends JSON lines to a log file for every pipeline event.
Each entry has: timestamp, task_id, event, stage, message, extra.
The file is automatically rotated at 10 MB (3 backups kept).
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = Path(os.getenv("PIPELINE_LOG", str(_PROJECT_ROOT / "pipeline.log")))
_lock = threading.Lock()

_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "3"))

# Dedicated rotating logger — initialised lazily on first emit so tests can
# override LOG_PATH before the handler is attached to a file.
_pipeline_logger = logging.getLogger("pipeline_log")
_pipeline_logger.propagate = False
_handler_attached = False


def _ensure_handler():
    # type: () -> None
    global _handler_attached
    if _handler_attached:
        return
    # No lock here — called only while _lock is already held by emit()
    fh = RotatingFileHandler(str(LOG_PATH), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT)
    fh.setFormatter(logging.Formatter("%(message)s"))
    _pipeline_logger.addHandler(fh)
    _pipeline_logger.setLevel(logging.DEBUG)
    _handler_attached = True


_dynamo_log_store = None  # Lazy-initialised DynamoTaskStore for log mirroring


def _get_dynamo_log_store():
    # type: () -> Optional[Any]
    """Return DynamoTaskStore for log mirroring."""
    global _dynamo_log_store
    if _dynamo_log_store is None:
        try:
            from .dynamo_store import DynamoTaskStore

            _dynamo_log_store = DynamoTaskStore()
        except Exception:
            _dynamo_log_store = False  # Mark failed so we don't retry every emit
    return _dynamo_log_store if _dynamo_log_store else None


def emit(task_id, event, stage="", message="", **extra):
    # type: (str, str, str, str, **Any) -> None
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_id": task_id,
        "event": event,
        "stage": stage,
        "message": message,
    }
    if extra:
        entry["extra"] = extra

    line = json.dumps(entry, default=str)
    with _lock:
        _ensure_handler()
        _pipeline_logger.info(line)

    # Mirror to DynamoDB when using dynamo backend so Activity view (Lambda API) sees events
    store = _get_dynamo_log_store()
    if store is not None:
        try:
            store.write_log_event(task_id, event, stage=stage, message=message, **extra)
        except Exception:
            logging.getLogger(__name__).exception("Failed to write pipeline log event to DynamoDB")


def read_logs(task_id=None, limit=200, offset=0):
    # type: (Optional[str], int, int) -> List[Dict]
    """Read log entries, newest first. Optionally filter by task_id."""
    if not LOG_PATH.exists():
        return []

    entries = []  # type: List[Dict]
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if task_id and entry.get("task_id") != task_id:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue

    entries.reverse()
    return entries[offset : offset + limit]


def count_logs(task_id=None):
    # type: (Optional[str]) -> int
    if not LOG_PATH.exists():
        return 0
    count = 0
    with open(LOG_PATH) as f:
        for line in f:
            if not line.strip():
                continue
            if task_id:
                try:
                    entry = json.loads(line)
                    if entry.get("task_id") != task_id:
                        continue
                except json.JSONDecodeError:
                    continue
            count += 1
    return count
