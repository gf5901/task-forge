import { Hono } from "hono";
import * as db from "../lib/dynamo.js";
import { generateSpecFromPrompt } from "../lib/generate-spec-bedrock.js";
import { generateKPIs } from "../lib/generate-kpis-bedrock.js";
import {
  triggerDirectiveDecomposition,
  triggerProposePlan,
  RunnerUnavailableError,
} from "../lib/ssm.js";
import type { PlanItem, ProjectStatus, TaskPriority } from "../lib/types.js";

const PROJECT_STATUSES = new Set(["active", "paused", "completed"]);

export const projects = new Hono();

// GET /projects
projects.get("/projects", async (c) => {
  const status = c.req.query("status") as ProjectStatus | undefined;
  if (status && !PROJECT_STATUSES.has(status)) {
    return c.json({ error: "invalid status" }, 400);
  }
  const list = await db.listProjects(status);
  const enriched = await Promise.all(
    list.map(async (p) => {
      const tasks = await db.listTasksByProject(p.id);
      const done = tasks.filter((t) =>
        ["completed", "in_review"].includes(t.status)
      ).length;
      const directives = await db.listDirectives(p.id);
      const lastDir =
        directives.length > 0
          ? directives[directives.length - 1].created_at
          : null;
      return {
        ...p,
        task_total: tasks.length,
        task_done: done,
        last_directive_at: lastDir,
      };
    })
  );
  return c.json({ projects: enriched });
});

// GET /projects/:id
projects.get("/projects/:id", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const [directives, projectTasks] = await Promise.all([
    db.listDirectives(p.id),
    db.listTasksByProject(p.id),
  ]);
  const done = projectTasks.filter((t) =>
    ["completed", "in_review"].includes(t.status)
  ).length;
  return c.json({
    project: p,
    directives,
    tasks: projectTasks,
    progress: {
      total: projectTasks.length,
      done,
    },
  });
});

// GET /projects/:id/chat
projects.get("/projects/:id/chat", async (c) => {
  const limitRaw = c.req.query("limit");
  const limit = Math.min(
    100,
    Math.max(1, parseInt(limitRaw ?? "50", 10) || 50)
  );
  const data = await db.getProjectChat(c.req.param("id"), limit);
  if (!data) return c.json({ error: "not found" }, 404);
  return c.json({
    messages: data.messages,
    reply_pending: data.reply_pending,
  });
});

// POST /projects/:id/chat
projects.post("/projects/:id/chat", async (c) => {
  const body = await c.req.json().catch(() => ({}));
  const text = typeof body.body === "string" ? body.body : "";
  if (!text.trim()) return c.json({ error: "body is required" }, 400);
  const author =
    typeof body.author === "string" && body.author.trim()
      ? body.author.trim()
      : "web";
  const requestPmReply = body.request_pm_reply !== false;
  const result = await db.addProjectChatMessage(
    c.req.param("id"),
    author,
    text,
    requestPmReply
  );
  if (!result) return c.json({ error: "not found" }, 404);
  return c.json({
    message: result.message,
    reply_pending: result.reply_pending,
  });
});

// GET /projects/:id/directives
projects.get("/projects/:id/directives", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const directives = await db.listDirectives(p.id);
  return c.json({ directives });
});

// POST /projects
projects.post("/projects", async (c) => {
  const body = await c.req.json();
  if (!body.title?.trim()) return c.json({ error: "title is required" }, 400);
  const priority = (body.priority ?? "medium") as TaskPriority;
  if (!["low", "medium", "high", "urgent"].includes(priority)) {
    return c.json({ error: "invalid priority" }, 400);
  }
  const status = (body.status ?? "active") as ProjectStatus;
  if (!PROJECT_STATUSES.has(status)) {
    return c.json({ error: "invalid status" }, 400);
  }
  const autopilotMode =
    body.autopilot_mode === "continuous" ? "continuous" : "daily";
  const project = await db.createProject({
    title: body.title.trim(),
    spec: (body.spec ?? "").trim(),
    priority,
    target_repo: (body.target_repo ?? "").trim(),
    status,
    kpis: Array.isArray(body.kpis) ? body.kpis : [],
    autopilot: Boolean(body.autopilot),
    autopilot_mode: autopilotMode,
  });
  return c.json(project);
});

// POST /projects/generate-spec — AI draft/refine project spec (Bedrock)
projects.post("/projects/generate-spec", async (c) => {
  const body = await c.req.json().catch(() => ({}));
  const prompt = typeof body.prompt === "string" ? body.prompt : "";
  const existingSpec =
    typeof body.existing_spec === "string" ? body.existing_spec : undefined;
  if (!prompt.trim()) {
    return c.json({ error: "prompt is required" }, 400);
  }
  try {
    const spec = await generateSpecFromPrompt({ prompt, existingSpec });
    return c.json({ spec });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "generation failed";
    if (msg.includes("required") || msg.includes("too long")) {
      return c.json({ error: msg }, 400);
    }
    console.error("[generate-spec]", e);
    return c.json({ error: msg }, 502);
  }
});

// POST /projects/:id/generate-kpis — AI-suggest KPIs for a project (Bedrock)
projects.post("/projects/:id/generate-kpis", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  try {
    const suggested = await generateKPIs({
      title: p.title,
      spec: p.spec,
      existingKpis: p.kpis,
    });
    return c.json({ kpis: suggested });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "generation failed";
    console.error("[generate-kpis]", e);
    return c.json({ error: msg }, 502);
  }
});

// PATCH /projects/:id
projects.patch("/projects/:id", async (c) => {
  const body = await c.req.json();
  const updates: Parameters<typeof db.updateProject>[1] = {};
  if (body.title !== undefined) updates.title = body.title;
  if (body.spec !== undefined) updates.spec = body.spec;
  if (body.status !== undefined) {
    if (!PROJECT_STATUSES.has(body.status)) {
      return c.json({ error: "invalid status" }, 400);
    }
    updates.status = body.status;
  }
  if (body.priority !== undefined) {
    if (!["low", "medium", "high", "urgent"].includes(body.priority)) {
      return c.json({ error: "invalid priority" }, 400);
    }
    updates.priority = body.priority;
  }
  if (body.target_repo !== undefined) updates.target_repo = body.target_repo;
  if (Array.isArray(body.kpis)) updates.kpis = body.kpis;
  if (body.autopilot !== undefined) updates.autopilot = Boolean(body.autopilot);
  if (body.autopilot_mode !== undefined) {
    updates.autopilot_mode =
      body.autopilot_mode === "continuous" ? "continuous" : "daily";
    if (body.autopilot_mode !== "continuous") {
      updates.cycle_started_at = "";
      updates.cycle_paused = false;
      updates.cycle_pause_reason = "";
      updates.next_check_at = "";
    }
  }
  if (body.cycle_max_hours !== undefined) {
    const n = Number(body.cycle_max_hours);
    if (Number.isFinite(n) && n > 0) updates.cycle_max_hours = Math.floor(n);
  }

  const p = await db.updateProject(c.req.param("id"), updates);
  if (!p) return c.json({ error: "not found" }, 404);
  return c.json(p);
});

// DELETE /projects/:id
projects.delete("/projects/:id", async (c) => {
  const id = c.req.param("id");
  const p = await db.getProject(id);
  if (!p) return c.json({ error: "not found" }, 404);
  const tasks = await db.listTasksByProject(id);
  for (const t of tasks) {
    await db.deleteTask(t.id);
  }
  await db.deleteProject(id);
  return c.json({ ok: true });
});

// POST /projects/:id/directive
projects.post("/projects/:id/directive", async (c) => {
  const body = await c.req.json();
  const content = (body.content ?? "").trim();
  if (!content) return c.json({ error: "content is required" }, 400);

  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  if (p.status !== "active") {
    return c.json({ error: "project is not active" }, 400);
  }

  await db.cancelPendingPlanTasks(p.id);

  const { directive } = await db.addDirective(p.id, "user", content);
  await db.updateProject(p.id, {
    awaiting_next_directive: false,
    active_directive_sk: directive.sk,
  });

  await triggerDirectiveDecomposition(p.id, directive.sk);
  return c.json({ ok: true, directive });
});

// POST /projects/:id/directive/dismiss — clear stuck directive so autopilot can proceed
projects.post("/projects/:id/directive/dismiss", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  if (!p.active_directive_sk) {
    return c.json({ error: "no active directive to dismiss" }, 400);
  }
  await db.updateProject(p.id, {
    awaiting_next_directive: true,
  });
  return c.json({ ok: true });
});

// ---------------------------------------------------------------------------
// Project docs (DOC#<slug>)
// ---------------------------------------------------------------------------

// GET /projects/:id/docs
projects.get("/projects/:id/docs", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const docs = await db.listProjectDocs(p.id);
  return c.json({ docs });
});

// GET /projects/:id/docs/:slug
projects.get("/projects/:id/docs/:slug", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const slug = c.req.param("slug");
  if (!db.isValidDocSlug(slug)) {
    return c.json({ error: "invalid slug (lowercase alphanumeric, hyphens, underscores)" }, 400);
  }
  const doc = await db.getProjectDoc(p.id, slug);
  if (!doc) return c.json({ error: "doc not found" }, 404);
  return c.json({ doc });
});

// PUT /projects/:id/docs/:slug — create or update
projects.put("/projects/:id/docs/:slug", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const slug = c.req.param("slug");
  if (!db.isValidDocSlug(slug)) {
    return c.json({ error: "invalid slug (lowercase alphanumeric, hyphens, underscores)" }, 400);
  }
  const body = await c.req.json().catch(() => ({}));
  const title = typeof body.title === "string" ? body.title.trim() : "";
  const content = typeof body.content === "string" ? body.content : "";
  if (!title) {
    return c.json({ error: "title is required" }, 400);
  }
  const doc = await db.putProjectDoc(p.id, slug, title, content);
  return c.json({ doc });
});

// DELETE /projects/:id/docs/:slug
projects.delete("/projects/:id/docs/:slug", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const slug = c.req.param("slug");
  if (!db.isValidDocSlug(slug)) {
    return c.json({ error: "invalid slug" }, 400);
  }
  const deleted = await db.deleteProjectDoc(p.id, slug);
  if (!deleted) return c.json({ error: "doc not found" }, 404);
  return c.json({ ok: true });
});

// ---------------------------------------------------------------------------
// Snapshots
// ---------------------------------------------------------------------------

// GET /projects/:id/snapshots?days=14
projects.get("/projects/:id/snapshots", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const days = Math.min(Number(c.req.query("days") ?? 14), 90);
  const snapshots = await db.listSnapshots(p.id, days);
  return c.json({ snapshots });
});

// ---------------------------------------------------------------------------
// Proposals
// ---------------------------------------------------------------------------

// GET /projects/:id/proposals?status=pending
projects.get("/projects/:id/proposals", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const status = c.req.query("status") || undefined;
  const proposals = await db.listProposals(p.id, status);
  return c.json({ proposals });
});

// PATCH /projects/:id/proposals/:propSk — approve or reject
projects.patch("/projects/:id/proposals/:propSk", async (c) => {
  const projectId = c.req.param("id");
  const propSk = decodeURIComponent(c.req.param("propSk"));
  const p = await db.getProject(projectId);
  if (!p) return c.json({ error: "not found" }, 404);

  const body = await c.req.json();
  const status = body.status as string;
  if (!status || !["approved", "rejected"].includes(status)) {
    return c.json({ error: "status must be 'approved' or 'rejected'" }, 400);
  }

  if (status === "approved") {
    // Fetch the proposal to get its content for the task
    const proposals = await db.listProposals(projectId);
    const prop = proposals.find((pr) => pr.sk === propSk);
    if (!prop) return c.json({ error: "proposal not found" }, 404);

    const task = await db.createTask({
      title: prop.action.slice(0, 120),
      description: `${prop.action}\n\n**Rationale:** ${prop.rationale}`,
      priority: p.priority,
      target_repo: p.target_repo,
      project_id: p.id,
    });
    await db.updateProposalStatus(projectId, propSk, "approved", {
      task_id: task.id,
    });
    return c.json({ ok: true, task_id: task.id });
  }

  // Rejected
  await db.updateProposalStatus(projectId, propSk, "rejected", {
    feedback: (body.feedback ?? "").trim() || undefined,
  });
  return c.json({ ok: true });
});

// ---------------------------------------------------------------------------
// Autopilot plans (PLAN#YYYY-MM-DD or PLAN#YYYY-MM-DDTHH:MM:SS)
// ---------------------------------------------------------------------------

// GET /projects/:id/plans
projects.get("/projects/:id/plans", async (c) => {
  const p = await db.getProject(c.req.param("id"));
  if (!p) return c.json({ error: "not found" }, 404);
  const limit = Math.min(Number(c.req.query("limit") ?? 14), 90);
  const plans = await db.listPlans(p.id, limit);
  return c.json({ plans });
});

// GET /projects/:id/plans/:planId — planId = YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS (UTC)
projects.get("/projects/:id/plans/:planId", async (c) => {
  const projectId = c.req.param("id");
  const planId = decodeURIComponent(c.req.param("planId"));
  if (!db.isValidPlanId(planId)) {
    return c.json({ error: "invalid plan id" }, 400);
  }
  const p = await db.getProject(projectId);
  if (!p) return c.json({ error: "not found" }, 404);
  const plan = await db.getPlan(projectId, planId);
  if (!plan) return c.json({ error: "plan not found" }, 404);
  const tasks = await Promise.all(
    plan.task_ids.map((tid) => db.getTask(tid)),
  );
  return c.json({
    plan,
    tasks: tasks.filter((t): t is NonNullable<typeof t> => t != null),
  });
});

// PATCH /projects/:id/plans/:planId — edit items before approve
projects.patch("/projects/:id/plans/:planId", async (c) => {
  const projectId = c.req.param("id");
  const planId = decodeURIComponent(c.req.param("planId"));
  if (!db.isValidPlanId(planId)) {
    return c.json({ error: "invalid plan id" }, 400);
  }
  const p = await db.getProject(projectId);
  if (!p) return c.json({ error: "not found" }, 404);
  const body = await c.req.json().catch(() => ({}));
  const rawItems = body.items;
  if (!Array.isArray(rawItems) || rawItems.length === 0) {
    return c.json({ error: "items array required" }, 400);
  }
  const items: PlanItem[] = [];
  for (const it of rawItems) {
    if (!it || typeof it.title !== "string" || !it.title.trim()) {
      return c.json({ error: "each item needs a non-empty title" }, 400);
    }
    let pr: TaskPriority = "medium";
    if (
      typeof it.priority === "string" &&
      ["low", "medium", "high", "urgent"].includes(it.priority)
    ) {
      pr = it.priority as TaskPriority;
    }
    items.push({
      title: it.title.trim().slice(0, 500),
      description: typeof it.description === "string" ? it.description : "",
      role: typeof it.role === "string" ? it.role.trim() : "",
      priority: pr,
    });
  }
  const reflection =
    typeof body.reflection === "string" ? body.reflection : undefined;
  const updated = await db.updatePlanItems(projectId, planId, items, reflection);
  if (!updated) {
    return c.json({ error: "plan not found or not editable" }, 400);
  }
  return c.json({ plan: updated });
});

// POST /projects/:id/plans/:planId/approve
projects.post("/projects/:id/plans/:planId/approve", async (c) => {
  const projectId = c.req.param("id");
  const planId = decodeURIComponent(c.req.param("planId"));
  if (!db.isValidPlanId(planId)) {
    return c.json({ error: "invalid plan id" }, 400);
  }
  const p = await db.getProject(projectId);
  if (!p) return c.json({ error: "not found" }, 404);
  if (p.status !== "active") {
    return c.json({ error: "project is not active" }, 400);
  }
  const body = await c.req.json().catch(() => ({}));
  const humanNotes =
    typeof body.human_notes === "string" ? body.human_notes : "";
  const result = await db.approvePlanAndCreateTasks(
    projectId,
    planId,
    humanNotes,
    p,
  );
  if (!result) {
    return c.json(
      { error: "plan not found, not proposed, or has no items" },
      400,
    );
  }
  return c.json({ ok: true, plan: result.plan, task_ids: result.tasks.map((t) => t.id) });
});

// POST /projects/:id/plans/:planId/regenerate
projects.post("/projects/:id/plans/:planId/regenerate", async (c) => {
  const projectId = c.req.param("id");
  const planId = decodeURIComponent(c.req.param("planId"));
  if (!db.isValidPlanId(planId)) {
    return c.json({ error: "invalid plan id" }, 400);
  }
  const p = await db.getProject(projectId);
  if (!p) return c.json({ error: "not found" }, 404);
  if (!p.autopilot) {
    return c.json({ error: "autopilot is not enabled for this project" }, 400);
  }
  const today = new Date().toISOString().slice(0, 10);
  if (p.autopilot_mode !== "continuous" && planId !== today) {
    return c.json(
      { error: "daily mode can only regenerate today's plan (YYYY-MM-DD)" },
      400,
    );
  }
  const plan = await db.getPlan(projectId, planId);
  if (!plan || plan.status !== "proposed") {
    return c.json({ error: "no proposed plan to regenerate" }, 400);
  }
  try {
    await triggerProposePlan(projectId, true, planId);
  } catch (e) {
    if (e instanceof RunnerUnavailableError) {
      return c.json({ error: e.message }, 503);
    }
    throw e;
  }
  return c.json({ ok: true });
});

// POST /projects/:id/cycle/start — continuous autopilot only
projects.post("/projects/:id/cycle/start", async (c) => {
  const projectId = c.req.param("id");
  const p = await db.getProject(projectId);
  if (!p) return c.json({ error: "not found" }, 404);
  if (p.autopilot_mode !== "continuous") {
    return c.json({ error: "cycle controls require autopilot_mode continuous" }, 400);
  }
  if (!p.autopilot) {
    return c.json({ error: "enable autopilot first" }, 400);
  }
  if (p.status !== "active") {
    return c.json({ error: "project is not active" }, 400);
  }
  const body = await c.req.json().catch(() => ({}));
  const maxHours =
    typeof body.max_hours === "number" &&
    Number.isFinite(body.max_hours) &&
    body.max_hours > 0
      ? Math.floor(body.max_hours)
      : p.cycle_max_hours || 24;
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  await db.updateProject(projectId, {
    cycle_started_at: now,
    cycle_paused: false,
    cycle_pause_reason: "",
    cycle_max_hours: maxHours,
    next_check_at: "",
  });
  try {
    await triggerProposePlan(projectId, false, undefined);
  } catch (e) {
    if (e instanceof RunnerUnavailableError) {
      return c.json({ error: e.message }, 503);
    }
    throw e;
  }
  return c.json({ ok: true });
});

// POST /projects/:id/cycle/stop
projects.post("/projects/:id/cycle/stop", async (c) => {
  const projectId = c.req.param("id");
  const p = await db.getProject(projectId);
  if (!p) return c.json({ error: "not found" }, 404);
  if (p.autopilot_mode !== "continuous") {
    return c.json({ error: "cycle controls require autopilot_mode continuous" }, 400);
  }
  await db.updateProject(projectId, {
    cycle_paused: true,
    cycle_pause_reason: "manual",
  });
  return c.json({ ok: true });
});

// POST /projects/:id/cycle/review — optional feedback + restart
projects.post("/projects/:id/cycle/review", async (c) => {
  const projectId = c.req.param("id");
  const p = await db.getProject(projectId);
  if (!p) return c.json({ error: "not found" }, 404);
  const body = await c.req.json().catch(() => ({}));
  const feedback =
    typeof body.feedback === "string" ? body.feedback.trim() : "";
  const restart = Boolean(body.restart);
  const maxHours =
    typeof body.max_hours === "number" &&
    Number.isFinite(body.max_hours) &&
    body.max_hours > 0
      ? Math.floor(body.max_hours)
      : p.cycle_max_hours || 24;
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
  if (restart) {
    await db.updateProject(projectId, {
      cycle_feedback: feedback,
      cycle_started_at: now,
      cycle_paused: false,
      cycle_pause_reason: "",
      cycle_max_hours: maxHours,
      next_check_at: "",
    });
    try {
      await triggerProposePlan(projectId, false, undefined);
    } catch (e) {
      if (e instanceof RunnerUnavailableError) {
        return c.json({ error: e.message }, 503);
      }
      throw e;
    }
  } else {
    await db.updateProject(projectId, { cycle_feedback: feedback });
  }
  return c.json({ ok: true });
});
