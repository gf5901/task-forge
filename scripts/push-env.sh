#!/usr/bin/env bash
# Upload the current .env to AWS Secrets Manager.
# Creates the secret on first run, updates it on subsequent runs.
#
# Usage:
#   bash scripts/push-env.sh                 # uses default secret name + region
#   bash scripts/push-env.sh my-secret-name  # custom secret name
#
# Requires: aws CLI with credentials that have secretsmanager:CreateSecret,
#           secretsmanager:PutSecretValue, and secretsmanager:UpdateSecret.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
SECRET_NAME="${1:-task-forge/env}"
AWS_REGION="${AWS_REGION:-us-west-2}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[push-env]${NC} $*"; }
ok()   { echo -e "${GREEN}[push-env]${NC} $*"; }
die()  { echo -e "${RED}[push-env]${NC} $*" >&2; exit 1; }

command -v aws &>/dev/null || die "aws CLI not found"
command -v jq  &>/dev/null || die "jq not found"
[[ -f "$ENV_FILE" ]] || die ".env not found at $ENV_FILE"

aws sts get-caller-identity --region "$AWS_REGION" &>/dev/null \
    || die "No AWS credentials. Attach an IAM instance profile or run 'aws configure'."

info "Reading $ENV_FILE..."

# Parse .env into a JSON object (skip comments, blank lines, and empty values)
JSON=$(while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    # Skip keys with empty values
    [[ -z "$val" ]] && continue
    printf '%s\n' "$key" "$val"
done < "$ENV_FILE" | jq -Rn '
    [inputs] | . as $lines |
    reduce range(0; length; 2) as $i ({}; . + {($lines[$i]): $lines[$i+1]})
')

KEY_COUNT=$(echo "$JSON" | jq 'length')
info "Parsed $KEY_COUNT env var(s) with values"

# Create or update the secret
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$AWS_REGION" &>/dev/null; then
    info "Secret '$SECRET_NAME' exists — updating..."
    aws secretsmanager put-secret-value \
        --secret-id "$SECRET_NAME" \
        --secret-string "$JSON" \
        --region "$AWS_REGION" \
        --output text --query 'Name' >/dev/null
else
    info "Creating secret '$SECRET_NAME'..."
    aws secretsmanager create-secret \
        --name "$SECRET_NAME" \
        --description "task-forge .env configuration" \
        --secret-string "$JSON" \
        --region "$AWS_REGION" \
        --output text --query 'Name' >/dev/null
fi

ok "Pushed $KEY_COUNT var(s) to Secrets Manager: $SECRET_NAME ($AWS_REGION)"
echo ""
echo "To pull on a new instance:"
echo "  bash scripts/pull-env.sh"
