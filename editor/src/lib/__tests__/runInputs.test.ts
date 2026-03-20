import { describe, expect, it } from "vitest";

import { detectRunInputVariables } from "../runInputs";
import type { DanGraph } from "../../types/graph";

describe("detectRunInputVariables", () => {
  it("includes explicit input-node variables", () => {
    const graph: DanGraph = {
      version: "dan_graph_v1",
      metadata: { name: "wf", description: "" },
      nodes: [
        {
          id: "workflow_input",
          node_type: "input",
          name: "Workflow Input",
          description: "",
          position: { x: 0, y: 0 },
          input_ports: [],
          output_ports: [{ name: "input", schema: {} }],
          ui: {},
          metadata: {},
          variables: [
            { name: "watchlist_path", type: "string", default: "", description: "" },
          ],
        },
      ],
      edges: [],
      sub_graphs: {},
      entry_points: ["workflow_input"],
      exit_points: ["workflow_input"],
      shared_context: [],
      artifact_refs: [],
    };

    expect(detectRunInputVariables(graph)).toEqual(["watchlist_path"]);
  });

  it("still includes inferred entry-node variables", () => {
    const graph: DanGraph = {
      version: "dan_graph_v1",
      metadata: { name: "wf", description: "" },
      nodes: [
        {
          id: "writer",
          node_type: "llm_operator",
          name: "Writer",
          description: "",
          position: { x: 0, y: 0 },
          input_ports: [{ name: "topic", schema: {}, required: false }],
          output_ports: [{ name: "text", schema: {} }],
          ui: {},
          metadata: {},
          prompt_template: "Write about {topic} for {audience}",
          model: "mock-model",
        },
      ],
      edges: [],
      sub_graphs: {},
      entry_points: ["writer"],
      exit_points: ["writer"],
      shared_context: [],
      artifact_refs: [],
    };

    expect(detectRunInputVariables(graph)).toEqual(["audience", "topic"]);
  });
});
