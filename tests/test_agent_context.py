"""Tests for agent prompt helpers (project context injection)."""

from types import SimpleNamespace

from src.agent import _project_context_markdown, build_prompt


def test_project_context_markdown_empty_id():
    assert _project_context_markdown("") == ""


def test_project_context_markdown_missing_project(monkeypatch):
    monkeypatch.setattr("src.projects_dynamo.get_project", lambda _pid: None)
    assert _project_context_markdown("any") == ""


def test_project_context_markdown_with_spec(monkeypatch):
    monkeypatch.setattr(
        "src.projects_dynamo.get_project",
        lambda pid: (
            {"title": "  App  ", "spec": "  Do things  "} if pid == "p1" else None
        ),
    )
    md = _project_context_markdown("p1")
    assert "**App**" in md
    assert "Do things" in md


def test_build_prompt_includes_working_dir():
    task = SimpleNamespace(
        title="T",
        description=None,
        tags=None,
        role="",
        project_id="",
    )
    out = build_prompt(task, agent_cwd="/tmp/wt")
    assert "/tmp/wt" in out
    assert "YOUR WORKING DIRECTORY" in out
