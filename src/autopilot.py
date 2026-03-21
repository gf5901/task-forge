"""Daily autopilot plan proposal for projects with autopilot enabled."""

import json
import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .agent import MODEL_FULL, _extract_agent_text, run_agent
from .context_cli import write_ctx_script
from .pipeline_log import emit as plog
from .projects_dynamo import get_plan, get_project, list_plans, list_proposals, put_plan
from .roles import ROLES

log = logging.getLogger(__name__)

PLAN_TIMEOUT = 600

_ROLE_IDS = {r["id"] for r in ROLES}


def _parse_plan_json(stdout: str) -> Optional[Dict[str, Any]]:
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


def _quick_stats(
    prior_plans: List[Dict[str, Any]],
    all_project_tasks: List[Any],
    all_proposals: List[Dict[str, Any]],
) -> str:
    lines = []
    if prior_plans:
        latest = prior_plans[0]
        ld = latest.get("plan_date") or (latest.get("sk") or "").replace("PLAN#", "", 1)
        ls = latest.get("status", "?")
        lines.append("- Prior plans: %d total (latest: %s [%s])" % (len(prior_plans), ld, ls))
    else:
        lines.append("- Prior plans: none yet")

    active = [
        t
        for t in all_project_tasks
        if getattr(t, "status", None) and t.status.value in ("pending", "in_progress", "in_review")
    ]
    if active:
        counts = {}  # type: Dict[str, int]
        for t in active:
            sv = t.status.value
            counts[sv] = counts.get(sv, 0) + 1
        parts = ["%s %d" % (k, v) for k, v in sorted(counts.items())]
        lines.append("- Active tasks: %d (%s)" % (len(active), ", ".join(parts)))
    else:
        lines.append("- Active tasks: none")

    approved_active = 0
    for pr in all_proposals:
        if pr.get("status") != "approved":
            continue
        tid = pr.get("task_id")
        if not tid:
            approved_active += 1
            continue
        for t in all_project_tasks:
            if getattr(t, "id", None) == str(tid):
                sv = getattr(t, "status", None)
                if sv and sv.value in ("pending", "in_progress"):
                    approved_active += 1
                break
    lines.append("- Approved proposals with active tasks: %d" % approved_active)

    human = [
        t
        for t in all_project_tasks
        if getattr(t, "assignee", "agent") == "human"
        and getattr(t, "status", None)
        and t.status.value not in ("completed", "cancelled")
    ]
    lines.append("- Human tasks (non-terminal): %d" % len(human))

    return "\n".join(lines)


AUTOPILOT_LEAN_PROMPT = """\
You are the daily planner for the project "{title}".
Target repo: {target_repo}

## Quick stats
{quick_stats}

## Available tools
Load context on demand (only load what you need). From this directory, run:
  ./ctx spec                          # Full project spec (markdown)
  ./ctx plans                         # Recent daily plans with outcomes
  ./ctx tasks --recent 20 --status completed  # Recent finished work
  ./ctx tasks --assignee human        # All human-assigned tasks
  ./ctx proposals --status approved   # Approved proposals (may have active tasks)
  ./ctx proposals --status pending    # Pending proposals

## Valid role ids for plan items (use exactly one per item, or empty string)
{role_list}

## Your task
Start by loading the context you need (at minimum: ./ctx spec and ./ctx plans).

Propose 3–6 concrete work items for today. Each item must be specific enough for a coding agent \
to execute in one session (clear scope, files or areas to touch). If nothing should run today \
(blocked on human, waiting on merges, etc.), return an empty "items" array and explain in \
"reflection".

Respond ONLY with valid JSON (no markdown fences, no extra text):
{{
  "reflection": "why this focus today, or why nothing to run",
  "items": [
    {{
      "title": "short imperative title",
      "description": "detailed instructions for the agent",
      "role": "one of the valid role ids or empty string",
      "priority": "low|medium|high|urgent"
    }}
  ]
}}
"""


def _normalize_items(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        if not isinstance(it, dict) or not str(it.get("title", "")).strip():
            continue
        pr = str(it.get("priority", "medium"))
        if pr not in ("low", "medium", "high", "urgent"):
            pr = "medium"
        role = str(it.get("role", "")).strip()
        if role and role not in _ROLE_IDS:
            role = "fullstack_engineer"
        out.append(
            {
                "title": str(it["title"]).strip()[:500],
                "description": str(it.get("description", "")).strip(),
                "role": role,
                "priority": pr,
            }
        )
    return out


def propose_daily_plan(
    store: Any,
    project_id: str,
    regenerate: bool = False,
) -> bool:
    """Propose PLAN#<today> for an autopilot project. Returns True if a plan row was written or skip is OK."""
    today = datetime.now(timezone.utc).date().isoformat()
    log.info(
        "autopilot propose_daily_plan project=%s today=%s regenerate=%s",
        project_id,
        today,
        regenerate,
    )
    plog(project_id, "autopilot_plan_start", "autopilot", "Proposing daily plan")

    proj = get_project(project_id)
    if not proj:
        log.error("autopilot: project %s not found", project_id)
        return False
    if not proj.get("autopilot"):
        log.info("autopilot: project %s has autopilot disabled — skip", project_id)
        return True
    if proj.get("proj_status") != "active":
        log.info("autopilot: project %s not active — skip", project_id)
        return True

    awaiting = bool(proj.get("awaiting_next_directive"))
    active_sk = str(proj.get("active_directive_sk") or "")
    if not awaiting and active_sk.startswith("DIR#"):
        log.info("autopilot: directive batch in progress — skip planning")
        plog(project_id, "autopilot_plan_skip", "autopilot", "Directive active")
        return True

    existing = get_plan(project_id, today)
    if existing and not regenerate:
        log.info("autopilot: plan for %s already exists — skip", today)
        plog(project_id, "autopilot_plan_skip", "autopilot", "Plan exists for today")
        return True
    if regenerate and (not existing or existing.get("status") != "proposed"):
        log.warning("autopilot: regenerate requires proposed plan for today")
        return False

    prior = list_plans(project_id, limit=20)
    for p in prior:
        pd = p.get("plan_date") or (p.get("sk") or "").replace("PLAN#", "", 1)
        if p.get("status") == "proposed" and pd < today:
            log.warning(
                "autopilot: stale proposed plan %s — skip new proposal until human acts",
                pd,
            )
            plog(
                project_id,
                "autopilot_plan_skip",
                "autopilot",
                "Stale proposed plan %s" % pd,
            )
            return True

    title = str(proj.get("title", "Project")).strip()
    target_repo = str(proj.get("target_repo", "")).strip()
    role_list = ", ".join(sorted(_ROLE_IDS))

    all_proposals = list_proposals(project_id, limit=50)
    all_project_tasks = []  # type: List[Any]
    try:
        all_project_tasks = store.list_tasks_for_project(project_id)
    except Exception:
        log.warning("Could not load tasks for project %s", project_id, exc_info=True)

    quick_stats = _quick_stats(prior, all_project_tasks, all_proposals)

    prompt = AUTOPILOT_LEAN_PROMPT.format(
        title=title,
        target_repo=target_repo or "(not set)",
        quick_stats=quick_stats,
        role_list=role_list,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="autopilot-%s-" % project_id) as tmp:
            write_ctx_script(tmp, project_id)
            result, _elapsed, _, _usage = run_agent(
                prompt,
                cwd=tmp,
                timeout=PLAN_TIMEOUT,
                model=MODEL_FULL or None,
                task_id=project_id,
            )
    except subprocess.TimeoutExpired:
        log.warning("autopilot: timed out for project %s", project_id)
        plog(project_id, "autopilot_plan_timeout", "autopilot", "Planner timed out")
        return False
    except Exception:
        log.exception("autopilot: agent error for project %s", project_id)
        plog(project_id, "autopilot_plan_error", "autopilot", "Agent error")
        return False

    if result.returncode != 0 or not result.stdout.strip():
        log.warning("autopilot: bad agent exit for project %s", project_id)
        plog(project_id, "autopilot_plan_empty", "autopilot", "No agent output")
        return False

    agent_text = _extract_agent_text(result.stdout)
    parsed = _parse_plan_json(agent_text)
    if not parsed:
        log.warning("autopilot: could not parse JSON for project %s", project_id)
        plog(project_id, "autopilot_plan_parse_error", "autopilot", "JSON parse failed")
        return False

    reflection = str(parsed.get("reflection", "")).strip()
    items = _normalize_items(parsed.get("items"))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not items:
        created = now
        if existing and existing.get("created_at"):
            created = existing["created_at"]
        put_plan(
            project_id,
            today,
            {
                "status": "completed",
                "reflection": reflection or "No work items for today.",
                "items": [],
                "task_ids": [],
                "created_at": created,
                "completed_at": now,
                "outcome_summary": {"completed": 0, "in_review": 0, "failed": 0, "cancelled": 0},
            },
        )
        log.info("autopilot: empty plan stored as completed for %s", today)
        plog(project_id, "autopilot_plan_done", "autopilot", "Empty plan (completed)")
        return True

    put_plan(
        project_id,
        today,
        {
            "status": "proposed",
            "reflection": reflection,
            "items": items,
            "task_ids": [],
            "human_notes": "",
        },
    )
    log.info("autopilot: proposed plan with %d item(s) for %s", len(items), today)
    plog(
        project_id,
        "autopilot_plan_done",
        "autopilot",
        "Proposed %d item(s)" % len(items),
    )
    return True
