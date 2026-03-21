#!/usr/bin/env bash
# Pull env vars from AWS Secrets Manager and write .env.
# Uses .env.example as a template so comments are preserved.
#
# Usage:
#   bash scripts/pull-env.sh                 # uses default secret name + region
#   bash scripts/pull-env.sh my-secret-name  # custom secret name
#
# Requires: aws CLI with credentials that have secretsmanager:GetSecretValue.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE="$PROJECT_DIR/.env.example"
SECRET_NAME="${1:-task-forge/env}"
AWS_REGION="${AWS_REGION:-us-west-2}"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[pull-env]${NC} $*"; }
ok()   { echo -e "${GREEN}[pull-env]${NC} $*"; }
warn() { echo -e "${YELLOW}[pull-env]${NC} $*"; }
die()  { echo -e "${RED}[pull-env]${NC} $*" >&2; exit 1; }

command -v aws &>/dev/null || die "aws CLI not found"
command -v jq  &>/dev/null || die "jq not found"
[[ -f "$ENV_EXAMPLE" ]] || die ".env.example not found at $ENV_EXAMPLE"

aws sts get-caller-identity --region "$AWS_REGION" &>/dev/null \
    || die "No AWS credentials. Attach an IAM instance profile or run 'aws configure'."

info "Fetching secret '$SECRET_NAME' from $AWS_REGION..."

JSON=$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_NAME" \
    --region "$AWS_REGION" \
    --query 'SecretString' \
    --output text 2>&1) \
    || die "Failed to fetch secret: $JSON"

KEY_COUNT=$(echo "$JSON" | jq 'length')
info "Retrieved $KEY_COUNT var(s) from Secrets Manager"

if [[ -f "$ENV_FILE" ]]; then
    BACKUP="$ENV_FILE.bak.$(date +%s)"
    cp "$ENV_FILE" "$BACKUP"
    warn "Existing .env backed up to $BACKUP"
fi

# Start from .env.example as template, overlay secret values.
# For each line in .env.example: if it's a KEY=VALUE line and the key exists
# in the secret, replace the value. Otherwise keep the line as-is.
OUTPUT=""
while IFS= read -r line; do
    if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
        key="${line%%=*}"
        secret_val=$(echo "$JSON" | jq -r --arg k "$key" '.[$k] // empty')
        if [[ -n "$secret_val" ]]; then
            OUTPUT+="${key}=${secret_val}"$'\n'
        else
            OUTPUT+="${line}"$'\n'
        fi
    elif [[ "$line" =~ ^#\ *[A-Za-z_][A-Za-z0-9_]*= ]]; then
        # Commented-out key (e.g. "# DYNAMO_TABLE=agent-tasks") — uncomment if secret has a value
        stripped="${line#\#}"
        stripped="${stripped# }"
        key="${stripped%%=*}"
        secret_val=$(echo "$JSON" | jq -r --arg k "$key" '.[$k] // empty')
        if [[ -n "$secret_val" ]]; then
            OUTPUT+="${key}=${secret_val}"$'\n'
        else
            OUTPUT+="${line}"$'\n'
        fi
    else
        OUTPUT+="${line}"$'\n'
    fi
done < "$ENV_EXAMPLE"

# Append any secret keys not present in .env.example
EXTRA=""
for key in $(echo "$JSON" | jq -r 'keys[]'); do
    if ! grep -qE "^#?\\s*${key}=" "$ENV_EXAMPLE" 2>/dev/null; then
        val=$(echo "$JSON" | jq -r --arg k "$key" '.[$k]')
        EXTRA+="${key}=${val}"$'\n'
    fi
done

if [[ -n "$EXTRA" ]]; then
    OUTPUT+=$'\n'"# --- Additional vars from Secrets Manager ---"$'\n'
    OUTPUT+="$EXTRA"
fi

printf '%s' "$OUTPUT" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

ok "Wrote $ENV_FILE ($KEY_COUNT secret values applied, mode 600)"
