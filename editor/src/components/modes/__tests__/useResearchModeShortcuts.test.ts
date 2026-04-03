// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../../../store/useAppStore";
import { useCodeStore } from "../../../store/useCodeStore";
import { useResearchStore } from "../../../store/useResearchStore";
import { useResearchModeShortcuts } from "../useResearchModeShortcuts";

function ShortcutHarness() {
  useResearchModeShortcuts();
  return null;
}

async function mountHarness() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(React.createElement(ShortcutHarness));
  });

  return { container, root };
}

function dispatchShortcut(
  key: string,
  options: Partial<KeyboardEventInit> = {},
) {
  window.dispatchEvent(
    new KeyboardEvent("keydown", {
      key,
      bubbles: true,
      cancelable: true,
      ...options,
    }),
  );
}

describe("useResearchModeShortcuts", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    useAppStore.setState({ activeMode: "research" });
    useResearchStore.setState({
      primaryTab: "editor",
      contextTab: "references",
      showContextPanel: false,
      activeRailSection: "library",
      trainingSessions: [],
      pipeline: [],
      documentContent: "",
      papers: [],
      activePaperId: null,
      annotations: [],
      pageSummaries: {},
    });
    useCodeStore.setState({
      showTerminal: false,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("only fires shortcuts while Research mode is active", async () => {
    const { root } = await mountHarness();

    useAppStore.setState({ activeMode: "development" });
    await act(async () => {
      dispatchShortcut("e", { metaKey: true });
    });
    expect(useResearchStore.getState().primaryTab).toBe("editor");

    useAppStore.setState({ activeMode: "research" });
    await act(async () => {
      dispatchShortcut("e", { metaKey: true });
    });
    expect(useResearchStore.getState().primaryTab).toBe("reader");

    await act(async () => {
      root.unmount();
    });
  });

  it("opens the context drawer and switches context tabs from research shortcuts", async () => {
    const { root } = await mountHarness();

    await act(async () => {
      dispatchShortcut("r", { metaKey: true, shiftKey: true });
    });
    expect(useResearchStore.getState().contextTab).toBe("notes");
    expect(useResearchStore.getState().showContextPanel).toBe(true);

    useResearchStore.setState({
      contextTab: "notes",
      showContextPanel: false,
    });

    await act(async () => {
      dispatchShortcut("o", { metaKey: true, shiftKey: true });
    });
    expect(useResearchStore.getState().contextTab).toBe("outline");
    expect(useResearchStore.getState().showContextPanel).toBe(true);

    await act(async () => {
      root.unmount();
    });
  });

  it("toggles the shared terminal from the research shortcut rail", async () => {
    const { root } = await mountHarness();

    await act(async () => {
      dispatchShortcut("`", { metaKey: true });
    });
    expect(useCodeStore.getState().showTerminal).toBe(true);

    await act(async () => {
      dispatchShortcut("`", { metaKey: true });
    });
    expect(useCodeStore.getState().showTerminal).toBe(false);

    await act(async () => {
      root.unmount();
    });
  });
});
