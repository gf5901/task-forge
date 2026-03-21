"""
DynamoDB-backed task store (`DynamoTaskStore`).

Table schema (single-table):
  PK: TASK#{id}   SK: META            — core metadata
  PK: TASK#{id}   SK: OUTPUT#{ts}     — agent output sections
  PK: TASK#{id}   SK: COMMENT#{ts}    — comments
  PK: TASK#{id}   SK: PLAN#{ts}       — plan sections
  PK: TASK#{id}   SK: LOG#{ts}        — pipeline events

GSIs: status-index, repo-index, parent-index, pr-index
"""

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key

from .task_store import Comment, Task, TaskPriority, TaskStatus

log = logging.getLogger(__name__)

TABLE_NAME = os.getenv("DYNAMO_TABLE", "agent-tasks")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

PRIORITY_SORT = {"urgent": "0", "high": "1", "medium": "2", "low": "3"}


def _pk(task_id: str) -> str:
    return "TASK#%s" % task_id


def _priority_sort_created(priority: str, created_at: str) -> str:
    return "%s#%s" % (PRIORITY_SORT.get(priority, "2"), created_at)


def _task_from_item(item: Dict[str, Any]) -> Task:
    task_id = item.get("task_id", "")
    if not task_id and "pk" in item:
        task_id = item["pk"].replace("TASK#", "", 1)

    tags_raw = item.get("tags", [])
    if isinstance(tags_raw, str):
        tags_raw = [t.strip() for t in tags_raw.strip("[]").split(",") if t.strip()]

    depends_raw = item.get("depends_on", [])
    if isinstance(depends_raw, str):
        depends_raw = [d.strip() for d in depends_raw.strip("[]").split(",") if d.strip()]

    return Task(
        id=task_id,
        title=item.get("title", ""),
        description=item.get("description", ""),
        status=TaskStatus(item.get("status", "pending")),
        priority=TaskPriority(item.get("priority", "medium")),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
        created_by=item.get("created_by", ""),
        tags=tags_raw if isinstance(tags_raw, list) else [],
        target_repo=item.get("target_repo", ""),
        parent_id=item.get("parent_id", ""),
        model=item.get("model", ""),
        plan_only=bool(item.get("plan_only", False)),
        depends_on=depends_raw if isinstance(depends_raw, list) else [],
        session_id=item.get("session_id", ""),
        reply_pending=bool(item.get("reply_pending", False)),
        role=item.get("role", ""),
        spawned_by=item.get("spawned_by", ""),
        project_id=item.get("project_id", ""),
        directive_sk=item.get("directive_sk", ""),
        directive_date=item.get("directive_date", ""),
        assignee=item.get("assignee", "agent"),
    )


def _task_to_meta_item(task: Task) -> Dict[str, Any]:
    item = {
        "pk": _pk(task.id),
        "sk": "META",
        "task_id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "priority": task.priority.value,
        "priority_sort_created": _priority_sort_created(task.priority.value, task.created_at),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "created_by": task.created_by,
        "tags": task.tags or [],
    }
    if task.target_repo:
        item["target_repo"] = task.target_repo
    if task.parent_id:
        item["parent_id"] = task.parent_id
    if task.model:
        item["model"] = task.model
    if task.plan_only:
        item["plan_only"] = True
    if task.depends_on:
        item["depends_on"] = task.depends_on
    if task.session_id:
        item["session_id"] = task.session_id
    if task.reply_pending:
        item["reply_pending"] = True
    if task.role:
        item["role"] = task.role
    if task.spawned_by:
        item["spawned_by"] = task.spawned_by
    if task.project_id:
        item["project_id"] = task.project_id
    if task.directive_sk:
        item["directive_sk"] = task.directive_sk
    if task.directive_date:
        item["directive_date"] = task.directive_date
    if task.assignee and task.assignee != "agent":
        item["assignee"] = task.assignee
    return item


class DynamoTaskStore:
    def __init__(self, table_name: str = TABLE_NAME, region: str = AWS_REGION):
        self._ddb = boto3.resource("dynamodb", region_name=region)
        self._table = self._ddb.Table(table_name)
        self._table_name = table_name

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        created_by: str = "",
        tags: Optional[List[str]] = None,
        target_repo: str = "",
        parent_id: str = "",
        model: str = "",
        plan_only: bool = False,
        depends_on: Optional[List[str]] = None,
        role: str = "",
        spawned_by: str = "",
        project_id: str = "",
        directive_sk: str = "",
        directive_date: str = "",
        assignee: str = "agent",
    ) -> Task:
        import uuid

        task = Task(
            id=uuid.uuid4().hex[:8],
            title=title,
            description=description,
            priority=TaskPriority(priority),
            created_by=created_by,
            tags=tags or [],
            target_repo=target_repo,
            parent_id=parent_id,
            model=model,
            plan_only=plan_only,
            depends_on=depends_on or [],
            role=role,
            spawned_by=spawned_by,
            project_id=project_id,
            directive_sk=directive_sk,
            directive_date=directive_date,
            assignee=assignee,
        )
        self._table.put_item(Item=_task_to_meta_item(task))
        return task

    def get(self, task_id: str) -> Optional[Task]:
        resp = self._table.get_item(
            Key={"pk": _pk(task_id), "sk": "META"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if not item:
            return None
        return _task_from_item(item)

    def update_status(self, task_id: str, status: TaskStatus) -> Optional[Task]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = self._get_meta_fields(task_id, "priority", "created_at")
        priority = meta.get("priority", "medium")
        created_at = meta.get("created_at", now)
        try:
            resp = self._table.update_item(
                Key={"pk": _pk(task_id), "sk": "META"},
                UpdateExpression="SET #st = :s, updated_at = :u, priority_sort_created = :psc",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":s": status.value,
                    ":u": now,
                    ":psc": _priority_sort_created(priority, created_at),
                },
                ConditionExpression=Attr("pk").exists(),
                ReturnValues="ALL_NEW",
            )
            return _task_from_item(resp["Attributes"])
        except self._ddb.meta.client.exceptions.ConditionalCheckFailedException:
            return None

    def _get_meta_fields(self, task_id: str, *fields: str) -> Dict[str, str]:
        projection = ", ".join(fields)
        resp = self._table.get_item(
            Key={"pk": _pk(task_id), "sk": "META"},
            ProjectionExpression=projection,
        )
        return resp.get("Item", {})

    def list_tasks(
        self, status: Optional[TaskStatus] = None, parent_id: Optional[str] = None
    ) -> List[Task]:
        if parent_id is not None:
            tasks = self._query_all(
                IndexName="parent-index",
                KeyConditionExpression=Key("parent_id").eq(parent_id),
            )
            if status is not None:
                tasks = [t for t in tasks if t.status == status]
        elif status is not None:
            tasks = self._scan_meta(FilterExpression=Attr("status").eq(status.value))
        else:
            tasks = self._scan_meta()

        return tasks

    def _scan_meta(self, **extra_kwargs) -> List[Task]:
        """Scan for META items with optional extra filter, handling pagination."""
        base_filter = Attr("sk").eq("META")
        extra = extra_kwargs.pop("FilterExpression", None)
        combined = base_filter & extra if extra is not None else base_filter
        items = []  # type: List[Dict[str, Any]]
        resp = self._table.scan(FilterExpression=combined, **extra_kwargs)
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._table.scan(
                FilterExpression=combined,
                ExclusiveStartKey=resp["LastEvaluatedKey"],
                **extra_kwargs,
            )
            items.extend(resp.get("Items", []))
        return [_task_from_item(i) for i in items]

    def _query_all(self, **kwargs) -> List[Task]:
        """Run a DynamoDB query, handling pagination, and return Task objects."""
        items = []  # type: List[Dict[str, Any]]
        resp = self._table.query(**kwargs)
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._table.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
            items.extend(resp.get("Items", []))
        return [_task_from_item(i) for i in items]

    def deps_ready(self, task: Task) -> bool:
        for dep_id in task.depends_on:
            dep = self.get(dep_id)
            if dep and dep.status not in (TaskStatus.COMPLETED, TaskStatus.IN_REVIEW):
                return False
        return True

    def find_dependents(self, task_id: str) -> List[Task]:
        """Return pending tasks whose depends_on includes task_id and are now ready."""
        pending = self.list_tasks(status=TaskStatus.PENDING)
        results = []  # type: List[Task]
        for t in pending:
            if task_id in t.depends_on and self.deps_ready(t):
                results.append(t)
        return results

    def list_subtasks(self, parent_id: str) -> List[Task]:
        return self.list_tasks(parent_id=parent_id)

    def list_tasks_for_project(self, project_id: str) -> List[Task]:
        """All tasks with META sk linked to a project (via project-index GSI)."""
        if not project_id:
            return []
        return self._query_all(
            IndexName="project-index",
            KeyConditionExpression=Key("project_id").eq(project_id),
        )

    def maybe_finalize_directive_batch(self, task_id: str) -> None:
        """When all tasks for a directive batch are terminal, set awaiting_next_directive on project."""
        task = self.get(task_id)
        if not task or not task.project_id or not task.directive_sk:
            return
        related = self.list_tasks_for_project(task.project_id)
        batch = [t for t in related if t.directive_sk == task.directive_sk]
        if not batch:
            return
        terminal = {
            TaskStatus.COMPLETED,
            TaskStatus.IN_REVIEW,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        }
        if all(t.status in terminal for t in batch):
            from .projects_dynamo import finalize_plan_batch, update_project

            update_project(task.project_id, {"awaiting_next_directive": True})
            sk = task.directive_sk or ""
            if sk.startswith("PLAN#"):
                finalize_plan_batch(task.project_id, sk, batch)

    def list_spawned_tasks(self, spawned_by: str) -> List[Task]:
        resp = self._table.scan(
            FilterExpression=Attr("sk").eq("META") & Attr("spawned_by").eq(spawned_by),
        )
        return [_task_from_item(i) for i in resp.get("Items", [])]

    def list_reply_pending(self) -> List[Task]:
        """Return tasks with reply_pending=true (typically 0-1 at a time)."""
        return self._scan_meta(FilterExpression=Attr("reply_pending").eq(True))

    def delete(self, task_id: str) -> bool:
        pk = _pk(task_id)
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(pk),
            ProjectionExpression="pk, sk",
        )
        items = resp.get("Items", [])
        if not items:
            return False
        with self._table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        return True

    # ------------------------------------------------------------------
    # Sections (append-only items)
    # ------------------------------------------------------------------

    def append_section(self, task_id: str, section: str, body: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sk_prefix = _section_to_sk_prefix(section)
        self._table.put_item(
            Item={
                "pk": _pk(task_id),
                "sk": "%s#%s" % (sk_prefix, now),
                "section": section,
                "body": body,
                "created_at": now,
            }
        )

    def append_agent_result(self, task_id: str, result: Any, section: str = "Agent Output") -> None:
        from .agent import _extract_agent_text

        output = _extract_agent_text(result.stdout)
        if result.returncode == 0:
            body = output
        else:
            body = "**Exit code:** %d\n\n%s" % (result.returncode, output)
            if result.stderr and result.stderr.strip():
                body += "\n\n**Stderr:**\n%s" % result.stderr.strip()
        self.append_section(task_id, section, body)

    # ------------------------------------------------------------------
    # Field setters
    # ------------------------------------------------------------------

    def set_field(self, task_id: str, field: str, value: str) -> None:
        safe_name = "#f_" + field.replace("-", "_")
        try:
            self._table.update_item(
                Key={"pk": _pk(task_id), "sk": "META"},
                UpdateExpression="SET %s = :v" % safe_name,
                ExpressionAttributeNames={safe_name: field},
                ExpressionAttributeValues={":v": value},
            )
        except Exception:
            log.warning("set_field(%s, %s) failed", task_id, field, exc_info=True)

    def set_model(self, task_id: str, model: str) -> None:
        self.set_field(task_id, "model", model)

    def set_session_id(self, task_id: str, session_id: str) -> None:
        self.set_field(task_id, "session_id", session_id)

    def set_depends_on(self, task_id: str, dep_ids: List[str]) -> None:
        self._table.update_item(
            Key={"pk": _pk(task_id), "sk": "META"},
            UpdateExpression="SET depends_on = :d",
            ExpressionAttributeValues={":d": dep_ids},
        )

    def set_plan_only(self, task_id: str, value: bool) -> None:
        if value:
            self._table.update_item(
                Key={"pk": _pk(task_id), "sk": "META"},
                UpdateExpression="SET plan_only = :v",
                ExpressionAttributeValues={":v": True},
            )
        else:
            self._table.update_item(
                Key={"pk": _pk(task_id), "sk": "META"},
                UpdateExpression="REMOVE plan_only",
            )

    def replan_as_pending(self, task_id: str) -> None:
        self.set_plan_only(task_id, True)
        self.update_status(task_id, TaskStatus.PENDING)

    def set_cancelled_by(self, task_id: str, actor: str) -> None:
        self.set_field(task_id, "cancelled_by", actor)

    def clear_cancelled_by(self, task_id: str) -> None:
        self._table.update_item(
            Key={"pk": _pk(task_id), "sk": "META"},
            UpdateExpression="REMOVE cancelled_by",
        )

    def set_reply_pending(self, task_id: str, pending: bool) -> None:
        if pending:
            self._table.update_item(
                Key={"pk": _pk(task_id), "sk": "META"},
                UpdateExpression="SET reply_pending = :v",
                ExpressionAttributeValues={":v": True},
            )
        else:
            self._table.update_item(
                Key={"pk": _pk(task_id), "sk": "META"},
                UpdateExpression="REMOVE reply_pending",
            )

    def set_pr_url(self, task_id: str, url: str) -> None:
        self.set_field(task_id, "pr_url", url)

    def set_merged_at(self, task_id: str, timestamp: str) -> None:
        self.set_field(task_id, "merged_at", timestamp)

    def set_deployed_at(self, task_id: str, timestamp: str) -> None:
        self.set_field(task_id, "deployed_at", timestamp)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_pr_url(self, task_id: str) -> Optional[str]:
        resp = self._table.get_item(
            Key={"pk": _pk(task_id), "sk": "META"},
            ProjectionExpression="pr_url",
        )
        return resp.get("Item", {}).get("pr_url")

    def get_agent_output(self, task_id: str) -> Optional[str]:
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_pk(task_id)) & Key("sk").begins_with("OUTPUT#"),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return None
        return items[0].get("body") or None

    def get_comments(self, task_id: str) -> List[Comment]:
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_pk(task_id)) & Key("sk").begins_with("COMMENT#"),
            ScanIndexForward=True,
        )
        return [
            Comment(
                author=i.get("author", ""),
                body=i.get("body", ""),
                created_at=i.get("created_at", ""),
            )
            for i in resp.get("Items", [])
        ]

    def add_comment(self, task_id: str, author: str, body: str) -> Optional[Comment]:
        task = self.get(task_id)
        if not task:
            return None
        comment = Comment(author=author, body=body)
        now_precise = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self._table.put_item(
            Item={
                "pk": _pk(task_id),
                "sk": "COMMENT#%s" % now_precise,
                "author": author,
                "body": body,
                "created_at": comment.created_at,
            }
        )
        return comment

    def has_section(self, task_id: str, heading_prefix: str) -> bool:
        section_name = heading_prefix.lstrip("# ").strip()
        sk_prefix = _section_to_sk_prefix(section_name)
        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_pk(task_id)) & Key("sk").begins_with(sk_prefix),
            FilterExpression=Attr("section").begins_with(section_name),
            ProjectionExpression="pk",
        )
        return len(resp.get("Items", [])) > 0

    def get_cancelled_by(self, task_id: str) -> Optional[str]:
        resp = self._table.get_item(
            Key={"pk": _pk(task_id), "sk": "META"},
            ProjectionExpression="cancelled_by",
        )
        return resp.get("Item", {}).get("cancelled_by")

    def get_merged_at(self, task_id: str) -> Optional[str]:
        resp = self._table.get_item(
            Key={"pk": _pk(task_id), "sk": "META"},
            ProjectionExpression="merged_at",
        )
        return resp.get("Item", {}).get("merged_at")

    def get_deployed_at(self, task_id: str) -> Optional[str]:
        resp = self._table.get_item(
            Key={"pk": _pk(task_id), "sk": "META"},
            ProjectionExpression="deployed_at",
        )
        return resp.get("Item", {}).get("deployed_at")

    def list_merged_not_deployed(self) -> List[str]:
        resp = self._table.scan(
            FilterExpression=(
                Attr("sk").eq("META") & Attr("merged_at").exists() & ~Attr("deployed_at").exists()
            ),
            ProjectionExpression="task_id",
        )
        return [i["task_id"] for i in resp.get("Items", [])]

    def find_task_by_pr_url(self, pr_url: str) -> Optional[str]:
        pr_url = pr_url.rstrip("/")
        resp = self._table.query(
            IndexName="pr-index",
            KeyConditionExpression=Key("pr_url").eq(pr_url),
            Limit=1,
            ProjectionExpression="task_id",
        )
        items = resp.get("Items", [])
        return items[0]["task_id"] if items else None

    def get_repos(self) -> List[str]:
        """Return sorted list of distinct target_repo values from tasks."""
        repos = set()  # type: set
        resp = self._table.scan(
            FilterExpression=Attr("sk").eq("META") & Attr("target_repo").exists(),
            ProjectionExpression="target_repo",
        )
        for item in resp.get("Items", []):
            repo = item.get("target_repo", "").strip()
            if repo:
                repos.add(repo)
        while "LastEvaluatedKey" in resp:
            resp = self._table.scan(
                FilterExpression=Attr("sk").eq("META") & Attr("target_repo").exists(),
                ProjectionExpression="target_repo",
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            for item in resp.get("Items", []):
                repo = item.get("target_repo", "").strip()
                if repo:
                    repos.add(repo)
        known = os.getenv("KNOWN_REPOS", "")
        if known:
            repos.update(r.strip() for r in known.split(",") if r.strip())
        return sorted(repos)

    # ------------------------------------------------------------------
    # Pipeline log events
    # ------------------------------------------------------------------

    def write_log_event(
        self,
        task_id: str,
        event: str,
        stage: str = "",
        message: str = "",
        **extra: Any,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        item = {
            "pk": _pk(task_id),
            "sk": "LOG#%s" % now,
            "event": event,
            "stage": stage,
            "message": message,
            "created_at": now,
        }  # type: Dict[str, Any]
        if extra:
            for k, v in extra.items():
                if isinstance(v, float):
                    item[k] = Decimal(str(v))
                else:
                    item[k] = v
        self._table.put_item(Item=item)


def _section_to_sk_prefix(section: str) -> str:
    s = section.lower().strip()
    if "agent output" in s or "doc update" in s:
        return "OUTPUT"
    if "comment" in s:
        return "COMMENT"
    if "plan" in s:
        return "PLAN"
    if "log" in s:
        return "LOG"
    return "OUTPUT"
