import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  PutCommand,
  QueryCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";
import { SSMClient, SendCommandCommand } from "@aws-sdk/client-ssm";
import { Resource } from "sst";
import { fetchPageSpeedMetrics } from "./pagespeed.js";
import { fetchGitHubMetrics } from "./github.js";

const TABLE = process.env.DYNAMO_TABLE ?? "agent-tasks";
const REGION = process.env.AWS_REGION ?? "us-west-2";
const WORK_DIR = process.env.EC2_WORK_DIR ?? "/home/ec2-user/workspace/task-forge";
const VENV_PYTHON = `${WORK_DIR}/.venv/bin/python3`;
const RUN_TASK_SCRIPT = `${WORK_DIR}/run_task.py`;
const GITHUB_OWNER = process.env.GITHUB_OWNER ?? "";

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }), {
  marshallOptions: { removeUndefinedValues: true },
});
const ssm = new SSMClient({ region: REGION });

interface KPI {
  id: string;
  label: string;
  target: number;
  current: number;
  source: string;
  direction: string;
  unit: string;
}

interface ProjectRecord {
  pk: string;
  project_id: string;
  title: string;
  target_repo: string;
  kpis: KPI[];
}

function projectPk(id: string): string {
  return `PROJECT#${id}`;
}

async function getActiveProjectsWithKPIs(): Promise<ProjectRecord[]> {
  const projects: ProjectRecord[] = [];
  let lastKey: Record<string, unknown> | undefined;
  do {
    const resp = await ddb.send(
      new QueryCommand({
        TableName: TABLE,
        IndexName: "project-list-index",
        KeyConditionExpression: "proj_status = :s",
        ExpressionAttributeValues: { ":s": "active" },
        ExclusiveStartKey: lastKey,
      }),
    );
    for (const item of resp.Items ?? []) {
      const kpis = item.kpis as KPI[] | undefined;
      if (Array.isArray(kpis) && kpis.length > 0) {
        projects.push({
          pk: item.pk as string,
          project_id: item.project_id as string,
          title: item.title as string,
          target_repo: (item.target_repo as string) ?? "",
          kpis,
        });
      }
    }
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);
  return projects;
}

async function writeSnapshot(
  projectId: string,
  date: string,
  readings: Record<string, number | null>,
): Promise<void> {
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  await ddb.send(
    new PutCommand({
      TableName: TABLE,
      Item: {
        pk: projectPk(projectId),
        sk: `SNAPSHOT#${date}`,
        date,
        kpi_readings: readings,
        created_at: now,
      },
    }),
  );
}

async function updateKPICurrentValues(
  projectId: string,
  kpis: KPI[],
  readings: Record<string, number | null>,
): Promise<void> {
  const updated = kpis.map((kpi) => {
    const val = readings[kpi.id];
    return val !== null && val !== undefined ? { ...kpi, current: val } : kpi;
  });
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE,
      Key: { pk: projectPk(projectId), sk: "PROJECT" },
      UpdateExpression: "SET kpis = :k, updated_at = :u, project_updated = :u",
      ExpressionAttributeValues: { ":k": updated, ":u": now },
    }),
  );
}

function getInstanceId(): string {
  return (
    process.env.EC2_INSTANCE_ID ||
    (Resource as { Ec2InstanceId?: { value: string } }).Ec2InstanceId?.value ||
    ""
  );
}

async function triggerDailyCycle(projectId: string): Promise<void> {
  const instanceId = getInstanceId();
  if (!instanceId) {
    console.warn("No EC2_INSTANCE_ID — skipping daily cycle trigger");
    return;
  }
  const esc = (s: string) => s.replace(/'/g, "'\\''");
  await ssm.send(
    new SendCommandCommand({
      InstanceIds: [instanceId],
      DocumentName: "AWS-RunShellScript",
      Parameters: {
        commands: [
          `sudo -u ec2-user setsid ${VENV_PYTHON} ${RUN_TASK_SCRIPT} --daily-cycle '${esc(projectId)}' >/dev/null 2>&1 &`,
        ],
        workingDirectory: [WORK_DIR],
      },
      TimeoutSeconds: 600,
    }),
  );
  console.log(`Triggered daily cycle for project ${projectId}`);
}

export async function handler(): Promise<void> {
  const today = new Date().toISOString().slice(0, 10);
  console.log(`Metrics Lambda running for date ${today}`);

  const projects = await getActiveProjectsWithKPIs();
  console.log(`Found ${projects.length} active project(s) with KPIs`);

  for (const project of projects) {
    console.log(`Processing project ${project.project_id}: ${project.title}`);
    const readings: Record<string, number | null> = {};
    const sources = new Set(project.kpis.map((k) => k.source));

    if (sources.has("pagespeed") && project.target_repo) {
      const url =
        process.env[`PSI_URL_${project.project_id.toUpperCase()}`] ??
        `https://${project.target_repo.replace(/-/g, "")}.org`;
      console.log(`Fetching PageSpeed metrics for ${url}`);
      try {
        const psi = await fetchPageSpeedMetrics(url);
        if (psi.lighthouse_seo !== null) readings.lighthouse_seo = psi.lighthouse_seo;
        if (psi.lighthouse_perf !== null) readings.lighthouse_perf = psi.lighthouse_perf;
        if (psi.lighthouse_accessibility !== null)
          readings.lighthouse_accessibility = psi.lighthouse_accessibility;
        if (psi.lighthouse_best_practices !== null)
          readings.lighthouse_best_practices = psi.lighthouse_best_practices;
      } catch (err) {
        console.error(`PageSpeed fetch failed:`, err);
      }
    }

    if (sources.has("github") && project.target_repo) {
      if (!GITHUB_OWNER.trim()) {
        console.warn("GITHUB_OWNER not set — skipping GitHub metrics for this project");
      } else {
        console.log(`Fetching GitHub metrics for ${GITHUB_OWNER}/${project.target_repo}`);
        try {
          const gh = await fetchGitHubMetrics(GITHUB_OWNER, project.target_repo);
          if (gh.content_pages !== null) readings.content_pages = gh.content_pages;
          if (gh.recent_commits_7d !== null) readings.recent_commits_7d = gh.recent_commits_7d;
          if (gh.open_prs !== null) readings.open_prs = gh.open_prs;
        } catch (err) {
          console.error(`GitHub fetch failed:`, err);
        }
      }
    }

    if (Object.keys(readings).length === 0) {
      console.log(`No readings collected for project ${project.project_id}, skipping`);
      continue;
    }

    console.log(`Writing snapshot for ${project.project_id}:`, readings);
    await writeSnapshot(project.project_id, today, readings);
    await updateKPICurrentValues(project.project_id, project.kpis, readings);
    await triggerDailyCycle(project.project_id);
  }

  console.log("Metrics Lambda complete");
}
