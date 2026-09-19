import { expect, it } from "vitest";
import { completedMessageContent } from "../eventPresentation";
it("preserves a long streamed response when completion contains only its 500-character preview", () => {
  const full = "The complete pitch. ".repeat(70);
  expect(completedMessageContent(full, full.slice(0, 500))).toBe(full);
});
it("accepts an explicit final answer and retains summary-only responses", () => {
  expect(completedMessageContent("Progress", "Done", "Full answer")).toBe("Full answer");
  expect(completedMessageContent("", "Answer")).toBe("Answer");
});
