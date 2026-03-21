---
name: deploy-infra
description: Deploy SST infrastructure (Lambda API, DynamoDB, crons) to AWS from a developer machine. Use when the user asks to deploy, deploy infra, run sst deploy, update Lambda, or push infrastructure changes.
---

# Deploy Infrastructure (SST)

SST deploys must **not** run from the EC2 task runner instance (see project conventions — disk and provider downloads).

## Lambda API checks (before deploy)

```bash
cd infra/packages/api && npm ci && npm run typecheck && npm run lint && npm test
```

## Prerequisites

Verify before deploying:

```bash
node -v                                    # must be v22+
ls infra/node_modules/.bin/sst             # deps installed
aws sts get-caller-identity                # credentials valid (use AWS_PROFILE if needed)
```

If `node_modules` is missing: `cd infra && pnpm install`

## Configure deploy environment

Set the variables in **`docs/infra-deploy.md`** (copy `infra/.env.example` to `infra/.env` or export in your shell). At minimum: `SST_API_DOMAIN`, `SST_DNS_ZONE_ID`, `SST_ACM_CERT_ARN`, `SST_UI_ORIGIN`, `SST_EC2_HEALTH_URL`.

## Deploy

```bash
cd infra && npx sst deploy --stage production
```

Use the AWS profile your account expects, e.g. `AWS_PROFILE=myprofile npx sst deploy --stage production`.

## Setting / Updating Secrets

SST secrets are set per-stage and encrypted:

```bash
cd infra && npx sst secret set SecretName "value" --stage production
```

Typical secrets: `DiscordWebhookUrl`, `AuthSecretKey`, `AuthEmail`, `AuthPassword`, `Ec2InstanceId`, `GitHubToken`

List values:

```bash
cd infra && npx sst secret list --stage production
```

## Spec generation (Bedrock)

The API exposes `POST /api/projects/generate-spec` (JWT required when auth is on). The Lambda needs `bedrock:InvokeModel` (configured in `sst.config.ts`). Override before deploy: `export BEDROCK_SPEC_MODEL_ID=...` (must be enabled in the account/region).

## What Gets Deployed

| Resource | Type | Purpose |
|----------|------|---------|
| Tasks (DynamoDB) | `sst.aws.Dynamo` | Single-table task store with GSIs |
| Api (API Gateway + Lambda) | `sst.aws.ApiGatewayV2` | Hono API (custom domain from `SST_API_DOMAIN`) |
| Watchdog | `sst.aws.Cron` | Health check every 5 min |
| Digest | `sst.aws.Cron` | Daily Discord summary at 14:00 UTC |
| Metrics | `sst.aws.Cron` | Daily KPI collection at 06:00 UTC |
| RepoScanner | `sst.aws.Cron` | Hourly GitHub issue → task sync (if `SST_SCAN_REPOS` / token configured) |

## Troubleshooting

- **`pulumi:providers:aws error`**: Run `cd infra && pnpm install` to refresh providers
- **Credential errors**: Verify `aws sts get-caller-identity` works
- **Timeout on deploy**: SST plugin downloads can be slow on first run (~2 GB); subsequent deploys are faster
- **Secret not found**: Set it with `npx sst secret set` before deploying
- **Missing SST_* errors**: Fill in `infra/.env` per `docs/infra-deploy.md`

## Post-Deploy Verification

After deploying, verify the API (use your API hostname):

```bash
curl -s "https://${SST_API_DOMAIN}/api/health" | jq .
```

Replace with the domain you set for `SST_API_DOMAIN`.
