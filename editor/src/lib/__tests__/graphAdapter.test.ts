import { describe, it, expect } from "vitest";
import {
  createDefaultNode,
  getDrillTargets,
  resolveGraphAtStack,
  deepSetSubGraph,
  type LayerStackEntry,
  MAX_DRILL_DEPTH,
} from "../graphAdapter";
import { PALETTE_NODE_TO_RUNTIME_NODE_TYPE, type DanGraph } from "../../types/graph";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function makeGraph(name: string, subGraphs: Record<string, DanGraph> = {}): DanGraph {
  return {
    version: "dan_graph_v1",
    metadata: { name },
    nodes: [],
    edges: [],
    sub_graphs: subGraphs,
    entry_points: [],
    exit_points: [],
    shared_context: [],
    artifact_refs: [],
  };
}

function entry(graphKey: string): LayerStackEntry {
  return { graphKey, nodeId: `node_${graphKey}` };
}

/* ------------------------------------------------------------------ */
/*  resolveGraphAtStack                                                */
/* ------------------------------------------------------------------ */

describe("resolveGraphAtStack", () => {
  const level3 = makeGraph("level-3");
  const level2 = makeGraph("level-2", { body3: level3 });
  const level1 = makeGraph("level-1", { body2: level2 });
  const root = makeGraph("root", { body1: level1 });

  it("empty stack returns root", () => {
    expect(resolveGraphAtStack(root, [])).toBe(root);
  });

  it("depth-1: valid key resolves correctly", () => {
    const result = resolveGraphAtStack(root, [entry("body1")]);
    expect(result).toBe(level1);
    expect(result!.metadata.name).toBe("level-1");
  });

  it("depth-2: nested key resolves correctly", () => {
    const result = resolveGraphAtStack(root, [entry("body1"), entry("body2")]);
    expect(result).toBe(level2);
    expect(result!.metadata.name).toBe("level-2");
  });

  it("depth-3: maximum allowed depth", () => {
    expect(MAX_DRILL_DEPTH).toBe(3);
    const result = resolveGraphAtStack(root, [
      entry("body1"),
      entry("body2"),
      entry("body3"),
    ]);
    expect(result).toBe(level3);
    expect(result!.metadata.name).toBe("level-3");
  });

  it("depth-4: exceeds cap → null", () => {
    const level4 = makeGraph("level-4");
    const root4 = makeGraph("root", {
      b1: makeGraph("l1", {
        b2: makeGraph("l2", {
          b3: makeGraph("l3", { b4: level4 }),
        }),
      }),
    });
    const result = resolveGraphAtStack(root4, [
      entry("b1"),
      entry("b2"),
      entry("b3"),
      entry("b4"),
    ]);
    expect(result).toBeNull();
  });

  it("invalid key at any level → null", () => {
    expect(resolveGraphAtStack(root, [entry("nonexistent")])).toBeNull();
  });

  it("invalid key at depth-2 → null", () => {
    expect(
      resolveGraphAtStack(root, [entry("body1"), entry("bad_key")])
    ).toBeNull();
  });

  it("root with no sub_graphs, non-empty stack → null", () => {
    const bare = makeGraph("bare");
    expect(resolveGraphAtStack(bare, [entry("anything")])).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  deepSetSubGraph                                                    */
/* ------------------------------------------------------------------ */

describe("deepSetSubGraph", () => {
  const level2 = makeGraph("level-2-original");
  const level1 = makeGraph("level-1", { body2: level2 });
  const root = makeGraph("root", { body1: level1 });

  it("empty stack replaces entire root", () => {
    const replacement = makeGraph("replacement");
    const result = deepSetSubGraph(root, [], replacement);
    expect(result).toBe(replacement);
  });

  it("depth-1 update", () => {
    const updated = makeGraph("level-1-updated");
    const result = deepSetSubGraph(root, [entry("body1")], updated);

    expect(result.sub_graphs.body1).toBe(updated);
    expect(result.metadata.name).toBe("root");
  });

  it("depth-2 update", () => {
    const updated = makeGraph("level-2-updated");
    const result = deepSetSubGraph(
      root,
      [entry("body1"), entry("body2")],
      updated
    );

    const resolvedL1 = result.sub_graphs.body1 as unknown as DanGraph;
    expect(resolvedL1.sub_graphs.body2).toBe(updated);
    expect(resolvedL1.metadata.name).toBe("level-1");
    expect(result.metadata.name).toBe("root");
  });

  it("depth-3 update", () => {
    const level3 = makeGraph("level-3");
    const deep2 = makeGraph("deep-2", { body3: level3 });
    const deep1 = makeGraph("deep-1", { body2: deep2 });
    const deepRoot = makeGraph("deep-root", { body1: deep1 });

    const updated = makeGraph("level-3-updated");
    const result = deepSetSubGraph(
      deepRoot,
      [entry("body1"), entry("body2"), entry("body3")],
      updated
    );

    const r1 = result.sub_graphs.body1 as unknown as DanGraph;
    const r2 = r1.sub_graphs.body2 as unknown as DanGraph;
    expect(r2.sub_graphs.body3).toBe(updated);
  });

  it("immutability: original root is unchanged", () => {
    const originalName = root.metadata.name;
    const originalL1 = root.sub_graphs.body1 as unknown as DanGraph;
    const originalL2Name = (originalL1.sub_graphs.body2 as unknown as DanGraph).metadata.name;

    const updated = makeGraph("CHANGED");
    deepSetSubGraph(root, [entry("body1"), entry("body2")], updated);

    expect(root.metadata.name).toBe(originalName);
    const afterL1 = root.sub_graphs.body1 as unknown as DanGraph;
    const afterL2 = afterL1.sub_graphs.body2 as unknown as DanGraph;
    expect(afterL2.metadata.name).toBe(originalL2Name);
  });

  it("round-trip: deepSet then resolve returns updated graph", () => {
    const updated = makeGraph("round-trip-target");
    const stack: LayerStackEntry[] = [entry("body1"), entry("body2")];
    const newRoot = deepSetSubGraph(root, stack, updated);
    const resolved = resolveGraphAtStack(newRoot, stack);
    expect(resolved).toBe(updated);
  });
});

/* ------------------------------------------------------------------ */
/*  createDefaultNode                                                  */
/* ------------------------------------------------------------------ */

describe("createDefaultNode", () => {
  it("exposes explicit palette-to-runtime node lowering", () => {
    expect(PALETTE_NODE_TO_RUNTIME_NODE_TYPE.gate_if_else).toBe("gate");
    expect(PALETTE_NODE_TO_RUNTIME_NODE_TYPE.gate_while).toBe("gate");
    expect(PALETTE_NODE_TO_RUNTIME_NODE_TYPE.human).toBe("human");
    expect(PALETTE_NODE_TO_RUNTIME_NODE_TYPE.goal_loop).toBe("goal_loop");
    expect(PALETTE_NODE_TO_RUNTIME_NODE_TYPE.vote).toBe("vote");
    expect(PALETTE_NODE_TO_RUNTIME_NODE_TYPE.reflection).toBe("reflection");
    expect(PALETTE_NODE_TO_RUNTIME_NODE_TYPE.orchestrator).toBe("orchestrator");
    expect(PALETTE_NODE_TO_RUNTIME_NODE_TYPE.agent_team).toBe("agent_team");
  });

  it("lowers palette gate_if_else to runtime gate", () => {
    const node = createDefaultNode("gate_if_else", { x: 10, y: 20 });
    expect(node.node_type).toBe("gate");
    expect("gate_mode" in node && node.gate_mode).toBe("if_else");
    expect(node.output_ports.map((p) => p.name)).toEqual(["true", "false"]);
  });

  it("lowers palette gate_while to runtime gate", () => {
    const node = createDefaultNode("gate_while", { x: 10, y: 20 });
    expect(node.node_type).toBe("gate");
    expect("gate_mode" in node && node.gate_mode).toBe("while");
    expect(node.output_ports.map((p) => p.name)).toEqual(["continue", "done"]);
  });

  it("creates canonical human nodes for the palette human entry", () => {
    const node = createDefaultNode("human", { x: 10, y: 20 });
    expect(node.node_type).toBe("human");
    expect(node.output_ports.map((p) => p.name)).toEqual(["response"]);
    expect("render_mode" in node && node.render_mode).toBe("text");
    expect("render_target" in node && node.render_target).toBe("dialog");
  });

  it("creates goal_loop nodes with canonical defaults", () => {
    const node = createDefaultNode("goal_loop", { x: 10, y: 20 });
    expect(node.node_type).toBe("goal_loop");
    expect("metric_name" in node && node.metric_name).toBe("score");
    expect("body_graph" in node && node.body_graph).toBe("");
    expect(node.output_ports.map((p) => p.name)).toEqual([
      "result",
      "goal_met",
      "iterations",
      "best_score",
    ]);
  });

  it("creates vote nodes with canonical defaults", () => {
    const node = createDefaultNode("vote", { x: 10, y: 20 });
    expect(node.node_type).toBe("vote");
    expect("candidates" in node && node.candidates).toEqual(["claude-sonnet-4-6"]);
    expect("vote_strategy" in node && node.vote_strategy).toBe("majority");
    expect(node.output_ports.map((p) => p.name)).toContain("winner");
  });

  it("creates reflection nodes with canonical defaults", () => {
    const node = createDefaultNode("reflection", { x: 10, y: 20 });
    expect(node.node_type).toBe("reflection");
    expect("source" in node && node.source).toBe("last_run");
    expect("output_format" in node && node.output_format).toBe("principles");
    expect(node.output_ports.map((p) => p.name)).toEqual([
      "principles",
      "principle_count",
      "source",
      "text",
    ]);
  });

  it("creates orchestrator nodes with canonical defaults", () => {
    const node = createDefaultNode("orchestrator", { x: 10, y: 20 });
    expect(node.node_type).toBe("orchestrator");
    expect("teams" in node && node.teams).toEqual({});
    expect("completion_condition" in node && node.completion_condition).toBe("all_done");
    expect(node.output_ports.map((p) => p.name)).toEqual(["results"]);
  });

  it("creates agent_team nodes with canonical defaults", () => {
    const node = createDefaultNode("agent_team", { x: 10, y: 20 });
    expect(node.node_type).toBe("agent_team");
    expect("agents" in node && node.agents).toEqual({});
    expect("turn_strategy" in node && node.turn_strategy).toBe("round_robin");
    expect(node.output_ports.map((p) => p.name)).toEqual([
      "result",
      "agent_contributions",
      "consensus_reached",
      "total_turns",
      "conversation",
    ]);
  });
});

/* ------------------------------------------------------------------ */
/*  getDrillTargets                                                    */
/* ------------------------------------------------------------------ */

describe("getDrillTargets", () => {
  it("returns body graph targets for body-bearing nodes", () => {
    const node = {
      id: "goal",
      node_type: "goal_loop",
      name: "Goal Loop",
      body_graph: "goal_body",
      input_ports: [],
      output_ports: [],
      position: { x: 0, y: 0 },
      ui: {},
      metadata: {},
      goal_text: "",
    } as any;

    expect(getDrillTargets(node)).toEqual([{ label: "body", graphKey: "goal_body" }]);
  });

  it("returns branch targets for parallel_subagents", () => {
    const node = {
      id: "parallel",
      node_type: "parallel_subagents",
      name: "Parallel",
      branch_graphs: ["parallel_researcher", "parallel_writer"],
      input_ports: [],
      output_ports: [],
      position: { x: 0, y: 0 },
      ui: {},
      metadata: {},
    } as any;

    expect(getDrillTargets(node)).toEqual([
      { label: "researcher", graphKey: "parallel_researcher" },
      { label: "writer", graphKey: "parallel_writer" },
    ]);
  });

  it("returns team targets for orchestrator nodes", () => {
    const node = {
      id: "coord",
      node_type: "orchestrator",
      name: "Orchestrator",
      teams: { researcher: "coord_researcher", analyst: "coord_analyst" },
      input_ports: [],
      output_ports: [],
      position: { x: 0, y: 0 },
      ui: {},
      metadata: {},
    } as any;

    expect(getDrillTargets(node)).toEqual([
      { label: "researcher", graphKey: "coord_researcher" },
      { label: "analyst", graphKey: "coord_analyst" },
    ]);
  });

  it("returns agent targets for agent_team nodes", () => {
    const node = {
      id: "team",
      node_type: "agent_team",
      name: "Team",
      agents: { researcher: "team_researcher", writer: "team_writer" },
      input_ports: [],
      output_ports: [],
      position: { x: 0, y: 0 },
      ui: {},
      metadata: {},
    } as any;

    expect(getDrillTargets(node)).toEqual([
      { label: "researcher", graphKey: "team_researcher" },
      { label: "writer", graphKey: "team_writer" },
    ]);
  });
});
