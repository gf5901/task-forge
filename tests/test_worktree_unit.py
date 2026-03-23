"""Unit tests for worktree path helpers (no git network)."""

from src.worktree import _resolve_repo_dir, _slugify_branch


def test_slugify_branch_strips_and_limits():
    assert _slugify_branch("  Hello World!  ") == "hello-world"
    assert _slugify_branch("a" * 100) == "a" * 40


def test_resolve_repo_dir_workspace_sibling(tmp_tasks, tmp_path, monkeypatch):
    monkeypatch.setattr("src.worktree.WORKSPACE_DIR", tmp_path)
    name = "sidecar-repo"
    repo = tmp_path / name
    repo.mkdir()
    (repo / ".git").mkdir()
    task = tmp_tasks.create(title="t", target_repo=name)
    assert _resolve_repo_dir(task) == str(repo)


def test_resolve_repo_dir_falls_back_to_project_root(tmp_tasks, tmp_path, monkeypatch):
    from src import worktree as wt

    monkeypatch.setattr("src.worktree.WORKSPACE_DIR", tmp_path)
    task = tmp_tasks.create(title="t", target_repo="nonexistent-repo-xyz")
    assert _resolve_repo_dir(task) == str(wt.PROJECT_ROOT)
