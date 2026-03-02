import { describe, it, expect } from "vitest";
import { resolveGraphAtStack, deepSetSubGraph, type LayerStackEntry } from "../graphAdapter";
import type { DanGraph } from "../../types/graph";
import fixture from "./fixtures/nested-workflow.json";

const root = fixture as unknown as DanGraph;

function entry(graphKey: string, nodeId: string, nodeName?: string): LayerStackEntry {
  return { graphKey, nodeId, nodeName };
}

/* ------------------------------------------------------------------ */
/*  Integration: drill-in on realistic fixture                         */
/* ------------------------------------------------------------------ */

describe("integration — nested drill-in on fixture", () => {
  const stackDepth1: LayerStackEntry[] = [
    entry("iteration_loop__body", "iteration_loop", "Iteration Loop"),
  ];
  const stackDepth2: LayerStackEntry[] = [
    ...stackDepth1,
    entry("dept_composite__body", "dept_composite", "Department REV"),
  ];

  it("depth-0: root has 2 nodes", () => {
    const g = resolveGraphAtStack(root, []);
    expect(g).not.toBeNull();
    expect(g!.nodes).toHaveLength(2);
    expect(g!.metadata.name).toBe("vibe-research-mock");
  });

  it("depth-1: iteration body has dept_router + dept_composite", () => {
    const g = resolveGraphAtStack(root, stackDepth1);
    expect(g).not.toBeNull();
    expect(g!.metadata.name).toBe("iteration-body");
    expect(g!.nodes).toHaveLength(2);
    const ids = g!.nodes.map((n) => n.id);
    expect(ids).toContain("dept_router");
    expect(ids).toContain("dept_composite");
  });

  it("depth-2: dept body has strategy_coder + backtest_runner", () => {
    const g = resolveGraphAtStack(root, stackDepth2);
    expect(g).not.toBeNull();
    expect(g!.metadata.name).toBe("dept-REV-body");
    expect(g!.nodes).toHaveLength(2);
    const ids = g!.nodes.map((n) => n.id);
    expect(ids).toContain("strategy_coder");
    expect(ids).toContain("backtest_runner");
  });

  it("breadcrumb would show 3 segments at depth-2", () => {
    const segments = ["Root", ...stackDepth2.map((e) => e.nodeName ?? e.nodeId)];
    expect(segments).toEqual(["Root", "Iteration Loop", "Department REV"]);
    expect(segments).toHaveLength(3);
  });
});

/* ------------------------------------------------------------------ */
/*  Integration: save at depth-2, re-resolve                           */
/* ------------------------------------------------------------------ */

describe("integration — save at depth-2 and re-resolve", () => {
  const stackDepth2: LayerStackEntry[] = [
    entry("iteration_loop__body", "iteration_loop"),
    entry("dept_composite__body", "dept_composite"),
  ];

  it("modify dept body, deepSet, resolve returns updated graph", () => {
    const original = resolveGraphAtStack(root, stackDepth2)!;
    expect(original.nodes).toHaveLength(2);

    const newNode = {
      id: "bug_fixer",
      node_type: "llm_operator" as const,
      name: "Bug Fixer",
      input_ports: [{ name: "input", schema: {} }],
      output_ports: [{ name: "output", schema: {} }],
      position: { x: 600, y: 0 },
      model: "test-model",
      prompt_template: "Fix bugs",
      ui: {},
      metadata: {},
    };

    const updatedSub: DanGraph = {
      ...original,
      nodes: [...original.nodes, newNode],
    };

    const newRoot = deepSetSubGraph(root, stackDepth2, updatedSub);
    const resolved = resolveGraphAtStack(newRoot, stackDepth2)!;

    expect(resolved.nodes).toHaveLength(3);
    expect(resolved.nodes.map((n) => n.id)).toContain("bug_fixer");
  });

  it("save at depth-2 does not mutate depth-1 nodes", () => {
    const stackD1: LayerStackEntry[] = [
      entry("iteration_loop__body", "iteration_loop"),
    ];

    const original = resolveGraphAtStack(root, stackDepth2)!;
    const updatedSub: DanGraph = {
      ...original,
      metadata: { ...original.metadata, name: "modified-dept-body" },
    };

    const newRoot = deepSetSubGraph(root, stackDepth2, updatedSub);

    const depth1 = resolveGraphAtStack(newRoot, stackD1)!;
    expect(depth1.metadata.name).toBe("iteration-body");
    expect(depth1.nodes).toHaveLength(2);

    const depth2 = resolveGraphAtStack(newRoot, stackDepth2)!;
    expect(depth2.metadata.name).toBe("modified-dept-body");
  });

  it("original root is unchanged after save", () => {
    const original = resolveGraphAtStack(root, stackDepth2)!;
    const updatedSub: DanGraph = {
      ...original,
      metadata: { ...original.metadata, name: "should-not-affect-original" },
    };

    deepSetSubGraph(root, stackDepth2, updatedSub);

    const stillOriginal = resolveGraphAtStack(root, stackDepth2)!;
    expect(stillOriginal.metadata.name).toBe("dept-REV-body");
  });
});
