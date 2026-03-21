import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  QueryCommand,
  ScanCommand,
  type ScanCommandOutput,
} from "@aws-sdk/lib-dynamodb";
import { Resource } from "sst";

const TABLE = process.env.DYNAMO_TABLE ?? "agent-tasks";
const REGION = process.env.AWS_REGION ?? "us-west-2";
const HEALTH_URL = process.env.HEALTH_URL ?? "";
const UI_URL = process.env.UI_URL ?? "";

const client = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }));

function cutoffIso(hours: number): string {
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
}

async function scanMetaSince(cutoff: string): Promise<Record<string, unknown>[]> {
  const items: Record<string, unknown>[] = [];
  let start: Record<string, unknown> | undefined;
  do {
    const resp: ScanCommandOutput = await client.send(
      new ScanCommand({
        TableName: TABLE,
        FilterExpression: "#sk = :meta AND #ua >= :c",
        ExpressionAttributeNames: { "#sk": "sk", "#ua": "updated_at" },
        ExpressionAttributeValues: {
          ":meta": "META",
          ":c": cutoff,
        },
        ExclusiveStartKey: start,
      })
    );
    items.push(...(resp.Items ?? []));
    start = resp.LastEvaluatedKey as Record<string, unknown> | undefined;
  } while (start);
  return items;
}

async function sendDiscord(content: string): Promise<void> {
  const res = Resource as { DiscordWebhookUrl?: { value: string } };
  const webhookUrl = res.DiscordWebhookUrl?.value;
  if (!webhookUrl) {
    console.warn("No Discord webhook — skipping digest");
    return;
  }
  const resp = await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content: content.slice(0, 1900),
    }),
  });
  if (!resp.ok) {
    console.error("Discord digest failed:", resp.status, await resp.text());
  }
}

export async function handler(): Promise<void> {
  const since = cutoffIso(24);
  const items = await scanMetaSince(since);

  const byProject = new Map<string, { done: number; active: number }>();
  let completed24 = 0;
  let inReview = 0;
  let inProgress = 0;
  let pending = 0;
  let humanPending = 0;

  for (const it of items) {
    const st = (it.status as string) ?? "";
    const pid = (it.project_id as string) ?? "";
    const assignee = (it.assignee as string) ?? "agent";
    if (st === "completed") completed24++;
    if (st === "in_review") inReview++;
    if (st === "in_progress") inProgress++;
    if (st === "pending") pending++;
    if (assignee === "human" && !["completed", "cancelled"].includes(st)) humanPending++;

    if (pid) {
      const cur = byProject.get(pid) ?? { done: 0, active: 0 };
      if (st === "completed" || st === "in_review") cur.done++;
      if (st === "pending" || st === "in_progress") cur.active++;
      byProject.set(pid, cur);
    }
  }

  let healthLine = "";
  if (!HEALTH_URL.trim()) {
    healthLine = "Health: not configured (HEALTH_URL empty).";
  } else {
    try {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), 10_000);
      const r = await fetch(HEALTH_URL, { signal: controller.signal });
      clearTimeout(t);
      if (r.ok) {
        const h = (await r.json()) as {
          disk_free_pct?: number;
          task_counts?: Record<string, number>;
        };
        healthLine = `Health: disk ${h.disk_free_pct ?? "?"}% free; pending ${h.task_counts?.pending ?? "?"}.`;
      } else {
        healthLine = `Health endpoint returned HTTP ${r.status}.`;
      }
    } catch (err) {
      healthLine = `Health unreachable: ${err instanceof Error ? err.message : String(err)}`;
    }
  }

  const lines: string[] = [
    "## Daily digest (24h)",
    `- Tasks completed / in review (recent): ~${completed24} / ${inReview}`,
    `- Pending: ${pending}, in progress: ${inProgress}`,
    ...(humanPending > 0 ? [`- **Your tasks**: ${humanPending} awaiting action`] : []),
    healthLine,
  ];

  if (byProject.size > 0) {
    lines.push("\n**By project**");
    for (const [pid, v] of [...byProject.entries()].slice(0, 12)) {
      lines.push(`- \`${pid}\`: ${v.done} done/review, ${v.active} active`);
    }
    if (byProject.size > 12) lines.push(`- …and ${byProject.size - 12} more projects`);
  }

  const msg = lines.join("\n");
  console.log(msg);
  await sendDiscord(msg);

  // Objective briefings — for each active project with KPIs
  const objectiveLines = await buildObjectiveBriefings();
  if (objectiveLines) {
    console.log(objectiveLines);
    await sendDiscord(objectiveLines);
  }
}

// ---------------------------------------------------------------------------
// Objective briefings
// ---------------------------------------------------------------------------

interface ProjectKPI {
  id: string;
  label: string;
  target: number;
  current: number;
  unit: string;
  direction: string;
}

interface ProjectWithKPIs {
  project_id: string;
  title: string;
  kpis: ProjectKPI[];
}

async function getActiveProjectsWithKPIs(): Promise<ProjectWithKPIs[]> {
  const projects: ProjectWithKPIs[] = [];
  let lastKey: Record<string, unknown> | undefined;
  do {
    const resp = await client.send(
      new QueryCommand({
        TableName: TABLE,
        IndexName: "project-list-index",
        KeyConditionExpression: "proj_status = :s",
        ExpressionAttributeValues: { ":s": "active" },
        ExclusiveStartKey: lastKey,
      }),
    );
    for (const item of resp.Items ?? []) {
      const kpis = item.kpis as ProjectKPI[] | undefined;
      if (Array.isArray(kpis) && kpis.length > 0) {
        projects.push({
          project_id: item.project_id as string,
          title: item.title as string,
          kpis,
        });
      }
    }
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);
  return projects;
}

async function getLatestSnapshot(
  projectId: string,
): Promise<Record<string, unknown> | null> {
  const resp = await client.send(
    new QueryCommand({
      TableName: TABLE,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :s)",
      ExpressionAttributeValues: {
        ":pk": `PROJECT#${projectId}`,
        ":s": "SNAPSHOT#",
      },
      ScanIndexForward: false,
      Limit: 1,
    }),
  );
  return (resp.Items ?? [])[0] ?? null;
}

async function getPendingProposals(
  projectId: string,
): Promise<Record<string, unknown>[]> {
  const resp = await client.send(
    new QueryCommand({
      TableName: TABLE,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :s)",
      FilterExpression: "#st = :pending",
      ExpressionAttributeNames: { "#st": "status" },
      ExpressionAttributeValues: {
        ":pk": `PROJECT#${projectId}`,
        ":s": "PROP#",
        ":pending": "pending",
      },
      ScanIndexForward: false,
      Limit: 20,
    }),
  );
  return resp.Items ?? [];
}

function dirArrow(dir: string, current: number, target: number): string {
  if (dir === "maintain") {
    const diff = Math.abs(current - target);
    const pct = target > 0 ? diff / target : 0;
    return pct <= 0.05 ? "✓" : "⚠";
  }
  if (dir === "up") return current >= target ? "✓" : "↑";
  if (dir === "down") return current <= target ? "✓" : "↓";
  return "";
}

async function buildObjectiveBriefings(): Promise<string | null> {
  const projects = await getActiveProjectsWithKPIs();
  if (projects.length === 0) return null;

  const sections: string[] = [];

  for (const project of projects.slice(0, 5)) {
    const [snapshot, proposals] = await Promise.all([
      getLatestSnapshot(project.project_id),
      getPendingProposals(project.project_id),
    ]);

    const lines: string[] = [`**📊 ${project.title}**`];

    // KPI readings
    lines.push("KPIs:");
    for (const kpi of project.kpis) {
      const arrow = dirArrow(kpi.direction, kpi.current, kpi.target);
      const currentStr = kpi.current > 0 ? String(kpi.current) : "—";
      lines.push(
        `  ${kpi.label}: ${currentStr} / ${kpi.target} ${kpi.unit}  ${arrow}`,
      );
    }

    // Reflection
    const reflection = (snapshot?.reflection as string) ?? "";
    if (reflection) {
      const short = reflection.length > 200 ? reflection.slice(0, 200) + "…" : reflection;
      lines.push(`\nReflection: ${short}`);
    }

    // Proposals
    if (proposals.length > 0) {
      const proposalIntro = UI_URL.trim()
        ? `\nProposals (${proposals.length} pending — ${UI_URL}/projects/${project.project_id}):`
        : `\nProposals (${proposals.length} pending):`;
      lines.push(proposalIntro);
      for (const p of proposals.slice(0, 5)) {
        lines.push(`  • ${(p.action as string) ?? "?"}`);
      }
      if (proposals.length > 5) {
        lines.push(`  …and ${proposals.length - 5} more`);
      }
    }

    sections.push(lines.join("\n"));
  }

  return sections.join("\n\n---\n\n");
}
