import { Hono } from "hono";
import * as jose from "jose";
import { Resource } from "sst";
import { checkRateLimit, recordFailedAttempt, clearRateLimit } from "../lib/rate-limit.js";

const JWT_ALG = "HS256";
const JWT_EXPIRY = "30d";

function getAuthConfig() {
  const email =
    process.env.AUTH_EMAIL || (Resource as any).AuthEmail?.value || "";
  const password =
    process.env.AUTH_PASSWORD || (Resource as any).AuthPassword?.value || "";
  const secret =
    process.env.AUTH_SECRET_KEY || (Resource as any).AuthSecretKey?.value || "";
  return { email, password, secret, enabled: Boolean(email && password) };
}

function getJwtSecret(): Uint8Array {
  return new TextEncoder().encode(getAuthConfig().secret);
}

export const auth = new Hono();

// POST /auth/login
auth.post("/auth/login", async (c) => {
  const cfg = getAuthConfig();
  if (!cfg.enabled) {
    return c.json({ ok: true, auth_enabled: false });
  }

  const ip = c.req.header("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  const retryAfter = await checkRateLimit(ip);
  if (retryAfter > 0) {
    return c.json(
      { error: "too many login attempts", retry_after_seconds: retryAfter },
      429
    );
  }

  const body = await c.req.json();
  const email = body.email ?? "";
  const password = body.password ?? "";

  const emailMatch = safeCompare(email, cfg.email);
  const passMatch = safeCompare(password, cfg.password);

  if (!emailMatch || !passMatch) {
    await recordFailedAttempt(ip);
    return c.json({ error: "invalid credentials" }, 401);
  }

  await clearRateLimit(ip);

  const token = await new jose.SignJWT({ email })
    .setProtectedHeader({ alg: JWT_ALG })
    .setIssuedAt()
    .setExpirationTime(JWT_EXPIRY)
    .sign(getJwtSecret());

  return c.json({ ok: true, token });
});

// GET /auth/me
auth.get("/auth/me", async (c) => {
  const cfg = getAuthConfig();
  if (!cfg.enabled) {
    return c.json({ authenticated: true, auth_enabled: false });
  }

  const token = extractToken(c);
  if (!token) {
    return c.json({ authenticated: false, auth_enabled: true, email: "" });
  }

  try {
    const { payload } = await jose.jwtVerify(token, getJwtSecret());
    return c.json({
      authenticated: true,
      auth_enabled: true,
      email: payload.email ?? "",
    });
  } catch {
    return c.json({ authenticated: false, auth_enabled: true, email: "" });
  }
});

// POST /auth/logout
auth.post("/auth/logout", async (c) => {
  return c.json({ ok: true });
});

// ─── Auth middleware ─────────────────────────────────────────────────────────

import type { Context, Next } from "hono";

const EXEMPT_PREFIXES = [
  "/api/auth/",
  "/api/health",
  "/webhook/",
];

export async function authMiddleware(
  c: Context,
  next: Next
): Promise<Response | void> {
  const cfg = getAuthConfig();
  if (!cfg.enabled) return next();

  const path = new URL(c.req.url).pathname;
  if (EXEMPT_PREFIXES.some((p) => path.startsWith(p))) return next();

  const token = extractToken(c);
  if (!token) {
    return c.json({ error: "unauthorized" }, 401);
  }

  try {
    await jose.jwtVerify(token, getJwtSecret());
  } catch {
    return c.json({ error: "unauthorized" }, 401);
  }

  return next();
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function extractToken(c: Context): string | null {
  const header = c.req.header("Authorization");
  if (header?.startsWith("Bearer ")) {
    return header.slice(7);
  }
  return null;
}

function safeCompare(a: string, b: string): boolean {
  const bufA = new TextEncoder().encode(a);
  const bufB = new TextEncoder().encode(b);
  const maxLen = Math.max(bufA.length, bufB.length);
  let result = bufA.length ^ bufB.length;
  for (let i = 0; i < maxLen; i++) {
    result |= (bufA[i] ?? 0) ^ (bufB[i] ?? 0);
  }
  return result === 0;
}
