/**
 * Repo Scanner Lambda — runs on a schedule, scans configured GitHub repos for:
 * 1. Open issues with a configurable label (e.g., "agent")
 * 2. Failing CI checks on the default branch
 * 3. (Future) TODO comments in recent commits
 *
 * For each finding, creates a task in DynamoDB if one doesn't already exist.
 */

import {
  DynamoDBClient,
  type DynamoDBClientConfig,
} from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  PutCommand,
  ScanCommand,
} from "@aws-sdk/lib-dynamodb";
import { SSMClient, SendCommandCommand } from "@aws-sdk/client-ssm";
import { resolveInstanceId } from "../../api/src/lib/ec2";

const REGION = process.env.AWS_REGION ?? "us-west-2";
const TABLE_NAME = process.env.DYNAMO_TABLE ?? "agent-tasks";
const REPOS = (process.env.SCAN_REPOS ?? "")
  .split(",")
  .map((r) => r.trim())
  .filter(Boolean);
const GITHUB_OWNER = process.env.GITHUB_OWNER ?? "";
const ISSUE_LABEL = process.env.ISSUE_LABEL ?? "agent";
const SCAN_CI = process.env.SCAN_CI !== "false";

function getGitHubToken(): string {
  return (
    process.env.GITHUB_TOKEN ||
    (Resource as any).GitHubToken?.value ||
    ""
  );
}

const config: DynamoDBClientConfig = { region: REGION };
const raw = new DynamoDBClient(config);
const ddb = DynamoDBDocumentClient.from(raw, {
  marshallOptions: { removeUndefinedValues: true },
});

interface GitHubIssue {
  number: number;
  title: string;
  body: string | null;
  html_url: string;
  labels: { name: string }[];
  pull_request?: unknown;
}

interface CheckRun {
  name: string;
  conclusion: string | null;
  html_url: string;
}

const WORK_DIR = process.env.EC2_WORK_DIR ?? "/home/ec2-user/workspace/task-forge";
const VENV_PYTHON = `${WORK_DIR}/.venv/bin/python3`;
const RUN_TASK_SCRIPT = `${WORK_DIR}/run_task.py`;
const ssmClient = new SSMClient({ region: REGION });

async function triggerRunner(taskId: string): Promise<void> {
  const instanceId = await resolveInstanceId();
  if (!instanceId) {
    console.warn("Could not resolve EC2 instance — skipping runner trigger for %s", taskId);
    return;
  }
  try {
    await ssmClient.send(
      new SendCommandCommand({
        InstanceIds: [instanceId],
        DocumentName: "AWS-RunShellScript",
        Parameters: {
          commands: [
            `sudo -u ec2-user setsid ${VENV_PYTHON} ${RUN_TASK_SCRIPT} ${taskId} </dev/null >/dev/null 2>&1 &`,
          ],
          workingDirectory: [WORK_DIR],
        },
        TimeoutSeconds: 30,
      })
    );
  } catch (err) {
    console.error(`Failed to trigger runner for ${taskId}:`, err);
  }
}

async function ghFetch<T>(path: string): Promise<T> {
  const token = getGitHubToken();
  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetch(`https://api.github.com${path}`, { headers });
  if (!resp.ok) {
    throw new Error(`GitHub API ${path}: ${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<T>;
}

async function getExistingTaskSources(): Promise<Set<string>> {
  const sources = new Set<string>();
  let lastKey: Record<string, unknown> | undefined;
  do {
    const resp = await ddb.send(
      new ScanCommand({
        TableName: TABLE_NAME,
        FilterExpression: "sk = :meta AND attribute_exists(source_key)",
        ExpressionAttributeValues: { ":meta": "META" },
        ProjectionExpression: "source_key",
        ExclusiveStartKey: lastKey,
      })
    );
    for (const item of resp.Items ?? []) {
      sources.add(item.source_key as string);
    }
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);
  return sources;
}

async function createTask(params: {
  title: string;
  description: string;
  target_repo: string;
  tags: string[];
  source_key: string;
  priority?: string;
}): Promise<string> {
  const id = crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const priority = params.priority ?? "medium";
  const prioritySort: Record<string, string> = {
    urgent: "0",
    high: "1",
    medium: "2",
    low: "3",
  };

  await ddb.send(
    new PutCommand({
      TableName: TABLE_NAME,
      Item: {
        pk: `TASK#${id}`,
        sk: "META",
        task_id: id,
        title: params.title,
        description: params.description,
        status: "pending",
        priority,
        priority_sort_created: `${prioritySort[priority] ?? "2"}#${now}`,
        created_at: now,
        updated_at: now,
        created_by: "repo-scanner",
        tags: params.tags,
        target_repo: params.target_repo,
        source_key: params.source_key,
      },
    })
  );
  return id;
}

async function scanIssues(repo: string, existing: Set<string>): Promise<number> {
  const path = `/repos/${GITHUB_OWNER}/${repo}/issues?labels=${encodeURIComponent(ISSUE_LABEL)}&state=open&per_page=20`;
  let issues: GitHubIssue[];
  try {
    issues = await ghFetch<GitHubIssue[]>(path);
  } catch (err) {
    console.error(`Failed to fetch issues for ${repo}:`, err);
    return 0;
  }

  let created = 0;
  for (const issue of issues) {
    if (issue.pull_request) continue;
    const sourceKey = `github-issue:${GITHUB_OWNER}/${repo}#${issue.number}`;
    if (existing.has(sourceKey)) continue;

    const taskId = await createTask({
      title: `[${repo}] ${issue.title}`,
      description:
        `From GitHub issue: ${issue.html_url}\n\n${(issue.body ?? "").slice(0, 2000)}`,
      target_repo: repo,
      tags: ["from-github", ...issue.labels.map((l) => l.name)],
      source_key: sourceKey,
    });
    await triggerRunner(taskId);
    console.log(`Created task ${taskId} for ${sourceKey}`);
    existing.add(sourceKey);
    created++;
  }
  return created;
}

async function scanCI(repo: string, existing: Set<string>): Promise<number> {
  let defaultBranch: string;
  try {
    const repoInfo = await ghFetch<{ default_branch: string }>(
      `/repos/${GITHUB_OWNER}/${repo}`
    );
    defaultBranch = repoInfo.default_branch;
  } catch (err) {
    console.error(`Failed to fetch repo info for ${repo}:`, err);
    return 0;
  }

  let runs: { check_runs: CheckRun[] };
  try {
    runs = await ghFetch<{ check_runs: CheckRun[] }>(
      `/repos/${GITHUB_OWNER}/${repo}/commits/${defaultBranch}/check-runs?per_page=50`
    );
  } catch (err) {
    console.error(`Failed to fetch CI for ${repo}:`, err);
    return 0;
  }

  const failures = runs.check_runs.filter((r) => r.conclusion === "failure");
  if (failures.length === 0) return 0;

  const today = new Date().toISOString().slice(0, 10);
  const sourceKey = `ci-failure:${GITHUB_OWNER}/${repo}:${today}`;
  if (existing.has(sourceKey)) return 0;

  const failNames = failures.map((f) => `- ${f.name}`).join("\n");
  const taskId = await createTask({
    title: `[${repo}] CI failures on default branch`,
    description:
      `The following CI checks are failing on the default branch:\n\n${failNames}\n\n` +
      `Check: ${failures[0].html_url}`,
    target_repo: repo,
    tags: ["ci-failure", "from-scanner"],
    source_key: sourceKey,
    priority: "high",
  });
  await triggerRunner(taskId);
  console.log(`Created task ${taskId} for ${sourceKey}`);
  existing.add(sourceKey);
  return 1;
}

export async function handler(): Promise<void> {
  if (!GITHUB_OWNER) {
    console.warn("GITHUB_OWNER not set — skipping repo scan");
    return;
  }
  if (REPOS.length === 0) {
    console.warn("SCAN_REPOS not set — skipping repo scan");
    return;
  }
  if (!getGitHubToken()) {
    console.warn("GITHUB_TOKEN not set — skipping repo scan");
    return;
  }

  console.log(`Scanning ${REPOS.length} repo(s): ${REPOS.join(", ")}`);
  const existing = await getExistingTaskSources();
  console.log(`Found ${existing.size} existing scanner-created tasks`);

  let totalIssues = 0;
  let totalCI = 0;

  for (const repo of REPOS) {
    totalIssues += await scanIssues(repo, existing);
    if (SCAN_CI) {
      totalCI += await scanCI(repo, existing);
    }
  }

  console.log(
    `Scan complete: ${totalIssues} issue task(s), ${totalCI} CI failure task(s) created`
  );
}
