/** Input limits for POST /projects/generate-spec (matches Bedrock prompt sizing). */

export const MAX_SPEC_PROMPT_CHARS = 8000;
export const MAX_SPEC_EXISTING_CHARS = 100_000;

/**
 * @throws Error with message suitable for HTTP 400 when invalid
 */
export function validateGenerateSpecInput(
  prompt: string,
  existingSpec?: string
): void {
  const p = prompt.trim();
  if (!p) {
    throw new Error("prompt is required");
  }
  if (p.length > MAX_SPEC_PROMPT_CHARS) {
    throw new Error(`prompt too long (max ${MAX_SPEC_PROMPT_CHARS} characters)`);
  }

  const existing = (existingSpec ?? "").trim();
  if (existing.length > MAX_SPEC_EXISTING_CHARS) {
    throw new Error(
      `existing_spec too long (max ${MAX_SPEC_EXISTING_CHARS} characters)`
    );
  }
}

/** User message body for Claude (existing spec + instructions, or prompt only). */
export function buildSpecUserMessage(prompt: string, existingSpec?: string): string {
  const p = prompt.trim();
  const existing = (existingSpec ?? "").trim();
  if (existing) {
    return `## Existing spec\n\n${existing}\n\n## Instructions\n\n${p}`;
  }
  return p;
}
