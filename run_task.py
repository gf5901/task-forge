#!/usr/bin/env python3
"""Entry point for the task runner, comment replies, directives, and daily cycles.

Usage:
    python run_task.py                  # pick next pending task
    python run_task.py <task_id>        # run specific task
    python run_task.py --reply <task_id>  # reply to comments on a task
    python run_task.py --directive <project_id> <directive_sk>  # decompose directive into tasks
    python run_task.py --daily-cycle <project_id>  # run daily observe/reflect/propose cycle
    python run_task.py --propose-plan <project_id>  # autopilot plan [--regenerate] [--plan-suffix ID]
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

os.chdir(Path(__file__).parent)


# Ensure the main checkout is always on the default branch.
# Tasks run in isolated worktrees, so this should normally be a no-op.
# If a previous task somehow left the main checkout on a task branch
# (e.g. worktree creation failed and the agent ran in-place), reset it.
def _ensure_main_branch():
    import subprocess

    project_root = Path(__file__).parent
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        default = (
            result.stdout.strip().replace("refs/remotes/origin/", "")
            if result.returncode == 0
            else "main"
        )
        current = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        ).stdout.strip()
        if current and current != default:
            subprocess.run(["git", "checkout", default], cwd=str(project_root), capture_output=True)
    except Exception:
        pass


_ensure_main_branch()


def _make_store():
    from src.dynamo_store import DynamoTaskStore

    return DynamoTaskStore()


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--directive":
        from src.logging_config import configure as _configure_logging
        from src.pipeline import run_directive

        _configure_logging()
        store = _make_store()
        ok = run_directive(store, sys.argv[2], sys.argv[3])
        raise SystemExit(0 if ok else 1)
    if len(sys.argv) >= 3 and sys.argv[1] == "--daily-cycle":
        from src.logging_config import configure as _configure_logging
        from src.objectives import run_daily_cycle

        _configure_logging()
        store = _make_store()
        ok = run_daily_cycle(store, sys.argv[2])
        raise SystemExit(0 if ok else 1)
    if len(sys.argv) >= 3 and sys.argv[1] == "--propose-plan":
        from src.autopilot import propose_daily_plan
        from src.logging_config import configure as _configure_logging

        _configure_logging()
        store = _make_store()
        project_id = sys.argv[2]
        regenerate = False
        plan_suffix = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--regenerate":
                regenerate = True
                i += 1
            elif sys.argv[i] == "--plan-suffix" and i + 1 < len(sys.argv):
                plan_suffix = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        ok = propose_daily_plan(
            store,
            project_id,
            regenerate=regenerate,
            plan_suffix=plan_suffix,
        )
        raise SystemExit(0 if ok else 1)
    if len(sys.argv) >= 3 and sys.argv[1] == "--reply":
        from src.logging_config import configure as _configure_logging
        from src.runner import run_comment_reply

        _configure_logging()
        store = _make_store()
        run_comment_reply(store, sys.argv[2])
    else:
        from src.runner import main

        main()
