"""Tests for PR URL storage and model stamping in the pipeline."""

import subprocess
from unittest.mock import MagicMock, patch

from src.task_store import TaskStatus


class TestSetPrUrl:
    """set_pr_url persists on DynamoTaskStore and is retrievable."""

    def test_set_and_get(self, tmp_tasks):
        task = tmp_tasks.create(title="PR task")
        tmp_tasks.set_pr_url(task.id, "https://github.com/user/repo/pull/99")
        assert tmp_tasks.get_pr_url(task.id) == "https://github.com/user/repo/pull/99"

    def test_overwrite(self, tmp_tasks):
        task = tmp_tasks.create(title="PR task")
        tmp_tasks.set_pr_url(task.id, "https://github.com/user/repo/pull/1")
        tmp_tasks.set_pr_url(task.id, "https://github.com/user/repo/pull/2")
        assert tmp_tasks.get_pr_url(task.id) == "https://github.com/user/repo/pull/2"

    def test_nonexistent_task(self, tmp_tasks):
        # set_field on missing task is a no-op (no crash)
        tmp_tasks.set_pr_url("nonexistent", "https://github.com/user/repo/pull/1")

    def test_survives_status_update(self, tmp_tasks):
        task = tmp_tasks.create(title="PR task")
        tmp_tasks.set_pr_url(task.id, "https://github.com/user/repo/pull/42")
        tmp_tasks.update_status(task.id, TaskStatus.IN_REVIEW)
        assert tmp_tasks.get_pr_url(task.id) == "https://github.com/user/repo/pull/42"


def _make_run_cmd(return_map=None):
    """Build a mock _run_cmd that returns different results based on the first arg."""
    defaults = MagicMock(returncode=0, stdout="", stderr="")

    def side_effect(cmd, cwd=None, timeout=None):
        if return_map:
            for key, val in return_map.items():
                if key in cmd or (isinstance(cmd, list) and any(key in str(c) for c in cmd)):
                    return val
        return defaults

    return side_effect


class TestCommitAndCreatePr:
    """commit_and_create_pr stores pr_url on success, skips on no-changes."""

    def _make_store(self, tmp_tasks):
        store = tmp_tasks
        store.set_pr_url = MagicMock()
        store.append_section = MagicMock()
        return store

    @patch("src.pr._verify_pr_diff", return_value=(True, "LGTM"))
    @patch("src.pr._wait_for_pr_ci", return_value=True)
    @patch("src.pr._generate_pr_body", return_value="body")
    @patch("src.pr._run_cmd")
    @patch("src.pr.run_agent")
    def test_sets_pr_url_on_success(
        self, mock_agent, mock_cmd, mock_body, mock_ci, mock_verify, tmp_tasks
    ):
        from src.pr import commit_and_create_pr

        store = self._make_store(tmp_tasks)
        task = tmp_tasks.create(title="Test task", target_repo="task-forge")

        status_result = MagicMock(returncode=0, stdout="M file.py", stderr="")
        add_result = MagicMock(returncode=0, stdout="", stderr="")
        commit_result = MagicMock(returncode=0, stdout="", stderr="")
        branch_result = MagicMock(returncode=0, stdout="task/abc-test", stderr="")
        push_result = MagicMock(returncode=0, stdout="", stderr="")
        pr_result = MagicMock(
            returncode=0,
            stdout="https://github.com/user/repo/pull/99\n",
            stderr="",
        )
        default_branch = MagicMock(returncode=0, stdout="main", stderr="")
        blocked_result = MagicMock(returncode=0, stdout="", stderr="")

        def side_effect(cmd, cwd=None, timeout=None):
            if isinstance(cmd, list):
                joined = " ".join(str(c) for c in cmd)
                if "status --porcelain" in joined:
                    return status_result
                if "add -A" in joined:
                    return add_result
                if "diff --cached --name-only" in joined:
                    return blocked_result
                if "commit -m" in joined:
                    return commit_result
                if "rev-parse --abbrev-ref HEAD" in joined:
                    return branch_result
                if "push -u" in joined:
                    return push_result
                if "pr create" in joined:
                    return pr_result
                if "rev-parse --show-toplevel" in joined:
                    return MagicMock(returncode=0, stdout="/tmp/repo", stderr="")
                if "symbolic-ref" in joined:
                    return default_branch
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_cmd.side_effect = side_effect

        result = commit_and_create_pr(store, task, "/tmp/wt")
        assert result == "https://github.com/user/repo/pull/99"
        store.set_pr_url.assert_called_once_with(task.id, "https://github.com/user/repo/pull/99")

    @patch("src.pr._run_cmd")
    def test_no_changes_skips_pr_url(self, mock_cmd, tmp_tasks):
        from src.pr import NO_CHANGES_SENTINEL, commit_and_create_pr

        store = self._make_store(tmp_tasks)
        task = tmp_tasks.create(title="Clean task", target_repo="task-forge")

        # Worktree is clean
        mock_cmd.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = commit_and_create_pr(store, task, "/tmp/wt")
        assert result == NO_CHANGES_SENTINEL
        store.set_pr_url.assert_not_called()

    @patch("src.pr._check_sensitive_files", return_value=[])
    @patch("src.pr._run_cmd")
    def test_push_failed_skips_pr_url(self, mock_cmd, mock_sensitive, tmp_tasks):
        from src.pr import PUSH_FAILED_SENTINEL, commit_and_create_pr

        store = self._make_store(tmp_tasks)
        task = tmp_tasks.create(title="Push fail", target_repo="task-forge")

        call_count = {"n": 0}

        def side_effect(cmd, cwd=None, timeout=None):
            call_count["n"] += 1
            joined = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
            if "status --porcelain" in joined:
                return MagicMock(returncode=0, stdout="M file.py", stderr="")
            if "add -A" in joined:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "diff --cached --name-only" in joined:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "commit -m" in joined:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "rev-parse --abbrev-ref HEAD" in joined:
                return MagicMock(returncode=0, stdout="task/abc-test", stderr="")
            if "push -u" in joined:
                return MagicMock(returncode=1, stdout="", stderr="push failed")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_cmd.side_effect = side_effect

        result = commit_and_create_pr(store, task, "/tmp/wt")
        assert result == PUSH_FAILED_SENTINEL
        store.set_pr_url.assert_not_called()


class TestModelStamping:
    """_run_one_inner stamps the resolved model after execution."""

    @patch("src.pipeline.trigger_unblocked_dependents")
    @patch("src.pipeline._maybe_finalize_directive_batch")
    @patch("src.pipeline.commit_and_create_pr")
    @patch("src.pipeline.run_agent")
    @patch("src.pipeline.create_worktree", return_value="/tmp/wt")
    @patch("src.pipeline.ensure_repo")
    @patch("src.pipeline.cleanup_worktree")
    @patch("src.pipeline.plog")
    def test_stamps_model_after_execution(
        self,
        mock_plog,
        mock_cleanup,
        mock_ensure,
        mock_create_wt,
        mock_agent,
        mock_pr,
        mock_finalize,
        mock_unblock,
        tmp_tasks,
        monkeypatch,
    ):
        from src.pipeline import _run_one_inner

        task = tmp_tasks.create(title="Model test", priority="high")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)
        task = tmp_tasks.get(task.id)

        mock_agent.return_value = (
            subprocess.CompletedProcess(args=[], returncode=0, stdout="done"),
            10.0,
            "",
            {},
        )
        mock_pr.return_value = None  # no PR

        monkeypatch.setattr("src.pipeline.AUTO_PLAN", False)
        monkeypatch.setattr("src.pipeline.AUTO_DOCS", False)
        monkeypatch.setattr("src.pipeline.AUTO_PR", False)
        monkeypatch.setattr("src.pipeline._resolve_model", lambda t: "claude-4.6-sonnet-medium")

        _run_one_inner(tmp_tasks, task)

        updated = tmp_tasks.get(task.id)
        assert updated.model == "claude-4.6-sonnet-medium"

    @patch("src.pipeline.trigger_unblocked_dependents")
    @patch("src.pipeline._maybe_finalize_directive_batch")
    @patch("src.pipeline.run_agent")
    @patch("src.pipeline.create_worktree", return_value="/tmp/wt")
    @patch("src.pipeline.ensure_repo")
    @patch("src.pipeline.cleanup_worktree")
    @patch("src.pipeline.plog")
    def test_no_stamp_when_model_is_none(
        self,
        mock_plog,
        mock_cleanup,
        mock_ensure,
        mock_create_wt,
        mock_agent,
        mock_finalize,
        mock_unblock,
        tmp_tasks,
        monkeypatch,
    ):
        from src.pipeline import _run_one_inner

        task = tmp_tasks.create(title="No model test")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)
        task = tmp_tasks.get(task.id)

        mock_agent.return_value = (
            subprocess.CompletedProcess(args=[], returncode=0, stdout="done"),
            10.0,
            "",
            {},
        )

        monkeypatch.setattr("src.pipeline.AUTO_PLAN", False)
        monkeypatch.setattr("src.pipeline.AUTO_DOCS", False)
        monkeypatch.setattr("src.pipeline.AUTO_PR", False)
        monkeypatch.setattr("src.pipeline._resolve_model", lambda t: None)

        _run_one_inner(tmp_tasks, task)

        updated = tmp_tasks.get(task.id)
        assert updated.model == ""
