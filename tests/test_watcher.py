"""Tests for src/watcher.py — snapshot, callback dispatch."""

import pytest

from src.task_store import TaskStatus
from src.watcher import TaskWatcher


class TestSnapshot:
    def test_empty(self, tmp_tasks):
        watcher = TaskWatcher(tmp_tasks)
        assert watcher._snapshot() == {}

    def test_excludes_subtasks(self, tmp_tasks):
        parent = tmp_tasks.create(title="Parent")
        tmp_tasks.create(title="Child", parent_id=parent.id)
        snapshot = TaskWatcher(tmp_tasks)._snapshot()
        assert parent.id in snapshot
        assert len(snapshot) == 1

    def test_tracks_statuses(self, tmp_tasks):
        t1 = tmp_tasks.create(title="A")
        t2 = tmp_tasks.create(title="B")
        tmp_tasks.update_status(t2.id, TaskStatus.COMPLETED)
        snapshot = TaskWatcher(tmp_tasks)._snapshot()
        assert snapshot[t1.id] == TaskStatus.PENDING
        assert snapshot[t2.id] == TaskStatus.COMPLETED


class TestCallbacks:
    @pytest.mark.asyncio
    async def test_notify_on_change(self, tmp_tasks):
        watcher = TaskWatcher(tmp_tasks, poll_interval=0.1)
        changes = []

        async def on_change(task, old_status):
            changes.append((task.id, old_status, task.status))

        watcher.on_status_change(on_change)

        task = tmp_tasks.create(title="Watch me")
        watcher._status_cache = {task.id: TaskStatus.PENDING}

        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)

        # Manually trigger one poll cycle
        current = watcher._snapshot()
        for task_id, new_status in current.items():
            old_status = watcher._status_cache.get(task_id)
            if old_status is not None and old_status != new_status:
                t = tmp_tasks.get(task_id)
                if t:
                    await watcher._notify(t, old_status)

        assert len(changes) == 1
        assert changes[0] == (task.id, TaskStatus.PENDING, TaskStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_no_notify_for_new_task(self, tmp_tasks):
        """New tasks (not in cache) should not trigger a callback."""
        watcher = TaskWatcher(tmp_tasks, poll_interval=0.1)
        changes = []

        async def on_change(task, old_status):
            changes.append(task.id)

        watcher.on_status_change(on_change)
        watcher._status_cache = {}

        tmp_tasks.create(title="New task")

        current = watcher._snapshot()
        for task_id, new_status in current.items():
            old_status = watcher._status_cache.get(task_id)
            if old_status is not None and old_status != new_status:
                t = tmp_tasks.get(task_id)
                if t:
                    await watcher._notify(t, old_status)

        assert len(changes) == 0

    @pytest.mark.asyncio
    async def test_callback_exception_doesnt_propagate(self, tmp_tasks):
        watcher = TaskWatcher(tmp_tasks)
        task = tmp_tasks.create(title="Error test")

        async def bad_callback(task, old_status):
            raise RuntimeError("oops")

        watcher.on_status_change(bad_callback)
        await watcher._notify(task, TaskStatus.PENDING)
