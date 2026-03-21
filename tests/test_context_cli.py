"""Tests for src/context_cli.py — daily cycle context CLI."""

import os
import stat

import src.context_cli as cc
import src.projects_dynamo as pd


def test_main_requires_project(capsys, monkeypatch):
    monkeypatch.delenv("CTX_PROJECT_ID", raising=False)
    rc = cc.main(["spec"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "project id required" in err


def test_tasks_invalid_status(capsys):
    rc = cc.main(["--project", "p1", "tasks", "--status", "nope"])
    assert rc == 1
    assert "invalid --status" in capsys.readouterr().err


def test_tasks_invalid_assignee(capsys):
    rc = cc.main(["--project", "p1", "tasks", "--assignee", "bot"])
    assert rc == 1
    assert "invalid --assignee" in capsys.readouterr().err


def test_proposals_invalid_status(capsys):
    rc = cc.main(["--project", "p1", "proposals", "--status", "done"])
    assert rc == 1
    assert "invalid --status" in capsys.readouterr().err


def test_proposals_pending_empty_ok(monkeypatch, capsys):
    monkeypatch.setattr(cc, "list_proposals", lambda *a, **k: [])
    rc = cc.main(["--project", "p1", "proposals", "--status", "pending"])
    assert rc == 0
    assert "(no proposals)" in capsys.readouterr().out


def test_cmd_memory_save_success(tmp_path, monkeypatch):
    calls = []

    def fake_put(pid, text):
        calls.append((pid, text))

    monkeypatch.setattr(cc, "put_memory", fake_put)
    rc = cc.cmd_memory_save("projA", "hello note", str(tmp_path))
    assert rc == 0
    assert calls == [("projA", "hello note")]
    counter = (tmp_path / cc.MEMORY_COUNTER_FILE).read_text(encoding="utf-8")
    assert counter == "1"


def test_cmd_memory_save_rolls_back_counter_when_put_fails(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ddb down")

    monkeypatch.setattr(cc, "put_memory", boom)
    rc = cc.cmd_memory_save("projA", "x", str(tmp_path))
    assert rc == 1
    # Reserved slot was rolled back from 1 to 0
    p = tmp_path / cc.MEMORY_COUNTER_FILE
    assert p.read_text(encoding="utf-8") == "0"


def test_cmd_memory_save_respects_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "put_memory", lambda *a, **k: None)
    p = tmp_path / cc.MEMORY_COUNTER_FILE
    p.write_text(str(cc.MEMORY_SAVE_MAX_PER_CYCLE), encoding="utf-8")
    rc = cc.cmd_memory_save("projA", "x", str(tmp_path))
    assert rc == 1
    assert p.read_text(encoding="utf-8") == str(cc.MEMORY_SAVE_MAX_PER_CYCLE)


def test_cmd_memory_get_matches_suffix(monkeypatch, capsys):
    monkeypatch.setattr(pd, "get_memory", lambda pid, ref: None)
    monkeypatch.setattr(
        pd,
        "list_memories",
        lambda pid, limit=100: [
            {"sk": "MEMORY#2026-03-20T00:00:00.123456", "content": "body text"},
        ],
    )
    rc = cc.cmd_memory_get("projA", "00.123456")
    assert rc == 0
    out = capsys.readouterr().out
    assert "MEMORY#2026-03-20T00:00:00.123456" in out
    assert "body text" in out


def test_cmd_memory_get_short_substring_not_used(monkeypatch, capsys):
    """Substring match requires len(ref) >= 8."""
    monkeypatch.setattr(pd, "get_memory", lambda pid, ref: None)
    monkeypatch.setattr(
        pd,
        "list_memories",
        lambda pid, limit=100: [
            {"sk": "MEMORY#contains-short-token", "content": "c"},
        ],
    )
    rc = cc.cmd_memory_get("projA", "short")
    assert rc == 1
    assert "memory not found" in capsys.readouterr().err


def test_cmd_memory_get_substring_when_long_enough(monkeypatch, capsys):
    monkeypatch.setattr(pd, "get_memory", lambda pid, ref: None)
    monkeypatch.setattr(
        pd,
        "list_memories",
        lambda pid, limit=100: [
            {"sk": "MEMORY#2026-03-20T12:00:00.999999", "content": "z"},
        ],
    )
    rc = cc.cmd_memory_get("projA", "2026-03-20")
    assert rc == 0
    out = capsys.readouterr().out
    assert "999999" in out.splitlines()[0]


def test_write_ctx_script_creates_executable(tmp_path):
    cc.write_ctx_script(str(tmp_path), "proj-xyz")
    p = tmp_path / "ctx"
    assert p.is_file()
    mode = os.stat(p).st_mode
    assert mode & stat.S_IXUSR
    text = p.read_text(encoding="utf-8")
    assert "CTX_PROJECT_ID" in text
    assert "proj-xyz" in text
    assert "context_cli.py" in text


def test_plans_empty(monkeypatch, capsys):
    monkeypatch.setattr(cc, "list_plans", lambda pid, limit=14: [])
    rc = cc.cmd_plans("p1", 14)
    assert rc == 0
    assert "(no plans yet)" in capsys.readouterr().out


def test_plans_renders_summary(monkeypatch, capsys):
    monkeypatch.setattr(
        cc,
        "list_plans",
        lambda pid, limit=14: [
            {
                "sk": "PLAN#2026-03-20",
                "plan_date": "2026-03-20",
                "status": "completed",
                "reflection": "Shipped",
                "items": [{"title": "a"}],
                "outcome_summary": {"completed": 2, "failed": 0},
            }
        ],
    )
    rc = cc.cmd_plans("p1", 14)
    assert rc == 0
    out = capsys.readouterr().out
    assert "2026-03-20" in out
    assert "completed" in out
    assert "items: 1" in out
    assert "outcomes:" in out
