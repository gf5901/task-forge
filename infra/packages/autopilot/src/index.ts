import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand } from "@aws-sdk/lib-dynamodb";
import { SSMClient, SendCommandCommand } from "@aws-sdk/client-ssm";
import { resolveInstanceId } from "../../api/src/lib/ec2";

const TABLE = process.env.DYNAMO_TABLE ?? "agent-tasks";
const REGION = process.env.AWS_REGION ?? "us-west-2";
const WORK_DIR = process.env.EC2_WORK_DIR ?? "/home/ec2-user/workspace/task-forge";
const VENV_PYTHON = `${WORK_DIR}/.venv/bin/python3`;
const RUN_TASK_SCRIPT = `${WORK_DIR}/run_task.py`;

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }), {
  marshallOptions: { removeUndefinedValues: true },
});
const ssm = new SSMClient({ region: REGION });

function shouldTriggerProposePlan(item: Record<string, unknown>): boolean {
  if (item.autopilot !== true || !item.project_id) return false;
  const mode =
    (item.autopilot_mode as string) === "continuous" ? "continuous" : "daily";
  if (mode === "continuous") {
    if (item.cycle_paused === true) return false;
    const started = (item.cycle_started_at as string) ?? "";
    if (!started.trim()) return false;
    return true;
  }
  const hour = new Date().getUTCHours();
  return hour === 7;
}

function shouldTriggerPmSweep(item: Record<string, unknown>): boolean {
  if (!item.project_id) return false;
  return item.reply_pending === true;
}

async function listActiveProjects(): Promise<{
  autopilotIds: string[];
  pmSweepIds: string[];
}> {
  const autopilotIds: string[] = [];
  const pmSweepIds: string[] = [];
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
      const rec = item as Record<string, unknown>;
      if (shouldTriggerProposePlan(rec)) {
        autopilotIds.push(rec.project_id as string);
      }
      if (shouldTriggerPmSweep(rec)) {
        pmSweepIds.push(rec.project_id as string);
      }
    }
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);
  return { autopilotIds, pmSweepIds };
}

async function triggerProposePlan(projectId: string): Promise<void> {
  const instanceId = await resolveInstanceId();
  if (!instanceId) {
    console.warn("Could not resolve EC2 instance — skipping autopilot plan trigger");
    return;
  }
  const esc = (s: string) => s.replace(/'/g, "'\\''");
  await ssm.send(
    new SendCommandCommand({
      InstanceIds: [instanceId],
      DocumentName: "AWS-RunShellScript",
      Parameters: {
        commands: [
          `sudo -u ec2-user setsid ${VENV_PYTHON} ${RUN_TASK_SCRIPT} --propose-plan '${esc(
            projectId,
          )}' >/dev/null 2>&1 &`,
        ],
        workingDirectory: [WORK_DIR],
      },
      TimeoutSeconds: 600,
    }),
  );
  console.log(`Triggered autopilot propose-plan for project ${projectId}`);
}

async function triggerPmSweep(projectId: string): Promise<void> {
  const instanceId = await resolveInstanceId();
  if (!instanceId) {
    console.warn("Could not resolve EC2 instance — skipping PM sweep trigger");
    return;
  }
  const esc = (s: string) => s.replace(/'/g, "'\\''");
  await ssm.send(
    new SendCommandCommand({
      InstanceIds: [instanceId],
      DocumentName: "AWS-RunShellScript",
      Parameters: {
        commands: [
          `sudo -u ec2-user setsid ${VENV_PYTHON} ${RUN_TASK_SCRIPT} --pm-reply '${esc(
            projectId,
          )}' >/dev/null 2>&1 &`,
        ],
        workingDirectory: [WORK_DIR],
      },
      TimeoutSeconds: 600,
    }),
  );
  console.log(`Triggered PM sweep for project ${projectId}`);
}

export async function handler(): Promise<void> {
  const today = new Date().toISOString().slice(0, 10);
  console.log(`Autopilot plan Lambda running (UTC date ${today}, hourly)`);

  const { autopilotIds, pmSweepIds } = await listActiveProjects();
  console.log(
    `Found ${autopilotIds.length} autopilot project(s), ${pmSweepIds.length} project(s) needing PM sweep`,
  );

  for (const id of autopilotIds) {
    try {
      await triggerProposePlan(id);
    } catch (err) {
      console.error(`Failed to trigger propose-plan for ${id}:`, err);
    }
  }

  for (const id of pmSweepIds) {
    if (autopilotIds.includes(id)) {
      console.log(`PM sweep for ${id} — project already triggered for autopilot, triggering PM separately`);
    }
    try {
      await triggerPmSweep(id);
    } catch (err) {
      console.error(`Failed to trigger PM sweep for ${id}:`, err);
    }
  }

  console.log("Autopilot plan Lambda complete");
}
