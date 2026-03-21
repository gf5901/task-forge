"""Autopilot plan proposal: daily (human approve) or continuous (auto-approve, hourly)."""

import json
import logging
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from .agent import MODEL_FULL, _extract_agent_text, run_agent
from .context_cli import write_ctx_script
from .pipeline_log import emit as plog
from .projects_dynamo import (
    get_plan,
    get_project,
    list_plans,
    list_proposals,
    new_plan_suffix_utc,
    plan_date_from_suffix,
    plan_sk,
    put_plan,
    update_plan_fields,
    update_project,
)
from .roles import ROLES
from .task_store import TaskStatus

log = logging.getLogger(__name__)

PLAN_TIMEOUT = 600

_ROLE_IDS = {r["id"] for r in ROLES}

_TERMINAL: Set[str] = {
    TaskStatus.COMPLETED.value,
    TaskStatus.IN_REVIEW.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.FAILED.value,
}


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


def _parse_iso_datetime(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _post_discord_embed(title: str, description: str) -> None:
    url = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        return
    payload = json.dumps(
        {
            "embeds": [
                {
                    "title": title[:256],
                    "description": description[:2000],
                    "color": 5793266,
                }
            ]
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, OSError):
        log.warning("autopilot: discord webhook post failed", exc_info=True)


def _pause_cycle(
    project_id: str,
    proj: Dict[str, Any],
    reason: str,
    discord_body: str,
) -> None:
    update_project(
        project_id,
        {
            "cycle_paused": True,
            "cycle_pause_reason": reason,
        },
    )
    title = str(proj.get("title", "Project"))
    _post_discord_embed("Autopilot paused: %s" % title, discord_body)


def _failure_streak_exceeds(recent_plans: List[Dict[str, Any]], streak: int = 3) -> bool:
    completed = [
        p
        for p in recent_plans
        if p.get("status") == "completed" and isinstance(p.get("outcome_summary"), dict)
    ]
    if len(completed) < streak:
        return False
    for p in completed[:streak]:
        os_ = p.get("outcome_summary") or {}
        failed = int(os_.get("failed", 0) or 0)
        total = sum(int(os_.get(k, 0) or 0) for k in ("completed", "in_review", "failed", "cancelled"))
        if total == 0 or failed / float(total) <= 0.5:
            return False
    return True


def _fully_blocked_on_human(all_tasks: List[Any]) -> bool:
    non_term = [
        t
        for t in all_tasks
        if getattr(t, "status", None) and getattr(t.status, "value", "") not in _TERMINAL
    ]
    if not non_term:
        return False
    return all(getattr(t, "assignee", "agent") == "human" for t in non_term)


def _approved_batch_active(
    plan_rec: Dict[str, Any],
    all_tasks: List[Any],
) -> bool:
    sk = str(plan_rec.get("sk") or "")
    if plan_rec.get("status") != "approved" or not sk.startswith("PLAN#"):
        return False
    batch = [t for t in all_tasks if getattr(t, "directive_sk", "") == sk]
    if not batch:
        return False
    for t in batch:
        st = getattr(t, "status", None)
        if st and st.value in ("pending", "in_progress"):
            return True
    return False


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


AUTOPILOT_DAILY_PROMPT = """\
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

AUTOPILOT_CONTINUOUS_PROMPT = """\
You are the continuous planner for the project "{title}".
Target repo: {target_repo}
Cycle wall-clock: ~{cycle_elapsed_hours:.1f}h elapsed of {cycle_max_hours}h max (UTC).

## Quick stats
{quick_stats}

{feedback_block}

## Available tools
Load context on demand (only load what you need). From this directory, run:
  ./ctx spec                          # Full project spec (markdown)
  ./ctx plans                         # Recent plans with outcomes
  ./ctx tasks --recent 20 --status completed  # Recent finished work
  ./ctx tasks --assignee human        # All human-assigned tasks
  ./ctx proposals --status approved   # Approved proposals (may have active tasks)
  ./ctx proposals --status pending    # Pending proposals (awaiting human approval)

## Valid role ids for plan items (use exactly one per item, or empty string)
{role_list}

## Your task
Assess whether new autonomous work is needed **right now**. Consider:
- Tasks still running (pending/in_progress)? Prefer waiting — empty items and next_check_hours.
- Outcomes of the last plan batch; do not duplicate work already in flight or in review.
- Pending KPI proposals: do not re-propose the same work; align with approved proposal tasks.
- Human-assigned tasks blocking progress? You may still propose parallel agent work if safe.

If new work is needed, propose 3–6 concrete items. If not, return an empty "items" array and set \
"next_check_hours" (integer 1–8) for when to reconsider.

Respond ONLY with valid JSON (no markdown fences, no extra text):
{{
  "reflection": "why this focus now, or why nothing to run",
  "items": [
    {{
      "title": "short imperative title",
      "description": "detailed instructions for the agent",
      "role": "one of the valid role ids or empty string",
      "priority": "low|medium|high|urgent"
    }}
  ],
  "next_check_hours": 0
}}
Use next_check_hours 0 when items is non-empty. When items is empty, next_check_hours must be 1–8.
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


def _auto_approve_plan(
    store: Any,
    project_id: str,
    proj: Dict[str, Any],
    plan_suffix: str,
    items: List[Dict[str, Any]],
) -> None:
    """Create tasks for each item; mark plan approved; set project active batch."""
    target_repo = str(proj.get("target_repo", "")).strip()
    directive_date = plan_date_from_suffix(plan_suffix)
    sk_val = plan_sk(plan_suffix)
    task_ids = []  # type: List[str]
    for it in items:
        t = store.create(
            title=str(it["title"])[:200],
            description=str(it.get("description", "")),
            priority=str(it.get("priority", "medium")),
            created_by="autopilot-plan",
            target_repo=target_repo,
            project_id=project_id,
            directive_sk=sk_val,
            directive_date=directive_date,
            role=str(it.get("role", "")).strip() or "",
        )
        task_ids.append(t.id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    update_plan_fields(
        project_id,
        plan_suffix,
        {
            "status": "approved",
            "task_ids": task_ids,
            "approved_at": now,
            "human_notes": "",
        },
    )
    update_project(
        project_id,
        {
            "active_directive_sk": sk_val,
            "awaiting_next_directive": False,
            "next_check_at": "",
        },
    )


def propose_daily_plan(
    store: Any,
    project_id: str,
    regenerate: bool = False,
    plan_suffix: Optional[str] = None,
) -> bool:
    """Propose a plan for an autopilot project. Returns True if OK or intentional skip."""
    today = datetime.now(timezone.utc).date().isoformat()
    log.info(
        "autopilot propose_daily_plan project=%s today=%s regenerate=%s plan_suffix=%s",
        project_id,
        today,
        regenerate,
        plan_suffix,
    )
    plog(project_id, "autopilot_plan_start", "autopilot", "Proposing plan")

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

    mode = str(proj.get("autopilot_mode") or "daily").strip()
    if mode not in ("daily", "continuous"):
        mode = "daily"

    if mode == "daily":
        if datetime.now(timezone.utc).hour != 7 and not regenerate:
            log.info("autopilot: daily mode — skip (not 07:00 UTC)")
            return True
    else:
        if not str(proj.get("cycle_started_at") or "").strip():
            log.info("autopilot: continuous — no active cycle (cycle_started_at empty)")
            return True
        if proj.get("cycle_paused"):
            log.info("autopilot: continuous — cycle paused for review")
            return True
        started = _parse_iso_datetime(proj.get("cycle_started_at"))
        max_h = int(proj.get("cycle_max_hours") or 24)
        if max_h < 1:
            max_h = 24
        if started:
            elapsed_h = (datetime.now(timezone.utc) - started).total_seconds() / 3600.0
            if elapsed_h >= float(max_h):
                title = str(proj.get("title", "Project"))
                _pause_cycle(
                    project_id,
                    proj,
                    "time_expired",
                    "%dh window complete for **%s**. Open Task Forge to review and start the next cycle."
                    % (max_h, title),
                )
                plog(project_id, "autopilot_plan_skip", "autopilot", "Cycle time expired")
                return True
        nc = _parse_iso_datetime(proj.get("next_check_at"))
        if nc and nc > datetime.now(timezone.utc) and not regenerate:
            log.info("autopilot: next_check_at in future — skip")
            return True

    awaiting = bool(proj.get("awaiting_next_directive"))
    active_sk = str(proj.get("active_directive_sk") or "")
    if not awaiting and active_sk.startswith("DIR#"):
        log.info("autopilot: directive batch in progress — skip planning")
        plog(project_id, "autopilot_plan_skip", "autopilot", "Directive active")
        return True

    regenerate_target = None  # type: Optional[str]
    if regenerate:
        if mode == "continuous":
            if not (plan_suffix and str(plan_suffix).strip()):
                log.warning("autopilot: continuous regenerate requires plan_suffix")
                return False
            regenerate_target = str(plan_suffix).strip()
        else:
            regenerate_target = str(plan_suffix).strip() if plan_suffix else today
        rp = get_plan(project_id, regenerate_target)
        if not rp or rp.get("status") != "proposed":
            log.warning(
                "autopilot: regenerate requires proposed plan (suffix=%s)",
                regenerate_target,
            )
            return False

    prior = list_plans(project_id, limit=30)
    all_project_tasks = []  # type: List[Any]
    try:
        all_project_tasks = store.list_tasks_for_project(project_id)
    except Exception:
        log.warning("Could not load tasks for project %s", project_id, exc_info=True)

    if mode == "continuous" and not regenerate:
        if _failure_streak_exceeds(prior, 3):
            title = str(proj.get("title", "Project"))
            _pause_cycle(
                project_id,
                proj,
                "failures",
                "**%s** — recent plans had high failure rates. Review tasks and restart the cycle in the app."
                % title,
            )
            plog(project_id, "autopilot_plan_skip", "autopilot", "Failure circuit breaker")
            return True
        if (
            prior
            and prior[0].get("status") == "proposed"
        ):
            raw_items = prior[0].get("items") or []
            items_rec = _normalize_items(raw_items)
            if items_rec:
                suf = (prior[0].get("sk") or "").replace("PLAN#", "", 1)
                log.info("autopilot: recovering stranded proposed plan %s", suf)
                _auto_approve_plan(store, project_id, proj, suf, items_rec)
                plog(project_id, "autopilot_plan_done", "autopilot", "Recovered auto-approve")
                return True
        if prior and _approved_batch_active(prior[0], all_project_tasks) and not regenerate:
            log.info("autopilot: approved batch still has active tasks — skip")
            plog(project_id, "autopilot_plan_skip", "autopilot", "Batch in flight")
            return True
        if _fully_blocked_on_human(all_project_tasks) and not regenerate:
            title = str(proj.get("title", "Project"))
            _pause_cycle(
                project_id,
                proj,
                "blocked",
                "**%s** — all remaining work is on human-assigned tasks. Complete or unblock them to continue."
                % title,
            )
            plog(project_id, "autopilot_plan_skip", "autopilot", "Blocked on human tasks")
            return True
    else:
        existing = get_plan(project_id, today)
        if existing and not regenerate:
            log.info("autopilot: plan for %s already exists — skip", today)
            plog(project_id, "autopilot_plan_skip", "autopilot", "Plan exists for today")
            return True

        for p in prior:
            pd = str(p.get("plan_date") or (p.get("sk") or "").replace("PLAN#", "", 1))
            p_date = pd[:10] if len(pd) >= 10 else pd
            if p.get("status") == "proposed" and p_date < today:
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
    quick_stats = _quick_stats(prior, all_project_tasks, all_proposals)

    feedback = str(proj.get("cycle_feedback") or "").strip()
    feedback_block = ""
    if feedback:
        feedback_block = "## Human feedback from last review\n%s\n\n" % feedback

    if mode == "continuous":
        started = _parse_iso_datetime(proj.get("cycle_started_at"))
        max_h = float(int(proj.get("cycle_max_hours") or 24))
        elapsed_h = 0.0
        if started:
            elapsed_h = (datetime.now(timezone.utc) - started).total_seconds() / 3600.0
        prompt = AUTOPILOT_CONTINUOUS_PROMPT.format(
            title=title,
            target_repo=target_repo or "(not set)",
            cycle_elapsed_hours=elapsed_h,
            cycle_max_hours=int(max_h),
            quick_stats=quick_stats,
            feedback_block=feedback_block,
            role_list=role_list,
        )
        update_project(project_id, {"next_check_at": ""})
    else:
        prompt = AUTOPILOT_DAILY_PROMPT.format(
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
    next_check_raw = parsed.get("next_check_hours", 0)
    try:
        next_check_hours = int(float(next_check_raw))
    except (TypeError, ValueError):
        next_check_hours = 0

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if mode == "continuous" and feedback:
        update_project(project_id, {"cycle_feedback": ""})

    if not items:
        if mode == "continuous":
            nh = next_check_hours if 1 <= next_check_hours <= 8 else 1
            next_at = (datetime.now(timezone.utc) + timedelta(hours=nh)).isoformat(
                timespec="seconds"
            )
            update_project(project_id, {"next_check_at": next_at})
            log.info("autopilot: empty plan; next_check in %dh", nh)
            plog(
                project_id,
                "autopilot_plan_done",
                "autopilot",
                "No items; next check in %dh" % nh,
            )
            return True

        existing_daily = get_plan(project_id, today)
        created = now
        if existing_daily and existing_daily.get("created_at"):
            created = existing_daily["created_at"]
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

    if regenerate and regenerate_target is not None:
        new_suffix = regenerate_target
    elif mode == "continuous":
        new_suffix = new_plan_suffix_utc()
    else:
        new_suffix = today

    put_plan(
        project_id,
        new_suffix,
        {
            "status": "proposed",
            "reflection": reflection,
            "items": items,
            "task_ids": [],
            "human_notes": "",
        },
    )
    log.info("autopilot: proposed plan with %d item(s) sk=PLAN#%s", len(items), new_suffix)
    plog(
        project_id,
        "autopilot_plan_done",
        "autopilot",
        "Proposed %d item(s)" % len(items),
    )

    if mode == "continuous":
        _auto_approve_plan(store, project_id, proj, new_suffix, items)

    return True
