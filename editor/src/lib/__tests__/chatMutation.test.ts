import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../../types/chat";
import {
  buildAutoApplyPreviewMessage,
  parseRunIdFromStreamChannel,
  readMutationConfirmPreference,
  shouldAutoApplyMutation,
  summarizeMutationPlan,
} from "../chatMutation";

describe("chatMutation helpers", () => {
  it("reads confirm preference from storage", () => {
    const storage = {
      getItem: () => "1",
    };
    expect(readMutationConfirmPreference(storage)).toBe(true);
  });

  it("defaults confirm preference to false", () => {
    const storage = {
      getItem: () => null,
    };
    expect(readMutationConfirmPreference(storage)).toBe(false);
  });

  it("auto-applies only on successful dry run when confirm is off", () => {
    expect(shouldAutoApplyMutation({ success: true }, false)).toBe(true);
    expect(shouldAutoApplyMutation({ success: false }, false)).toBe(false);
    expect(shouldAutoApplyMutation({ success: true }, true)).toBe(false);
    expect(shouldAutoApplyMutation({ success: true }, false, "applied")).toBe(false);
  });

  it("summarizes mutation plan", () => {
    expect(
      summarizeMutationPlan({
        operations: [
          { op: "add_node" },
          { op: "add_edge" },
          { op: "edit_node" },
          { op: "remove_edge" },
        ],
      }),
    ).toBe("Applied: +1 node, +1 edge, -1 edge, ~1 edit");
  });

  it("parses run id from run stream channel", () => {
    expect(parseRunIdFromStreamChannel("run-run-123")).toBe("run-123");
    expect(parseRunIdFromStreamChannel("chat-abc")).toBeNull();
    expect(parseRunIdFromStreamChannel(undefined)).toBeNull();
  });

  it("builds preview message payload for auto-apply", () => {
    const base: ChatMessage = {
      id: "a1",
      role: "assistant",
      content: "",
      timestamp: 1,
    };
    const msg = buildAutoApplyPreviewMessage(
      base,
      { operations: [{ op: "add_node" }] },
      { success: true },
      "mid-1",
      "proposed",
      "reasoning",
      { prompt: 1, completion: 2 },
    );
    expect(msg.mutationStatus).toBe("proposed");
    expect(msg.mutationId).toBe("mid-1");
    expect(msg.content).toBe("reasoning");
    expect(msg.dryRunResult?.success).toBe(true);
  });

  it("preserves applied mutation status from the stream event", () => {
    const base: ChatMessage = {
      id: "a1",
      role: "assistant",
      content: "",
      timestamp: 1,
    };
    const msg = buildAutoApplyPreviewMessage(
      base,
      { operations: [{ op: "add_node" }] },
      { success: true },
      "mid-2",
      "applied",
      "applied content",
      { prompt: 1, completion: 2 },
    );
    expect(msg.mutationStatus).toBe("applied");
    expect(msg.content).toBe("applied content");
  });
});
