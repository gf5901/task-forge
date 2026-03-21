"""Tests for src/pr_review.py — gh helpers and task matching."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import src.pr_review as pr_review


class TestListOpenPrs:
    def test_returns_empty_on_gh_failure(self, monkeypatch):
        r = SimpleNamespace(returncode=1, stdout="", stderr="no gh")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        assert pr_review._list_open_prs("/repo") == []

    def test_returns_empty_on_bad_json(self, monkeypatch):
        r = SimpleNamespace(returncode=0, stdout="not json", stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        assert pr_review._list_open_prs("/repo") == []

    def test_returns_prs(self, monkeypatch):
        payload = [{"number": 1, "title": "T"}]
        r = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        out = pr_review._list_open_prs("/repo")
        assert len(out) == 1
        assert out[0]["number"] == 1


class TestGetPrDiff:
    def test_failure_returns_placeholder(self, monkeypatch):
        r = SimpleNamespace(returncode=1, stdout="", stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        stats, body = pr_review._get_pr_diff(1, "/repo")
        assert stats == "(diff unavailable)"
        assert body == ""

    def test_success_truncates(self, monkeypatch):
        diff_lines = ["diff --git a", "--- a", "+++ b", "@@ x @@"] + ["line"] * 9000
        big = "\n".join(diff_lines)
        r = SimpleNamespace(returncode=0, stdout=big, stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        stats, body = pr_review._get_pr_diff(2, "/repo")
        assert "diff --git" in stats
        assert len(body) <= 8000


class TestGetPrCiStatus:
    def test_failure(self, monkeypatch):
        r = SimpleNamespace(returncode=1, stdout="", stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        assert pr_review._get_pr_ci_status(1, "/r") == "No CI data available"

    def test_bad_json(self, monkeypatch):
        r = SimpleNamespace(returncode=0, stdout="x", stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        assert pr_review._get_pr_ci_status(1, "/r") == "No CI data available"

    def test_empty_checks(self, monkeypatch):
        r = SimpleNamespace(returncode=0, stdout="[]", stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        assert pr_review._get_pr_ci_status(1, "/r") == "No CI checks found"

    def test_lists_checks(self, monkeypatch):
        payload = [{"name": "ci", "state": "SUCCESS"}]
        r = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        out = pr_review._get_pr_ci_status(1, "/r")
        assert "ci: SUCCESS" in out


class TestFindTaskForPr:
    def test_body_markdown_task_id(self):
        store = MagicMock()
        task = SimpleNamespace(id="abc12345", title="T")
        store.get.return_value = task
        pr = {
            "body": "Foo **Task ID:** `abc12345` bar",
            "headRefName": "task/abc12345-slug",
        }
        assert pr_review._find_task_for_pr(store, pr) is task

    def test_body_plain_task_id(self):
        store = MagicMock()
        task = SimpleNamespace(id="deadbeef12", title="T")
        store.get.return_value = task
        pr = {"body": "Task ID: deadbeef12", "headRefName": "feature"}
        assert pr_review._find_task_for_pr(store, pr) is task

    def test_branch_fallback(self):
        store = MagicMock()
        task = SimpleNamespace(id="abcd1234", title="T")
        store.get.return_value = task
        pr = {"body": "", "headRefName": "task/abcd1234-my-branch"}
        assert pr_review._find_task_for_pr(store, pr) is task

    def test_none_when_unmatched(self):
        store = MagicMock()
        store.get.return_value = None
        pr = {"body": "", "headRefName": "main"}
        assert pr_review._find_task_for_pr(store, pr) is None


class TestAlreadyReviewedRecently:
    def test_false_on_gh_failure(self, monkeypatch):
        r = SimpleNamespace(returncode=1, stdout="", stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        assert pr_review._already_reviewed_recently(1, "/r") is False

    def test_detects_verdict(self, monkeypatch):
        r = SimpleNamespace(returncode=0, stdout="LGTM: good\n", stderr="")
        monkeypatch.setattr(pr_review, "_run_cmd", lambda *a, **k: r)
        assert pr_review._already_reviewed_recently(1, "/r") is True


def test_review_pr_skips_bot_author(monkeypatch):
    monkeypatch.setattr(pr_review, "_already_reviewed_recently", lambda *a: False)
    store = MagicMock()
    pr = {
        "number": 1,
        "title": "T",
        "headRefName": "main",
        "author": {"login": "dependabot[bot]"},
        "url": "u",
        "body": "",
    }
    assert pr_review.review_pr(store, pr, "/repo") is None


def test_review_pr_skips_already_reviewed(monkeypatch):
    monkeypatch.setattr(pr_review, "_already_reviewed_recently", lambda *a: True)
    store = MagicMock()
    pr = {
        "number": 1,
        "title": "T",
        "headRefName": "main",
        "author": {"login": "human"},
        "url": "u",
        "body": "",
    }
    assert pr_review.review_pr(store, pr, "/repo") is None


def test_review_pr_timeout(monkeypatch):
    monkeypatch.setattr(pr_review, "_already_reviewed_recently", lambda *a: False)
    monkeypatch.setattr(pr_review, "_get_pr_diff", lambda *a: ("", ""))
    monkeypatch.setattr(pr_review, "_get_pr_ci_status", lambda *a: "")

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="agent", timeout=1)

    monkeypatch.setattr(pr_review, "run_agent", _timeout)
    store = MagicMock()
    pr = {
        "number": 9,
        "title": "T",
        "headRefName": "main",
        "author": {"login": "human"},
        "url": "u",
        "body": "",
    }
    assert pr_review.review_pr(store, pr, "/repo") is None


def test_review_pr_agent_failure(monkeypatch):
    monkeypatch.setattr(pr_review, "_already_reviewed_recently", lambda *a: False)
    monkeypatch.setattr(pr_review, "_get_pr_diff", lambda *a: ("", ""))
    monkeypatch.setattr(pr_review, "_get_pr_ci_status", lambda *a: "")
    res = SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(
        pr_review,
        "run_agent",
        lambda *a, **k: (res, 1.0, None, None),
    )
    store = MagicMock()
    pr = {
        "number": 3,
        "title": "T",
        "headRefName": "main",
        "author": {"login": "human"},
        "url": "u",
        "body": "",
    }
    assert pr_review.review_pr(store, pr, "/repo") is None


def test_review_pr_needs_work_appends_comment(monkeypatch):
    monkeypatch.setattr(pr_review, "_already_reviewed_recently", lambda *a: False)
    monkeypatch.setattr(pr_review, "_get_pr_diff", lambda *a: ("stats", "diff"))
    monkeypatch.setattr(pr_review, "_get_pr_ci_status", lambda *a: "ok")
    monkeypatch.setattr(pr_review, "_post_gh_pr_comment", lambda *a: True)
    monkeypatch.setattr(pr_review, "_append_text_to_task", lambda *a: None)

    res = SimpleNamespace(returncode=0, stdout="NEEDS_WORK: fix x", stderr="")
    monkeypatch.setattr(
        pr_review,
        "run_agent",
        lambda *a, **k: (res, 1.0, None, None),
    )
    monkeypatch.setattr(pr_review, "_extract_agent_text", lambda stdout: "NEEDS_WORK: fix x")

    task = SimpleNamespace(id="tid", title="Task", description="d")
    store = MagicMock()
    store.get.return_value = task

    pr = {
        "number": 5,
        "title": "PR",
        "headRefName": "main",
        "author": {"login": "human"},
        "url": "u",
        "body": "**Task ID:** `tid`",
    }
    verdict = pr_review.review_pr(store, pr, "/repo")
    assert verdict.startswith("NEEDS_WORK")
    store.add_comment.assert_called_once()


def test_run_pr_review_no_prs(monkeypatch):
    monkeypatch.setattr(pr_review, "_list_open_prs", lambda d: [])
    monkeypatch.setattr(pr_review, "_get_repo_dir", lambda: "/r")
    store = MagicMock()
    assert pr_review.run_pr_review(store) == []


def test_run_pr_review_handles_exception(monkeypatch):
    def bad_review(*a, **k):
        raise RuntimeError("no gh")

    monkeypatch.setattr(pr_review, "_list_open_prs", lambda d: [{"number": 1, "title": "T"}])
    monkeypatch.setattr(pr_review, "_get_repo_dir", lambda: "/r")
    monkeypatch.setattr(pr_review, "review_pr", bad_review)
    store = MagicMock()
    out = pr_review.run_pr_review(store)
    assert len(out) == 1
    assert out[0]["verdict"] is None
