"""DynamoDB project records (pk=PROJECT#id, sk=PROJECT or DIR#...)."""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

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
    if "autopilot_mode" in updates:
        names["#am"] = "autopilot_mode"
        vals[":am"] = str(updates["autopilot_mode"] or "daily")
        sets.append("#am = :am")
    if "cycle_started_at" in updates:
        names["#cs"] = "cycle_started_at"
        vals[":cs"] = updates["cycle_started_at"]
        sets.append("#cs = :cs")
    if "cycle_max_hours" in updates:
        names["#cmh"] = "cycle_max_hours"
        vals[":cmh"] = int(updates["cycle_max_hours"])
        sets.append("#cmh = :cmh")
    if "cycle_paused" in updates:
        names["#cp"] = "cycle_paused"
        vals[":cp"] = bool(updates["cycle_paused"])
        sets.append("#cp = :cp")
    if "cycle_pause_reason" in updates:
        names["#cpr"] = "cycle_pause_reason"
        vals[":cpr"] = updates["cycle_pause_reason"]
        sets.append("#cpr = :cpr")
    if "cycle_feedback" in updates:
        names["#cf"] = "cycle_feedback"
        vals[":cf"] = str(updates["cycle_feedback"] or "")
        sets.append("#cf = :cf")
    if "next_check_at" in updates:
        names["#nc"] = "next_check_at"
        vals[":nc"] = updates["next_check_at"]
        sets.append("#nc = :nc")
    if "reply_pending" in updates:
        names["#rp"] = "reply_pending"
        vals[":rp"] = bool(updates["reply_pending"])
        sets.append("#rp = :rp")
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
# Autopilot plans (sk=PLAN#YYYY-MM-DD legacy, or PLAN#YYYY-MM-DDTHH:MM:SS UTC)
# ---------------------------------------------------------------------------


def plan_sk(plan_id_suffix: str) -> str:
    """Sort key for a plan; *plan_id_suffix* is the part after ``PLAN#``."""
    return "PLAN#%s" % plan_id_suffix


def plan_date_from_suffix(plan_id_suffix: str) -> str:
    """Calendar YYYY-MM-DD for grouping/display (from suffix before ``T`` if present)."""
    if "T" in plan_id_suffix:
        return plan_id_suffix.split("T", 1)[0]
    return plan_id_suffix[:10] if len(plan_id_suffix) >= 10 else plan_id_suffix


def new_plan_suffix_utc() -> str:
    """New continuous-plan sort key suffix (UTC, no timezone letter)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def get_plan(project_id: str, plan_id_suffix: str) -> Optional[Dict[str, Any]]:
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.get_item(Key={"pk": _pk(project_id), "sk": plan_sk(plan_id_suffix)})
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


def put_plan(project_id: str, plan_id_suffix: str, record: Dict[str, Any]) -> None:
    """Write or replace a PLAN# record (caller sets status, items, reflection, etc.)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pd = record.get("plan_date") or plan_date_from_suffix(plan_id_suffix)
    item = {
        "pk": _pk(project_id),
        "sk": plan_sk(plan_id_suffix),
        "plan_date": pd,
        "updated_at": now,
        **record,
    }
    if "created_at" not in item:
        item["created_at"] = now
    table.put_item(Item=item)


def update_plan_fields(project_id: str, plan_id_suffix: str, fields: Dict[str, Any]) -> None:
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
        Key={"pk": _pk(project_id), "sk": plan_sk(plan_id_suffix)},
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


# ---------------------------------------------------------------------------
# Project-level PM chat (sk=CHAT#<iso>)
# ---------------------------------------------------------------------------


def add_chat_message(project_id: str, author: str, body: str) -> str:
    """Append a chat message. Returns sort key (CHAT#...)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    sk = "CHAT#%s" % now
    table.put_item(
        Item={
            "pk": _pk(project_id),
            "sk": sk,
            "author": (author or "").strip() or "unknown",
            "body": body or "",
            "created_at": now,
        }
    )
    return sk


def post_system_message(project_id: str, body: str) -> None:
    """Post a system line to the project chat (no reply_pending)."""
    add_chat_message(project_id, "system", body)


def list_chat_messages(project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Recent chat messages, oldest first (up to *limit* newest)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_pk(project_id)) & Key("sk").begins_with("CHAT#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    items = list(reversed(resp.get("Items", [])))
    return items


def set_project_reply_pending(project_id: str, pending: bool) -> None:
    """Set PM reply_pending on the PROJECT item."""
    update_project(project_id, {"reply_pending": pending})


def claim_project_pm_reply(project_id: str) -> bool:
    """Atomically clear reply_pending if it was true. Returns False if already claimed or not set."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        table.update_item(
            Key={"pk": _pk(project_id), "sk": "PROJECT"},
            UpdateExpression="SET reply_pending = :f, updated_at = :u, project_updated = :pu",
            ConditionExpression="reply_pending = :t",
            ExpressionAttributeValues={
                ":f": False,
                ":t": True,
                ":u": now,
                ":pu": now,
            },
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            return False
        raise
    return True


# ---------------------------------------------------------------------------
# Project docs (sk=DOC#<slug>)
# ---------------------------------------------------------------------------

DOC_CONTENT_MAX = 50000
DOC_SLUG_RE = r"^[a-z0-9][a-z0-9_-]{0,62}$"


def _valid_doc_slug(slug: str) -> bool:
    return bool(re.match(DOC_SLUG_RE, slug))


def list_docs(project_id: str) -> List[Dict[str, Any]]:
    """Return DOC# items for a project, sorted by slug ascending."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(_pk(project_id)) & Key("sk").begins_with("DOC#"),
        ScanIndexForward=True,
    )
    return resp.get("Items", [])


def get_doc(project_id: str, slug: str) -> Optional[Dict[str, Any]]:
    """Get one doc by slug."""
    sk = slug if slug.startswith("DOC#") else "DOC#%s" % slug
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    resp = table.get_item(Key={"pk": _pk(project_id), "sk": sk})
    return resp.get("Item")


def put_doc(project_id: str, slug: str, title: str, content: str) -> Dict[str, Any]:
    """Create or overwrite a DOC#<slug> record. Returns the item."""
    if not _valid_doc_slug(slug):
        raise ValueError("invalid doc slug: %r" % slug)
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text = (content or "")[:DOC_CONTENT_MAX]
    item = {
        "pk": _pk(project_id),
        "sk": "DOC#%s" % slug,
        "title": (title or slug).strip(),
        "content": text,
        "updated_at": now,
    }
    existing = get_doc(project_id, slug)
    if existing and existing.get("created_at"):
        item["created_at"] = existing["created_at"]
    else:
        item["created_at"] = now
    table.put_item(Item=item)
    return item


def delete_doc(project_id: str, slug: str) -> bool:
    """Delete a DOC# record. Returns True if it existed."""
    sk = slug if slug.startswith("DOC#") else "DOC#%s" % slug
    existing = get_doc(project_id, slug)
    if not existing:
        return False
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    table.delete_item(Key={"pk": _pk(project_id), "sk": sk})
    return True


def list_project_reply_pending() -> List[str]:
    """Return project_ids that need a PM reply (scan; low volume)."""
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(TABLE_NAME)
    out = []  # type: List[str]
    start_key = None  # type: Optional[Dict[str, Any]]
    while True:
        kwargs = {
            "FilterExpression": Attr("sk").eq("PROJECT") & Attr("reply_pending").eq(True),
            "ProjectionExpression": "project_id",
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = table.scan(**kwargs)
        for it in resp.get("Items", []):
            pid = it.get("project_id")
            if pid:
                out.append(str(pid))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return out
