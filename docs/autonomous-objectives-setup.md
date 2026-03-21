# Autonomous Objectives — Human Setup Guide

What you (the human operator) need to do to get the daily cycle running.

## Phase 1: Deploy and create the pilot objective (no external accounts needed)

### 1. Deploy the infra changes

Configure `infra/.env` per `docs/infra-deploy.md`, then:

```bash
cd infra && npx sst deploy --stage production
```

This creates the Metrics Lambda (6 AM UTC daily cron) and deploys the updated API with snapshot/proposal endpoints.

### 2. Deploy the frontend

```bash
cd frontend && pnpm run build
aws s3 sync dist/ s3://YOUR_UI_BUCKET/ --delete
aws cloudfront create-invalidation --distribution-id YOUR_DISTRIBUTION_ID --paths "/*"
```

### 3. Restart the backend

```bash
sudo systemctl restart taskbot-web
sudo systemctl restart taskbot-poller
```

### 4. Create a pilot project

Via the API (or the UI):

```bash
curl -X POST https://YOUR_API_HOSTNAME/api/projects \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-token>" \
  -d '{
    "title": "Example: grow your public site",
    "target_repo": "my-public-site",
    "priority": "high",
    "spec": "Short product/technical description of the site you want to improve. Include current stack, goals, and constraints.",
    "kpis": [
      {"id": "lighthouse_seo", "label": "Lighthouse SEO Score", "target": 95, "current": 0, "source": "pagespeed", "direction": "maintain", "unit": "score"},
      {"id": "lighthouse_perf", "label": "Lighthouse Performance Score", "target": 90, "current": 0, "source": "pagespeed", "direction": "maintain", "unit": "score"},
      {"id": "content_pages", "label": "Content Pages Published", "target": 50, "current": 0, "source": "github", "direction": "up", "unit": "pages"},
      {"id": "weekly_visitors", "label": "Weekly Unique Visitors", "target": 5000, "current": 0, "source": "ga4", "direction": "up", "unit": "visitors"},
      {"id": "search_impressions", "label": "Monthly Search Impressions", "target": 50000, "current": 0, "source": "search_console", "direction": "up", "unit": "impressions"},
      {"id": "search_ctr", "label": "Search Console CTR", "target": 5, "current": 0, "source": "search_console", "direction": "up", "unit": "percent"}
    ]
  }'
```

Once created, the Metrics Lambda will start collecting Lighthouse scores and GitHub content counts at 6 AM UTC daily. The daily cycle will trigger automatically after each snapshot.

**What works immediately (no setup):**
- Lighthouse SEO and Performance scores (PageSpeed Insights API — free, public)
- Content page count and commit activity (GitHub API — token already exists)
- Daily reflection + proposals from the agent
- Proposal approval flow in the web UI
- KPI dashboard with sparklines

**What shows "—" until you set up accounts:**
- Weekly visitors (needs GA4)
- Search impressions and CTR (needs Search Console)

---

## Phase 2: Connect Google Analytics (when ready)

### 1. Create a GA4 property

- Go to https://analytics.google.com
- Create a new GA4 property for your site
- Get the Measurement ID (starts with `G-`)

### 2. Enable the GA tag on the site

In your site repo, add the GA script and replace `GA_MEASUREMENT_ID` with your real measurement ID. Commit and push.

### 3. Create a service account for API access

- Go to https://console.cloud.google.com
- Create a project (or use an existing one)
- Enable the "Google Analytics Data API"
- Create a service account (IAM → Service Accounts → Create)
- Generate a JSON key file
- In GA4 Admin → Property → Property Access Management, add the service account email as a Viewer

### 4. Store the credentials

```bash
aws ssm put-parameter \
  --name "/agent/ga4/service-account-key" \
  --type SecureString \
  --value "$(cat path/to/service-account-key.json)"
```

### 5. Add the GA4 adapter to the metrics Lambda

(This is a code task — the ga4.ts adapter needs to be built. Can be done by the agent itself once Phase 1 is running.)

---

## Phase 3: Connect Google Search Console (when ready)

### 1. Add the site to Search Console

- Go to https://search.google.com/search-console
- Add property: `https://your-site.example`
- Verify ownership (DNS TXT record or HTML file upload)

### 2. Create OAuth2 credentials

Search Console API requires OAuth2, not just a service account:

- In Google Cloud Console, go to APIs & Services → Credentials
- Create an OAuth 2.0 Client ID (type: Web application)
- Add `http://localhost:8080` as an authorized redirect URI (for one-time consent)
- Note the client ID and client secret

### 3. Run the one-time consent flow

```bash
# Install the helper
pip install google-auth-oauthlib

# Run consent
python3 -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_config(
    {'installed': {
        'client_id': 'YOUR_CLIENT_ID',
        'client_secret': 'YOUR_CLIENT_SECRET',
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    }},
    scopes=['https://www.googleapis.com/auth/webmasters.readonly']
)
creds = flow.run_local_server(port=8080)
print('Refresh token:', creds.refresh_token)
"
```

### 4. Store the refresh token

```bash
aws ssm put-parameter \
  --name "/agent/gsc/refresh-token" \
  --type SecureString \
  --value "YOUR_REFRESH_TOKEN"

# Also store client ID and secret
aws ssm put-parameter \
  --name "/agent/gsc/client-id" \
  --type SecureString \
  --value "YOUR_CLIENT_ID"

aws ssm put-parameter \
  --name "/agent/gsc/client-secret" \
  --type SecureString \
  --value "YOUR_CLIENT_SECRET"
```

### 5. Add the Search Console adapter to the metrics Lambda

(Code task — the search_console.ts adapter needs to be built.)

---

## Ongoing: Daily human review (~15 min)

Once the system is running, your daily routine is:

1. **Check Discord** — The digest (2 PM UTC) includes a KPI briefing and pending proposals
2. **Review proposals** — Go to the project detail page in the web UI, approve or reject proposals (rejected proposals can include feedback the agent sees next cycle)
3. **Fulfill requests** — If the agent asks for something (API key, account access, etc.), do it and mark the request as "Done"
4. **Optional: send a directive** — If you want to steer the agent's focus, send a directive from the project detail page

The agent adapts based on your approvals, rejections, and metric trends. Over time, its proposals should get more targeted as it learns from outcomes.
