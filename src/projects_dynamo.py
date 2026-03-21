"""DynamoDB project records (pk=PROJECT#id, sk=PROJECT or DIR#...)."""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

log = logging.getLogger(__name__)

TABLE_NAME = os.getenv("DYNAMO_TABLE", "agent-tasks")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")


def _pk(project_id: str) -> str:
    return "PROJECT#%s" % project_id


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Return raw project META item or None."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.get_item(
        Key={"pk": _pk(project_id), "sk": "PROJECT"},
        ConsistentRead=True,
    )
    return resp.get("Item")


def update_project(
    project_id: str,
    updates: Dict[str, Any],
) -> None:
    """Merge updates into project META (expression builder for common fields)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    names = {"#u": "updated_at", "#pu": "project_updated"}
    vals = {":u": now, ":pu": now}
    sets = ["#u = :u", "#pu = :pu"]
    if "awaiting_next_directive" in updates:
        names["#an"] = "awaiting_next_directive"
        vals[":an"] = updates["awaiting_next_directive"]
        sets.append("#an = :an")
    if "active_directive_sk" in updates:
        names["#ad"] = "active_directive_sk"
        vals[":ad"] = updates["active_directive_sk"]
        sets.append("#ad = :ad")
    if "autopilot" in updates:
        names["#ap"] = "autopilot"
        vals[":ap"] = bool(updates["autopilot"])
        sets.append("#ap = :ap")
    table.update_item(
        Key={"pk": _pk(project_id), "sk": "PROJECT"},
        UpdateExpression="SET %s" % ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )


def update_directive_task_ids(project_id: str, directive_sk: str, task_ids: List[str]) -> None:
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    table.update_item(
        Key={"pk": _pk(project_id), "sk": directive_sk},
        UpdateExpression="SET task_ids = :t",
        ExpressionAttributeValues={":t": task_ids},
    )


def get_directive_item(project_id: str, directive_sk: str) -> Optional[Dict[str, Any]]:
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.get_item(Key={"pk": _pk(project_id), "sk": directive_sk})
    return resp.get("Item")


def list_directive_keys(project_id: str) -> List[str]:
    """Return sk values for DIR# items under project."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_pk(project_id)) & Key("sk").begins_with("DIR#"),
        ProjectionExpression="sk",
    )
    return [i["sk"] for i in resp.get("Items", [])]


def list_snapshots(project_id: str, limit: int = 14) -> List[Dict[str, Any]]:
    """Return recent SNAPSHOT# items for a project, newest first."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_pk(project_id)) & Key("sk").begins_with("SNAPSHOT#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


def list_proposals(
    project_id: str, status: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    """Return PROP# items for a project, newest first. Optionally filter by status.

    When ``status`` is set, paginates until ``limit`` matching items are collected or
    the partition is exhausted (DynamoDB applies Limit before FilterExpression).
    """
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    kce = Key("pk").eq(_pk(project_id)) & Key("sk").begins_with("PROP#")

    if not status:
        resp = table.query(
            KeyConditionExpression=kce,
            ScanIndexForward=False,
            Limit=limit,
        )
        return resp.get("Items", [])

    items = []  # type: List[Dict[str, Any]]
    last_key = None  # type: Optional[Dict[str, Any]]
    page_size = max(min(limit * 4, 100), 25)
    while len(items) < limit:
        kwargs = {
            "KeyConditionExpression": kce,
            "ScanIndexForward": False,
            "Limit": page_size,
            "FilterExpression": "#st = :st",
            "ExpressionAttributeNames": {"#st": "status"},
            "ExpressionAttributeValues": {":st": status},
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return items[:limit]


def put_snapshot(project_id: str, date: str, snapshot: Dict[str, Any]) -> None:
    """Write a SNAPSHOT#<date> record."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    item = {
        "pk": _pk(project_id),
        "sk": "SNAPSHOT#%s" % date,
        **snapshot,
    }
    table.put_item(Item=item)


def put_proposal(project_id: str, date: str, prop_id: str, proposal: Dict[str, Any]) -> None:
    """Write a PROP#<date>#<id> record."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ttl_epoch = int(datetime.now(timezone.utc).timestamp()) + 7 * 86400
    item = {
        "pk": _pk(project_id),
        "sk": "PROP#%s#%s" % (date, prop_id),
        "status": "pending",
        "created_at": now,
        "ttl": ttl_epoch,
        **proposal,
    }
    table.put_item(Item=item)


def update_snapshot_reflection(project_id: str, date: str, reflection: str) -> None:
    """Set the reflection field on an existing snapshot record."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    table.update_item(
        Key={"pk": _pk(project_id), "sk": "SNAPSHOT#%s" % date},
        UpdateExpression="SET reflection = :r",
        ExpressionAttributeValues={":r": reflection},
    )


def update_proposal_status(project_id: str, prop_sk: str, status: str, **extras: Any) -> None:
    """Update a proposal's status and optional extra fields (feedback, task_id, outcome)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    names = {"#st": "status"}  # type: Dict[str, str]
    vals = {":st": status}  # type: Dict[str, Any]
    sets = ["#st = :st"]
    for i, (k, v) in enumerate(extras.items()):
        alias = "#e%d" % i
        val_alias = ":e%d" % i
        names[alias] = k
        vals[val_alias] = v
        sets.append("%s = %s" % (alias, val_alias))
    table.update_item(
        Key={"pk": _pk(project_id), "sk": prop_sk},
        UpdateExpression="SET %s" % ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )


def update_project_kpi_current(project_id: str, kpis: List[Dict[str, Any]]) -> None:
    """Overwrite the kpis array on a project (used by metrics Lambda via API)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    table.update_item(
        Key={"pk": _pk(project_id), "sk": "PROJECT"},
        UpdateExpression="SET kpis = :k, updated_at = :u, project_updated = :u",
        ExpressionAttributeValues={":k": kpis, ":u": now},
    )


# ---------------------------------------------------------------------------
# Daily cycle agent memories (sk=MEMORY#<iso>)
# ---------------------------------------------------------------------------

MEMORY_CONTENT_MAX = 2000
MEMORY_KEEP_MAX = 50
# Minimum ref length for substring matching (avoids accidental short-string hits)
MEMORY_REF_SUBSTRING_MIN_LEN = 8


def list_memories(project_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent MEMORY# items for a project, newest first."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_pk(project_id)) & Key("sk").begins_with("MEMORY#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


def get_memory(project_id: str, memory_sk: str) -> Optional[Dict[str, Any]]:
    """Get one memory by sk (with or without MEMORY# prefix)."""
    sk = memory_sk if memory_sk.startswith("MEMORY#") else "MEMORY#%s" % memory_sk
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.get_item(Key={"pk": _pk(project_id), "sk": sk})
    return resp.get("Item")


def resolve_memory_by_ref(project_id: str, memory_ref: str) -> Optional[Dict[str, Any]]:
    """Resolve a memory by full sk, unique suffix, or substring (if ref is long enough).

    Used by the context CLI and keeps lookup rules in one place.
    """
    ref = (memory_ref or "").strip()
    if not ref:
        return None
    it = get_memory(project_id, ref)
    if it:
        return it
    for cand in list_memories(project_id, limit=100):
        sk = str(cand.get("sk", ""))
        if sk == ref or sk.endswith(ref):
            return cand
        if len(ref) >= MEMORY_REF_SUBSTRING_MIN_LEN and ref in sk:
            return cand
    return None


def prune_memories(project_id: str, keep: int = MEMORY_KEEP_MAX) -> None:
    """Delete oldest memories so at most `keep` remain (newest kept)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    items = []  # type: List[Dict[str, Any]]
    last_key = None  # type: Optional[Dict[str, Any]]
    while True:
        kwargs = {
            "KeyConditionExpression": Key("pk").eq(_pk(project_id))
            & Key("sk").begins_with("MEMORY#"),
            "ScanIndexForward": True,
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    if len(items) <= keep:
        return
    # Oldest first in `items` (ScanIndexForward=True). Drop oldest until len == keep.
    to_delete = items[: len(items) - keep]
    for it in to_delete:
        table.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})


def put_memory(
    project_id: str,
    content: str,
    cycle_date: Optional[str] = None,
) -> str:
    """Store a memory; truncates content, prunes to MEMORY_KEEP_MAX. Returns sk."""
    text = (content or "").strip()
    if len(text) > MEMORY_CONTENT_MAX:
        text = text[:MEMORY_CONTENT_MAX]
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    sk = "MEMORY#%s" % now
    if cycle_date is None:
        cycle_date = datetime.now(timezone.utc).date().isoformat()
    table.put_item(
        Item={
            "pk": _pk(project_id),
            "sk": sk,
            "content": text,
            "cycle_date": cycle_date,
            "created_at": now,
        }
    )
    prune_memories(project_id, keep=MEMORY_KEEP_MAX)
    return sk


# ---------------------------------------------------------------------------
# Daily autopilot plans (sk=PLAN#YYYY-MM-DD)
# ---------------------------------------------------------------------------


def plan_sk(date_str: str) -> str:
    return "PLAN#%s" % date_str


def get_plan(project_id: str, date_str: str) -> Optional[Dict[str, Any]]:
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.get_item(Key={"pk": _pk(project_id), "sk": plan_sk(date_str)})
    return resp.get("Item")


def list_plans(project_id: str, limit: int = 14) -> List[Dict[str, Any]]:
    """Recent PLAN# items, newest first."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_pk(project_id)) & Key("sk").begins_with("PLAN#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


def put_plan(project_id: str, date_str: str, record: Dict[str, Any]) -> None:
    """Write or replace a PLAN# record (caller sets status, items, reflection, etc.)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    item = {
        "pk": _pk(project_id),
        "sk": plan_sk(date_str),
        "plan_date": date_str,
        "updated_at": now,
        **record,
    }
    if "created_at" not in item:
        item["created_at"] = now
    table.put_item(Item=item)


def update_plan_fields(project_id: str, date_str: str, fields: Dict[str, Any]) -> None:
    """SET arbitrary scalar/list/map fields on a plan item."""
    if not fields:
        return
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    names = {"#u": "updated_at"}
    vals = {":u": now}
    sets = ["#u = :u"]
    for i, (k, v) in enumerate(fields.items()):
        nk = "#f%d" % i
        vk = ":v%d" % i
        names[nk] = k
        vals[vk] = v
        sets.append("%s = %s" % (nk, vk))
    table.update_item(
        Key={"pk": _pk(project_id), "sk": plan_sk(date_str)},
        UpdateExpression="SET %s" % ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )


def finalize_plan_batch(project_id: str, plan_sk_val: str, batch_tasks: List[Any]) -> None:
    """Mark plan completed and store outcome counts (caller ensures all tasks terminal)."""
    if not batch_tasks:
        return
    date_str = plan_sk_val.replace("PLAN#", "", 1)
    counts = {"completed": 0, "in_review": 0, "failed": 0, "cancelled": 0}
    for t in batch_tasks:
        st = getattr(t, "status", None)
        if st is None:
            continue
        key = st.value if hasattr(st, "value") else str(st)
        if key in counts:
            counts[key] += 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    update_plan_fields(
        project_id,
        date_str,
        {
            "status": "completed",
            "completed_at": now,
            "outcome_summary": counts,
        },
    )
