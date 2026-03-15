import { describe, expect, it } from "vitest";

import { deriveGraphRevisionSource, getClientGraphRevision } from "../chatGraphRevision";

describe("chatGraphRevision", () => {
  it("prefers the server-provided graph revision over a local hash", async () => {
    const graph = {
      nodes: [
        { id: "n1", position: { x: 0, y: 0 } },
        { id: "n2", position: { x: 260, y: 0 } },
      ],
    };

    await expect(getClientGraphRevision(graph, "server-rev-1234")).resolves.toBe(
      "server-rev-1234",
    );
  });

  it("prefers the local hash when the graph has unsaved edits", async () => {
    const graph = {
      nodes: [
        { id: "n1", position: { x: 0, y: 0 } },
        { id: "n2", position: { x: 260, y: 0 } },
      ],
    };

    const revision = await getClientGraphRevision(graph, "server-rev-1234", {
      preferLocal: true,
    });

    expect(revision).toBeDefined();
    expect(revision).not.toBe("server-rev-1234");
  });

  it("falls back to a local hash when no server revision is available", async () => {
    const graph = {
      b: 2,
      a: 1,
    };

    const first = await getClientGraphRevision(graph, null);
    const second = await getClientGraphRevision({ a: 1, b: 2 }, undefined);

    expect(first).toBeDefined();
    expect(second).toBe(first);
  });

  it("derives a revision source from live unsaved editor state", () => {
    const baseGraph = {
      version: "dan_graph_v1",
      metadata: { name: "test" },
      nodes: [
        {
          id: "n1",
          node_type: "llm_operator" as const,
          name: "Old name",
          input_ports: [],
          output_ports: [],
          position: { x: 0, y: 0 },
          ui: {},
          metadata: {},
          model: "mock",
          prompt_template: "x",
        },
      ],
      edges: [],
      sub_graphs: {},
      entry_points: ["n1"],
      exit_points: ["n1"],
      shared_context: [],
      artifact_refs: [],
    };

    const derived = deriveGraphRevisionSource({
      baseGraph,
      nodes: [
        {
          id: "n1",
          type: "danNode",
          position: { x: 0, y: 0 },
          data: { ...baseGraph.nodes[0], name: "Unsaved name" },
        } as never,
      ],
      edges: [],
      layerStack: [],
      loopGroups: [],
    });

    expect(derived?.nodes[0]?.name).toBe("Unsaved name");
  });
});
