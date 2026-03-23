"""
FastAPI TestClient tests for the tasks router API surface.

These tests exercise the HTTP layer — auth middleware, request validation,
status codes, and response shapes — not just the underlying functions.
Auth is disabled (AUTH_EMAIL/AUTH_PASSWORD unset in conftest).
"""

import pytest
from fastapi.testclient import TestClient

import src.routers.tasks as tasks_router
import src.web as web_mod
from src.task_store import TaskStatus


@pytest.fixture
def client(tmp_tasks, monkeypatch):
    """TestClient wired to a fresh DynamoTaskStore with no auth and no real runner."""
    monkeypatch.setattr(tasks_router, "_get_store", lambda: tmp_tasks)
    monkeypatch.setattr(web_mod, "trigger_runner", lambda task_id: None)
    monkeypatch.setattr(web_mod, "cancel_runner", lambda task_id: None)
    # Tests assume auth is off; host env may set AUTH_EMAIL/PASSWORD (setdefault in conftest
    # does not override). Force-disable so /api/* returns 200, not 401.
    monkeypatch.setattr(web_mod, "AUTH_ENABLED", False)
    from src.web import app

    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /api/tasks
# ---------------------------------------------------------------------------


class TestListTasks:
    def test_empty(self, client):
        r = client.get("/api/tasks")
        assert r.status_code == 200
        assert r.json()["tasks"] == []
        assert r.json()["total"] == 0

    def test_returns_created_task(self, client, tmp_tasks):
        tmp_tasks.create(title="Hello")
        r = client.get("/api/tasks")
        assert r.status_code == 200
        tasks = r.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Hello"

    def test_filters_by_status(self, client, tmp_tasks):
        t1 = tmp_tasks.create(title="Pending")
        t2 = tmp_tasks.create(title="Done")
        tmp_tasks.update_status(t2.id, TaskStatus.COMPLETED)

        r = client.get("/api/tasks?status=pending")
        ids = [t["id"] for t in r.json()["tasks"]]
        assert t1.id in ids
        assert t2.id not in ids

    def test_pagination(self, client, tmp_tasks):
        for i in range(5):
            tmp_tasks.create(title="Task %d" % i)
        r = client.get("/api/tasks?limit=2&offset=0")
        assert len(r.json()["tasks"]) == 2
        assert r.json()["total"] == 5


# ---------------------------------------------------------------------------
# GET /api/tasks/{id}
# ---------------------------------------------------------------------------


class TestGetTask:
    def test_returns_task_detail(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Detail task", description="Some desc")
        r = client.get("/api/tasks/%s" % task.id)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == task.id
        assert data["title"] == "Detail task"
        assert data["description"] == "Some desc"

    def test_404_for_missing(self, client):
        r = client.get("/api/tasks/notexist")
        assert r.status_code == 404

    def test_includes_subtasks(self, client, tmp_tasks):
        parent = tmp_tasks.create(title="Parent")
        child = tmp_tasks.create(title="Child", parent_id=parent.id)
        r = client.get("/api/tasks/%s" % parent.id)
        assert r.status_code == 200
        subtask_ids = [s["id"] for s in r.json()["subtasks"]]
        assert child.id in subtask_ids


# ---------------------------------------------------------------------------
# POST /api/tasks
# ---------------------------------------------------------------------------


class TestCreateTask:
    def test_creates_task(self, client, tmp_tasks):
        r = client.post("/api/tasks", json={"title": "New task"})
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "New task"
        assert data["status"] == "pending"

    def test_missing_title_returns_422(self, client):
        r = client.post("/api/tasks", json={})
        assert r.status_code == 422

    def test_creates_with_priority(self, client, tmp_tasks):
        r = client.post("/api/tasks", json={"title": "Urgent task", "priority": "urgent"})
        assert r.status_code == 200
        assert r.json()["priority"] == "urgent"

    def test_invalid_priority_returns_400(self, client):
        r = client.post("/api/tasks", json={"title": "Bad", "priority": "critical"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# PATCH /api/tasks/{id}/status
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    def test_updates_status(self, client, tmp_tasks):
        task = tmp_tasks.create(title="T")
        r = client.patch("/api/tasks/%s/status" % task.id, json={"status": "completed"})
        assert r.status_code == 200
        assert tmp_tasks.get(task.id).status == TaskStatus.COMPLETED

    def test_invalid_status_returns_400(self, client, tmp_tasks):
        task = tmp_tasks.create(title="T")
        r = client.patch("/api/tasks/%s/status" % task.id, json={"status": "bogus"})
        assert r.status_code == 400

    def test_missing_task_returns_404(self, client):
        r = client.patch("/api/tasks/missing/status", json={"status": "completed"})
        assert r.status_code == 404

    def test_cancel_stamps_cancelled_by_user(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Cancel me")
        r = client.patch("/api/tasks/%s/status" % task.id, json={"status": "cancelled"})
        assert r.status_code == 200
        assert tmp_tasks.get_cancelled_by(task.id) == "user"


# ---------------------------------------------------------------------------
# POST /api/tasks/{id}/run
# ---------------------------------------------------------------------------


class TestRunTask:
    def test_run_pending_task(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Run me")
        r = client.post("/api/tasks/%s/run" % task.id)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_run_missing_task_returns_404(self, client):
        r = client.post("/api/tasks/missing/run")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tasks/{id}/rerun
# ---------------------------------------------------------------------------


class TestRerunTask:
    def test_rerun_completed_task(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Redo")
        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)
        r = client.post("/api/tasks/%s/rerun" % task.id)
        assert r.status_code == 200
        assert tmp_tasks.get(task.id).status == TaskStatus.PENDING

    def test_rerun_in_review_task(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Review redo")
        tmp_tasks.update_status(task.id, TaskStatus.IN_REVIEW)
        r = client.post("/api/tasks/%s/rerun" % task.id)
        assert r.status_code == 200
        assert tmp_tasks.get(task.id).status == TaskStatus.PENDING

    def test_rerun_pending_task_returns_400(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Still pending")
        r = client.post("/api/tasks/%s/rerun" % task.id)
        assert r.status_code == 400

    def test_rerun_clears_cancelled_by(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Was user-cancelled")
        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)
        tmp_tasks.set_cancelled_by(task.id, "user")
        r = client.post("/api/tasks/%s/rerun" % task.id)
        assert r.status_code == 200
        assert tmp_tasks.get_cancelled_by(task.id) is None

    def test_rerun_clears_reply_pending(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Had reply pending")
        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)
        tmp_tasks.set_reply_pending(task.id, True)
        r = client.post("/api/tasks/%s/rerun" % task.id)
        assert r.status_code == 200
        assert not tmp_tasks.get(task.id).reply_pending

    def test_rerun_missing_task_returns_404(self, client):
        r = client.post("/api/tasks/missing/rerun")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tasks/{id}/comment
# ---------------------------------------------------------------------------


class TestAddComment:
    def test_adds_comment(self, client, tmp_tasks, monkeypatch):
        import src.web as web_mod

        monkeypatch.setattr(web_mod, "trigger_comment_reply", lambda task_id: None)
        task = tmp_tasks.create(title="T")
        r = client.post("/api/tasks/%s/comment" % task.id, json={"body": "Hello agent"})
        assert r.status_code == 200
        comments = tmp_tasks.get_comments(task.id)
        assert any(c.body == "Hello agent" for c in comments)

    def test_comment_missing_task_returns_404(self, client, tmp_tasks, monkeypatch):
        import src.web as web_mod

        monkeypatch.setattr(web_mod, "trigger_comment_reply", lambda task_id: None)
        r = client.post("/api/tasks/missing/comment", json={"body": "Hi"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/tasks/{id}
# ---------------------------------------------------------------------------


class TestDeleteTask:
    def test_deletes_task(self, client, tmp_tasks, monkeypatch):
        monkeypatch.setattr("src.routers.tasks._get_store", lambda: tmp_tasks)
        # Stub out subprocess calls that the background cleanup thread makes
        import subprocess as _sp

        monkeypatch.setattr(_sp, "run", lambda *a, **kw: None)
        task = tmp_tasks.create(title="Delete me")
        r = client.delete("/api/tasks/%s" % task.id)
        assert r.status_code == 200
        assert tmp_tasks.get(task.id) is None

    def test_delete_missing_task_returns_404(self, client, tmp_tasks, monkeypatch):
        r = client.delete("/api/tasks/missing")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/repos, /api/roles, /api/counts
# ---------------------------------------------------------------------------


class TestSpawnedByAPI:
    def test_create_task_with_spawned_by(self, client, tmp_tasks):
        root = tmp_tasks.create(title="Root task")
        r = client.post("/api/tasks", json={"title": "Generated task", "spawned_by": root.id})
        assert r.status_code == 200
        data = r.json()
        assert data["spawned_by"] == root.id

    def test_spawned_by_header(self, client, tmp_tasks):
        root = tmp_tasks.create(title="Root task")
        r = client.post(
            "/api/tasks",
            json={"title": "Header-spawned task"},
            headers={"X-Spawned-By-Task": root.id},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["spawned_by"] == root.id

    def test_body_spawned_by_takes_precedence_over_header(self, client, tmp_tasks):
        root = tmp_tasks.create(title="Root")
        other = tmp_tasks.create(title="Other")
        r = client.post(
            "/api/tasks",
            json={"title": "T", "spawned_by": root.id},
            headers={"X-Spawned-By-Task": other.id},
        )
        assert r.status_code == 200
        assert r.json()["spawned_by"] == root.id

    def test_get_task_includes_spawned_tasks(self, client, tmp_tasks):
        root = tmp_tasks.create(title="Root")
        s1 = tmp_tasks.create(title="Spawned 1", spawned_by=root.id)
        s2 = tmp_tasks.create(title="Spawned 2", spawned_by=root.id)
        r = client.get("/api/tasks/%s" % root.id)
        assert r.status_code == 200
        spawned_ids = [t["id"] for t in r.json()["spawned_tasks"]]
        assert s1.id in spawned_ids
        assert s2.id in spawned_ids

    def test_get_task_includes_spawned_by_task(self, client, tmp_tasks):
        root = tmp_tasks.create(title="Root")
        child = tmp_tasks.create(title="Child", spawned_by=root.id)
        r = client.get("/api/tasks/%s" % child.id)
        assert r.status_code == 200
        data = r.json()
        assert data["spawned_by_task"] is not None
        assert data["spawned_by_task"]["id"] == root.id
        assert data["spawned_by_task"]["title"] == "Root"

    def test_get_task_spawned_by_task_null_when_none(self, client, tmp_tasks):
        task = tmp_tasks.create(title="Standalone")
        r = client.get("/api/tasks/%s" % task.id)
        assert r.status_code == 200
        assert r.json()["spawned_by_task"] is None

    def test_get_task_spawned_tasks_empty_when_none(self, client, tmp_tasks):
        task = tmp_tasks.create(title="No children")
        r = client.get("/api/tasks/%s" % task.id)
        assert r.status_code == 200
        assert r.json()["spawned_tasks"] == []


class TestMetaEndpoints:
    def test_repos_returns_list(self, client):
        r = client.get("/api/repos")
        assert r.status_code == 200
        assert "repos" in r.json()
        assert isinstance(r.json()["repos"], list)

    def test_roles_returns_list(self, client):
        r = client.get("/api/roles")
        assert r.status_code == 200
        assert isinstance(r.json()["roles"], list)
        assert len(r.json()["roles"]) > 0

    def test_counts_returns_all_statuses(self, client):
        r = client.get("/api/counts")
        assert r.status_code == 200
        data = r.json()
        for status in ("pending", "in_progress", "in_review", "completed", "cancelled"):
            assert status in data


class TestSettingsEndpoints:
    def test_get_settings(self, client, monkeypatch):
        def fake():
            return {
                "max_concurrent_runners": 2,
                "min_spawn_interval": 0,
                "task_timeout": 900,
                "budget_daily_usd": 0.0,
            }

        monkeypatch.setattr("src.routers.settings.get_settings", fake)
        r = client.get("/api/settings")
        assert r.status_code == 200
        assert r.json()["max_concurrent_runners"] == 2

    def test_patch_empty_body_returns_settings(self, client, monkeypatch):
        def fake():
            return {
                "max_concurrent_runners": 1,
                "min_spawn_interval": 300,
                "task_timeout": 900,
                "budget_daily_usd": 0.0,
            }

        monkeypatch.setattr("src.routers.settings.get_settings", fake)
        r = client.patch("/api/settings", json={})
        assert r.status_code == 200
        assert r.json()["min_spawn_interval"] == 300

    def test_patch_validation_error_returns_400(self, client, monkeypatch):
        def boom(_patch):
            raise ValueError("budget_daily_usd: out of range")

        monkeypatch.setattr("src.routers.settings.update_settings", boom)
        r = client.patch("/api/settings", json={"budget_daily_usd": 9999.0})
        assert r.status_code == 400
        assert "out of range" in r.json()["error"]

    def test_patch_success(self, client, monkeypatch):
        def fake_update(patch):
            assert "max_concurrent_runners" in patch
            return {
                "max_concurrent_runners": 2,
                "min_spawn_interval": 300,
                "task_timeout": 900,
                "budget_daily_usd": 0.0,
            }

        monkeypatch.setattr("src.routers.settings.update_settings", fake_update)
        r = client.patch("/api/settings", json={"max_concurrent_runners": 2})
        assert r.status_code == 200
        assert r.json()["max_concurrent_runners"] == 2
