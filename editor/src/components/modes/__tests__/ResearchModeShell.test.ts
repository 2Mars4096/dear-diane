// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useResearchStore } from "../../../store/useResearchStore";
import { useSettingsStore } from "../../../store/useSettingsStore";
import { useWorkspaceStore } from "../../../store/useWorkspaceStore";

vi.mock("../../research/DomainProfile", () => ({
  default: ({ onSelect }: { onSelect?: () => void }) =>
    React.createElement(
      "button",
      { onClick: onSelect },
      "domain-profile-selector",
    ),
  getActiveProfile: () => ({
    name: "Default Profile",
    citationStyle: "apa",
    quickStarts: ["quick-start-a"],
  }),
}));

vi.mock("../../research/WritingPane", () => ({
  default: () => React.createElement("div", null, "writing-pane"),
}));

vi.mock("../../research/SplitPdfReader", () => ({
  default: () => React.createElement("div", null, "reader-pane"),
}));

vi.mock("../../research/ReferencePanel", () => ({
  default: () => React.createElement("div", null, "references-panel"),
}));

vi.mock("../../research/ReviewPanel", () => ({
  default: () => React.createElement("div", null, "reviews-panel"),
}));

vi.mock("../../research/OutlinePanel", () => ({
  default: () => React.createElement("div", null, "outline-panel"),
}));

vi.mock("../../research/NotesPanel", () => ({
  default: () => React.createElement("div", null, "notes-panel"),
}));

vi.mock("../../research/DistillationTab", () => ({
  default: () => React.createElement("div", null, "distillation-panel"),
}));

vi.mock("../../research/QuickStartPanel", () => ({
  default: ({ visibleIds }: { visibleIds: string[] }) =>
    React.createElement("div", null, `quick-start:${visibleIds.join(",")}`),
}));

import {
  ResearchModeContextPanel,
  ResearchModeFunctionRail,
  ResearchModePipelineProgress,
  ResearchModePrimaryPanel,
} from "../ResearchModeShell";

async function renderNode(node: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(node);
  });
  return { container, root };
}

describe("ResearchModeShell", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    useResearchStore.setState({
      activeRailSection: "library",
      activePaperId: null,
      activeQuickStart: null,
      annotations: [],
      contextTab: "references",
      documentContent: "",
      notes: [],
      pageSummaries: {},
      papers: [],
      pipeline: [],
      pipelinePreset: "research",
      primaryTab: "editor",
      showContextPanel: false,
      trainingSessions: [],
    });
    useWorkspaceStore.setState({
      activeWorkspaceId: "ws-test",
      workspaces: [
        {
          id: "ws-test",
          name: "Research Project",
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
      researchPdfRoots: [],
      researchNoteRoots: [],
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("shows the quick-start surface when the research desk is empty", async () => {
    const { container, root } = await renderNode(
      React.createElement(ResearchModePrimaryPanel, {
        furnacePanel: React.createElement("div", null, "furnace-panel"),
      }),
    );

    await act(async () => {
      await vi.dynamicImportSettled();
    });

    expect(container.textContent).toContain("quick-start:quick-start-a");

    await act(async () => {
      root.unmount();
    });
  });

  it("renders the furnace surface when the furnace tab is active", async () => {
    useResearchStore.setState({ primaryTab: "furnace" });

    const { container, root } = await renderNode(
      React.createElement(ResearchModePrimaryPanel, {
        furnacePanel: React.createElement("div", null, "furnace-panel"),
      }),
    );

    expect(container.textContent).toContain("furnace-panel");
    expect(container.textContent).not.toContain("quick-start:");

    await act(async () => {
      root.unmount();
    });
  });

  it("routes pipeline stage clicks into the mapped context drawer", async () => {
    useResearchStore.setState({
      pipeline: [{ id: "search", label: "Search", status: "queued" }],
    });

    const { container, root } = await renderNode(
      React.createElement(ResearchModePipelineProgress),
    );

    const stageButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Search"),
    );
    expect(stageButton).toBeTruthy();

    await act(async () => {
      stageButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });

    expect(useResearchStore.getState().contextTab).toBe("references");
    expect(useResearchStore.getState().showContextPanel).toBe(true);

    await act(async () => {
      root.unmount();
    });
  });

  it("switches rail sections without leaving the shell empty", async () => {
    const { container, root } = await renderNode(
      React.createElement(ResearchModeFunctionRail),
    );

    const trainingTab = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Training"),
    );
    expect(trainingTab).toBeTruthy();

    await act(async () => {
      trainingTab?.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });

    expect(container.textContent).toContain("No training sessions yet");

    await act(async () => {
      root.unmount();
    });
  });

  it("renders the selected context tab panel", async () => {
    useResearchStore.setState({ contextTab: "notes" });

    const { container, root } = await renderNode(
      React.createElement(ResearchModeContextPanel),
    );

    await act(async () => {
      await vi.dynamicImportSettled();
    });

    expect(container.textContent).toContain("notes-panel");

    await act(async () => {
      root.unmount();
    });
  });
});
