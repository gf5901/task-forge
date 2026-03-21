#!/usr/bin/env bash
# Add CloudFront custom error responses so SPA client-side routes (e.g. /projects)
# work on refresh: 403 and 404 from S3 are served as 200 /index.html.
# Run with AWS_PROFILE=personal (or your profile) if needed.
set -euo pipefail

: "${DISTRIBUTION_ID:?Set DISTRIBUTION_ID to your CloudFront distribution ID}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Fetching current CloudFront distribution config..."
aws cloudfront get-distribution-config --id "$DISTRIBUTION_ID" > "$TMP/raw.json"

ETAG="$(jq -r '.ETag' "$TMP/raw.json")"
echo "ETag: $ETAG"

echo "Adding custom error responses (403, 404 → 200 /index.html)..."
jq '.DistributionConfig | .CustomErrorResponses = {
  "Quantity": 2,
  "Items": [
    {
      "ErrorCode": 403,
      "ResponsePagePath": "/index.html",
      "ResponseCode": "200",
      "ErrorCachingMinTTL": 0
    },
    {
      "ErrorCode": 404,
      "ResponsePagePath": "/index.html",
      "ResponseCode": "200",
      "ErrorCachingMinTTL": 0
    }
  ]
}' "$TMP/raw.json" > "$TMP/config.json"

echo "Updating CloudFront distribution..."
aws cloudfront update-distribution \
  --id "$DISTRIBUTION_ID" \
  --distribution-config "file://$TMP/config.json" \
  --if-match "$ETAG" \
  --query 'Distribution.{Id:Id,Status:Status}' \
  --output table

echo "Done. Status will move to Deployed in ~5–15 minutes; then refresh on /projects will work."
