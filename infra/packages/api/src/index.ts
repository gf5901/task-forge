import { Hono } from "hono";
import { cors } from "hono/cors";
import { handle } from "hono/aws-lambda";
import { tasks } from "./routes/tasks.js";
import { projects } from "./routes/projects.js";
import { auth, authMiddleware } from "./routes/auth.js";
import { health } from "./routes/health.js";
import { settings } from "./routes/settings.js";

const app = new Hono();

// ─── CORS ────────────────────────────────────────────────────────────────────

const allowedOrigins = (process.env.CORS_ORIGINS ?? "http://localhost:5173")
  .split(",")
  .map((o) => o.trim())
  .filter(Boolean);

app.use(
  "*",
  cors({
    origin: allowedOrigins,
    credentials: true,
    allowMethods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allowHeaders: ["Content-Type", "Authorization", "X-Spawned-By-Task"],
  })
);

// ─── Auth middleware (skips exempt paths) ─────────────────────────────────────

app.use("/api/*", authMiddleware);

// ─── Routes ──────────────────────────────────────────────────────────────────

app.route("/api", tasks);
app.route("/api", projects);
app.route("/api", auth);
app.route("/api", health);
app.route("/api", settings);

// Root — simple liveness check
app.get("/", (c) => c.json({ service: "task-forge-api", status: "ok" }));

export const handler = handle(app);
