import {
  DynamoDBClient,
  type DynamoDBClientConfig,
} from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  QueryCommand,
  ScanCommand,
  UpdateCommand,
  BatchWriteCommand,
} from "@aws-sdk/lib-dynamodb";
import type {
  Task,
  TaskStatus,
  TaskPriority,
  TaskAssignee,
  Comment,
  LogEntry,
  StatusCounts,
  Project,
  ProjectStatus,
  Directive,
  KPI,
  DailyPlan,
  PlanItem,
  PlanStatus,
} from "./types.js";

const TABLE_NAME = process.env.DYNAMO_TABLE ?? "agent-tasks";
const REGION = process.env.AWS_REGION ?? "us-west-2";

const PRIORITY_SORT: Record<string, string> = {
  urgent: "0",
  high: "1",
  medium: "2",
  low: "3",
};

const config: DynamoDBClientConfig = { region: REGION };
const raw = new DynamoDBClient(config);
export const ddb = DynamoDBDocumentClient.from(raw, {
  marshallOptions: { removeUndefinedValues: true },
});
export { TABLE_NAME };

function pk(taskId: string): string {
  return `TASK#${taskId}`;
}

function prioritySortCreated(priority: string, createdAt: string): string {
  return `${PRIORITY_SORT[priority] ?? "2"}#${createdAt}`;
}

function itemToTask(item: Record<string, unknown>): Task {
  let tags = item.tags as string[] | string | undefined;
  if (typeof tags === "string") {
    tags = tags
      .replace(/^\[|]$/g, "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
  }
  let deps = item.depends_on as string[] | string | undefined;
  if (typeof deps === "string") {
    deps = deps
      .replace(/^\[|]$/g, "")
      .split(",")
      .map((d) => d.trim())
      .filter(Boolean);
  }
  return {
    id: (item.task_id as string) ?? "",
    title: (item.title as string) ?? "",
    description: (item.description as string) ?? "",
    status: (item.status as TaskStatus) ?? "pending",
    priority: (item.priority as TaskPriority) ?? "medium",
    created_at: (item.created_at as string) ?? "",
    updated_at: (item.updated_at as string) ?? "",
    created_by: (item.created_by as string) ?? "",
    tags: Array.isArray(tags) ? tags : [],
    target_repo: (item.target_repo as string) ?? "",
    parent_id: (item.parent_id as string) ?? "",
    model: (item.model as string) ?? "",
    plan_only: Boolean(item.plan_only),
    depends_on: Array.isArray(deps) ? deps : [],
    session_id: (item.session_id as string) ?? "",
    reply_pending: Boolean(item.reply_pending),
    role: (item.role as string) ?? "",
    spawned_by: (item.spawned_by as string) ?? "",
    project_id: (item.project_id as string) ?? "",
    directive_sk: (item.directive_sk as string) ?? "",
    directive_date: (item.directive_date as string) ?? "",
    assignee: (item.assignee as TaskAssignee) ?? "agent",
  };
}

// ─── CRUD ────────────────────────────────────────────────────────────────────

export async function createTask(params: {
  title: string;
  description?: string;
  priority?: string;
  created_by?: string;
  tags?: string[];
  target_repo?: string;
  plan_only?: boolean;
  role?: string;
  model?: string;
  spawned_by?: string;
  project_id?: string;
  directive_sk?: string;
  directive_date?: string;
  depends_on?: string[];
  assignee?: string;
}): Promise<Task> {
  const id = crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const priority = params.priority ?? "medium";
  const task: Task = {
    id,
    title: params.title,
    description: params.description ?? "",
    status: "pending",
    priority: priority as TaskPriority,
    created_at: now,
    updated_at: now,
    created_by: params.created_by ?? "",
    tags: params.tags ?? [],
    target_repo: params.target_repo ?? "",
    parent_id: "",
    model: params.model ?? "",
    plan_only: params.plan_only ?? false,
    depends_on: params.depends_on ?? [],
    session_id: "",
    reply_pending: false,
    role: params.role ?? "",
    spawned_by: params.spawned_by ?? "",
    project_id: params.project_id ?? "",
    directive_sk: params.directive_sk ?? "",
    directive_date: params.directive_date ?? "",
    assignee: (params.assignee ?? "agent") as TaskAssignee,
  };

  const item: Record<string, unknown> = {
    pk: pk(id),
    sk: "META",
    task_id: id,
    title: task.title,
    description: task.description,
    status: task.status,
    priority: task.priority,
    priority_sort_created: prioritySortCreated(task.priority, task.created_at),
    created_at: task.created_at,
    updated_at: task.updated_at,
    created_by: task.created_by,
    tags: task.tags,
  };
  if (task.target_repo) item.target_repo = task.target_repo;
  if (task.plan_only) item.plan_only = true;
  if (task.role) item.role = task.role;
  if (task.spawned_by) item.spawned_by = task.spawned_by;
  if (task.project_id) item.project_id = task.project_id;
  if (task.directive_sk) item.directive_sk = task.directive_sk;
  if (task.directive_date) item.directive_date = task.directive_date;
  if (task.depends_on?.length) item.depends_on = task.depends_on;
  if (task.assignee && task.assignee !== "agent") item.assignee = task.assignee;

  await ddb.send(new PutCommand({ TableName: TABLE_NAME, Item: item }));
  return task;
}

export async function getTask(taskId: string): Promise<Task | null> {
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: pk(taskId), sk: "META" },
      ConsistentRead: true,
    })
  );
  return resp.Item ? itemToTask(resp.Item) : null;
}

export async function updateStatus(
  taskId: string,
  status: TaskStatus
): Promise<Task | null> {
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const priority = await _getPriority(taskId);
  try {
    const resp = await ddb.send(
      new UpdateCommand({
        TableName: TABLE_NAME,
        Key: { pk: pk(taskId), sk: "META" },
        UpdateExpression:
          "SET #st = :s, updated_at = :u, priority_sort_created = :psc",
        ExpressionAttributeNames: { "#st": "status" },
        ExpressionAttributeValues: {
          ":s": status,
          ":u": now,
          ":psc": prioritySortCreated(priority, now),
        },
        ConditionExpression: "attribute_exists(pk)",
        ReturnValues: "ALL_NEW",
      })
    );
    return resp.Attributes ? itemToTask(resp.Attributes) : null;
  } catch (err: unknown) {
    if ((err as { name?: string }).name === "ConditionalCheckFailedException")
      return null;
    throw err;
  }
}

async function _getPriority(taskId: string): Promise<string> {
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: pk(taskId), sk: "META" },
      ProjectionExpression: "priority",
    })
  );
  return (resp.Item?.priority as string) ?? "medium";
}

export async function listTasks(
  status?: TaskStatus,
  parentId?: string
): Promise<Task[]> {
  if (status) {
    const tasks: Task[] = [];
    let lastKey: Record<string, unknown> | undefined;
    do {
      const resp = await ddb.send(
        new QueryCommand({
          TableName: TABLE_NAME,
          IndexName: "status-index",
          KeyConditionExpression: "#st = :s",
          ExpressionAttributeNames: { "#st": "status" },
          ExpressionAttributeValues: { ":s": status },
          ScanIndexForward: true,
          ExclusiveStartKey: lastKey,
        })
      );
      tasks.push(...(resp.Items ?? []).map(itemToTask));
      lastKey = resp.LastEvaluatedKey;
    } while (lastKey);
    return tasks;
  }

  if (parentId) {
    const tasks: Task[] = [];
    let lastKey: Record<string, unknown> | undefined;
    do {
      const resp = await ddb.send(
        new QueryCommand({
          TableName: TABLE_NAME,
          IndexName: "parent-index",
          KeyConditionExpression: "parent_id = :p",
          ExpressionAttributeValues: { ":p": parentId },
          ExclusiveStartKey: lastKey,
        })
      );
      tasks.push(...(resp.Items ?? []).map(itemToTask));
      lastKey = resp.LastEvaluatedKey;
    } while (lastKey);
    return tasks;
  }

  const tasks: Task[] = [];
  let lastKey: Record<string, unknown> | undefined;
  do {
    const resp = await ddb.send(
      new ScanCommand({
        TableName: TABLE_NAME,
        FilterExpression: "sk = :meta",
        ExpressionAttributeValues: { ":meta": "META" },
        ExclusiveStartKey: lastKey,
      })
    );
    tasks.push(...(resp.Items ?? []).map(itemToTask));
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);

  return tasks;
}

export async function listSubtasks(parentId: string): Promise<Task[]> {
  return listTasks(undefined, parentId);
}

export async function listSpawnedTasks(spawnedBy: string): Promise<Task[]> {
  const resp = await ddb.send(
    new ScanCommand({
      TableName: TABLE_NAME,
      FilterExpression: "sk = :meta AND spawned_by = :s",
      ExpressionAttributeValues: { ":meta": "META", ":s": spawnedBy },
    })
  );
  return (resp.Items ?? []).map(itemToTask);
}

export async function depsReady(task: Task): Promise<boolean> {
  for (const depId of task.depends_on) {
    const dep = await getTask(depId);
    if (dep && dep.status !== "completed" && dep.status !== "in_review")
      return false;
  }
  return true;
}

export async function findDependents(taskId: string): Promise<Task[]> {
  const pending = await listTasks("pending");
  const results: Task[] = [];
  for (const t of pending) {
    if (t.depends_on.includes(taskId) && (await depsReady(t))) {
      results.push(t);
    }
  }
  return results;
}

export async function deleteTask(taskId: string): Promise<boolean> {
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "pk = :pk",
      ExpressionAttributeValues: { ":pk": pk(taskId) },
      ProjectionExpression: "pk, sk",
    })
  );
  const items = resp.Items ?? [];
  if (items.length === 0) return false;

  const batches: Record<string, unknown>[][] = [];
  for (let i = 0; i < items.length; i += 25) {
    batches.push(items.slice(i, i + 25));
  }
  for (const batch of batches) {
    await ddb.send(
      new BatchWriteCommand({
        RequestItems: {
          [TABLE_NAME]: batch.map((item) => ({
            DeleteRequest: { Key: { pk: item.pk, sk: item.sk } },
          })),
        },
      })
    );
  }
  return true;
}

// ─── Sections ────────────────────────────────────────────────────────────────

export async function getAgentOutput(
  taskId: string
): Promise<string | null> {
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :prefix)",
      ExpressionAttributeValues: { ":pk": pk(taskId), ":prefix": "OUTPUT#" },
      ScanIndexForward: false,
      Limit: 1,
    })
  );
  const items = resp.Items ?? [];
  return items.length > 0 ? ((items[0].body as string) ?? null) : null;
}

export async function getComments(taskId: string): Promise<Comment[]> {
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :prefix)",
      ExpressionAttributeValues: { ":pk": pk(taskId), ":prefix": "COMMENT#" },
      ScanIndexForward: true,
    })
  );
  return (resp.Items ?? []).map((i) => ({
    author: (i.author as string) ?? "",
    body: (i.body as string) ?? "",
    created_at: (i.created_at as string) ?? "",
  }));
}

export async function addComment(
  taskId: string,
  author: string,
  body: string
): Promise<Comment | null> {
  const task = await getTask(taskId);
  if (!task) return null;
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const comment: Comment = { author, body, created_at: now };
  await ddb.send(
    new PutCommand({
      TableName: TABLE_NAME,
      Item: {
        pk: pk(taskId),
        sk: `COMMENT#${now}`,
        author,
        body,
        created_at: now,
      },
    })
  );
  return comment;
}

// ─── Field setters ───────────────────────────────────────────────────────────

export async function setField(
  taskId: string,
  field: string,
  value: string | boolean
): Promise<void> {
  const safeName = `#f_${field.replace(/-/g, "_")}`;
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: pk(taskId), sk: "META" },
      UpdateExpression: `SET ${safeName} = :v`,
      ExpressionAttributeNames: { [safeName]: field },
      ExpressionAttributeValues: { ":v": value },
    })
  );
}

export async function removeField(
  taskId: string,
  field: string
): Promise<void> {
  const safeName = `#f_${field.replace(/-/g, "_")}`;
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: pk(taskId), sk: "META" },
      UpdateExpression: `REMOVE ${safeName}`,
      ExpressionAttributeNames: { [safeName]: field },
    })
  );
}

export async function setReplyPending(
  taskId: string,
  pending: boolean
): Promise<void> {
  if (pending) {
    await setField(taskId, "reply_pending", true);
  } else {
    await removeField(taskId, "reply_pending");
  }
}

export async function setCancelledBy(
  taskId: string,
  actor: string
): Promise<void> {
  await setField(taskId, "cancelled_by", actor);
}

export async function clearCancelledBy(taskId: string): Promise<void> {
  await removeField(taskId, "cancelled_by");
}

export async function replanAsPending(taskId: string): Promise<void> {
  await setField(taskId, "plan_only", true);
  await updateStatus(taskId, "pending");
}

// ─── Read helpers ────────────────────────────────────────────────────────────

export async function getPrUrl(taskId: string): Promise<string | null> {
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: pk(taskId), sk: "META" },
      ProjectionExpression: "pr_url",
    })
  );
  return (resp.Item?.pr_url as string) ?? null;
}

export async function getMergedAt(taskId: string): Promise<string | null> {
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: pk(taskId), sk: "META" },
      ProjectionExpression: "merged_at",
    })
  );
  return (resp.Item?.merged_at as string) ?? null;
}

export async function getDeployedAt(taskId: string): Promise<string | null> {
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: pk(taskId), sk: "META" },
      ProjectionExpression: "deployed_at",
    })
  );
  return (resp.Item?.deployed_at as string) ?? null;
}

export async function getCancelledBy(
  taskId: string
): Promise<string | null> {
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: pk(taskId), sk: "META" },
      ProjectionExpression: "cancelled_by",
    })
  );
  return (resp.Item?.cancelled_by as string) ?? null;
}

export async function findTaskByPrUrl(prUrl: string): Promise<string | null> {
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      IndexName: "pr-index",
      KeyConditionExpression: "pr_url = :p",
      ExpressionAttributeValues: { ":p": prUrl.replace(/\/$/, "") },
      Limit: 1,
      ProjectionExpression: "task_id",
    })
  );
  const items = resp.Items ?? [];
  return items.length > 0 ? (items[0].task_id as string) : null;
}

export async function getRepos(): Promise<string[]> {
  const repos = new Set<string>();
  let lastKey: Record<string, unknown> | undefined;
  do {
    const resp = await ddb.send(
      new ScanCommand({
        TableName: TABLE_NAME,
        FilterExpression:
          "sk = :meta AND attribute_exists(target_repo)",
        ExpressionAttributeValues: { ":meta": "META" },
        ProjectionExpression: "target_repo",
        ExclusiveStartKey: lastKey,
      })
    );
    for (const item of resp.Items ?? []) {
      const raw = (item.target_repo as string)?.trim();
      if (!raw) continue;
      const repo = raw.includes("/") ? raw.split("/").pop()! : raw;
      if (repo) repos.add(repo);
    }
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);

  const known = process.env.KNOWN_REPOS ?? "";
  if (known) {
    for (const r of known.split(",")) {
      const trimmed = r.trim();
      if (trimmed) repos.add(trimmed);
    }
  }
  return [...repos].sort();
}

export async function getCounts(): Promise<StatusCounts> {
  const tasks = await listTasks();
  const topLevel = tasks.filter((t) => !t.parent_id);
  const counts: StatusCounts = {
    all: topLevel.length,
    pending: 0,
    in_progress: 0,
    in_review: 0,
    completed: 0,
    cancelled: 0,
    failed: 0,
    human: 0,
  };
  for (const t of topLevel) {
    counts[t.status]++;
    if (t.assignee === "human" && !["completed", "cancelled"].includes(t.status)) {
      counts.human++;
    }
  }
  return counts;
}

// ─── Pipeline logs ───────────────────────────────────────────────────────────

export async function readLogs(params: {
  taskId?: string;
  limit?: number;
  offset?: number;
  /** When true, allow up to 10k entries (for /stats aggregation). Default cap is 500. */
  forStats?: boolean;
}): Promise<LogEntry[]> {
  const maxCap = params.forStats ? 10_000 : 500;
  const limit = Math.min(params.limit ?? 200, maxCap);
  const offset = params.offset ?? 0;

  if (params.taskId) {
    const resp = await ddb.send(
      new QueryCommand({
        TableName: TABLE_NAME,
        KeyConditionExpression: "pk = :pk AND begins_with(sk, :prefix)",
        ExpressionAttributeValues: {
          ":pk": pk(params.taskId),
          ":prefix": "LOG#",
        },
        ScanIndexForward: false,
      })
    );
    const items = resp.Items ?? [];
    return items.slice(offset, offset + limit).map(logItemToEntry);
  }

  // Full scan for all logs — expensive but matches Python behavior
  const allLogs: Record<string, unknown>[] = [];
  let lastKey: Record<string, unknown> | undefined;
  do {
    const resp = await ddb.send(
      new ScanCommand({
        TableName: TABLE_NAME,
        FilterExpression: "begins_with(sk, :prefix)",
        ExpressionAttributeValues: { ":prefix": "LOG#" },
        ExclusiveStartKey: lastKey,
      })
    );
    allLogs.push(...(resp.Items ?? []));
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);

  allLogs.sort((a, b) =>
    ((b.created_at as string) ?? "").localeCompare(
      (a.created_at as string) ?? ""
    )
  );
  return allLogs.slice(offset, offset + limit).map(logItemToEntry);
}

function logItemToEntry(item: Record<string, unknown>): LogEntry {
  const { pk: _pk, sk: _sk, event, stage, message, created_at, ...rest } =
    item;
  return {
    ts: (created_at as string) ?? "",
    task_id: ((item.pk as string) ?? "").replace(/^TASK#/, ""),
    event: (event as string) ?? "",
    stage: (stage as string) ?? "",
    message: (message as string) ?? "",
    extra: Object.keys(rest).length > 0 ? rest : undefined,
  };
}

// ─── Projects & directives ───────────────────────────────────────────────────

function projectPk(id: string): string {
  return `PROJECT#${id}`;
}

function itemToProject(item: Record<string, unknown>): Project {
  let kpis = item.kpis as KPI[] | undefined;
  if (!Array.isArray(kpis)) kpis = [];
  return {
    id: (item.project_id as string) ?? "",
    title: (item.title as string) ?? "",
    spec: (item.spec as string) ?? "",
    status: ((item.proj_status as string) ?? "active") as ProjectStatus,
    priority: ((item.priority as TaskPriority) ?? "medium") as TaskPriority,
    target_repo: (item.target_repo as string) ?? "",
    created_at: (item.created_at as string) ?? "",
    updated_at: (item.updated_at as string) ?? "",
    awaiting_next_directive: Boolean(item.awaiting_next_directive),
    active_directive_sk: (item.active_directive_sk as string) ?? "",
    kpis,
    autopilot: Boolean(item.autopilot),
  };
}

function itemToDirective(item: Record<string, unknown>): Directive {
  const sk = (item.sk as string) ?? "";
  let taskIds = item.task_ids as string[] | undefined;
  if (!Array.isArray(taskIds)) taskIds = [];
  return {
    sk,
    author: (item.author as string) ?? "user",
    content: (item.content as string) ?? "",
    created_at: (item.created_at as string) ?? "",
    task_ids: taskIds,
  };
}

export async function createProject(params: {
  title: string;
  spec?: string;
  priority?: TaskPriority;
  target_repo?: string;
  status?: ProjectStatus;
  kpis?: KPI[];
  autopilot?: boolean;
}): Promise<Project> {
  const id = crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const status = params.status ?? "active";
  const item: Record<string, unknown> = {
    pk: projectPk(id),
    sk: "PROJECT",
    project_id: id,
    title: params.title,
    spec: params.spec ?? "",
    proj_status: status,
    priority: params.priority ?? "medium",
    created_at: now,
    updated_at: now,
    project_updated: now,
    awaiting_next_directive: false,
    active_directive_sk: "",
    kpis: params.kpis ?? [],
    autopilot: params.autopilot ?? false,
  };
  if (params.target_repo?.trim()) item.target_repo = params.target_repo.trim();
  await ddb.send(new PutCommand({ TableName: TABLE_NAME, Item: item }));
  return itemToProject(item);
}

export async function getProject(projectId: string): Promise<Project | null> {
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: projectPk(projectId), sk: "PROJECT" },
      ConsistentRead: true,
    })
  );
  return resp.Item ? itemToProject(resp.Item) : null;
}

export async function listProjects(
  status?: ProjectStatus
): Promise<Project[]> {
  if (status) {
    const projects: Project[] = [];
    let lastKey: Record<string, unknown> | undefined;
    do {
      const resp = await ddb.send(
        new QueryCommand({
          TableName: TABLE_NAME,
          IndexName: "project-list-index",
          KeyConditionExpression: "proj_status = :s",
          ExpressionAttributeValues: { ":s": status },
          ScanIndexForward: false,
          ExclusiveStartKey: lastKey,
        })
      );
      projects.push(...(resp.Items ?? []).map(itemToProject));
      lastKey = resp.LastEvaluatedKey;
    } while (lastKey);
    return projects.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }
  const statuses: ProjectStatus[] = ["active", "paused", "completed"];
  const all: Project[] = [];
  for (const st of statuses) {
    all.push(...(await listProjects(st)));
  }
  return all.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

export async function updateProject(
  projectId: string,
  updates: Partial<{
    title: string;
    spec: string;
    status: ProjectStatus;
    priority: TaskPriority;
    target_repo: string;
    awaiting_next_directive: boolean;
    active_directive_sk: string;
    kpis: KPI[];
    autopilot: boolean;
  }>
): Promise<Project | null> {
  const p = await getProject(projectId);
  if (!p) return null;
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const names: Record<string, string> = {
    "#u": "updated_at",
    "#pu": "project_updated",
  };
  const vals: Record<string, unknown> = {
    ":u": now,
    ":pu": now,
  };
  const sets: string[] = ["#u = :u", "#pu = :pu"];
  if (updates.title !== undefined) {
    names["#t"] = "title";
    vals[":t"] = updates.title;
    sets.push("#t = :t");
  }
  if (updates.spec !== undefined) {
    names["#sp"] = "spec";
    vals[":sp"] = updates.spec;
    sets.push("#sp = :sp");
  }
  if (updates.status !== undefined) {
    names["#ps"] = "proj_status";
    vals[":ps"] = updates.status;
    sets.push("#ps = :ps");
  }
  if (updates.priority !== undefined) {
    names["#pr"] = "priority";
    vals[":pr"] = updates.priority;
    sets.push("#pr = :pr");
  }
  const removes: string[] = [];
  if (updates.target_repo !== undefined) {
    if (updates.target_repo.trim()) {
      names["#tr"] = "target_repo";
      vals[":tr"] = updates.target_repo.trim();
      sets.push("#tr = :tr");
    } else {
      names["#tr"] = "target_repo";
      removes.push("#tr");
    }
  }
  if (updates.awaiting_next_directive !== undefined) {
    names["#an"] = "awaiting_next_directive";
    vals[":an"] = updates.awaiting_next_directive;
    sets.push("#an = :an");
  }
  if (updates.active_directive_sk !== undefined) {
    names["#ad"] = "active_directive_sk";
    vals[":ad"] = updates.active_directive_sk;
    sets.push("#ad = :ad");
  }
  if (updates.kpis !== undefined) {
    names["#kp"] = "kpis";
    vals[":kp"] = updates.kpis;
    sets.push("#kp = :kp");
  }
  if (updates.autopilot !== undefined) {
    names["#ap"] = "autopilot";
    vals[":ap"] = updates.autopilot;
    sets.push("#ap = :ap");
  }
  let updateExpr = `SET ${sets.join(", ")}`;
  if (removes.length) updateExpr += ` REMOVE ${removes.join(", ")}`;
  const resp = await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: projectPk(projectId), sk: "PROJECT" },
      UpdateExpression: updateExpr,
      ExpressionAttributeNames: names,
      ExpressionAttributeValues: vals,
      ReturnValues: "ALL_NEW",
    })
  );
  return resp.Attributes ? itemToProject(resp.Attributes) : null;
}

export async function deleteProject(projectId: string): Promise<boolean> {
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "pk = :pk",
      ExpressionAttributeValues: { ":pk": projectPk(projectId) },
      ProjectionExpression: "pk, sk",
    })
  );
  const items = resp.Items ?? [];
  if (items.length === 0) return false;
  for (let i = 0; i < items.length; i += 25) {
    const batch = items.slice(i, i + 25);
    await ddb.send(
      new BatchWriteCommand({
        RequestItems: {
          [TABLE_NAME]: batch.map((it) => ({
            DeleteRequest: { Key: { pk: it.pk, sk: it.sk } },
          })),
        },
      })
    );
  }
  return true;
}

export async function addDirective(
  projectId: string,
  author: string,
  content: string
): Promise<{ directive: Directive }> {
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const sk = `DIR#${now}`;
  await ddb.send(
    new PutCommand({
      TableName: TABLE_NAME,
      Item: {
        pk: projectPk(projectId),
        sk,
        author,
        content,
        created_at: now,
        task_ids: [],
      },
    })
  );
  return {
    directive: {
      sk,
      author,
      content,
      created_at: now,
      task_ids: [],
    },
  };
}

export async function listDirectives(projectId: string): Promise<Directive[]> {
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :d)",
      ExpressionAttributeValues: {
        ":pk": projectPk(projectId),
        ":d": "DIR#",
      },
      ScanIndexForward: true,
    })
  );
  return (resp.Items ?? []).map(itemToDirective);
}

export async function updateDirectiveTaskIds(
  projectId: string,
  directiveSk: string,
  taskIds: string[]
): Promise<void> {
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: projectPk(projectId), sk: directiveSk },
      UpdateExpression: "SET task_ids = :t",
      ExpressionAttributeValues: { ":t": taskIds },
    })
  );
}

export async function listTasksByProject(projectId: string): Promise<Task[]> {
  const tasks: Task[] = [];
  let lastKey: Record<string, unknown> | undefined;
  do {
    const resp = await ddb.send(
      new QueryCommand({
        TableName: TABLE_NAME,
        IndexName: "project-index",
        KeyConditionExpression: "project_id = :p",
        ExpressionAttributeValues: { ":p": projectId },
        ExclusiveStartKey: lastKey,
      })
    );
    tasks.push(...(resp.Items ?? []).map(itemToTask));
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);
  return tasks;
}

/** When all tasks for a directive batch are terminal, set project awaiting_next_directive. */
export async function maybeFinalizeDirectiveBatch(taskId: string): Promise<void> {
  const task = await getTask(taskId);
  if (!task?.project_id || !task.directive_sk) return;
  const related = await listTasksByProject(task.project_id);
  const batch = related.filter((t) => t.directive_sk === task.directive_sk);
  if (batch.length === 0) return;
  const terminal = new Set(["completed", "in_review", "cancelled", "failed"]);
  const allDone = batch.every((t) => terminal.has(t.status));
  if (allDone) {
    await updateProject(task.project_id, { awaiting_next_directive: true });
    if (task.directive_sk.startsWith("PLAN#")) {
      await finalizePlanBatchRecord(task.project_id, task.directive_sk, batch);
    }
  }
}

function planRecordSk(dateStr: string): string {
  return `PLAN#${dateStr}`;
}

function itemToPlan(item: Record<string, unknown>): DailyPlan {
  let items = item.items as PlanItem[] | undefined;
  if (!Array.isArray(items)) items = [];
  const norm: PlanItem[] = items.map((it) => ({
    title: typeof it.title === "string" ? it.title : "",
    description: typeof it.description === "string" ? it.description : "",
    role: typeof it.role === "string" ? it.role : "",
    priority: (["low", "medium", "high", "urgent"].includes(
      String((it as PlanItem).priority),
    )
      ? (it as PlanItem).priority
      : "medium") as TaskPriority,
  }));
  const st = (item.status as string) ?? "proposed";
  const status: PlanStatus =
    st === "approved" || st === "executing" || st === "completed"
      ? st
      : "proposed";
  return {
    sk: (item.sk as string) ?? "",
    plan_date: (item.plan_date as string) ?? "",
    status,
    reflection: (item.reflection as string) ?? "",
    human_notes: (item.human_notes as string) ?? "",
    items: norm,
    task_ids: Array.isArray(item.task_ids)
      ? (item.task_ids as string[])
      : [],
    created_at: (item.created_at as string) ?? "",
    approved_at: (item.approved_at as string) ?? null,
    completed_at: (item.completed_at as string) ?? null,
    outcome_summary:
      item.outcome_summary && typeof item.outcome_summary === "object"
        ? (item.outcome_summary as Record<string, number>)
        : null,
  };
}

async function finalizePlanBatchRecord(
  projectId: string,
  planSk: string,
  batch: Task[],
): Promise<void> {
  const counts: Record<string, number> = {
    completed: 0,
    in_review: 0,
    failed: 0,
    cancelled: 0,
  };
  for (const t of batch) {
    if (counts[t.status] !== undefined) counts[t.status]++;
  }
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: projectPk(projectId), sk: planSk },
      UpdateExpression:
        "SET #st = :st, completed_at = :c, outcome_summary = :o, updated_at = :u",
      ExpressionAttributeNames: { "#st": "status" },
      ExpressionAttributeValues: {
        ":st": "completed",
        ":c": now,
        ":o": counts,
        ":u": now,
      },
    }),
  );
}

export async function getPlan(
  projectId: string,
  dateStr: string,
): Promise<DailyPlan | null> {
  const resp = await ddb.send(
    new GetCommand({
      TableName: TABLE_NAME,
      Key: { pk: projectPk(projectId), sk: planRecordSk(dateStr) },
      ConsistentRead: true,
    }),
  );
  return resp.Item ? itemToPlan(resp.Item) : null;
}

export async function listPlans(
  projectId: string,
  limit = 14,
): Promise<DailyPlan[]> {
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :p)",
      ExpressionAttributeValues: { ":pk": projectPk(projectId), ":p": "PLAN#" },
      ScanIndexForward: false,
      Limit: limit,
    }),
  );
  return (resp.Items ?? []).map((it) => itemToPlan(it));
}

export async function approvePlanAndCreateTasks(
  projectId: string,
  dateStr: string,
  humanNotes: string,
  project: Project,
): Promise<{ plan: DailyPlan; tasks: Task[] } | null> {
  const plan = await getPlan(projectId, dateStr);
  if (!plan || plan.status !== "proposed" || plan.items.length === 0) return null;
  const planSk = planRecordSk(dateStr);
  const taskIds: string[] = [];
  const created: Task[] = [];
  for (const item of plan.items) {
    const task = await createTask({
      title: item.title.slice(0, 200),
      description: item.description,
      priority: item.priority,
      target_repo: project.target_repo,
      project_id: projectId,
      directive_sk: planSk,
      directive_date: dateStr,
      role: item.role?.trim() || undefined,
      created_by: "autopilot-plan",
    });
    taskIds.push(task.id);
    created.push(task);
  }
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: projectPk(projectId), sk: planSk },
      UpdateExpression:
        "SET task_ids = :t, #st = :st, approved_at = :a, human_notes = :n, updated_at = :u",
      ExpressionAttributeNames: { "#st": "status" },
      ExpressionAttributeValues: {
        ":t": taskIds,
        ":st": "approved",
        ":a": now,
        ":n": humanNotes.trim(),
        ":u": now,
      },
    }),
  );
  await updateProject(projectId, {
    active_directive_sk: planSk,
    awaiting_next_directive: false,
  });
  const updated = await getPlan(projectId, dateStr);
  return updated ? { plan: updated, tasks: created } : null;
}

export async function updatePlanItems(
  projectId: string,
  dateStr: string,
  items: PlanItem[],
  reflection?: string,
): Promise<DailyPlan | null> {
  const existing = await getPlan(projectId, dateStr);
  if (!existing || existing.status !== "proposed") return null;
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const names: Record<string, string> = { "#u": "updated_at", "#it": "items" };
  const vals: Record<string, unknown> = { ":u": now, ":it": items };
  let expr = "SET #it = :it, #u = :u";
  if (reflection !== undefined) {
    names["#r"] = "reflection";
    vals[":r"] = reflection;
    expr += ", #r = :r";
  }
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: projectPk(projectId), sk: planRecordSk(dateStr) },
      UpdateExpression: expr,
      ExpressionAttributeNames: names,
      ExpressionAttributeValues: vals,
    }),
  );
  return getPlan(projectId, dateStr);
}

/** Cancel pending tasks tied to any PLAN# batch (superseded by a new directive). */
export async function cancelPendingPlanTasks(projectId: string): Promise<number> {
  const tasks = await listTasksByProject(projectId);
  let n = 0;
  for (const t of tasks) {
    if (
      t.status === "pending" &&
      t.directive_sk.startsWith("PLAN#")
    ) {
      await updateStatus(t.id, "cancelled");
      await setCancelledBy(t.id, "directive");
      n++;
    }
  }
  return n;
}

// ---------------------------------------------------------------------------
// Snapshots
// ---------------------------------------------------------------------------

export interface Snapshot {
  sk: string;
  date: string;
  kpi_readings: Record<string, number | null>;
  reflection: string | null;
  created_at: string;
}

export async function listSnapshots(
  projectId: string,
  limit = 14,
): Promise<Snapshot[]> {
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :s)",
      ExpressionAttributeValues: {
        ":pk": projectPk(projectId),
        ":s": "SNAPSHOT#",
      },
      ScanIndexForward: false,
      Limit: limit,
    }),
  );
  return (resp.Items ?? []).map((item) => ({
    sk: (item.sk as string) ?? "",
    date: (item.date as string) ?? "",
    kpi_readings: (item.kpi_readings as Record<string, number | null>) ?? {},
    reflection: (item.reflection as string) ?? null,
    created_at: (item.created_at as string) ?? "",
  }));
}

// ---------------------------------------------------------------------------
// Proposals
// ---------------------------------------------------------------------------

export interface Proposal {
  sk: string;
  action: string;
  rationale: string;
  domain: string;
  target_kpi: string;
  status: string;
  feedback: string | null;
  task_id: string | null;
  outcome: string | null;
  created_at: string;
}

export async function listProposals(
  projectId: string,
  status?: string,
  limit = 50,
): Promise<Proposal[]> {
  const kce = "pk = :pk AND begins_with(sk, :s)";
  const vals: Record<string, unknown> = {
    ":pk": projectPk(projectId),
    ":s": "PROP#",
  };
  let filter: string | undefined;
  let names: Record<string, string> | undefined;
  if (status) {
    filter = "#st = :st";
    names = { "#st": "status" };
    vals[":st"] = status;
  }
  const resp = await ddb.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: kce,
      ...(filter
        ? { FilterExpression: filter, ExpressionAttributeNames: names }
        : {}),
      ExpressionAttributeValues: vals,
      ScanIndexForward: false,
      Limit: limit,
    }),
  );
  return (resp.Items ?? []).map(itemToProposal);
}

function itemToProposal(item: Record<string, unknown>): Proposal {
  return {
    sk: (item.sk as string) ?? "",
    action: (item.action as string) ?? "",
    rationale: (item.rationale as string) ?? "",
    domain: (item.domain as string) ?? "",
    target_kpi: (item.target_kpi as string) ?? "",
    status: (item.status as string) ?? "pending",
    feedback: (item.feedback as string) ?? null,
    task_id: (item.task_id as string) ?? null,
    outcome: (item.outcome as string) ?? null,
    created_at: (item.created_at as string) ?? "",
  };
}

export async function updateProposalStatus(
  projectId: string,
  propSk: string,
  status: string,
  extras?: { feedback?: string; task_id?: string; outcome?: string },
): Promise<void> {
  const names: Record<string, string> = { "#st": "status" };
  const vals: Record<string, unknown> = { ":st": status };
  const sets = ["#st = :st"];
  if (extras?.feedback !== undefined) {
    names["#fb"] = "feedback";
    vals[":fb"] = extras.feedback;
    sets.push("#fb = :fb");
  }
  if (extras?.task_id !== undefined) {
    names["#ti"] = "task_id";
    vals[":ti"] = extras.task_id;
    sets.push("#ti = :ti");
  }
  if (extras?.outcome !== undefined) {
    names["#oc"] = "outcome";
    vals[":oc"] = extras.outcome;
    sets.push("#oc = :oc");
  }
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE_NAME,
      Key: { pk: projectPk(projectId), sk: propSk },
      UpdateExpression: `SET ${sets.join(", ")}`,
      ExpressionAttributeNames: names,
      ExpressionAttributeValues: vals,
    }),
  );
}
