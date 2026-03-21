"""Tests for src/budget.py — cost estimation and budget enforcement."""

import json
from datetime import datetime, timezone

import src.budget as budget
from src.budget import _get_budget_cap, budget_status, daily_spend, estimate_cost, within_budget


class TestEstimateCost:
    def test_known_values(self):
        usage = {
            "inputTokens": 1_000_000,
            "outputTokens": 1_000_000,
            "cacheReadTokens": 1_000_000,
            "cacheWriteTokens": 1_000_000,
        }
        cost = estimate_cost(usage)
        expected = 3.00 + 15.00 + 0.30 + 3.75
        assert abs(cost - expected) < 0.01

    def test_empty_usage(self):
        assert estimate_cost({}) == 0.0

    def test_partial_usage(self):
        usage = {"outputTokens": 500_000}
        cost = estimate_cost(usage)
        assert abs(cost - 7.50) < 0.01

    def test_zero_tokens(self):
        usage = {"inputTokens": 0, "outputTokens": 0}
        assert estimate_cost(usage) == 0.0


class TestDailySpend:
    def test_only_counts_today(self, tmp_log):
        today = datetime.now(timezone.utc).date().isoformat()
        yesterday = "2020-01-01"

        entries = [
            {
                "ts": "%sT10:00:00+00:00" % today,
                "task_id": "t1",
                "event": "done",
                "stage": "execute",
                "message": "",
                "extra": {"outputTokens": 1_000_000},
            },
            {
                "ts": "%sT10:00:00+00:00" % yesterday,
                "task_id": "t2",
                "event": "done",
                "stage": "execute",
                "message": "",
                "extra": {"outputTokens": 1_000_000},
            },
        ]
        for e in entries:
            tmp_log.open("a").write(json.dumps(e) + "\n")

        spent = daily_spend()
        # Only today's entry counts: 1M output tokens * $15/M = $15
        assert abs(spent - 15.0) < 0.01

    def test_zero_when_no_entries(self, tmp_log):
        assert daily_spend() == 0.0

    def test_skips_entries_with_no_token_usage_keys(self, tmp_log):
        today = datetime.now(timezone.utc).date().isoformat()
        entry = {
            "ts": "%sT10:00:00+00:00" % today,
            "task_id": "t1",
            "event": "done",
            "stage": "execute",
            "message": "",
            "extra": {"note": "no usage"},
        }
        tmp_log.open("a").write(json.dumps(entry) + "\n")
        assert daily_spend() == 0.0


class TestBudgetCap:
    def test_get_budget_cap_falls_back_on_config_error(self, monkeypatch):
        def boom():
            raise RuntimeError("config unavailable")

        monkeypatch.setattr("src.config.get_settings", boom)
        assert _get_budget_cap() == budget.BUDGET_DAILY_USD


class TestBudgetStatus:
    def test_unlimited_cap(self, monkeypatch, tmp_log):
        monkeypatch.setattr("src.budget._get_budget_cap", lambda: 0.0)
        st = budget_status()
        assert st["budget_enabled"] is False
        assert st["remaining_usd"] == -1

    def test_enabled_cap_and_remaining(self, monkeypatch, tmp_log):
        monkeypatch.setattr("src.budget._get_budget_cap", lambda: 100.0)
        monkeypatch.setattr("src.budget.daily_spend", lambda: 25.0)
        st = budget_status()
        assert st["budget_enabled"] is True
        assert st["daily_cap_usd"] == 100.0
        assert st["spent_today_usd"] == 25.0
        assert st["remaining_usd"] == 75.0


class TestWithinBudget:
    def test_unlimited_when_cap_is_zero(self, monkeypatch, tmp_log):
        monkeypatch.setattr("src.budget.BUDGET_DAILY_USD", 0.0)
        assert within_budget() is True

    def test_under_budget(self, monkeypatch, tmp_log):
        monkeypatch.setattr("src.budget.BUDGET_DAILY_USD", 100.0)
        assert within_budget() is True

    def test_over_budget(self, monkeypatch, tmp_log):
        monkeypatch.setattr("src.budget._get_budget_cap", lambda: 0.001)
        today = datetime.now(timezone.utc).date().isoformat()
        entry = {
            "ts": "%sT10:00:00+00:00" % today,
            "task_id": "t1",
            "event": "done",
            "stage": "execute",
            "message": "",
            "extra": {"outputTokens": 1_000_000},
        }
        tmp_log.open("a").write(json.dumps(entry) + "\n")
        assert within_budget() is False
