"""Tests for heal_stale_worktrees and run_healer orchestration."""

import os
import time
from unittest.mock import MagicMock

from src.task_store import TaskStatus


class TestHealStaleWorktrees:
    def test_removes_old_worktree_when_task_not_in_progress(
        self, tmp_tasks, tmp_path, monkeypatch
    ):
        from src.healer import heal_stale_worktrees

        base = tmp_path / "worktrees"
        base.mkdir()
        monkeypatch.setattr("src.worktree.WORKTREE_BASE", base)
        monkeypatch.setenv("STALE_WORKTREE_DAYS", "0")

        task = tmp_tasks.create(title="Stale")
        tid = task.id
        wt = base / ("task-%s" % tid)
        wt.mkdir()
        old = time.time() - 10 * 86400
        os.utime(str(wt), (old, old))

        plog_calls = []

        def plog(*a, **k):
            plog_calls.append((a, k))

        n = heal_stale_worktrees(tmp_tasks, plog)
        assert n == 1
        assert not wt.exists()
        assert any("heal_stale_worktree" in str(c) for c in plog_calls)

    def test_skips_in_progress_task(self, tmp_tasks, tmp_path, monkeypatch):
        from src.healer import heal_stale_worktrees

        base = tmp_path / "worktrees2"
        base.mkdir()
        monkeypatch.setattr("src.worktree.WORKTREE_BASE", base)
        monkeypatch.setenv("STALE_WORKTREE_DAYS", "0")

        task = tmp_tasks.create(title="Running")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)
        tid = task.id
        wt = base / ("task-%s" % tid)
        wt.mkdir()
        old = time.time() - 10 * 86400
        os.utime(str(wt), (old, old))

        n = heal_stale_worktrees(tmp_tasks, lambda *a, **k: None)
        assert n == 0
        assert wt.exists()


class TestRunHealer:
    def test_returns_tuple_from_subhealers(self, monkeypatch):
        from src.healer import run_healer

        monkeypatch.setattr("src.healer.heal_stale_in_progress", lambda s, p: 2)
        monkeypatch.setattr("src.healer.heal_branch_no_pr", lambda s, p: 3)
        monkeypatch.setattr("src.healer.heal_cancelled_with_work", lambda s, p: 5)
        monkeypatch.setattr("src.healer.heal_stale_worktrees", lambda s, p: 7)

        out = run_healer(MagicMock())
        assert out == (2, 3, 5, 7)
