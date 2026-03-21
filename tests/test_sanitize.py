"""Tests for src/sanitize.py — secret redaction."""

import src.sanitize as sanitize
from src.sanitize import redact


class TestRedact:
    def test_empty_and_whitespace(self):
        assert redact("") == ""
        assert redact("   ") == "   "

    def test_github_token_pattern(self):
        text = "GITHUB_TOKEN=ghp_supersecret123456"
        out = redact(text)
        assert "ghp_" not in out
        assert "[REDACTED]" in out

    def test_aws_access_key_id_pattern(self):
        key = "AKIA" + "A" * 16
        text = "key %s here" % key
        out = redact(text)
        assert key not in out
        assert "[REDACTED]" in out

    def test_long_hex_token(self):
        hex64 = "a" * 64
        out = redact("prefix %s suffix" % hex64)
        assert hex64 not in out

    def test_env_value_redaction(self, monkeypatch):
        secret = "x" * 12
        monkeypatch.setenv("DISCORD_TOKEN", secret)
        sanitize._extra_loaded = False
        sanitize._EXTRA_SECRETS.clear()
        try:
            out = redact("token=%s" % secret)
            assert secret not in out
            assert "[REDACTED]" in out
        finally:
            sanitize._extra_loaded = False
            sanitize._EXTRA_SECRETS.clear()
