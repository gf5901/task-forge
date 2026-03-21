"""Tests for the self-healing pipeline runner (src/healer.py)."""

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.task_store import TaskStatus


def _plog(*a, **kw):
    pass


def _make_ts(minutes_ago=0):
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat(timespec="seconds")


def _fake_run_agent(stdout="", returncode=0):
    result = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")
    return lambda *a, **kw: (result, 1.0, "", {})


class TestHealStaleInProgress:
    def test_resets_stale_task(self, tmp_tasks):
        from src.healer import heal_stale_in_progress

        task = tmp_tasks.create(title="Stuck task")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)
        tmp_tasks.set_field(task.id, "updated_at", _make_ts(minutes_ago=60))

        healed = heal_stale_in_progress(tmp_tasks, _plog)
        assert healed == 1
        assert tmp_tasks.get(task.id).status == TaskStatus.PENDING

    def test_ignores_fresh_in_progress(self, tmp_tasks):
        from src.healer import heal_stale_in_progress

        task = tmp_tasks.create(title="Fresh task")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)

        healed = heal_stale_in_progress(tmp_tasks, _plog)
        assert healed == 0
        assert tmp_tasks.get(task.id).status == TaskStatus.IN_PROGRESS

    def test_adds_healer_note_to_file(self, tmp_tasks):
        from src.healer import heal_stale_in_progress

        task = tmp_tasks.create(title="Stuck")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)
        tmp_tasks.set_field(task.id, "updated_at", _make_ts(minutes_ago=90))

        heal_stale_in_progress(tmp_tasks, _plog)
        assert tmp_tasks.has_section(task.id, "Healer Note")

    def test_ignores_subtasks(self, tmp_tasks):
        from src.healer import heal_stale_in_progress

        parent = tmp_tasks.create(title="Parent")
        sub = tmp_tasks.create(title="Sub", parent_id=parent.id)
        tmp_tasks.update_status(sub.id, TaskStatus.IN_PROGRESS)
        tmp_tasks.set_field(sub.id, "updated_at", _make_ts(minutes_ago=60))

        healed = heal_stale_in_progress(tmp_tasks, _plog)
        assert healed == 0


class TestHasMeaningfulAgentOutput:
    def test_empty_is_not_meaningful(self, tmp_tasks):
        from src.healer import _has_meaningful_agent_output

        task = tmp_tasks.create(title="T")
        assert not _has_meaningful_agent_output(tmp_tasks, task)

    def test_real_output_is_meaningful(self, tmp_tasks):
        from src.healer import _has_meaningful_agent_output
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="T")
        _append_text_to_task(
            tmp_tasks, task, "Agent Output", "I updated the file and fixed the bug."
        )
        assert _has_meaningful_agent_output(tmp_tasks, task)

    def test_timeout_only_is_not_meaningful(self, tmp_tasks):
        from src.healer import _has_meaningful_agent_output
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="T")
        _append_text_to_task(tmp_tasks, task, "Agent Output", "**Timed out after 600s**")
        assert not _has_meaningful_agent_output(tmp_tasks, task)


class TestHealBranchNoPr:
    def test_skips_task_with_no_remote_branch(self, tmp_tasks, monkeypatch):
        from src.healer import heal_branch_no_pr

        task = tmp_tasks.create(title="No branch")
        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: False)

        healed = heal_branch_no_pr(tmp_tasks, _plog)
        assert healed == 0

    def test_skips_task_that_already_has_pr(self, tmp_tasks, monkeypatch):
        from src.healer import heal_branch_no_pr
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="Has PR")
        tmp_tasks.update_status(task.id, TaskStatus.IN_REVIEW)
        _append_text_to_task(tmp_tasks, task, "PR Created", "https://github.com/org/repo/pull/1")
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: True)

        healed = heal_branch_no_pr(tmp_tasks, _plog)
        assert healed == 0

    def test_backfills_existing_pr_url(self, tmp_tasks, monkeypatch):
        from src.healer import heal_branch_no_pr

        task = tmp_tasks.create(title="Existing PR on GitHub")
        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: True)
        monkeypatch.setattr(
            "src.healer._open_pr_for_branch", lambda b: "https://github.com/org/repo/pull/99"
        )

        healed = heal_branch_no_pr(tmp_tasks, _plog)
        assert healed == 1
        assert tmp_tasks.get(task.id).status == TaskStatus.IN_REVIEW
        assert tmp_tasks.has_section(task.id, "PR Created")

    def test_creates_pr_when_none_exists(self, tmp_tasks, monkeypatch):
        from src.healer import heal_branch_no_pr

        task = tmp_tasks.create(title="Needs PR", target_repo="")
        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: True)
        monkeypatch.setattr("src.healer._open_pr_for_branch", lambda b: None)
        # patch at the source module since healer imports them from runner
        monkeypatch.setattr("src.runner._get_default_branch", lambda d: "main")
        monkeypatch.setattr(
            "src.runner._resolve_repo_dir", lambda t: str(Path(__file__).parent.parent)
        )
        fake_pr = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://github.com/org/repo/pull/42", stderr=""
        )
        monkeypatch.setattr("src.healer._run", lambda *a, **kw: fake_pr)

        healed = heal_branch_no_pr(tmp_tasks, _plog)
        assert healed == 1
        assert tmp_tasks.get(task.id).status == TaskStatus.IN_REVIEW
        assert "pull/42" in (tmp_tasks.get_pr_url(task.id) or "")

    def test_handles_pr_creation_failure_gracefully(self, tmp_tasks, monkeypatch):
        from src.healer import heal_branch_no_pr

        task = tmp_tasks.create(title="PR fail")
        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: True)
        monkeypatch.setattr("src.healer._open_pr_for_branch", lambda b: None)
        monkeypatch.setattr("src.runner._get_default_branch", lambda d: "main")
        monkeypatch.setattr(
            "src.runner._resolve_repo_dir", lambda t: str(Path(__file__).parent.parent)
        )
        fail_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="gh: error"
        )
        monkeypatch.setattr("src.healer._run", lambda *a, **kw: fail_result)

        healed = heal_branch_no_pr(tmp_tasks, _plog)
        assert healed == 0
        assert tmp_tasks.get(task.id).status == TaskStatus.COMPLETED


class TestHealCancelledWithWork:
    def test_skips_cancelled_without_output(self, tmp_tasks, monkeypatch):
        from src.healer import heal_cancelled_with_work

        task = tmp_tasks.create(title="No output")
        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)

        called = []
        monkeypatch.setattr(
            "src.runner.run_agent", lambda *a, **kw: called.append(1) or (None, 0, "", {})
        )

        heal_cancelled_with_work(tmp_tasks, _plog)
        assert not called

    def test_rerun_decision_resets_to_pending(self, tmp_tasks, monkeypatch):
        from src.healer import heal_cancelled_with_work
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="Failed early")
        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)
        _append_text_to_task(
            tmp_tasks, task, "Agent Output", "I started working but hit an error on line 5."
        )

        monkeypatch.setattr("src.runner.run_agent", _fake_run_agent("RERUN"))
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: False)

        healed = heal_cancelled_with_work(tmp_tasks, _plog)
        assert healed == 1
        assert tmp_tasks.get(task.id).status == TaskStatus.PENDING

    def test_completed_decision_marks_completed(self, tmp_tasks, monkeypatch):
        from src.healer import heal_cancelled_with_work
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="Investigation done")
        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)
        _append_text_to_task(
            tmp_tasks,
            task,
            "Agent Output",
            "The bug is in auth.py line 42. No code changes needed.",
        )

        monkeypatch.setattr("src.runner.run_agent", _fake_run_agent("COMPLETED"))
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: False)

        healed = heal_cancelled_with_work(tmp_tasks, _plog)
        assert healed == 1
        assert tmp_tasks.get(task.id).status == TaskStatus.COMPLETED

    def test_skips_already_healed_task(self, tmp_tasks, monkeypatch):
        from src.healer import heal_cancelled_with_work
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="Already healed")
        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)
        _append_text_to_task(tmp_tasks, task, "Agent Output", "Did some work.")
        tmp_tasks.append_section(task.id, "Healer Note", "Already processed.")

        called = []
        monkeypatch.setattr(
            "src.runner.run_agent", lambda *a, **kw: called.append(1) or (None, 0, "", {})
        )

        heal_cancelled_with_work(tmp_tasks, _plog)
        assert not called

    def test_skips_user_cancelled_task(self, tmp_tasks, monkeypatch):
        """Tasks cancelled by the user must never be re-queued by the healer."""
        from src.healer import heal_cancelled_with_work
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="User cancelled")
        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)
        tmp_tasks.set_cancelled_by(task.id, "user")
        _append_text_to_task(tmp_tasks, task, "Agent Output", "Did some work before cancellation.")
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: False)

        called = []
        monkeypatch.setattr(
            "src.runner.run_agent", lambda *a, **kw: called.append(1) or (None, 0, "", {})
        )

        healed = heal_cancelled_with_work(tmp_tasks, _plog)
        assert healed == 0
        assert not called
        assert tmp_tasks.get(task.id).status == TaskStatus.CANCELLED

    def test_diagnosis_failure_is_graceful(self, tmp_tasks, monkeypatch):
        from src.healer import heal_cancelled_with_work
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="Diagnosis fails")
        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)
        _append_text_to_task(tmp_tasks, task, "Agent Output", "Some work done.")

        monkeypatch.setattr(
            "src.runner.run_agent", lambda *a, **kw: (_ for _ in ()).throw(Exception("unavailable"))
        )
        monkeypatch.setattr("src.healer._remote_branch_exists", lambda b: False)

        healed = heal_cancelled_with_work(tmp_tasks, _plog)
        assert healed == 0
        assert tmp_tasks.get(task.id).status == TaskStatus.CANCELLED
