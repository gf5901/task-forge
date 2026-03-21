"""GitHub webhook for auto-deploy on merge to main."""

import hashlib
import hmac
import json
import logging
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/webhook", tags=["webhook"])

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy.sh"
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


def _verify_github_signature(payload, signature):
    # type: (bytes, str) -> bool
    if not GITHUB_WEBHOOK_SECRET:
        return False
    expected = (
        "sha256="
        + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def _stamp_pr_merged(pr_url):
    # type: (str) -> None
    """Find the task for this PR, stamp merged_at, and clean up the worktree."""
    try:
        from ..web import store

        task_id = store.find_task_by_pr_url(pr_url)
        if task_id:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            store.set_merged_at(task_id, ts)
            log.info("Stamped merged_at=%s on task %s", ts, task_id)
            _cleanup_merged_worktree(store, task_id)
    except Exception:
        log.exception("Failed to stamp merged_at for %s", pr_url)


def _cleanup_merged_worktree(store, task_id):
    # type: (Any, str) -> None
    """Clean up the worktree for a merged PR's task (deferred from pipeline)."""
    try:
        from ..worktree import WORKTREE_BASE, cleanup_worktree

        task = store.get(task_id)
        if not task:
            return
        wt_path = str(WORKTREE_BASE / ("task-%s" % task_id))
        if Path(wt_path).exists():
            cleanup_worktree(task, wt_path)
            log.info("Cleaned up worktree for merged task %s", task_id)
    except Exception:
        log.exception("Failed to clean up worktree for task %s", task_id)


def _stamp_deployed_after_deploy(proc):
    # type: (subprocess.Popen) -> None  # type: ignore[type-arg]
    """Wait for the deploy process; on success stamp deployed_at on merged-but-not-deployed tasks."""
    try:
        proc.wait()
        if proc.returncode != 0:
            log.warning(
                "Deploy script exited with code %d; skipping deployed_at stamps", proc.returncode
            )
            return
        from ..web import store

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for task_id in store.list_merged_not_deployed():
            store.set_deployed_at(task_id, ts)
            log.info("Stamped deployed_at=%s on task %s", ts, task_id)
    except Exception:
        log.exception("Failed to stamp deployed_at after deploy")


@router.post("/github")
async def github_webhook(request: Request):
    if not GITHUB_WEBHOOK_SECRET:
        return JSONResponse({"error": "webhook not configured"}, status_code=503)

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_github_signature(body, signature):
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return JSONResponse({"ok": True, "msg": "pong"})

    payload = json.loads(body)

    if event == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        if action == "closed" and pr.get("merged"):
            pr_url = pr.get("html_url", "")
            if pr_url:
                threading.Thread(target=_stamp_pr_merged, args=(pr_url,), daemon=True).start()
        return JSONResponse({"ok": True, "msg": "pr event handled"})

    if event != "push":
        return JSONResponse({"ok": True, "msg": "ignored event: %s" % event})

    ref = payload.get("ref", "")
    if ref not in ("refs/heads/main", "refs/heads/master"):
        return JSONResponse({"ok": True, "msg": "ignored ref: %s" % ref})

    log.info("Deploy triggered by push to %s", ref)
    log_fd = open(PROJECT_ROOT / "deploy.log", "a")  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            ["bash", str(DEPLOY_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        threading.Thread(target=_stamp_deployed_after_deploy, args=(proc,), daemon=True).start()
    finally:
        log_fd.close()
    return JSONResponse({"ok": True, "msg": "deploy started"})
