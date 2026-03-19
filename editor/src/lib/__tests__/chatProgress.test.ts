import { describe, expect, it } from "vitest";

import { progressAckText } from "../chatProgress";

describe("chatProgress", () => {
  it("prefers phase labels for progress acknowledgements", () => {
    expect(progressAckText({
      type: "chat_complete",
      detected_mode: "progress_ack",
      phase_label: "Preparing workflow change preview",
      content: "Working on it",
    })).toBe("Preparing workflow change preview");
  });

  it("falls back to content when no phase label exists", () => {
    expect(progressAckText({
      type: "chat_complete",
      detected_mode: "progress_ack",
      content: "Working on it",
    })).toBe("Working on it");
  });

  it("ignores non-progress events", () => {
    expect(progressAckText({
      type: "chat_complete",
      detected_mode: "agent",
      content: "Done",
    })).toBeNull();
  });
});
