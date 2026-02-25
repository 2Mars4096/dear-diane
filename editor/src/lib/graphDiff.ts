export interface FieldDiff {
  field: string;
  oldValue: unknown;
  newValue: unknown;
}

export interface NodeDiff {
  nodeId: string;
  name: string;
  nodeType: string;
  status: "added" | "removed" | "modified";
  fieldDiffs?: FieldDiff[];
}

export interface EdgeDiff {
  edgeKey: string;
  edgeType: string;
  sourceId: string;
  sourcePort: string;
  targetId: string;
  targetPort: string;
  status: "added" | "removed" | "modified";
  fieldDiffs?: FieldDiff[];
}

export interface GraphDiff {
  addedNodes: NodeDiff[];
  removedNodes: NodeDiff[];
  modifiedNodes: NodeDiff[];
  addedEdges: EdgeDiff[];
  removedEdges: EdgeDiff[];
  modifiedEdges: EdgeDiff[];
  summary: string;
}

const COMPARED_NODE_FIELDS = [
  "name",
  "node_type",
  "model",
  "prompt_template",
  "system_prompt",
  "code",
  "condition",
  "tool_id",
] as const;

const SKIPPED_NODE_FIELDS = new Set([
  "position",
  "ui",
  "metadata",
  "id",
  "input_ports",
  "output_ports",
]);

function edgeKey(e: Record<string, unknown>): string {
  return `${e.source_node_id}.${e.source_port}->${e.target_node_id}.${e.target_port}`;
}

function diffFields(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
  fields: readonly string[],
): FieldDiff[] {
  const diffs: FieldDiff[] = [];
  for (const f of fields) {
    const ov = a[f];
    const nv = b[f];
    if (JSON.stringify(ov) !== JSON.stringify(nv)) {
      diffs.push({ field: f, oldValue: ov, newValue: nv });
    }
  }
  return diffs;
}

function allComparableFields(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): string[] {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const s of SKIPPED_NODE_FIELDS) keys.delete(s);
  return [...keys];
}

export function computeGraphDiff(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
): GraphDiff {
  const beforeNodes = (
    (before.nodes as Record<string, unknown>[]) ?? []
  ).reduce(
    (m, n) => {
      m.set(n.id as string, n);
      return m;
    },
    new Map<string, Record<string, unknown>>(),
  );

  const afterNodes = (
    (after.nodes as Record<string, unknown>[]) ?? []
  ).reduce(
    (m, n) => {
      m.set(n.id as string, n);
      return m;
    },
    new Map<string, Record<string, unknown>>(),
  );

  const addedNodes: NodeDiff[] = [];
  const removedNodes: NodeDiff[] = [];
  const modifiedNodes: NodeDiff[] = [];

  for (const [id, node] of afterNodes) {
    if (!beforeNodes.has(id)) {
      addedNodes.push({
        nodeId: id,
        name: (node.name as string) ?? id,
        nodeType: (node.node_type as string) ?? "unknown",
        status: "added",
      });
    }
  }

  for (const [id, node] of beforeNodes) {
    if (!afterNodes.has(id)) {
      removedNodes.push({
        nodeId: id,
        name: (node.name as string) ?? id,
        nodeType: (node.node_type as string) ?? "unknown",
        status: "removed",
      });
    }
  }

  for (const [id, afterNode] of afterNodes) {
    const beforeNode = beforeNodes.get(id);
    if (!beforeNode) continue;
    const fields = allComparableFields(beforeNode, afterNode);
    const prioritized = [
      ...COMPARED_NODE_FIELDS.filter((f) => fields.includes(f)),
      ...fields.filter(
        (f) => !(COMPARED_NODE_FIELDS as readonly string[]).includes(f),
      ),
    ];
    const fd = diffFields(beforeNode, afterNode, prioritized);
    if (fd.length > 0) {
      modifiedNodes.push({
        nodeId: id,
        name: (afterNode.name as string) ?? id,
        nodeType: (afterNode.node_type as string) ?? "unknown",
        status: "modified",
        fieldDiffs: fd,
      });
    }
  }

  const beforeEdges = (
    (before.edges as Record<string, unknown>[]) ?? []
  ).reduce(
    (m, e) => {
      m.set(edgeKey(e), e);
      return m;
    },
    new Map<string, Record<string, unknown>>(),
  );

  const afterEdges = (
    (after.edges as Record<string, unknown>[]) ?? []
  ).reduce(
    (m, e) => {
      m.set(edgeKey(e), e);
      return m;
    },
    new Map<string, Record<string, unknown>>(),
  );

  const addedEdges: EdgeDiff[] = [];
  const removedEdges: EdgeDiff[] = [];
  const modifiedEdges: EdgeDiff[] = [];

  function toEdgeDiff(
    key: string,
    e: Record<string, unknown>,
    status: EdgeDiff["status"],
    fieldDiffs?: FieldDiff[],
  ): EdgeDiff {
    return {
      edgeKey: key,
      edgeType: (e.edge_type as string) ?? "data",
      sourceId: e.source_node_id as string,
      sourcePort: e.source_port as string,
      targetId: e.target_node_id as string,
      targetPort: e.target_port as string,
      status,
      fieldDiffs,
    };
  }

  for (const [key, edge] of afterEdges) {
    if (!beforeEdges.has(key)) {
      addedEdges.push(toEdgeDiff(key, edge, "added"));
    }
  }

  for (const [key, edge] of beforeEdges) {
    if (!afterEdges.has(key)) {
      removedEdges.push(toEdgeDiff(key, edge, "removed"));
    }
  }

  for (const [key, afterEdge] of afterEdges) {
    const beforeEdge = beforeEdges.get(key);
    if (!beforeEdge) continue;
    const fd = diffFields(beforeEdge, afterEdge, ["edge_type", "condition", "context_key", "mode"]);
    if (fd.length > 0) {
      modifiedEdges.push(toEdgeDiff(key, afterEdge, "modified", fd));
    }
  }

  const parts: string[] = [];
  if (addedNodes.length) parts.push(`Add ${addedNodes.length} node${addedNodes.length > 1 ? "s" : ""}`);
  if (removedNodes.length) parts.push(`Remove ${removedNodes.length} node${removedNodes.length > 1 ? "s" : ""}`);
  if (modifiedNodes.length) parts.push(`Modify ${modifiedNodes.length} node${modifiedNodes.length > 1 ? "s" : ""}`);
  if (addedEdges.length) parts.push(`Add ${addedEdges.length} edge${addedEdges.length > 1 ? "s" : ""}`);
  if (removedEdges.length) parts.push(`Remove ${removedEdges.length} edge${removedEdges.length > 1 ? "s" : ""}`);
  if (modifiedEdges.length) parts.push(`Modify ${modifiedEdges.length} edge${modifiedEdges.length > 1 ? "s" : ""}`);
  const summary = parts.length > 0 ? parts.join(", ") : "No changes";

  return {
    addedNodes,
    removedNodes,
    modifiedNodes,
    addedEdges,
    removedEdges,
    modifiedEdges,
    summary,
  };
}

export function isEmptyDiff(diff: GraphDiff): boolean {
  return (
    diff.addedNodes.length === 0 &&
    diff.removedNodes.length === 0 &&
    diff.modifiedNodes.length === 0 &&
    diff.addedEdges.length === 0 &&
    diff.removedEdges.length === 0 &&
    diff.modifiedEdges.length === 0
  );
}
