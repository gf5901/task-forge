import { describe, expect, it } from "vitest";
import { specTextFromAnthropicResponseJson } from "./anthropic-spec-response.js";

describe("specTextFromAnthropicResponseJson", () => {
  it("joins text blocks", () => {
    const out = specTextFromAnthropicResponseJson({
      content: [{ type: "text", text: "# Hello\n" }],
      stop_reason: "end_turn",
    });
    expect(out).toBe("# Hello");
  });

  it("appends suffix when stop_reason is max_tokens", () => {
    const out = specTextFromAnthropicResponseJson({
      content: [{ type: "text", text: "partial" }],
      stop_reason: "max_tokens",
    });
    expect(out).toContain("partial");
    expect(out).toContain("output limit");
  });

  it("throws when no text blocks", () => {
    expect(() => specTextFromAnthropicResponseJson({ content: [] })).toThrow(
      "model returned no text"
    );
    expect(() =>
      specTextFromAnthropicResponseJson({
        content: [{ type: "tool_use", id: "1", name: "x", input: {} }],
      })
    ).toThrow("model returned no text");
  });

  it("throws on non-object", () => {
    expect(() => specTextFromAnthropicResponseJson(null)).toThrow(
      "model returned no text"
    );
  });
});
