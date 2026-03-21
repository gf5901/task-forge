# Autonomous Objectives — Design Vision

> Status: **Phase A-C built** — schema, metrics Lambda, daily cycle, proposals, UI, and digest all implemented. Awaiting deployment and pilot.
> Last updated: 2026-03-20
>
> Design notes: see also `docs/dynamo-schema.md` for record shapes and `docs/autonomous-objectives-setup.md` for human setup.
> Pilot use case: pick a public site or product repo (Lighthouse + GitHub metrics first, GA4/GSC after human setup)
> Human setup guide: `docs/autonomous-objectives-setup.md`

## Problem

The current system is a developer tool: a human writes a directive, the agent writes code, the human reviews a PR. Every cycle requires human initiation and validation. The agent has no awareness of whether its work *achieved anything* after merge.

We want to evolve toward an **autonomous organizational unit** — an agent that can drive measurable business outcomes (traffic, revenue, engagement) with minimal human involvement, operating across domains beyond just code.

## Core Concept

**Projects become Objectives.** Each objective has target KPIs, data source integrations, and a deadline or ongoing cadence. The agent observes metrics, proposes strategies, executes across multiple domains (code, content, email, SEO), and adapts based on results.

The human stays in the loop **once per day** — reviewing proposals, approving risky actions, providing feedback, and handling tasks that require human-only access (account setup, vendor calls, physical-world actions).

## Daily Cycle

```
┌─────────────────────────────────────────────────────────────────┐
│                        DAILY CYCLE                              │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│  │ Observe  │──▶│ Reflect  │──▶│ Propose  │──▶│ Execute  │    │
│  │ Metrics  │   │ & Learn  │   │ Plan     │   │ Actions  │    │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘    │
│       │                             │               │           │
│       │                             ▼               │           │
│       │                     ┌──────────────┐        │           │
│       │                     │ Human Review │        │           │
│       │                     │ (once/day)   │        │           │
│       │                     │              │        │           │
│       │                     │ • Approve    │        │           │
│       │                     │ • Reject     │        │           │
│       │                     │ • Redirect   │        │           │
│       │                     │ • Fulfill    │        │           │
│       │                     │   requests   │        │           │
│       │                     └──────────────┘        │           │
│       │                                             │           │
│       ◀─────────────────────────────────────────────┘           │
│                    (next day: observe results)                   │
└─────────────────────────────────────────────────────────────────┘
```

### 1. Observe (automated, early morning)

The agent pulls metrics from integrated data sources and compares against KPI targets.

- Google Analytics → traffic, bounce rate, session duration
- Mixpanel → user events, funnels, retention
- Stripe / payment provider → revenue, conversion rate
- CRM → leads, pipeline value
- Search Console → impressions, click-through rate, keyword rankings
- GitHub → PR merge rate, deploy frequency (internal health)

Data is fetched via APIs and stored as a time-series snapshot tied to the objective.

### 2. Reflect (automated)

The agent reviews:
- What actions were taken since the last cycle
- What metrics moved (and in which direction)
- Which strategies appear to be working vs. not
- Any anomalies or regressions

This produces a structured **daily briefing** — a short summary posted to Discord or the web UI.

### 3. Propose (automated → human review)

Based on the reflection, the agent generates:

**a) Self-directed actions** (low-risk, pre-approved categories):
- Write a blog post on topic X
- Optimize page Y for keyword Z
- Fix bug causing error spike
- Update documentation
- Deploy already-approved PR

These execute immediately without waiting for approval.

**b) Proposals requiring approval** (higher risk or cost):
- "I'd like to restructure the pricing page — here's my rationale and mockup description"
- "Keyword X has high volume and low competition — should I create a landing page?"
- "Traffic from source Y dropped 30% — I think we should investigate Z"
- "I recommend starting a weekly email newsletter targeting segment A"

**c) Human action requests** — things the agent can't do itself:
- "Please connect the Mixpanel API key (here's the setup guide)"
- "Please approve the Google Ads budget increase to $X/day"
- "I need access to the email service provider API"
- "Please review and publish the draft at [URL] — it needs a human eye"
- "The SSL cert for domain X expires in 7 days — please renew"

### 4. Execute (automated, after approval)

Approved actions flow into the existing task pipeline. The agent:
- Creates tasks (code, content, config changes)
- Runs them through the worktree pipeline (for code)
- Calls external APIs (for content publishing, email sends, etc.)
- Logs all actions with timestamps for next-day review

### 5. Human Review (once per day, ~15 min)

The human receives a structured daily digest:

```
📊 Daily Briefing — Project "Acme Growth"
──────────────────────────────────────────
KPIs:
  Weekly traffic:  4,200 / 5,000 target  (↑ 8% from last week)
  Signup rate:     2.1% / 3.0% target    (↔ flat)
  MRR:            $1,840 / $3,000 target (↑ 12%)

Yesterday's actions:
  ✅ Published blog post "Getting Started with X" (SEO: keyword Y)
  ✅ Fixed mobile layout bug on pricing page
  ✅ Deployed email capture widget to homepage

What worked:
  Blog post from 3 days ago driving 340 visits/day from organic search.
  Email capture converting at 4.2% — above benchmark.

What didn't:
  Social media posts getting low engagement. May need different approach.

Proposals (need your approval):
  1. Create comparison landing page "X vs Y" — high-intent keyword, est. 200 visits/mo
  2. Set up automated drip email sequence (3 emails) for new signups
  3. A/B test pricing page headline

I need from you:
  1. Mailchimp API key (so I can send the drip sequence)
  2. Review draft blog post at /drafts/seo-guide.md before I publish

Reply with approvals, rejections, or new direction.
```

The human responds in natural language. The agent parses approvals, rejections, feedback, and new directives.

## Execution Domains

The current system only operates in **code** (git worktrees, PRs). Autonomous objectives require expanding to:

| Domain | Actions | Tooling |
|--------|---------|---------|
| Code | Write features, fix bugs, optimize performance | Git, Cursor CLI (existing) |
| Content | Blog posts, landing pages, documentation | Markdown → CMS API, or git-based content |
| SEO | Keyword research, meta tags, structured data, internal linking | Search Console API, programmatic on-page changes |
| Email | Campaigns, drip sequences, transactional emails | Mailchimp / SendGrid / Resend API |
| Analytics | Configure events, funnels, dashboards | Mixpanel / GA4 API |
| Social | Schedule posts, engage with mentions | Buffer / native platform APIs |
| Ads | Create campaigns, adjust bids, pause underperformers | Google Ads / Meta Ads API |
| CRM | Update lead status, trigger workflows | HubSpot / Salesforce API |

Each domain is a **capability module** the agent can call. New domains are added by implementing a standard interface (execute action, check result, report status).

## Data Model Sketch

### Objective (extends current Project)

```yaml
id: obj_abc123
title: "Grow Acme to 5,000 weekly visitors"
spec: |
  Acme is a SaaS product for ...
  Target audience: ...
  Current state: ...
status: active  # active | paused | completed | abandoned
priority: high

# KPIs — the core addition
kpis:
  - id: weekly_traffic
    label: "Weekly unique visitors"
    target: 5000
    current: 4200
    source: google_analytics
    query: "ga:users, dateRange: 7d"
    direction: up        # up | down | maintain
    deadline: 2026-06-01

  - id: signup_rate
    label: "Visitor → signup conversion"
    target: 0.03
    current: 0.021
    source: mixpanel
    query: "funnel:visit_to_signup, window:7d"
    direction: up

  - id: mrr
    label: "Monthly recurring revenue"
    target: 3000
    current: 1840
    source: stripe
    query: "mrr"
    direction: up

# Data source credentials (references to secrets, not inline)
data_sources:
  google_analytics:
    type: google_analytics
    credentials_ref: ssm:/agent/ga/service-account-key
    property_id: "123456789"
  mixpanel:
    type: mixpanel
    credentials_ref: ssm:/agent/mixpanel/api-secret
    project_id: "987654"
  stripe:
    type: stripe
    credentials_ref: ssm:/agent/stripe/restricted-key

# Risk categories — what can the agent do without asking
auto_approve:
  - code_changes        # write code, open PRs
  - content_drafts      # write content (but flag for review before publish)
  - seo_on_page         # meta tags, structured data, internal links
  - analytics_config    # set up events and funnels

requires_approval:
  - content_publish     # actually publishing content
  - email_campaigns     # sending emails to real people
  - ad_spend            # anything that costs money
  - infrastructure      # DNS, hosting, domain changes
  - external_api_calls  # posting to social media, etc.

# Human action queue (now tasks with assignee: human)
# human_requests removed — agent creates assignee:human tasks directly

# Strategy log — what's been tried and what happened
strategy_history: []     # append-only log of strategies, actions, and outcomes
```

### Daily Snapshot

```yaml
objective_id: obj_abc123
date: 2026-03-20
kpi_readings:
  weekly_traffic: 4200
  signup_rate: 0.021
  mrr: 1840
actions_taken:
  - type: content
    description: "Published blog post: Getting Started with X"
    task_id: task_456
  - type: code
    description: "Fixed mobile pricing page layout"
    task_id: task_457
    pr_url: https://github.com/org/repo/pull/89
reflection: |
  Traffic up 8% week-over-week, primarily from organic search.
  Blog post from March 17 is the top driver (340 visits/day).
  Signup rate flat — suggests traffic quality is good but conversion
  path needs work. Recommend A/B testing pricing page headline.
proposals:
  - id: prop_001
    risk: requires_approval
    action: "Create comparison landing page: Acme vs Competitor"
    rationale: "High-intent keyword, 1,200 monthly searches, difficulty 34"
    status: pending  # pending | approved | rejected
  - id: prop_002
    risk: auto_approve
    action: "Optimize blog post titles for CTR based on Search Console data"
    status: approved
# Human tasks are now regular tasks with assignee: "human"
# Example: agent creates task "Set up GA4 property" with assignee: human
# Human marks it in_review when done, daily cycle reviews and completes or sends back
    context: "Needed for automated drip sequence (proposal from March 18)"
```

## Evolution Path

### Phase 1 — Observe (build next)

Add data source integrations and KPI tracking to projects. Display metrics alongside task history. Agent doesn't act on metrics yet — just surfaces them in the daily digest.

**What to build:**
- Data source abstraction (fetch metrics from GA, Mixpanel, Stripe, etc.)
- KPI schema on projects (targets, current values, direction)
- Daily snapshot storage (DynamoDB or S3)
- Enhanced digest Lambda that includes metric readings
- Metric history chart in the project detail UI

**Human role:** Still writes all directives. Now has data context when deciding what to direct.

### Phase 2 — Suggest (add strategy reasoning)

The agent analyzes metric trends and proposes strategies/directives. Human still approves everything.

**What to build:**
- Strategy reasoning prompt (given objective, KPIs, history → propose actions)
- Proposal queue in the UI (approve/reject/edit each proposal)
- "Suggested directive" flow — agent drafts, human approves, system executes
- Reflection/learning log — what worked, what didn't, why

**Human role:** Reviews daily proposals (~15 min). Approves, rejects, or redirects. Still the strategic decision-maker, but offloads the analytical work.

### Phase 3 — Expand execution domains

Move beyond code. Agent can create content, manage SEO, configure analytics events, draft emails.

**What to build:**
- Capability modules for each domain (content, SEO, email, social, ads)
- Action type routing (code → worktree pipeline, content → CMS API, email → ESP API)
- Risk classification per action type
- Auto-approve vs. requires-approval routing

**Human role:** Sets up tooling access (API keys, accounts). Reviews higher-risk actions. Handles the few things that truly need a human (vendor negotiations, legal review, physical tasks).

### Phase 4 — Autonomous daily cycles

The agent runs the full observe → reflect → propose → execute loop daily. Low-risk actions auto-execute. High-risk actions queue for the once-daily human review.

**What to build:**
- Scheduled daily cycle orchestrator (replaces manual directives)
- Auto-execution for pre-approved action categories
- Escalation rules (e.g., "if spending > $X, always ask")
- Performance-based trust adjustment (as strategies succeed, expand auto-approve scope)

**Human role:** ~15 min/day reviewing the briefing. Intervenes only for strategic pivots, high-risk approvals, and fulfilling agent requests (API keys, account access, etc.). Can go fully hands-off for days if the agent is performing well.

## Open Questions (some resolved)

1. **Trust calibration** — Deferred. All proposals require human approval initially. No auto-approve until proposal quality is validated over multiple cycles.

2. **Multi-objective conflicts** — Resolved: separate daily cycles per active project. Users can pause projects to skip them. Cross-project prioritization is a future concern.

3. **Cost tracking** — Open. Actions have costs (API calls, ad spend, compute time). Should the agent have a daily/weekly budget it manages autonomously?

4. **Rollback** — Open. If a strategy makes metrics worse, how aggressively should the agent revert? Automatic rollback for code is easy (revert PR); rolling back a published blog post or sent email is not.

5. **Multi-agent** — Open. Should different domains have specialized agents (a "content agent," an "SEO agent," a "growth engineer agent") coordinated by a planner, or one generalist agent?

6. **Evaluation** — Open. How do we measure whether the autonomous system is actually better than a human doing the same work? Need a clear before/after comparison framework.

7. **Credential security** — Resolved for Phase 1: SSM Parameter Store SecureStrings for GA4 service account keys and Search Console OAuth2 refresh tokens. Lambda gets `ssm:GetParameter` on `/agent/*` prefix.

8. **Task assignees and human task visibility** — Resolved. Added `assignee` field to tasks (enum: `"agent"` | `"human"`, default `"agent"`). REQ records removed entirely. The daily cycle now creates `assignee: "human"` tasks directly for things the agent needs from the operator (API keys, account setup, decisions). Human marks their tasks `in_review` when done; the daily cycle agent reviews them and either marks `completed` or sends back to `pending` with a comment. UI shows "My Tasks" filter in sidebar (desktop) and bottom nav (mobile), plus a "Your Tasks" section on ProjectDetail.

## Key Design Decisions

Resolved during planning — see implementation plan for full rationale.

1. **Metrics Lambda is TypeScript** — consistent with all other Lambdas in `infra/packages/`
2. **Reflection runs on EC2** — needs Cursor CLI for LLM calls; triggered via SSM from metrics Lambda
3. **Search Console needs OAuth2 refresh tokens** — unlike GA4 which uses service account keys
4. **All proposals require human approval initially** — no auto-approve until proposal quality is validated
5. **Content = code tasks** — writing MDX/content is a regular code task with `target_repo`
6. **Start with zero-auth metrics** — PageSpeed Insights (free, public) + GitHub (token already exists) before GA4/GSC
7. **Assignee replaces REQ records** — unified system where human tasks and agent tasks share the same schema; daily cycle creates `assignee: "human"` tasks and reviews them when human marks `in_review`

## Non-Goals (for now)

- Real-time decision making (reacting to events within minutes) — daily cadence is sufficient to start
- Multi-tenant / multi-user — single operator managing a few objectives
- Fully autonomous with zero human oversight — the once-daily check-in is a feature, not a limitation
- Replacing domain experts — the agent surfaces recommendations; the human applies judgment

## Relationship to Current Architecture

This builds on the existing project/directive model:

| Current | Autonomous |
|---------|------------|
| Project with spec | Objective with spec + KPIs + data sources |
| Human writes directive | Agent proposes directive, human approves |
| Tasks are always code | Tasks span code, content, email, SEO, etc. |
| Pipeline: plan → code → PR | Pipeline: observe → reflect → propose → execute → observe |
| Digest Lambda: task summary | Digest Lambda: KPI briefing + proposals + action requests |
| Manual trigger (POST directive) | Scheduled daily cycle (with human approval gate) |

The existing infra (DynamoDB, Lambda, EC2 runners, Discord, web UI) carries forward. The main additions are: data source integrations, strategy reasoning, expanded execution domains, and the daily autonomous cycle orchestrator.
