import { describe, expect, it } from "vitest";
import {
  buildSpecUserMessage,
  MAX_SPEC_EXISTING_CHARS,
  MAX_SPEC_PROMPT_CHARS,
  validateGenerateSpecInput,
} from "./validate-generate-spec-input.js";

describe("validateGenerateSpecInput", () => {
  it("accepts non-empty prompt", () => {
    expect(() => validateGenerateSpecInput("hello")).not.toThrow();
  });

  it("rejects empty prompt", () => {
    expect(() => validateGenerateSpecInput("")).toThrow("prompt is required");
    expect(() => validateGenerateSpecInput("   ")).toThrow("prompt is required");
  });

  it("rejects prompt over max length", () => {
    const long = "a".repeat(MAX_SPEC_PROMPT_CHARS + 1);
    expect(() => validateGenerateSpecInput(long)).toThrow("prompt too long");
  });

  it("rejects existing_spec over max length", () => {
    const existing = "x".repeat(MAX_SPEC_EXISTING_CHARS + 1);
    expect(() => validateGenerateSpecInput("ok", existing)).toThrow(
      "existing_spec too long"
    );
  });
});

describe("buildSpecUserMessage", () => {
  it("returns prompt only when no existing spec", () => {
    expect(buildSpecUserMessage("  do thing  ")).toBe("do thing");
  });

  it("wraps existing spec and instructions", () => {
    expect(buildSpecUserMessage("add APIs", "v1 spec")).toContain("## Existing spec");
    expect(buildSpecUserMessage("add APIs", "v1 spec")).toContain("v1 spec");
    expect(buildSpecUserMessage("add APIs", "v1 spec")).toContain("add APIs");
  });
});
