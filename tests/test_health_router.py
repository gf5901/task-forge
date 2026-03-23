"""Tests for src/routers/health.py."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.routers.health import _last_log_timestamp
from src.web import app


def test_last_log_timestamp_missing_file(tmp_path):
    assert _last_log_timestamp(tmp_path / "missing.log") == ""


def test_last_log_timestamp_empty_file(tmp_path):
    p = tmp_path / "empty.log"
    p.write_bytes(b"")
    assert _last_log_timestamp(p) == ""


def test_last_log_timestamp_json_line(tmp_path):
    p = tmp_path / "pipeline.log"
    p.write_text('{"ts":"2020-01-01T00:00:00Z","event":"x"}\n')
    assert _last_log_timestamp(p) == "2020-01-01T00:00:00Z"


def test_last_log_timestamp_fallback_line(tmp_path):
    p = tmp_path / "plain.log"
    p.write_text("not-json-line-here\n")
    out = _last_log_timestamp(p)
    assert len(out) <= 30
    assert out


def test_last_log_timestamp_uses_last_non_json_line(tmp_path):
    p = tmp_path / "mixed.log"
    p.write_text("older-line\nlast-line-plain\n")
    assert _last_log_timestamp(p) == "last-line-plain"


def test_last_log_timestamp_skips_frontmatter_dashes(tmp_path):
    p = tmp_path / "fm.log"
    p.write_text("---\nuse-this-line\n")
    out = _last_log_timestamp(p)
    assert "use-this" in out


def test_last_log_timestamp_open_oserror(tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("denied")

    monkeypatch.setattr("builtins.open", boom)
    assert _last_log_timestamp(tmp_path / "any.log") == ""


def test_api_health(monkeypatch):
    monkeypatch.setattr("src.web.store", MagicMock(list_tasks=lambda: []))
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "disk_free_pct" in data
    assert data["task_counts"]["pending"] == 0
    assert set(data["task_counts"].keys()) == {
        "pending",
        "in_progress",
        "in_review",
        "completed",
        "failed",
        "cancelled",
    }


def test_api_health_survives_store_error(monkeypatch):
    def boom():
        raise RuntimeError("ddb")

    monkeypatch.setattr("src.web.store", MagicMock(list_tasks=boom))
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["task_counts"] == {}
