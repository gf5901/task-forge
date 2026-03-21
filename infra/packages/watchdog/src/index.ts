import { Resource } from "sst";

interface HealthResponse {
  status: string;
  uptime_seconds: number;
  disk_total_bytes: number;
  disk_free_bytes: number;
  disk_free_pct: number;
  task_counts: Record<string, number>;
  last_runner_ts: string;
  last_healer_ts: string;
}

const HEALTH_URL = process.env.HEALTH_URL ?? "";
const DISK_WARN_PCT = Number(process.env.DISK_WARN_PCT ?? "20");
const DISK_CRIT_PCT = Number(process.env.DISK_CRIT_PCT ?? "10");

async function sendDiscordAlert(message: string): Promise<void> {
  const webhookUrl = (Resource as any).DiscordWebhookUrl.value;
  if (!webhookUrl) {
    console.warn("No Discord webhook URL configured — skipping alert");
    return;
  }

  const resp = await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: message }),
  });

  if (!resp.ok) {
    console.error(`Discord webhook failed: ${resp.status} ${await resp.text()}`);
  }
}

export async function handler(): Promise<void> {
  const alerts: string[] = [];

  if (!HEALTH_URL.trim()) {
    console.warn("HEALTH_URL not set — skipping watchdog health check");
    return;
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15_000);

    const resp = await fetch(HEALTH_URL, { signal: controller.signal });
    clearTimeout(timeout);

    if (!resp.ok) {
      alerts.push(
        `🔴 **Server unhealthy** — /api/health returned HTTP ${resp.status}`
      );
    } else {
      const data: HealthResponse = await resp.json();

      if (data.disk_free_pct < DISK_CRIT_PCT) {
        alerts.push(
          `🔴 **CRITICAL: Disk nearly full** — ${data.disk_free_pct}% free ` +
            `(${formatBytes(data.disk_free_bytes)} of ${formatBytes(data.disk_total_bytes)})`
        );
      } else if (data.disk_free_pct < DISK_WARN_PCT) {
        alerts.push(
          `🟡 **Low disk space** — ${data.disk_free_pct}% free ` +
            `(${formatBytes(data.disk_free_bytes)} of ${formatBytes(data.disk_total_bytes)})`
        );
      }

      const inProgress = data.task_counts?.in_progress ?? 0;
      const pending = data.task_counts?.pending ?? 0;
      console.log(
        `Health OK: uptime=${data.uptime_seconds}s, disk=${data.disk_free_pct}%, ` +
          `pending=${pending}, in_progress=${inProgress}`
      );
    }
  } catch (err: unknown) {
    const message =
      err instanceof Error ? err.message : String(err);
    alerts.push(
      `🔴 **Server unreachable** — failed to reach ${HEALTH_URL}: ${message}`
    );
  }

  if (alerts.length > 0) {
    const fullMessage = `**Agent Task Bot Watchdog**\n${alerts.join("\n")}`;
    await sendDiscordAlert(fullMessage);
    console.log(`Sent ${alerts.length} alert(s) to Discord`);
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = -1;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}
