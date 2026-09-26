import { describe, expect, it } from "vitest";
import { toEngineError } from "./engine";

describe("toEngineError", () => {
  it("keeps the three parts the engine separates", () => {
    const error = toEngineError(409, {
      error: {
        message: "triposr is not installed.",
        technical: null,
        suggestions: ["Install it first."],
      },
    });
    expect(error.message).toBe("triposr is not installed.");
    expect(error.suggestions).toEqual(["Install it first."]);
    expect(error.status).toBe(409);
  });

  it("keeps the technical detail apart from the message", () => {
    // Spec §55: what a person reads and what a developer needs are different
    // things, and showing the second as the first is the failure to avoid.
    const error = toEngineError(500, {
      error: { message: "Something went wrong.", technical: "RuntimeError: boom" },
    });
    expect(error.message).toBe("Something went wrong.");
    expect(error.technical).toBe("RuntimeError: boom");
  });

  it("says the status rather than inventing a cause when the body is not ours", () => {
    // A crash before the handlers, or something else answering on the port.
    const error = toEngineError(502, "<html>Bad Gateway</html>");
    expect(error.message).toContain("502");
    expect(error.technical).toBe("<html>Bad Gateway</html>");
    expect(error.suggestions).toEqual([]);
  });

  it("does not trust a body that merely looks like an error", () => {
    const error = toEngineError(400, { error: "just a string" });
    expect(error.message).toContain("400");
  });
});
