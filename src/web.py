"""
FastAPI web backend for task management — Linear-inspired dark theme.
Runs alongside the Discord bot; shares the same DynamoTaskStore.

Operates in two modes depending on whether the React frontend has been built:

* **SPA mode** (default when ``frontend/dist/`` exists) — serves the React SPA
  and exposes a JSON API at ``/api/*``.  Auth middleware only protects API
  routes; the SPA handles its own login UI via ``/api/auth/me``.

* **Legacy mode** — falls back to server-rendered Jinja2 templates when the
  frontend dist directory is absent.
"""

import hmac
import logging
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import markdown as md
from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from .task_store import TaskPriority, TaskStatus

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
RUN_TASK_SCRIPT = PROJECT_ROOT / "run_task.py"

AUTH_EMAIL = os.getenv("AUTH_EMAIL", "")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "") or secrets.token_hex(32)
AUTH_ENABLED = bool(AUTH_EMAIL and AUTH_PASSWORD)

# Default: Vite dev server. Production: set CORS_ORIGINS to your SPA origin(s), comma-separated.
_default_cors = "http://localhost:5173,http://127.0.0.1:5173"
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_cors).split(",") if o.strip()]


# ---------------------------------------------------------------------------
# Background process helpers
# ---------------------------------------------------------------------------


def _spawn_background(cmd):
    # type: (list) -> None
    """Spawn a process detached from this server, logging to runner.log."""
    log_fd = open(PROJECT_ROOT / "runner.log", "a")  # noqa: SIM115
    try:
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        log_fd.close()
        raise


def trigger_runner(task_id=None):
    # type: (Optional[str]) -> None
    """Spawn run_task.py in the background (fire-and-forget)."""
    cmd = [str(VENV_PYTHON), str(RUN_TASK_SCRIPT)]
    if task_id:
        cmd.append(task_id)
    _spawn_background(cmd)


def trigger_comment_reply(task_id):
    # type: (str) -> None
    """Spawn run_task.py --reply in the background."""
    _spawn_background([str(VENV_PYTHON), str(RUN_TASK_SCRIPT), "--reply", task_id])


def cancel_runner(task_id):
    # type: (str) -> bool
    """Signal the runner for task_id to stop. Returns True if a signal was sent."""
    from .runner import kill_runner_for_task

    return kill_runner_for_task(task_id)


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
SPA_MODE = FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").exists()


class AuthMiddleware:
    """Enforce auth on legacy Jinja2 routes. In SPA mode, only /api/* routes
    (except /api/auth/*) are protected."""

    EXEMPT_PREFIXES = ("/login", "/webhook/", "/api/auth/", "/api/health", "/assets/")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not AUTH_ENABLED:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        if SPA_MODE and not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        if not scope.get("session", {}).get("authenticated"):
            if path.startswith("/api/"):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
            else:
                response = RedirectResponse("/login", status_code=302)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Task Forge")

app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=AUTH_SECRET_KEY,
    max_age=60 * 60 * 24 * 30,  # 30 days
    same_site="none",
    https_only=True,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .dynamo_store import DynamoTaskStore

store = DynamoTaskStore()

# Register API routers
from .routers.auth import router as auth_router
from .routers.health import router as health_router
from .routers.settings import router as settings_router
from .routers.tasks import router as tasks_router
from .routers.webhook import router as webhook_router

app.include_router(tasks_router)
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(webhook_router)
app.include_router(settings_router)

# ---------------------------------------------------------------------------
# Legacy Jinja2 routes (disabled when React SPA is built)
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["auth_enabled"] = AUTH_ENABLED

_md = md.Markdown(extensions=["fenced_code", "tables", "nl2br", "sane_lists"])


def _render_markdown(text):
    # type: (str) -> Markup
    _md.reset()
    return Markup(_md.convert(text))


def _timeago(dt_str):
    # type: (str) -> str
    try:
        dt = datetime.fromisoformat(dt_str)
        seconds = (datetime.now(timezone.utc) - dt).total_seconds()
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return "%dm ago" % (seconds // 60)
        if seconds < 86400:
            return "%dh ago" % (seconds // 3600)
        if seconds < 604800:
            return "%dd ago" % (seconds // 86400)
        return "%dw ago" % (seconds // 604800)
    except (ValueError, TypeError):
        return dt_str


templates.env.filters["timeago"] = _timeago

if not SPA_MODE:

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if request.session.get("authenticated"):
            return RedirectResponse("/tasks", status_code=302)
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    @app.post("/login")
    async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
        if hmac.compare_digest(email, AUTH_EMAIL) and hmac.compare_digest(password, AUTH_PASSWORD):
            request.session["authenticated"] = True
            request.session["email"] = email
            return RedirectResponse("/tasks", status_code=303)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid email or password",
            },
        )

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return RedirectResponse("/tasks", status_code=302)

    def _counts():
        tasks = [t for t in store.list_tasks() if not t.parent_id]
        counts = {
            "all": len(tasks),
            "pending": 0,
            "in_progress": 0,
            "in_review": 0,
            "completed": 0,
            "cancelled": 0,
        }
        for t in tasks:
            counts[t.status.value] += 1
        return counts

    @app.get("/tasks", response_class=HTMLResponse)
    async def task_list(request: Request, status: str = "all"):
        filter_status = None if status == "all" else TaskStatus(status)
        tasks = store.list_tasks(status=filter_status)
        tasks = [t for t in tasks if not t.parent_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return templates.TemplateResponse(
            "tasks.html",
            {
                "request": request,
                "tasks": tasks,
                "current_status": status,
                "counts": _counts(),
            },
        )

    @app.get("/tasks/new", response_class=HTMLResponse)
    async def task_new(request: Request):
        return templates.TemplateResponse(
            "task_form.html",
            {
                "request": request,
                "counts": _counts(),
                "priorities": [p.value for p in TaskPriority],
                "repos": store.get_repos(),
            },
        )

    @app.post("/tasks")
    async def task_create(
        title: str = Form(...),
        description: str = Form(""),
        priority: str = Form("medium"),
        tags: str = Form(""),
        target_repo: str = Form(""),
    ):
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        task = store.create(
            title=title,
            description=description,
            priority=priority,
            created_by="web",
            tags=tag_list,
            target_repo=target_repo.strip(),
        )
        trigger_runner(task.id)
        return RedirectResponse("/tasks", status_code=303)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_detail(request: Request, task_id: str):
        task = store.get(task_id)
        if not task:
            return RedirectResponse("/tasks", status_code=302)
        output = store.get_agent_output(task_id)
        pr_url = store.get_pr_url(task_id)
        comments = store.get_comments(task_id)
        subtasks = store.list_subtasks(task_id)
        parent = store.get(task.parent_id) if task.parent_id else None
        return templates.TemplateResponse(
            "task_detail.html",
            {
                "request": request,
                "task": task,
                "agent_output": _render_markdown(output) if output else None,
                "description_html": _render_markdown(task.description)
                if task.description
                else None,
                "comments": comments,
                "comment_bodies_html": {
                    i: _render_markdown(c.body) for i, c in enumerate(comments)
                },
                "pr_url": pr_url,
                "subtasks": subtasks,
                "parent": parent,
                "counts": _counts(),
                "statuses": [s.value for s in TaskStatus],
            },
        )

    @app.post("/tasks/{task_id}/run")
    async def task_run(task_id: str):
        task = store.get(task_id)
        if task and task.status == TaskStatus.PENDING:
            trigger_runner(task_id)
        return RedirectResponse("/tasks/%s" % task_id, status_code=303)

    @app.post("/tasks/{task_id}/status")
    async def task_update_status(task_id: str, status: str = Form(...)):
        store.update_status(task_id, TaskStatus(status))
        return RedirectResponse("/tasks/%s" % task_id, status_code=303)

    @app.post("/tasks/{task_id}/comment")
    async def task_add_comment(task_id: str, body: str = Form(...)):
        store.add_comment(task_id, author="web", body=body.strip())
        return RedirectResponse("/tasks/%s" % task_id, status_code=303)

    @app.post("/tasks/{task_id}/delete")
    async def task_delete(task_id: str):
        store.delete(task_id)
        return RedirectResponse("/tasks", status_code=303)


# ---------------------------------------------------------------------------
# Serve React SPA (must be registered last for catch-all to work)
# ---------------------------------------------------------------------------

if SPA_MODE:
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="spa-assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa_catch_all(full_path: str):
        return FileResponse(str(FRONTEND_DIST / "index.html"))
