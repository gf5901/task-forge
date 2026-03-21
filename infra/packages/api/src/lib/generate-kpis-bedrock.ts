import {
  BedrockRuntimeClient,
  InvokeModelCommand,
} from "@aws-sdk/client-bedrock-runtime";
import { specTextFromAnthropicResponseJson } from "./anthropic-spec-response.js";
import type { KPI } from "./types.js";

const DEFAULT_MODEL =
  process.env.BEDROCK_SPEC_MODEL_ID?.trim() ||
  "us.anthropic.claude-sonnet-4-6";

const SYSTEM = `You are a product analytics expert that defines measurable KPIs for software projects.

Given a project spec (and optionally existing KPIs), suggest concrete, measurable KPIs.

Each KPI must have ALL of these fields:
- id: snake_case identifier (e.g. "monthly_visitors", "page_load_time")
- label: human-readable name (e.g. "Monthly Visitors")
- target: numeric target value
- current: 0 (unknown until measured)
- unit: measurement unit (e.g. "visits", "ms", "%", "score")
- direction: "up" (higher is better), "down" (lower is better), or "maintain"
- source: one of "pagespeed", "github", "ga4", "gsc", "manual"

Available automated sources:
- "pagespeed" — Google PageSpeed Insights (performance score, LCP, CLS, FID, TTFB)
- "github" — GitHub repo stats (stars, issues, PRs)
- "ga4" — Google Analytics 4 (visitors, sessions, bounce rate) — requires manual setup
- "gsc" — Google Search Console (impressions, clicks, CTR) — requires manual setup
- "manual" — anything measured by hand

Prefer automated sources when applicable. Suggest 3-8 KPIs that are relevant and actionable for the project.

If existing KPIs are provided, suggest only NEW ones that aren't already covered. You may also suggest modifications to existing KPIs if their targets seem wrong, but frame modifications as new entries with the same id.

Output ONLY a JSON array of KPI objects. No markdown fences, no preamble, no explanation.`;

export async function generateKPIs(params: {
  spec: string;
  title: string;
  existingKpis?: KPI[];
}): Promise<KPI[]> {
  if (!params.spec.trim() && !params.title.trim()) {
    throw new Error("project needs a title or spec to generate KPIs");
  }

  let userContent = `Project: ${params.title}\n\nSpec:\n${params.spec || "(no spec yet)"}`;
  if (params.existingKpis && params.existingKpis.length > 0) {
    userContent += `\n\nExisting KPIs (suggest only new/changed ones):\n${JSON.stringify(params.existingKpis, null, 2)}`;
  }

  const region = process.env.AWS_REGION ?? "us-west-2";
  const client = new BedrockRuntimeClient({ region });

  const body = JSON.stringify({
    anthropic_version: "bedrock-2023-05-31",
    max_tokens: 4096,
    temperature: 0.3,
    system: SYSTEM,
    messages: [{ role: "user", content: userContent }],
  });

  const out = await client.send(
    new InvokeModelCommand({
      modelId: DEFAULT_MODEL,
      contentType: "application/json",
      accept: "application/json",
      body: new TextEncoder().encode(body),
    })
  );

  if (!out.body) {
    throw new Error("empty Bedrock response");
  }

  let raw: unknown;
  try {
    raw = JSON.parse(new TextDecoder().decode(out.body));
  } catch {
    throw new Error("invalid model response JSON");
  }

  const text = specTextFromAnthropicResponseJson(raw);

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    const start = text.indexOf("[");
    const end = text.lastIndexOf("]");
    if (start >= 0 && end > start) {
      parsed = JSON.parse(text.slice(start, end + 1));
    } else {
      throw new Error("model did not return valid JSON array");
    }
  }

  if (!Array.isArray(parsed)) {
    throw new Error("model did not return a JSON array");
  }

  const VALID_DIRECTIONS = new Set(["up", "down", "maintain"]);
  const VALID_SOURCES = new Set(["pagespeed", "github", "ga4", "gsc", "manual"]);

  return parsed
    .filter((k): k is Record<string, unknown> => k && typeof k === "object" && typeof k.id === "string")
    .map((k) => ({
      id: String(k.id).slice(0, 40),
      label: String(k.label ?? k.id).slice(0, 100),
      target: typeof k.target === "number" ? k.target : 0,
      current: typeof k.current === "number" ? k.current : 0,
      unit: String(k.unit ?? "").slice(0, 20),
      direction: VALID_DIRECTIONS.has(String(k.direction)) ? String(k.direction) as KPI["direction"] : "up",
      source: VALID_SOURCES.has(String(k.source)) ? String(k.source) : "manual",
    }));
}
