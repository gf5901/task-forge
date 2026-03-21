"""Tests for src/logging_config.py."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

import src.logging_config as lc


@pytest.fixture(autouse=True)
def clear_root_handlers():
    root = logging.getLogger()
    root.handlers.clear()
    yield
    root.handlers.clear()


def _reset_logging():
    """Clear pytest-injected handlers so configure() can attach ours."""
    logging.getLogger().handlers.clear()


class TestConfigure:
    def test_adds_file_and_stream_handlers(self, tmp_path):
        _reset_logging()
        log_path = tmp_path / "test.log"
        lc.configure(log_file=str(log_path), level="DEBUG")
        root = logging.getLogger()
        assert len(root.handlers) == 2
        file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert Path(file_handlers[0].baseFilename) == log_path.resolve()
        root.info("probe")
        assert log_path.exists()

    def test_skips_when_handlers_already_present(self, tmp_path):
        _reset_logging()
        log_path = tmp_path / "a.log"
        lc.configure(log_file=str(log_path))
        n_first = len(logging.getLogger().handlers)
        lc.configure(log_file=str(tmp_path / "b.log"))
        assert len(logging.getLogger().handlers) == n_first

    def test_invalid_level_falls_back_to_info(self, tmp_path):
        _reset_logging()
        log_path = tmp_path / "c.log"
        lc.configure(log_file=str(log_path), level="NOT_A_REAL_LEVEL")
        assert logging.getLogger().level == logging.INFO
