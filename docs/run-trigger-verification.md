# Run trigger and Activity — verification checklist

Use this after deploying the "run stuck pending" fixes to confirm Lambda → EC2 dispatch and Activity visibility.

## 1. Lambda: EC2 instance ID available

The API Lambda needs the EC2 instance ID so SSM can send `run_task.py` to your runner. It comes from the linked SST secret `Ec2InstanceId`.

**In code (already correct):**
- `infra/sst.config.ts`: API route has `link: [..., ec2InstanceId]`.
- `infra/packages/api/src/lib/ssm.ts`: `getInstanceId()` uses `process.env.EC2_INSTANCE_ID || Resource.Ec2InstanceId?.value`. If both are empty, trigger returns 503.

**What you must do:**
- Set the secret (once per stage) so the Lambda gets the value at runtime:
  ```bash
  cd infra && npx sst secret set Ec2InstanceId i-xxxxxxxxx
  ```
  Use your real EC2 instance ID (e.g. from AWS Console → EC2 → Instances, or `aws ec2 describe-instances`).
- Redeploy so the Lambda bundle picks up the secret:
  ```bash
  cd infra && npx sst deploy
  ```

**How to verify:**
- Call Run on a pending task from the UI. If you get a 503 with "EC2_INSTANCE_ID not set — runner dispatch unavailable", the secret is still missing or not linked.
- If Run returns 200, the Lambda had an instance ID (either from env or `Resource.Ec2InstanceId.value`).

## 2. EC2: Dynamo backend and table

The runner on EC2 must use the same DynamoDB table as the Lambda so task status and pipeline logs are visible in the UI.

**In code (already correct):**
- `run_task.py` and `src/runner.py` use `DYNAMO_TABLE` from the environment (see `.env.example`).

**What you must do (on the EC2 host):**
- In the repo’s `.env` (or the env used by `run_task.py` / systemd), set:
  ```bash
  DYNAMO_TABLE=agent-tasks
  ```
  Optionally set `AWS_REGION=us-west-2` if not already set. The instance (or its role) must have IAM permission to read/write the `agent-tasks` table and to receive SSM commands.

**How to verify:**
- After triggering a task, check that the task status moves from **Pending** to **In progress** (and then to Completed/In Review/Cancelled). If it stays Pending, either the trigger never reached EC2 (check step 1) or the runner on EC2 isn’t configured for DynamoDB (check `.env` and restart).

## 3. Redeploy API and restart EC2 runner

After changing SST config or secrets, redeploy. After changing EC2 `.env`, restart the runner (and optionally the web service if it also runs there).

**Commands:**
```bash
# From repo root — deploy Lambda (and linked secret)
cd infra && npx sst deploy

# On the EC2 host — restart runner so run_task.py uses new code and env
sudo systemctl restart taskbot-discord   # if the runner is run via a service
# If you run the runner only via cron/SSM, no restart needed for env; ensure .env is loaded by the process that runs run_task.py (e.g. same user’s env when SSM runs the command).
```

**How to verify:**
- Run a task from the UI and confirm it goes to **In progress** and that **Activity** shows events (e.g. `task_start`, `execute_start`, `execute_done`). That confirms trigger, Dynamo status updates, and Dynamo log mirroring are all working.
