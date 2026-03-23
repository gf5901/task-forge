"""Tests for src/dynamo_store.py — helpers and DynamoTaskStore with mocked table."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.dynamo_store import (
    DynamoTaskStore,
    _pk,
    _priority_sort_created,
    _section_to_sk_prefix,
    _task_from_item,
    _task_to_meta_item,
)
from src.task_store import Comment, Task, TaskPriority, TaskStatus


class TestPureHelpers:
    def test_pk_and_priority_sort(self):
        assert _pk("abc123") == "TASK#abc123"
        assert _priority_sort_created("urgent", "2020-01-01") == "0#2020-01-01"
        assert _priority_sort_created("unknown", "2020-01-01") == "2#2020-01-01"

    def test_section_to_sk_prefix(self):
        assert _section_to_sk_prefix("Agent Output") == "OUTPUT"
        assert _section_to_sk_prefix("Doc Update") == "OUTPUT"
        assert _section_to_sk_prefix("Comment (x)") == "COMMENT"
        assert _section_to_sk_prefix("Plan") == "PLAN"
        assert _section_to_sk_prefix("pipeline log") == "LOG"
        assert _section_to_sk_prefix("other") == "OUTPUT"

    def test_task_from_item_pk_only(self):
        item = {"pk": "TASK#deadbeef", "sk": "META", "title": "T"}
        t = _task_from_item(item)
        assert t.id == "deadbeef"
        assert t.title == "T"

    def test_task_from_item_tags_string(self):
        item = {
            "task_id": "x",
            "tags": "[a, b]",
            "depends_on": "[d1, d2]",
            "status": "pending",
            "priority": "medium",
        }
        t = _task_from_item(item)
        assert t.tags == ["a", "b"]
        assert t.depends_on == ["d1", "d2"]

    def test_task_to_meta_item_optional_fields(self):
        task = Task(
            id="t1",
            title="Hi",
            description="D",
            status=TaskStatus.PENDING,
            priority=TaskPriority.HIGH,
            target_repo="r",
            parent_id="p",
            model="m",
            plan_only=True,
            depends_on=["a"],
            session_id="s",
            reply_pending=True,
            role="be_engineer",
            spawned_by="sp",
            project_id="proj",
            directive_sk="DIR#1",
            directive_date="2025-01-01",
        )
        item = _task_to_meta_item(task)
        assert item["target_repo"] == "r"
        assert item["plan_only"] is True
        assert item["reply_pending"] is True
        assert item["project_id"] == "proj"


def _make_store(table):
    """Build DynamoTaskStore without calling boto3.resource."""
    store = object.__new__(DynamoTaskStore)
    store._table = table
    store._ddb = MagicMock()
    store._ddb.meta.client.exceptions.ConditionalCheckFailedException = type(
        "ConditionalCheckFailedException",
        (Exception,),
        {},
    )
    store._table_name = "test-table"
    return store


def test_create_uses_put_item(monkeypatch):
    table = MagicMock()
    ddb = MagicMock()
    ddb.Table.return_value = table
    fake_boto = MagicMock()
    fake_boto.resource = lambda *args, **kwargs: ddb
    monkeypatch.setattr("src.dynamo_store.boto3", fake_boto)
    store = DynamoTaskStore(table_name="t", region="us-west-2")
    t = store.create(title="Hello", description="Body")
    assert len(t.id) == 8
    table.put_item.assert_called_once()
    call_kw = table.put_item.call_args[1]
    assert call_kw["Item"]["title"] == "Hello"


def test_get_missing_and_present():
    table = MagicMock()
    table.get_item.return_value = {}
    store = _make_store(table)
    assert store.get("nope") is None
    table.get_item.return_value = {
        "Item": {
            "pk": "TASK#abc",
            "sk": "META",
            "task_id": "abc",
            "title": "T",
            "description": "",
            "status": "pending",
            "priority": "medium",
        }
    }
    t = store.get("abc")
    assert t is not None
    assert t.title == "T"


def test_update_status_success():
    table = MagicMock()
    table.update_item.return_value = {
        "Attributes": {
            "pk": "TASK#x",
            "sk": "META",
            "task_id": "x",
            "title": "T",
            "description": "",
            "status": "completed",
            "priority": "medium",
            "created_at": "2020-01-01T00:00:00+00:00",
            "updated_at": "2020-01-02T00:00:00+00:00",
        }
    }
    table.get_item.return_value = {
        "Item": {
            "priority": "medium",
            "created_at": "2020-01-01T00:00:00+00:00",
        }
    }
    store = _make_store(table)
    out = store.update_status("x", TaskStatus.COMPLETED)
    assert out is not None
    assert out.status == TaskStatus.COMPLETED


def test_update_status_conditional_failure():
    table = MagicMock()
    CCF = type("CCF", (Exception,), {})
    store = _make_store(table)
    store._ddb.meta.client.exceptions.ConditionalCheckFailedException = CCF
    table.update_item.side_effect = CCF()
    table.get_item.return_value = {"Item": {"priority": "medium", "created_at": "2020-01-01"}}
    assert store.update_status("missing", TaskStatus.COMPLETED) is None


def test_list_tasks_scan_paths():
    table = MagicMock()
    meta = {
        "pk": "TASK#a",
        "sk": "META",
        "task_id": "a",
        "title": "A",
        "description": "",
        "status": "pending",
        "priority": "medium",
        "created_at": "2020-01-01",
        "updated_at": "2020-01-01",
    }
    table.scan.side_effect = [
        {"Items": [meta], "LastEvaluatedKey": {"pk": "x", "sk": "y"}},
        {"Items": []},
    ]
    store = _make_store(table)
    tasks = store.list_tasks(status=TaskStatus.PENDING)
    assert len(tasks) == 1
    assert tasks[0].id == "a"


def test_list_tasks_parent_index_query():
    table = MagicMock()
    meta = {
        "pk": "TASK#c",
        "sk": "META",
        "task_id": "c",
        "title": "Child",
        "description": "",
        "status": "pending",
        "priority": "medium",
        "parent_id": "p1",
        "created_at": "2020-01-01",
        "updated_at": "2020-01-01",
    }
    table.query.return_value = {"Items": [meta]}
    store = _make_store(table)
    tasks = store.list_tasks(parent_id="p1", status=TaskStatus.PENDING)
    assert len(tasks) == 1
    assert tasks[0].parent_id == "p1"


def test_deps_ready_and_find_dependents():
    table = MagicMock()

    def get_item(Key, **kwargs):
        tid = Key["pk"].replace("TASK#", "")
        if tid == "dep":
            return {
                "Item": {
                    "task_id": "dep",
                    "title": "D",
                    "description": "",
                    "status": "completed",
                    "priority": "medium",
                }
            }
        if tid == "dep2":
            return {
                "Item": {
                    "task_id": "dep2",
                    "title": "D2",
                    "description": "",
                    "status": "pending",
                    "priority": "medium",
                }
            }
        return {}

    table.get_item.side_effect = get_item
    store = _make_store(table)

    ok = Task(
        id="t",
        title="T",
        depends_on=["dep"],
    )
    assert store.deps_ready(ok) is True

    bad = Task(
        id="t2",
        title="T2",
        depends_on=["dep2"],
    )
    assert store.deps_ready(bad) is False

    table.scan.side_effect = [
        {
            "Items": [
                {
                    "pk": "TASK#child",
                    "sk": "META",
                    "task_id": "child",
                    "title": "C",
                    "description": "",
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": ["dep"],
                    "created_at": "2020-01-01",
                    "updated_at": "2020-01-01",
                }
            ]
        }
    ]
    found = store.find_dependents("dep")
    assert len(found) == 1
    assert found[0].id == "child"


def test_maybe_finalize_directive_batch(monkeypatch):
    table = MagicMock()
    store = _make_store(table)
    proj_task = Task(
        id="t1",
        title="T",
        project_id="proj1",
        directive_sk="DIR#x",
        status=TaskStatus.COMPLETED,
    )
    other = Task(
        id="t2",
        title="T2",
        project_id="proj1",
        directive_sk="DIR#x",
        status=TaskStatus.COMPLETED,
    )
    store.get = MagicMock(return_value=proj_task)
    store.list_tasks_for_project = MagicMock(return_value=[proj_task, other])
    called = []

    def fake_update(pid, updates):
        called.append((pid, updates))

    monkeypatch.setattr("src.projects_dynamo.update_project", fake_update)
    store.maybe_finalize_directive_batch("t1")
    assert len(called) == 1
    assert called[0][0] == "proj1"
    assert called[0][1].get("awaiting_next_directive") is True


def test_maybe_finalize_skips_when_not_all_terminal(monkeypatch):
    store = _make_store(MagicMock())
    proj_task = Task(
        id="t1",
        title="T",
        project_id="proj1",
        directive_sk="DIR#x",
        status=TaskStatus.COMPLETED,
    )
    pending = Task(
        id="t2",
        title="T2",
        project_id="proj1",
        directive_sk="DIR#x",
        status=TaskStatus.PENDING,
    )
    store.get = MagicMock(return_value=proj_task)
    store.list_tasks_for_project = MagicMock(return_value=[proj_task, pending])
    called = []
    monkeypatch.setattr("src.projects_dynamo.update_project", lambda *a, **k: called.append(1))
    store.maybe_finalize_directive_batch("t1")
    assert len(called) == 0


def test_delete_and_batch_writer():
    table = MagicMock()
    table.query.return_value = {
        "Items": [
            {"pk": "TASK#z", "sk": "META"},
            {"pk": "TASK#z", "sk": "OUTPUT#1"},
        ]
    }
    batch = MagicMock()
    batch.__enter__ = lambda self: self
    batch.__exit__ = lambda self, *a: None
    table.batch_writer.return_value = batch
    store = _make_store(table)
    assert store.delete("z") is True
    assert batch.delete_item.call_count == 2


def test_delete_false_when_empty():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    store = _make_store(table)
    assert store.delete("z") is False


def test_append_section_and_has_section():
    table = MagicMock()
    table.query.return_value = {"Items": [{"pk": "TASK#q", "sk": "OUTPUT#ts"}]}
    store = _make_store(table)
    store.append_section("q", "Agent Output", "hello")
    table.put_item.assert_called_once()
    assert store.has_section("q", "Agent Output") is True


def test_append_agent_result(monkeypatch):
    table = MagicMock()
    store = _make_store(table)
    monkeypatch.setattr(
        "src.agent._extract_agent_text",
        lambda stdout: "out",
    )
    res = SimpleNamespace(returncode=0, stdout="x", stderr="")
    store.append_agent_result("q", res)
    table.put_item.assert_called_once()
    kw = table.put_item.call_args[1]["Item"]
    assert "out" in kw["body"]

    res2 = SimpleNamespace(returncode=1, stdout="o", stderr="err line")
    store.append_agent_result("q", res2)
    body = table.put_item.call_args[1]["Item"]["body"]
    assert "Exit code" in body
    assert "err line" in body


def test_set_field_swallows_exception():
    table = MagicMock()
    table.update_item.side_effect = RuntimeError("ddb")
    store = _make_store(table)
    store.set_field("id", "model", "m")  # should not raise


def test_set_plan_only_and_reply_pending_and_clear_cancelled():
    table = MagicMock()
    store = _make_store(table)
    store.set_plan_only("a", True)
    store.set_plan_only("a", False)
    store.set_reply_pending("a", True)
    store.set_reply_pending("a", False)
    store.clear_cancelled_by("a")
    assert table.update_item.call_count == 5


def test_get_reads():
    table = MagicMock()
    table.get_item.return_value = {"Item": {"pr_url": "https://pr"}}
    store = _make_store(table)
    assert store.get_pr_url("a") == "https://pr"

    table.query.return_value = {"Items": [{"body": "agent text"}]}
    assert store.get_agent_output("a") == "agent text"
    table.query.return_value = {"Items": []}
    assert store.get_agent_output("a") is None

    table.query.return_value = {"Items": [{"author": "u", "body": "c", "created_at": "t"}]}
    comments = store.get_comments("a")
    assert len(comments) == 1
    assert isinstance(comments[0], Comment)


def test_add_comment_missing_task():
    table = MagicMock()
    store = _make_store(table)
    store.get = MagicMock(return_value=None)
    assert store.add_comment("n", "a", "b") is None


def test_find_task_by_pr_url():
    table = MagicMock()
    table.query.return_value = {"Items": [{"task_id": "tid"}]}
    store = _make_store(table)
    assert store.find_task_by_pr_url("https://github.com/x/y/pull/1") == "tid"
    table.query.assert_called_once()
    call_kw = table.query.call_args[1]
    assert call_kw["IndexName"] == "pr-index"
    assert call_kw["Limit"] == 1


def test_find_task_by_pr_url_none():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    store = _make_store(table)
    assert store.find_task_by_pr_url("https://x") is None


def test_get_repos_merges_known_repos(monkeypatch):
    table = MagicMock()
    table.scan.side_effect = [
        {
            "Items": [
                {"target_repo": "alpha"},
                {"target_repo": "  beta  "},
                {"target_repo": ""},
            ],
            "LastEvaluatedKey": {"pk": "x"},
        },
        {"Items": [{"target_repo": "gamma"}]},
    ]
    monkeypatch.setenv("KNOWN_REPOS", " delta , ")
    store = _make_store(table)
    repos = store.get_repos()
    assert repos == ["alpha", "beta", "delta", "gamma"]


def test_write_log_event_decimal_for_float():
    table = MagicMock()
    store = _make_store(table)
    store.write_log_event("t1", "done", "execute", "ok", inputTokens=1000000, ratio=0.5)
    item = table.put_item.call_args[1]["Item"]
    assert item["inputTokens"] == 1000000
    assert item["ratio"] == Decimal("0.5")


def test_list_reply_pending_and_list_spawned():
    table = MagicMock()
    store = _make_store(table)
    meta = {
        "pk": "TASK#rp",
        "sk": "META",
        "task_id": "rp",
        "title": "R",
        "description": "",
        "status": "pending",
        "priority": "medium",
        "reply_pending": True,
        "created_at": "2020-01-01",
        "updated_at": "2020-01-01",
    }
    table.scan.side_effect = [{"Items": [meta]}]
    r = store.list_reply_pending()
    assert len(r) == 1
    assert r[0].reply_pending is True

    table.scan.return_value = {"Items": [meta]}
    table.scan.side_effect = None
    sp = store.list_spawned_tasks("root")
    assert len(sp) == 1


def test_list_merged_not_deployed_and_get_timestamps():
    table = MagicMock()
    table.scan.return_value = {"Items": [{"task_id": "m1"}]}
    store = _make_store(table)
    assert store.list_merged_not_deployed() == ["m1"]

    table.get_item.return_value = {"Item": {"merged_at": "t1", "cancelled_by": "user"}}
    assert store.get_merged_at("x") == "t1"
    assert store.get_cancelled_by("x") == "user"
    table.get_item.return_value = {"Item": {"deployed_at": "d1"}}
    assert store.get_deployed_at("x") == "d1"


def test_replan_as_pending():
    table = MagicMock()
    table.update_item.return_value = {"Attributes": {}}
    table.get_item.return_value = {"Item": {"priority": "medium", "created_at": "2020-01-01"}}
    store = _make_store(table)
    store.replan_as_pending("z")
    assert table.update_item.call_count >= 2


def test_set_depends_on_and_model_helpers():
    table = MagicMock()
    store = _make_store(table)
    store.set_depends_on("a", ["b"])
    store.set_model("a", "fast")
    store.set_session_id("a", "sess")
    store.set_pr_url("a", "url")
    store.set_merged_at("a", "t")
    store.set_deployed_at("a", "t")
    assert table.update_item.called


def test_add_comment_success():
    table = MagicMock()
    store = _make_store(table)
    task = Task(id="tid", title="T")
    store.get = MagicMock(return_value=task)
    c = store.add_comment("tid", "me", "hello")
    assert c is not None
    assert c.author == "me"
    table.put_item.assert_called_once()


def test_list_tasks_for_project_empty_id_returns_empty(tmp_tasks):
    assert tmp_tasks.list_tasks_for_project("") == []


def test_list_human_reply_pending_empty_project_returns_empty(tmp_tasks):
    assert tmp_tasks.list_human_reply_pending_for_project("") == []
