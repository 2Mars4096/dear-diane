// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useResearchStore, type TrainingSession } from "../../../store/useResearchStore";

const furnaceListSessionsMock = vi.fn();
const furnaceConnectSSEMock = vi.fn();

vi.mock("../../../lib/api", () => ({
  furnaceListSessions: (...args: unknown[]) => furnaceListSessionsMock(...args),
  furnaceConnectSSE: (...args: unknown[]) => furnaceConnectSSEMock(...args),
}));

vi.mock("../../../lib/researchEventRouter", () => ({
  handleFurnaceSSEEvent: vi.fn(),
}));

import { useResearchFurnaceSessions } from "../useResearchFurnaceSessions";

function HookHarness({ enabled = true }: { enabled?: boolean }) {
  useResearchFurnaceSessions({ enabled });
  return React.createElement("div", null, "furnace-hook");
}

function makeSession(
  overrides: Partial<TrainingSession> = {},
): TrainingSession {
  return {
    id: "session-local-1",
    sessionId: "sess-live",
    recipeId: "recipe-live",
    name: "Live Session",
    topic: "Live Topic",
    status: "running",
    targetPapers: 10,
    processedPapers: 5,
    startedAt: 1,
    ...overrides,
  };
}

function makeEventSource(): EventSource {
  return {
    close: vi.fn(),
  } as unknown as EventSource;
}

async function renderHarness(enabled = true) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(HookHarness, { enabled }));
  });
  return { container, root };
}

async function flushEffects() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useResearchFurnaceSessions", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.useFakeTimers();
    furnaceListSessionsMock.mockReset();
    furnaceConnectSSEMock.mockReset();
    useResearchStore.setState({
      trainingSessions: [],
    });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("syncs backend session summaries into the research store", async () => {
    furnaceListSessionsMock.mockResolvedValue({
      sessions: [
        {
          session_id: "sess-1",
          recipe_id: "recipe-1",
          name: "Distillation Run",
          topic: "Systems Research",
          status: "running",
          current_phase: "aggregate",
          source_count: 12,
          processed_count: 7,
          total_cost_usd: 1.25,
          variant_label: "",
          parent_session_id: "",
          family_session_id: "sess-1",
          tags: ["core"],
          created_at: 1,
          updated_at: 2,
        },
      ],
    });
    furnaceConnectSSEMock.mockImplementation(() => makeEventSource());

    const { root } = await renderHarness();
    await flushEffects();

    const sessions = useResearchStore.getState().trainingSessions;
    expect(sessions).toHaveLength(1);
    expect(sessions[0]).toMatchObject({
      sessionId: "sess-1",
      recipeId: "recipe-1",
      name: "Distillation Run",
      topic: "Systems Research",
      status: "running",
      currentPhase: "aggregate",
      sourceCount: 12,
      processedPapers: 7,
    });
    expect(furnaceConnectSSEMock).toHaveBeenCalledWith(
      "sess-1",
      expect.any(Function),
      expect.any(Function),
    );

    await act(async () => {
      root.unmount();
    });
  });

  it("marks active sessions as reconnecting and retries the SSE stream once", async () => {
    let firstClose: (() => void) | undefined;

    useResearchStore.setState({
      trainingSessions: [makeSession()],
    });
    furnaceListSessionsMock.mockResolvedValue({ sessions: [] });
    furnaceConnectSSEMock.mockImplementation(
      (_sessionId: string, _onEvent: unknown, onClose?: () => void) => {
        if (!firstClose) {
          firstClose = onClose;
        }
        return makeEventSource();
      },
    );

    const { root } = await renderHarness();
    await flushEffects();

    expect(furnaceConnectSSEMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      firstClose?.();
    });

    expect(useResearchStore.getState().trainingSessions[0]?.statusMessage).toBe(
      "Reconnecting live session updates...",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(furnaceConnectSSEMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      root.unmount();
    });
  });

  it("stays inert when the controller is disabled", async () => {
    furnaceListSessionsMock.mockResolvedValue({
      sessions: [
        {
          session_id: "sess-1",
          recipe_id: "recipe-1",
          name: "Distillation Run",
          topic: "Systems Research",
          status: "running",
          current_phase: "aggregate",
          source_count: 12,
          processed_count: 7,
          total_cost_usd: 1.25,
          variant_label: "",
          parent_session_id: "",
          family_session_id: "sess-1",
          tags: ["core"],
          created_at: 1,
          updated_at: 2,
        },
      ],
    });

    const { root } = await renderHarness(false);
    await flushEffects();

    expect(useResearchStore.getState().trainingSessions).toHaveLength(0);
    expect(furnaceListSessionsMock).not.toHaveBeenCalled();
    expect(furnaceConnectSSEMock).not.toHaveBeenCalled();

    await act(async () => {
      root.unmount();
    });
  });
});
