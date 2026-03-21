import {
  BedrockRuntimeClient,
  InvokeModelCommand,
} from "@aws-sdk/client-bedrock-runtime";
import { specTextFromAnthropicResponseJson } from "./anthropic-spec-response.js";
import {
  buildSpecUserMessage,
  validateGenerateSpecInput,
} from "./validate-generate-spec-input.js";

const DEFAULT_MODEL =
  process.env.BEDROCK_SPEC_MODEL_ID?.trim() ||
  "us.anthropic.claude-sonnet-4-6";

const SYSTEM = `You are a product and engineering spec writer for software projects.
Given instructions, produce a clear project specification in Markdown.

Structure the spec with these sections (omit empty sections):
- Overview
- Goals
- Technical approach
- Key features
- Constraints and non-goals
- Open questions (if any)

If an "Existing spec" is provided, refine and expand it according to the user's instructions — do not discard useful content unless the user asks to replace it.

Output only the Markdown specification. No preamble, no closing remarks.`;

export async function generateSpecFromPrompt(params: {
  prompt: string;
  existingSpec?: string;
}): Promise<string> {
  validateGenerateSpecInput(params.prompt, params.existingSpec);
  const userContent = buildSpecUserMessage(params.prompt, params.existingSpec);

  const region = process.env.AWS_REGION ?? "us-west-2";
  const client = new BedrockRuntimeClient({ region });

  const body = JSON.stringify({
    anthropic_version: "bedrock-2023-05-31",
    max_tokens: 8192,
    temperature: 0.4,
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

  return specTextFromAnthropicResponseJson(raw);
}
