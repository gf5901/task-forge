# Documentation index

## Getting started

| Document | Description |
|----------|-------------|
| [../README.md](../README.md) | Project overview, EC2 quick path, Discord setup, web UI |
| [local-dev.md](local-dev.md) | Tests, frontend dev server, Python venv |
| [ec2-setup.md](ec2-setup.md) | Full Amazon Linux bootstrap, IAM, Secrets Manager, systemd |
| [ec2-hardening.md](ec2-hardening.md) | SSH, firewalld, fail2ban, SELinux, audit rules, AIDE |

## Architecture & data

| Document | Description |
|----------|-------------|
| [dynamo-schema.md](dynamo-schema.md) | DynamoDB keys, GSIs, task/project/activity records |
| [compound-engineering.md](compound-engineering.md) | Worktree pipeline, planning, PRs, `AUTO_*` flags |
| [autonomous-objectives.md](autonomous-objectives.md) | KPIs, daily cycle, proposals (design) |
| [autonomous-objectives-setup.md](autonomous-objectives-setup.md) | Human setup for metrics integrations |

## AWS & deploy

| Document | Description |
|----------|-------------|
| [infra-deploy.md](infra-deploy.md) | SST env vars, GitHub Actions secrets for the SPA |
| [infra/README.md](../infra/README.md) | SST app layout, secrets, deploy workflow |
| [s3-cloudfront-deployment.md](s3-cloudfront-deployment.md) | React SPA on S3 + CloudFront |
| [run-trigger-verification.md](run-trigger-verification.md) | Lambda → EC2 trigger checks |

## Housekeeping

- `.cursor/rules/` and `AGENTS.md` files are **Cursor IDE / agent** context — not required reading for human contributors (see [CONTRIBUTING.md](../CONTRIBUTING.md)).
