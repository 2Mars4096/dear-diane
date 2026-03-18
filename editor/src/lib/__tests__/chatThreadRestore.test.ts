// @vitest-environment happy-dom

import { beforeEach, describe, expect, it } from "vitest";

import {
  chooseInitialChatThreadId,
  readSavedActiveThreadSelection,
  saveActiveThreadSelection,
} from "../chatThreadRestore";
import type { ChatThreadSummary } from "../api";

function makeThread(id: string): ChatThreadSummary {
  return {
    id,
    title: id,
    workflow_id: "_scratch",
    message_count: 1,
    created_at: "2026-03-18T00:00:00.000Z",
    updated_at: "2026-03-18T00:00:00.000Z",
  };
}

describe("chatThreadRestore", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("stores scratch-thread selections per workspace", () => {
    saveActiveThreadSelection("_scratch", "thread-a", "ws-a");
    saveActiveThreadSelection("_scratch", "thread-b", "ws-b");

    expect(readSavedActiveThreadSelection("_scratch", "ws-a")).toBe("thread-a");
    expect(readSavedActiveThreadSelection("_scratch", "ws-b")).toBe("thread-b");
  });

  it("falls back to the legacy graph-only key", () => {
    localStorage.setItem("dan_active_thread__scratch", "legacy-thread");

    expect(readSavedActiveThreadSelection("_scratch", "ws-a")).toBe(
      "legacy-thread",
    );
  });

  it("prefers workspace, then app, then saved, then most recent thread", () => {
    const threads = [makeThread("first"), makeThread("second")];

    saveActiveThreadSelection("_scratch", "second", "ws-a");
    expect(
      chooseInitialChatThreadId({
        threads,
        workflowId: "_scratch",
        workspaceId: "ws-a",
        workspaceActiveThreadId: "missing",
        appThreadId: "first",
        appWorkflowId: "_scratch",
      }),
    ).toBe("first");

    expect(
      chooseInitialChatThreadId({
        threads,
        workflowId: "_scratch",
        workspaceId: "ws-a",
        workspaceActiveThreadId: "second",
        appThreadId: "first",
        appWorkflowId: "_scratch",
      }),
    ).toBe("second");

    expect(
      chooseInitialChatThreadId({
        threads,
        workflowId: "_scratch",
        workspaceId: "ws-a",
        workspaceActiveThreadId: null,
        appThreadId: null,
        appWorkflowId: null,
      }),
    ).toBe("second");

    localStorage.clear();
    expect(
      chooseInitialChatThreadId({
        threads,
        workflowId: "_scratch",
        workspaceId: "ws-a",
        workspaceActiveThreadId: null,
        appThreadId: null,
        appWorkflowId: null,
      }),
    ).toBe("first");
  });
});
