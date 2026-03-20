// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";

const { startRunMock, connectRunEventsMock } = vi.hoisted(() => ({
  startRunMock: vi.fn(),
  connectRunEventsMock: vi.fn(() => null),
}));

vi.mock("../../lib/api", () => ({
  startRun: startRunMock,
  connectRunEvents: connectRunEventsMock,
}));

import { useGraphStore } from "../useGraphStore";

describe("startRun input forwarding", () => {
  beforeEach(() => {
    startRunMock.mockReset();
    connectRunEventsMock.mockClear();
    startRunMock.mockResolvedValue({ run_id: "run-12345678" });
    useGraphStore.setState({
      graphId: "wf-1",
      danGraph: {
        version: "dan_graph_v1",
        metadata: { name: "wf", description: "" },
        nodes: [
          {
            id: "input-a",
            node_type: "input",
            name: "Input A",
            description: "",
            position: { x: 0, y: 0 },
            input_ports: [],
            output_ports: [{ name: "input", schema: {} }],
            ui: {},
            metadata: {},
            variables: [{ name: "watchlist_path", type: "string", default: "", description: "" }],
          },
          {
            id: "input-b",
            node_type: "input",
            name: "Input B",
            description: "",
            position: { x: 0, y: 0 },
            input_ports: [],
            output_ports: [{ name: "input", schema: {} }],
            ui: {},
            metadata: {},
            variables: [{ name: "kb_root", type: "string", default: "", description: "" }],
          },
        ],
        edges: [],
        sub_graphs: {},
        entry_points: ["input-a", "input-b"],
        exit_points: ["input-a", "input-b"],
        shared_context: [],
        artifact_refs: [],
      },
      inputNodeValues: {
        "input-a": { watchlist_path: "/tmp/watchlist.csv" },
        "input-b": { kb_root: "/tmp/kb" },
      },
      saveGraph: vi.fn().mockResolvedValue(true),
      addToast: vi.fn(),
      handleRunEvent: vi.fn(),
    });
  });

  it("merges values from all input nodes when no explicit run inputs are provided", async () => {
    await useGraphStore.getState().startRun();

    expect(startRunMock).toHaveBeenCalledWith("wf-1", {
      watchlist_path: "/tmp/watchlist.csv",
      kb_root: "/tmp/kb",
    });
  });
});
