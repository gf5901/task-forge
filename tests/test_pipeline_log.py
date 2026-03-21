"""Tests for src/pipeline_log.py — emit, read, filter, count."""

import json

from src.pipeline_log import count_logs, emit, read_logs


class TestEmit:
    def test_basic_emit(self, tmp_log):
        emit("task1", "start", "pipeline", "Starting task")
        lines = tmp_log.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["task_id"] == "task1"
        assert entry["event"] == "start"
        assert entry["stage"] == "pipeline"
        assert entry["message"] == "Starting task"
        assert "ts" in entry

    def test_emit_with_extra(self, tmp_log):
        emit("task2", "done", "execute", "Finished", runtime=12.5, model="fast")
        entry = json.loads(tmp_log.read_text().strip())
        assert entry["extra"]["runtime"] == 12.5
        assert entry["extra"]["model"] == "fast"

    def test_multiple_emits(self, tmp_log):
        emit("t1", "a", "s1", "m1")
        emit("t2", "b", "s2", "m2")
        emit("t1", "c", "s3", "m3")
        lines = tmp_log.read_text().strip().splitlines()
        assert len(lines) == 3


class TestReadLogs:
    def test_empty_log(self, tmp_log):
        assert read_logs() == []

    def test_nonexistent_log(self, tmp_log):
        tmp_log.unlink(missing_ok=True)
        assert read_logs() == []

    def test_read_all(self, tmp_log):
        emit("t1", "a", "s", "m1")
        emit("t2", "b", "s", "m2")
        entries = read_logs()
        assert len(entries) == 2
        # Newest first
        assert entries[0]["task_id"] == "t2"
        assert entries[1]["task_id"] == "t1"

    def test_filter_by_task_id(self, tmp_log):
        emit("t1", "a", "s", "m1")
        emit("t2", "b", "s", "m2")
        emit("t1", "c", "s", "m3")
        entries = read_logs(task_id="t1")
        assert len(entries) == 2
        assert all(e["task_id"] == "t1" for e in entries)

    def test_limit(self, tmp_log):
        for i in range(10):
            emit("t1", "e%d" % i, "s", "m")
        entries = read_logs(limit=3)
        assert len(entries) == 3

    def test_offset(self, tmp_log):
        for i in range(5):
            emit("t1", "e%d" % i, "s", "msg%d" % i)
        entries = read_logs(offset=2, limit=2)
        assert len(entries) == 2

    def test_malformed_line_skipped(self, tmp_log):
        tmp_log.write_text("not valid json\n")
        emit("t1", "ok", "s", "good")
        entries = read_logs()
        assert len(entries) == 1
        assert entries[0]["event"] == "ok"


class TestCountLogs:
    def test_empty(self, tmp_log):
        assert count_logs() == 0

    def test_count_all(self, tmp_log):
        emit("t1", "a", "s", "m")
        emit("t2", "b", "s", "m")
        assert count_logs() == 2

    def test_count_filtered(self, tmp_log):
        emit("t1", "a", "s", "m")
        emit("t2", "b", "s", "m")
        emit("t1", "c", "s", "m")
        assert count_logs(task_id="t1") == 2
        assert count_logs(task_id="t2") == 1
        assert count_logs(task_id="missing") == 0
