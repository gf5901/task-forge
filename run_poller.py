#!/usr/bin/env python3
"""Polling daemon that picks up pending tasks, task comment replies, and project PM chat from DynamoDB.

Replaces SSM-based triggering from the Lambda API. Runs as a systemd service
on EC2 and spawns run_task.py subprocesses — existing slot locking handles
concurrency so multiple runners never exceed MAX_CONCURRENT_RUNNERS.

Usage:
    .venv/bin/python3 run_poller.py
"""

import logging
import os
import signal
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

PROJECT_ROOT = Path(__file__).parent
VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python3")
RUN_TASK_SCRIPT = str(PROJECT_ROOT / "run_task.py")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

log = logging.getLogger("poller")

_shutdown = False
_last_spawn_time = 0.0


def _handle_signal(signum, frame):
    global _shutdown
    log.info("Received signal %d — shutting down after current cycle", signum)
    _shutdown = True


def _make_store():
    from src.dynamo_store import DynamoTaskStore

    return DynamoTaskStore()


def _spawn(args):
    # type: (list) -> None
    """Spawn run_task.py with given args, detached (fire-and-forget)."""
    cmd = [VENV_PYTHON, RUN_TASK_SCRIPT] + args
    log_fd = open(PROJECT_ROOT / "runner.log", "a")  # noqa: SIM115
    try:
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        log_fd.close()
        raise


def main():
    global _last_spawn_time
    from src.logging_config import configure

    configure()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    store = _make_store()

    try:
        from src.config import get_settings

        cfg = get_settings()
        log.info(
            "Poller started (interval=%ds, max_concurrent=%d, cooldown=%ds)",
            POLL_INTERVAL,
            cfg["max_concurrent_runners"],
            cfg["min_spawn_interval"],
        )
    except Exception:
        log.info("Poller started (interval=%ds, config read failed — using env defaults)", POLL_INTERVAL)

    from src.task_store import TaskStatus

    while not _shutdown:
        try:
            try:
                from src.config import get_settings

                cfg = get_settings()
            except Exception:
                cfg = {
                    "max_concurrent_runners": int(os.getenv("MAX_CONCURRENT_RUNNERS", "1")),
                    "min_spawn_interval": int(os.getenv("MIN_SPAWN_INTERVAL", "300")),
                }

            max_concurrent = cfg["max_concurrent_runners"]
            cooldown = cfg["min_spawn_interval"]

            pending = store.list_tasks(status=TaskStatus.PENDING)
            ready = [
                t for t in pending
                if not t.parent_id
                and store.deps_ready(t)
                and getattr(t, "assignee", "agent") != "human"
            ]

            # Apply project-level filtering (same as pick_next_task)
            try:
                from src.projects_dynamo import get_project
            except ImportError:
                get_project = None

            if get_project is not None:
                filtered = []
                for t in ready:
                    pid = getattr(t, "project_id", "") or ""
                    if pid:
                        p = get_project(pid)
                        if p:
                            if p.get("proj_status") == "paused":
                                continue
                            if (
                                p.get("awaiting_next_directive")
                                and getattr(t, "directive_sk", "")
                                and t.directive_sk == p.get("active_directive_sk")
                            ):
                                continue
                    filtered.append(t)
                ready = filtered
            if ready:
                if time.monotonic() - _last_spawn_time >= cooldown:
                    spawns = min(len(ready), max_concurrent)
                    for _ in range(spawns):
                        _spawn([])
                    _last_spawn_time = time.monotonic()
                    log.info("Spawned %d runner(s) for %d pending task(s)", spawns, len(ready))
                else:
                    remaining = int(cooldown - (time.monotonic() - _last_spawn_time))
                    log.debug("Cooldown active (%ds remaining), skipping %d ready task(s)", remaining, len(ready))

            reply_tasks = store.list_reply_pending()
            for t in reply_tasks:
                _spawn(["--reply", t.id])
                log.info("Spawned reply runner for task %s", t.id)

            try:
                from src.projects_dynamo import list_project_reply_pending
            except ImportError:
                list_project_reply_pending = None  # type: ignore[assignment]

            if list_project_reply_pending is not None:
                for pid in list_project_reply_pending():
                    _spawn(["--pm-reply", pid])
                    log.info("Spawned PM reply runner for project %s", pid)

        except Exception:
            log.exception("Poller iteration failed")

        time.sleep(POLL_INTERVAL)

    log.info("Poller stopped")


if __name__ == "__main__":
    main()
