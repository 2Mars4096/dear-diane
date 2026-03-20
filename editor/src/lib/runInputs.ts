import type { DanGraph, DanNode } from "../types/graph";

/**
 * Extract run-time input variable names from entry nodes and explicit input nodes.
 */
export function detectRunInputVariables(graph: DanGraph): string[] {
  const vars = new Set<string>();
  const entryIds = new Set(graph.entry_points);

  for (const node of graph.nodes) {
    if (node.node_type === "input") {
      const inputNode = node as DanNode & {
        variables?: Array<{ name?: string }>;
      };
      for (const variable of inputNode.variables ?? []) {
        const name = String(variable?.name ?? "").trim();
        if (name) vars.add(name);
      }
      continue;
    }

    if (!entryIds.has(node.id)) continue;

    if (node.node_type === "llm_operator") {
      const llm = node as DanNode & { prompt_template?: string };
      const prompt = llm.prompt_template ?? "";
      const matches = prompt.matchAll(/(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})/g);
      for (const m of matches) {
        vars.add(m[1]);
      }
    }

    for (const port of node.input_ports) {
      const hasIncomingEdge = graph.edges.some(
        (e) => e.target_node_id === node.id && e.target_port === port.name,
      );
      if (!hasIncomingEdge) {
        vars.add(port.name);
      }
    }
  }

  return [...vars].sort();
}
