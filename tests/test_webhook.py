"""Tests for src/routers/webhook.py — signature verification and routing."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from src.web import app


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def webhook_secret(monkeypatch):
    secret = "test-webhook-secret"
    monkeypatch.setattr("src.routers.webhook.GITHUB_WEBHOOK_SECRET", secret)
    return secret


def test_github_webhook_not_configured(monkeypatch):
    monkeypatch.setattr("src.routers.webhook.GITHUB_WEBHOOK_SECRET", "")
    client = TestClient(app)
    r = client.post("/webhook/github", content=b"{}")
    assert r.status_code == 503
    assert "not configured" in r.json()["error"]


def test_github_webhook_invalid_signature(webhook_secret):
    client = TestClient(app)
    r = client.post(
        "/webhook/github",
        content=b"{}",
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert r.status_code == 403


def test_github_webhook_ping(webhook_secret):
    body = b"{}"
    client = TestClient(app)
    r = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body, webhook_secret),
            "X-GitHub-Event": "ping",
        },
    )
    assert r.status_code == 200
    assert r.json().get("msg") == "pong"


def test_github_webhook_ignores_non_push_event(webhook_secret):
    body = json.dumps({"action": "opened"}).encode()
    client = TestClient(app)
    r = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body, webhook_secret),
            "X-GitHub-Event": "issues",
        },
    )
    assert r.status_code == 200
    assert "ignored event" in r.json().get("msg", "")


def test_github_webhook_ignores_non_main_ref(webhook_secret):
    body = json.dumps({"ref": "refs/heads/feature"}).encode()
    client = TestClient(app)
    r = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body, webhook_secret),
            "X-GitHub-Event": "push",
        },
    )
    assert r.status_code == 200
    assert "ignored ref" in r.json().get("msg", "")


def test_verify_github_signature_helper(monkeypatch):
    from src.routers import webhook as wh

    assert wh._verify_github_signature(b"x", "sha256=abc") is False
    monkeypatch.setattr(wh, "GITHUB_WEBHOOK_SECRET", "s")
    body = b"payload"
    sig = _sign(body, "s")
    assert wh._verify_github_signature(body, sig) is True
