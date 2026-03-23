"""Tests for PR verification and PR body generation (mocked git + agent)."""

from types import SimpleNamespace
from unittest.mock import patch

from tests.support import mock_process


@patch("src.pr.run_agent")
@patch("src.pr._run_cmd")
def test_verify_pr_diff_accepts_lgtm(mock_cmd, mock_agent, tmp_tasks):
    from src.pr import _verify_pr_diff

    task = tmp_tasks.create(title="Task title", description="Desc")
    mock_cmd.return_value = mock_process(stdout=" file | 1 +\n")
    cp = SimpleNamespace(returncode=0, stdout="LGTM looks good\n")
    mock_agent.return_value = (cp, 1.0, "", {})

    ok, note = _verify_pr_diff(task, "/tmp/wt")
    assert ok is True
    assert "LGTM" in note or note == "LGTM looks good"


@patch("src.pr.run_agent")
@patch("src.pr._run_cmd")
def test_verify_pr_diff_flags_concern(mock_cmd, mock_agent, tmp_tasks):
    from src.pr import _verify_pr_diff

    task = tmp_tasks.create(title="T")
    mock_cmd.return_value = mock_process(stdout="diff")
    cp = SimpleNamespace(returncode=0, stdout="CONCERN: missing tests\n")
    mock_agent.return_value = (cp, 1.0, "", {})

    ok, note = _verify_pr_diff(task, "/tmp/wt")
    assert ok is False
    assert "CONCERN" in note.upper()


@patch("src.pr.run_agent")
@patch("src.pr._run_cmd")
def test_verify_pr_diff_skips_on_agent_error(mock_cmd, mock_agent, tmp_tasks):
    from src.pr import _verify_pr_diff

    task = tmp_tasks.create(title="T")
    mock_cmd.return_value = mock_process(stdout="x")
    mock_agent.side_effect = RuntimeError("agent down")

    ok, note = _verify_pr_diff(task, "/tmp/wt")
    assert ok is True
    assert "skipped" in note.lower()


@patch("src.pr.run_agent")
@patch("src.pr._run_cmd")
def test_generate_pr_body_uses_agent_when_ok(mock_cmd, mock_agent, tmp_tasks):
    from src.pr import _generate_pr_body

    task = tmp_tasks.create(title="Feature", description="D")
    mock_cmd.return_value = mock_process(stdout="stat")
    cp = SimpleNamespace(returncode=0, stdout='{"result":"## Summary\\nDone."}\n')
    mock_agent.return_value = (cp, 1.0, "", {})

    body = _generate_pr_body(task, "/tmp/wt")
    assert task.id in body
    assert "Summary" in body or "Done" in body


@patch("src.pr.run_agent")
@patch("src.pr._run_cmd")
def test_generate_pr_body_fallback_on_exception(mock_cmd, mock_agent, tmp_tasks):
    from src.pr import _generate_pr_body

    task = tmp_tasks.create(title="Fallback task")
    mock_cmd.return_value = mock_process(stdout=" M x")
    mock_agent.side_effect = RuntimeError("no")

    body = _generate_pr_body(task, "/tmp/wt")
    assert task.id in body
    assert "Automated PR" in body or "Fallback" in body
