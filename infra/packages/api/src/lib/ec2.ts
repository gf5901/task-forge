import {
  EC2Client,
  DescribeInstancesCommand,
} from "@aws-sdk/client-ec2";

const REGION = process.env.AWS_REGION ?? "us-west-2";
const RUNNER_TAG_KEY = process.env.EC2_TAG_KEY ?? "Role";
const RUNNER_TAG_VALUE = process.env.EC2_TAG_VALUE ?? "task-forge-runner";

const ec2 = new EC2Client({ region: REGION });

let cached: string | undefined;

/**
 * Resolve the task-runner EC2 instance ID by tag lookup.
 * Falls back to EC2_INSTANCE_ID env var. Result is cached for the
 * Lambda's lifetime so the EC2 API is called at most once per cold start.
 */
export async function resolveInstanceId(): Promise<string> {
  if (cached !== undefined) return cached;

  const envId = (process.env.EC2_INSTANCE_ID ?? "").trim();
  if (envId) {
    cached = envId;
    return cached;
  }

  try {
    const resp = await ec2.send(
      new DescribeInstancesCommand({
        Filters: [
          { Name: `tag:${RUNNER_TAG_KEY}`, Values: [RUNNER_TAG_VALUE] },
          { Name: "instance-state-name", Values: ["running"] },
        ],
      }),
    );
    for (const r of resp.Reservations ?? []) {
      for (const inst of r.Instances ?? []) {
        if (inst.InstanceId) {
          cached = inst.InstanceId;
          return cached;
        }
      }
    }
  } catch (err) {
    console.warn("ec2: failed to resolve instance by tag:", err);
  }

  cached = "";
  return cached;
}
