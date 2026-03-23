"""
Shared helpers for tests — mock git/subprocess shapes, PR store stubs, command dispatch.

Import from ``tests.support`` in test modules (not collected as tests).
"""

from typing import Any, Callable, Optional, Tuple, Union
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Subprocess / git CLI mocks (``src.pr._run_cmd`` and similar)
# ---------------------------------------------------------------------------


def mock_process(returncode=0, stdout="", stderr=""):
    # type: (int, str, str) -> MagicMock
    """Build a MagicMock matching ``subprocess.CompletedProcess`` fields tests read."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def git_cmd_join(cmd):
    # type: (Any) -> str
    """Join a argv list the same way PR tests match substrings (``"git status" in joined``)."""
    if isinstance(cmd, list):
        return " ".join(str(c) for c in cmd)
    return str(cmd)


GitCmdRule = Union[
    Tuple[str, Any],
    Tuple[Callable[[Any, Optional[str], str], bool], Any],
]


def git_cmd_side_effect(rules, default=None):
    # type: (list, Any) -> Callable[..., Any]
    """Return ``side_effect`` for a mock ``_run_cmd``.

    Each *rule* is either:

    - ``(substring, result)`` — first match where *substring* appears in the joined argv wins.
    - ``(predicate, result)`` — *predicate* is ``(cmd, cwd, joined) -> bool``.

    Rules are evaluated in order; use specific predicates before broad substrings.
    """
    if default is None:
        default = mock_process()

    def side_effect(cmd, cwd=None, timeout=None):
        joined = git_cmd_join(cmd)
        for first, result in rules:
            if isinstance(first, str):
                if first in joined:
                    return result
            elif first(cmd, cwd, joined):
                return result
        return default

    return side_effect


# ---------------------------------------------------------------------------
# DynamoTaskStore stubs for PR pipeline tests
# ---------------------------------------------------------------------------


def attach_pr_mocks(store):
    # type: (Any) -> Any
    """Stub ``set_pr_url`` / ``append_section`` so PR code does not hit Dynamo for those paths."""
    store.set_pr_url = MagicMock()
    store.append_section = MagicMock()
    return store
