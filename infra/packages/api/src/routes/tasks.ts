import { Hono } from "hono";
import * as db from "../lib/dynamo.js";
import { ROLES } from "../lib/roles.js";
import { cancelRunner } from "../lib/ssm.js";
import { budgetStatus, estimateCost } from "../lib/budget.js";
import type { TaskListItem } from "../lib/types.js";

const TASK_STATUSES = new Set([
  "pending",
  "in_progress",
  "in_review",
  "completed",
  "cancelled",
  "failed",
]);

const TASK_PRIORITIES = new Set(["low", "medium", "high", "urgent"]);

export const tasks = new Hono();

async function taskToListItem(
  task: Awaited<ReturnType<typeof db.getTask>> & {}
): Promise<TaskListItem> {
  return {
    ...task,
    deps_ready: await db.depsReady(task),
  };
}

async function taskToDetail(
  task: Awaited<ReturnType<typeof db.getTask>> & {},
  includeOutput = false
) {
  const d: Record<string, unknown> = {
    ...(await taskToListItem(task)),
  };
  if (includeOutput) {
    d.agent_output = await db.getAgentOutput(task.id);
    d.pr_url = await db.getPrUrl(task.id);
    d.merged_at = await db.getMergedAt(task.id);
    d.deployed_at = await db.getDeployedAt(task.id);
  }
  return d;
}

// GET /tasks
tasks.get("/tasks", async (c) => {
  const status = c.req.query("status") ?? "all";
  const limit = Math.min(Number(c.req.query("limit") ?? "25"), 500);
  const offset = Number(c.req.query("offset") ?? "0");

  const isHumanFilter = status === "human";
  const filterStatus =
    status === "all" || isHumanFilter
      ? undefined
      : (status as Parameters<typeof db.listTasks>[0]);
  const all = await db.listTasks(filterStatus);
  let topLevel = all.filter((t) => !t.parent_id);
  if (isHumanFilter) {
    topLevel = topLevel.filter(
      (t) =>
        t.assignee === "human" &&
        !["completed", "cancelled"].includes(t.status)
    );
  }
  topLevel.sort((a, b) => b.created_at.localeCompare(a.created_at));

  const total = topLevel.length;
  const page = topLevel.slice(offset, offset + limit);
  const items = await Promise.all(page.map(taskToListItem));
  const counts = await db.getCounts();

  return c.json({ tasks: items, total, counts });
});

// GET /tasks/:id
tasks.get("/tasks/:id", async (c) => {
  const task = await db.getTask(c.req.param("id"));
  if (!task) return c.json({ error: "not found" }, 404);

  const d = await taskToDetail(task, true);

  const [subtasks, comments, spawnedTasks] = await Promise.all([
    db.listSubtasks(task.id),
    db.getComments(task.id),
    db.listSpawnedTasks(task.id),
  ]);

  d.subtasks = await Promise.all(subtasks.map(taskToListItem));
  d.comments = comments.map((cm) => ({
    author: cm.author,
    body: cm.body,
    created_at: cm.created_at,
  }));

  // Dependency tasks
  const depTasks: { id: string; title: string; status: string }[] = [];
  for (const depId of task.depends_on) {
    const dep = await db.getTask(depId);
    if (dep) depTasks.push({ id: dep.id, title: dep.title, status: dep.status });
  }
  d.dep_tasks = depTasks;

  // Parent
  if (task.parent_id) {
    const parent = await db.getTask(task.parent_id);
    d.parent = parent ? { id: parent.id, title: parent.title } : null;
  } else {
    d.parent = null;
  }

  d.spawned_tasks = await Promise.all(spawnedTasks.map(taskToListItem));

  if (task.spawned_by) {
    const spawner = await db.getTask(task.spawned_by);
    d.spawned_by_task = spawner
      ? { id: spawner.id, title: spawner.title }
      : null;
  } else {
    d.spawned_by_task = null;
  }

  // Runtime + tokens from pipeline logs
  const logs = await db.readLogs({ taskId: task.id, limit: 500 });
  let totalRuntime = 0;
  const tokenKeys = [
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
  ] as const;
  const totals: Record<string, number> = {};
  for (const k of tokenKeys) totals[k] = 0;

  for (const e of logs) {
    if (e.extra?.runtime) totalRuntime += Number(e.extra.runtime);
    for (const k of tokenKeys) {
      if (e.extra?.[k]) totals[k] += Number(e.extra[k]);
    }
  }
  d.runtime = totalRuntime ? Math.round(totalRuntime * 10) / 10 : null;
  d.tokens = Object.values(totals).some((v) => v > 0) ? totals : null;

  return c.json(d);
});

// POST /tasks
tasks.post("/tasks", async (c) => {
  const body = await c.req.json();
  if (!body.title) return c.json({ error: "title is required" }, 400);

  const priority = body.priority ?? "medium";
  if (!TASK_PRIORITIES.has(priority)) {
    return c.json({ error: `invalid priority: ${priority}` }, 400);
  }

  const tags = body.tags
    ? (body.tags as string)
        .split(",")
        .map((t: string) => t.trim())
        .filter(Boolean)
    : [];

  const spawnedBy =
    (body.spawned_by ?? "").trim() ||
    c.req.header("X-Spawned-By-Task")?.trim() ||
    "";

  const task = await db.createTask({
    title: body.title,
    description: body.description ?? "",
    priority,
    created_by: "web",
    tags,
    target_repo: (body.target_repo ?? "").trim(),
    plan_only: body.plan_only ?? false,
    role: (body.role ?? "").trim(),
    spawned_by: spawnedBy,
    model: (body.model ?? "").trim(),
    assignee: (body.assignee ?? "").trim() || undefined,
  });

  return c.json(await taskToListItem(task));
});

// PATCH /tasks/:id/status
tasks.patch("/tasks/:id/status", async (c) => {
  const body = await c.req.json();
  if (!body.status || !TASK_STATUSES.has(body.status)) {
    return c.json({ error: `invalid status: ${body.status}` }, 400);
  }
  const task = await db.updateStatus(c.req.param("id"), body.status);
  if (!task) return c.json({ error: "not found" }, 404);

  if (body.status === "cancelled") {
    await db.setCancelledBy(task.id, "user");
    await cancelRunner(task.id);
  }
  // Dependents are picked up automatically by the EC2 poller; no SSM trigger needed.
  if (
    ["completed", "in_review", "cancelled", "failed"].includes(body.status)
  ) {
    await db.maybeFinalizeDirectiveBatch(task.id);
  }
  return c.json(await taskToListItem(task));
});

// POST /tasks/:id/run — no-op; the EC2 poller picks up pending tasks automatically.
tasks.post("/tasks/:id/run", async (c) => {
  const task = await db.getTask(c.req.param("id"));
  if (!task) return c.json({ error: "not found" }, 404);
  return c.json({ ok: true });
});

// POST /tasks/:id/rerun
tasks.post("/tasks/:id/rerun", async (c) => {
  const task = await db.getTask(c.req.param("id"));
  if (!task) return c.json({ error: "not found" }, 404);
  if (!["completed", "in_review", "cancelled", "failed"].includes(task.status)) {
    return c.json(
      { error: "only completed, in_review, cancelled, or failed tasks can be rerun" },
      400
    );
  }

  const existingPr = await db.getPrUrl(task.id);
  if (existingPr) {
    await db.addComment(
      task.id,
      "agent",
      `**Note:** This task was rerun while an existing PR may still be open: ${existingPr}\n\n` +
        "The new run will create a fresh branch and PR. Please close the old PR if it is no longer needed."
    );
  }

  await db.setReplyPending(task.id, false);
  await db.clearCancelledBy(task.id);
  await db.updateStatus(task.id, "pending");
  return c.json({ ok: true });
});

// POST /tasks/:id/comment
tasks.post("/tasks/:id/comment", async (c) => {
  const body = await c.req.json();
  const comment = await db.addComment(
    c.req.param("id"),
    "web",
    (body.body ?? "").trim()
  );
  if (!comment) return c.json({ error: "not found" }, 404);

  await db.setReplyPending(c.req.param("id"), true);

  // For human-assigned tasks with a project, also flag the project for PM sweep
  // so the hourly autopilot Lambda triggers the PM to review the response.
  const task = await db.getTask(c.req.param("id"));
  if (task?.assignee === "human" && task?.project_id) {
    await db.updateProject(task.project_id, { reply_pending: true });
  }

  return c.json({
    author: comment.author,
    body: comment.body,
    created_at: comment.created_at,
  });
});

// POST /tasks/:id/replan
tasks.post("/tasks/:id/replan", async (c) => {
  const task = await db.getTask(c.req.param("id"));
  if (!task) return c.json({ error: "not found" }, 404);
  if (task.status !== "cancelled" && task.status !== "failed") {
    return c.json({ error: "only cancelled or failed tasks can be replanned" }, 400);
  }
  await db.replanAsPending(task.id);
  return c.json({ ok: true, message: "Task reset to plan-only and queued" });
});

// POST /tasks/:id/reply — sets reply_pending; the EC2 poller dispatches the reply.
tasks.post("/tasks/:id/reply", async (c) => {
  const task = await db.getTask(c.req.param("id"));
  if (!task) return c.json({ error: "not found" }, 404);
  await db.setReplyPending(task.id, true);
  return c.json({ ok: true, message: "Agent reply triggered" });
});

// DELETE /tasks/:id
tasks.delete("/tasks/:id", async (c) => {
  const taskId = c.req.param("id");
  const task = await db.getTask(taskId);
  if (!task) return c.json({ error: "not found" }, 404);

  const subtasks = await db.listSubtasks(taskId);
  for (const sub of subtasks) {
    await db.deleteTask(sub.id);
  }
  const ok = await db.deleteTask(taskId);
  if (!ok) return c.json({ error: "not found" }, 404);
  return c.json({ ok: true });
});

// GET /repos
tasks.get("/repos", async (c) => {
  return c.json({ repos: await db.getRepos() });
});

// GET /roles
tasks.get("/roles", async (c) => {
  return c.json({ roles: ROLES });
});

// GET /counts
tasks.get("/counts", async (c) => {
  return c.json(await db.getCounts());
});

// GET /logs
tasks.get("/logs", async (c) => {
  const taskId = c.req.query("task_id");
  const limit = Number(c.req.query("limit") ?? "200");
  const offset = Number(c.req.query("offset") ?? "0");
  const entries = await db.readLogs({ taskId, limit, offset });
  return c.json({ entries, count: entries.length });
});

// GET /budget
tasks.get("/budget", async (c) => {
  return c.json(await budgetStatus());
});

const TOKEN_KEYS = [
  "inputTokens",
  "outputTokens",
  "cacheReadTokens",
  "cacheWriteTokens",
] as const;

function emptyTokenRecord(): Record<(typeof TOKEN_KEYS)[number], number> {
  return {
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
  };
}

/** Aggregate pipeline log token usage — matches Python GET /stats. */
async function computeStats() {
  const entries = await db.readLogs({ limit: 10_000, offset: 0, forStats: true });
  const todayStr = new Date().toISOString().slice(0, 10);

  const todayTokens = emptyTokenRecord();
  const allTokens = emptyTokenRecord();
  const dailyBuckets = new Map<string, ReturnType<typeof emptyTokenRecord>>();

  for (const e of entries) {
    const extra = e.extra ?? {};
    const usage: Partial<Record<(typeof TOKEN_KEYS)[number], number>> = {};
    for (const k of TOKEN_KEYS) {
      const raw = extra[k];
      if (raw === undefined || raw === null) continue;
      const v = typeof raw === "number" ? raw : Number(raw);
      if (!Number.isFinite(v)) continue;
      usage[k] = Math.floor(v);
      allTokens[k] += usage[k]!;
    }
    if (Object.keys(usage).length === 0) continue;

    const ts = e.ts ?? "";
    const day = ts.length >= 10 ? ts.slice(0, 10) : "";
    if (day) {
      let bucket = dailyBuckets.get(day);
      if (!bucket) {
        bucket = emptyTokenRecord();
        dailyBuckets.set(day, bucket);
      }
      for (const k of TOKEN_KEYS) {
        if (usage[k] != null) bucket[k] += usage[k]!;
      }
    }
    if (ts.startsWith(todayStr)) {
      for (const k of TOKEN_KEYS) {
        if (usage[k] != null) todayTokens[k] += usage[k]!;
      }
    }
  }

  const todayCost = estimateCost(todayTokens);
  const allCost = estimateCost(allTokens);

  const sortedDays = [...dailyBuckets.keys()].sort();
  const daily = sortedDays.slice(-14).map((date) => {
    const bucket = dailyBuckets.get(date)!;
    return {
      date,
      cost_usd: Math.round(estimateCost(bucket) * 10_000) / 10_000,
      tokens: TOKEN_KEYS.reduce((s, k) => s + bucket[k], 0),
    };
  });

  return {
    today: { ...todayTokens, cost_usd: Math.round(todayCost * 10_000) / 10_000 },
    all_time: { ...allTokens, cost_usd: Math.round(allCost * 10_000) / 10_000 },
    daily,
  };
}

// GET /stats — aggregate token usage from DynamoDB pipeline logs
tasks.get("/stats", async (c) => {
  return c.json(await computeStats());
});
