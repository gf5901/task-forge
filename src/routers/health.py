"""Health check endpoint — unauthenticated, used by Lambda watchdog."""

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["health"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_START_TIME = time.time()


def _last_log_timestamp(log_path: Path) -> str:
    """Return the ISO timestamp from the last non-empty line of a log file."""
    if not log_path.exists():
        return ""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return ""
            pos = min(size, 4096)
            f.seek(-pos, 2)
            last_lines = f.read().decode("utf-8", errors="replace").strip().splitlines()
        for line in reversed(last_lines):
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                import json

                try:
                    return json.loads(line).get("ts", "")
                except (json.JSONDecodeError, KeyError):
                    pass
            if line.startswith("---"):
                continue
            return line[:30]
        return ""
    except OSError:
        return ""


@router.get("/api/health")
async def health() -> Dict[str, Any]:
    from ..web import store

    disk = shutil.disk_usage("/")
    disk_free_pct = round(disk.free / disk.total * 100, 1)

    counts = {}  # type: Dict[str, int]
    try:
        from ..task_store import TaskStatus

        all_tasks = store.list_tasks()
        for status in TaskStatus:
            counts[status.value] = sum(1 for t in all_tasks if t.status == status)
    except Exception:
        pass

    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _START_TIME),
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "disk_free_pct": disk_free_pct,
        "task_counts": counts,
        "last_runner_ts": _last_log_timestamp(
            Path(os.getenv("PIPELINE_LOG", str(_PROJECT_ROOT / "pipeline.log")))
        ),
        "last_healer_ts": _last_log_timestamp(_PROJECT_ROOT / "healer.log"),
    }
