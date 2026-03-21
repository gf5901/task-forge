# S3 + CloudFront deployment (React SPA)

Static hosting for the React SPA frontend via S3 + CloudFront with zero-downtime deploys.

## Split architecture

The frontend and backend are deployed independently:

| Layer | Typical host | Hosting | Deployed by |
|-------|----------------|---------|-------------|
| React SPA | `https://your-spa.example.com` | S3 + CloudFront | GitHub Actions on push to `main` (`.github/workflows/deploy-ui.yml`) |
| FastAPI API | `https://your-ec2.example.com` | EC2 (nginx → uvicorn) | GitHub webhook → `scripts/deploy.sh` |

- The EC2 host serves **only** `/api/*` for the legacy FastAPI app (if used). It does not build the SPA.
- CORS on FastAPI / Lambda must allow your SPA origin — set `CORS_ORIGINS` (EC2) or deploy SST with `SST_UI_ORIGIN` (Lambda API).
- `scripts/deploy.sh` on the server does **not** run `pnpm build` — the frontend is built in CI and uploaded to S3.

## AWS resources (your account)

Create your own S3 bucket, CloudFront distribution, ACM certificate, and Route 53 records. **Do not commit** bucket names, distribution IDs, certificate ARNs, or account IDs — configure them via environment variables and [GitHub Actions secrets](infra-deploy.md).

See **`docs/infra-deploy.md`** for:

- SST deploy variables (`SST_API_DOMAIN`, `SST_UI_ORIGIN`, etc.)
- Frontend CI secrets (`DEPLOY_UI_S3_BUCKET`, `DEPLOY_UI_CLOUDFRONT_ID`, `VITE_API_BASE_URL`, `AWS_OIDC_ROLE_ARN`)

## Request flow (conceptual)

```
Browser → your-spa.example.com (Route 53 alias → CloudFront)
        → S3 origin (private bucket, OAC / OAI)

Browser → Lambda API or EC2 /api/* (CORS from SPA origin)
```

## Deploying the frontend manually

```bash
cd frontend
pnpm run build

export DEPLOY_UI_S3_BUCKET=your-bucket-name
export DEPLOY_UI_CLOUDFRONT_ID=YOURDISTID
bash ../scripts/deploy-frontend.sh
```

Or use `aws s3 sync` and `aws cloudfront create-invalidation` with your own bucket and distribution ID.

## GitHub Actions

Configure repository secrets listed in `docs/infra-deploy.md`. The workflow `.github/workflows/deploy-ui.yml` builds with `VITE_API_BASE_URL` and syncs to your bucket.

## SPA routing (403/404 → index.html)

For client-side routes, configure CloudFront custom error responses or run `scripts/cloudfront-spa-error-pages.sh` after setting `DISTRIBUTION_ID`.
