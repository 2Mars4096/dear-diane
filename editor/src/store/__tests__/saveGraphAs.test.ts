// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";

const { saveGraphAsMock } = vi.hoisted(() => ({
  saveGraphAsMock: vi.fn(),
}));

vi.mock("../../lib/api", () => ({
  saveGraphAs: saveGraphAsMock,
}));

import { useGraphStore } from "../useGraphStore";

describe("saveGraphAs", () => {
  beforeEach(() => {
    saveGraphAsMock.mockReset();
    saveGraphAsMock.mockResolvedValue({
      graph_id: "daily-equity-watchlist",
      source_graph_id: "_scratch",
      data: {
        version: "dan_graph_v1",
        metadata: { name: "Daily Equity Watchlist" },
        nodes: [],
        edges: [],
        sub_graphs: {},
        entry_points: [],
        exit_points: [],
        shared_context: [],
        artifact_refs: [],
      },
      graph_revision: "rev-2",
    });
  });

  it("persists the current graph under a new named workflow and switches to it", async () => {
    const addToast = vi.fn();
    const loadGraphList = vi.fn().mockResolvedValue(undefined);
    const replaceActiveTabGraph = vi.fn().mockResolvedValue(undefined);

    useGraphStore.setState({
      graphId: "_scratch",
      danGraph: {
        version: "dan_graph_v1",
        metadata: { name: "code_gen_validation", description: "" },
        nodes: [],
        edges: [],
        sub_graphs: {},
        entry_points: [],
        exit_points: [],
        shared_context: [],
        artifact_refs: [],
      },
      nodes: [],
      edges: [],
      layerStack: [],
      loopGroups: [],
      addToast,
      loadGraphList,
      replaceActiveTabGraph,
    });

    const result = await useGraphStore.getState().saveGraphAs("Daily Equity Watchlist");

    expect(result).toBe("daily-equity-watchlist");
    expect(saveGraphAsMock).toHaveBeenCalledWith(
      "_scratch",
      expect.objectContaining({
        new_name: "Daily Equity Watchlist",
      }),
    );
    expect(loadGraphList).toHaveBeenCalled();
    expect(replaceActiveTabGraph).toHaveBeenCalledWith("daily-equity-watchlist");
    expect(addToast).toHaveBeenCalledWith(
      expect.objectContaining({ type: "success" }),
    );
  });
});
