// @vitest-environment happy-dom

import { beforeEach, describe, expect, it } from "vitest";

import {
  buildLegacyModeChatStorageKey,
  buildModeChatStorageKey,
  clearModeChatSession,
  loadModeChatSession,
  saveModeChatSession,
} from "../modeChatSidebarSession";

describe("modeChatSidebarSession", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("scopes persisted sessions by workflow and only uses legacy storage for scratch", () => {
    const workspaceId = "ws-test";
    const mode = "development";

    localStorage.setItem(
      buildLegacyModeChatStorageKey(workspaceId, mode),
      JSON.stringify({
        threadId: "legacy-thread",
        chatMode: "agent",
        messages: [
          {
            id: "legacy-message",
            role: "user",
            content: "Legacy scratch draft",
            timestamp: 1,
          },
        ],
      }),
    );

    saveModeChatSession(workspaceId, mode, "workflow-alpha", {
      threadId: "thread-alpha",
      chatMode: "plan",
      messages: [
        {
          id: "alpha-message",
          role: "user",
          content: "Alpha workflow draft",
          timestamp: 2,
        },
      ],
    });

    expect(loadModeChatSession(workspaceId, mode, "_scratch")).toMatchObject({
      threadId: "legacy-thread",
      chatMode: "agent",
    });
    expect(loadModeChatSession(workspaceId, mode, "_scratch").messages[0]?.content).toBe(
      "Legacy scratch draft",
    );

    expect(loadModeChatSession(workspaceId, mode, "workflow-alpha")).toMatchObject({
      threadId: "thread-alpha",
      chatMode: "plan",
    });
    expect(
      loadModeChatSession(workspaceId, mode, "workflow-alpha").messages[0]?.content,
    ).toBe("Alpha workflow draft");

    expect(loadModeChatSession(workspaceId, mode, "workflow-beta")).toEqual({
      messages: [],
      threadId: null,
      chatMode: "auto",
    });
  });

  it("clears only the targeted workflow session and removes scratch legacy fallback", () => {
    const workspaceId = "ws-test";
    const mode = "development";

    localStorage.setItem(
      buildLegacyModeChatStorageKey(workspaceId, mode),
      JSON.stringify({ messages: [{ id: "legacy", role: "user", content: "legacy", timestamp: 1 }] }),
    );
    localStorage.setItem(
      buildModeChatStorageKey(workspaceId, mode, "workflow-alpha"),
      JSON.stringify({ messages: [{ id: "alpha", role: "user", content: "alpha", timestamp: 2 }] }),
    );
    localStorage.setItem(
      buildModeChatStorageKey(workspaceId, mode, "_scratch"),
      JSON.stringify({ messages: [{ id: "scratch", role: "user", content: "scratch", timestamp: 3 }] }),
    );

    clearModeChatSession(workspaceId, mode, "workflow-alpha");

    expect(
      localStorage.getItem(buildModeChatStorageKey(workspaceId, mode, "workflow-alpha")),
    ).toBeNull();
    expect(localStorage.getItem(buildLegacyModeChatStorageKey(workspaceId, mode))).not.toBeNull();
    expect(
      localStorage.getItem(buildModeChatStorageKey(workspaceId, mode, "_scratch")),
    ).not.toBeNull();

    clearModeChatSession(workspaceId, mode, "_scratch");

    expect(localStorage.getItem(buildLegacyModeChatStorageKey(workspaceId, mode))).toBeNull();
    expect(
      localStorage.getItem(buildModeChatStorageKey(workspaceId, mode, "_scratch")),
    ).toBeNull();
  });
});
