#!/usr/bin/env python3
"""Context CLI for the daily-cycle agent — load project data from DynamoDB on demand.

Run with PYTHONPATH set to the repository root (see objectives.run_daily_cycle wrapper).
Also provides ``write_ctx_script()`` for any agent that needs ./ctx in its working dir.
"""

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.dynamo_store import DynamoTaskStore
from src.projects_dynamo import (
    get_doc,
    get_project,
    list_docs,
    list_memories,
    list_plans,
    list_proposals,
    list_snapshots,
    put_memory,
    resolve_memory_by_ref,
)
from src.task_store import TaskStatus

MEMORY_SAVE_MAX_PER_CYCLE = 5
MEMORY_COUNTER_FILE = "_ctx_memory_count"


def write_ctx_script(target_dir: str, project_id: str) -> None:
    """Write executable ./ctx wrapper into *target_dir*.

    Usable by any agent that needs on-demand DynamoDB project context.
    """
    project_root = Path(__file__).resolve().parent.parent
    script = project_root / "src" / "context_cli.py"
    py = Path(sys.executable)
    lines = [
        "#!/bin/bash",
        'export PYTHONPATH="%s"' % str(project_root),
        'export CTX_PROJECT_ID="%s"' % project_id.replace('"', ""),
        'export CTX_DATA_DIR="$(cd "$(dirname "$0")" && pwd)"',
        'exec "%s" "%s" "$@"' % (str(py), str(script)),
        "",
    ]
    path = os.path.join(target_dir, "ctx")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


_TASK_STATUS_VALUES = frozenset(s.value for s in TaskStatus)
_PROPOSAL_STATUS_VALUES = frozenset(("pending", "approved", "rejected"))


def _err(msg: str) -> None:
    print("Error: %s" % msg, file=sys.stderr)


def _resolve_project_id(args: argparse.Namespace) -> Optional[str]:
    pid = args.project or os.getenv("CTX_PROJECT_ID", "").strip()
    if not pid:
        _err("project id required (--project or CTX_PROJECT_ID)")
        return None
    return pid


def _data_dir(args: argparse.Namespace) -> str:
    return (args.data_dir or os.getenv("CTX_DATA_DIR", "") or os.getcwd()).strip()


def _format_kpis(kpis: List[Dict[str, Any]]) -> str:
    if not kpis:
        return "(no KPIs defined)"
    lines = []
    for k in kpis:
        direction = {"up": "↑", "down": "↓", "maintain": "↔"}.get(str(k.get("direction", "")), "")
        lines.append(
            "- %s (id=%s): current %s / target %s %s [%s] source=%s"
            % (
                k.get("label", "?"),
                k.get("id", "?"),
                k.get("current", "—"),
                k.get("target", "?"),
                k.get("unit", ""),
                direction,
                k.get("source", "?"),
            )
        )
    return "\n".join(lines)


def _format_snapshots(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "(no snapshots yet)"
    lines = []
    for s in sorted(items, key=lambda x: x.get("date", ""), reverse=True):
        readings = s.get("kpi_readings", {})
        parts = ["%s=%s" % (k, v) for k, v in readings.items() if v is not None]
        refl = (s.get("reflection") or "").strip()
        extra = (" | reflection: %s" % refl[:120]) if refl else ""
        lines.append(
            "%s: %s%s"
            % (s.get("date", "?"), ", ".join(parts) if parts else "(empty)", extra)
        )
    return "\n".join(lines)


def cmd_spec(project_id: str) -> int:
    try:
        p = get_project(project_id)
        if not p:
            _err("project not found")
            return 1
        spec = (p.get("spec") or "").strip()
        print(spec if spec else "(no spec)")
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_kpis(project_id: str) -> int:
    try:
        p = get_project(project_id)
        if not p:
            _err("project not found")
            return 1
        kpis = p.get("kpis", [])
        if not isinstance(kpis, list):
            kpis = []
        print(_format_kpis(kpis))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_snapshots(project_id: str, days: int) -> int:
    try:
        items = list_snapshots(project_id, limit=max(1, min(days, 90)))
        print(_format_snapshots(items))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_tasks(project_id: str, status: Optional[str], recent: int, assignee: Optional[str]) -> int:
    try:
        if status and status not in _TASK_STATUS_VALUES:
            _err(
                "invalid --status %r (use one of: %s)"
                % (status, ", ".join(sorted(_TASK_STATUS_VALUES)))
            )
            return 1
        if assignee and assignee not in ("agent", "human"):
            _err("invalid --assignee %r (use agent or human)" % assignee)
            return 1
        store = DynamoTaskStore()
        tasks = store.list_tasks_for_project(project_id)
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        if assignee:
            tasks = [t for t in tasks if getattr(t, "assignee", "agent") == assignee]
        tasks.sort(key=lambda t: getattr(t, "updated_at", ""), reverse=True)
        tasks = tasks[: max(1, min(recent, 200))]
        if not tasks:
            print("(no matching tasks)")
            return 0
        for t in tasks:
            print("- [%s] %s: %s" % (t.status.value, t.id, t.title))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_proposals(project_id: str, status: Optional[str]) -> int:
    try:
        if status and status not in _PROPOSAL_STATUS_VALUES:
            _err(
                "invalid --status %r (use one of: %s)"
                % (status, ", ".join(sorted(_PROPOSAL_STATUS_VALUES)))
            )
            return 1
        props = list_proposals(project_id, status=status, limit=80)
        if not props:
            print("(no proposals)")
            return 0
        for p in props:
            line = "- [%s] %s: %s" % (p.get("status", "?"), p.get("sk", "?"), p.get("action", "?"))
            if p.get("outcome"):
                line += " → %s" % p.get("outcome")
            if p.get("feedback"):
                line += " (feedback: %s)" % p.get("feedback")
            print(line)
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_human_tasks(project_id: str) -> int:
    try:
        store = DynamoTaskStore()
        tasks = store.list_tasks_for_project(project_id)
        rows = [
            t
            for t in tasks
            if getattr(t, "assignee", "agent") == "human" and t.status.value == "in_review"
        ]
        if not rows:
            print("(none awaiting review)")
            return 0
        for t in rows:
            desc = (t.description or "")[:200]
            if len(t.description or "") > 200:
                desc += "…"
            print("- [%s] %s: %s" % (t.id, t.title, desc))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_plans(project_id: str, limit: int) -> int:
    try:
        plans = list_plans(project_id, limit=max(1, min(limit, 50)))
        if not plans:
            print("(no plans yet)")
            return 0
        for p in plans:
            d = p.get("plan_date") or (p.get("sk") or "").replace("PLAN#", "", 1)
            st = p.get("status", "?")
            ref = (p.get("reflection") or "").strip()
            if len(ref) > 200:
                ref = ref[:200] + "…"
            out = p.get("outcome_summary")
            n_items = len(p.get("items", []))
            parts = ["%s [%s]" % (d, st)]
            if n_items:
                parts.append("items: %d" % n_items)
            if out and isinstance(out, dict):
                oparts = ["%s=%s" % (k, v) for k, v in out.items() if v]
                if oparts:
                    parts.append("outcomes: %s" % ", ".join(oparts))
            if ref:
                parts.append(ref)
            print("- " + " — ".join(parts))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_memory_list(project_id: str, limit: int) -> int:
    try:
        items = list_memories(project_id, limit=max(1, min(limit, 50)))
        if not items:
            print("(no memories)")
            return 0
        for it in items:
            sk = it.get("sk", "")
            body = (it.get("content") or "").replace("\n", " ")[:500]
            print("%s [%s] %s" % (sk, it.get("cycle_date", ""), body))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_memory_save(project_id: str, text: str, data_dir: str) -> int:
    path = os.path.join(data_dir, MEMORY_COUNTER_FILE)
    try:
        n = 0
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                try:
                    n = int(f.read().strip())
                except ValueError:
                    n = 0
        if n >= MEMORY_SAVE_MAX_PER_CYCLE:
            _err("memory save limit (%d) reached for this cycle" % MEMORY_SAVE_MAX_PER_CYCLE)
            return 1
        # Reserve the slot first so a successful put cannot exceed the per-cycle cap
        # if the counter file fails to update later.
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(n + 1))
        except OSError as exc:
            _err("could not update save counter: %s" % exc)
            return 1
        try:
            put_memory(project_id, text)
        except Exception:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(str(n))
            except OSError:
                pass
            raise
        print("ok")
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_memory_get(project_id: str, memory_ref: str) -> int:
    try:
        it = resolve_memory_by_ref(project_id, memory_ref)
        if not it:
            _err("memory not found (use ./ctx memory list for full sk values)")
            return 1
        print(it.get("sk", ""))
        print(it.get("content", ""))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_docs_list(project_id: str) -> int:
    try:
        items = list_docs(project_id)
        if not items:
            print("(no docs)")
            return 0
        for it in items:
            slug = str(it.get("sk", "")).replace("DOC#", "", 1)
            title = it.get("title", slug)
            updated = it.get("updated_at", "")
            content = (it.get("content") or "").replace("\n", " ")
            preview = content[:120] + ("…" if len(content) > 120 else "")
            print("- %s (%s) [%s] %s" % (slug, title, updated, preview))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def cmd_docs_get(project_id: str, slug: str) -> int:
    try:
        it = get_doc(project_id, slug)
        if not it:
            _err("doc '%s' not found (use ./ctx docs to list)" % slug)
            return 1
        print("# %s" % it.get("title", slug))
        print("")
        print(it.get("content", ""))
        return 0
    except Exception as e:
        _err(str(e))
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description="Daily cycle context tool")
    parser.add_argument("--project", default="", help="Project id (or CTX_PROJECT_ID)")
    parser.add_argument(
        "--data-dir",
        default="",
        help="Directory for memory save counter (or CTX_DATA_DIR, default cwd)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("spec", help="Print project spec markdown")
    sub.add_parser("kpis", help="Print KPI definitions")

    p_snap = sub.add_parser("snapshots", help="Print metric snapshot history")
    p_snap.add_argument("--days", type=int, default=14, help="Max rows (default 14)")

    p_tasks = sub.add_parser("tasks", help="List project tasks")
    p_tasks.add_argument("--status", default="", help="Filter: pending, completed, …")
    p_tasks.add_argument("--recent", type=int, default=20, help="Max tasks (default 20)")
    p_tasks.add_argument("--assignee", default="", help="Filter: agent or human")

    p_prop = sub.add_parser("proposals", help="List proposals")
    p_prop.add_argument("--status", default="", help="Filter: pending, approved, rejected")

    sub.add_parser("human-tasks", help="Human tasks in in_review")

    p_plans = sub.add_parser("plans", help="Recent daily autopilot plans")
    p_plans.add_argument("--limit", type=int, default=14, help="Max plans (default 14)")

    p_mem = sub.add_parser("memory", help="Memory subcommands")
    mem_sub = p_mem.add_subparsers(dest="mem_cmd", required=True)
    p_ml = mem_sub.add_parser("list", help="List memories")
    p_ml.add_argument("--limit", type=int, default=20)
    p_ms = mem_sub.add_parser("save", help="Save a memory")
    p_ms.add_argument("text", nargs="+")
    p_mg = mem_sub.add_parser("get", help="Get one memory by sk or suffix")
    p_mg.add_argument("memory_id")

    p_docs = sub.add_parser("docs", help="Project docs (list or get by slug)")
    p_docs.add_argument("slug", nargs="?", default="", help="Doc slug to retrieve (omit to list)")

    args = parser.parse_args(argv)
    project_id = _resolve_project_id(args)
    if not project_id:
        return 1
    data_dir = _data_dir(args)

    if args.cmd == "spec":
        return cmd_spec(project_id)
    if args.cmd == "kpis":
        return cmd_kpis(project_id)
    if args.cmd == "snapshots":
        return cmd_snapshots(project_id, args.days)
    if args.cmd == "tasks":
        st = args.status.strip() or None
        asg = args.assignee.strip() or None
        return cmd_tasks(project_id, st, args.recent, asg)
    if args.cmd == "proposals":
        st = args.status.strip() or None
        return cmd_proposals(project_id, st)
    if args.cmd == "human-tasks":
        return cmd_human_tasks(project_id)
    if args.cmd == "plans":
        return cmd_plans(project_id, args.limit)
    if args.cmd == "memory":
        if args.mem_cmd == "list":
            return cmd_memory_list(project_id, args.limit)
        if args.mem_cmd == "save":
            text = " ".join(args.text).strip()
            if not text:
                _err("memory text required")
                return 1
            return cmd_memory_save(project_id, text, data_dir)
        if args.mem_cmd == "get":
            return cmd_memory_get(project_id, args.memory_id.strip())
    if args.cmd == "docs":
        slug = (args.slug or "").strip()
        if slug:
            return cmd_docs_get(project_id, slug)
        return cmd_docs_list(project_id)

    _err("unknown command")
    return 1


if __name__ == "__main__":
    sys.exit(main())
