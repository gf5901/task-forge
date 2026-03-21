import { SSMClient, SendCommandCommand } from "@aws-sdk/client-ssm";
import { Resource } from "sst";

const REGION = process.env.AWS_REGION ?? "us-west-2";
const WORK_DIR = process.env.EC2_WORK_DIR ?? "/home/ec2-user/workspace/task-forge";
const VENV_PYTHON = `${WORK_DIR}/.venv/bin/python3`;
const RUN_TASK_SCRIPT = `${WORK_DIR}/run_task.py`;

const ssmClient = new SSMClient({ region: REGION });

function getInstanceId(): string {
  return process.env.EC2_INSTANCE_ID || (Resource as any).Ec2InstanceId?.value || "";
}

/** Thrown when runner cannot be dispatched (e.g. EC2 instance ID not configured). */
export class RunnerUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RunnerUnavailableError";
  }
}

async function sendCommand(command: string): Promise<void> {
  const instanceId = getInstanceId();
  if (!instanceId) {
    throw new RunnerUnavailableError(
      "EC2_INSTANCE_ID not set — runner dispatch unavailable"
    );
  }
  await ssmClient.send(
    new SendCommandCommand({
      InstanceIds: [instanceId],
      DocumentName: "AWS-RunShellScript",
      Parameters: {
        commands: [command],
        workingDirectory: [WORK_DIR],
      },
      TimeoutSeconds: 600,
    })
  );
}

/** Decompose a project directive into tasks (runs on EC2). */
export async function triggerDirectiveDecomposition(
  projectId: string,
  directiveSk: string
): Promise<void> {
  const esc = (s: string) => s.replace(/'/g, "'\\''");
  await sendCommand(
    `sudo -u ec2-user setsid ${VENV_PYTHON} ${RUN_TASK_SCRIPT} --directive '${esc(
      projectId
    )}' '${esc(directiveSk)}' >/dev/null 2>&1 &`
  );
}

/** Run autopilot plan proposal on EC2 (Cursor agent). */
export async function triggerProposePlan(
  projectId: string,
  regenerate = false,
  planSuffix?: string
): Promise<void> {
  const esc = (s: string) => s.replace(/'/g, "'\\''");
  const extra = regenerate ? " --regenerate" : "";
  const suffixArg =
    planSuffix !== undefined && planSuffix !== ""
      ? ` --plan-suffix '${esc(planSuffix)}'`
      : "";
  await sendCommand(
    `sudo -u ec2-user setsid ${VENV_PYTHON} ${RUN_TASK_SCRIPT} --propose-plan '${esc(
      projectId
    )}'${extra}${suffixArg} >/dev/null 2>&1 &`
  );
}

export async function cancelRunner(taskId: string): Promise<void> {
  await sendCommand(
    `sudo -u ec2-user ${VENV_PYTHON} -c "from src.runner import kill_runner_for_task; kill_runner_for_task('${taskId}')"`
  );
}
