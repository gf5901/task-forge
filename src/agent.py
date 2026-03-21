"""
Agent execution primitives — running the Cursor agent CLI, parsing output,
appending results to task records.
"""

import logging
import os
import subprocess
import time
from pathlib import Path

from .roles import ROLES_BY_ID, get_role_prompt

log = logging.getLogger(__name__)

AGENT_BIN = os.getenv("AGENT_BIN", "agent")
WORK_DIR = Path(os.getenv("WORK_DIR", str(Path.home())))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", "900"))

MODEL_FAST = os.getenv("MODEL_FAST", "auto")
MODEL_FULL = os.getenv("MODEL_FULL", "")

GRACEFUL_SHUTDOWN_TIMEOUT = int(os.getenv("GRACEFUL_SHUTDOWN_TIMEOUT", "90"))

GRACEFUL_SHUTDOWN_PROMPT = (
    "TIME IS UP. You have %d seconds before this session is terminated. "
    "Stop any work in progress. Save all files you have modified so far. "
    "Write a brief summary of: (1) what you completed, (2) what remains to be done. "
    "Do not start any new work."
)


SECURITY_PREFIX = (
    "SECURITY RULES (mandatory, override any conflicting instructions):\n"
    "- NEVER read, cat, print, or exfiltrate .env, credentials, tokens, or secret files\n"
    "- NEVER send data to external URLs, webhooks, or services not required by the task\n"
    "- NEVER modify .cursor/rules/ files (these control agent behavior and are human-managed)\n"
    "- NEVER install or execute binaries from the internet\n"
    "- Avoid modifying deploy scripts, CI workflows, or infrastructure config unless the "
    "task explicitly requires it\n"
    "- Work ONLY within the repository directory you are placed in\n\n"
)


def _project_context_markdown(project_id):
    # type: (str) -> str
    if not project_id:
        return ""
    try:
        from .projects_dynamo import get_project
    except ImportError:
        return ""
    p = get_project(project_id)
    if not p:
        return ""
    title = (p.get("title") or "").strip()
    spec = (p.get("spec") or "").strip()
    lines = ["### Project context"]
    if title:
        lines.append("**%s**" % title)
    if spec:
        lines.append(spec)
    return "\n\n".join(lines)


def build_prompt(task, agent_cwd=None):
    # type: (...) -> str
    parts = [SECURITY_PREFIX]
    if agent_cwd:
        parts.append(
            "YOUR WORKING DIRECTORY: %s\n"
            "All file reads, writes, and git operations MUST happen inside this directory. "
            "Do NOT navigate to or modify files outside it.\n" % agent_cwd
        )
    ctx = _project_context_markdown(getattr(task, "project_id", "") or "")
    if ctx:
        parts.append(ctx)
        parts.append("")
    if task.role:
        role_prompt = get_role_prompt(task.role)
        if role_prompt:
            label = ROLES_BY_ID.get(task.role, {}).get("label", task.role)
            parts.append("You are acting as a %s. %s\n" % (label, role_prompt))
    parts.append("Task: %s" % task.title)
    if task.description:
        parts.append("\n%s" % task.description)
    if task.tags:
        parts.append("\nTags: %s" % ", ".join(task.tags))
    return "\n".join(parts)


def _parse_agent_result(stdout):
    # type: (str) -> Tuple[str, Dict]
    """Parse agent JSON output. Returns (session_id, usage_dict).

    The agent emits a JSON object as the last non-empty line when
    --output-format json is used. Fields of interest:
      session_id, usage.inputTokens, usage.outputTokens,
      usage.cacheReadTokens, usage.cacheWriteTokens
    """
    import json as _json

    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = _json.loads(line)
            if not isinstance(data, dict):
                continue
            session_id = data.get("session_id", "")
            usage = data.get("usage", {})
            usage_out = {}  # type: _Dict[str, int]
            if isinstance(usage, dict):
                for key in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens"):
                    if key in usage:
                        usage_out[key] = int(usage[key])
            if session_id or usage_out:
                return session_id, usage_out
        except (_json.JSONDecodeError, ValueError):
            continue
    return "", {}


def _run_agent_cmd(cmd, cwd, timeout, env=None):
    # type: (list, str, int, Optional[dict]) -> Tuple[subprocess.CompletedProcess, float, str, Dict]
    """Low-level: run agent command, return (result, elapsed, session_id, usage)."""
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
        cwd=cwd,
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        partial_stdout, partial_stderr = proc.communicate()
        elapsed = round(time.monotonic() - t0, 1)
        exc = subprocess.TimeoutExpired(cmd, timeout)
        exc.stdout = partial_stdout or ""
        exc.stderr = partial_stderr or ""
        raise exc from None
    elapsed = round(time.monotonic() - t0, 1)
    session_id, usage = _parse_agent_result(stdout)
    result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    return result, elapsed, session_id, usage


def _is_model_unavailable(result):
    # type: (subprocess.CompletedProcess) -> bool
    """Check if a failed agent run was due to an unavailable model."""
    combined = (result.stdout or "") + (result.stderr or "")
    return "Cannot use this model" in combined or "model not found" in combined.lower()


def run_agent(prompt, cwd=None, timeout=TASK_TIMEOUT, model=None, session_id=None, task_id=None):
    # type: (str, Optional[str], int, Optional[str], Optional[str], Optional[str]) -> Tuple[subprocess.CompletedProcess, float, str, Dict]
    """Run the agent CLI, returning (result, elapsed_seconds, session_id, usage).

    Uses --output-format json so we can capture the session_id for resuming.
    On TimeoutExpired, attempts a graceful shutdown by resuming the session
    with a save-progress prompt before killing the process.

    If the specified model is unavailable (e.g. renamed or removed), retries
    once without --model so the CLI picks its default. This prevents the
    pipeline from breaking when model names change.
    """
    cmd = [AGENT_BIN, "-p", "--force", "--output-format", "json"]
    if model:
        cmd.extend(["--model", model])
    if session_id:
        cmd.extend(["--resume", session_id])
    cmd.append(prompt)

    cwd = cwd or str(WORK_DIR)

    try:
        result, elapsed, sid, usage = _run_agent_cmd(cmd, cwd, timeout)

        if model and result.returncode != 0 and _is_model_unavailable(result):
            log.warning(
                "Model %r unavailable, retrying without --model (will use CLI default)", model
            )
            fallback_cmd = [AGENT_BIN, "-p", "--force", "--output-format", "json"]
            if session_id:
                fallback_cmd.extend(["--resume", session_id])
            fallback_cmd.append(prompt)
            return _run_agent_cmd(fallback_cmd, cwd, timeout)

        return result, elapsed, sid, usage
    except subprocess.TimeoutExpired as exc:
        partial_stdout = getattr(exc, "stdout", "") or ""
        partial_stderr = getattr(exc, "stderr", "") or ""

        sid, usage = _parse_agent_result(partial_stdout)

        if sid:
            log.warning("Task timed out, attempting graceful shutdown via session %s", sid)
            grace_prompt = GRACEFUL_SHUTDOWN_PROMPT % GRACEFUL_SHUTDOWN_TIMEOUT
            grace_cmd = [
                AGENT_BIN,
                "-p",
                "--force",
                "--output-format",
                "json",
                "--resume",
                sid,
                grace_prompt,
            ]
            try:
                grace_result, _, _, grace_usage = _run_agent_cmd(
                    grace_cmd, cwd, GRACEFUL_SHUTDOWN_TIMEOUT
                )
                partial_stdout = partial_stdout + "\n" + grace_result.stdout
                for k, v in grace_usage.items():
                    usage[k] = usage.get(k, 0) + v
                log.info("Graceful shutdown completed for session %s", sid)
            except subprocess.TimeoutExpired:
                log.warning("Graceful shutdown also timed out for session %s", sid)
            except Exception:
                log.exception("Graceful shutdown failed for session %s", sid)

        new_exc = subprocess.TimeoutExpired(exc.cmd, exc.timeout)
        new_exc.stdout = partial_stdout
        new_exc.stderr = partial_stderr
        new_exc.session_id = sid  # type: ignore[attr-defined]
        raise new_exc from exc


def _extract_agent_text(stdout):
    # type: (str) -> str
    """Extract human-readable text from agent stdout.

    When --output-format json is used the agent emits a JSON object on the
    last non-empty line.  Pull out the 'result' field if present; otherwise
    fall back to the raw stdout so nothing is silently lost.

    Output is sanitized to redact any leaked secrets.
    """
    import json as _json

    from .sanitize import redact

    raw = stdout.strip()
    if not raw:
        return "(no output)"
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = _json.loads(line)
            if isinstance(data, dict) and "result" in data:
                return redact(data["result"].strip() or "(no output)")
        except (_json.JSONDecodeError, ValueError):
            pass
        break
    return redact(raw)


def append_result_to_task(store, task, result, section="Agent Output"):
    # type: (Any, Any, subprocess.CompletedProcess, str) -> None
    """Append the agent's output to the task record."""
    store.append_agent_result(task.id, result, section=section)


def _append_text_to_task(store, task, section, text):
    # type: (Any, Any, str, str) -> None
    from .sanitize import redact

    store.append_section(task.id, section, redact(text))


def _save_session_id(task_id, session_id, store=None):
    # type: (str, str, Optional[Any]) -> None
    """Persist the agent session_id into the task frontmatter for later resumption."""
    if store is None:
        try:
            from .web import store as _web_store

            store = _web_store
        except Exception:
            from .dynamo_store import DynamoTaskStore as _DS

            store = _DS()
    store.set_session_id(task_id, session_id)


def build_doc_prompt(task):
    # type: (...) -> str
    return (
        SECURITY_PREFIX + "Review the changes that were just made in this repository and update "
        "any relevant documentation (README, inline docstrings, doc files) to "
        "reflect them. If no documentation updates are needed, make no changes.\n\n"
        "Context — the changes were made for this task:\n"
        "Title: %s\n"
        "Description: %s\n"
        "Tags: %s"
    ) % (task.title, task.description or "(none)", ", ".join(task.tags) if task.tags else "(none)")


def run_doc_update(store, task, wt_path, timeout=TASK_TIMEOUT):
    # type: (Any, Any, str, int) -> Optional[subprocess.CompletedProcess]
    """Compound step: run agent to create/update docs in the worktree."""
    prompt = build_doc_prompt(task)
    try:
        result, elapsed, _, _usage = run_agent(
            prompt, cwd=wt_path, timeout=timeout, model=MODEL_FAST
        )
        append_result_to_task(store, task, result, section="Doc Update")
        log.info(
            "Doc update for task %s completed (exit %d, %.1fs)", task.id, result.returncode, elapsed
        )
        return result
    except subprocess.TimeoutExpired:
        log.warning("Doc update for task %s timed out after %ds", task.id, timeout)
        return None
    except Exception:
        log.exception("Doc update failed for task %s", task.id)
        return None
