// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunEventPayload } from "../../types/chat";
import RunOutputBlock from "../RunOutputBlock";

async function renderBlock(events: RunEventPayload[]) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(RunOutputBlock, { events }));
  });
  return { container, root };
}

describe("RunOutputBlock lint telemetry", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("summarizes lint outcomes and renders per-node lint activity", async () => {
    const events: RunEventPayload[] = [
      {
        type: "chat_run_event",
        event_type: "node_started",
        node_id: "source",
        summary: "Started source",
        detail: {},
      },
      {
        type: "chat_run_event",
        event_type: "lint_auto_fixed",
        node_id: "source",
        summary: "Auto-fixed whitespace",
        detail: {
          data: {
            source_port: "result",
            target_node_id: "reviewer",
            target_port: "draft",
            diagnostics: [{ message: "Trimmed extra whitespace" }],
          },
        },
      },
      {
        type: "chat_run_event",
        event_type: "lint_failed",
        node_id: "source",
        summary: "Blocked by lint",
        detail: {
          data: {
            source_port: "result",
            target_node_id: "reviewer",
            target_port: "draft",
            severity: "error",
            handoff_committed: false,
            diagnostics: [{ message: "Missing summary" }],
          },
        },
      },
      {
        type: "chat_run_event",
        event_type: "run_failed",
        summary: "Run failed",
        detail: {},
      },
    ];

    const { container, root } = await renderBlock(events);

    expect(container.textContent).toContain(
      "1 node — Failed · 1 lint blocked / 1 auto-fixed / 0 passed",
    );

    const buttons = Array.from(container.querySelectorAll("button"));
    const headerButton = buttons.find((button) =>
      button.textContent?.includes("1 node — Failed"),
    );
    expect(headerButton).toBeTruthy();

    await act(async () => {
      headerButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.textContent).toContain("Lint Summary");
    expect(container.textContent).toContain("1 blocked, 1 auto-fixed, 0 passed");

    const sourceButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("source"),
    );
    expect(sourceButton).toBeTruthy();

    await act(async () => {
      sourceButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.textContent).toContain("Lint activity");
    expect(container.textContent).toContain("result -> reviewer.draft");
    expect(container.textContent).toContain("Missing summary");
    expect(container.textContent).toContain("Trimmed extra whitespace");

    await act(async () => {
      root.unmount();
    });
  });
});
