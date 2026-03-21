#!/usr/bin/env python3
"""
Heartbeat dispatcher — runs the Cursor agent against HEARTBEAT.md every 15 minutes.

The agent reads HEARTBEAT.md, checks system health (stuck tasks, disk space),
and reports status. This is complementary to run_heal.py which runs the
structured healer strategies (PR recovery, cancelled-task diagnosis, worktree cleanup).

Usage:
    .venv/bin/python3 run_heartbeat.py
"""

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent
load_dotenv(PROJECT_ROOT / ".env")
os.chdir(PROJECT_ROOT)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("heartbeat")

AGENT_BIN = os.getenv("AGENT_BIN", "agent")
MODEL = os.getenv("HEARTBEAT_MODEL", os.getenv("MODEL_FAST", ""))
TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT", "300"))
HEARTBEAT_FILE = PROJECT_ROOT / "HEARTBEAT.md"
LOCK_FILE = Path("/tmp/heartbeat.lock")


def _acquire_lock():
    """Prevent overlapping heartbeat runs."""
    import fcntl

    fd = open(LOCK_FILE, "w")  # noqa: SIM115
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except OSError:
        fd.close()
        return None


def main():
    lock_fd = _acquire_lock()
    if not lock_fd:
        log.info("Another heartbeat is already running — skipping")
        return

    try:
        if not HEARTBEAT_FILE.exists():
            log.error("HEARTBEAT.md not found at %s", HEARTBEAT_FILE)
            return

        prompt = HEARTBEAT_FILE.read_text()

        cmd = [AGENT_BIN, "-p", "--force"]
        if MODEL:
            cmd.extend(["--model", MODEL])
        cmd.append(prompt)

        log.info("Heartbeat starting (timeout=%ds, model=%s)", TIMEOUT, MODEL or "default")
        t0 = time.monotonic()

        with tempfile.TemporaryDirectory(prefix="heartbeat-") as tmpdir:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                cwd=tmpdir,
            )

        elapsed = round(time.monotonic() - t0, 1)

        if result.returncode == 0:
            log.info("Heartbeat completed in %.1fs", elapsed)
        else:
            log.warning(
                "Heartbeat exited with code %d after %.1fs", result.returncode, elapsed
            )

        output = result.stdout.strip()
        if output:
            for line in output.splitlines()[-10:]:
                log.info("  agent: %s", line.rstrip())

    except subprocess.TimeoutExpired:
        log.warning("Heartbeat timed out after %ds", TIMEOUT)
    except Exception:
        log.exception("Heartbeat failed")
    finally:
        try:
            lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
