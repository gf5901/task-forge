import { Hono } from "hono";

export const health = new Hono();

const HEALTH_URL = process.env.HEALTH_URL ?? "";

// GET /health — proxies to the EC2 health endpoint so the frontend
// can call the Lambda API for health without CORS to the EC2 directly.
health.get("/health", async (c) => {
  if (!HEALTH_URL.trim()) {
    return c.json(
      {
        status: "error",
        error: "HEALTH_URL is not configured on the Lambda API",
      },
      503
    );
  }
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    const resp = await fetch(HEALTH_URL, { signal: controller.signal });
    clearTimeout(timeout);

    if (!resp.ok) {
      return c.json(
        { status: "unhealthy", error: `EC2 returned ${resp.status}` },
        502
      );
    }

    const data = await resp.json();
    return c.json(data);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return c.json({ status: "unreachable", error: message }, 502);
  }
});
