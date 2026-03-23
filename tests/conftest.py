"""Shared test fixtures.

Reusable non-fixture helpers live in ``tests.support`` (mock git/PR helpers, etc.).
"""

import os

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

os.environ.setdefault("PIPELINE_LOG", "/tmp/test-pipeline.log")
os.environ.setdefault("AUTH_EMAIL", "")
os.environ.setdefault("AUTH_PASSWORD", "")

TABLE_NAME = "test-agent-tasks"


def _create_table(ddb):
    """Create a minimal DynamoDB table matching the agent-tasks schema."""
    ddb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "parent_id", "AttributeType": "S"},
            {"AttributeName": "pr_url", "AttributeType": "S"},
            {"AttributeName": "project_id", "AttributeType": "S"},
            {"AttributeName": "priority_sort_created", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "parent-index",
                "KeySchema": [
                    {"AttributeName": "parent_id", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "pr-index",
                "KeySchema": [
                    {"AttributeName": "pr_url", "KeyType": "HASH"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
            {
                "IndexName": "project-index",
                "KeySchema": [
                    {"AttributeName": "project_id", "KeyType": "HASH"},
                    {"AttributeName": "priority_sort_created", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def tmp_tasks():
    """Return a DynamoTaskStore backed by a moto-mocked DynamoDB table."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-west-2")
        _create_table(ddb)
        from src.dynamo_store import DynamoTaskStore

        store = DynamoTaskStore(table_name=TABLE_NAME, region="us-west-2")
        yield store


@pytest.fixture
def client(tmp_tasks, monkeypatch):
    """FastAPI TestClient with a fresh DynamoTaskStore; auth off; runner/cancel are no-ops."""
    import src.routers.tasks as tasks_router
    import src.web as web_mod

    monkeypatch.setattr(tasks_router, "_get_store", lambda: tmp_tasks)
    monkeypatch.setattr(web_mod, "trigger_runner", lambda task_id: None)
    monkeypatch.setattr(web_mod, "cancel_runner", lambda task_id: None)
    monkeypatch.setattr(web_mod, "AUTH_ENABLED", False)
    from src.web import app

    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    """Provide a temporary pipeline log path and patch the module."""
    log_path = tmp_path / "pipeline.log"
    import src.pipeline_log as pl

    monkeypatch.setattr(pl, "LOG_PATH", log_path)
    monkeypatch.setattr(pl, "_handler_attached", False)
    monkeypatch.setattr(pl, "_dynamo_log_store", False)
    pl._pipeline_logger.handlers.clear()
    return log_path
