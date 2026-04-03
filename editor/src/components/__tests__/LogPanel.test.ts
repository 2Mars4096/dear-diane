// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LogPanel from "../LogPanel";
import { danNodeToReactFlow } from "../../lib/graphAdapter";
import { useGraphStore } from "../../store/useGraphStore";
import type { DanNode } from "../../types/graph";

function makeWorkerNode(): DanNode {
  return {
    id: "worker_1",
    node_type: "worker",
    name: "Linted Worker",
    description: "A worker used for log testing",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "result", schema: {} }],
    position: { x: 0, y: 0 },
    ui: {},
    metadata: {},
  };
}

async function renderPanel() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(LogPanel));
  });
  return { container, root };
}

describe("LogPanel lint telemetry", () => {
  const openDebugWithError = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    openDebugWithError.mockReset();
    useGraphStore.setState({
      logs: [
        {
          timestamp: 1,
          event_type: "lint_failed",
          node_id: "worker_1",
          message: "lint_failed — worker_1",
          data: {
            source_port: "result",
            target_node_id: "reviewer_1",
            target_port: "draft",
            severity: "error",
            tier_reached: 3,
            attempt: 2,
            elapsed_ms: 14.5,
            diagnostic_count: 1,
            handoff_committed: false,
            retry_scheduled: true,
            rule_codes: ["intent_alignment"],
            diagnostics: [
              {
                code: "intent_alignment",
                message: "Output does not satisfy the downstream draft intent",
              },
            ],
          },
        },
        {
          timestamp: 2,
          event_type: "lint_auto_fixed",
          node_id: "worker_1",
          message: "lint_auto_fixed — worker_1",
          data: {
            source_port: "result",
            target_node_id: "reviewer_1",
            target_port: "draft",
            severity: "warning",
            tier_reached: 1,
            attempt: 1,
            elapsed_ms: 4.2,
            diagnostic_count: 1,
            handoff_committed: true,
            rule_codes: ["trim_whitespace"],
            applied_fixes: ["trim_whitespace"],
            diagnostics: [
              {
                code: "trim_whitespace",
                message: "Removed leading and trailing whitespace",
              },
            ],
          },
        },
      ],
      nodes: [danNodeToReactFlow(makeWorkerNode())],
      edges: [],
      nodeTimings: {},
      nodeUsage: {},
      nodeCosts: {},
      runSummary: null,
      runStatus: "running",
      selectedNodeId: null,
      selectedEdgeId: null,
      selectedNodeIds: new Set<string>(),
      wasteFindings: [],
      openDebugWithError,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("groups lint events and renders structured lint details", async () => {
    const { container, root } = await renderPanel();

    expect(container.textContent).toContain("Lint Gates");
    expect(container.textContent).toContain("lint_failed");
    expect(container.textContent).toContain("lint_auto_fixed");
    expect(container.textContent).toContain("severity=error");
    expect(container.textContent).toContain("tier=3");
    expect(container.textContent).toContain("retry=scheduled");
    expect(container.textContent).toContain("handoff=blocked");
    expect(container.textContent).toContain("rules=intent_alignment");
    expect(container.textContent).toContain("handoff=committed");
    expect(container.textContent).toContain("rules=trim_whitespace");

    const moreButtons = Array.from(container.querySelectorAll("button")).filter((button) =>
      button.textContent?.includes("more"),
    );

    await act(async () => {
      for (const button of moreButtons) {
        button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      }
    });

    expect(container.textContent).toContain(
      "intent_alignment: Output does not satisfy the downstream draft intent",
    );
    expect(container.textContent).toContain("fixes=trim_whitespace");
    expect(container.textContent).toContain(
      "trim_whitespace: Removed leading and trailing whitespace",
    );

    const fixButton = container.querySelector(
      'button[title="Fix this error in Debug mode"]',
    ) as HTMLButtonElement | null;
    expect(fixButton).toBeTruthy();

    await act(async () => {
      fixButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(openDebugWithError).toHaveBeenCalledWith(
      expect.stringContaining("lint_failed"),
    );

    await act(async () => {
      root.unmount();
    });
  });

  it("surfaces lint counts in the run summary bar", async () => {
    useGraphStore.setState({
      runStatus: "failed",
      runSummary: {
        elapsed_seconds: 1.2,
        total_prompt_tokens: 10,
        total_completion_tokens: 5,
        total_tokens: 15,
      },
    });

    const { container, root } = await renderPanel();

    expect(container.textContent).toContain("Failed in 1.2s");
    expect(container.textContent).toContain("lint 1 blocked / 1 fixed / 0 passed");

    const detailsButton = Array.from(container.querySelectorAll("span")).find((node) =>
      node.textContent?.includes("details"),
    );
    expect(detailsButton).toBeTruthy();

    await act(async () => {
      detailsButton?.parentElement?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.textContent).toContain("Lint Summary");
    expect(container.textContent).toContain("1 blocked, 1 auto-fixed, 0 passed");

    await act(async () => {
      root.unmount();
    });
  });
});
