import type { Edge, Node } from "@xyflow/react";

import type {
  DanGraph,
  LoopGroup,
  OptimizationMutation,
  TierInfo,
  TokenBreakdown,
  WasteFinding,
} from "../types/graph";

export interface GraphSnapshot {
  nodes: Node[];
  edges: Edge[];
  danGraph: DanGraph | null;
}

interface GraphLogEntry {
  timestamp: number;
  event_type: string;
  node_id?: string;
  message: string;
  data?: Record<string, unknown>;
}

export interface TabInfo {
  id: string;
  graphId: string;
  graphName: string;
  cachedRunStatus: string | null;
}

export interface TabSnapshot {
  graphId: string | null;
  danGraph: DanGraph | null;
  graphRevision: string | null;
  dirty: boolean;
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  selectedNodeIds: Set<string>;
  layerStack: Array<{ graphKey: string; nodeId: string; nodeName?: string }>;
  validationErrors: Record<string, string[]>;
  inputNodeValues: Record<string, Record<string, unknown>>;
  nodeTimings: Record<string, { start: number; end?: number }>;
  activeExecutionPath: Set<string>;
  nodeIterations: Record<string, { current: number; total?: number; condition?: string }>;
  streamingOutputs: Record<string, string>;
  pendingHumanInput: { requestId: string; nodeId: string; prompt: string } | null;
  _history: { past: GraphSnapshot[]; future: GraphSnapshot[] };
  runId: string | null;
  runStatus: string | null;
  nodeStatuses: Record<string, string>;
  nodeOutputs: Record<string, Record<string, unknown>>;
  logs: GraphLogEntry[];
  loopGroups: LoopGroup[];
  runSummary: {
    elapsed_seconds?: number;
    total_prompt_tokens?: number;
    total_completion_tokens?: number;
    total_tokens?: number;
  } | null;
  nodeUsage: Record<
    string,
    { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  >;
  nodeCosts: Record<string, number>;
  tokenBreakdowns: Record<string, TokenBreakdown>;
  wasteFindings: WasteFinding[];
  optimizationMutations: OptimizationMutation[];
  edgeTokenCounts: Record<string, number>;
  nodeTiers: Record<string, TierInfo>;
}

export interface GraphTabSnapshotSource {
  graphId: string | null;
  danGraph: DanGraph | null;
  graphRevision: string | null;
  dirty: boolean;
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  selectedNodeIds: Set<string>;
  layerStack: Array<{ graphKey: string; nodeId: string; nodeName?: string }>;
  validationErrors: Record<string, string[]>;
  inputNodeValues: Record<string, Record<string, unknown>>;
  nodeTimings: Record<string, { start: number; end?: number }>;
  activeExecutionPath: Set<string>;
  nodeIterations: Record<string, { current: number; total?: number; condition?: string }>;
  streamingOutputs: Record<string, string>;
  pendingHumanInput: { requestId: string; nodeId: string; prompt: string } | null;
  _history: { past: GraphSnapshot[]; future: GraphSnapshot[] };
  runId: string | null;
  runStatus: string | null;
  nodeStatuses: Record<string, string>;
  nodeOutputs: Record<string, Record<string, unknown>>;
  logs: GraphLogEntry[];
  loopGroups: LoopGroup[];
  runSummary: TabSnapshot["runSummary"];
  nodeUsage: TabSnapshot["nodeUsage"];
  nodeCosts: Record<string, number>;
  tokenBreakdowns: Record<string, TokenBreakdown>;
  wasteFindings: WasteFinding[];
  optimizationMutations: OptimizationMutation[];
  edgeTokenCounts: Record<string, number>;
  nodeTiers: Record<string, TierInfo>;
}

interface PersistedTabSession {
  tabs: TabInfo[];
  activeTabId: string | null;
  runs: Record<string, { runId: string | null; graphId: string }>;
}

export function snapshotGraphTab(
  state: GraphTabSnapshotSource,
): TabSnapshot {
  return {
    graphId: state.graphId,
    danGraph: state.danGraph ? structuredClone(state.danGraph) : null,
    graphRevision: state.graphRevision,
    dirty: state.dirty,
    nodes: structuredClone(state.nodes),
    edges: structuredClone(state.edges),
    selectedNodeId: state.selectedNodeId,
    selectedEdgeId: state.selectedEdgeId,
    selectedNodeIds: new Set(state.selectedNodeIds),
    layerStack: structuredClone(state.layerStack),
    validationErrors: { ...state.validationErrors },
    inputNodeValues: structuredClone(state.inputNodeValues),
    nodeTimings: { ...state.nodeTimings },
    activeExecutionPath: new Set(state.activeExecutionPath),
    nodeIterations: structuredClone(state.nodeIterations),
    streamingOutputs: { ...state.streamingOutputs },
    pendingHumanInput: state.pendingHumanInput
      ? { ...state.pendingHumanInput }
      : null,
    _history: structuredClone(state._history),
    runId: state.runId,
    runStatus: state.runStatus,
    nodeStatuses: { ...state.nodeStatuses },
    nodeOutputs: structuredClone(state.nodeOutputs),
    logs: [...state.logs],
    loopGroups: structuredClone(state.loopGroups),
    runSummary: state.runSummary ? { ...state.runSummary } : null,
    nodeUsage: { ...state.nodeUsage },
    nodeCosts: { ...state.nodeCosts },
    tokenBreakdowns: { ...state.tokenBreakdowns },
    wasteFindings: [...state.wasteFindings],
    optimizationMutations: [...state.optimizationMutations],
    edgeTokenCounts: { ...state.edgeTokenCounts },
    nodeTiers: { ...state.nodeTiers },
  };
}

export function restoreGraphTab(
  snapshot: TabSnapshot,
): Pick<
  GraphTabSnapshotSource,
  | "graphId"
  | "danGraph"
  | "graphRevision"
  | "dirty"
  | "nodes"
  | "edges"
  | "selectedNodeId"
  | "selectedEdgeId"
  | "selectedNodeIds"
  | "layerStack"
  | "validationErrors"
  | "inputNodeValues"
  | "nodeTimings"
  | "activeExecutionPath"
  | "nodeIterations"
  | "streamingOutputs"
  | "pendingHumanInput"
  | "_history"
  | "runId"
  | "runStatus"
  | "nodeStatuses"
  | "nodeOutputs"
  | "logs"
  | "loopGroups"
  | "runSummary"
  | "nodeUsage"
  | "nodeCosts"
  | "tokenBreakdowns"
  | "wasteFindings"
  | "optimizationMutations"
  | "edgeTokenCounts"
  | "nodeTiers"
> {
  return {
    graphId: snapshot.graphId,
    danGraph: snapshot.danGraph,
    graphRevision: snapshot.graphRevision,
    dirty: snapshot.dirty,
    nodes: snapshot.nodes,
    edges: snapshot.edges,
    selectedNodeId: snapshot.selectedNodeId,
    selectedEdgeId: snapshot.selectedEdgeId,
    selectedNodeIds: snapshot.selectedNodeIds,
    layerStack: snapshot.layerStack,
    validationErrors: snapshot.validationErrors,
    inputNodeValues: snapshot.inputNodeValues,
    nodeTimings: snapshot.nodeTimings,
    activeExecutionPath: snapshot.activeExecutionPath,
    nodeIterations: snapshot.nodeIterations,
    streamingOutputs: snapshot.streamingOutputs,
    pendingHumanInput: snapshot.pendingHumanInput,
    _history: snapshot._history,
    runId: snapshot.runId,
    runStatus: snapshot.runStatus,
    nodeStatuses: snapshot.nodeStatuses,
    nodeOutputs: snapshot.nodeOutputs,
    logs: snapshot.logs,
    loopGroups: snapshot.loopGroups ?? [],
    runSummary: snapshot.runSummary ?? null,
    nodeUsage: snapshot.nodeUsage ?? {},
    nodeCosts: snapshot.nodeCosts ?? {},
    tokenBreakdowns: snapshot.tokenBreakdowns ?? {},
    wasteFindings: snapshot.wasteFindings ?? [],
    optimizationMutations: snapshot.optimizationMutations ?? [],
    edgeTokenCounts: snapshot.edgeTokenCounts ?? {},
    nodeTiers: snapshot.nodeTiers ?? {},
  };
}

export function createEmptyGraphRunState() {
  return {
    runId: null,
    runStatus: null,
    nodeStatuses: {},
    nodeOutputs: {},
    logs: [],
    runSummary: null,
    nodeTimings: {},
    nodeUsage: {},
    nodeCosts: {},
    edgeTokenCounts: {},
    activeExecutionPath: new Set<string>(),
    nodeIterations: {},
    streamingOutputs: {},
    pendingHumanInput: null,
    tokenBreakdowns: {},
    wasteFindings: [],
    optimizationMutations: [],
    nodeTiers: {},
  };
}

export function createEmptyGraphEditorState() {
  return {
    ...createEmptyGraphRunState(),
    _history: { past: [], future: [] } as {
      past: GraphSnapshot[];
      future: GraphSnapshot[];
    },
    validationErrors: {},
    inputNodeValues: {},
    selectedNodeId: null,
    selectedEdgeId: null,
    selectedNodeIds: new Set<string>(),
    layerStack: [] as Array<{ graphKey: string; nodeId: string; nodeName?: string }>,
    loopGroups: [] as LoopGroup[],
  };
}

export function closeGraphRunSocket(ws: WebSocket | null): void {
  if (!ws) return;
  ws.onmessage = null;
  ws.onclose = null;
  ws.close();
}

export function persistGraphTabState(args: {
  tabs: TabInfo[];
  activeTabId: string | null;
  tabCache: Record<string, TabSnapshot>;
  runId: string | null;
}): void {
  try {
    const runs: Record<string, { runId: string | null; graphId: string }> = {};
    for (const tab of args.tabs) {
      if (tab.id === args.activeTabId) {
        runs[tab.id] = { runId: args.runId, graphId: tab.graphId };
      } else {
        runs[tab.id] = {
          runId: args.tabCache[tab.id]?.runId ?? null,
          graphId: tab.graphId,
        };
      }
    }
    sessionStorage.setItem(
      "dan_open_tabs",
      JSON.stringify({
        tabs: args.tabs,
        activeTabId: args.activeTabId,
        runs,
      }),
    );
  } catch {
    /* quota */
  }
}

export function readPersistedGraphTabState(): PersistedTabSession | null {
  try {
    const raw = sessionStorage.getItem("dan_open_tabs");
    return raw ? (JSON.parse(raw) as PersistedTabSession) : null;
  } catch {
    return null;
  }
}
