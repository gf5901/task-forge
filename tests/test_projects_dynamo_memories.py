"""Tests for memory helpers in src/projects_dynamo.py."""

import src.projects_dynamo as pd


def test_resolve_memory_by_ref_returns_get_memory_result(monkeypatch):
    hit = {"sk": "MEMORY#x", "content": "direct"}

    def fake_get(pid, ref):
        return hit if ref == "MEMORY#x" else None

    monkeypatch.setattr(pd, "get_memory", fake_get)
    monkeypatch.setattr(pd, "list_memories", lambda *a, **k: [])

    assert pd.resolve_memory_by_ref("p", "MEMORY#x") == hit


def test_resolve_memory_by_ref_suffix_from_list(monkeypatch):
    monkeypatch.setattr(pd, "get_memory", lambda pid, ref: None)
    monkeypatch.setattr(
        pd,
        "list_memories",
        lambda pid, limit=100: [{"sk": "MEMORY#abc-def-ghi", "content": "z"}],
    )
    out = pd.resolve_memory_by_ref("p", "def-ghi")
    assert out and out["content"] == "z"


def test_resolve_memory_by_ref_empty_and_whitespace():
    assert pd.resolve_memory_by_ref("p", "") is None
    assert pd.resolve_memory_by_ref("p", "   ") is None
