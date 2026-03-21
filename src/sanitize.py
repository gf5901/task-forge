"""
Sanitize agent output before persisting to task store or displaying in the UI.

Redacts known secret patterns (API keys, tokens, passwords) so they don't
leak into DynamoDB or the web frontend even if the agent
reads them during execution.
"""

import os
import re
from typing import List, Tuple

_REDACTED = "[REDACTED]"

_SECRET_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(DISCORD[_-]?TOKEN\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(DISCORD[_-]?WEBHOOK[_-]?URL\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(GITHUB[_-]?TOKEN\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(GITHUB[_-]?WEBHOOK[_-]?SECRET\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(AUTH[_-]?PASSWORD\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(AUTH[_-]?SECRET[_-]?KEY\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(AWS[_-]?SECRET[_-]?ACCESS[_-]?KEY\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(AWS[_-]?SESSION[_-]?TOKEN\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(OPENAI[_-]?API[_-]?KEY\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(ANTHROPIC[_-]?API[_-]?KEY\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(PRIVATE[_-]?KEY\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(DATABASE[_-]?URL\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(SECRET[_-]?KEY\s*[=:]\s*)(\S+)"), r"\1" + _REDACTED),
    # AWS access key ID format: AKIA followed by 16 uppercase alphanumeric chars
    (re.compile(r"\b(AKIA[A-Z0-9]{16})\b"), _REDACTED),
    # Generic long hex tokens (64+ chars, likely secrets)
    (re.compile(r"\b([0-9a-f]{64,})\b"), _REDACTED),
]

_EXTRA_SECRETS: List[str] = []
_extra_loaded = False


def _load_extra_secrets():
    """Load actual secret values from .env so we can redact them by value."""
    global _extra_loaded
    if _extra_loaded:
        return
    _extra_loaded = True
    sensitive_keys = (
        "DISCORD_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_WEBHOOK_SECRET",
        "AUTH_PASSWORD",
        "AUTH_SECRET_KEY",
        "DISCORD_WEBHOOK_URL",
    )
    for key in sensitive_keys:
        val = os.getenv(key, "")
        if val and len(val) >= 8:
            _EXTRA_SECRETS.append(val)


def redact(text: str) -> str:
    """Redact known secret patterns from text."""
    if not text:
        return text

    _load_extra_secrets()

    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)

    for secret in _EXTRA_SECRETS:
        if secret in text:
            text = text.replace(secret, _REDACTED)

    return text
