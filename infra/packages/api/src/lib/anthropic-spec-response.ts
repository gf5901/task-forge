const MAX_TOKENS_SUFFIX =
  "\n\n_(Generation hit the output limit — try a narrower prompt or shorter existing spec.)_";

/**
 * Turn parsed JSON from Bedrock InvokeModel (Claude Messages) into final markdown spec.
 * @throws Error if structure is unusable
 */
export function specTextFromAnthropicResponseJson(raw: unknown): string {
  if (!raw || typeof raw !== "object") {
    throw new Error("model returned no text");
  }
  const r = raw as {
    content?: Array<{ type?: string; text?: string }>;
    stop_reason?: string;
  };

  const text = (r.content ?? [])
    .filter((b) => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text as string)
    .join("")
    .trim();

  if (!text) {
    throw new Error("model returned no text");
  }

  if (r.stop_reason === "max_tokens") {
    return text + MAX_TOKENS_SUFFIX;
  }

  return text;
}
