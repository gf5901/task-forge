"""Daily observe → reflect → propose cycle for autonomous objectives."""

import json
import logging
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .agent import MODEL_FULL, _extract_agent_text, run_agent
from .context_cli import write_ctx_script
from .pipeline_log import emit as plog
from .projects_dynamo import (
    get_project,
    list_memories,
    list_proposals,
    put_proposal,
    update_proposal_status,
    update_snapshot_reflection,
)
from .task_store import TaskStatus
from .worktree import (
    create_cycle_read_worktree,
    remove_cycle_worktree,
    resolve_target_repo_path,
)

log = logging.getLogger(__name__)

REFLECTION_TIMEOUT = 900

REFLECTION_JSON_SCHEMA = """{
  "reflection": "...",
  "proposals": [
    {"action": "...", "rationale": "...", "domain": "...", "target_kpi": "..."}
  ],
  "kpi_suggestions": [
    {"action": "add|modify|remove", "kpi": {...}, "reason": "..."}
  ],
  "human_tasks": [
    {"title": "...", "description": "...", "priority": "medium"}
  ],
  "human_task_reviews": [
    {"task_id": "...", "approved": true, "comment": "..."}
  ]
}"""

REFLECTION_LEAN_PROMPT = """\
You are the strategic advisor for the project "{title}".
Target repo (label): {target_repo_label}

## Quick stats
{quick_stats}

## Recent memories (from prior cycles)
{memories_block}

## Available tools
Load context on demand (only load what you need). From this directory, run:
  ./ctx spec                          # Full project spec (markdown)
  ./ctx kpis                          # KPI definitions and targets
  ./ctx snapshots --days 14           # Daily metric readings
  ./ctx tasks --recent 20 --status completed  # Recent finished work
  ./ctx proposals --status pending    # Awaiting human approval
  ./ctx proposals                     # Broader proposal history (may be long)
  ./ctx human-tasks                   # Human-assigned tasks in in_review
  ./ctx memory list                   # Your past insights (note full sk for get)
  ./ctx memory get MEMORY#...         # Or a unique suffix (8+ chars) from list
  ./ctx memory save "short insight"   # Persist for next cycle (max 5 saves per run)

{repo_section}## Your task
Start by loading the context you need (at minimum: ./ctx kpis, ./ctx snapshots, and \
./ctx proposals --status pending). Use ./ctx human-tasks before filling human_task_reviews.

1. **Reflect**: What's working? What isn't? What anomalies or trends do you see in the metrics?

2. **Review pending proposals**: If any pending proposals should be reprioritized or amended \
given new data, say so. Do NOT re-propose items that are already pending — suggest new actions \
or explain priority changes.

3. **Propose 3-7 NEW concrete actions** for the next cycle, each with:
   - action: specific enough to become a task description for a coding agent
   - rationale: why this will move a specific KPI
   - domain: one of "code", "content", "seo", "outreach", "research"
   - target_kpi: which KPI id this aims to improve

4. **Review KPIs**: After reading ./ctx kpis, decide if metrics still fit. Suggest changes in \
"kpi_suggestions" only when warranted (add/modify/remove with full kpi object for add/modify, \
{{"id": "..."}} for remove). Do not churn KPIs every cycle.

5. **Human operator needs**: API keys, access, decisions — each as human_tasks with title, \
description, priority (low|medium|high|urgent).

6. **Human tasks in review**: Use ./ctx human-tasks. For each, approve or send back with a comment.

Note: GA4 and Search Console data may lag 24-48 hours. Reason about weekly trends. If you \
loaded ./repo/, treat it as read-only reference — do not modify files there.

Respond ONLY with valid JSON (no markdown fences, no extra text):
"""


def _coerce_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _classify_kpis(kpis: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """Return (on_track, behind, no_data) counts."""
    on_track = 0
    behind = 0
    no_data = 0
    for k in kpis:
        direction = str(k.get("direction", "up"))
        cur = _coerce_float(k.get("current"))
        tgt = _coerce_float(k.get("target"))
        # target 0: treat as "no numeric goal" — not behind/ahead, just informational
        if tgt is None or tgt == 0:
            if cur is None:
                no_data += 1
            else:
                on_track += 1
            continue
        if cur is None:
            no_data += 1
            continue
        if direction == "up":
            if cur >= tgt * 0.95:
                on_track += 1
            else:
                behind += 1
        elif direction == "down":
            if cur <= tgt * 1.05:
                on_track += 1
            else:
                behind += 1
        else:
            band = max(abs(tgt) * 0.1, 1.0)
            if abs(cur - tgt) <= band:
                on_track += 1
            else:
                behind += 1
    return on_track, behind, no_data


def _quick_stats_text(
    kpis: List[Dict[str, Any]],
    all_proposals: List[Dict[str, Any]],
    all_project_tasks: List[Any],
    human_review_n: int,
) -> str:
    lines = []
    pending_props = len([p for p in all_proposals if p.get("status") == "pending"])
    if not kpis:
        lines.append("- KPIs: none defined (use ./ctx kpis; consider human_tasks to add metrics)")
    else:
        ot, bh, nd = _classify_kpis(kpis)
        lines.append(
            "- KPIs: %d defined — ~%d on track, ~%d behind/misaligned, ~%d low/no data"
            % (len(kpis), ot, bh, nd)
        )
    lines.append("- Pending proposals: %d" % pending_props)
    active = [
        t
        for t in all_project_tasks
        if getattr(t, "status", None) and t.status.value in ("pending", "in_progress", "in_review")
    ]
    if not active:
        lines.append("- Active tasks: none")
    else:
        counts = {}  # type: Dict[str, int]
        for t in active:
            sv = t.status.value
            counts[sv] = counts.get(sv, 0) + 1
        parts = ["%s %d" % (k, v) for k, v in sorted(counts.items())]
        lines.append("- Active tasks: %d (%s)" % (len(active), ", ".join(parts)))
    lines.append("- Human tasks in review: %d (see ./ctx human-tasks)" % human_review_n)
    return "\n".join(lines)


def _memories_block_text(project_id: str, limit: int = 2) -> str:
    try:
        items = list_memories(project_id, limit=limit)
    except Exception:
        return "(could not load memories)"
    if not items:
        return "(none yet — use ./ctx memory save to add durable notes)"
    lines = []
    for it in reversed(items):
        cd = it.get("cycle_date", "")
        body = (it.get("content") or "").replace("\n", " ").strip()
        if len(body) > 400:
            body = body[:400] + "…"
        lines.append("[%s] %s" % (cd, body))
    return "\n".join(lines)


def _parse_response(stdout: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from agent output, handling markdown fences."""
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


def _update_proposal_outcomes(store: Any, project_id: str, proposals: List[Dict[str, Any]]) -> None:
    """Check approved proposals with completed tasks and set their outcome."""
    for prop in proposals:
        if prop.get("status") != "approved":
            continue
        if prop.get("outcome"):
            continue
        task_id = prop.get("task_id")
        if not task_id:
            continue
        try:
            task = store.get(str(task_id))
            if not task:
                continue
            status_val = getattr(task, "status", None)
            if status_val and hasattr(status_val, "value"):
                status_val = status_val.value
            if status_val in ("completed", "in_review"):
                outcome = "Task completed (status: %s)" % status_val
                pr_url = getattr(task, "pr_url", None) or ""
                if pr_url:
                    outcome += " — PR: %s" % pr_url
                update_proposal_status(project_id, prop["sk"], "approved", outcome=outcome)
            elif status_val in ("failed", "cancelled"):
                outcome = "Task %s" % status_val
                update_proposal_status(project_id, prop["sk"], "approved", outcome=outcome)
        except Exception:
            log.warning("Could not update outcome for proposal %s", prop.get("sk"), exc_info=True)


def run_daily_cycle(store: Any, project_id: str) -> bool:
    """Run the daily observe → reflect → propose cycle for a project."""
    log.info("Starting daily cycle for project %s", project_id)
    plog(project_id, "daily_cycle_start", "objectives", "Starting daily reflection cycle")

    proj = get_project(project_id)
    if not proj:
        log.error("daily_cycle: project %s not found", project_id)
        return False

    title = str(proj.get("title", "Project")).strip()
    target_repo = str(proj.get("target_repo", "")).strip()
    kpis = proj.get("kpis", [])
    if not isinstance(kpis, list):
        kpis = []

    # Gather context (stats for lean prompt; full detail via ./ctx)
    all_proposals = list_proposals(project_id, limit=50)

    # Update outcomes on approved proposals that have completed tasks
    _update_proposal_outcomes(store, project_id, all_proposals)

    all_project_tasks = []  # type: List[Any]
    human_tasks_in_review = []  # type: List[Any]
    try:
        all_project_tasks = store.list_tasks_for_project(project_id)
        human_tasks_in_review = [
            t
            for t in all_project_tasks
            if getattr(t, "assignee", "agent") == "human"
            and getattr(t, "status", None)
            and t.status.value == "in_review"
        ]
    except Exception:
        log.warning("Could not load recent tasks for project %s", project_id, exc_info=True)

    quick_stats = _quick_stats_text(
        kpis,
        all_proposals,
        all_project_tasks,
        len(human_tasks_in_review),
    )
    memories_block = _memories_block_text(project_id, limit=2)

    today = datetime.now(timezone.utc).date().isoformat()

    cycle_wt_path = None  # type: Optional[str]
    repo_dir_cleanup = None  # type: Optional[str]
    result = None  # type: Any
    elapsed = 0.0

    try:
        with tempfile.TemporaryDirectory(prefix="daily-cycle-%s-" % project_id) as cycle_dir:
            write_ctx_script(cycle_dir, project_id)
            repo_section = ""
            rpath = resolve_target_repo_path(target_repo)
            if rpath:
                wt = create_cycle_read_worktree(rpath, project_id)
                if wt:
                    cycle_wt_path = wt
                    repo_dir_cleanup = rpath
                    link = os.path.join(cycle_dir, "repo")
                    try:
                        os.symlink(wt, link, target_is_directory=True)
                        repo_section = (
                            "## Target repo (read-only)\n"
                            "Browse code at ./repo/ — do not modify files there.\n\n"
                        )
                    except OSError as exc:
                        log.warning("Could not symlink repo into cycle dir: %s", exc)

            prompt = REFLECTION_LEAN_PROMPT.format(
                title=title,
                target_repo_label=target_repo or "(none)",
                quick_stats=quick_stats,
                memories_block=memories_block,
                repo_section=repo_section,
            ) + "\n" + REFLECTION_JSON_SCHEMA
            result, elapsed, _, usage = run_agent(
                prompt,
                cwd=cycle_dir,
                timeout=REFLECTION_TIMEOUT,
                model=MODEL_FULL or None,
                task_id=project_id,
            )
    except subprocess.TimeoutExpired:
        log.warning("daily_cycle: timed out for project %s", project_id)
        plog(project_id, "daily_cycle_timeout", "objectives", "Reflection timed out")
        return False
    except Exception:
        log.exception("daily_cycle: agent error for project %s", project_id)
        plog(project_id, "daily_cycle_error", "objectives", "Agent error")
        return False
    finally:
        if cycle_wt_path and repo_dir_cleanup:
            remove_cycle_worktree(cycle_wt_path, repo_dir_cleanup)

    if result is None or result.returncode != 0 or not result.stdout.strip():
        log.warning("daily_cycle: agent returned non-zero or empty for project %s", project_id)
        plog(project_id, "daily_cycle_empty", "objectives", "No agent output")
        return False

    # Parse response
    agent_text = _extract_agent_text(result.stdout)
    parsed = _parse_response(agent_text)
    if not parsed:
        log.warning("daily_cycle: could not parse agent JSON for project %s", project_id)
        plog(
            project_id,
            "daily_cycle_parse_error",
            "objectives",
            "Could not parse JSON from agent output",
        )
        return False

    reflection = str(parsed.get("reflection", ""))
    proposals = parsed.get("proposals", [])
    human_tasks = parsed.get("human_tasks", [])
    human_task_reviews = parsed.get("human_task_reviews", [])
    kpi_suggestions = parsed.get("kpi_suggestions", [])

    if not isinstance(proposals, list):
        proposals = []
    if not isinstance(human_tasks, list):
        human_tasks = []
    if not isinstance(human_task_reviews, list):
        human_task_reviews = []
    if not isinstance(kpi_suggestions, list):
        kpi_suggestions = []

    log.info(
        "daily_cycle: project %s — %d proposals, %d human tasks, %d reviews, %d kpi suggestions",
        project_id,
        len(proposals),
        len(human_tasks),
        len(human_task_reviews),
        len(kpi_suggestions),
    )

    # Store reflection on today's snapshot
    if reflection:
        try:
            update_snapshot_reflection(project_id, today, reflection)
        except Exception:
            log.warning("Could not update snapshot reflection", exc_info=True)

    # Create proposal records
    for prop in proposals:
        if not isinstance(prop, dict) or not prop.get("action"):
            continue
        prop_id = uuid.uuid4().hex[:8]
        put_proposal(
            project_id,
            today,
            prop_id,
            {
                "action": str(prop.get("action", "")),
                "rationale": str(prop.get("rationale", "")),
                "domain": str(prop.get("domain", "")),
                "target_kpi": str(prop.get("target_kpi", "")),
            },
        )

    # Create human-assigned tasks
    for ht in human_tasks:
        if not isinstance(ht, dict) or not ht.get("title"):
            continue
        priority = str(ht.get("priority", "medium"))
        if priority not in ("low", "medium", "high", "urgent"):
            priority = "medium"
        try:
            store.create(
                title=str(ht["title"]),
                description=str(ht.get("description", "")),
                priority=priority,
                created_by="agent",
                project_id=project_id,
                assignee="human",
            )
        except Exception:
            log.warning("Could not create human task: %s", ht.get("title"), exc_info=True)

    # Process human task reviews
    for review in human_task_reviews:
        if not isinstance(review, dict) or not review.get("task_id"):
            continue
        task_id = str(review["task_id"])
        approved = bool(review.get("approved", False))
        comment = str(review.get("comment", ""))
        try:
            task = store.get(task_id)
            if not task or getattr(task, "assignee", "agent") != "human":
                continue
            if task.status.value != "in_review":
                continue
            if approved:
                store.update_status(task_id, TaskStatus.COMPLETED)
                if comment:
                    store.add_comment(task_id, "agent", comment)
            else:
                if comment:
                    store.add_comment(task_id, "agent", comment)
                store.update_status(task_id, TaskStatus.PENDING)
        except Exception:
            log.warning("Could not process review for task %s", task_id, exc_info=True)

    # Process KPI suggestions as proposals (domain="kpi") for human approval
    for suggestion in kpi_suggestions:
        if not isinstance(suggestion, dict):
            continue
        action = str(suggestion.get("action", ""))
        kpi_obj = suggestion.get("kpi", {})
        reason = str(suggestion.get("reason", ""))
        if action not in ("add", "modify", "remove") or not kpi_obj:
            continue
        kpi_id = str(kpi_obj.get("id", "")) if isinstance(kpi_obj, dict) else ""
        if not kpi_id:
            continue
        action_text = "KPI %s: %s" % (action, kpi_id)
        if action in ("add", "modify") and isinstance(kpi_obj, dict):
            label = kpi_obj.get("label", kpi_id)
            target = kpi_obj.get("target", "?")
            unit = kpi_obj.get("unit", "")
            action_text = "KPI %s: %s — target %s %s" % (action, label, target, unit)
        prop_id = uuid.uuid4().hex[:8]
        put_proposal(
            project_id,
            today,
            prop_id,
            {
                "action": action_text,
                "rationale": reason,
                "domain": "kpi",
                "target_kpi": kpi_id,
            },
        )

    plog(
        project_id,
        "daily_cycle_done",
        "objectives",
        "Reflection complete: %d proposals, %d human tasks, %d reviews, %d kpi suggestions"
        % (len(proposals), len(human_tasks), len(human_task_reviews), len(kpi_suggestions)),
        runtime=elapsed,
    )

    log.info("Daily cycle complete for project %s (%.1fs)", project_id, elapsed)
    return True
