"""Tests for src/autopilot.py helpers (lean planner + JSON parsing)."""

import json

import pytest

from src.autopilot import _normalize_items, _parse_plan_json, _quick_stats
from src.task_store import Task, TaskStatus


def test_quick_stats_no_prior_plans_no_tasks():
    s = _quick_stats([], [], [])
    assert "Prior plans: none yet" in s
    assert "Active tasks: none" in s
    assert "Approved proposals with active tasks: 0" in s
    assert "Human tasks (non-terminal): 0" in s


def test_quick_stats_prior_plan_latest_line():
    prior = [
        {
            "plan_date": "2026-03-20",
            "status": "proposed",
            "sk": "PLAN#2026-03-20",
        }
    ]
    s = _quick_stats(prior, [], [])
    assert "Prior plans: 1 total" in s
    assert "2026-03-20" in s
    assert "[proposed]" in s


def test_quick_stats_active_tasks_and_human():
    tasks = [
        Task(id="a", title="t1", status=TaskStatus.PENDING),
        Task(id="b", title="t2", status=TaskStatus.IN_PROGRESS, assignee="human"),
        Task(id="c", title="t3", status=TaskStatus.COMPLETED, assignee="human"),
    ]
    s = _quick_stats([], tasks, [])
    assert "Active tasks: 2" in s
    assert "Human tasks (non-terminal): 1" in s


def test_quick_stats_approved_proposals_counts():
    tasks = [
        Task(id="t1", title="x", status=TaskStatus.PENDING),
        Task(id="t2", title="y", status=TaskStatus.IN_REVIEW),
    ]
    proposals = [
        {"status": "approved", "task_id": "t1"},
        {"status": "approved", "task_id": "t2"},
        {"status": "approved"},  # no task yet
        {"status": "pending", "task_id": "t1"},
    ]
    s = _quick_stats([], tasks, proposals)
    assert "Approved proposals with active tasks: 2" in s


def test_parse_plan_json_raw():
    payload = {"reflection": "ok", "items": [{"title": "Do thing", "description": "", "role": "", "priority": "medium"}]}
    assert _parse_plan_json(json.dumps(payload)) == payload


def test_parse_plan_json_markdown_fence():
    payload = {"reflection": "x", "items": []}
    text = "```json\n%s\n```" % json.dumps(payload)
    assert _parse_plan_json(text) == payload


def test_normalize_items_invalid_role_defaults():
    raw = [
        {
            "title": "  Fix bug  ",
            "description": "desc",
            "role": "not_a_real_role_id",
            "priority": "high",
        }
    ]
    out = _normalize_items(raw)
    assert len(out) == 1
    assert out[0]["role"] == "fullstack_engineer"
    assert out[0]["title"] == "Fix bug"
    assert out[0]["priority"] == "high"


@pytest.mark.parametrize("raw", [None, "x", 1, {}])
def test_normalize_items_non_list_returns_empty(raw):
    assert _normalize_items(raw) == []
