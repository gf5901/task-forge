"""
Polls the DynamoDB task store for status changes and fires callbacks
when a task transitions (e.g. to 'completed').
"""

import asyncio
import logging
from typing import Awaitable, Callable, Dict, List

from .dynamo_store import DynamoTaskStore
from .task_store import Task, TaskStatus

log = logging.getLogger(__name__)

StatusCallback = Callable[[Task, TaskStatus], Awaitable[None]]


class TaskWatcher:
    """Polls DynamoDB for task status transitions."""

    def __init__(self, store: DynamoTaskStore, poll_interval: float = 5.0):
        self.store = store
        self.poll_interval = poll_interval
        self._status_cache: Dict[str, TaskStatus] = {}
        self._callbacks: List[StatusCallback] = []
        self._running = False

    def on_status_change(self, callback: StatusCallback):
        self._callbacks.append(callback)

    async def _notify(self, task: Task, old_status: TaskStatus):
        for cb in self._callbacks:
            try:
                await cb(task, old_status)
            except Exception:
                log.exception("Error in watcher callback for task %s", task.id)

    def _snapshot(self) -> Dict[str, TaskStatus]:
        return {t.id: t.status for t in self.store.list_tasks() if not t.parent_id}

    async def start(self):
        self._running = True
        self._status_cache = self._snapshot()
        log.info("Task watcher started (polling every %.1fs)", self.poll_interval)

        while self._running:
            await asyncio.sleep(self.poll_interval)
            try:
                current = self._snapshot()
                for task_id, new_status in current.items():
                    old_status = self._status_cache.get(task_id)
                    if old_status is not None and old_status != new_status:
                        task = self.store.get(task_id)
                        if task:
                            log.info(
                                "Task %s: %s -> %s", task_id, old_status.value, new_status.value
                            )
                            await self._notify(task, old_status)
                self._status_cache = current
            except Exception:
                log.exception("Error during task watch poll")

    def stop(self):
        self._running = False
