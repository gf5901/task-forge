"""Project-level PM agent: replies in project chat with ./ctx and optional task creation."""

import json
import logging
import os
import subprocess
import tempfile
from typing import Any, Dict, Optional, Set

from .agent import MODEL_FULL, SECURITY_PREFIX, _extract_agent_text, run_agent
from .context_cli import write_ctx_script
from .pipeline_log import emit as plog
from .projects_dynamo import (
    add_chat_message,
    claim_project_pm_reply,
    get_project,
    list_chat_messages,
)
from .roles import ROLES

log = logging.getLogger(__name__)

PM_REPLY_TIMEOUT = int(os.getenv("PM_REPLY_TIMEOUT", "600"))

_ROLE_IDS: Set[str] = {r["id"] for r in ROLES}


def _parse_pm_json(stdout: str) -> Optional[Dict[str, Any]]:
    text = stdout.strip()
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("{"):
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    continue
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _normalize_agent_task(it: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(it, dict):
        return None
    title = str(it.get("title", "")).strip()
    if not title:
        return None
    pr = str(it.get("priority", "medium"))
    if pr not in ("low", "medium", "high", "urgent"):
        pr = "medium"
    role = str(it.get("role", "")).strip()
    if role and role not in _ROLE_IDS:
        role = "fullstack_engineer"
    return {
        "title": title[:500],
        "description": str(it.get("description", "")).strip(),
        "role": role,
        "priority": pr,
    }


def _normalize_human_task(it: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(it, dict):
        return None
    title = str(it.get("title", "")).strip()
    if not title:
        return None
    pr = str(it.get("priority", "medium"))
    if pr not in ("low", "medium", "high", "urgent"):
        pr = "medium"
    return {
        "title": title[:500],
        "description": str(it.get("description", "")).strip(),
        "priority": pr,
    }


def _build_role_list() -> str:
    return "\n".join('  "%s" — %s' % (r["id"], r["label"]) for r in ROLES)


PM_AGENT_PROMPT = (
    SECURITY_PREFIX
    + """\
You are the project manager for "{title}".
Target repo: {target_repo}

## Recent chat (oldest first)
{chat_block}

## Available tools
Load context on demand from this directory:
  ./ctx spec                          # Full project spec (markdown)
  ./ctx plans                         # Recent autopilot plans
  ./ctx tasks --recent 30             # Recent tasks (add filters as needed)
  ./ctx tasks --assignee human        # Human-assigned tasks
  ./ctx proposals --status pending    # Pending KPI proposals
  ./ctx proposals --status approved   # Approved proposals
  ./ctx kpis                          # KPI definitions (if any)
  ./ctx snapshots                     # Metric snapshots (if any)

## Valid role ids for agent tasks (use exactly one per item, or empty string)
{role_list}

## Your task
Respond to the human's latest message in the chat. Use ./ctx when you need facts you do not \
already have. You may create work by returning structured fields in your JSON response.

Respond ONLY with valid JSON (no markdown fences, no extra text):
{{
  "reply": "markdown message to the human (required)",
  "create_agent_tasks": [
    {{
      "title": "short imperative title",
      "description": "instructions for the coding agent",
      "role": "role id or empty string",
      "priority": "low|medium|high|urgent"
    }}
  ],
  "create_human_tasks": [
    {{
      "title": "what you need from the human",
      "description": "clear acceptance criteria",
      "priority": "low|medium|high|urgent"
    }}
  ]
}}
Use empty arrays [] if you are not creating tasks. The "reply" field is always required.
"""
)


def run_pm_reply(store: Any, project_id: str) -> bool:
    """Handle one PM chat turn: claim, run agent in ctx dir, parse JSON, create tasks, post reply."""
    proj = get_project(project_id)
    if not proj:
        log.warning("pm_reply: project %s not found", project_id)
        return False
    if not proj.get("reply_pending"):
        log.info("pm_reply: reply_pending false for project %s — skip", project_id)
        return False

    if not claim_project_pm_reply(project_id):
        log.info("pm_reply: lost claim race for project %s", project_id)
        return False

    proj = get_project(project_id)
    if not proj:
        return False

    title = str(proj.get("title", "Project"))
    target_repo = str(proj.get("target_repo", "") or "(not set)").strip() or "(not set)"
    messages = list_chat_messages(project_id, limit=30)
    if not messages:
        log.info("pm_reply: no chat messages for project %s", project_id)
        add_chat_message(
            project_id,
            "pm-agent",
            "I did not find a message to respond to.",
        )
        return True

    lines = []
    for m in messages:
        author = str(m.get("author", ""))
        body = str(m.get("body", "")).strip()
        ts = str(m.get("created_at", ""))
        lines.append("- (%s) **%s**\n  %s" % (ts, author, body))
    chat_block = "\n".join(lines) if lines else "(no messages)"

    prompt = PM_AGENT_PROMPT.format(
        title=title,
        target_repo=target_repo,
        chat_block=chat_block,
        role_list=_build_role_list(),
    )

    plog(project_id, "pm_reply_start", "pm_agent", "PM reply")

    try:
        with tempfile.TemporaryDirectory(prefix="pm-reply-%s-" % project_id) as tmp:
            write_ctx_script(tmp, project_id)
            result, elapsed, _, usage = run_agent(
                prompt,
                cwd=tmp,
                timeout=PM_REPLY_TIMEOUT,
                model=MODEL_FULL or None,
                task_id=project_id,
            )
    except subprocess.TimeoutExpired:
        log.error("pm_reply: timeout for project %s", project_id)
        plog(project_id, "pm_reply_timeout", "pm_agent", "Timed out")
        add_chat_message(
            project_id,
            "pm-agent",
            "Sorry — I timed out while preparing a response. Please try again.",
        )
        return False
    except Exception:
        log.exception("pm_reply: error for project %s", project_id)
        plog(project_id, "pm_reply_error", "pm_agent", "Error")
        add_chat_message(
            project_id,
            "pm-agent",
            "Sorry — I hit an error while responding. Please try again.",
        )
        return False

    if result.returncode != 0 or not result.stdout.strip():
        log.warning("pm_reply: bad agent exit for project %s", project_id)
        plog(project_id, "pm_reply_failed", "pm_agent", "Bad exit")
        add_chat_message(
            project_id,
            "pm-agent",
            "Sorry — I could not produce a response (agent error). Please try again.",
        )
        return False

    agent_text = _extract_agent_text(result.stdout)
    parsed = _parse_pm_json(agent_text)

    reply_body = ""
    extras = []  # type: List[str]

    if parsed and isinstance(parsed.get("reply"), str):
        reply_body = str(parsed["reply"]).strip()
        agent_tasks_raw = parsed.get("create_agent_tasks", [])
        human_tasks_raw = parsed.get("create_human_tasks", [])
        if not isinstance(agent_tasks_raw, list):
            agent_tasks_raw = []
        if not isinstance(human_tasks_raw, list):
            human_tasks_raw = []

        for it in agent_tasks_raw:
            norm = _normalize_agent_task(it)
            if not norm:
                continue
            try:
                store.create(
                    title=norm["title"][:200],
                    description=norm["description"],
                    priority=norm["priority"],
                    created_by="pm-agent",
                    target_repo=str(proj.get("target_repo", "") or "").strip(),
                    project_id=project_id,
                    role=norm["role"],
                )
                extras.append("Created agent task: **%s**" % norm["title"][:80])
            except Exception:
                log.warning("pm_reply: could not create agent task", exc_info=True)

        for it in human_tasks_raw:
            norm = _normalize_human_task(it)
            if not norm:
                continue
            try:
                store.create(
                    title=norm["title"][:200],
                    description=norm["description"],
                    priority=norm["priority"],
                    created_by="pm-agent",
                    target_repo=str(proj.get("target_repo", "") or "").strip(),
                    project_id=project_id,
                    assignee="human",
                    tags=["pm-request"],
                )
                extras.append("Assigned you a task: **%s**" % norm["title"][:80])
            except Exception:
                log.warning("pm_reply: could not create human task", exc_info=True)
    else:
        reply_body = agent_text.strip()

    if not reply_body:
        reply_body = "I processed your message but could not format a reply."

    if extras:
        reply_body += "\n\n---\n" + "\n".join(extras)

    add_chat_message(project_id, "pm-agent", reply_body)
    plog(
        project_id,
        "pm_reply_done",
        "pm_agent",
        "PM reply posted",
        runtime=elapsed,
        **usage,
    )
    log.info("pm_reply for project %s completed (%.1fs)", project_id, elapsed)
    return True
