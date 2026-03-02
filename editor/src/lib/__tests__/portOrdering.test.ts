import { describe, it, expect } from "vitest";
import { orderPorts } from "../portOrdering";
import type { Node, Edge } from "@xyflow/react";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

interface PortDef {
  name: string;
  schema?: Record<string, unknown>;
  required?: boolean;
}

function port(name: string): PortDef {
  return { name, schema: {} };
}

function makeNode(id: string, y: number): Node {
  return { id, type: "danNode", position: { x: 0, y }, data: {} };
}

function makeEdge(
  sourceId: string,
  sourcePort: string,
  targetId: string,
  targetPort: string
): Edge {
  return {
    id: `${sourceId}-${targetId}-${sourcePort}-${targetPort}`,
    source: sourceId,
    target: targetId,
    sourceHandle: `port:${sourcePort}`,
    targetHandle: `port:${targetPort}`,
  };
}

/* ------------------------------------------------------------------ */
/*  Gate pin ordering (P0)                                             */
/* ------------------------------------------------------------------ */

describe("orderPorts — gate pin ordering", () => {
  it("output: true/continue pinned first, false/done second", () => {
    const ports = [port("false"), port("true")];
    const result = orderPorts(ports, [], [], "gate1", "output", "gate");
    expect(result.map((p) => p.name)).toEqual(["true", "false"]);
  });

  it("output: continue/done ordering for while gate", () => {
    const ports = [port("done"), port("continue")];
    const result = orderPorts(ports, [], [], "gate1", "output", "gate");
    expect(result.map((p) => p.name)).toEqual(["continue", "done"]);
  });

  it("input: 'input' pinned for gate", () => {
    const ports = [port("extra"), port("input")];
    const result = orderPorts(ports, [], [], "gate1", "input", "gate");
    expect(result.map((p) => p.name)).toEqual(["input", "extra"]);
  });
});

/* ------------------------------------------------------------------ */
/*  Connected ports sorted by peer Y (P1)                              */
/* ------------------------------------------------------------------ */

describe("orderPorts — connected ports by peer Y", () => {
  it("output ports sorted by target node Y position", () => {
    const nodeId = "src";
    const nodes = [makeNode("src", 0), makeNode("t1", 300), makeNode("t2", 100)];
    const edges = [
      makeEdge("src", "alpha", "t1", "in"),
      makeEdge("src", "beta", "t2", "in"),
    ];
    const ports = [port("alpha"), port("beta")];
    const result = orderPorts(ports, edges, nodes, nodeId, "output");
    expect(result.map((p) => p.name)).toEqual(["beta", "alpha"]);
  });

  it("input ports sorted by source node Y position", () => {
    const nodeId = "tgt";
    const nodes = [makeNode("s1", 200), makeNode("s2", 50), makeNode("tgt", 0)];
    const edges = [
      makeEdge("s1", "out", "tgt", "a"),
      makeEdge("s2", "out", "tgt", "b"),
    ];
    const ports = [port("a"), port("b")];
    const result = orderPorts(ports, edges, nodes, nodeId, "input");
    expect(result.map((p) => p.name)).toEqual(["b", "a"]);
  });
});

/* ------------------------------------------------------------------ */
/*  Unconnected ports (P2)                                             */
/* ------------------------------------------------------------------ */

describe("orderPorts — unconnected ports", () => {
  it("unconnected ports sort after connected, alphabetically among themselves", () => {
    const nodes = [makeNode("src", 0), makeNode("t1", 100)];
    const edges = [makeEdge("src", "connected", "t1", "in")];
    const ports = [port("zebra"), port("connected"), port("apple")];
    const result = orderPorts(ports, edges, nodes, "src", "output");
    expect(result.map((p) => p.name)).toEqual(["connected", "apple", "zebra"]);
  });

  it("all unconnected ports sort alphabetically", () => {
    const ports = [port("charlie"), port("alpha"), port("bravo")];
    const result = orderPorts(ports, [], [], "node1", "output");
    expect(result.map((p) => p.name)).toEqual(["alpha", "bravo", "charlie"]);
  });
});

/* ------------------------------------------------------------------ */
/*  Deterministic tie-breaking                                         */
/* ------------------------------------------------------------------ */

describe("orderPorts — deterministic tie-breaking", () => {
  it("same peer Y: alphabetical fallback", () => {
    const nodes = [makeNode("src", 0), makeNode("t1", 100)];
    const edges = [
      makeEdge("src", "beta", "t1", "in1"),
      makeEdge("src", "alpha", "t1", "in2"),
    ];
    const ports = [port("beta"), port("alpha")];
    const result = orderPorts(ports, edges, nodes, "src", "output");
    expect(result.map((p) => p.name)).toEqual(["alpha", "beta"]);
  });

  it("single port returned as-is", () => {
    const ports = [port("only")];
    const result = orderPorts(ports, [], [], "n", "output");
    expect(result).toEqual(ports);
  });
});

/* ------------------------------------------------------------------ */
/*  portReorder hint (crossing minimization)                           */
/* ------------------------------------------------------------------ */

describe("orderPorts — portReorder hint", () => {
  it("portReorder overrides peer Y for listed ports", () => {
    const nodes = [makeNode("src", 0), makeNode("t1", 100), makeNode("t2", 200)];
    const edges = [
      makeEdge("src", "a", "t1", "in"),
      makeEdge("src", "b", "t2", "in"),
    ];
    const ports = [port("a"), port("b")];
    const result = orderPorts(ports, edges, nodes, "src", "output", undefined, [
      "b",
      "a",
    ]);
    expect(result.map((p) => p.name)).toEqual(["b", "a"]);
  });
});

/* ------------------------------------------------------------------ */
/*  Mixed bands                                                        */
/* ------------------------------------------------------------------ */

describe("orderPorts — mixed P0 + P1 + P2", () => {
  it("gate pins first, then connected, then unconnected", () => {
    const nodes = [makeNode("gate1", 0), makeNode("t1", 50)];
    const edges = [makeEdge("gate1", "extra_connected", "t1", "in")];
    const ports = [
      port("unconnected_z"),
      port("extra_connected"),
      port("false"),
      port("true"),
    ];
    const result = orderPorts(ports, edges, nodes, "gate1", "output", "gate");
    expect(result.map((p) => p.name)).toEqual([
      "true",
      "false",
      "extra_connected",
      "unconnected_z",
    ]);
  });
});
