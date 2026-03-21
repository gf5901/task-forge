import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand } from "@aws-sdk/lib-dynamodb";
import { SSMClient, SendCommandCommand } from "@aws-sdk/client-ssm";
import { Resource } from "sst";

const TABLE = process.env.DYNAMO_TABLE ?? "agent-tasks";
const REGION = process.env.AWS_REGION ?? "us-west-2";
const WORK_DIR = process.env.EC2_WORK_DIR ?? "/home/ec2-user/workspace/task-forge";
const VENV_PYTHON = `${WORK_DIR}/.venv/bin/python3`;
const RUN_TASK_SCRIPT = `${WORK_DIR}/run_task.py`;

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }), {
  marshallOptions: { removeUndefinedValues: true },
});
const ssm = new SSMClient({ region: REGION });

function getInstanceId(): string {
  return (
    process.env.EC2_INSTANCE_ID ||
    (Resource as { Ec2InstanceId?: { value: string } }).Ec2InstanceId?.value ||
    ""
  );
}

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

async function listActiveAutopilotProjectIds(): Promise<string[]> {
  const ids: string[] = [];
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
      if (shouldTriggerProposePlan(item as Record<string, unknown>)) {
        ids.push(item.project_id as string);
      }
    }
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);
  return ids;
}

async function triggerProposePlan(projectId: string): Promise<void> {
  const instanceId = getInstanceId();
  if (!instanceId) {
    console.warn("No EC2_INSTANCE_ID — skipping autopilot plan trigger");
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

export async function handler(): Promise<void> {
  const today = new Date().toISOString().slice(0, 10);
  console.log(`Autopilot plan Lambda running (UTC date ${today}, hourly)`);

  const projectIds = await listActiveAutopilotProjectIds();
  console.log(`Found ${projectIds.length} active autopilot project(s)`);

  for (const id of projectIds) {
    try {
      await triggerProposePlan(id);
    } catch (err) {
      console.error(`Failed to trigger propose-plan for ${id}:`, err);
    }
  }

  console.log("Autopilot plan Lambda complete");
}
