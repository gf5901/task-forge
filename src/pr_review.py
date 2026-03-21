"""
Periodic PR review agent — reviews open PRs and posts feedback.

Runs the review agent against each open PR, posts a GitHub comment with the verdict,
and appends it to the originating task for visibility.
"""

import logging
import os
import re
import subprocess
import tempfile

from .agent import (
    MODEL_FULL,
    SECURITY_PREFIX,
    _append_text_to_task,
    _extract_agent_text,
    run_agent,
)
from .worktree import GH_BIN, _run_cmd

log = logging.getLogger(__name__)

PR_REVIEW_TIMEOUT = int(os.getenv("PR_REVIEW_TIMEOUT", "300"))
PR_REVIEW_MAX_PRS = int(os.getenv("PR_REVIEW_MAX_PRS", "10"))

# Use MODEL_FULL (e.g. claude-opus) falling back to MODEL_DEFAULT, then agent default.
_MODEL_DEFAULT = os.getenv("MODEL_DEFAULT", "")
PR_REVIEW_MODEL = os.getenv("PR_REVIEW_MODEL", MODEL_FULL or _MODEL_DEFAULT or "")

PR_REVIEW_PROMPT = (
    SECURITY_PREFIX
    + """\
You are a senior code reviewer performing a thorough review of a pull request.

## Pull Request
Title: %(title)s
Branch: %(branch)s
Author: %(author)s
URL: %(url)s

## Task that originated this PR
Task ID: %(task_id)s
Task Title: %(task_title)s
Task Description:
%(task_description)s

## CI Status
%(ci_status)s

## Diff Stats
%(diff_stats)s

## Full Diff (truncated to 8000 chars)
%(diff)s

## Your job
Review the PR carefully. Consider:
1. Does the diff correctly implement what the task asked for?
2. Are there bugs, logic errors, or missing edge-case handling?
3. Are there obvious security issues (e.g. secrets in code, SQL injection, XSS)?
4. Are there style/lint violations or obviously broken tests?
5. Is anything incomplete or obviously missing?

Respond with EXACTLY one of these two formats (no extra text before the verdict line):

If the PR is ready to merge:
LGTM: <one sentence explaining why it looks good>

If changes are needed:
NEEDS_WORK: <concise bullet-point list of specific issues that must be fixed>

After the verdict line you may add optional detail, but the first line must be the verdict.
"""
)


def _list_open_prs(repo_dir):
    # type: (str) -> list
    """Return a list of open PR dicts via gh CLI."""
    import json as _json

    result = _run_cmd(
        [
            GH_BIN,
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,headRefName,author,url,body",
            "--limit",
            str(PR_REVIEW_MAX_PRS),
        ],
        cwd=repo_dir,
        timeout=30,
    )
    if result.returncode != 0:
        log.warning("gh pr list failed: %s", result.stderr[:200])
        return []
    try:
        prs = _json.loads(result.stdout or "[]")
        return prs if isinstance(prs, list) else []
    except ValueError:
        log.warning("gh pr list JSON parse error")
        return []


def _get_pr_diff(pr_number, repo_dir):
    # type: (int, str) -> tuple
    """Return (diff_stats, diff_body) for a PR number."""
    stat = _run_cmd(
        [GH_BIN, "pr", "diff", str(pr_number), "--patch"],
        cwd=repo_dir,
        timeout=30,
    )
    if stat.returncode != 0:
        return "(diff unavailable)", ""
    full = stat.stdout or ""
    lines = full.splitlines()
    stat_lines = [ln for ln in lines if ln.startswith(("---", "+++", "@@", "diff --git"))]
    stats_summary = "\n".join(stat_lines[:30]) or "(no stats)"
    return stats_summary, full[:8000]


def _get_pr_ci_status(pr_number, repo_dir):
    # type: (int, str) -> str
    """Return a human-readable CI status summary for a PR."""
    import json as _json

    result = _run_cmd(
        [GH_BIN, "pr", "checks", str(pr_number), "--json", "name,state,conclusion"],
        cwd=repo_dir,
        timeout=30,
    )
    if result.returncode != 0:
        return "No CI data available"
    try:
        items = _json.loads(result.stdout or "[]")
    except ValueError:
        return "No CI data available"
    if not items:
        return "No CI checks found"
    lines = []
    for c in items:
        state = c.get("state", "UNKNOWN")
        name = c.get("name", "unnamed")
        lines.append("- %s: %s" % (name, state))
    return "\n".join(lines)


def _find_task_for_pr(store, pr):
    # type: (Any, dict) -> Any
    """Find the task that originated a PR.

    Looks for 'Task ID:' in the PR body, then falls back to parsing the
    branch name (task/<id>-<slug>).
    """
    body = pr.get("body") or ""
    branch = pr.get("headRefName") or ""

    # Try explicit Task ID tag in PR body first
    task_id_match = re.search(r"\*\*Task ID:\*\*\s*`([^`]+)`", body)
    if not task_id_match:
        task_id_match = re.search(r"Task ID[:\s]+([a-f0-9]{8,})", body, re.IGNORECASE)
    if task_id_match:
        task_id = task_id_match.group(1).strip()
        task = store.get(task_id)
        if task:
            return task

    # Fall back to branch name: task/<id>-<slug>
    branch_match = re.match(r"task/([a-f0-9]{8,})-", branch)
    if branch_match:
        task_id = branch_match.group(1)
        task = store.get(task_id)
        if task:
            return task

    return None


def _post_gh_pr_comment(pr_number, body, repo_dir):
    # type: (int, str, str) -> bool
    """Post a comment on a GitHub PR. Returns True on success."""
    result = _run_cmd(
        [GH_BIN, "pr", "comment", str(pr_number), "--body", body],
        cwd=repo_dir,
        timeout=30,
    )
    if result.returncode != 0:
        log.warning("Failed to post GH comment on PR #%s: %s", pr_number, result.stderr[:200])
        return False
    return True


def _already_reviewed_recently(pr_number, repo_dir):
    # type: (int, str) -> bool
    """Return True if a bot review comment already exists on this PR (avoids duplicate spam)."""
    result = _run_cmd(
        [
            GH_BIN,
            "pr",
            "view",
            str(pr_number),
            "--json",
            "comments",
            "--jq",
            ".comments[].body",
        ],
        cwd=repo_dir,
        timeout=30,
    )
    if result.returncode != 0:
        return False
    # If we already posted a LGTM/NEEDS_WORK comment, skip re-review
    existing = result.stdout or ""
    return bool(re.search(r"^(LGTM|NEEDS_WORK):", existing, re.MULTILINE))


def review_pr(store, pr, repo_dir):
    # type: (Any, dict, str) -> Any
    """Review a single PR. Returns the verdict string, or None on failure."""
    pr_number = pr.get("number")
    title = pr.get("title", "(untitled)")
    branch = pr.get("headRefName", "")
    author_info = pr.get("author") or {}
    author = author_info.get("login", "unknown") if isinstance(author_info, dict) else "unknown"
    url = pr.get("url", "")

    log.info("Reviewing PR #%s: %s", pr_number, title)

    # Skip PRs authored by bots to prevent feedback loops
    _skip_authors = {"github-actions[bot]", "dependabot[bot]"}
    _skip_authors.add(os.getenv("GH_BOT_USER", ""))
    if author in _skip_authors:
        log.info("PR #%s authored by %s — skipping bot PR", pr_number, author)
        return None

    if _already_reviewed_recently(pr_number, repo_dir):
        log.info("PR #%s already has a bot review — skipping", pr_number)
        return None

    task = _find_task_for_pr(store, pr)
    task_id = task.id if task else "(unknown)"
    task_title = task.title if task else title
    task_description = (task.description or "(none)") if task else "(none)"

    diff_stats, diff_body = _get_pr_diff(pr_number, repo_dir)
    ci_status = _get_pr_ci_status(pr_number, repo_dir)

    prompt = PR_REVIEW_PROMPT % {
        "title": title,
        "branch": branch,
        "author": author,
        "url": url,
        "task_id": task_id,
        "task_title": task_title,
        "task_description": task_description,
        "ci_status": ci_status,
        "diff_stats": diff_stats,
        "diff": diff_body,
    }

    try:
        with tempfile.TemporaryDirectory(prefix="pr-review-%s-" % pr_number) as review_dir:
            result, elapsed, _, _usage = run_agent(
                prompt,
                cwd=review_dir,
                timeout=PR_REVIEW_TIMEOUT,
                model=PR_REVIEW_MODEL or None,
            )
    except subprocess.TimeoutExpired:
        log.warning("PR review timed out for PR #%s", pr_number)
        return None
    except Exception:
        log.exception("PR review agent error for PR #%s", pr_number)
        return None

    if result.returncode != 0:
        log.warning("PR review agent failed for PR #%s (exit %d)", pr_number, result.returncode)
        return None

    verdict = _extract_agent_text(result.stdout).strip()
    if not verdict:
        log.warning("PR review returned empty verdict for PR #%s", pr_number)
        return None

    log.info("PR #%s verdict (%.1fs): %s", pr_number, elapsed, verdict[:120])

    # Post to GitHub PR
    gh_comment = "**Automated PR Review**\n\n%s" % verdict
    _post_gh_pr_comment(pr_number, gh_comment, repo_dir)

    # Wire back to the task if found
    if task:
        _append_text_to_task(store, task, "PR Review", verdict)

        if verdict.upper().startswith("NEEDS_WORK"):
            log.info("PR #%s needs work — adding review note to task %s", pr_number, task.id)
            store.add_comment(
                task.id,
                "pr-reviewer",
                "**PR #%s needs changes:**\n\n%s" % (pr_number, verdict),
            )

    return verdict


def _get_repo_dir():
    # type: () -> str
    """Return the Task Forge repo directory (where gh CLI should run)."""
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    return str(project_root)


def run_pr_review(store):
    # type: (Any) -> list
    """List open PRs and review each one.

    Returns a list of result dicts with keys: pr_number, title, verdict.
    """
    repo_dir = _get_repo_dir()
    prs = _list_open_prs(repo_dir)

    if not prs:
        log.info("No open PRs to review")
        return []

    log.info("Found %d open PR(s) to review", len(prs))
    results = []

    for pr in prs:
        pr_number = pr.get("number")
        title = pr.get("title", "(untitled)")
        try:
            verdict = review_pr(store, pr, repo_dir)
            results.append({"pr_number": pr_number, "title": title, "verdict": verdict})
        except Exception:
            log.exception("Unhandled error reviewing PR #%s", pr_number)
            results.append({"pr_number": pr_number, "title": title, "verdict": None})

    return results
