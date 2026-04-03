// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useResearchStore } from "../../../store/useResearchStore";
import { useSettingsStore } from "../../../store/useSettingsStore";
import { useWorkspaceStore } from "../../../store/useWorkspaceStore";
import ResearchFurnacePanel from "../ResearchFurnacePanel";
import type { ResearchFurnaceSessionsController } from "../useResearchFurnaceSessions";

function buildController(): ResearchFurnaceSessionsController {
  return {
    connectSessionSSE: vi.fn(),
    resolveSessionId: vi.fn(),
  };
}

async function renderNode(node: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(node);
  });
  return { container, root };
}

describe("ResearchFurnacePanel", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    useResearchStore.setState({
      papers: [],
      trainingSessions: [],
    });
    useWorkspaceStore.setState({
      activeWorkspaceId: "ws-test",
      workspaces: [
        {
          id: "ws-test",
          name: "Research Workspace",
          pinnedPaths: [],
          lastActiveMode: "research",
          openThreadIds: [],
          activeThreadId: null,
          createdAt: 1,
          lastAccessedAt: 1,
          researchConfig: {},
        },
      ],
    });
    useSettingsStore.setState({
      researchPdfRoots: ["/papers"],
      researchNoteRoots: ["/notes"],
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("shows distinct composer and session-desk guidance on first use", async () => {
    const { container, root } = await renderNode(
      React.createElement(ResearchFurnacePanel, {
        sessionsController: buildController(),
      }),
    );

    expect(container.textContent).toContain("Compose Run");
    expect(container.textContent).toContain("Define the next Furnace session");
    expect(container.textContent).toContain("Session Desk");
    expect(container.textContent).toContain("No sessions yet");

    await act(async () => {
      root.unmount();
    });
  });

  it("prefills the composer when continuing an existing session", async () => {
    useResearchStore.setState({
      trainingSessions: [
        {
          id: "session-1",
          sessionId: "backend-session-1",
          recipeId: "recipe-1",
          name: "Distillation Run",
          topic: "Systems Research",
          status: "running",
          targetPapers: 24,
          processedPapers: 10,
          startedAt: Date.now() - 10_000,
          sourceCount: 24,
          tags: ["core"],
        },
      ],
    });

    const { container, root } = await renderNode(
      React.createElement(ResearchFurnacePanel, {
        sessionsController: buildController(),
      }),
    );

    const continueButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Continue"),
    );
    expect(continueButton).toBeTruthy();

    await act(async () => {
      continueButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });

    expect(container.textContent).toContain("Build on existing session: Distillation Run");
    expect(container.textContent).toContain("Continue Existing Session");

    await act(async () => {
      root.unmount();
    });
  });
});
