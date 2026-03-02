import { describe, it, expect } from "vitest";
import {
  resolveGraphAtStack,
  deepSetSubGraph,
  type LayerStackEntry,
  MAX_DRILL_DEPTH,
} from "../graphAdapter";
import type { DanGraph } from "../../types/graph";

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
