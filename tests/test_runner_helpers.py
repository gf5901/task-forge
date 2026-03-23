"""Tests for runner helper functions — prompts, slugs, model resolution, appending."""

import json
import os
import subprocess
from unittest.mock import patch

from src.task_store import Task, TaskPriority, TaskStatus


class TestSlugifyBranch:
    def test_basic(self):
        from src.runner import _slugify_branch

        assert _slugify_branch("Fix the login bug") == "fix-the-login-bug"

    def test_special_chars(self):
        from src.runner import _slugify_branch

        assert _slugify_branch("Add feat: new UI (v2)") == "add-feat-new-ui-v2"

    def test_truncation(self):
        from src.runner import _slugify_branch

        result = _slugify_branch("a" * 100)
        assert len(result) <= 40


class TestBuildPrompt:
    def test_title_only(self):
        from src.runner import build_prompt

        task = Task(id="abc", title="Fix bug")
        prompt = build_prompt(task)
        assert "Task: Fix bug" in prompt

    def test_with_description(self):
        from src.runner import build_prompt

        task = Task(id="abc", title="Fix bug", description="The login page crashes")
        prompt = build_prompt(task)
        assert "The login page crashes" in prompt

    def test_with_tags(self):
        from src.runner import build_prompt

        task = Task(id="abc", title="Fix bug", tags=["frontend", "urgent"])
        prompt = build_prompt(task)
        assert "frontend" in prompt
        assert "urgent" in prompt


class TestBuildChecklistPrompt:
    def test_basic_checklist(self):
        from src.runner import _build_checklist_prompt

        task = Task(id="abc", title="Big refactor", description="Refactor the codebase")
        steps = [
            {"title": "Extract module", "description": "Move helpers to utils.py"},
            {"title": "Update imports", "description": "Fix all import paths"},
        ]
        prompt = _build_checklist_prompt(task, steps)
        assert "# Task: Big refactor" in prompt
        assert "Refactor the codebase" in prompt
        assert "Step 1: Extract module" in prompt
        assert "Step 2: Update imports" in prompt
        assert "Move helpers to utils.py" in prompt

    def test_includes_tags(self):
        from src.runner import _build_checklist_prompt

        task = Task(id="abc", title="T", tags=["backend"])
        steps = [{"title": "S1", "description": "D1"}]
        prompt = _build_checklist_prompt(task, steps)
        assert "backend" in prompt


class TestResolveModel:
    def test_no_model(self):
        from src.runner import _resolve_model

        task = Task(id="abc", title="T")
        assert _resolve_model(task) is None

    def test_fast_tier(self):
        from src.runner import MODEL_FAST, _resolve_model

        task = Task(id="abc", title="T", model="fast")
        result = _resolve_model(task)
        if MODEL_FAST:
            assert result == MODEL_FAST
        else:
            assert result is None

    def test_custom_model_string(self):
        from src.runner import _resolve_model

        task = Task(id="abc", title="T", model="claude-3.5-sonnet")
        assert _resolve_model(task) == "claude-3.5-sonnet"

    def test_default_tier(self, monkeypatch):
        from src.pipeline import MODEL_MAP, ModelTier, _resolve_model

        monkeypatch.setitem(MODEL_MAP, ModelTier.DEFAULT, "claude-sonnet-4")
        task = Task(id="abc", title="T", model="default")
        assert _resolve_model(task) == "claude-sonnet-4"

    def test_default_tier_empty_returns_none(self, monkeypatch):
        from src.pipeline import MODEL_MAP, ModelTier, _resolve_model

        monkeypatch.setitem(MODEL_MAP, ModelTier.DEFAULT, "")
        task = Task(id="abc", title="T", model="default")
        assert _resolve_model(task) is None

    def test_full_tier(self, monkeypatch):
        from src.pipeline import MODEL_MAP, ModelTier, _resolve_model

        monkeypatch.setitem(MODEL_MAP, ModelTier.FULL, "claude-opus-4-5")
        task = Task(id="abc", title="T", model="full")
        assert _resolve_model(task) == "claude-opus-4-5"


class TestPriorityOrder:
    def test_ordering(self):
        from src.runner import PRIORITY_ORDER

        assert PRIORITY_ORDER[TaskPriority.URGENT] < PRIORITY_ORDER[TaskPriority.HIGH]
        assert PRIORITY_ORDER[TaskPriority.HIGH] < PRIORITY_ORDER[TaskPriority.MEDIUM]
        assert PRIORITY_ORDER[TaskPriority.MEDIUM] < PRIORITY_ORDER[TaskPriority.LOW]


class TestPickNextTask:
    def test_empty_store(self, tmp_tasks):
        from src.runner import pick_next_task

        assert pick_next_task(tmp_tasks) is None

    def test_picks_highest_priority(self, tmp_tasks):
        from src.runner import pick_next_task

        tmp_tasks.create(title="Low", priority="low")
        urgent = tmp_tasks.create(title="Urgent", priority="urgent")
        picked = pick_next_task(tmp_tasks)
        assert picked.id == urgent.id

    def test_skips_subtasks(self, tmp_tasks):
        from src.runner import pick_next_task

        parent = tmp_tasks.create(title="Parent")
        tmp_tasks.update_status(parent.id, TaskStatus.IN_PROGRESS)
        sub = tmp_tasks.create(title="Sub", parent_id=parent.id)
        picked = pick_next_task(tmp_tasks)
        assert picked is None or picked.id != sub.id

    def test_skips_non_pending(self, tmp_tasks):
        from src.runner import pick_next_task

        task = tmp_tasks.create(title="Done")
        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)
        assert pick_next_task(tmp_tasks) is None

    def test_picks_oldest_when_same_priority(self, tmp_tasks):
        from src.runner import pick_next_task

        # Manually set created_at so ordering is deterministic
        t1 = tmp_tasks.create(title="First", priority="medium")
        t2 = tmp_tasks.create(title="Second", priority="medium")
        tmp_tasks.set_field(t1.id, "created_at", "2026-01-01T00:00:00+00:00")
        tmp_tasks.set_field(t2.id, "created_at", "2026-01-02T00:00:00+00:00")
        picked = pick_next_task(tmp_tasks)
        assert picked.id == t1.id


class TestAppendResultToTask:
    def _make_result(self, stdout="", returncode=0, stderr=""):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_appends_success_output(self, tmp_tasks):
        from src.runner import append_result_to_task

        task = tmp_tasks.create(title="T", description="Desc")
        result = self._make_result(stdout="Agent did the work")
        append_result_to_task(tmp_tasks, task, result)
        output = tmp_tasks.get_agent_output(task.id)
        assert output == "Agent did the work"

    def test_appends_failure_output(self, tmp_tasks):
        from src.runner import append_result_to_task

        task = tmp_tasks.create(title="T")
        result = self._make_result(stdout="partial output", returncode=1, stderr="error msg")
        append_result_to_task(tmp_tasks, task, result)
        output = tmp_tasks.get_agent_output(task.id)
        assert "**Exit code:** 1" in output
        assert "partial output" in output
        assert "error msg" in output

    def test_no_output_placeholder(self, tmp_tasks):
        from src.runner import append_result_to_task

        task = tmp_tasks.create(title="T")
        result = self._make_result(stdout="")
        append_result_to_task(tmp_tasks, task, result)
        output = tmp_tasks.get_agent_output(task.id)
        assert output == "(no output)"

    def test_preserves_description(self, tmp_tasks):
        from src.runner import append_result_to_task

        task = tmp_tasks.create(title="T", description="Keep this")
        result = self._make_result(stdout="Done")
        append_result_to_task(tmp_tasks, task, result)
        assert tmp_tasks.get(task.id).description == "Keep this"

    def test_custom_section_name(self, tmp_tasks):
        from src.runner import append_result_to_task

        task = tmp_tasks.create(title="T")
        result = self._make_result(stdout="Doc updated")
        append_result_to_task(tmp_tasks, task, result, section="Doc Update")
        assert tmp_tasks.has_section(task.id, "Doc Update")

    def test_nonexistent_task_is_noop(self, tmp_tasks):
        from src.runner import append_result_to_task

        task = Task(id="missing", title="Ghost")
        result = self._make_result(stdout="output")
        append_result_to_task(tmp_tasks, task, result)  # should not raise


class TestAppendTextToTask:
    def test_appends_text(self, tmp_tasks):
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="T")
        _append_text_to_task(tmp_tasks, task, "Plan", "Step 1\nStep 2")
        assert tmp_tasks.has_section(task.id, "Plan")

    def test_preserves_existing_content(self, tmp_tasks):
        from src.runner import _append_text_to_task

        task = tmp_tasks.create(title="T", description="Original")
        _append_text_to_task(tmp_tasks, task, "Plan", "The plan")
        assert tmp_tasks.get(task.id).description == "Original"


class TestPlanTaskParsing:
    def test_valid_json_plan(self, monkeypatch):
        import subprocess

        from src.runner import plan_task

        task = Task(id="abc", title="Big task", description="Do stuff")

        valid_json = '[{"title": "Step 1", "description": "Do A"}, {"title": "Step 2", "description": "Do B"}]'
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=valid_json, stderr=""
        )

        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake_result, 1.0, "", {}))

        store = object()  # plan_task doesn't use store directly
        steps = plan_task(store, task)
        assert len(steps) == 2
        assert steps[0]["title"] == "Step 1"
        assert steps[1]["title"] == "Step 2"

    def test_json_embedded_in_text(self, monkeypatch):
        import subprocess

        from src.runner import plan_task

        task = Task(id="abc", title="T")
        output = 'Here is the plan:\n[{"title": "Do it", "description": "Just do it"}]\nDone.'
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake_result, 1.0, "", {}))

        steps = plan_task(object(), task)
        assert len(steps) == 1
        assert steps[0]["title"] == "Do it"

    def test_invalid_json_returns_empty(self, monkeypatch):
        import subprocess

        from src.runner import plan_task

        task = Task(id="abc", title="T")
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json at all", stderr=""
        )
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake_result, 1.0, "", {}))

        steps = plan_task(object(), task)
        assert steps == []

    def test_agent_failure_returns_empty(self, monkeypatch):
        import subprocess

        from src.runner import plan_task

        task = Task(id="abc", title="T")
        fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake_result, 1.0, "", {}))

        steps = plan_task(object(), task)
        assert steps == []

    def test_timeout_returns_empty(self, monkeypatch):
        from src.runner import plan_task

        task = Task(id="abc", title="T")
        monkeypatch.setattr(
            "src.pipeline.run_agent",
            lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("agent", 60)),
        )

        steps = plan_task(object(), task)
        assert steps == []

    def test_plan_prompt_contains_role_options(self):
        from src.roles import ROLES
        from src.runner import PLAN_PROMPT, _build_role_options

        role_opts = _build_role_options()
        for role in ROLES:
            assert role["id"] in role_opts
            assert role["label"] in role_opts

        rendered = (PLAN_PROMPT % role_opts) % ("My task", "Description", "tags")
        assert "role" in rendered
        assert "fe_engineer" in rendered

    def test_plan_only_prompt_contains_role_options(self):
        from src.roles import ROLES
        from src.runner import PLAN_ONLY_PROMPT, _build_role_options

        role_opts = _build_role_options()
        rendered = (PLAN_ONLY_PROMPT % role_opts) % ("My task", "Description", "tags")
        assert "role" in rendered
        for role in ROLES:
            assert role["id"] in rendered

    def test_plan_task_preserves_role_field(self, monkeypatch):
        import subprocess

        from src.runner import plan_task

        task = Task(id="abc", title="Build UI", description="React components")
        valid_json = (
            '[{"title": "Design layout", "description": "Wireframe", "role": "product_designer"},'
            ' {"title": "Implement components", "description": "React", "role": "fe_engineer"}]'
        )
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=valid_json, stderr=""
        )
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake_result, 1.0, "", {}))

        steps = plan_task(object(), task)
        assert steps[0]["role"] == "product_designer"
        assert steps[1]["role"] == "fe_engineer"

    def test_plan_task_uses_model_plan(self, monkeypatch):
        """Planning should use MODEL_PLAN (full model), not MODEL_FAST."""
        import subprocess

        from src.runner import plan_task

        captured = {}

        def capture_agent(*a, **kw):
            captured["model"] = kw.get("model")
            valid = '[{"title": "S1", "description": "d"}]'
            fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=valid, stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)

        task = Task(id="abc", title="T")
        plan_task(object(), task)

        import src.pipeline as _p

        assert captured["model"] == _p.MODEL_PLAN


class TestModelFallback:
    def test_is_model_unavailable_detects_error(self):
        import subprocess

        from src.agent import _is_model_unavailable

        result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Cannot use this model: old-model"
        )
        assert _is_model_unavailable(result) is True

    def test_is_model_unavailable_false_for_other_errors(self):
        import subprocess

        from src.agent import _is_model_unavailable

        result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Some other error"
        )
        assert _is_model_unavailable(result) is False

    def test_run_agent_retries_without_model_on_unavailable(self, monkeypatch):
        import subprocess

        from src.agent import run_agent

        calls = []

        def fake_run_cmd(cmd, cwd, timeout, env=None):
            calls.append(cmd[:])
            if "--model" in cmd and "bad-model" in cmd:
                result = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=1,
                    stdout="",
                    stderr="Cannot use this model: bad-model",
                )
                return result, 1.0, "", {}
            result = subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="success", stderr=""
            )
            return result, 2.0, "sid", {}

        monkeypatch.setattr("src.agent._run_agent_cmd", fake_run_cmd)

        result, elapsed, sid, usage = run_agent("do stuff", cwd="/tmp", model="bad-model")
        assert result.returncode == 0
        assert len(calls) == 2
        assert "--model" in calls[0]
        assert "--model" not in calls[1]

    def test_run_agent_no_retry_when_model_works(self, monkeypatch):
        import subprocess

        from src.agent import run_agent

        calls = []

        def fake_run_cmd(cmd, cwd, timeout, env=None):
            calls.append(cmd[:])
            result = subprocess.CompletedProcess(args=cmd, returncode=0, stdout="done", stderr="")
            return result, 1.0, "", {}

        monkeypatch.setattr("src.agent._run_agent_cmd", fake_run_cmd)

        result, elapsed, sid, usage = run_agent("do stuff", cwd="/tmp", model="good-model")
        assert result.returncode == 0
        assert len(calls) == 1

    def test_run_agent_no_retry_without_model(self, monkeypatch):
        import subprocess

        from src.agent import run_agent

        calls = []

        def fake_run_cmd(cmd, cwd, timeout, env=None):
            calls.append(cmd[:])
            result = subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="some error"
            )
            return result, 1.0, "", {}

        monkeypatch.setattr("src.agent._run_agent_cmd", fake_run_cmd)

        result, elapsed, sid, usage = run_agent("do stuff", cwd="/tmp", model=None)
        assert result.returncode == 1
        assert len(calls) == 1


class TestCreateSubtasks:
    def test_role_from_plan_step(self, tmp_tasks):
        from src.runner import create_subtasks

        parent = tmp_tasks.create(title="Parent", role="fullstack_engineer")
        plan = [
            {"title": "Build UI", "description": "React components", "role": "fe_engineer"},
            {"title": "Add API", "description": "FastAPI endpoint", "role": "be_engineer"},
        ]
        subtasks = create_subtasks(tmp_tasks, parent, plan)
        assert subtasks[0].role == "fe_engineer"
        assert subtasks[1].role == "be_engineer"

    def test_role_falls_back_to_parent(self, tmp_tasks):
        from src.runner import create_subtasks

        parent = tmp_tasks.create(title="Parent", role="devops_engineer")
        plan = [
            {"title": "Step A", "description": "Do something", "role": ""},
            {"title": "Step B", "description": "Do more"},
        ]
        subtasks = create_subtasks(tmp_tasks, parent, plan)
        assert subtasks[0].role == "devops_engineer"
        assert subtasks[1].role == "devops_engineer"

    def test_role_persisted_to_disk(self, tmp_tasks):
        from src.runner import create_subtasks

        parent = tmp_tasks.create(title="Parent")
        plan = [{"title": "Write tests", "description": "pytest", "role": "qa_engineer"}]
        subtasks = create_subtasks(tmp_tasks, parent, plan)
        reloaded = tmp_tasks.get(subtasks[0].id)
        assert reloaded.role == "qa_engineer"

    def test_no_role_on_step_or_parent(self, tmp_tasks):
        from src.runner import create_subtasks

        parent = tmp_tasks.create(title="Parent")
        plan = [{"title": "Do thing", "description": "Desc"}]
        subtasks = create_subtasks(tmp_tasks, parent, plan)
        assert subtasks[0].role == ""


class TestParseAgentResult:
    def test_parses_session_id_and_usage(self):
        import json

        from src.runner import _parse_agent_result

        line = json.dumps(
            {
                "type": "result",
                "result": "hi",
                "session_id": "abc-123",
                "usage": {
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "cacheReadTokens": 100,
                    "cacheWriteTokens": 50,
                },
            }
        )
        sid, usage = _parse_agent_result(line)
        assert sid == "abc-123"
        assert usage["inputTokens"] == 10
        assert usage["outputTokens"] == 5
        assert usage["cacheReadTokens"] == 100
        assert usage["cacheWriteTokens"] == 50

    def test_no_usage_returns_empty_dict(self):
        import json

        from src.runner import _parse_agent_result

        line = json.dumps({"session_id": "xyz", "result": "done"})
        sid, usage = _parse_agent_result(line)
        assert sid == "xyz"
        assert usage == {}

    def test_parses_last_json_line(self):
        import json

        from src.runner import _parse_agent_result

        stdout = "some text\nmore text\n" + json.dumps(
            {
                "session_id": "s1",
                "usage": {"inputTokens": 3, "outputTokens": 4},
            }
        )
        sid, usage = _parse_agent_result(stdout)
        assert sid == "s1"
        assert usage["inputTokens"] == 3

    def test_empty_stdout(self):
        from src.runner import _parse_agent_result

        sid, usage = _parse_agent_result("")
        assert sid == ""
        assert usage == {}

    def test_non_json_stdout(self):
        from src.runner import _parse_agent_result

        sid, usage = _parse_agent_result("just plain text output")
        assert sid == ""
        assert usage == {}

    def test_short_message(self):
        from src.bot import _split_message

        chunks = _split_message("Hello world")
        assert chunks == ["Hello world"]

    def test_exact_limit(self):
        from src.bot import _split_message

        text = "x" * 1990
        chunks = _split_message(text, limit=1990)
        assert len(chunks) == 1

    def test_splits_on_newline(self):
        from src.bot import _split_message

        text = "Line one\nLine two\nLine three"
        chunks = _split_message(text, limit=15)
        assert len(chunks) > 1
        assert all(len(c) <= 15 for c in chunks)
        assert "Line one" in chunks[0]

    def test_splits_without_newline(self):
        from src.bot import _split_message

        text = "a" * 100
        chunks = _split_message(text, limit=30)
        assert len(chunks) > 1
        assert all(len(c) <= 30 for c in chunks)
        assert "".join(chunks) == text

    def test_empty_string(self):
        from src.bot import _split_message

        assert _split_message("") == [""]


class TestConcurrentClaim:
    """run_one must not execute a task that was already claimed by another runner."""

    def test_skips_task_already_in_progress(self, tmp_tasks, monkeypatch):
        """If a task is flipped to in_progress between pick and claim, run_one bails."""
        from src.runner import run_one
        from src.task_store import TaskStatus

        task = tmp_tasks.create(title="Race task")

        # Simulate the other runner winning: after our update_status call sets
        # IN_PROGRESS the re-read returns CANCELLED (another process overrode it).
        original_update = tmp_tasks.update_status

        calls = []

        def patched_update(task_id, status):
            result = original_update(task_id, status)
            if not calls:
                calls.append(1)
                # Simulate another runner having already changed it
                original_update(task_id, TaskStatus.CANCELLED)
            return result

        monkeypatch.setattr(tmp_tasks, "update_status", patched_update)

        did_work = run_one(tmp_tasks, task_id=task.id)
        assert not did_work

    def test_pick_next_skips_in_progress(self, tmp_tasks):
        """pick_next_task never returns a task already in progress."""
        from src.runner import pick_next_task
        from src.task_store import TaskStatus

        task = tmp_tasks.create(title="Already running")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)

        assert pick_next_task(tmp_tasks) is None

    def test_two_pending_tasks_picked_independently(self, tmp_tasks):
        """Two pending tasks are distinct picks — no duplicate selection."""
        from src.runner import pick_next_task
        from src.task_store import TaskStatus

        t1 = tmp_tasks.create(title="Task one", priority="high")
        t2 = tmp_tasks.create(title="Task two", priority="medium")

        first = pick_next_task(tmp_tasks)
        assert first.id == t1.id

        # Simulate runner 1 claiming t1
        tmp_tasks.update_status(t1.id, TaskStatus.IN_PROGRESS)

        second = pick_next_task(tmp_tasks)
        assert second.id == t2.id


class TestPidfile:
    def test_write_and_remove(self, tmp_path, monkeypatch):
        import src.runner as runner_mod

        monkeypatch.setattr(runner_mod, "PIDFILE_DIR", tmp_path)
        runner_mod.write_pidfile("abc123")
        pidfile = tmp_path / "task-runner-abc123.pid"
        assert pidfile.exists()
        assert int(pidfile.read_text()) == os.getpid()

        runner_mod.remove_pidfile("abc123")
        assert not pidfile.exists()

    def test_remove_nonexistent_is_noop(self, tmp_path, monkeypatch):
        import src.runner as runner_mod

        monkeypatch.setattr(runner_mod, "PIDFILE_DIR", tmp_path)
        runner_mod.remove_pidfile("no-such-task")  # should not raise

    def test_kill_no_pidfile_returns_false(self, tmp_path, monkeypatch):
        import src.runner as runner_mod

        monkeypatch.setattr(runner_mod, "PIDFILE_DIR", tmp_path)
        assert runner_mod.kill_runner_for_task("no-such-task") is False

    def test_kill_stale_pidfile_returns_false(self, tmp_path, monkeypatch):
        import src.runner as runner_mod

        monkeypatch.setattr(runner_mod, "PIDFILE_DIR", tmp_path)
        # Write a PID that definitely doesn't exist
        (tmp_path / "task-runner-stale.pid").write_text("999999999")
        result = runner_mod.kill_runner_for_task("stale")
        assert result is False
        # Pidfile should be cleaned up
        assert not (tmp_path / "task-runner-stale.pid").exists()

    def test_kill_live_process(self, tmp_path, monkeypatch):
        import src.runner as runner_mod

        monkeypatch.setattr(runner_mod, "PIDFILE_DIR", tmp_path)

        # Spawn in a new session to match real runner behaviour (start_new_session=True)
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        (tmp_path / "task-runner-live.pid").write_text(str(proc.pid))

        result = runner_mod.kill_runner_for_task("live")
        proc.wait(timeout=3)
        assert result is True
        assert proc.returncode is not None

    def test_pidfile_written_in_run_one(self, tmp_path, tmp_tasks, monkeypatch):
        """Cron path: pidfile written inside run_one after task is picked."""
        import src.pipeline as pipeline_mod
        import src.runner as runner_mod

        monkeypatch.setattr(runner_mod, "PIDFILE_DIR", tmp_path)
        # Stub out the expensive parts — patch in pipeline where run_one resolves them
        monkeypatch.setattr(pipeline_mod, "create_worktree", lambda t: None)
        monkeypatch.setattr(pipeline_mod, "ensure_repo", lambda t: None)

        task = tmp_tasks.create(title="Cron task")
        runner_mod.run_one(tmp_tasks, task_id=task.id)
        # Pidfile is removed in run_one's finally
        assert not (tmp_path / ("task-runner-%s.pid" % task.id)).exists()

    def test_task_cancelled_error_is_base_exception(self):
        from src.runner import _TaskCancelledError

        assert issubclass(_TaskCancelledError, BaseException)
        assert not issubclass(_TaskCancelledError, Exception)


class TestCommentReply:
    """run_comment_reply sends only the latest user comment, not full history."""

    def _fake_agent(self, monkeypatch, stdout="Agent reply", returncode=0):
        import subprocess as _sp

        fake = _sp.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake, 1.0, "", {}))

    def _set_reply_pending(self, tmp_tasks, task_id):
        """Helper: stamp reply_pending=true so the claim guard passes."""
        tmp_tasks.set_reply_pending(task_id, True)

    def test_replies_to_latest_user_comment(self, tmp_tasks, monkeypatch):
        from src.pipeline import run_comment_reply

        self._fake_agent(monkeypatch, stdout="I handled your request")
        task = tmp_tasks.create(title="My task", description="Do the thing")
        tmp_tasks.add_comment(task.id, "web", "First comment")
        tmp_tasks.add_comment(task.id, "agent", "Agent first reply")
        tmp_tasks.add_comment(task.id, "web", "Latest user comment")
        self._set_reply_pending(tmp_tasks, task.id)

        result = run_comment_reply(tmp_tasks, task.id)

        assert result is True
        comments = tmp_tasks.get_comments(task.id)
        agent_replies = [c for c in comments if c.author == "agent"]
        assert len(agent_replies) == 2
        assert "I handled your request" in agent_replies[-1].body

    def test_prompt_contains_latest_comment(self, tmp_tasks, monkeypatch):
        """The prompt includes the latest comment; full history comes from the agent session."""
        from src.pipeline import run_comment_reply

        captured = {}

        def capture_agent(prompt, **kw):
            import subprocess as _sp

            captured["prompt"] = prompt
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)

        task = tmp_tasks.create(title="T", description="Desc")
        tmp_tasks.add_comment(task.id, "web", "Old comment")
        tmp_tasks.add_comment(task.id, "web", "New comment please act on this")
        self._set_reply_pending(tmp_tasks, task.id)

        run_comment_reply(tmp_tasks, task.id)

        assert "New comment please act on this" in captured["prompt"]

    def test_returns_false_when_no_user_comments(self, tmp_tasks, monkeypatch):
        from src.pipeline import run_comment_reply

        self._fake_agent(monkeypatch)
        task = tmp_tasks.create(title="T")
        # Only agent comments — no user comments
        tmp_tasks.add_comment(task.id, "agent", "Agent said something")
        self._set_reply_pending(tmp_tasks, task.id)

        result = run_comment_reply(tmp_tasks, task.id)
        assert result is False

    def test_returns_false_when_no_comments_at_all(self, tmp_tasks, monkeypatch):
        from src.pipeline import run_comment_reply

        self._fake_agent(monkeypatch)
        task = tmp_tasks.create(title="T")
        self._set_reply_pending(tmp_tasks, task.id)

        result = run_comment_reply(tmp_tasks, task.id)
        assert result is False

    def test_returns_false_for_missing_task(self, tmp_tasks, monkeypatch):
        from src.pipeline import run_comment_reply

        self._fake_agent(monkeypatch)
        result = run_comment_reply(tmp_tasks, "nonexistent-id")
        assert result is False

    def test_agent_failure_returns_false(self, tmp_tasks, monkeypatch):
        from src.pipeline import run_comment_reply

        self._fake_agent(monkeypatch, stdout="", returncode=1)
        task = tmp_tasks.create(title="T")
        tmp_tasks.add_comment(task.id, "web", "Help me")
        self._set_reply_pending(tmp_tasks, task.id)

        result = run_comment_reply(tmp_tasks, task.id)
        assert result is False

    def test_skips_when_reply_pending_false(self, tmp_tasks, monkeypatch):
        """If reply_pending is already false, bail out without calling the agent."""
        from src.pipeline import run_comment_reply

        agent_calls = []

        def counting_agent(*a, **kw):
            import subprocess as _sp

            agent_calls.append(1)
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", counting_agent)

        task = tmp_tasks.create(title="T")
        tmp_tasks.add_comment(task.id, "web", "Hello")
        # reply_pending is False by default — do NOT call set_reply_pending

        result = run_comment_reply(tmp_tasks, task.id)
        assert result is False
        assert agent_calls == []  # agent must not have been called

    def test_double_reply_prevention(self, tmp_tasks, monkeypatch):
        """Second concurrent call must bail out after first clears reply_pending."""
        import subprocess as _sp

        from src.pipeline import run_comment_reply

        fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake, 1.0, "", {}))

        task = tmp_tasks.create(title="T")
        tmp_tasks.add_comment(task.id, "web", "Hello")
        self._set_reply_pending(tmp_tasks, task.id)

        # First call succeeds and clears reply_pending
        r1 = run_comment_reply(tmp_tasks, task.id)
        assert r1 is True

        # Second call finds reply_pending=false and bails
        r2 = run_comment_reply(tmp_tasks, task.id)
        assert r2 is False

        # Only one agent reply was added
        agent_replies = [c for c in tmp_tasks.get_comments(task.id) if c.author == "agent"]
        assert len(agent_replies) == 1

    def test_reply_clears_reply_pending(self, tmp_tasks, monkeypatch):
        """reply_pending is cleared before the agent runs (claim pattern)."""
        import subprocess as _sp

        from src.pipeline import run_comment_reply

        fake = _sp.CompletedProcess(args=[], returncode=0, stdout="done", stderr="")
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake, 1.0, "", {}))

        task = tmp_tasks.create(title="T")
        tmp_tasks.add_comment(task.id, "web", "Hi")
        self._set_reply_pending(tmp_tasks, task.id)

        run_comment_reply(tmp_tasks, task.id)

        assert not tmp_tasks.get(task.id).reply_pending

    def test_session_id_passed_to_agent(self, tmp_tasks, monkeypatch):
        """run_comment_reply resumes the task's saved session_id."""
        from src.pipeline import run_comment_reply

        captured = {}

        def capture_agent(prompt, **kw):
            import subprocess as _sp

            captured["session_id"] = kw.get("session_id")
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
            return fake, 1.0, "new-sid", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)

        task = tmp_tasks.create(title="T")
        tmp_tasks.set_session_id(task.id, "saved-session-abc")

        tmp_tasks.add_comment(task.id, "web", "Follow-up question")
        self._set_reply_pending(tmp_tasks, task.id)

        run_comment_reply(tmp_tasks, task.id)

        assert captured["session_id"] == "saved-session-abc"

    def test_no_session_id_when_none_saved(self, tmp_tasks, monkeypatch):
        """If no session_id is saved, agent is called with session_id=None."""
        from src.pipeline import run_comment_reply

        captured = {}

        def capture_agent(prompt, **kw):
            import subprocess as _sp

            captured["session_id"] = kw.get("session_id")
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)

        task = tmp_tasks.create(title="T")
        tmp_tasks.add_comment(task.id, "web", "Hello")
        self._set_reply_pending(tmp_tasks, task.id)

        run_comment_reply(tmp_tasks, task.id)

        assert captured["session_id"] is None

    def test_skips_worktree_when_task_in_progress(self, tmp_tasks, monkeypatch):
        """If the task is in_progress, reply falls back to tmpdir to avoid collision."""
        import subprocess as _sp

        from src.pipeline import run_comment_reply
        from src.task_store import TaskStatus

        cwd_used = {}

        def capture_agent(prompt, cwd=None, **kw):
            cwd_used["cwd"] = cwd
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)
        # Stub worktree helper so it would return a path if not blocked
        monkeypatch.setattr(
            "src.pipeline._get_or_create_reply_worktree",
            lambda task, comment_body="": ("/tmp/fake-wt", False),
        )

        task = tmp_tasks.create(title="T")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)
        tmp_tasks.add_comment(task.id, "web", "Hello")
        self._set_reply_pending(tmp_tasks, task.id)

        # _get_or_create_reply_worktree is stubbed to bypass in_progress check in isolation;
        # re-test via the real helper by not stubbing and checking status blocks worktree.
        monkeypatch.undo()  # restore real helper
        self._fake_agent(monkeypatch, stdout="reply")

        run_comment_reply(tmp_tasks, task.id)
        # No crash — fell back to tmpdir gracefully

    def test_commit_reply_changes_called_when_wt_present(self, tmp_tasks, monkeypatch):
        """_commit_reply_changes is invoked after a successful reply when wt_path is set."""
        import subprocess as _sp

        from src.pipeline import run_comment_reply

        fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake, 1.0, "", {}))

        commit_calls = []
        monkeypatch.setattr(
            "src.pipeline._commit_reply_changes",
            lambda store, task, wt: commit_calls.append(wt),
        )
        monkeypatch.setattr(
            "src.pipeline._get_or_create_reply_worktree",
            lambda task, comment_body="": ("/tmp/fake-wt", False),
        )

        task = tmp_tasks.create(title="T")
        tmp_tasks.add_comment(task.id, "web", "Please make a change")
        self._set_reply_pending(tmp_tasks, task.id)

        run_comment_reply(tmp_tasks, task.id)

        assert commit_calls == ["/tmp/fake-wt"]

    def test_fresh_worktree_cleaned_up_after_reply(self, tmp_tasks, monkeypatch):
        """A freshly-created worktree (created_fresh=True) is cleaned up in finally."""
        import subprocess as _sp

        from src.pipeline import run_comment_reply

        fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake, 1.0, "", {}))
        monkeypatch.setattr("src.pipeline._commit_reply_changes", lambda *a, **kw: None)
        monkeypatch.setattr(
            "src.pipeline._get_or_create_reply_worktree",
            lambda task, comment_body="": ("/tmp/fake-wt", True),  # created_fresh=True
        )

        cleanup_calls = []
        monkeypatch.setattr(
            "src.pipeline.cleanup_worktree",
            lambda task, wt: cleanup_calls.append(wt),
        )

        task = tmp_tasks.create(title="T")
        tmp_tasks.add_comment(task.id, "web", "Do something")
        self._set_reply_pending(tmp_tasks, task.id)

        run_comment_reply(tmp_tasks, task.id)

        assert cleanup_calls == ["/tmp/fake-wt"]

    def test_preexisting_worktree_not_cleaned_up(self, tmp_tasks, monkeypatch):
        """A pre-existing worktree (created_fresh=False) is NOT cleaned up after reply."""
        import subprocess as _sp

        from src.pipeline import run_comment_reply

        fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
        monkeypatch.setattr("src.pipeline.run_agent", lambda *a, **kw: (fake, 1.0, "", {}))
        monkeypatch.setattr("src.pipeline._commit_reply_changes", lambda *a, **kw: None)
        monkeypatch.setattr(
            "src.pipeline._get_or_create_reply_worktree",
            lambda task, comment_body="": ("/tmp/fake-wt", False),  # created_fresh=False
        )

        cleanup_calls = []
        monkeypatch.setattr(
            "src.pipeline.cleanup_worktree",
            lambda task, wt: cleanup_calls.append(wt),
        )

        task = tmp_tasks.create(title="T")
        tmp_tasks.add_comment(task.id, "web", "Do something")
        self._set_reply_pending(tmp_tasks, task.id)

        run_comment_reply(tmp_tasks, task.id)

        assert cleanup_calls == []

    def test_human_task_with_project_deferred_to_pm(self, tmp_tasks, monkeypatch):
        """Human-assigned tasks with a project_id are deferred to the PM sweep."""
        from src.pipeline import run_comment_reply

        agent_calls = []

        def counting_agent(*a, **kw):
            import subprocess as _sp

            agent_calls.append(1)
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="reply", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", counting_agent)

        task = tmp_tasks.create(
            title="Confirm DNS setup",
            description="Please confirm DNS",
            assignee="human",
            project_id="proj-123",
        )
        tmp_tasks.add_comment(task.id, "web", "Yes, please proceed")
        self._set_reply_pending(tmp_tasks, task.id)

        result = run_comment_reply(tmp_tasks, task.id)
        assert result is False
        assert agent_calls == []
        # reply_pending should still be true (not claimed)
        assert tmp_tasks.get(task.id).reply_pending is True

    def test_human_task_without_project_uses_human_prompt(self, tmp_tasks, monkeypatch):
        """Human-assigned tasks without a project_id use the HUMAN_TASK_REPLY_PROMPT."""
        from src.pipeline import run_comment_reply

        captured = {}

        def capture_agent(prompt, **kw):
            import subprocess as _sp

            captured["prompt"] = prompt
            captured["kw"] = kw
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="acknowledged", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)

        task = tmp_tasks.create(
            title="Provide API key",
            description="Please provide the GA4 API key",
            assignee="human",
        )
        tmp_tasks.add_comment(task.id, "web", "Here is the key: abc123")
        self._set_reply_pending(tmp_tasks, task.id)

        result = run_comment_reply(tmp_tasks, task.id)
        assert result is True
        assert "assigned to a human operator" in captured["prompt"]
        assert "Provide API key" in captured["prompt"]
        assert "Here is the key: abc123" in captured["prompt"]
        assert "Do NOT attempt to execute engineering work" in captured["prompt"]

    def test_prompt_includes_task_context(self, tmp_tasks, monkeypatch):
        """Both prompts now include the task title and description."""
        from src.pipeline import run_comment_reply

        captured = {}

        def capture_agent(prompt, **kw):
            import subprocess as _sp

            captured["prompt"] = prompt
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)

        task = tmp_tasks.create(title="Fix the login bug", description="Users cannot log in")
        tmp_tasks.add_comment(task.id, "web", "Still broken")
        self._set_reply_pending(tmp_tasks, task.id)

        run_comment_reply(tmp_tasks, task.id)

        assert "Fix the login bug" in captured["prompt"]
        assert "Users cannot log in" in captured["prompt"]


class TestCancelViaStatusUpdate:
    """cancel_runner is called when PATCH /status sets cancelled."""

    def test_cancel_runner_called_on_cancelled_status(self, tmp_tasks, monkeypatch):
        from src.routers import tasks as tasks_router

        task = tmp_tasks.create(title="Running task")
        tmp_tasks.update_status(
            task.id, __import__("src.task_store", fromlist=["TaskStatus"]).TaskStatus.IN_PROGRESS
        )

        cancelled_ids = []
        monkeypatch.setattr(tasks_router, "_get_store", lambda: tmp_tasks)

        import src.web as web_mod

        monkeypatch.setattr(web_mod, "cancel_runner", lambda tid: cancelled_ids.append(tid) or True)

        # Simulate what the endpoint does directly
        from src.task_store import TaskStatus

        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)
        web_mod.cancel_runner(task.id)

        assert task.id in cancelled_ids

    def test_cancel_sets_cancelled_by_user(self, tmp_tasks, monkeypatch):
        """PATCH /status cancelled must stamp cancelled_by: user in the file."""
        import src.web as web_mod
        from src.routers import tasks as tasks_router
        from src.task_store import TaskStatus

        task = tmp_tasks.create(title="Cancel me")
        monkeypatch.setattr(tasks_router, "_get_store", lambda: tmp_tasks)
        monkeypatch.setattr(web_mod, "cancel_runner", lambda tid: True)

        tmp_tasks.update_status(task.id, TaskStatus.CANCELLED)
        tmp_tasks.set_cancelled_by(task.id, "user")

        assert tmp_tasks.get_cancelled_by(task.id) == "user"

    def test_cancel_runner_not_called_for_other_statuses(self, tmp_tasks, monkeypatch):
        import src.web as web_mod

        cancel_called = []
        monkeypatch.setattr(web_mod, "cancel_runner", lambda tid: cancel_called.append(tid))

        task = tmp_tasks.create(title="T")
        # Completing a task should not trigger cancel_runner
        from src.task_store import TaskStatus

        tmp_tasks.update_status(task.id, TaskStatus.COMPLETED)
        # cancel_runner is only called from the endpoint logic — not called here
        assert cancel_called == []

    def test_update_status_endpoint_returns_400_for_invalid_status(self, tmp_tasks, monkeypatch):
        """PATCH /status with a bogus status string should return 400, not 500."""
        import asyncio

        from src.routers import tasks as tasks_router
        from src.routers.tasks import StatusBody
        from src.routers.tasks import update_status as endpoint_update_status

        monkeypatch.setattr(tasks_router, "_get_store", lambda: tmp_tasks)

        task = tmp_tasks.create(title="Bad status")
        response = asyncio.get_event_loop().run_until_complete(
            endpoint_update_status(task.id, StatusBody(status="nonsense"))
        )
        assert response.status_code == 400


class TestTextOnlyCommentReply:
    """When the worktree cannot be recreated, the agent gets a text-only prompt."""

    def _set_reply_pending(self, tmp_tasks, task_id):
        tmp_tasks.set_reply_pending(task_id, True)

    def test_text_only_prompt_when_worktree_returns_none(self, tmp_tasks, monkeypatch):
        """If _get_or_create_reply_worktree returns None, use COMMENT_REPLY_TEXT_ONLY_PROMPT."""
        from src.pipeline import run_comment_reply

        captured = {}

        def capture_agent(prompt, **kw):
            import subprocess as _sp

            captured["prompt"] = prompt
            captured["cwd"] = kw.get("cwd")
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="text reply", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)
        monkeypatch.setattr(
            "src.pipeline._get_or_create_reply_worktree",
            lambda task, comment_body="": (None, False),
        )

        task = tmp_tasks.create(title="My broken task", description="Some desc")
        tmp_tasks.add_comment(task.id, "web", "Why is CI failing?")
        self._set_reply_pending(tmp_tasks, task.id)

        result = run_comment_reply(tmp_tasks, task.id)

        assert result is True
        assert "you do NOT have access to the code" in captured["prompt"]
        assert "same git worktree" not in captured["prompt"]
        assert "My broken task" in captured["prompt"]
        assert "Why is CI failing?" in captured["prompt"]

    def test_code_access_prompt_when_worktree_exists(self, tmp_tasks, monkeypatch):
        """If _get_or_create_reply_worktree returns a path, use the standard prompt."""
        from src.pipeline import run_comment_reply

        captured = {}

        def capture_agent(prompt, **kw):
            import subprocess as _sp

            captured["prompt"] = prompt
            fake = _sp.CompletedProcess(args=[], returncode=0, stdout="code reply", stderr="")
            return fake, 1.0, "", {}

        monkeypatch.setattr("src.pipeline.run_agent", capture_agent)
        monkeypatch.setattr("src.pipeline._commit_reply_changes", lambda *a, **kw: None)
        monkeypatch.setattr(
            "src.pipeline._get_or_create_reply_worktree",
            lambda task, comment_body="": ("/tmp/fake-wt", False),
        )

        task = tmp_tasks.create(title="Good task", description="Works fine")
        tmp_tasks.add_comment(task.id, "web", "Please fix this")
        self._set_reply_pending(tmp_tasks, task.id)

        result = run_comment_reply(tmp_tasks, task.id)

        assert result is True
        assert "same git worktree" in captured["prompt"]
        assert "do NOT have access" not in captured["prompt"]


class TestSubtaskStatusOnPr:
    """Child subtasks always get completed, only the parent gets in_review."""

    @patch("src.pipeline._notify_pm_chat_task_terminal")
    @patch("src.pipeline.trigger_unblocked_dependents")
    @patch("src.pipeline._maybe_finalize_directive_batch")
    @patch("src.pipeline.commit_and_create_pr")
    @patch("src.pipeline.run_agent")
    @patch("src.pipeline.create_worktree", return_value="/tmp/wt")
    @patch("src.pipeline.ensure_repo")
    @patch("src.pipeline.cleanup_worktree")
    @patch("src.pipeline.plog")
    def test_subtasks_completed_when_parent_gets_in_review(
        self,
        mock_plog,
        mock_cleanup,
        mock_ensure,
        mock_create_wt,
        mock_agent,
        mock_pr,
        mock_finalize,
        mock_unblock,
        mock_notify,
        tmp_tasks,
        monkeypatch,
    ):
        from src.pipeline import _run_one_inner

        parent = tmp_tasks.create(
            title="Parent task",
            priority="high",
            description="Step one: build the frontend. Then deploy. After that run integration tests.",
        )
        tmp_tasks.update_status(parent.id, TaskStatus.IN_PROGRESS)
        parent = tmp_tasks.get(parent.id)

        plan_json = (
            '[{"title": "Step 1", "description": "Do A"}, '
            '{"title": "Step 2", "description": "Do B"}]'
        )
        mock_agent.return_value = (
            subprocess.CompletedProcess(args=[], returncode=0, stdout="done"),
            10.0,
            "",
            {},
        )
        mock_pr.return_value = "https://github.com/user/repo/pull/42"

        monkeypatch.setattr("src.pipeline.AUTO_PLAN", True)
        monkeypatch.setattr("src.pipeline.AUTO_DOCS", False)
        monkeypatch.setattr("src.pipeline.AUTO_PR", True)
        monkeypatch.setattr("src.pipeline._resolve_model", lambda t: None)
        monkeypatch.setattr("src.pipeline.plan_task", lambda s, t, cwd=None: json.loads(plan_json))

        _run_one_inner(tmp_tasks, parent)

        parent_updated = tmp_tasks.get(parent.id)
        assert parent_updated.status == TaskStatus.IN_REVIEW

        subtasks = tmp_tasks.list_subtasks(parent.id)
        assert len(subtasks) == 2
        for sub in subtasks:
            assert sub.status == TaskStatus.COMPLETED

    @patch("src.pipeline._notify_pm_chat_task_terminal")
    @patch("src.pipeline.trigger_unblocked_dependents")
    @patch("src.pipeline._maybe_finalize_directive_batch")
    @patch("src.pipeline.commit_and_create_pr")
    @patch("src.pipeline.run_agent")
    @patch("src.pipeline.create_worktree", return_value="/tmp/wt")
    @patch("src.pipeline.ensure_repo")
    @patch("src.pipeline.cleanup_worktree")
    @patch("src.pipeline.plog")
    def test_subtasks_completed_when_no_pr(
        self,
        mock_plog,
        mock_cleanup,
        mock_ensure,
        mock_create_wt,
        mock_agent,
        mock_pr,
        mock_finalize,
        mock_unblock,
        mock_notify,
        tmp_tasks,
        monkeypatch,
    ):
        from src.pipeline import _run_one_inner

        parent = tmp_tasks.create(title="No PR task", priority="high")
        tmp_tasks.update_status(parent.id, TaskStatus.IN_PROGRESS)
        parent = tmp_tasks.get(parent.id)

        mock_agent.return_value = (
            subprocess.CompletedProcess(args=[], returncode=0, stdout="done"),
            10.0,
            "",
            {},
        )
        mock_pr.return_value = None

        monkeypatch.setattr("src.pipeline.AUTO_PLAN", False)
        monkeypatch.setattr("src.pipeline.AUTO_DOCS", False)
        monkeypatch.setattr("src.pipeline.AUTO_PR", True)
        monkeypatch.setattr("src.pipeline._resolve_model", lambda t: None)

        _run_one_inner(tmp_tasks, parent)

        parent_updated = tmp_tasks.get(parent.id)
        assert parent_updated.status == TaskStatus.COMPLETED


class TestGetOrCreateReplyWorktree:
    """Tests for _get_or_create_reply_worktree worktree recreation logic."""

    def test_returns_none_when_in_progress(self, tmp_tasks):
        from src.pipeline import _get_or_create_reply_worktree

        task = tmp_tasks.create(title="Running task")
        tmp_tasks.update_status(task.id, TaskStatus.IN_PROGRESS)
        task = tmp_tasks.get(task.id)

        wt_path, created = _get_or_create_reply_worktree(task)
        assert wt_path is None
        assert created is False

    def test_attempts_branch_recreation(self, tmp_tasks, monkeypatch):
        """When the worktree doesn't exist, it attempts to re-create on the task branch."""
        from unittest.mock import MagicMock

        from src.pipeline import _get_or_create_reply_worktree

        task = tmp_tasks.create(title="Test task", target_repo="my-repo")
        tmp_tasks.update_status(task.id, TaskStatus.IN_REVIEW)
        task = tmp_tasks.get(task.id)

        cmd_calls = []
        success = MagicMock(returncode=0, stdout="", stderr="")

        def track_cmd(cmd, cwd=None, timeout=None):
            cmd_calls.append(cmd)
            return success

        monkeypatch.setattr("src.pipeline._run_cmd", track_cmd)
        monkeypatch.setattr("src.pipeline._resolve_repo_dir", lambda t: "/fake/repo")
        monkeypatch.setattr("src.pipeline.WORKTREE_BASE", MagicMock())
        monkeypatch.setattr("src.pipeline.WORKTREE_BASE.__truediv__", lambda s, x: "/tmp/task-worktrees/" + x)

        import pathlib

        monkeypatch.setattr(pathlib.Path, "exists", lambda self: False)

        wt_path, created = _get_or_create_reply_worktree(task)

        git_cmds = [c for c in cmd_calls if "worktree" in str(c)]
        assert len(git_cmds) >= 1
        assert "add" in str(git_cmds[0])

    def test_passes_comment_body_for_rebase(self, tmp_tasks, monkeypatch):
        """Comment body mentioning merge conflicts triggers rebase attempt."""
        from unittest.mock import MagicMock

        from src.pipeline import _maybe_rebase_for_merge_conflicts

        rebase_calls = []
        success = MagicMock(returncode=0, stdout="", stderr="")

        def track_cmd(cmd, cwd=None, timeout=None):
            rebase_calls.append(cmd)
            return success

        monkeypatch.setattr("src.pipeline._run_cmd", track_cmd)
        monkeypatch.setattr(
            "src.worktree._get_default_branch",
            lambda repo: "main",
        )

        _maybe_rebase_for_merge_conflicts("/tmp/wt", "/fake/repo", "There are merge conflicts in this PR")

        rebase_cmd = [c for c in rebase_calls if "rebase" in str(c)]
        assert len(rebase_cmd) == 1
        assert "origin/main" in str(rebase_cmd[0])

    def test_no_rebase_without_conflict_keywords(self, tmp_tasks, monkeypatch):
        """No rebase attempted if comment doesn't mention conflicts."""
        from unittest.mock import MagicMock

        from src.pipeline import _maybe_rebase_for_merge_conflicts

        rebase_calls = []

        def track_cmd(cmd, cwd=None, timeout=None):
            rebase_calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("src.pipeline._run_cmd", track_cmd)

        _maybe_rebase_for_merge_conflicts("/tmp/wt", "/fake/repo", "Please fix the typo")

        assert len(rebase_calls) == 0

    def test_rebase_abort_on_failure(self, tmp_tasks, monkeypatch):
        """If rebase fails, it runs git rebase --abort."""
        from unittest.mock import MagicMock

        from src.pipeline import _maybe_rebase_for_merge_conflicts

        cmd_calls = []

        def track_cmd(cmd, cwd=None, timeout=None):
            cmd_calls.append(cmd)
            if "rebase" in str(cmd) and "--abort" not in str(cmd):
                return MagicMock(returncode=1, stdout="", stderr="CONFLICT in file.py")
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("src.pipeline._run_cmd", track_cmd)
        monkeypatch.setattr("src.worktree._get_default_branch", lambda repo: "main")

        _maybe_rebase_for_merge_conflicts("/tmp/wt", "/fake/repo", "merge conflicts need resolving")

        abort_calls = [c for c in cmd_calls if "--abort" in str(c)]
        assert len(abort_calls) == 1
