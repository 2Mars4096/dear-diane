// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../code/FileExplorer", () => ({
  default: () => React.createElement("div", null, "explorer-panel"),
}));

vi.mock("../../code/SearchPanel", () => ({
  default: () => React.createElement("div", null, "search-panel"),
}));

vi.mock("../../code/TerminalPanel", () => ({
  default: () => React.createElement("div", null, "terminal-panel"),
}));

vi.mock("../../code/ProblemsPanel", () => ({
  default: () => React.createElement("div", null, "problems-panel"),
  useProblemsCount: () => ({ errors: 0, warnings: 0 }),
}));

vi.mock("../../code/OutputPanel", () => ({
  default: () => React.createElement("div", null, "output-panel"),
}));

vi.mock("../../code/GitPanel", () => ({
  default: () => React.createElement("div", null, "git-panel"),
}));

vi.mock("../../code/DevelopmentTimelinePanel", () => ({
  default: () => React.createElement("div", null, "development-timeline-panel"),
}));

vi.mock("../DevelopmentModePanels", () => ({
  PREVIEW_ONLY_CODE_PANELS: new Set([
    "explorer",
    "search",
    "git",
    "timeline",
    "tasks",
    "testing",
    "outline",
    "debug",
  ]),
  DevelopmentPreviewPanel: ({ panel }: { panel: string }) =>
    React.createElement("div", null, `preview:${panel}`),
  WorkflowSidebarPanel: () => React.createElement("div", null, "workflow-panel"),
  FurnaceSidebarPanel: () => React.createElement("div", null, "furnace-panel"),
  DevelopmentModeStatusStrip: () => React.createElement("div", null, "status-strip"),
}));

import {
  DevelopmentRuntimeBanner,
  DevelopmentSidebarSurface,
} from "../DevelopmentModeShell";

async function renderNode(node: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(node);
  });
  return { container, root };
}

describe("DevelopmentModeShell", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("shows the desktop-only warning banner in browser preview", async () => {
    const { container, root } = await renderNode(
      React.createElement(DevelopmentRuntimeBanner, { electron: false }),
    );

    expect(container.textContent).toContain("requires the desktop app");
    expect(container.textContent).toContain("Browser preview stays available");

    await act(async () => {
      root.unmount();
    });
  });

  it("hides the runtime warning banner in Electron", async () => {
    const { container, root } = await renderNode(
      React.createElement(DevelopmentRuntimeBanner, { electron: true }),
    );

    expect(container.textContent).toBe("");

    await act(async () => {
      root.unmount();
    });
  });

  it("routes preview-only panels to the honest browser preview surface", async () => {
    const { container, root } = await renderNode(
      React.createElement(DevelopmentSidebarSurface, {
        panel: "git",
        electron: false,
      }),
    );

    expect(container.textContent).toContain("preview:git");
    expect(container.textContent).not.toContain("git-panel");

    await act(async () => {
      root.unmount();
    });
  });

  it("keeps DAN workflow handoff panels available in browser preview", async () => {
    const { container, root } = await renderNode(
      React.createElement(DevelopmentSidebarSurface, {
        panel: "workflow",
        electron: false,
      }),
    );

    expect(container.textContent).toContain("workflow-panel");
    expect(container.textContent).not.toContain("preview:workflow");

    await act(async () => {
      root.unmount();
    });
  });

  it("renders the real desktop panel once Electron-backed surfaces are available", async () => {
    const { container, root } = await renderNode(
      React.createElement(DevelopmentSidebarSurface, {
        panel: "git",
        electron: true,
      }),
    );

    await act(async () => {
      await vi.dynamicImportSettled();
    });

    expect(container.textContent).toContain("git-panel");
    expect(container.textContent).not.toContain("preview:git");

    await act(async () => {
      root.unmount();
    });
  });

  it("routes the timeline sidebar to the development timeline panel in Electron", async () => {
    const { container, root } = await renderNode(
      React.createElement(DevelopmentSidebarSurface, {
        panel: "timeline",
        electron: true,
      }),
    );

    await act(async () => {
      await vi.dynamicImportSettled();
    });

    expect(container.textContent).toContain("development-timeline-panel");

    await act(async () => {
      root.unmount();
    });
  });
});
