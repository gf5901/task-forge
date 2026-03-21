"""Task management API routes."""

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..roles import ROLES
from ..task_store import TaskPriority, TaskStatus

router = APIRouter(prefix="/api", tags=["tasks"])


def _get_store():
    from ..web import store

    return store


PRIORITY_ORDER = {
    TaskPriority.URGENT: 0,
    TaskPriority.HIGH: 1,
    TaskPriority.MEDIUM: 2,
    TaskPriority.LOW: 3,
}


def task_to_dict(task, include_output=False):
    """Serialize a Task to a JSON-safe dict."""
    s = _get_store()
    d = {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "priority": task.priority.value,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "created_by": task.created_by,
        "tags": task.tags,
        "target_repo": task.target_repo,
        "parent_id": task.parent_id,
        "model": task.model,
        "plan_only": task.plan_only,
        "depends_on": task.depends_on,
        "deps_ready": s.deps_ready(task),
        "session_id": task.session_id,
        "reply_pending": task.reply_pending,
        "role": task.role,
        "spawned_by": task.spawned_by,
    }
    if include_output:
        d["agent_output"] = s.get_agent_output(task.id)
        d["pr_url"] = s.get_pr_url(task.id)
        d["merged_at"] = s.get_merged_at(task.id)
        d["deployed_at"] = s.get_deployed_at(task.id)
    return d


def _counts():
    s = _get_store()
    tasks = [t for t in s.list_tasks() if not t.parent_id]
    counts = {
        "all": len(tasks),
        "pending": 0,
        "in_progress": 0,
        "in_review": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for t in tasks:
        counts[t.status.value] += 1
    return counts


def _trigger_unblocked_dependents(store, completed_task_id: str) -> None:
    """After a task completes, trigger any pending tasks that were waiting on it."""
    from ..web import trigger_runner

    for t in store.find_dependents(completed_task_id):
        try:
            trigger_runner(t.id)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Failed to trigger dependent task %s", t.id)


class TaskCreateBody(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    tags: str = ""
    target_repo: str = ""
    plan_only: bool = False
    role: str = ""
    spawned_by: str = ""


class StatusBody(BaseModel):
    status: str


class CommentBody(BaseModel):
    body: str


@router.get("/tasks")
async def list_tasks(status: str = "all", limit: int = 25, offset: int = 0):
    s = _get_store()
    filter_status = None if status == "all" else TaskStatus(status)
    tasks = s.list_tasks(status=filter_status)
    tasks = [t for t in tasks if not t.parent_id]
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    total = len(tasks)
    page = tasks[offset : offset + limit]
    return {"tasks": [task_to_dict(t) for t in page], "total": total, "counts": _counts()}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    s = _get_store()
    task = s.get(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)

    d = task_to_dict(task, include_output=True)
    d["subtasks"] = [task_to_dict(sub) for sub in s.list_subtasks(task_id)]
    d["dep_tasks"] = [
        {"id": dep.id, "title": dep.title, "status": dep.status.value}
        for dep_id in task.depends_on
        for dep in [s.get(dep_id)]
        if dep
    ]
    d["comments"] = [
        {"author": c.author, "body": c.body, "created_at": c.created_at}
        for c in s.get_comments(task_id)
    ]
    if task.parent_id:
        parent = s.get(task.parent_id)
        d["parent"] = {"id": parent.id, "title": parent.title} if parent else None

    # Tasks spawned by agents running this task
    d["spawned_tasks"] = [task_to_dict(t) for t in s.list_spawned_tasks(task.id)]

    # Link back to the task that spawned this one (always present, null if none)
    spawned_by_task = None
    if task.spawned_by:
        spawner = s.get(task.spawned_by)
        spawned_by_task = {"id": spawner.id, "title": spawner.title} if spawner else None
    d["spawned_by_task"] = spawned_by_task

    from ..pipeline_log import read_logs

    logs = read_logs(task_id=task_id, limit=500)
    total_runtime = sum(
        float(e.get("extra", {}).get("runtime", 0))
        for e in logs
        if e.get("extra", {}).get("runtime")
    )
    d["runtime"] = round(total_runtime, 1) if total_runtime else None

    token_keys = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
    totals = dict.fromkeys(token_keys, 0)
    for e in logs:
        extra = e.get("extra", {})
        for k in token_keys:
            if k in extra:
                totals[k] += int(extra[k])
    d["tokens"] = totals if any(totals.values()) else None

    return d


@router.post("/tasks")
async def create_task(body: TaskCreateBody, request: Request):
    s = _get_store()
    try:
        TaskPriority(body.priority)
    except ValueError:
        return JSONResponse({"error": "invalid priority: %s" % body.priority}, status_code=400)
    tag_list = [t.strip() for t in body.tags.split(",") if t.strip()]

    # spawned_by can be provided in the request body or via a header
    # (body field takes precedence; header is a fallback for callers that can't modify the body)
    spawned_by = body.spawned_by.strip() or request.headers.get("X-Spawned-By-Task", "").strip()

    task = s.create(
        title=body.title,
        description=body.description,
        priority=body.priority,
        created_by="web",
        tags=tag_list,
        target_repo=body.target_repo.strip(),
        plan_only=body.plan_only,
        role=body.role.strip(),
        spawned_by=spawned_by,
    )
    from ..web import trigger_runner

    trigger_runner(task.id)
    return task_to_dict(task)


@router.patch("/tasks/{task_id}/status")
async def update_status(task_id: str, body: StatusBody):
    s = _get_store()
    try:
        new_status = TaskStatus(body.status)
    except ValueError:
        return JSONResponse({"error": "invalid status: %s" % body.status}, status_code=400)
    task = s.update_status(task_id, new_status)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    if body.status == "cancelled":
        s.set_cancelled_by(task_id, "user")
        from ..web import cancel_runner

        cancel_runner(task_id)
    if new_status in (TaskStatus.COMPLETED, TaskStatus.IN_REVIEW):
        _trigger_unblocked_dependents(s, task_id)
    return task_to_dict(task)


@router.post("/tasks/{task_id}/run")
async def run_task(task_id: str):
    s = _get_store()
    task = s.get(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    if task.status == TaskStatus.PENDING:
        from ..web import trigger_runner

        trigger_runner(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/rerun")
async def rerun_task(task_id: str):
    """Reset a completed/in_review/cancelled task to pending and trigger the runner."""
    s = _get_store()
    task = s.get(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    if task.status not in (
        TaskStatus.COMPLETED,
        TaskStatus.IN_REVIEW,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    ):
        return JSONResponse(
            {"error": "only completed, in_review, failed, or cancelled tasks can be rerun"},
            status_code=400,
        )
    # Warn if an open PR already exists from a previous run
    existing_pr = s.get_pr_url(task_id)
    if existing_pr:
        s.add_comment(
            task_id,
            "agent",
            "**Note:** This task was rerun while an existing PR may still be open: %s\n\n"
            "The new run will create a fresh branch and PR. Please close the old PR if it is no longer needed."
            % existing_pr,
        )
    # Clear stale metadata that could confuse the healer or reply runner
    s.set_reply_pending(task_id, False)
    s.clear_cancelled_by(task_id)
    s.update_status(task_id, TaskStatus.PENDING)
    from ..web import trigger_runner

    trigger_runner(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/comment")
async def add_comment(task_id: str, body: CommentBody):
    s = _get_store()
    comment = s.add_comment(task_id, author="web", body=body.body.strip())
    if not comment:
        return JSONResponse({"error": "not found"}, status_code=404)
    s.set_reply_pending(task_id, True)
    from ..web import trigger_comment_reply

    trigger_comment_reply(task_id)
    return {"author": comment.author, "body": comment.body, "created_at": comment.created_at}


@router.post("/tasks/{task_id}/replan")
async def replan_task(task_id: str):
    """Reset a cancelled task as plan_only=True and re-trigger the runner."""
    s = _get_store()
    task = s.get(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    if task.status not in (TaskStatus.CANCELLED, TaskStatus.FAILED):
        return JSONResponse(
            {"error": "only cancelled or failed tasks can be replanned"}, status_code=400
        )

    s.replan_as_pending(task_id)

    from ..web import trigger_runner

    trigger_runner(task_id)
    return {"ok": True, "message": "Task reset to plan-only and queued"}


@router.post("/tasks/{task_id}/reply")
async def trigger_reply(task_id: str):
    s = _get_store()
    task = s.get(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    from ..web import trigger_comment_reply

    trigger_comment_reply(task_id)
    return {"ok": True, "message": "Agent reply triggered"}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    import threading

    s = _get_store()
    task = s.get(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Delete subtasks first
    for sub in s.list_subtasks(task_id):
        s.delete(sub.id)

    ok = s.delete(task_id)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Clean up git branch, worktree, pidfile in the background so the
    # HTTP response isn't held up by git push (may take a few seconds).
    def _cleanup():
        from ..runner import delete_task_artifacts

        try:
            delete_task_artifacts(task)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("delete_task_artifacts failed for %s", task_id)

    threading.Thread(target=_cleanup, daemon=True).start()
    return {"ok": True}


@router.post("/heal")
async def heal():
    """Run the self-healer and return a summary of actions taken."""
    import threading

    result = {}

    def _run():
        from ..healer import run_healer

        stale, pr, cancelled, worktrees = run_healer(_get_store())
        result.update(
            {
                "stale_reset": stale,
                "prs_created": pr,
                "cancelled_recovered": cancelled,
                "worktrees_cleaned": worktrees,
                "total": stale + pr + cancelled + worktrees,
            }
        )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=120)
    return result if result else {"error": "healer timed out"}


@router.get("/repos")
async def list_repos():
    """Return all known target repo names."""
    return {"repos": _get_store().get_repos()}


@router.get("/roles")
async def list_roles():
    """Return all predefined agent roles."""
    return {"roles": ROLES}


@router.get("/counts")
async def counts():
    return _counts()


@router.get("/logs")
async def logs(task_id: Optional[str] = None, limit: int = 200, offset: int = 0):
    from ..pipeline_log import read_logs

    entries = read_logs(task_id=task_id, limit=min(limit, 500), offset=offset)
    return {"entries": entries, "count": len(entries)}


@router.get("/budget")
async def budget():
    from ..budget import budget_status

    return budget_status()


@router.get("/stats")
async def stats():
    """Aggregate token usage and cost stats."""
    from collections import defaultdict
    from datetime import datetime, timezone

    from ..budget import estimate_cost
    from ..pipeline_log import read_logs

    entries = read_logs(limit=10000)
    token_keys = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")

    today_str = datetime.now(timezone.utc).date().isoformat()

    today_tokens = dict.fromkeys(token_keys, 0)
    all_tokens = dict.fromkeys(token_keys, 0)
    daily_buckets = defaultdict(lambda: dict.fromkeys(token_keys, 0))  # type: ignore[var-annotated]

    for e in entries:
        extra = e.get("extra", {})
        usage = {}
        for k in token_keys:
            if k in extra:
                v = int(extra[k])
                usage[k] = v
                all_tokens[k] += v
        if not usage:
            continue
        ts = e.get("ts", "")
        day = ts[:10] if len(ts) >= 10 else ""
        if day:
            for k, v in usage.items():
                daily_buckets[day][k] += v
        if ts.startswith(today_str):
            for k, v in usage.items():
                today_tokens[k] += v

    today_cost = estimate_cost(today_tokens)
    all_cost = estimate_cost(all_tokens)

    daily = []
    for day in sorted(daily_buckets.keys())[-14:]:
        bucket = daily_buckets[day]
        daily.append(
            {
                "date": day,
                "cost_usd": round(estimate_cost(bucket), 4),
                "tokens": sum(bucket.values()),
            }
        )

    return {
        "today": {**today_tokens, "cost_usd": round(today_cost, 4)},
        "all_time": {**all_tokens, "cost_usd": round(all_cost, 4)},
        "daily": daily,
    }
