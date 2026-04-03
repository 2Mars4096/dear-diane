// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    fetchTelemetryAnalytics: vi.fn(),
  };
});

import { fetchTelemetryAnalytics, type TelemetryAnalyticsResponse } from "../../lib/api";
import { useGraphStore } from "../../store/useGraphStore";
import TokenAnalyticsPanel from "../TokenAnalyticsPanel";

const fetchTelemetryAnalyticsMock = vi.mocked(fetchTelemetryAnalytics);

async function renderPanel() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(TokenAnalyticsPanel));
  });
  await act(async () => {
    await Promise.resolve();
  });
  return { container, root };
}

function telemetrySummary(): TelemetryAnalyticsResponse {
  return {
    filters: { surface: "editor", session_id: null, since: null, until: null },
    totals: {
      events: 8,
      chat_turns: 2,
      fast_commands: 0,
      gateway_calls: 2,
      total_tokens: 320,
      total_cost: 0.0042,
    },
    event_types: [],
    activity_by_hour: [],
    models: [],
    modes: [],
    lint: {
      workflow_runs: 4,
      with_activity: 3,
      blocked_runs: 1,
      autofixed_runs: 2,
      warning_runs: 1,
      passed_runs: 1,
      states: [],
    },
    window_hours: 24,
  };
}

describe("TokenAnalyticsPanel lint telemetry strip", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    fetchTelemetryAnalyticsMock.mockResolvedValue(telemetrySummary());
    useGraphStore.setState({
      runId: "run-telemetry-panel",
      runStatus: "completed",
      wasteFindings: [],
      optimizationMutations: [],
      analyticsLoading: false,
      nodes: [],
      nodeCosts: {},
      nodeUsage: {},
      fetchTokenAnalytics: vi.fn().mockResolvedValue(undefined),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
    fetchTelemetryAnalyticsMock.mockReset();
  });

  it("surfaces lint rollout counts in the telemetry summary strip", async () => {
    const { container, root } = await renderPanel();

    expect(fetchTelemetryAnalyticsMock).toHaveBeenCalled();
    expect(container.textContent).toContain("Last 24h telemetry");
    expect(container.textContent).toContain("lint: 3/4 runs");
    expect(container.textContent).toContain("blocked 1");
    expect(container.textContent).toContain("auto-fixed 2");
    expect(container.textContent).toContain("warnings 1");
    expect(container.textContent).toContain("passed 1");

    await act(async () => {
      root.unmount();
    });
  });
});
