"""Additional PR module tests: sensitive files, wrong-dir sentinel, CI polling."""

import json
from unittest.mock import patch

from tests.support import attach_pr_mocks, git_cmd_side_effect, mock_process


class TestCheckSensitiveFiles:
    def test_override_tag_skips_check(self, tmp_tasks):
        from src.pr import _check_sensitive_files

        task = tmp_tasks.create(title="t", tags=["allow-sensitive-files"])
        diff = mock_process(stdout=".env\n")
        with patch("src.pr._run_cmd", return_value=diff):
            assert _check_sensitive_files(task, "/tmp/wt") == []

    def test_blocks_env_file(self, tmp_tasks):
        from src.pr import _check_sensitive_files

        task = tmp_tasks.create(title="t")
        diff = mock_process(stdout=".env\n")
        with patch("src.pr._run_cmd", return_value=diff):
            assert _check_sensitive_files(task, "/tmp/wt") == [".env"]


class TestCommitWrongDir:
    @patch("src.pr._verify_pr_diff", return_value=(True, "LGTM"))
    @patch("src.pr._run_cmd")
    def test_wrong_dir_sentinel_when_main_has_changes(
        self, mock_cmd, mock_verify, tmp_tasks, monkeypatch
    ):
        from src.pr import WRONG_DIR_SENTINEL, commit_and_create_pr

        store = attach_pr_mocks(tmp_tasks)
        task = tmp_tasks.create(title="Wrong dir", target_repo="task-forge")

        rules = [
            (
                lambda c, wd, j: "status --porcelain" in j and wd == "/tmp/wt",
                mock_process(),
            ),
            (
                lambda c, wd, j: "status --porcelain" in j and wd != "/tmp/wt",
                mock_process(stdout="M other.py"),
            ),
        ]
        mock_cmd.side_effect = git_cmd_side_effect(rules)
        monkeypatch.setattr("src.pr._resolve_repo_dir", lambda t: "/main/checkout")

        result = commit_and_create_pr(store, task, "/tmp/wt")
        assert result == WRONG_DIR_SENTINEL
        store.set_pr_url.assert_not_called()


class TestPollCiStatus:
    @patch("time.monotonic", return_value=0.0)
    @patch("time.sleep")
    @patch("src.pr._run_cmd")
    def test_gh_error_returns_none_summary(self, mock_cmd, mock_sleep, mock_mono):
        from src.pr import poll_ci_status

        mock_cmd.return_value = mock_process(returncode=1, stderr="err")
        ok, summary = poll_ci_status("42", "/repo", timeout=5)
        assert ok is True
        assert summary is None
        mock_sleep.assert_called()

    @patch("time.monotonic", return_value=0.0)
    @patch("time.sleep")
    @patch("src.pr._run_cmd")
    def test_failed_check_returns_false(self, mock_cmd, mock_sleep, mock_mono):
        from src.pr import poll_ci_status

        failed = json.dumps(
            [{"state": "FAILURE", "name": "build", "conclusion": None}]
        )
        mock_cmd.return_value = mock_process(stdout=failed)
        ok, summary = poll_ci_status("7", "/repo", timeout=30)
        assert ok is False
        assert "failed" in (summary or "").lower()
        assert "build" in (summary or "")

    @patch("time.monotonic", return_value=0.0)
    @patch("time.sleep")
    @patch("src.pr._run_cmd")
    def test_empty_checks_returns_none_summary(self, mock_cmd, mock_sleep, mock_mono):
        from src.pr import poll_ci_status

        mock_cmd.return_value = mock_process(stdout="[]")
        ok, summary = poll_ci_status("1", "/repo", timeout=30)
        assert ok is True
        assert summary is None

    @patch("time.monotonic", return_value=0.0)
    @patch("time.sleep")
    @patch("src.pr._run_cmd")
    def test_all_checks_passed_message(self, mock_cmd, mock_sleep, mock_mono):
        from src.pr import poll_ci_status

        payload = json.dumps(
            [{"state": "SUCCESS", "name": "ci", "conclusion": None}]
        )
        mock_cmd.return_value = mock_process(stdout=payload)
        ok, summary = poll_ci_status("3", "/repo", timeout=30)
        assert ok is True
        assert summary and "passed" in summary.lower()

    @patch("time.monotonic", side_effect=[0.0, 0.0, 100.0])
    @patch("time.sleep")
    @patch("src.pr._run_cmd")
    def test_timeout_returns_still_running_message(self, mock_cmd, mock_sleep, mock_mono):
        from src.pr import poll_ci_status

        pending = json.dumps([{"state": "PENDING", "name": "slow"}])
        mock_cmd.return_value = mock_process(stdout=pending)
        ok, summary = poll_ci_status("9", "/repo", timeout=5)
        assert ok is True
        assert summary and "timed out" in summary.lower()
