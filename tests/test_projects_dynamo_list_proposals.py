"""Tests for src/projects_dynamo.py list_proposals (filtered pagination)."""

from unittest.mock import MagicMock

import src.projects_dynamo as pd


def _patch_boto_table(monkeypatch, table):
    ddb = MagicMock()
    ddb.Table.return_value = table
    fake_boto = MagicMock()
    fake_boto.resource = lambda *args, **kwargs: ddb
    monkeypatch.setattr(pd, "boto3", fake_boto)


def test_list_proposals_no_status_single_query(monkeypatch):
    table = MagicMock()
    table.query.return_value = {
        "Items": [{"sk": "PROP#a", "status": "pending"}],
    }
    _patch_boto_table(monkeypatch, table)

    out = pd.list_proposals("proj1", status=None, limit=50)

    assert len(out) == 1
    table.query.assert_called_once()
    kw = table.query.call_args[1]
    assert kw["Limit"] == 50
    assert "FilterExpression" not in kw


def test_list_proposals_with_status_paginates_past_empty_pages(monkeypatch):
    """Limit is applied before filter in Dynamo; code must page until matches appear."""
    table = MagicMock()

    def query_side_effect(**kwargs):
        if "ExclusiveStartKey" not in kwargs:
            return {
                "Items": [],
                "LastEvaluatedKey": {"pk": "PROJECT#proj1", "sk": "PROP#cursor"},
            }
        return {"Items": [{"sk": "PROP#hit", "status": "pending"}]}

    table.query.side_effect = query_side_effect
    _patch_boto_table(monkeypatch, table)

    out = pd.list_proposals("proj1", status="pending", limit=10)

    assert out == [{"sk": "PROP#hit", "status": "pending"}]
    assert table.query.call_count == 2
    second = table.query.call_args_list[1][1]
    assert second["ExclusiveStartKey"] == {"pk": "PROJECT#proj1", "sk": "PROP#cursor"}
    assert second["FilterExpression"] == "#st = :st"


def test_list_proposals_with_status_truncates_to_limit(monkeypatch):
    table = MagicMock()
    table.query.return_value = {
        "Items": [
            {"sk": "PROP#1", "status": "approved"},
            {"sk": "PROP#2", "status": "approved"},
            {"sk": "PROP#3", "status": "approved"},
        ],
        "LastEvaluatedKey": None,
    }
    _patch_boto_table(monkeypatch, table)

    out = pd.list_proposals("proj1", status="approved", limit=2)

    assert len(out) == 2
    assert [i["sk"] for i in out] == ["PROP#1", "PROP#2"]
