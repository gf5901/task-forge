#!/usr/bin/env bash
# Build and deploy the React SPA to S3/CloudFront.
# Run this after any frontend change that isn't auto-deployed by GitHub Actions.
#
# Required env:
#   DEPLOY_UI_S3_BUCKET  — bucket name only (no s3://), e.g. my-app-ui
#   DEPLOY_UI_CLOUDFRONT_ID — CloudFront distribution ID
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"
: "${DEPLOY_UI_S3_BUCKET:?Set DEPLOY_UI_S3_BUCKET (see docs/infra-deploy.md)}"
: "${DEPLOY_UI_CLOUDFRONT_ID:?Set DEPLOY_UI_CLOUDFRONT_ID}"
BUCKET="s3://${DEPLOY_UI_S3_BUCKET}"
DISTRIBUTION_ID="${DEPLOY_UI_CLOUDFRONT_ID}"

echo "Building frontend..."
cd "$FRONTEND_DIR"
pnpm run build

echo "Uploading to S3..."
aws s3 sync dist/ "$BUCKET" --delete --quiet

echo "Invalidating CloudFront cache..."
aws cloudfront create-invalidation \
  --distribution-id "$DISTRIBUTION_ID" \
  --paths "/*" \
  --query 'Invalidation.Id' \
  --output text

echo "Done. Changes will be live once the invalidation completes (~30s)."
