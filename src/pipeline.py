"""
Task pipeline orchestration — planning, checklist execution, comment replies,
and the main run_one entry point.
"""

import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from .agent import (
    MODEL_FAST,
    MODEL_FULL,
    SECURITY_PREFIX,
    TASK_TIMEOUT,
    _append_text_to_task,
    _extract_agent_text,
    _save_session_id,
    append_result_to_task,
    build_prompt,
    run_agent,
    run_doc_update,
)
from .pipeline_log import emit as plog
from .pr import NO_CHANGES_SENTINEL, PUSH_FAILED_SENTINEL, WRONG_DIR_SENTINEL, commit_and_create_pr
from .roles import ROLES
from .task_store import ModelTier, TaskPriority, TaskStatus
from .worktree import (
    WORKTREE_BASE,
    _resolve_repo_dir,
    _run_cmd,
    _slugify_branch,
    cleanup_worktree,
    create_worktree,
    ensure_repo,
)

log = logging.getLogger(__name__)


def _notify_pm_chat_task_terminal(task, status_label: str) -> None:
    # type: (Any, str) -> None
    """Post a system line to project PM chat when a task with project_id finishes."""
    pid = (getattr(task, "project_id", None) or "").strip()
    if not pid:
        return
    try:
        from .projects_dynamo import post_system_message

        tid = getattr(task, "id", "")
        title = (getattr(task, "title", "") or "").strip() or tid
        post_system_message(
            pid,
            "Task **%s** (`%s`) → %s" % (title[:200], tid, status_label),
        )
    except Exception:
        log.debug("pm chat task notify failed", exc_info=True)


AUTO_DOCS = os.getenv("AUTO_DOCS", "true").lower() == "true"
AUTO_PR = os.getenv("AUTO_PR", "true").lower() == "true"
AUTO_PLAN = os.getenv("AUTO_PLAN", "true").lower() == "true"
PLAN_TIMEOUT = int(os.getenv("PLAN_TIMEOUT", "120"))

MODEL_PLAN = MODEL_FULL or MODEL_FAST

MODEL_MAP = {
    ModelTier.FAST: os.getenv("MODEL_FAST", "auto"),
    ModelTier.DEFAULT: os.getenv("MODEL_DEFAULT", ""),
    ModelTier.FULL: os.getenv("MODEL_FULL", ""),
}

PRIORITY_ORDER = {
    TaskPriority.URGENT: 0,
    TaskPriority.HIGH: 1,
    TaskPriority.MEDIUM: 2,
    TaskPriority.LOW: 3,
}


def _resolve_model(task):
    # type: (...) -> Optional[str]
    """Return the --model flag value for a task, or None to use the CLI default."""
    if task.model:
        tier = task.model
        if tier in (t.value for t in ModelTier):
            return MODEL_MAP.get(ModelTier(tier)) or None
        return task.model
    return None


def _build_role_options():
    # type: () -> str
    return "\n".join('  "%s" — %s' % (r["id"], r["label"]) for r in ROLES)


PLAN_PROMPT = (
    SECURITY_PREFIX
    + """\
You are a task planner. Break this task into concrete steps.

RULES:
- If the task is simple (single file change, config tweak, small bug fix), return ONE step.
- If complex (multi-file changes, investigation + fix, multiple concerns), return 2-5 steps.
- Do NOT create separate "investigate" and "fix" steps — combine them.
- Do NOT add a "verify" step — the executor verifies its own work.
- Be specific about files and functions when possible.
- For each step, choose the most appropriate "role" from the list below based on the work
  involved. If none fits, omit the field or use an empty string.

Available roles:
%s

Output ONLY a JSON array. Each element must have:
- "title": short action-oriented title (start with a verb)
- "description": 1-2 sentences of what to do
- "role": role id string (or "" if no specific role applies)

Task title: %%s
Task description: %%s
Tags: %%s

Respond with ONLY the JSON array, no markdown fences, no explanation."""
)

PLAN_ONLY_PROMPT = (
    SECURITY_PREFIX
    + """\
You are a task planner for a large, complex task. Your job is to break it down into
self-contained subtasks that can each be executed independently by an AI agent.

RULES:
- Create 3-10 subtasks.
- Each subtask must be completable in a single agent session (< 10 min of work).
- Each subtask description must include enough context to succeed standalone — name exact
  file paths, functions, endpoints, or components. Do not rely on the agent having read
  the parent task.
- For documentation/research subtasks, always specify the exact output file path
  (e.g. "Create docs/seo-audit.md containing…"). This lets the pipeline verify
  the deliverable was actually produced.
- Each subtask must have a UNIQUE, non-overlapping scope. Do not create two subtasks
  that would produce the same file or cover the same topic.
- Express dependencies explicitly using "depends_on": a list of 0-based indices of other
  steps in this array that must complete before this one can start. Use [] if no deps.
  Example: step 2 depends on step 0 → "depends_on": [0]
- Do NOT add a "verify" or "test" subtask — each executor tests its own work.
- For each subtask, choose the most appropriate "role" from the list below based on the
  work involved. If none fits, omit the field or use an empty string.

Available roles:
%s

Output ONLY a JSON array. Each element must have:
- "title": short action-oriented title (start with a verb)
- "description": 2-4 sentences describing exactly what to do and what success looks like
- "depends_on": list of 0-based indices this step depends on ([] if none)
- "role": role id string (or "" if no specific role applies)
- "expected_files": list of file paths the subtask should create or modify ([] if pure investigation)

Task title: %%s
Task description: %%s
Tags: %%s

Respond with ONLY the JSON array, no markdown fences, no explanation."""
)

COMMENT_REPLY_PROMPT = (
    SECURITY_PREFIX
    + """\
You are continuing work on a task you previously worked on. A user has posted a new comment \
that requires your attention. You are running inside the same git worktree where you did the \
original work, so all your previous changes are present.

## Latest Comment (from %s)
%s

## Your Instructions
Respond to the comment above. If it asks you to do something, do it — you can read and edit \
files in the worktree directly. If it asks a question, investigate and answer. If it provides \
information, acknowledge it and take any appropriate action. Respond concisely."""
)


def pick_next_task(store):
    # type: (Any) -> Optional[Any]
    """Return the highest-priority, oldest pending top-level task whose dependencies are met."""
    try:
        from .projects_dynamo import get_project
    except ImportError:
        get_project = None  # type: ignore[assignment]

    pending = store.list_tasks(status=TaskStatus.PENDING)
    pending = [
        t
        for t in pending
        if not t.parent_id and store.deps_ready(t) and getattr(t, "assignee", "agent") != "human"
    ]
    filtered = []
    for t in pending:
        pid = getattr(t, "project_id", "") or ""
        if pid and get_project is not None:
            p = get_project(pid)
            if not p:
                filtered.append(t)
                continue
            if p.get("proj_status") == "paused":
                continue
            if (
                p.get("awaiting_next_directive")
                and getattr(t, "directive_sk", "")
                and t.directive_sk == p.get("active_directive_sk")
            ):
                continue
        filtered.append(t)
    if not filtered:
        return None
    filtered.sort(key=lambda ta: (PRIORITY_ORDER.get(ta.priority, 99), ta.created_at))
    return filtered[0]


def _maybe_finalize_directive_batch(store, task_id):
    # type: (Any, str) -> None
    fn = getattr(store, "maybe_finalize_directive_batch", None)
    if callable(fn):
        try:
            fn(task_id)
        except Exception:
            log.exception("maybe_finalize_directive_batch failed for %s", task_id)


def trigger_unblocked_dependents(store, completed_task_id):
    # type: (DynamoTaskStore, str) -> None
    """After a task completes, fire the runner for any tasks that were waiting on it."""
    from .web import trigger_runner

    for t in store.find_dependents(completed_task_id):
        try:
            log.info(
                "Dependency %s met — triggering %s (%s)", completed_task_id, t.id, t.title[:60]
            )
            plog(t.id, "deps_unblocked", "pipeline", "Unblocked by %s" % completed_task_id)
            trigger_runner(t.id)
        except Exception:
            log.exception("Failed to trigger dependent task %s", t.id)


def plan_task(store, task, cwd=None):
    # type: (DynamoTaskStore, Any, Optional[str]) -> list
    """Ask a fast model to decompose a task into steps. Returns a list of dicts."""
    import json as _json

    role_options = _build_role_options()
    prompt = (PLAN_PROMPT % role_options) % (
        task.title,
        task.description or "(none)",
        ", ".join(task.tags) if task.tags else "(none)",
    )
    try:
        result, elapsed, _, _usage = run_agent(
            prompt, cwd=cwd, timeout=PLAN_TIMEOUT, model=MODEL_PLAN
        )
        if result.returncode != 0:
            log.warning("Planning failed for task %s (exit %d)", task.id, result.returncode)
            return []

        text = _extract_agent_text(result.stdout)
        json_match = re.search(r"\[.*\]", text, re.DOTALL)
        if not json_match:
            log.warning("Planning returned no JSON for task %s", task.id)
            return []

        steps = _json.loads(json_match.group())
        if not isinstance(steps, list) or not steps:
            return []

        log.info("Planned %d steps for task %s (%.1fs)", len(steps), task.id, elapsed)
        return steps

    except subprocess.TimeoutExpired:
        log.warning("Planning timed out for task %s", task.id)
        return []
    except (_json.JSONDecodeError, Exception):
        log.exception("Planning parse error for task %s", task.id)
        return []


def _build_checklist_prompt(task, steps):
    # type: (Any, list) -> str
    """Build a single compound prompt with the plan as a checklist."""
    parts = ["# Task: %s" % task.title]
    if task.description:
        parts.append("\n%s" % task.description)
    if task.tags:
        parts.append("\nTags: %s" % ", ".join(task.tags))

    parts.append("\n\n## Execution Plan\n")
    parts.append("Complete each step in order:\n")
    for i, step in enumerate(steps, 1):
        title = step.get("title", "Untitled")
        desc = step.get("description", "")
        parts.append("### Step %d: %s" % (i, title))
        if desc:
            parts.append(desc)
        parts.append("")

    parts.append(
        "Work through every step sequentially. "
        "After completing all steps, write a brief summary of what you did."
    )
    return "\n".join(parts)


def create_subtasks(store, parent_task, plan):
    # type: (DynamoTaskStore, Any, list) -> list
    """Create child task records for UI visibility (not individually executed)."""
    subtasks = []
    for step in plan:
        title = step.get("title", "Untitled step")
        desc = step.get("description", "")
        sub = store.create(
            title=title,
            description=desc,
            priority=parent_task.priority.value,
            created_by="planner",
            tags=parent_task.tags,
            target_repo=parent_task.target_repo,
            parent_id=parent_task.id,
            model=step.get("model", ""),
            role=step.get("role", "") or parent_task.role,
        )
        subtasks.append(sub)
        log.info("  Created step %s: %s (role=%s)", sub.id, title, sub.role or "none")
    return subtasks


def run_plan_only(store, task):
    # type: (DynamoTaskStore, Any) -> bool
    """Plan-only pipeline: decompose task into independent pending subtasks, no execution.

    Each subtask is created as a real pending top-level task (no parent_id) so it
    will be picked up and executed independently by the runner. The parent task is
    marked completed once the subtasks are created.
    """
    import json as _json

    log.info("Running plan-only for task %s: %s", task.id, task.title)
    plog(task.id, "plan_only_start", "plan", "Decomposing into independent subtasks")

    prompt = (PLAN_ONLY_PROMPT % _build_role_options()) % (
        task.title,
        task.description or "(none)",
        ", ".join(task.tags) if task.tags else "(none)",
    )

    prior_session = task.session_id or None

    try:
        with tempfile.TemporaryDirectory(prefix="plan-%s-" % task.id) as plan_dir:
            result, elapsed, _, _usage = run_agent(
                prompt,
                cwd=plan_dir,
                timeout=PLAN_TIMEOUT,
                model=MODEL_PLAN,
                session_id=prior_session,
                task_id=task.id,
            )
    except subprocess.TimeoutExpired:
        log.warning("Plan-only timed out for task %s", task.id)
        store.update_status(task.id, TaskStatus.FAILED)
        _notify_pm_chat_task_terminal(task, "**failed** (plan-only timeout)")
        plog(task.id, "plan_only_timeout", "plan", "Timed out")
        return False
    except Exception:
        log.exception("Plan-only error for task %s", task.id)
        store.update_status(task.id, TaskStatus.FAILED)
        _notify_pm_chat_task_terminal(task, "**failed** (plan-only error)")
        return False

    if result.returncode != 0:
        log.warning("Plan-only agent failed for task %s (exit %d)", task.id, result.returncode)
        store.update_status(task.id, TaskStatus.FAILED)
        _notify_pm_chat_task_terminal(task, "**failed** (plan-only agent error)")
        plog(task.id, "plan_only_failed", "plan", "Agent failed (exit %d)" % result.returncode)
        return False

    text = _extract_agent_text(result.stdout)
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if not json_match:
        log.warning("Plan-only returned no JSON for task %s", task.id)
        store.update_status(task.id, TaskStatus.FAILED)
        _notify_pm_chat_task_terminal(task, "**failed** (no plan JSON)")
        return False

    try:
        steps = _json.loads(json_match.group())
    except _json.JSONDecodeError:
        log.exception("Plan-only JSON parse error for task %s", task.id)
        store.update_status(task.id, TaskStatus.FAILED)
        _notify_pm_chat_task_terminal(task, "**failed** (bad plan JSON)")
        return False

    if not isinstance(steps, list) or not steps:
        log.warning("Plan-only returned empty plan for task %s", task.id)
        store.update_status(task.id, TaskStatus.FAILED)
        _notify_pm_chat_task_terminal(task, "**failed** (empty plan)")
        return False

    parent_desc = task.description.strip() if task.description else "(no description)"
    if len(parent_desc) > 500:
        parent_desc = parent_desc[:500] + "… (truncated)"
    parent_context = ("**Parent task:** %s\n\n%s\n\n---\n\n") % (task.title, parent_desc)

    subtasks = []
    for step in steps:
        title = step.get("title", "Untitled step")
        desc = step.get("description", "")
        expected = step.get("expected_files", [])
        if expected and isinstance(expected, list):
            desc += "\n\n**Expected output files:** %s" % ", ".join("`%s`" % f for f in expected)
        sub = store.create(
            title=title,
            description=parent_context + desc,
            priority=task.priority.value,
            created_by="planner",
            tags=task.tags,
            target_repo=task.target_repo,
            model=step.get("model", ""),
            role=step.get("role", "") or task.role,
        )
        subtasks.append(sub)
        log.info(
            "  Created independent subtask %s: %s (role=%s)", sub.id, title, sub.role or "none"
        )

    for i, (step, sub) in enumerate(zip(steps, subtasks)):
        dep_indices = step.get("depends_on", [])
        if not isinstance(dep_indices, list):
            dep_indices = []
        dep_ids = [
            subtasks[j].id
            for j in dep_indices
            if isinstance(j, int) and 0 <= j < len(subtasks) and j != i
        ]
        if dep_ids:
            store.set_depends_on(sub.id, dep_ids)
            log.info("  Task %s depends on: %s", sub.id, dep_ids)

    plan_summary = "\n".join("- **%s** `%s`" % (s.title, s.id) for s in subtasks)
    _append_text_to_task(
        store,
        task,
        "Plan",
        "Decomposed into %d independent tasks:\n\n%s" % (len(subtasks), plan_summary),
    )

    store.update_status(task.id, TaskStatus.COMPLETED)
    _notify_pm_chat_task_terminal(
        task,
        "**completed** (plan-only → %d subtask(s))" % len(subtasks),
    )
    trigger_unblocked_dependents(store, task.id)
    plog(
        task.id,
        "plan_only_done",
        "plan",
        "Created %d independent tasks" % len(subtasks),
        runtime=elapsed,
    )
    log.info("Plan-only for task %s complete: %d subtasks created", task.id, len(subtasks))
    return True


def run_directive(store, project_id, directive_sk):
    # type: (Any, str, str) -> bool
    """Decompose a project directive into top-level tasks (plan-only style), then dispatch runners."""
    import json as _json

    from .projects_dynamo import get_directive_item, get_project, update_directive_task_ids
    from .web import trigger_runner

    proj = get_project(project_id)
    if not proj:
        log.error("run_directive: project %s not found", project_id)
        return False
    ditem = get_directive_item(project_id, directive_sk)
    if not ditem:
        log.error("run_directive: directive %s not found for project %s", directive_sk, project_id)
        return False

    spec = (proj.get("spec") or "").strip() or "(no spec)"
    directive_body = (ditem.get("content") or "").strip()
    title = str(proj.get("title") or "Project").strip()
    target_repo = str(proj.get("target_repo") or "").strip()
    prio = str(proj.get("priority") or "medium").strip()
    if prio not in ("low", "medium", "high", "urgent"):
        prio = "medium"

    directive_date = datetime.now(timezone.utc).date().isoformat()
    pseudo_desc = "## Project spec\n\n%s\n\n## Directive\n\n%s" % (spec, directive_body)

    prompt = (PLAN_ONLY_PROMPT % _build_role_options()) % (
        "%s — daily directive" % title,
        pseudo_desc,
        "directive",
    )

    log.info("run_directive: planning for project=%s directive=%s", project_id, directive_sk[:40])

    try:
        with tempfile.TemporaryDirectory(prefix="directive-%s-" % project_id) as plan_dir:
            result, elapsed, _, _usage = run_agent(
                prompt,
                cwd=plan_dir,
                timeout=PLAN_TIMEOUT,
                model=MODEL_PLAN,
            )
    except subprocess.TimeoutExpired:
        log.warning("run_directive: timed out for project %s", project_id)
        return False
    except Exception:
        log.exception("run_directive: agent error for project %s", project_id)
        return False

    if result.returncode != 0:
        log.warning(
            "run_directive: agent failed project %s (exit %d)", project_id, result.returncode
        )
        return False

    text = _extract_agent_text(result.stdout)
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if not json_match:
        log.warning("run_directive: no JSON for project %s", project_id)
        return False

    try:
        steps = _json.loads(json_match.group())
    except _json.JSONDecodeError:
        log.exception("run_directive: JSON parse error for project %s", project_id)
        return False

    if not isinstance(steps, list) or not steps:
        log.warning("run_directive: empty plan for project %s", project_id)
        return False

    spec_excerpt = spec if len(spec) <= 2000 else spec[:2000] + "… (truncated)"
    parent_context = (
        "**Project:** %s\n\n**Spec (excerpt):**\n%s\n\n**Directive:**\n%s\n\n---\n\n"
        % (title, spec_excerpt, directive_body)
    )

    subtasks = []
    for step in steps:
        stitle = step.get("title", "Untitled step")
        desc = step.get("description", "")
        expected = step.get("expected_files", [])
        if expected and isinstance(expected, list):
            desc += "\n\n**Expected output files:** %s" % ", ".join("`%s`" % f for f in expected)
        sub = store.create(
            title=stitle,
            description=parent_context + desc,
            priority=prio,
            created_by="directive-planner",
            tags=[],
            target_repo=target_repo,
            model=step.get("model", ""),
            role=step.get("role", "") or "fullstack_engineer",
            project_id=project_id,
            directive_sk=directive_sk,
            directive_date=directive_date,
        )
        subtasks.append(sub)
        log.info("run_directive: created task %s: %s (role=%s)", sub.id, stitle, sub.role or "none")

    for i, (step, sub) in enumerate(zip(steps, subtasks)):
        dep_indices = step.get("depends_on", [])
        if not isinstance(dep_indices, list):
            dep_indices = []
        dep_ids = [
            subtasks[j].id
            for j in dep_indices
            if isinstance(j, int) and 0 <= j < len(subtasks) and j != i
        ]
        if dep_ids:
            store.set_depends_on(sub.id, dep_ids)
            log.info("run_directive: task %s depends on %s", sub.id, dep_ids)

    task_ids = [s.id for s in subtasks]
    update_directive_task_ids(project_id, directive_sk, task_ids)

    for sub in subtasks:
        if store.deps_ready(sub):
            try:
                trigger_runner(sub.id)
            except Exception:
                log.exception("run_directive: failed to trigger runner for %s", sub.id)

    plog(
        subtasks[0].id if subtasks else project_id,
        "directive_decomposed",
        "plan",
        "Created %d tasks from directive" % len(subtasks),
        runtime=elapsed,
    )
    log.info(
        "run_directive: complete project=%s tasks=%d (%.1fs)",
        project_id,
        len(subtasks),
        elapsed,
    )
    return True


def _get_or_create_reply_worktree(task):
    # type: (...) -> tuple
    """Return (wt_path, created_fresh) for the task's worktree.

    - Reuses the existing worktree if present.
    - Re-creates it on the task branch if it was cleaned up after a PR.
    - Returns (None, False) if the task has no repo or worktree creation fails.
    - Never touches the worktree while the main runner may be using it (in_progress).
    """
    from pathlib import Path as _Path

    # Don't touch the worktree if the main runner is actively using it.
    if task.status == TaskStatus.IN_PROGRESS:
        log.info(
            "Comment reply: task %s is in_progress — skipping worktree to avoid collision",
            task.id,
        )
        return None, False

    wt_path = str(WORKTREE_BASE / ("task-%s" % task.id))

    if _Path(wt_path).exists():
        log.info("Comment reply: reusing existing worktree at %s", wt_path)
        return wt_path, False

    # Worktree was cleaned up after PR — try to re-create it on the task branch.
    try:
        repo_dir = _resolve_repo_dir(task)
    except Exception:
        return None, False

    try:
        _run_cmd(["git", "fetch", "origin"], cwd=repo_dir, timeout=60)
        slug = _slugify_branch(task.title)
        branch = "task/%s-%s" % (task.id, slug)
        WORKTREE_BASE.mkdir(parents=True, exist_ok=True)
        result = _run_cmd(
            ["git", "worktree", "add", wt_path, branch],
            cwd=repo_dir,
        )
        if result.returncode == 0:
            log.info("Comment reply: re-created worktree at %s on branch %s", wt_path, branch)
            return wt_path, True
        # Branch doesn't exist remotely — fall back to default branch
        from .worktree import _get_default_branch

        default_branch = _get_default_branch(repo_dir)
        result2 = _run_cmd(
            ["git", "worktree", "add", "-b", branch, wt_path, "origin/%s" % default_branch],
            cwd=repo_dir,
        )
        if result2.returncode == 0:
            log.info("Comment reply: created fresh worktree at %s on %s", wt_path, default_branch)
            return wt_path, True
    except Exception:
        log.exception("Comment reply: failed to create worktree for task %s", task.id)

    return None, False


def _commit_reply_changes(store, task, wt_path):
    # type: (DynamoTaskStore, Any, str) -> None
    """Commit and push any file changes the agent made during a comment reply.

    If the branch already has an open PR, the new commit appears there automatically.
    If the branch is gone or push fails, we log a warning but don't raise — the
    text reply was already saved.
    """
    from .pr import NO_CHANGES_SENTINEL, commit_and_create_pr

    status = _run_cmd(["git", "status", "--porcelain"], cwd=wt_path)
    if not status.stdout.strip():
        return

    log.info("Comment reply: agent made file changes in %s — committing", wt_path)
    _run_cmd(["git", "add", "-A"], cwd=wt_path)
    commit_msg = "task(%s): follow-up changes from comment reply" % task.id
    commit = _run_cmd(["git", "commit", "-m", commit_msg], cwd=wt_path)
    if commit.returncode != 0:
        log.warning("Comment reply: commit failed for task %s: %s", task.id, commit.stderr[:200])
        return

    branch = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=wt_path).stdout.strip()
    push = _run_cmd(["git", "push", "-u", "origin", branch], cwd=wt_path, timeout=60)
    if push.returncode != 0:
        log.warning(
            "Comment reply: push failed for task %s (branch %s): %s",
            task.id,
            branch,
            push.stderr[:200],
        )
        return

    # Check whether an open PR already exists for this branch.
    from .worktree import GH_BIN

    pr_check = _run_cmd(
        [GH_BIN, "pr", "view", branch, "--json", "url,state", "--jq", '.url + " " + .state'],
        cwd=wt_path,
        timeout=30,
    )
    if pr_check.returncode == 0 and "OPEN" in pr_check.stdout:
        pr_url = pr_check.stdout.strip().split()[0]
        note = "Follow-up changes committed to existing PR: [%s](%s)" % (pr_url, pr_url)
        log.info("Comment reply: pushed follow-up commit to open PR %s", pr_url)
        store.set_pr_url(task.id, pr_url)
    else:
        # No open PR — create one (covers merged PR + new edits, or fresh branch case).
        pr_url = commit_and_create_pr(store, task, wt_path)
        if pr_url and pr_url != NO_CHANGES_SENTINEL:
            note = "Follow-up changes committed and new PR opened: [%s](%s)" % (pr_url, pr_url)
            log.info("Comment reply: opened new PR %s for follow-up changes", pr_url)
        else:
            note = "Follow-up changes committed to branch `%s` (no PR created)." % branch
            log.info("Comment reply: committed changes to branch %s, PR creation skipped", branch)

    _append_text_to_task(store, task, "PR Created", note)


def run_comment_reply(store, task_id):
    # type: (DynamoTaskStore, str) -> bool
    """Re-engage the agent in the task's worktree to respond to the latest comment."""
    task = store.get(task_id)
    if not task:
        log.warning("Comment reply: task %s not found", task_id)
        return False

    # Atomically claim the reply by clearing reply_pending before doing any work.
    # If reply_pending is already false another process already claimed it — bail.
    if not task.reply_pending:
        log.info("Comment reply: reply_pending already false for task %s — skipping", task_id)
        return False
    store.set_reply_pending(task_id, False)
    # Re-read to confirm we won the race
    task = store.get(task_id)
    if not task or task.reply_pending:
        log.info("Comment reply: lost race for task %s — skipping", task_id)
        return False

    comments = store.get_comments(task_id)
    # Find the latest non-agent comment to reply to
    user_comments = [c for c in comments if c.author != "agent"]
    if not user_comments:
        log.info("Comment reply: no user comments on task %s", task_id)
        return False

    latest = user_comments[-1]

    prompt = COMMENT_REPLY_PROMPT % (
        latest.author,
        latest.body,
    )

    plog(task_id, "reply_start", "execute", "Responding to comment")

    wt_path, created_fresh = _get_or_create_reply_worktree(task)

    try:
        if wt_path:
            result, elapsed, _, usage = run_agent(
                prompt,
                cwd=wt_path,
                timeout=TASK_TIMEOUT,
                session_id=task.session_id or None,
            )
        else:
            # No repo / worktree available (no target_repo, or task is in_progress).
            # Fall back to a tmpdir for a text-only reply.
            with tempfile.TemporaryDirectory(prefix="reply-%s-" % task_id) as reply_dir:
                result, elapsed, _, usage = run_agent(
                    prompt,
                    cwd=reply_dir,
                    timeout=TASK_TIMEOUT,
                    session_id=task.session_id or None,
                )
        if result.returncode == 0 and result.stdout.strip():
            store.add_comment(task_id, "agent", _extract_agent_text(result.stdout))
            # Commit any file changes the agent made — only when we have a worktree
            # and the task is not actively being run (in_progress check is in _get_or_create).
            if wt_path:
                try:
                    task = store.get(task_id)  # re-read for latest state
                    _commit_reply_changes(store, task, wt_path)
                except Exception:
                    log.exception("Comment reply: error committing changes for task %s", task_id)
            plog(task_id, "reply_done", "execute", "Agent replied", runtime=elapsed, **usage)
            log.info("Comment reply for task %s completed (%.1fs)", task_id, elapsed)
            return True
        else:
            plog(
                task_id,
                "reply_failed",
                "execute",
                "Agent failed (exit %d)" % result.returncode,
                runtime=elapsed,
            )
            log.warning("Comment reply failed for task %s (exit %d)", task_id, result.returncode)
            return False
    except subprocess.TimeoutExpired:
        plog(task_id, "reply_timeout", "execute", "Timed out after %ds" % TASK_TIMEOUT)
        log.error("Comment reply for task %s timed out", task_id)
        return False
    except Exception as exc:
        plog(task_id, "reply_error", "execute", str(exc)[:200])
        log.exception("Comment reply error for task %s", task_id)
        return False
    finally:
        # Only clean up worktrees we created fresh — not ones that were already
        # present (which may belong to an active or recently-finished run).
        if wt_path and created_fresh:
            try:
                cleanup_worktree(task, wt_path)
            except Exception:
                log.warning("Comment reply: failed to clean up worktree %s", wt_path)


def run_one(store, task_id=None):
    # type: (DynamoTaskStore, Optional[str]) -> bool
    """Run the compound pipeline for one task, return True if work was done."""
    from .budget import within_budget
    from .runner import remove_pidfile, write_pidfile

    if not within_budget():
        log.warning("Daily budget exceeded — skipping task dispatch")
        plog(task_id or "system", "budget_exceeded", "pipeline", "Daily budget cap reached")
        return False

    if task_id:
        task = store.get(task_id)
        if not task or task.status != TaskStatus.PENDING:
            log.info("Task %s not found or not pending", task_id)
            return False
        if getattr(task, "assignee", "agent") == "human":
            log.info("Task %s is human-assigned, skipping", task_id)
            return False
    else:
        task = pick_next_task(store)
        if not task:
            log.info("No pending tasks")
            return False

    store.update_status(task.id, TaskStatus.IN_PROGRESS)
    task = store.get(task.id)
    if not task or task.status != TaskStatus.IN_PROGRESS:
        log.info("Task %s claimed by another runner, skipping", task.id if task else task_id)
        return False

    write_pidfile(task.id)

    try:
        return _run_one_inner(store, task)
    finally:
        remove_pidfile(task.id)


def _get_task_timeout():
    # type: () -> int
    """Read task_timeout from runtime config, falling back to env/constant."""
    try:
        from .config import get_settings

        return get_settings().get("task_timeout", TASK_TIMEOUT)
    except Exception:
        return TASK_TIMEOUT


def _run_one_inner(store, task):
    # type: (DynamoTaskStore, Any) -> bool
    """Execute the pipeline for a claimed in_progress task."""
    task_timeout = _get_task_timeout()
    log.info("Picking up task %s: %s [%s]", task.id, task.title, task.priority.value)
    plog(
        task.id,
        "task_start",
        "pipeline",
        task.title,
        priority=task.priority.value,
        created_by=task.created_by or "",
    )

    if task.plan_only:
        return run_plan_only(store, task)

    ensure_repo(task)
    wt_path = create_worktree(task)

    if wt_path is None:
        msg = "Worktree creation failed; refusing to run agent in main checkout."
        log.error("Task %s: %s", task.id, msg)
        _append_text_to_task(store, task, "Agent Output", "**Error:** " + msg)
        store.update_status(task.id, TaskStatus.FAILED)
        _notify_pm_chat_task_terminal(task, "**failed** (worktree error)")
        _maybe_finalize_directive_batch(store, task.id)
        plog(task.id, "worktree_failed", "worktree", msg)
        return False

    agent_cwd = wt_path
    agent_succeeded = False
    push_failed = False
    pr_created = False

    try:
        prompt = None
        subtasks = []
        if AUTO_PLAN:
            existing_subs = store.list_subtasks(task.id)
            if not existing_subs:
                desc = (task.description or "").strip()
                desc_words = len(desc.split())
                is_simple = (
                    desc_words < int(os.getenv("PLAN_SKIP_WORDS", "40"))
                    and len(task.tags or []) < 3
                    and not any(
                        kw in desc.lower()
                        for kw in (
                            "step",
                            "then",
                            "after that",
                            "first",
                            "second",
                            "third",
                            "also",
                            "additionally",
                            "multiple",
                            "several",
                        )
                    )
                )
                if is_simple:
                    log.info(
                        "Skipping planning pass for task %s (simple, %d words)", task.id, desc_words
                    )
                    plog(
                        task.id,
                        "planning_skip",
                        "plan",
                        "Skipped — simple task (%d words)" % desc_words,
                    )
                else:
                    log.info("Running planning pass for task %s", task.id)
                    plog(task.id, "planning_start", "plan", "Decomposing task")
                    plan = plan_task(store, task, cwd=wt_path)

                    if plan and len(plan) > 1:
                        plog(
                            task.id, "planning_done", "plan", "Decomposed into %d steps" % len(plan)
                        )
                        subtasks = create_subtasks(store, task, plan)
                        plan_summary = "\n".join(
                            "- **%s** `%s`" % (s.title, s.id) for s in subtasks
                        )
                        _append_text_to_task(
                            store,
                            task,
                            "Plan",
                            "Decomposed into %d steps (single-session checklist):\n\n%s"
                            % (len(subtasks), plan_summary),
                        )
                        for sub in subtasks:
                            store.update_status(sub.id, TaskStatus.IN_PROGRESS)
                        prompt = _build_checklist_prompt(task, plan)
                        plog(
                            task.id,
                            "execute_start",
                            "execute",
                            "Running checklist (%d steps)" % len(plan),
                        )
                    elif plan and len(plan) == 1:
                        log.info("Plan returned 1 step — running directly")
                        plog(task.id, "planning_skip", "plan", "Single step, running directly")
                        _append_text_to_task(
                            store, task, "Plan", "Single-step task, running directly."
                        )
                    else:
                        plog(
                            task.id, "planning_skip", "plan", "No plan generated, running directly"
                        )

        if prompt is None:
            model = _resolve_model(task)
            plog(task.id, "execute_start", "execute", "Running directly", model=model or "default")
            prompt = build_prompt(task, agent_cwd=agent_cwd)

        model = _resolve_model(task)
        if model:
            store.set_model(task.id, model)
        result, elapsed, sid, usage = run_agent(
            prompt,
            cwd=agent_cwd,
            timeout=task_timeout,
            model=model,
            session_id=task.session_id or None,
            task_id=task.id,
        )
        if model:
            store.set_field(task.id, "model", model)
        if sid:
            _save_session_id(task.id, sid)
        append_result_to_task(store, task, result)
        agent_succeeded = result.returncode == 0

        if agent_succeeded:
            plog(task.id, "execute_done", "execute", "Agent completed", runtime=elapsed, **usage)

            if AUTO_DOCS and wt_path:
                diff_lines = _run_cmd(
                    ["git", "diff", "HEAD", "--", ".", ":(exclude)*.lock"],
                    cwd=wt_path,
                    timeout=15,
                ).stdout
                changed_lines = sum(
                    1
                    for ln in diff_lines.splitlines()
                    if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
                )
                docs_threshold = int(os.getenv("DOCS_MIN_LINES", "20"))
                if changed_lines < docs_threshold:
                    log.info(
                        "Skipping doc update for task %s (%d changed lines < %d threshold)",
                        task.id,
                        changed_lines,
                        docs_threshold,
                    )
                    plog(
                        task.id,
                        "docs_skip",
                        "docs",
                        "Skipped — diff too small (%d lines)" % changed_lines,
                    )
                else:
                    log.info(
                        "Running doc update for task %s (%d changed lines)", task.id, changed_lines
                    )
                    plog(task.id, "docs_start", "docs", "Running doc update")
                    run_doc_update(store, task, wt_path)
                    plog(task.id, "docs_done", "docs", "Doc update finished")

            if AUTO_PR and wt_path:
                log.info("Checking for PR-able changes for task %s", task.id)
                plog(task.id, "pr_start", "pr", "Creating PR")
                pr_result = commit_and_create_pr(store, task, wt_path)
                if pr_result == PUSH_FAILED_SENTINEL:
                    push_failed = True
                    plog(task.id, "pr_skip", "pr", "Push failed — worktree preserved")
                elif pr_result == WRONG_DIR_SENTINEL:
                    plog(
                        task.id,
                        "pr_skip",
                        "pr",
                        "Agent wrote to main checkout instead of worktree",
                    )
                    for sub in subtasks:
                        store.update_status(sub.id, TaskStatus.FAILED)
                    store.update_status(task.id, TaskStatus.FAILED)
                    _notify_pm_chat_task_terminal(task, "**failed** (wrong directory)")
                    _maybe_finalize_directive_batch(store, task.id)
                    log.error("Task %s: agent wrote to main checkout — marking failed", task.id)
                    plog(
                        task.id,
                        "task_failed",
                        "pipeline",
                        "Wrong directory — worktree clean but main checkout dirty",
                    )
                    return True
                elif pr_result and pr_result != NO_CHANGES_SENTINEL:
                    pr_created = True
                    plog(task.id, "pr_done", "pr", pr_result)
                elif pr_result == NO_CHANGES_SENTINEL:
                    plog(task.id, "pr_skip", "pr", "No changes to commit")
                else:
                    plog(task.id, "pr_skip", "pr", "CI checks failed or PR creation failed")

            final_status = TaskStatus.IN_REVIEW if pr_created else TaskStatus.COMPLETED
            for sub in subtasks:
                store.update_status(sub.id, final_status)
            store.update_status(task.id, final_status)
            _notify_pm_chat_task_terminal(
                task,
                "**%s**" % final_status.value.replace("_", " "),
            )
            _maybe_finalize_directive_batch(store, task.id)
            trigger_unblocked_dependents(store, task.id)
            log.info("Task %s completed (status=%s)", task.id, final_status.value)
            plog(task.id, "task_done", "pipeline", "Completed", runtime=elapsed)
        else:
            for sub in subtasks:
                store.update_status(sub.id, TaskStatus.FAILED)
            store.update_status(task.id, TaskStatus.FAILED)
            _notify_pm_chat_task_terminal(task, "**failed** (agent exit %d)" % result.returncode)
            _maybe_finalize_directive_batch(store, task.id)
            log.warning("Task %s failed (exit %d, %.1fs)", task.id, result.returncode, elapsed)
            plog(
                task.id,
                "task_failed",
                "pipeline",
                "Agent failed (exit %d)" % result.returncode,
                runtime=elapsed,
            )

    except subprocess.TimeoutExpired as exc:
        log.error("Task %s timed out after %ds", task.id, task_timeout)
        sid = getattr(exc, "session_id", "") or ""
        if sid:
            _save_session_id(task.id, sid)
        partial = _extract_agent_text(getattr(exc, "stdout", None) or "")
        timeout_note = "**Timed out after %ds**" % task_timeout
        if partial and partial != "(no output)":
            timeout_note += "\n\n**Partial output (work completed before timeout):**\n\n" + partial
        _append_text_to_task(store, task, "Agent Output", timeout_note)
        for sub in subtasks:
            store.update_status(sub.id, TaskStatus.FAILED)
        store.update_status(task.id, TaskStatus.FAILED)
        _notify_pm_chat_task_terminal(task, "**failed** (timeout)")
        _maybe_finalize_directive_batch(store, task.id)
        plog(task.id, "task_timeout", "pipeline", "Timed out after %ds" % task_timeout)

    except Exception as exc:
        log.exception("Unexpected error running task %s", task.id)
        if agent_succeeded:
            store.update_status(task.id, TaskStatus.COMPLETED)
            _notify_pm_chat_task_terminal(task, "**completed** (post-agent pipeline error)")
            _maybe_finalize_directive_batch(store, task.id)
            trigger_unblocked_dependents(store, task.id)
            _append_text_to_task(
                store,
                task,
                "Warning",
                "Pipeline error after agent completed: `%s`. "
                "Agent output is preserved above. Create PR manually if needed." % str(exc)[:200],
            )
            plog(
                task.id,
                "task_error",
                "pipeline",
                "Post-execution error (task preserved as completed): %s" % str(exc)[:200],
            )
        else:
            store.update_status(task.id, TaskStatus.FAILED)
            _notify_pm_chat_task_terminal(task, "**failed** (pipeline error)")
            _maybe_finalize_directive_batch(store, task.id)
            plog(task.id, "task_error", "pipeline", str(exc)[:200])

    finally:
        if wt_path:
            if push_failed:
                log.warning("Preserving worktree %s — push failed, manual recovery needed", wt_path)
            elif pr_created:
                log.info("Preserving worktree %s — PR open, cleanup deferred until merge", wt_path)
            else:
                cleanup_worktree(task, wt_path)

    return True
