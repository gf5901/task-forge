/// <reference path="./.sst/platform/config.d.ts" />

export default $config({
  app(input) {
    return {
      name: "task-forge",
      removal: input?.stage === "production" ? "retain" : "remove",
      home: "aws",
      providers: {
        aws: {
          region: "us-west-2",
        },
      },
    };
  },
  async run() {
    const tablePhysicalName = process.env.DYNAMO_TABLE_NAME ?? "agent-tasks";
    const sstApiDomain = process.env.SST_API_DOMAIN;
    const sstDnsZoneId = process.env.SST_DNS_ZONE_ID;
    const sstAcmCertArn = process.env.SST_ACM_CERT_ARN;
    const sstUiOrigin = process.env.SST_UI_ORIGIN ?? "";
    const sstEc2HealthUrl = process.env.SST_EC2_HEALTH_URL ?? "";
    const sstDigestUiUrl = process.env.SST_DIGEST_UI_URL ?? sstUiOrigin;
    const githubOwner = process.env.SST_GITHUB_OWNER ?? "";
    const knownRepos = process.env.KNOWN_REPOS ?? "";
    const scanRepos = process.env.SST_SCAN_REPOS ?? "";

    if (!sstApiDomain || !sstDnsZoneId || !sstAcmCertArn) {
      throw new Error(
        "Set SST_API_DOMAIN, SST_DNS_ZONE_ID, and SST_ACM_CERT_ARN before sst deploy (see docs/infra-deploy.md)"
      );
    }
    if (!sstUiOrigin) {
      throw new Error("Set SST_UI_ORIGIN before sst deploy (see docs/infra-deploy.md)");
    }
    if (!sstEc2HealthUrl) {
      throw new Error("Set SST_EC2_HEALTH_URL before sst deploy (see docs/infra-deploy.md)");
    }

    // -- Secrets --
    const discordWebhookUrl = new sst.Secret("DiscordWebhookUrl");
    const authSecretKey = new sst.Secret("AuthSecretKey");
    const authEmail = new sst.Secret("AuthEmail");
    const authPassword = new sst.Secret("AuthPassword");
    const ec2InstanceId = new sst.Secret("Ec2InstanceId");
    const gitHubToken = new sst.Secret("GitHubToken");

    // -- DynamoDB --
    const table = new sst.aws.Dynamo("Tasks", {
      fields: {
        pk: "string",
        sk: "string",
        status: "string",
        priority_sort_created: "string",
        target_repo: "string",
        parent_id: "string",
        pr_url: "string",
        project_id: "string",
        proj_status: "string",
        project_updated: "string",
      },
      primaryIndex: { hashKey: "pk", rangeKey: "sk" },
      globalIndexes: {
        "status-index": {
          hashKey: "status",
          rangeKey: "priority_sort_created",
        },
        "repo-index": {
          hashKey: "target_repo",
          rangeKey: "priority_sort_created",
        },
        "parent-index": {
          hashKey: "parent_id",
          rangeKey: "priority_sort_created",
        },
        "pr-index": {
          hashKey: "pr_url",
          rangeKey: "pk",
        },
        "project-index": {
          hashKey: "project_id",
          rangeKey: "priority_sort_created",
        },
        "project-list-index": {
          hashKey: "proj_status",
          rangeKey: "project_updated",
        },
      },
      pointInTimeRecovery: true,
      transform: {
        table: {
          billingMode: "PAY_PER_REQUEST",
          name: tablePhysicalName,
          ttl: { attributeName: "ttl" },
        },
      },
    });

    // -- Watchdog Lambda --
    new sst.aws.Cron("Watchdog", {
      schedule: "rate(5 minutes)",
      job: {
        handler: "packages/watchdog/src/index.handler",
        runtime: "nodejs22.x",
        timeout: "30 seconds",
        memory: "128 MB",
        link: [discordWebhookUrl],
        environment: {
          HEALTH_URL: sstEc2HealthUrl,
          DISK_WARN_PCT: "20",
          DISK_CRIT_PCT: "10",
        },
      },
    });

    new sst.aws.Cron("Digest", {
      schedule: "cron(0 14 * * ? *)",
      job: {
        handler: "packages/digest/src/index.handler",
        runtime: "nodejs22.x",
        timeout: "60 seconds",
        memory: "256 MB",
        link: [table, discordWebhookUrl],
        environment: {
          DYNAMO_TABLE: tablePhysicalName,
          HEALTH_URL: sstEc2HealthUrl,
          UI_URL: sstDigestUiUrl,
        },
      },
    });

    new sst.aws.Cron("Metrics", {
      schedule: "cron(0 6 * * ? *)",
      job: {
        handler: "packages/metrics/src/index.handler",
        runtime: "nodejs22.x",
        timeout: "120 seconds",
        memory: "256 MB",
        link: [table, gitHubToken, ec2InstanceId],
        permissions: [
          {
            actions: ["ssm:SendCommand"],
            resources: ["*"],
          },
        ],
        environment: {
          DYNAMO_TABLE: tablePhysicalName,
          GITHUB_OWNER: githubOwner,
        },
      },
    });

    /** Autopilot: hourly tick (continuous); daily-mode projects run only at 07 UTC in Lambda filter */
    new sst.aws.Cron("AutopilotPlan", {
      schedule: "cron(0 * * * ? *)",
      job: {
        handler: "packages/autopilot/src/index.handler",
        runtime: "nodejs22.x",
        timeout: "120 seconds",
        memory: "256 MB",
        link: [table, ec2InstanceId],
        permissions: [
          {
            actions: ["ssm:SendCommand"],
            resources: ["*"],
          },
        ],
        environment: {
          DYNAMO_TABLE: tablePhysicalName,
        },
      },
    });

    // -- Repo Scanner Lambda --
    new sst.aws.Cron("RepoScanner", {
      schedule: "rate(1 hour)",
      job: {
        handler: "packages/repo-scanner/src/index.handler",
        runtime: "nodejs22.x",
        timeout: "60 seconds",
        memory: "128 MB",
        link: [table, gitHubToken, ec2InstanceId],
        environment: {
          DYNAMO_TABLE: tablePhysicalName,
          GITHUB_OWNER: githubOwner,
          SCAN_REPOS: scanRepos,
          ISSUE_LABEL: "agent",
          SCAN_CI: "true",
        },
      },
    });

    // -- API Lambda (Hono) --
    const api = new sst.aws.ApiGatewayV2("Api", {
      domain: {
        name: sstApiDomain,
        dns: sst.aws.dns({ zone: sstDnsZoneId }),
        cert: sstAcmCertArn,
      },
      cors: {
        allowOrigins: [sstUiOrigin],
        allowMethods: ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allowHeaders: ["Content-Type", "Authorization", "X-Spawned-By-Task"],
        allowCredentials: true,
      },
    });

    api.route("$default", {
      handler: "packages/api/src/index.handler",
      runtime: "nodejs22.x",
      timeout: "30 seconds",
      memory: "256 MB",
      link: [table, authSecretKey, authEmail, authPassword, ec2InstanceId],
      permissions: [
        {
          actions: ["ssm:SendCommand"],
          resources: ["*"],
        },
        {
          actions: ["bedrock:InvokeModel"],
          resources: ["*"],
        },
      ],
      environment: {
        DYNAMO_TABLE: tablePhysicalName,
        CORS_ORIGINS: sstUiOrigin,
        HEALTH_URL: sstEc2HealthUrl,
        KNOWN_REPOS: knownRepos,
        BUDGET_DAILY_USD: process.env.BUDGET_DAILY_USD ?? "0",
        // Optional: override Bedrock model for POST /projects/generate-spec
        BEDROCK_SPEC_MODEL_ID: process.env.BEDROCK_SPEC_MODEL_ID ?? "",
      },
    });

    return {
      tableArn: table.arn,
      tableName: table.name,
      apiUrl: api.url,
    };
  },
});
