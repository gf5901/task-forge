"""Tests for src/config.py — Dynamo-backed runtime settings."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import src.config as config


class TestGetSettings:
    def test_returns_defaults_when_dynamo_fails(self, monkeypatch):
        def boom():
            raise RuntimeError("no table")

        monkeypatch.setattr(config, "_get_table", boom)
        out = config.get_settings()
        assert set(out.keys()) == set(config.DEFAULTS.keys())
        for k in config.DEFAULTS:
            assert type(out[k]) is type(config.DEFAULTS[k])

    def test_merges_decimal_int_from_item(self, monkeypatch):
        table = MagicMock()
        table.get_item.return_value = {
            "Item": {
                "max_concurrent_runners": Decimal("3"),
                "min_spawn_interval": Decimal("100"),
                "task_timeout": Decimal("120"),
            }
        }
        monkeypatch.setattr(config, "_get_table", lambda: table)
        out = config.get_settings()
        assert out["max_concurrent_runners"] == 3
        assert isinstance(out["max_concurrent_runners"], int)
        assert out["min_spawn_interval"] == 100
        assert out["task_timeout"] == 120

    def test_merges_decimal_float_from_item(self, monkeypatch):
        table = MagicMock()
        table.get_item.return_value = {
            "Item": {"budget_daily_usd": Decimal("12.5")},
        }
        monkeypatch.setattr(config, "_get_table", lambda: table)
        out = config.get_settings()
        assert out["budget_daily_usd"] == 12.5


class TestUpdateSettings:
    def test_empty_patch_calls_get_settings(self, monkeypatch):
        table = MagicMock()
        table.get_item.return_value = {"Item": {}}
        monkeypatch.setattr(config, "_get_table", lambda: table)
        out = config.update_settings({})
        assert table.update_item.call_count == 0
        assert set(out.keys()) == set(config.DEFAULTS.keys())

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError) as exc:
            config.update_settings({"not_a_setting": 1})
        assert "unknown" in str(exc.value).lower()

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError) as exc:
            config.update_settings({"max_concurrent_runners": "nope"})
        assert "invalid type" in str(exc.value).lower()

    def test_invalid_float_string_raises(self):
        with pytest.raises(ValueError) as exc:
            config.update_settings({"budget_daily_usd": "not-a-number"})
        assert "invalid type" in str(exc.value).lower()

    def test_coerces_float_setting_from_string(self, monkeypatch):
        table = MagicMock()
        table.get_item.return_value = {"Item": {"budget_daily_usd": Decimal("12.5")}}
        monkeypatch.setattr(config, "_get_table", lambda: table)
        out = config.update_settings({"budget_daily_usd": "12.5"})
        assert out["budget_daily_usd"] == 12.5
        # float stored as Decimal in ExpressionAttributeValues
        call = table.update_item.call_args
        vals = call[1]["ExpressionAttributeValues"]
        assert Decimal("12.5") in vals.values()

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError) as exc:
            config.update_settings({"max_concurrent_runners": 99})
        assert "out of range" in str(exc.value).lower()

    def test_update_item_and_returns_merged(self, monkeypatch):
        table = MagicMock()
        # get_settings() runs once after update_item and reads the merged item from Dynamo
        table.get_item.return_value = {"Item": {"max_concurrent_runners": Decimal("2")}}
        monkeypatch.setattr(config, "_get_table", lambda: table)
        out = config.update_settings({"max_concurrent_runners": 2})
        assert table.update_item.call_count == 1
        assert out["max_concurrent_runners"] == 2
