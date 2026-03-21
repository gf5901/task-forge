# Infrastructure deploy configuration (SST)

Deploy-time values are **not** committed to the repo. Set them in the environment when you run `sst deploy`, or in a `.env` file in the `infra/` directory (SST loads env files from the app directory).

## Required for `sst deploy`

| Variable | Example | Purpose |
|----------|---------|---------|
| `SST_APP_NAME` | `agent-task-bot` | SST/Pulumi app (stack) name — must match the deployed stack. Defaults to `agent-task-bot`. |
| `SST_API_DOMAIN` | `api.agent.example.com` | Custom domain for the Lambda API (API Gateway HTTP API) |
| `SST_DNS_ZONE_ID` | `Z1234567890ABC` | Route 53 hosted zone ID for DNS validation |
| `SST_ACM_CERT_ARN` | `arn:aws:acm:us-west-2:123456789012:certificate/...` | ACM certificate ARN (same region as the API, or as required by SST) |
| `SST_UI_ORIGIN` | `https://your-spa.example.com` | Allowed origin for CORS on the Lambda API (your SPA URL, no trailing slash) |
| `SST_EC2_HEALTH_URL` | `https://your-ec2.example.com/api/health` | EC2 FastAPI health endpoint (Watchdog, Digest, Lambda `/api/health` proxy) |

Optional:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SST_DIGEST_UI_URL` | same as `SST_UI_ORIGIN` | Base URL for links in the daily Discord digest |
| `SST_GITHUB_OWNER` | *(empty)* | GitHub org/user for metrics (PageSpeed/GitHub KPI sources) |
| `DYNAMO_TABLE_NAME` | `agent-tasks` | Physical DynamoDB table name (`transform` in `sst.config.ts`) |
| `KNOWN_REPOS` | *(empty)* | Comma-separated repo names for Lambda `KNOWN_REPOS` |
| `SST_SCAN_REPOS` | *(empty)* | Comma-separated repos for the Repo Scanner cron |

## Example (local deploy)

```bash
cd infra
export SST_API_DOMAIN=api.agent.example.com
export SST_DNS_ZONE_ID=Z1234567890ABC
export SST_ACM_CERT_ARN=arn:aws:acm:us-west-2:123456789012:certificate/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export SST_UI_ORIGIN=https://your-spa.example.com
export SST_EC2_HEALTH_URL=https://your-ec2.example.com/api/health
npx sst deploy --stage production
```

SST secrets (e.g. `Ec2InstanceId`, `GitHubToken`) are still set with `npx sst secret set` — see `.cursor/skills/deploy-infra/SKILL.md`.

## GitHub Actions — frontend deploy

Workflow `.github/workflows/deploy-ui.yml` expects these **repository secrets**:

| Secret | Purpose |
|--------|---------|
| `AWS_OIDC_ROLE_ARN` | IAM role ARN for OIDC (`assume role`) |
| `DEPLOY_UI_S3_BUCKET` | S3 bucket name (no `s3://` prefix) |
| `DEPLOY_UI_CLOUDFRONT_ID` | CloudFront distribution ID |
| `VITE_API_BASE_URL` | Full origin for the Lambda API (e.g. `https://api.agent.example.com`) |
