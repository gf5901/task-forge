# Infrastructure (SST)

This directory contains **SST v3** (Ion) configuration for AWS: DynamoDB, Lambda API (Hono/TypeScript), Watchdog, Digest, Metrics, Autopilot, and related resources. See `sst.config.ts` and `packages/*`.

## Before you deploy

1. **Read** [docs/infra-deploy.md](../docs/infra-deploy.md) for required env vars (`SST_API_DOMAIN`, `SST_DNS_ZONE_ID`, `SST_ACM_CERT_ARN`, `SST_UI_ORIGIN`, `SST_EC2_HEALTH_URL`, etc.).
2. **Copy** `infra/.env.example` → `infra/.env` (or export vars in your shell). Do not commit real domains, ARNs, or account IDs.
3. **Install** Node dependencies from the repo root or `infra/` as your workflow expects (`npm ci` / `pnpm install` if applicable).
4. **Install SST CLI** (e.g. `npx sst` / `pnpm exec sst`) on a machine with enough disk — not on the small EC2 runner.

## Secrets

Set SST secrets interactively (example):

```bash
cd infra
npx sst secret set Ec2InstanceId "i-xxxxxxxxxxxxxxxxx" --stage production
```

See `docs/infra-deploy.md` and the deploy skill at `.cursor/skills/deploy-infra/SKILL.md` for a full checklist.

## Related docs

| Doc | Topic |
|-----|--------|
| [../docs/infra-deploy.md](../docs/infra-deploy.md) | Env vars, GitHub Actions for UI deploy |
| [../docs/s3-cloudfront-deployment.md](../docs/s3-cloudfront-deployment.md) | SPA hosting |
| [../docs/dynamo-schema.md](../docs/dynamo-schema.md) | Table design |
