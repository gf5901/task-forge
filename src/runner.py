"""
Task runner entry point.

Acquires a slot lock, sets up signal handling, and delegates to the pipeline.
All pipeline logic lives in the submodules:
  src/worktree.py  — git worktree management
  src/agent.py     — agent CLI execution and output parsing
  src/pr.py        — PR creation and CI polling
  src/pipeline.py  — task orchestration (plan → execute → PR → cleanup)

Re-exports the public API so external callers (healer, web, tests) continue to
import from `src.runner` without changes.
"""

import contextlib
import fcntl
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from .agent import (  # noqa: F401  re-exported
    MODEL_FAST,
    TASK_TIMEOUT,
    _append_text_to_task,
    _extract_agent_text,
    _parse_agent_result,
    _save_session_id,
    append_result_to_task,
    build_prompt,
    run_agent,
    run_doc_update,
)
from .pipeline import (  # noqa: F401  re-exported
    PLAN_ONLY_PROMPT,
    PLAN_PROMPT,
    PLAN_TIMEOUT,
    PRIORITY_ORDER,
    _build_checklist_prompt,
    _build_role_options,
    _resolve_model,
    _run_one_inner,
    create_subtasks,
    pick_next_task,
    plan_task,
    run_comment_reply,
    run_directive,
    run_one,
    run_plan_only,
)
from .pipeline_log import emit as plog
from .pr import (  # noqa: F401  re-exported
    CI_CHECK_TIMEOUT,
    NO_CHANGES_SENTINEL,
    PR_SUMMARY_PROMPT,
    WRONG_DIR_SENTINEL,
    _generate_pr_body,
    _wait_for_pr_ci,
    commit_and_create_pr,
)
from .task_store import ModelTier, TaskPriority, TaskStatus  # noqa: F401
from .worktree import (  # noqa: F401  re-exported
    PROJECT_ROOT,
    WORK_DIR,
    WORKSPACE_DIR,
    WORKTREE_BASE,
    _get_default_branch,
    _resolve_repo_dir,
    _run_cmd,
    _slugify_branch,
    cleanup_worktree,
    create_worktree,
    delete_task_artifacts,
    ensure_repo,
)

log = logging.getLogger(__name__)

LOCK_PATH = Path(os.getenv("RUNNER_LOCK", "/tmp/task-forge-runner.lock"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_RUNNERS", "2"))
PIDFILE_DIR = Path(os.getenv("PIDFILE_DIR", "/tmp"))


class _TaskCancelledError(BaseException):
    """Raised by the SIGTERM handler to cancel the current task.

    Inherits from BaseException (not Exception) so it is not accidentally
    swallowed by broad `except Exception` clauses in the pipeline, but it
    *is* caught by the outer try/finally in main() so cleanup still runs.
    """


def _pidfile_path(task_id):
    # type: (str) -> Path
    return PIDFILE_DIR / ("task-runner-%s.pid" % task_id)


def write_pidfile(task_id):
    # type: (str) -> None
    try:
        _pidfile_path(task_id).write_text(str(os.getpid()))
    except OSError:
        log.warning("Failed to write pidfile for task %s", task_id)


def remove_pidfile(task_id):
    # type: (str) -> None
    with contextlib.suppress(OSError):
        _pidfile_path(task_id).unlink(missing_ok=True)


def kill_runner_for_task(task_id):
    # type: (str) -> bool
    """Send SIGTERM to the process group of the runner for task_id.

    Returns True if a signal was sent, False if no live process was found.
    """
    path = _pidfile_path(task_id)
    if not path.exists():
        return False
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return False
    except PermissionError:
        pass

    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        log.info("Sent SIGTERM to process group %d for task %s", pgid, task_id)
        return True
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return False
    except OSError as exc:
        log.warning("Failed to kill runner for task %s: %s", task_id, exc)
        return False


def main():
    from .logging_config import configure as _configure_logging

    _configure_logging()

    from .dynamo_store import DynamoTaskStore

    store = DynamoTaskStore()

    lock_file = None
    for slot in range(MAX_CONCURRENT):
        slot_path = Path("%s.%d" % (LOCK_PATH, slot))
        f = open(slot_path, "w")  # noqa: SIM115
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_file = f
            log.info("Acquired runner slot %d", slot)
            break
        except BlockingIOError:
            f.close()

    if lock_file is None:
        log.info("All %d runner slots occupied, exiting", MAX_CONCURRENT)
        sys.exit(0)

    task_id = sys.argv[1] if len(sys.argv) > 1 else None

    if task_id:
        write_pidfile(task_id)

    _cancel_event = threading.Event()

    def _handle_sigterm(signum, frame):
        _cancel_event.set()
        raise _TaskCancelledError("SIGTERM received")

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        run_one(store, task_id=task_id)
    except _TaskCancelledError:
        log.info("Task %s runner exiting after cancellation", task_id)
        if task_id:
            try:
                task = store.get(task_id)
                if task:
                    _append_text_to_task(
                        store,
                        task,
                        "Agent Output",
                        "**Cancelled** — task was cancelled while the agent was running.",
                    )
                    plog(task_id, "task_cancelled", "pipeline", "Cancelled via SIGTERM")
            except Exception:
                log.exception("Error writing cancellation note for task %s", task_id)
    finally:
        if task_id:
            remove_pidfile(task_id)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    main()
