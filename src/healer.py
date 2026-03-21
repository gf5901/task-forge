"""
Self-healing pipeline runner.

Runs periodically (via cron) to detect and recover stuck or failed tasks:

  1. Stale in_progress  — task has been in_progress for >STALE_MINUTES;
                          likely the runner process died. Reset to pending
                          so the poller re-executes it.

  2. Branch-no-PR       — task is completed/cancelled/in_review but has a
                          pushed remote branch with no open PR. Create the
                          PR and mark the task in_review.

  3. Cancelled-with-work — task is cancelled but the agent output shows
                           meaningful work was done (not just an empty or
                           error-only output). Ask a fast model to diagnose
                           and decide: re-run the task, or create a PR from
                           an existing branch if the work looks complete.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GH_BIN = os.getenv("GH_BIN", "gh")
STALE_MINUTES = int(os.getenv("HEALER_STALE_MINUTES", "30"))
MAX_HEAL_PER_RUN = int(os.getenv("HEALER_MAX_PER_RUN", "5"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd, cwd=None, timeout=30):
    # type: (List[str], Optional[str], int) -> subprocess.CompletedProcess
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or str(PROJECT_ROOT),
        timeout=timeout,
    )


def _remote_branch_exists(branch):
    # type: (str) -> bool
    r = _run(["git", "branch", "-r", "--list", "origin/" + branch])
    return bool(r.stdout.strip())


def _open_pr_for_branch(branch):
    # type: (str) -> Optional[str]
    """Return the URL of an open PR for this branch, or None."""
    r = _run(
        [
            GH_BIN,
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
            "--jq",
            ".[0].url",
        ],
        timeout=30,
    )
    url = r.stdout.strip()
    return url if url and url.startswith("http") else None


def _task_branch(task):
    # type: (...) -> str
    from .runner import _slugify_branch

    return "task/%s-%s" % (task.id, _slugify_branch(task.title))


def _age_minutes(ts):
    # type: (str) -> float
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60
    except Exception:
        return 0.0


def _has_meaningful_agent_output(store, task):
    # type: (...) -> bool
    output = store.get_agent_output(task.id) or ""
    stripped = output.strip()
    if not stripped or stripped == "(no output)":
        return False
    # Timed-out tasks with only the timeout note are not worth re-running as healed
    return not (stripped.startswith("**Timed out") and len(stripped) < 200)


# ---------------------------------------------------------------------------
# Healing strategies
# ---------------------------------------------------------------------------


def heal_stale_in_progress(store, plog):
    # type: (...) -> int
    """Reset tasks stuck in_progress back to pending."""
    from .task_store import TaskStatus

    healed = 0
    tasks = [t for t in store.list_tasks(status=TaskStatus.IN_PROGRESS) if not t.parent_id]
    for task in tasks:
        age = _age_minutes(task.updated_at)
        if age < STALE_MINUTES:
            continue
        log.warning(
            "Healer: task %s stuck in_progress for %.0f min — resetting to pending", task.id, age
        )
        store.append_section(
            task.id,
            "Healer Note",
            "Task was stuck `in_progress` for %.0f minutes "
            "(runner likely crashed). Reset to `pending` for re-execution." % age,
        )
        store.update_status(task.id, TaskStatus.PENDING)
        plog(task.id, "heal_stale", "healer", "Reset stale in_progress (%.0fmin) to pending" % age)
        healed += 1
    return healed


def heal_branch_no_pr(store, plog):
    # type: (...) -> int
    """Create PRs for tasks that have a pushed branch but no open PR."""
    from .runner import _get_default_branch, _resolve_repo_dir
    from .task_store import TaskStatus

    healed = 0
    candidates = []
    for status in (
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.IN_REVIEW,
        TaskStatus.FAILED,
    ):
        candidates.extend(t for t in store.list_tasks(status=status) if not t.parent_id)

    for task in candidates:
        if store.get_pr_url(task.id):
            continue  # already has a PR recorded
        branch = _task_branch(task)
        if not _remote_branch_exists(branch):
            continue

        # Check GitHub for an open PR on this branch
        existing_pr = _open_pr_for_branch(branch)
        if existing_pr:
            # PR exists but wasn't recorded in the task record — backfill it
            log.info("Healer: backfilling PR URL %s for task %s", existing_pr, task.id)
            store.append_section(task.id, "PR Created", existing_pr)
            if task.status != TaskStatus.IN_REVIEW:
                store.update_status(task.id, TaskStatus.IN_REVIEW)
            plog(task.id, "heal_backfill_pr", "healer", "Backfilled existing PR: %s" % existing_pr)
            healed += 1
            continue

        # No PR at all — create one
        repo_dir = _resolve_repo_dir(task)
        try:
            default_branch = _get_default_branch(repo_dir)
        except Exception:
            default_branch = "main"

        commit_msg = "task(%s): %s" % (task.id, task.title)
        pr = _run(
            [
                GH_BIN,
                "pr",
                "create",
                "--title",
                commit_msg,
                "--body",
                "Auto-created by healer for task %s.\n\nAgent completed work but PR creation failed during the original run."
                % task.id,
                "--base",
                default_branch,
                "--head",
                branch,
            ],
            cwd=repo_dir,
            timeout=60,
        )
        if pr.returncode == 0:
            pr_url = pr.stdout.strip()
            log.info("Healer: created PR %s for task %s", pr_url, task.id)
            store.append_section(task.id, "PR Created", pr_url)
            store.set_pr_url(task.id, pr_url)
            store.update_status(task.id, TaskStatus.IN_REVIEW)
            plog(task.id, "heal_create_pr", "healer", "Created missing PR: %s" % pr_url)
            healed += 1
        else:
            log.warning("Healer: failed to create PR for task %s: %s", task.id, pr.stderr[:200])

    return healed


def heal_cancelled_with_work(store, plog):
    # type: (...) -> int
    """Diagnose cancelled/failed tasks that have agent output and may be recoverable."""
    import tempfile

    from .runner import MODEL_FAST, _extract_agent_text, run_agent
    from .task_store import TaskStatus

    healed = 0
    candidates = [t for t in store.list_tasks(status=TaskStatus.CANCELLED) if not t.parent_id]
    candidates += [t for t in store.list_tasks(status=TaskStatus.FAILED) if not t.parent_id]

    for task in candidates:
        if healed >= MAX_HEAL_PER_RUN:
            break
        if not _has_meaningful_agent_output(store, task):
            continue
        if store.has_section(task.id, "## Healer"):
            continue
        if store.get_cancelled_by(task.id) == "user":
            continue

        agent_output = store.get_agent_output(task.id) or ""
        branch = _task_branch(task)
        branch_exists = _remote_branch_exists(branch)

        DIAGNOSE_PROMPT = """\
You are a pipeline healer reviewing a failed task. Decide the best recovery action.

## Task: %s
## Description:
%s

## Agent Output (what was accomplished before failure):
%s

## Branch pushed to remote: %s

Based on the agent output, respond with EXACTLY one of:
- "RERUN" — the task failed before meaningful work was done and should be re-executed from scratch
- "COMPLETED" — the agent output shows the work is fully done (no PR needed, e.g. investigation/analysis tasks)
- "PR_EXISTS" — meaningful code changes were made and a branch was pushed; create a PR

Respond with only the single word. No explanation.""" % (
            task.title,
            (task.description or "")[:500],
            agent_output[:1500],
            branch_exists,
        )

        try:
            with tempfile.TemporaryDirectory() as diag_dir:
                result, _, _, _ = run_agent(
                    DIAGNOSE_PROMPT, timeout=60, model=MODEL_FAST, cwd=diag_dir
                )
            decision = _extract_agent_text(result.stdout).strip().upper()
        except Exception as exc:
            log.warning("Healer: diagnosis failed for task %s: %s", task.id, exc)
            continue

        if decision == "RERUN":
            store.append_section(
                task.id,
                "Healer Note",
                "Healer diagnosed this as re-runnable. Reset to `pending`.",
            )
            store.update_status(task.id, TaskStatus.PENDING)
            plog(task.id, "heal_rerun", "healer", "Cancelled task reset to pending for re-run")
            healed += 1

        elif decision == "COMPLETED":
            store.append_section(
                task.id,
                "Healer Note",
                "Healer diagnosed work as complete. Marked `completed`.",
            )
            store.update_status(task.id, TaskStatus.COMPLETED)
            plog(task.id, "heal_completed", "healer", "Cancelled task marked completed by healer")
            healed += 1

        elif decision == "PR_EXISTS" and branch_exists:
            store.append_section(
                task.id,
                "Healer Note",
                "Healer diagnosed work as done with branch pushed. Attempting PR creation.",
            )
            # heal_branch_no_pr will pick this up on the next pass since branch exists
            # but we'll trigger it inline for immediacy
            n = heal_branch_no_pr(store, plog)
            if n:
                healed += n
        else:
            log.info("Healer: no action for task %s (decision=%r)", task.id, decision)

    return healed


def heal_stale_worktrees(store, plog):
    # type: (...) -> int
    """Clean up worktrees older than STALE_WORKTREE_DAYS (safety net for abandoned tasks)."""
    import shutil
    import time

    from .worktree import WORKTREE_BASE

    max_age_days = int(os.getenv("STALE_WORKTREE_DAYS", "7"))
    max_age_secs = max_age_days * 86400
    cleaned = 0

    if not WORKTREE_BASE.exists():
        return 0

    now = time.time()
    for entry in WORKTREE_BASE.iterdir():
        if not entry.is_dir() or not entry.name.startswith("task-"):
            continue
        try:
            age = now - entry.stat().st_mtime
            if age < max_age_secs:
                continue
            task_id = entry.name.replace("task-", "", 1)
            task = store.get(task_id) if task_id else None
            # Skip if task is still in_progress
            if task and task.status.value == "in_progress":
                continue
            log.info("Healer: removing stale worktree %s (age=%dd)", entry, int(age / 86400))
            shutil.rmtree(str(entry), ignore_errors=True)
            if task_id:
                plog(
                    task_id,
                    "heal_stale_worktree",
                    "healer",
                    "Removed stale worktree (age=%dd)" % int(age / 86400),
                )
            cleaned += 1
        except Exception:
            log.exception("Healer: failed to clean stale worktree %s", entry)

    return cleaned


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_healer(store, verbose=False):
    # type: (...) -> Tuple[int, int, int, int]
    """Run all healing strategies. Returns (stale_fixed, pr_fixed, cancelled_fixed, worktrees_cleaned)."""
    from .pipeline_log import emit as _plog

    def plog(task_id, event, stage, message, **extra):
        _plog(task_id, event, stage, message, **extra)

    log.info("Healer starting")

    stale = heal_stale_in_progress(store, plog)
    pr = heal_branch_no_pr(store, plog)
    cancelled = heal_cancelled_with_work(store, plog)
    worktrees = heal_stale_worktrees(store, plog)

    log.info(
        "Healer finished: %d stale reset, %d PRs created/backfilled, %d cancelled recovered, %d stale worktrees cleaned",
        stale,
        pr,
        cancelled,
        worktrees,
    )
    return stale, pr, cancelled, worktrees
