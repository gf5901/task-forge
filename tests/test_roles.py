"""Tests for the roles registry."""

import pytest

from src.roles import ROLES, ROLES_BY_ID, get_role_prompt

EXPECTED_ROLES = [
    "fe_engineer",
    "be_engineer",
    "fullstack_engineer",
    "product_designer",
    "product_manager",
    "devops_engineer",
    "data_engineer",
    "security_engineer",
    "technical_writer",
    "researcher",
    "content_strategist",
    "qa_engineer",
    "architect",
]


class TestRolesRegistry:
    def test_all_expected_roles_present(self):
        ids = [r["id"] for r in ROLES]
        for role_id in EXPECTED_ROLES:
            assert role_id in ids, "Missing role: %s" % role_id

    def test_each_role_has_required_fields(self):
        for role in ROLES:
            assert "id" in role and role["id"], "Role missing id: %r" % role
            assert "label" in role and role["label"], "Role missing label: %r" % role
            assert "prompt" in role and len(role["prompt"]) > 20, (
                "Role %s has too-short prompt" % role["id"]
            )

    def test_no_duplicate_ids(self):
        ids = [r["id"] for r in ROLES]
        assert len(ids) == len(set(ids)), "Duplicate role IDs found"

    def test_roles_by_id_lookup(self):
        for role_id in EXPECTED_ROLES:
            assert role_id in ROLES_BY_ID
            assert ROLES_BY_ID[role_id]["id"] == role_id

    def test_roles_by_id_matches_roles_list(self):
        assert len(ROLES_BY_ID) == len(ROLES)


class TestGetRolePrompt:
    def test_known_role_returns_prompt(self):
        prompt = get_role_prompt("fe_engineer")
        assert "frontend" in prompt.lower()

    def test_empty_string_returns_empty(self):
        assert get_role_prompt("") == ""

    def test_unknown_role_returns_empty(self):
        assert get_role_prompt("nonexistent_role") == ""

    def test_researcher_prompt(self):
        prompt = get_role_prompt("researcher")
        assert "investigat" in prompt.lower() or "findings" in prompt.lower()

    def test_content_strategist_prompt(self):
        prompt = get_role_prompt("content_strategist")
        assert "copy" in prompt.lower() or "content" in prompt.lower()

    def test_qa_engineer_prompt(self):
        prompt = get_role_prompt("qa_engineer")
        assert "test" in prompt.lower()

    def test_architect_prompt(self):
        prompt = get_role_prompt("architect")
        assert "design" in prompt.lower() or "architect" in prompt.lower()

    @pytest.mark.parametrize("role_id", EXPECTED_ROLES)
    def test_all_roles_return_nonempty_prompt(self, role_id):
        assert get_role_prompt(role_id) != ""
