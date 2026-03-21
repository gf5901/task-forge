#!/usr/bin/env python3
"""
PR Reviewer — periodically reviews open PRs and posts feedback.

Runs the review agent against each open PR, posts a GitHub comment with the verdict,
and appends it to the originating task for visibility.

Usage:
    .venv/bin/python3 run_pr_reviewer.py

Environment variables:
    PR_REVIEW_MODEL     — model to use (default: MODEL_FULL → MODEL_DEFAULT → agent default)
    PR_REVIEW_MAX_PRS   — max number of open PRs to review per run (default: 10)
    PR_REVIEW_TIMEOUT   — per-PR agent timeout in seconds (default: 300)
"""

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
os.chdir(PROJECT_ROOT)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("pr_reviewer")

LOCK_FILE = Path("/tmp/pr-reviewer.lock")


def _acquire_lock():
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


def _get_store():
    from src.dynamo_store import DynamoTaskStore

    return DynamoTaskStore()


def main():
    lock_fd = _acquire_lock()
    if not lock_fd:
        log.info("Another PR reviewer is already running — skipping")
        return

    try:
        store = _get_store()

        from src.pr_review import PR_REVIEW_MODEL, run_pr_review

        log.info(
            "PR reviewer starting (model=%s, max_prs=%s, timeout=%s)",
            PR_REVIEW_MODEL or "default",
            os.getenv("PR_REVIEW_MAX_PRS", "10"),
            os.getenv("PR_REVIEW_TIMEOUT", "300"),
        )

        results = run_pr_review(store)

        lgtm = sum(1 for r in results if (r.get("verdict") or "").upper().startswith("LGTM"))
        needs_work = sum(
            1
            for r in results
            if (r.get("verdict") or "").upper().startswith("NEEDS_WORK")
        )
        skipped = sum(1 for r in results if r.get("verdict") is None)

        log.info(
            "PR reviewer done: %d reviewed (%d LGTM, %d NEEDS_WORK, %d skipped/failed)",
            len(results),
            lgtm,
            needs_work,
            skipped,
        )
    except Exception:
        log.exception("PR reviewer failed")
    finally:
        try:
            lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
