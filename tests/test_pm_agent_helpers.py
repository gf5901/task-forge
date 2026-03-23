"""Unit tests for PM agent JSON parsing and task normalization helpers."""


class TestParsePmJson:
    def test_plain_json(self):
        from src.pm_agent import _parse_pm_json

        assert _parse_pm_json('{"actions": []}') == {"actions": []}

    def test_json_in_fence(self):
        from src.pm_agent import _parse_pm_json

        text = 'Here:\n```json\n{"reply": "ok"}\n```'
        assert _parse_pm_json(text) == {"reply": "ok"}

    def test_embedded_json(self):
        from src.pm_agent import _parse_pm_json

        text = 'Prefix {"x": 1} suffix'
        assert _parse_pm_json(text) == {"x": 1}

    def test_invalid_returns_none(self):
        from src.pm_agent import _parse_pm_json

        assert _parse_pm_json("not json") is None


class TestNormalizeAgentTask:
    def test_valid(self):
        from src.pm_agent import _normalize_agent_task

        out = _normalize_agent_task(
            {
                "title": "Do thing",
                "description": "Details",
                "priority": "high",
                "role": "be_engineer",
            }
        )
        assert out == {
            "title": "Do thing",
            "description": "Details",
            "role": "be_engineer",
            "priority": "high",
        }

    def test_invalid_priority_defaults_medium(self):
        from src.pm_agent import _normalize_agent_task

        out = _normalize_agent_task({"title": "T", "priority": "nope"})
        assert out["priority"] == "medium"

    def test_invalid_role_defaults_fullstack(self):
        from src.pm_agent import _normalize_agent_task

        out = _normalize_agent_task({"title": "T", "role": "not_a_real_role"})
        assert out["role"] == "fullstack_engineer"

    def test_non_dict_returns_none(self):
        from src.pm_agent import _normalize_agent_task

        assert _normalize_agent_task("x") is None
        assert _normalize_agent_task(None) is None

    def test_empty_title_returns_none(self):
        from src.pm_agent import _normalize_agent_task

        assert _normalize_agent_task({"title": "  "}) is None


class TestNormalizeHumanTask:
    def test_valid(self):
        from src.pm_agent import _normalize_human_task

        out = _normalize_human_task({"title": "Need API key", "priority": "urgent"})
        assert out["title"] == "Need API key"
        assert out["priority"] == "urgent"
        assert "role" not in out

    def test_non_dict_returns_none(self):
        from src.pm_agent import _normalize_human_task

        assert _normalize_human_task({}) is None


def test_build_role_list_includes_ids():
    from src.pm_agent import _build_role_list
    from src.roles import ROLES

    text = _build_role_list()
    assert "fe_engineer" in text
    assert len(text.splitlines()) == len(ROLES)
