"""
PR creation, CI polling, and git commit for task worktrees.
"""

import logging
import os
import re

from .agent import MODEL_FAST, _append_text_to_task, _extract_agent_text, run_agent
from .worktree import GH_BIN, _get_default_branch, _resolve_repo_dir, _run_cmd

log = logging.getLogger(__name__)

CI_CHECK_TIMEOUT = int(os.getenv("CI_CHECK_TIMEOUT", "600"))

SENSITIVE_PATTERNS = [
    ".env",
    ".env.*",
    "scripts/deploy.sh",
    ".github/workflows/*",
    ".cursor/rules/*",
    "sst.config.ts",
    "infra/sst.config.ts",
    "**/credentials*",
    "**/*secret*",
]

_SENSITIVE_OVERRIDE_TAG = "allow-sensitive-files"


def _check_sensitive_files(task, wt_path):
    # type: (Any, str) -> list
    """Return list of sensitive files in the staged diff, or [] if none / overridden."""
    if _SENSITIVE_OVERRIDE_TAG in (task.tags or []):
        return []

    import fnmatch

    diff = _run_cmd(["git", "diff", "--cached", "--name-only"], cwd=wt_path)
    changed = [f.strip() for f in diff.stdout.strip().splitlines() if f.strip()]

    blocked = []
    for filepath in changed:
        for pattern in SENSITIVE_PATTERNS:
            if fnmatch.fnmatch(filepath, pattern):
                blocked.append(filepath)
                break
    return blocked


NO_CHANGES_SENTINEL = "NO_CHANGES"
PUSH_FAILED_SENTINEL = "PUSH_FAILED"
WRONG_DIR_SENTINEL = "WRONG_DIR"

PR_SUMMARY_PROMPT = """\
Write a concise pull request description for the following changes.

Task: %s
Description: %s

Git diff summary:
%s

Files changed:
%s

Respond with ONLY the markdown body (no fences). Use this structure:
## Summary
1-3 bullet points of what changed and why.

## Changes
- List each meaningful change (file: what changed)

## Testing
- How to verify these changes work (1-2 bullets)"""


PR_VERIFY_PROMPT = """\
You are reviewing a pull request to verify it correctly implements the task.

Task: %s
Description: %s

PR diff summary:
%s

Full diff (truncated):
%s

Does this PR correctly implement what was asked? Look for:
1. Are the right files changed (nothing obviously missing or unrelated)?
2. Does the diff make sense for the task description?
3. Are there any obvious bugs or incomplete changes?

Respond with either:
- "LGTM" followed by a brief explanation if the PR looks correct
- "CONCERN: <issue>" if something looks wrong or incomplete

Be concise."""


def _verify_pr_diff(task, wt_path):
    # type: (..., str) -> Tuple[bool, str]
    """Run a fast agent to verify the PR diff matches the task. Returns (ok, note).

    Diffs against origin/main (not HEAD~1) so it works correctly even if the
    worktree branch has multiple commits. Runs the agent in a tempdir to prevent
    any accidental file writes during the reasoning-only review.
    """
    import tempfile

    default_branch = _get_default_branch(_resolve_repo_dir(task))
    diff_stat = _run_cmd(["git", "diff", "origin/%s" % default_branch, "--stat"], cwd=wt_path)
    diff_short = _run_cmd(
        ["git", "diff", "origin/%s" % default_branch, "--no-color", "-U3"],
        cwd=wt_path,
        timeout=30,
    )

    stat_text = diff_stat.stdout.strip()[:500] if diff_stat.stdout else "(no stats)"
    diff_text = diff_short.stdout.strip()[:4000] if diff_short.stdout else "(no diff)"

    prompt = PR_VERIFY_PROMPT % (
        task.title,
        task.description or "(none)",
        stat_text,
        diff_text,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="verify-%s-" % task.id) as verify_dir:
            result, _elapsed, _, _usage = run_agent(
                prompt, cwd=verify_dir, timeout=60, model=MODEL_FAST
            )
        if result.returncode == 0 and result.stdout.strip():
            text = _extract_agent_text(result.stdout)
            ok = not text.upper().startswith("CONCERN")
            return ok, text
    except Exception:
        log.warning("PR verification failed for task %s — treating as OK", task.id)

    return True, "(verification skipped)"


def _generate_pr_body(task, wt_path):
    # type: (..., str) -> str
    """Generate a rich PR body using the diff and an agent summary."""
    diff_stat = _run_cmd(["git", "diff", "HEAD~1", "--stat"], cwd=wt_path)
    diff_short = _run_cmd(
        ["git", "diff", "HEAD~1", "--no-color", "-U2"],
        cwd=wt_path,
        timeout=30,
    )

    stat_text = diff_stat.stdout.strip()[:500] if diff_stat.stdout else "(no stats)"
    diff_text = diff_short.stdout.strip()[:3000] if diff_short.stdout else "(no diff)"

    prompt = PR_SUMMARY_PROMPT % (
        task.title,
        task.description or "(none)",
        stat_text,
        diff_text,
    )

    try:
        result, _elapsed, _, _usage = run_agent(prompt, cwd=wt_path, timeout=60, model=MODEL_FAST)
        if result.returncode == 0 and result.stdout.strip():
            body = _extract_agent_text(result.stdout)
            body += "\n\n---\n\n"
            body += "**Task ID:** `%s`\n" % task.id
            body += "**Priority:** %s\n" % task.priority.value
            body += "**Created by:** %s\n" % (task.created_by or "system")
            return body
    except Exception:
        log.warning("PR summary generation failed for task %s, using fallback", task.id)

    return (
        "## Summary\n\n"
        "Automated PR from task `%s`.\n\n"
        "**Task:** %s\n"
        "**Priority:** %s\n"
        "**Created by:** %s\n\n"
        "## Changes\n\n```\n%s\n```\n"
    ) % (task.id, task.title, task.priority.value, task.created_by or "system", stat_text)


def _wait_for_pr_ci(pr_number, cwd, timeout=CI_CHECK_TIMEOUT):
    # type: (str, str, int) -> bool
    """Wait for CI checks on a PR to complete. Returns True if all passed (or no checks).

    CI only triggers on pull_request events, not on branch pushes, so we must
    create the PR first and then poll here. Timeout is non-fatal — PR already exists.
    """
    import time as _time

    log.info("Waiting for CI checks on PR #%s (timeout=%ds)", pr_number, timeout)
    deadline = _time.monotonic() + timeout

    # Give GitHub a moment to register the PR and start workflows
    _time.sleep(15)

    while _time.monotonic() < deadline:
        checks = _run_cmd(
            [GH_BIN, "pr", "checks", pr_number, "--json", "state,name,conclusion"],
            cwd=cwd,
            timeout=30,
        )
        if checks.returncode != 0:
            log.info(
                "gh pr checks non-zero for PR #%s (may have no checks): %s",
                pr_number,
                checks.stderr[:100],
            )
            return True

        try:
            import json as _json

            items = _json.loads(checks.stdout or "[]")
        except ValueError:
            _time.sleep(15)
            continue

        if not items:
            log.info("No CI checks found for PR #%s — treating as passed", pr_number)
            return True

        pending = [
            c
            for c in items
            if c.get("state") not in ("SUCCESS", "FAILURE", "ERROR", "CANCELLED", "SKIPPED")
        ]
        if pending:
            log.info("CI still running on PR #%s (%d pending)...", pr_number, len(pending))
            _time.sleep(20)
            continue

        failed = [c for c in items if c.get("state") in ("FAILURE", "ERROR")]
        if failed:
            log.warning(
                "CI checks failed on PR #%s: %s", pr_number, [c.get("name") for c in failed]
            )
            return False

        log.info("All CI checks passed on PR #%s", pr_number)
        return True

    log.warning("Timed out waiting for CI on PR #%s after %ds", pr_number, timeout)
    return True


REPLY_CI_TIMEOUT = int(os.getenv("REPLY_CI_TIMEOUT", "180"))


def poll_ci_status(pr_number, cwd, timeout=REPLY_CI_TIMEOUT):
    # type: (str, str, int) -> tuple
    """Poll CI checks on a PR and return (passed, summary_markdown).

    Similar to _wait_for_pr_ci but returns a human-readable summary suitable
    for saving to the task record.  Uses a shorter default timeout since this
    is called from comment-reply context, not the initial PR creation.

    Returns:
      (True,  "All CI checks **passed** …")
      (False, "CI checks **failed**: `build`, `lint`")
      (True,  None)   — no checks found or gh error (nothing to report)
    """
    import json as _json
    import time as _time

    log.info("Polling CI status on PR #%s (timeout=%ds)", pr_number, timeout)
    deadline = _time.monotonic() + timeout

    _time.sleep(10)

    while _time.monotonic() < deadline:
        checks = _run_cmd(
            [GH_BIN, "pr", "checks", pr_number, "--json", "state,name,conclusion"],
            cwd=cwd,
            timeout=30,
        )
        if checks.returncode != 0:
            return True, None

        try:
            items = _json.loads(checks.stdout or "[]")
        except ValueError:
            _time.sleep(10)
            continue

        if not items:
            return True, None

        pending = [
            c
            for c in items
            if c.get("state") not in ("SUCCESS", "FAILURE", "ERROR", "CANCELLED", "SKIPPED")
        ]
        if pending:
            _time.sleep(15)
            continue

        failed = [c for c in items if c.get("state") in ("FAILURE", "ERROR")]
        if failed:
            names = ", ".join("`%s`" % c.get("name", "?") for c in failed)
            summary = "CI checks **failed** on PR #%s: %s" % (pr_number, names)
            log.warning("CI failed on PR #%s: %s", pr_number, [c.get("name") for c in failed])
            return False, summary

        log.info("All CI checks passed on PR #%s", pr_number)
        return True, "All CI checks **passed** on PR #%s" % pr_number

    log.warning("Timed out polling CI on PR #%s after %ds", pr_number, timeout)
    return True, "CI checks still running on PR #%s (timed out after %ds)" % (pr_number, timeout)


def commit_and_create_pr(store, task, wt_path):
    # type: (Any, Any, str) -> Optional[str]
    """Compound step: commit all changes in the worktree and open a PR.

    Returns:
      - PR URL string on success
      - NO_CHANGES_SENTINEL if the worktree has no changes to commit
      - None if push or PR creation failed
    """
    status = _run_cmd(["git", "status", "--porcelain"], cwd=wt_path)
    if not status.stdout.strip():
        log.info("No changes to commit for task %s", task.id)
        repo_dir = _resolve_repo_dir(task)
        main_status = _run_cmd(["git", "status", "--porcelain"], cwd=repo_dir)
        if main_status.stdout.strip():
            log.warning(
                "Task %s: worktree is clean but main checkout has uncommitted changes — "
                "agent may have written to the wrong directory: %s",
                task.id,
                main_status.stdout.strip()[:300],
            )
            _append_text_to_task(
                store,
                task,
                "Warning",
                "The worktree was clean after execution (no changes to commit), but the "
                "**main checkout has uncommitted changes**. The agent may have edited files "
                "outside the worktree. Main checkout diff:\n\n```\n%s\n```\n\n"
                "Review and commit manually if needed." % main_status.stdout.strip()[:800],
            )
            return WRONG_DIR_SENTINEL
        return NO_CHANGES_SENTINEL

    _run_cmd(["git", "add", "-A"], cwd=wt_path)

    blocked = _check_sensitive_files(task, wt_path)
    if blocked:
        log.warning("Task %s: PR blocked — sensitive files modified: %s", task.id, blocked)
        _run_cmd(["git", "reset", "HEAD"], cwd=wt_path)
        _append_text_to_task(
            store,
            task,
            "Warning",
            "**PR creation blocked** — the agent modified sensitive files that are not "
            "allowed to be changed by automated tasks:\n\n%s\n\n"
            "Changes have been unstaged. Review the worktree manually if the modifications "
            "are intentional." % "\n".join("- `%s`" % f for f in blocked),
        )
        return None

    commit_msg = "task(%s): %s" % (task.id, task.title)
    commit = _run_cmd(["git", "commit", "-m", commit_msg], cwd=wt_path)
    if commit.returncode != 0:
        log.warning("Nothing to commit for task %s: %s", task.id, commit.stdout.strip())
        return NO_CHANGES_SENTINEL

    branch = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=wt_path).stdout.strip()

    import time as _time

    push = None
    for attempt in range(3):
        push = _run_cmd(["git", "push", "-u", "origin", branch], cwd=wt_path, timeout=60)
        if push.returncode == 0:
            break
        delay = 2 ** (attempt + 1)
        log.warning(
            "Push attempt %d/3 failed for branch %s (retrying in %ds): %s",
            attempt + 1,
            branch,
            delay,
            push.stderr[:200],
        )
        _time.sleep(delay)

    if push.returncode != 0:
        log.error("All push attempts failed for branch %s: %s", branch, push.stderr)
        _append_text_to_task(
            store,
            task,
            "Push Failed",
            "Failed to push branch `%s` after 3 attempts.\n\n"
            "Error: ```\n%s\n```\n\n"
            "The worktree has been preserved at `%s` for manual recovery."
            % (branch, push.stderr[:500], wt_path),
        )
        return PUSH_FAILED_SENTINEL

    repo_dir = _resolve_repo_dir(task)
    default_branch = _get_default_branch(repo_dir)
    pr_body = _generate_pr_body(task, wt_path)

    pr = _run_cmd(
        [
            GH_BIN,
            "pr",
            "create",
            "--title",
            commit_msg,
            "--body",
            pr_body,
            "--base",
            default_branch,
        ],
        cwd=wt_path,
        timeout=60,
    )

    if pr.returncode != 0:
        log.error("Failed to create PR for task %s: %s", task.id, pr.stderr)
        return None

    pr_url = pr.stdout.strip()
    log.info("Created PR for task %s: %s", task.id, pr_url)
    _append_text_to_task(store, task, "PR Created", pr_url)
    store.set_pr_url(task.id, pr_url)

    # Verify the diff looks correct for the task before marking in_review
    pr_ok, verify_note = _verify_pr_diff(task, wt_path)
    if not pr_ok:
        log.warning("PR verification concern for task %s: %s", task.id, verify_note)
        _append_text_to_task(
            store,
            task,
            "PR Verification",
            "**Concern flagged by automated review:**\n\n%s\n\n"
            "PR [%s](%s) was created but may need manual review before merging."
            % (verify_note, pr_url, pr_url),
        )
    else:
        log.info("PR verification passed for task %s: %s", task.id, verify_note)

    pr_num_match = re.search(r"/pull/(\d+)", pr_url)
    if pr_num_match:
        pr_number = pr_num_match.group(1)
        ci_passed = _wait_for_pr_ci(pr_number, wt_path)
        if not ci_passed:
            log.warning("CI checks failed on PR #%s for task %s", pr_number, task.id)
            _append_text_to_task(
                store,
                task,
                "CI Failed",
                "CI checks failed on PR [#%s](%s). Review before merging." % (pr_number, pr_url),
            )

    return pr_url
