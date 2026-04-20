// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OrganismLogTimelinePanel from "../OrganismLogTimelinePanel";
import { useCodeStore } from "../../../store/useCodeStore";

async function renderPanel() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(OrganismLogTimelinePanel));
  });
  return { container, root };
}

function setInputValue(node: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(node, value);
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("OrganismLogTimelinePanel", () => {
  let originalPinnedRoots: string[];

  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    originalPinnedRoots = [...useCodeStore.getState().pinnedRoots];
    useCodeStore.setState({ pinnedRoots: [] });
  });

  afterEach(() => {
    useCodeStore.setState({ pinnedRoots: originalPinnedRoots });
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("explains why manual open is disabled when only placeholder text is visible", async () => {
    const { container, root } = await renderPanel();

    const pathInput = container.querySelector("input") as HTMLInputElement | null;
    const openButton = container.querySelector(
      'button[type="submit"]',
    ) as HTMLButtonElement | null;

    expect(pathInput?.placeholder).toContain("/absolute/path/to/");
    expect(openButton?.disabled).toBe(true);
    expect(container.textContent).toContain("Paste a log path to open.");
    expect(container.textContent).toContain("Pin this repo as a workspace root");

    await act(async () => {
      root.unmount();
    });
  });

  it("allows manual open without a pinned root when the user pastes an absolute path", async () => {
    const { container, root } = await renderPanel();

    const pathInput = container.querySelector("input") as HTMLInputElement;
    const openButton = container.querySelector(
      'button[type="submit"]',
    ) as HTMLButtonElement;

    await act(async () => {
      setInputValue(pathInput, "/tmp/example/.dan-research/runs/turn-01/events.jsonl");
    });

    expect(openButton.disabled).toBe(false);
    expect(container.textContent).toContain("Open this log path directly.");

    await act(async () => {
      root.unmount();
    });
  });
});
