"""Tests for src/objectives.py KPI classification helpers."""

from src.objectives import _classify_kpis


class TestClassifyKpis:
    def test_target_zero_with_current_counts_on_track(self):
        on, behind, nodata = _classify_kpis(
            [{"direction": "up", "target": 0, "current": 42}],
        )
        assert (on, behind, nodata) == (1, 0, 0)

    def test_target_zero_no_current_counts_no_data(self):
        on, behind, nodata = _classify_kpis(
            [{"direction": "up", "target": 0, "current": None}],
        )
        assert (on, behind, nodata) == (0, 0, 1)

    def test_target_none_like_zero(self):
        on, behind, nodata = _classify_kpis(
            [{"direction": "up", "target": None, "current": 1}],
        )
        assert (on, behind, nodata) == (1, 0, 0)

    def test_up_direction_behind_and_on_track(self):
        on, behind, nodata = _classify_kpis(
            [
                {"direction": "up", "target": 100, "current": 96},
                {"direction": "up", "target": 100, "current": 50},
            ],
        )
        assert (on, behind, nodata) == (1, 1, 0)
