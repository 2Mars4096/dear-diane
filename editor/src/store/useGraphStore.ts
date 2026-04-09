/**
 * Zustand store — single source of truth for the editor.
 *
 * Manages: current graph, React Flow nodes/edges, selection,
 * run state, node execution statuses, and log buffer.
 */

import { create } from "zustand";
import {
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type Connection,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
  MarkerType,
} from "@xyflow/react";
import type { DanGraph, DanNode, DanEdge, LoopGroup, TokenBreakdown, WasteFinding, OptimizationMutation, TierInfo } from "../types/graph";
import {
  danGraphToReactFlow,
  danNodeToReactFlow,
  danEdgeToReactFlow,
  reactFlowToDanGraph,
  EDGE_COLORS,
  getDrillTargets,
  injectLoopGroups,
  stripLoopGroups,
  resolveGraphAtStack,
  deepSetSubGraph,
  MAX_DRILL_DEPTH,
} from "../lib/graphAdapter";
import { PREDEFINED_AGENT_TEMPLATES } from "../lib/paletteTemplates";
import { layoutGraph, needsAutoLayout } from "../lib/layout";
import * as api from "../lib/api";
import { useAppStore } from "./useAppStore";
import {
  closeGraphRunSocket,
  createEmptyGraphEditorState,
  createEmptyGraphRunState,
  type GraphSnapshot,
  persistGraphTabState,
  readPersistedGraphTabState,
  restoreGraphTab,
  snapshotGraphTab,
  type TabInfo,
  type TabSnapshot,
} from "./graphSessionState";
import {
  createInitialGraphPanelState,
  focusHistoryPanelForGraphStore,
  focusLogPanelForGraphStore,
  loadRunLogsForGraphStore,
  openBuildWithAIForGraphStore,
  openDebugWithErrorForGraphStore,
  setGraphChatMode,
  type GraphChatMode,
} from "./graphPanelFocus";
const MAX_HISTORY = 50;

type PersistenceState = Pick<
  GraphState,
  "danGraph" | "nodes" | "edges" | "layerStack" | "loopGroups"
>;

function materializeGraphForPersistence(
  state: PersistenceState,
): { graph: DanGraph | null; error?: string } {
  const { danGraph, nodes, edges, layerStack, loopGroups } = state;
  if (!danGraph) return { graph: null, error: "No graph loaded." };

  const realNodes = nodes.filter((node) => node.type !== "loopGroup");
  const realEdges = edges.filter((edge) => !edge.data?.synthetic && !edge.data?.loopGroupEdge);
  const cleanEdges = realEdges.map((edge) =>
    edge.data?._groupHidden
      ? { ...edge, hidden: false, data: { ...edge.data, _groupHidden: undefined } }
      : edge,
  );
  const cleanNodes = realNodes.map((node) => (node.hidden ? { ...node, hidden: false } : node));

  if (layerStack.length === 0) {
    let updated = reactFlowToDanGraph(cleanNodes, cleanEdges, danGraph);
    if (loopGroups.length > 0) {
      updated = { ...updated, metadata: { ...updated.metadata, loop_groups: loopGroups } };
    } else {
      const { loop_groups: _removed, ...restMeta } =
        (updated.metadata ?? {}) as unknown as Record<string, unknown>;
      updated = { ...updated, metadata: restMeta as unknown as DanGraph["metadata"] };
    }
    return { graph: updated };
  }

  const subBase = resolveGraphAtStack(danGraph, layerStack);
  if (!subBase) {
    return { graph: null, error: "Cannot save — layer path invalid. Reset to root." };
  }

  let updatedSub = reactFlowToDanGraph(cleanNodes, cleanEdges, subBase);
  if (loopGroups.length > 0) {
    updatedSub = { ...updatedSub, metadata: { ...updatedSub.metadata, loop_groups: loopGroups } };
  } else {
    const meta = (updatedSub.metadata ?? {}) as unknown as Record<string, unknown>;
    const { loop_groups: _removed, ...restMeta } = meta;
    updatedSub = { ...updatedSub, metadata: restMeta as unknown as DanGraph["metadata"] };
  }
  return { graph: deepSetSubGraph(danGraph, layerStack, updatedSub) };
}

export interface LogEntry {
  timestamp: number;
  event_type: string;
  node_id?: string;
  message: string;
  data?: Record<string, unknown>;
}

interface RunEventHandleOptions {
  historical?: boolean;
}

// -- 5-3: Rich logging -------------------------------------------------------
export const EVENT_CATEGORY: Record<string, string> = {
  llm_thinking: "thinking",
  tool_call_started: "tool",
  tool_call_result: "tool",
  code_output: "output",
  intermediate_text: "output",
  lint_passed: "lint",
  lint_failed: "lint",
  lint_auto_fixed: "lint",
  node_failed: "error",
  run_failed: "error",
  node_started: "lifecycle",
  node_completed: "lifecycle",
  node_skipped: "lifecycle",
  node_output: "lifecycle",
  run_started: "lifecycle",
  run_completed: "lifecycle",
  iteration_started: "lifecycle",
  iteration_completed: "lifecycle",
  human_input_needed: "lifecycle",
};

interface GraphState {
  // -- Graph identity
  graphId: string | null;
  danGraph: DanGraph | null;
  graphRevision: string | null;
  graphList: api.GraphListItem[];
  dirty: boolean;

  // -- React Flow state
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;

  // -- 6-1: Multi-select
  selectedNodeIds: Set<string>;

  // -- 6-2: Clipboard
  clipboard: { nodes: DanNode[]; edges: Record<string, unknown>[] };
  copySelected: () => void;
  pasteClipboard: (position?: { x: number; y: number }) => void;
  duplicateSelected: () => void;

  // -- Run state
  runId: string | null;
  runStatus: string | null;
  nodeStatuses: Record<string, string>;
  nodeOutputs: Record<string, Record<string, unknown>>;
  logs: LogEntry[];
  runSummary: { elapsed_seconds?: number; total_prompt_tokens?: number; total_completion_tokens?: number; total_tokens?: number } | null;
  ws: WebSocket | null;

  // -- 7-4: Per-node token/cost observability
  nodeUsage: Record<string, { prompt_tokens: number; completion_tokens: number; total_tokens: number }>;
  nodeCosts: Record<string, number>;

  // -- Actions: graph lifecycle
  loadGraphList: () => Promise<void>;
  loadGraph: (graphId: string) => Promise<void>;
  createGraph: (graphId: string) => Promise<void>;
  deleteGraph: (graphId: string) => Promise<void>;
  saveGraph: () => Promise<boolean>;
  saveGraphAs: (newName: string, newGraphId?: string) => Promise<string | null>;

  // -- Actions: React Flow callbacks
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: (conn: Connection) => void;
  setSelectedNode: (id: string | null) => void;
  setSelectedEdge: (id: string | null) => void;

  // -- Actions: node manipulation
  addNode: (node: DanNode) => void;
  updateNodeData: (nodeId: string, data: Partial<DanNode>) => void;
  deleteSelected: () => void;
  renamePort: (nodeId: string, portType: 'input' | 'output', oldName: string, newName: string) => void;
  deletePort: (nodeId: string, portType: 'input' | 'output', portName: string) => void;

  // -- Actions: run lifecycle
  startRun: (inputs?: Record<string, unknown>) => Promise<void>;
  resumeRun: () => Promise<void>;
  disconnectRun: () => void;
  recoverActiveRun: () => Promise<void>;

  // -- Actions: event handling
  handleRunEvent: (event: Record<string, unknown>, options?: RunEventHandleOptions) => void;

  // -- 5-3: Rich logging
  selectNodeFromLog: (nodeId: string) => void;

  // -- 5-1: Layer navigation
  layerStack: Array<{ graphKey: string; nodeId: string; nodeName?: string }>;
  drillIn: (nodeId: string, graphKey?: string) => void;
  drillOut: () => void;
  jumpToLayer: (index: number) => void;

  // -- 5-4: Build palette
  selectedEdgeType: "data" | "control" | "context";
  setSelectedEdgeType: (type: "data" | "control" | "context") => void;
  addTemplateNode: (templateId: string, position: { x: number; y: number }) => void;

  // -- 5-2: Live execution viz
  nodeTimings: Record<string, { start: number; end?: number }>;
  activeExecutionPath: Set<string>;

  // -- 6-4: Validation
  validationErrors: Record<string, string[]>;

  // -- 6-5: InputNode ephemeral values
  inputNodeValues: Record<string, Record<string, unknown>>;
  setInputNodeValue: (nodeId: string, varName: string, value: unknown) => void;

  // -- 6-5: Command palette
  commandPaletteOpen: boolean;
  setCommandPaletteOpen: (open: boolean) => void;

  // -- 5-5: UI polish
  toasts: Array<{ id: string; type: "success" | "error" | "info" | "warning"; message: string; action?: { label: string; onClick: () => void }; durationMs?: number }>;
  addToast: (toast: { type: "success" | "error" | "info" | "warning"; message: string; action?: { label: string; onClick: () => void }; durationMs?: number }) => void;
  removeToast: (id: string) => void;
  loadingGraph: boolean;
  savingGraph: boolean;
  applyAutoLayout: () => void;
  updateEdgeData: (edgeId: string, data: Partial<Record<string, unknown>>) => void;

  // -- 6-5: Sub-graph grouping
  groupIntoComposite: () => void;

  // -- 6-6: Loop iteration state
  nodeIterations: Record<string, { current: number; total?: number; condition?: string }>;

  // -- 6-6: Streaming output state
  streamingOutputs: Record<string, string>;

  // -- 6-6: Human input state
  pendingHumanInput: {
    requestId: string;
    nodeId: string;
    prompt: string;
  } | null;

  // -- 6-7: Workflow as node
  addGraphAsNode: (graphId: string, position: { x: number; y: number }) => Promise<void>;

  // -- 7-9: Create empty sub-graph for parallel_subagents
  createEmptySubGraph: (graphKey: string) => void;

  // -- 9-3: Boundary validators
  addBoundaryValidators: (nodeId: string) => Promise<void>;

  // -- 6-1: History (undo/redo)
  _history: { past: GraphSnapshot[]; future: GraphSnapshot[] };
  pushSnapshot: () => void;
  undo: () => void;
  redo: () => void;

  // -- 6-10: Loop groups (visual-only)
  loopGroups: LoopGroup[];
  createLoopGroup: (gateNodeId: string, memberNodeIds: string[], label?: string) => void;
  toggleLoopGroup: (groupId: string) => void;
  removeLoopGroup: (groupId: string) => void;

  // -- 6-9: Tab state
  tabs: TabInfo[];
  activeTabId: string | null;
  tabCache: Record<string, TabSnapshot>;
  openTab: (graphId: string) => Promise<void>;
  openTabFromTemplate: (templateId: string) => Promise<void>;
  replaceActiveTabGraph: (graphId: string) => Promise<void>;
  closeTab: (tabId: string) => Promise<void>;
  switchTab: (tabId: string) => Promise<void>;
  refreshTab: () => Promise<void>;
  restoreTabs: () => Promise<void>;

  // -- 10-9: Build-from-intent chat mode / 12-2: Multi-mode chat
  chatMode: GraphChatMode;
  setChatMode: (mode: GraphChatMode) => void;
  openBuildWithAI: () => Promise<void>;
  chatFocusTrigger: number;
  chatPrefill: string | null;
  openDebugWithError: (errorContext: string) => void;

  // -- 12-1: Log panel focus
  logFocusCounter: number;
  focusLogPanel: () => void;
  loadRunLogs: (runId: string) => Promise<void>;

  // -- 13-1: History panel focus
  historyFocusCounter: number;
  historyFocusRunId: string | null;
  focusHistoryPanel: (runId?: string) => void;

  // -- 13-2: Checkpoint portal
  rerunFromNode: (nodeId: string, scopeType: "downstream_of" | "single_node") => Promise<void>;

  // -- 18-4: Token analytics
  tokenBreakdowns: Record<string, TokenBreakdown>;
  wasteFindings: WasteFinding[];
  optimizationMutations: OptimizationMutation[];
  tokenHeatmapEnabled: boolean;
  analyticsLoading: boolean;
  edgeTokenCounts: Record<string, number>;
  showEdgeTokenLabels: boolean;
  setTokenHeatmapEnabled: (enabled: boolean) => void;
  setShowEdgeTokenLabels: (enabled: boolean) => void;
  fetchTokenAnalytics: () => Promise<void>;
  applyOptimizationMutation: (index: number) => Promise<void>;

  // -- 18-5: Model tier info
  nodeTiers: Record<string, TierInfo>;
}

// -- 7-4: Cost estimation (mirrors src/dan/providers/costs.py) ------------------
const COST_PER_1K: Record<string, { prompt: number; completion: number }> = {
  "gpt-4o": { prompt: 0.0025, completion: 0.01 },
  "gpt-4o-mini": { prompt: 0.00015, completion: 0.0006 },
  "gpt-4.1": { prompt: 0.002, completion: 0.008 },
  "gpt-4.1-mini": { prompt: 0.0004, completion: 0.0016 },
  "gpt-4.1-nano": { prompt: 0.0001, completion: 0.0004 },
  "o1": { prompt: 0.015, completion: 0.06 },
  "o3": { prompt: 0.01, completion: 0.04 },
  "o3-mini": { prompt: 0.0011, completion: 0.0044 },
  "o4-mini": { prompt: 0.0011, completion: 0.0044 },
  "claude-opus-4": { prompt: 0.015, completion: 0.075 },
  "claude-sonnet-4": { prompt: 0.003, completion: 0.015 },
  "claude-haiku-3.5": { prompt: 0.0008, completion: 0.004 },
  "gemini-2.0-flash": { prompt: 0.0001, completion: 0.0004 },
  "gemini-2.0-pro": { prompt: 0.00125, completion: 0.005 },
  "gemini-2.5-pro": { prompt: 0.00125, completion: 0.01 },
  "gemini-2.5-flash": { prompt: 0.00015, completion: 0.0006 },
};

const VALID_TIERS = new Set<string>(["micro", "routine", "reasoning", "critical"]);
const RUN_NOTIFICATION_KEYS = new Set<string>();

function estimateCost(model: string, promptTokens: number, completionTokens: number): number | null {
  let rates = COST_PER_1K[model];
  if (!rates) {
    for (const [key, r] of Object.entries(COST_PER_1K)) {
      if (model.startsWith(key)) { rates = r; break; }
    }
  }
  if (!rates) return null;
  return (promptTokens * rates.prompt + completionTokens * rates.completion) / 1000;
}

function _stringField(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function _lintRouteSummary(data: Record<string, unknown>): string | null {
  const sourcePort = _stringField(data.source_port);
  const targetNode = _stringField(data.target_node_id);
  const targetPort = _stringField(data.target_port);
  if (!sourcePort && !targetNode && !targetPort) return null;
  return `${sourcePort ?? "?"} -> ${targetNode ?? "?"}.${targetPort ?? "?"}`;
}

function _firstLintDiagnosticMessage(data: Record<string, unknown>): string | null {
  const diagnostics = data.diagnostics;
  if (Array.isArray(diagnostics)) {
    for (const item of diagnostics) {
      if (!item || typeof item !== "object" || Array.isArray(item)) continue;
      const message = _stringField((item as Record<string, unknown>).message);
      if (message) return message;
    }
  }
  return _stringField(data.message);
}

function _lintNotificationKey(
  runId: string | null,
  eventType: string,
  nodeId: string | undefined,
  timestamp: number,
  data: Record<string, unknown>,
): string {
  return [
    runId ?? "no-run",
    eventType,
    nodeId ?? "no-node",
    _stringField(data.edge_id) ?? "no-edge",
    typeof data.attempt === "number" ? String(data.attempt) : "no-attempt",
    String(timestamp),
  ].join("|");
}

export const useGraphStore = create<GraphState>((set, get) => {
  // -- 6-9: Tab internal helpers -----------------------------------------------

  const _snapshotActiveTab = (): TabSnapshot => snapshotGraphTab(get());

  const _restoreTab = (snapshot: TabSnapshot): void => {
    set(restoreGraphTab(snapshot));
  };

  const _persistTabState = (): void => {
    const { tabs, activeTabId, tabCache, runId } = get();
    persistGraphTabState({ tabs, activeTabId, tabCache, runId });
  };

  return {
  graphId: null,
  danGraph: null,
  graphRevision: null,
  graphList: [],
  dirty: false,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  selectedEdgeId: null,
  selectedNodeIds: new Set<string>(),
  clipboard: { nodes: [], edges: [] },
  runId: null,
  runStatus: null,
  nodeStatuses: {},
  nodeOutputs: {},
  logs: [],
  runSummary: null,
  ws: null,
  nodeUsage: {},
  nodeCosts: {},
  layerStack: [],
  selectedEdgeType: "data" as const,
  nodeTimings: {},
  activeExecutionPath: new Set<string>(),
  validationErrors: {},
  inputNodeValues: {},
  commandPaletteOpen: false,
  toasts: [],
  loadingGraph: false,
  savingGraph: false,
  nodeIterations: {},
  streamingOutputs: {},
  pendingHumanInput: null,
  _history: { past: [], future: [] },

  // -- 6-10: Loop groups
  loopGroups: [],

  // -- 10-9 / 12-2: Chat modes
  ...createInitialGraphPanelState(),

  // -- 18-4: Token analytics
  tokenBreakdowns: {},
  wasteFindings: [],
  optimizationMutations: [],
  edgeTokenCounts: {},
  showEdgeTokenLabels: false,
  tokenHeatmapEnabled: false,
  analyticsLoading: false,

  // -- 18-5: Model tier info
  nodeTiers: {},

  // -- 6-9: Tab state
  tabs: [],
  activeTabId: null,
  tabCache: {},

  // -- Graph lifecycle -------------------------------------------------------

  loadGraphList: async () => {
    try {
      const { graphs, last_opened } = await api.listGraphs();
      set({ graphList: graphs });
      if (get().tabs.length === 0 && !get().graphId) {
        if (last_opened) {
          const tabId = crypto.randomUUID();
          set({
            tabs: [{ id: tabId, graphId: last_opened, graphName: last_opened, cachedRunStatus: null }],
            activeTabId: tabId,
          });
          await get().loadGraph(last_opened);
        } else {
          await get().openTab("blank");
        }
      }
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to load graph list" });
    }
  },

  loadGraph: async (graphId: string) => {
    set({ loadingGraph: true });
    try {
      const { data, graph_revision } = await api.getGraph(graphId, { layout: true });
      const danGraph = data as unknown as DanGraph;
      let { nodes, edges } = danGraphToReactFlow(danGraph);

      // Fallback: auto-layout when all nodes share the same position (backend layout may be off)
      if (nodes.length > 1) {
        const allSamePos = nodes.every(
          (n) => n.position.x === nodes[0].position.x && n.position.y === nodes[0].position.y,
        );
        if (allSamePos) {
          nodes = layoutGraph(nodes, edges);
        }
      }

      const loadedGroups: LoopGroup[] = (danGraph.metadata as unknown as Record<string, unknown>).loop_groups as LoopGroup[] ?? [];
      if (loadedGroups.length > 0) {
        const injected = injectLoopGroups(nodes, edges, loadedGroups);
        nodes = injected.nodes;
        edges = injected.edges;
      }

      set({
        graphId,
        danGraph,
        graphRevision: graph_revision ?? null,
        nodes,
        edges,
        loopGroups: loadedGroups,
        dirty: false,
        selectedNodeId: null,
        selectedEdgeId: null,
        layerStack: [],
        validationErrors: {},
      });
      const { activeTabId: atId } = get();
      if (atId) {
        const gName = danGraph.metadata?.name ?? graphId;
        set((s) => ({ tabs: s.tabs.map((t) => t.id === atId ? { ...t, graphId, graphName: gName } : t) }));
      }
      get().addToast({ type: "success", message: `Loaded "${graphId}"` });
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to load graph" });
    } finally {
      set({ loadingGraph: false });
    }
  },

  createGraph: async (graphId: string) => {
    try {
      await api.createGraph(graphId);
      get().addToast({ type: "success", message: `Created "${graphId}"` });
      await get().loadGraphList();
      await get().openTab(graphId);
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to create graph" });
    }
  },

  deleteGraph: async (graphId: string) => {
    try {
      await api.deleteGraph(graphId);
      const { tabs } = get();
      const matchingTab = tabs.find((t) => t.graphId === graphId);
      if (matchingTab) {
        await get().closeTab(matchingTab.id);
      }
      get().addToast({ type: "info", message: `Deleted "${graphId}"` });
      await get().loadGraphList();
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to delete graph" });
    }
  },

  saveGraph: async () => {
    const { graphId, danGraph } = get();
    if (!graphId || !danGraph) return false;
    set({ savingGraph: true, validationErrors: {} });
    try {
      const materialized = materializeGraphForPersistence(get());
      if (!materialized.graph) {
        get().addToast({ type: "error", message: materialized.error ?? "Failed to prepare graph for saving." });
        const { nodes: rootNodes, edges: rootEdges } = danGraphToReactFlow(danGraph);
        set({
          layerStack: [],
          nodes: rootNodes,
          edges: rootEdges,
          loopGroups: [],
          savingGraph: false,
          selectedNodeId: null,
          selectedEdgeId: null,
        });
        return false;
      }
      const updated = materialized.graph;
      const response = await api.updateGraph(
        graphId,
        updated as unknown as Record<string, unknown>,
      );
      set({
        danGraph: updated,
        graphRevision: response.graph_revision ?? get().graphRevision ?? null,
        dirty: false,
      });
      get().addToast({ type: "success", message: "Graph saved" });

      // Run server-side validation after successful save
      try {
        const result = await api.validateGraph(graphId);
        const grouped: Record<string, string[]> = {};
        for (const issue of [...result.errors, ...result.warnings]) {
          const key = issue.node_id ?? issue.edge_id ?? "_graph";
          (grouped[key] ??= []).push(issue.message);
        }
        set({ validationErrors: grouped });
        const count = result.errors.length;
        if (count > 0) {
          get().addToast({ type: "warning", message: `Validation: ${count} error${count > 1 ? "s" : ""}` });
        } else {
          get().addToast({ type: "info", message: "Validation passed" });
        }
      } catch {
        // Validation failure shouldn't block save
      }

      return true;
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to save graph" });
      return false;
    } finally {
      set({ savingGraph: false });
    }
  },

  saveGraphAs: async (newName, newGraphId) => {
    const { graphId, danGraph } = get();
    const trimmedName = newName.trim();
    if (!graphId || !danGraph || !trimmedName) return null;
    set({ savingGraph: true, validationErrors: {} });
    try {
      const materialized = materializeGraphForPersistence(get());
      if (!materialized.graph) {
        get().addToast({ type: "error", message: materialized.error ?? "Failed to prepare graph for saving." });
        return null;
      }
      const response = await api.saveGraphAs(graphId, {
        new_name: trimmedName,
        new_graph_id: newGraphId?.trim() || undefined,
        data: materialized.graph as unknown as Record<string, unknown>,
      });
      set({
        danGraph: response.data as unknown as DanGraph,
        graphRevision: response.graph_revision ?? get().graphRevision ?? null,
        dirty: false,
      });
      await get().loadGraphList();
      await get().replaceActiveTabGraph(response.graph_id);
      get().addToast({
        type: "success",
        message: response.graph_id === graphId ? `Saved "${trimmedName}"` : `Saved as "${response.graph_id}"`,
      });
      return response.graph_id;
    } catch (err: unknown) {
      get().addToast({ type: "error", message: `Save as failed: ${(err as Error).message}` });
      return null;
    } finally {
      set({ savingGraph: false });
    }
  },

  // -- React Flow callbacks --------------------------------------------------

  onNodesChange: (changes) => {
    set((s) => ({ nodes: applyNodeChanges(changes, s.nodes), dirty: true }));
  },

  onEdgesChange: (changes) => {
    set((s) => ({ edges: applyEdgeChanges(changes, s.edges), dirty: true }));
  },

  onConnect: (conn) => {
    get().pushSnapshot();
    const edgeId = `e-${conn.source}-${conn.target}-${Date.now()}`;
    // -- 5-4: Build palette — use selected edge type
    const selectedEdgeType = get().selectedEdgeType;
    const danEdge: Record<string, unknown> = {
      id: edgeId,
      edge_type: selectedEdgeType,
      source_node_id: conn.source,
      source_port: conn.sourceHandle?.replace("port:", "") ?? "",
      target_node_id: conn.target,
      target_port: conn.targetHandle?.replace("port:", "") ?? "",
      ui: {},
      metadata: {},
    };
    if (selectedEdgeType === "control") danEdge.condition = null;
    if (selectedEdgeType === "context") {
      danEdge.context_key = "default";
      danEdge.mode = "read";
    }
    set((s) => ({
      edges: addEdge(
        {
          ...conn,
          id: edgeId,
          type: "smoothstep",
          animated: selectedEdgeType === "context",
          label: selectedEdgeType !== "data" ? selectedEdgeType : undefined,
          style: { stroke: EDGE_COLORS[selectedEdgeType] ?? "#94a3b8" },
          markerEnd: { type: MarkerType.ArrowClosed },
          data: { danEdge },
        },
        s.edges,
      ),
      dirty: true,
    }));
  },

  setSelectedNode: (id) => set({ selectedNodeId: id, selectedEdgeId: null }),
  setSelectedEdge: (id) => set({ selectedEdgeId: id, selectedNodeId: null }),

  // -- Node manipulation -----------------------------------------------------

  addNode: (node) => {
    get().pushSnapshot();
    const rfNode = danNodeToReactFlow(node);
    set((s) => ({ nodes: [...s.nodes, rfNode], dirty: true }));
  },

  updateNodeData: (nodeId, data) => {
    get().pushSnapshot();
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n,
      ),
      dirty: true,
    }));
  },

  deleteSelected: () => {
    const { selectedNodeId, selectedEdgeId, selectedNodeIds } = get();
    get().pushSnapshot();
    if (selectedNodeIds.size > 1) {
      set((s) => ({
        nodes: s.nodes.filter((n) => !selectedNodeIds.has(n.id)),
        edges: s.edges.filter(
          (e) => !selectedNodeIds.has(e.source) && !selectedNodeIds.has(e.target),
        ),
        selectedNodeId: null,
        selectedNodeIds: new Set<string>(),
        dirty: true,
      }));
    } else if (selectedNodeId) {
      set((s) => ({
        nodes: s.nodes.filter((n) => n.id !== selectedNodeId),
        edges: s.edges.filter(
          (e) => e.source !== selectedNodeId && e.target !== selectedNodeId,
        ),
        selectedNodeId: null,
        selectedNodeIds: new Set<string>(),
        dirty: true,
      }));
    } else if (selectedEdgeId) {
      set((s) => ({
        edges: s.edges.filter((e) => e.id !== selectedEdgeId),
        selectedEdgeId: null,
        dirty: true,
      }));
    }
  },

  // -- 6-2: Clipboard -------------------------------------------------------

  copySelected: () => {
    const { selectedNodeIds, selectedNodeId, nodes, edges } = get();
    const ids =
      selectedNodeIds.size > 0
        ? selectedNodeIds
        : selectedNodeId
          ? new Set([selectedNodeId])
          : new Set<string>();
    if (ids.size === 0) return;

    const copiedNodes = nodes
      .filter((n) => ids.has(n.id))
      .map((n) => structuredClone(n.data as unknown as DanNode));
    const copiedEdges = edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => structuredClone((e.data?.danEdge ?? {}) as Record<string, unknown>));

    set({ clipboard: { nodes: copiedNodes, edges: copiedEdges } });
  },

  pasteClipboard: (position) => {
    const { clipboard } = get();
    if (clipboard.nodes.length === 0) return;
    get().pushSnapshot();

    const idMap = new Map<string, string>();
    const baseNode = clipboard.nodes[0];

    const newNodes: DanNode[] = clipboard.nodes.map((n) => {
      const newId = crypto.randomUUID();
      idMap.set(n.id, newId);
      const cloned = structuredClone(n);
      cloned.id = newId;
      if (position) {
        cloned.position = {
          x: position.x + (n.position.x - baseNode.position.x),
          y: position.y + (n.position.y - baseNode.position.y),
        };
      } else {
        cloned.position = { x: n.position.x + 30, y: n.position.y + 30 };
      }
      return cloned;
    });

    const newEdges = clipboard.edges
      .filter(
        (e) =>
          idMap.has(e.source_node_id as string) &&
          idMap.has(e.target_node_id as string),
      )
      .map((e) => {
        const cloned = structuredClone(e);
        cloned.id = crypto.randomUUID();
        cloned.source_node_id = idMap.get(e.source_node_id as string)!;
        cloned.target_node_id = idMap.get(e.target_node_id as string)!;
        return cloned;
      });

    const rfNodes = newNodes.map(danNodeToReactFlow);
    const rfEdges = newEdges.map((e) =>
      danEdgeToReactFlow(e as unknown as DanEdge),
    );

    set((s) => ({
      nodes: [...s.nodes, ...rfNodes],
      edges: [...s.edges, ...rfEdges],
      dirty: true,
    }));
  },

  duplicateSelected: () => {
    get().copySelected();
    get().pasteClipboard();
  },

  renamePort: (nodeId, portType, oldName, newName) => {
    get().pushSnapshot();
    const portKey = portType === 'input' ? 'input_ports' : 'output_ports';
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== nodeId) return n;
        const d = n.data as Record<string, unknown>;
        const ports = (d[portKey] as Array<Record<string, unknown>>).map((p) =>
          p.name === oldName ? { ...p, name: newName } : p,
        );
        return { ...n, data: { ...n.data, [portKey]: ports } };
      }),
      edges: s.edges.map((e) => {
        const danEdge = (e.data?.danEdge ?? {}) as Record<string, unknown>;
        const isSourceMatch = e.source === nodeId && portType === 'output' && danEdge.source_port === oldName;
        const isTargetMatch = e.target === nodeId && portType === 'input' && danEdge.target_port === oldName;
        if (!isSourceMatch && !isTargetMatch) return e;
        const updatedDanEdge = { ...danEdge };
        if (isSourceMatch) updatedDanEdge.source_port = newName;
        if (isTargetMatch) updatedDanEdge.target_port = newName;
        return {
          ...e,
          ...(isSourceMatch ? { sourceHandle: `port:${newName}` } : {}),
          ...(isTargetMatch ? { targetHandle: `port:${newName}` } : {}),
          data: { ...e.data, danEdge: updatedDanEdge },
        };
      }),
      dirty: true,
    }));
  },

  deletePort: (nodeId, portType, portName) => {
    get().pushSnapshot();
    const portKey = portType === 'input' ? 'input_ports' : 'output_ports';
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== nodeId) return n;
        const d = n.data as Record<string, unknown>;
        const ports = (d[portKey] as Array<Record<string, unknown>>).filter(
          (p) => p.name !== portName,
        );
        return { ...n, data: { ...n.data, [portKey]: ports } };
      }),
      edges: s.edges.filter((e) => {
        const danEdge = (e.data?.danEdge ?? {}) as Record<string, unknown>;
        if (e.source === nodeId && portType === 'output' && danEdge.source_port === portName) return false;
        if (e.target === nodeId && portType === 'input' && danEdge.target_port === portName) return false;
        return true;
      }),
      dirty: true,
    }));
  },

  // -- Run lifecycle ---------------------------------------------------------

  startRun: async (inputs) => {
    const { graphId, danGraph, inputNodeValues } = get();
    if (!graphId) return;

    let finalInputs = inputs;
    if (!finalInputs && danGraph) {
      const mergedInputs = danGraph.nodes
        .filter((n) => n.node_type === "input")
        .reduce<Record<string, unknown>>((acc, node) => {
          const vals = inputNodeValues[node.id];
          if (vals && Object.keys(vals).length > 0) {
            Object.assign(acc, vals);
          }
          return acc;
        }, {});
      if (Object.keys(mergedInputs).length > 0) {
        finalInputs = mergedInputs;
      }
    }

    try {
      const saved = await get().saveGraph();
      if (!saved) return;
      const { run_id } = await api.startRun(graphId, finalInputs);
      set({
        ...createEmptyGraphRunState(),
        runId: run_id,
        runStatus: "running",
      });
      _persistTabState();
      get().addToast({ type: "info", message: `Run started (${run_id.slice(0, 8)})` });
      const ws = api.connectRunEvents(
        run_id,
        (event) => get().handleRunEvent(event),
        () => set({ ws: null }),
      );
      set({ ws });
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to start run" });
    }
  },

  resumeRun: async () => {
    const { graphId, runId } = get();
    if (!graphId || !runId) return;
    try {
      await api.resumeRun(runId, graphId);
      set({
        ...createEmptyGraphRunState(),
        runId,
        runStatus: "running",
      });
      _persistTabState();
      get().addToast({ type: "info", message: "Run resumed" });
      const ws = api.connectRunEvents(
        runId,
        (event) => get().handleRunEvent(event),
        () => set({ ws: null }),
      );
      set({ ws });
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to resume run" });
    }
  },

  rerunFromNode: async (nodeId: string, scopeType: "downstream_of" | "single_node") => {
    const { graphId, runId } = get();
    if (!graphId || !runId) {
      get().addToast({ type: "error", message: "No active run to rerun from" });
      return;
    }
    try {
      const result = await api.rerunFromCheckpoint(runId, {
        scope_type: scopeType,
        target_node_id: nodeId,
        graph_id: graphId,
      });
      set({
        ...createEmptyGraphRunState(),
        runId: result.run_id,
        runStatus: "running",
      });
      _persistTabState();
      get().addToast({ type: "info", message: `Rerun started (${scopeType.replace("_", " ")} ${nodeId.slice(0, 12)})` });
      const ws = api.connectRunEvents(
        result.run_id,
        (event) => get().handleRunEvent(event),
        () => set({ ws: null }),
      );
      set({ ws });
    } catch (err: unknown) {
      const msg = (err as Error).message ?? "Rerun failed";
      get().addToast({ type: "error", message: msg });
    }
  },

  disconnectRun: () => {
    get().ws?.close();
    set({ ws: null, runStatus: "disconnected" });
  },

  recoverActiveRun: async () => {
    let stored: { runId: string; graphId: string } | null = null;
    try {
      const raw = sessionStorage.getItem("dan_active_run");
      if (raw) stored = JSON.parse(raw);
    } catch { /* corrupt */ }
    if (!stored) return;

    try {
      const info = await api.getRun(stored.runId);
      if (info.status !== "pending" && info.status !== "running") {
        sessionStorage.removeItem("dan_active_run");
        if (stored.graphId !== get().graphId) {
          await get().loadGraph(stored.graphId);
        }
        set({
          runId: stored.runId,
          runStatus: info.status,
          nodeStatuses: info.node_statuses ?? {},
          nodeOutputs: info.outputs ? { _final: info.outputs } : {},
        });
        const label = info.status === "completed" ? "completed" : "failed";
        get().addToast({ type: info.status === "completed" ? "success" : "error", message: `Previous run ${stored.runId.slice(0, 8)} ${label}` });
        return;
      }
      if (stored.graphId !== get().graphId) {
        await get().loadGraph(stored.graphId);
      }
      set({
        ...createEmptyGraphRunState(),
        runId: stored.runId,
        runStatus: info.status,
        nodeStatuses: info.node_statuses ?? {},
        activeExecutionPath: new Set(Object.keys(info.node_statuses ?? {})),
      });
      const ws = api.connectRunEvents(
        stored.runId,
        (event) => get().handleRunEvent(event),
        () => set({ ws: null }),
      );
      set({ ws });
      get().addToast({ type: "info", message: `Reconnected to run ${stored.runId.slice(0, 8)}` });
    } catch {
      sessionStorage.removeItem("dan_active_run");
    }
  },

  // -- 5-3: Rich logging -----------------------------------------------------
  selectNodeFromLog: (nodeId) => set({ selectedNodeId: nodeId, selectedEdgeId: null, selectedNodeIds: new Set<string>() }),

  // -- Event handling --------------------------------------------------------

  handleRunEvent: (event, options) => {
    // 6-9: Ignore events from other tabs' runs
    const activeRunId = get().runId;
    const eventRunId = event.run_id as string | undefined;
    if (activeRunId && eventRunId && eventRunId !== activeRunId) return;
    const isHistorical = options?.historical === true;

    const eventType = event.event_type as string;
    const nodeId = event.node_id as string | undefined;
    const data = (event.data ?? {}) as Record<string, unknown>;
    const ts = (event.timestamp as number) ?? Date.now() / 1000;

    if (eventType === "_catchup") {
      const snapshot = event.snapshot as Record<string, unknown> | undefined;
      const buffered = (event.buffered_events ?? []) as Record<string, unknown>[];
      if (snapshot) {
        set({
          nodeStatuses: (snapshot.node_statuses ?? {}) as Record<string, string>,
          runStatus: snapshot.status as string,
        });
      }
      for (const be of buffered) {
        get().handleRunEvent(be, options);
      }
      const pendingHI = (event.pending_human_inputs ?? []) as Array<Record<string, unknown>>;
      if (pendingHI.length > 0) {
        const last = pendingHI[pendingHI.length - 1];
        const hiData = (last.data ?? {}) as Record<string, unknown>;
        set({
          pendingHumanInput: {
            requestId: hiData.request_id as string,
            nodeId: last.node_id as string,
            prompt: hiData.prompt as string,
          },
        });
      }
      return;
    }

    const logMsg: LogEntry = {
      timestamp: ts,
      event_type: eventType,
      node_id: nodeId,
      message: nodeId ? `${eventType} — ${nodeId}` : eventType,
      data,
    };

    set((s) => {
      const newStatuses = { ...s.nodeStatuses };
      const newOutputs = { ...s.nodeOutputs };
      const newTimings = { ...s.nodeTimings };
      const newUsage = { ...s.nodeUsage };
      const newCosts = { ...s.nodeCosts };
      let newRunStatus = s.runStatus;

      if (nodeId && ["node_started", "node_completed", "node_failed", "node_skipped"].includes(eventType)) {
        newStatuses[nodeId] = eventType;
      }
      if (nodeId && eventType === "node_output" && data.outputs) {
        newOutputs[nodeId] = data.outputs as Record<string, unknown>;
      }

      // -- 7-4: Extract per-node usage and cost on completion
      if (nodeId && eventType === "node_completed" && data.metadata) {
        const meta = data.metadata as Record<string, unknown>;
        const usage = meta.usage as Record<string, number> | undefined;
        if (usage && (usage.total_tokens ?? 0) > 0) {
          newUsage[nodeId] = {
            prompt_tokens: usage.prompt_tokens ?? 0,
            completion_tokens: usage.completion_tokens ?? 0,
            total_tokens: usage.total_tokens ?? 0,
          };
        }
        const model = meta.model as string | undefined;
        if (model && usage) {
          const cost = estimateCost(model, usage.prompt_tokens ?? 0, usage.completion_tokens ?? 0);
          if (cost != null) {
            newCosts[nodeId] = cost;
          }
        }
      }

      let newRunSummary = s.runSummary;
      if (eventType === "run_completed" || eventType === "run_failed") {
        newRunStatus = eventType === "run_completed" ? "completed" : "failed";
        newRunSummary = {
          elapsed_seconds: data.elapsed_seconds as number | undefined,
          total_prompt_tokens: data.total_prompt_tokens as number | undefined,
          total_completion_tokens: data.total_completion_tokens as number | undefined,
          total_tokens: data.total_tokens as number | undefined,
        };
      }

      // -- 5-2: Track node timings
      if (nodeId && eventType === "node_started") {
        newTimings[nodeId] = { start: ts };
      }
      if (nodeId && (eventType === "node_completed" || eventType === "node_failed")) {
        if (newTimings[nodeId]) {
          newTimings[nodeId] = { ...newTimings[nodeId], end: ts };
        }
      }

      // -- 18-5: Track tier info from model_selected events
      const isModelSelected = nodeId && eventType === "model_selected" && data.tier && VALID_TIERS.has(String(data.tier));
      const newTiers = isModelSelected ? { ...s.nodeTiers } : s.nodeTiers;
      if (isModelSelected) {
        newTiers[nodeId] = {
          tier: data.tier as TierInfo["tier"],
          tier_score: (data.tier_score as number) ?? 0,
          difficulty: (data.difficulty as number) ?? 0,
          impact: (data.impact as number) ?? 0,
          recoverability: (data.recoverability as number) ?? 0,
          model: (data.model as string) ?? "",
        };
      }

      // -- 6-6: Loop iteration tracking
      const newIterations = { ...s.nodeIterations };
      if (eventType === "iteration_started" && nodeId) {
        newIterations[nodeId] = {
          current: (data.iteration as number) ?? 0,
          total: (data.max_iterations as number) ?? (data.total as number),
          condition: data.condition as string | undefined,
        };
      }
      if (eventType === "iteration_completed" && nodeId) {
        const prev = newIterations[nodeId];
        if (prev) {
          newIterations[nodeId] = { ...prev, current: (data.iteration as number ?? prev.current) + 1 };
        }
      }
      if ((eventType === "node_completed" || eventType === "node_failed") && nodeId) {
        delete newIterations[nodeId];
      }

      // -- 6-6: Streaming output tracking
      const newStreaming = { ...s.streamingOutputs };
      if (eventType === "intermediate_text" && nodeId) {
        newStreaming[nodeId] = (data.text as string) ?? "";
      }
      if ((eventType === "node_completed" || eventType === "node_failed") && nodeId) {
        delete newStreaming[nodeId];
      }

      // -- 6-6: Human input
      let newPendingHuman = s.pendingHumanInput;
      if (eventType === "human_input_needed" && nodeId) {
        newPendingHuman = {
          requestId: data.request_id as string,
          nodeId: nodeId,
          prompt: data.prompt as string,
        };
      }

      return {
        nodeStatuses: newStatuses,
        nodeOutputs: newOutputs,
        nodeTimings: newTimings,
        nodeUsage: newUsage,
        nodeCosts: newCosts,
        nodeTiers: newTiers,
        activeExecutionPath: new Set(Object.keys(newStatuses)),
        runStatus: newRunStatus,
        runSummary: newRunSummary,
        logs: [...s.logs.slice(-4999), logMsg],
        nodeIterations: newIterations,
        streamingOutputs: newStreaming,
        pendingHumanInput: newPendingHuman,
      };
    });

    if (
      !isHistorical &&
      eventType === "lint_failed" &&
      data.severity === "error" &&
      data.handoff_committed === false
    ) {
      const notificationKey = _lintNotificationKey(
        eventRunId ?? activeRunId ?? null,
        eventType,
        nodeId,
        ts,
        data,
      );
      if (!RUN_NOTIFICATION_KEYS.has(notificationKey)) {
        RUN_NOTIFICATION_KEYS.add(notificationKey);
        const route = _lintRouteSummary(data);
        const diagnostic = _firstLintDiagnosticMessage(data);
        useAppStore.getState().addNotification({
          type: "error",
          title: nodeId ? `Lint blocked handoff in ${nodeId}` : "Lint blocked handoff",
          message: [route, diagnostic].filter(Boolean).join(" — "),
          source: "run",
          action: {
            label: "Open logs",
            callback: () => get().focusLogPanel(),
          },
        });
      }
    }

    if (eventType === "run_completed" || eventType === "run_failed") {
      _persistTabState();
      // 18-4: Auto-fetch token analytics after run completion
      get().fetchTokenAnalytics();
    }
  },

  // -- 5-1: Layer navigation ---------------------------------------------------

  drillIn: (nodeId, requestedGraphKey) => {
    const { danGraph, nodes, layerStack } = get();
    if (!danGraph) return;
    const rfNode = nodes.find((n) => n.id === nodeId);
    if (!rfNode) return;
    const d = rfNode.data as unknown as DanNode;
    const dr = d as unknown as Record<string, unknown>;
    const isBlackbox = dr.is_blackbox as boolean | undefined;
    const targets = getDrillTargets(d);
    const graphKey = requestedGraphKey ?? targets[0]?.graphKey;
    if (requestedGraphKey && !targets.some((target) => target.graphKey === requestedGraphKey)) {
      return;
    }
    if (!graphKey || isBlackbox) return;
    if (layerStack.length >= MAX_DRILL_DEPTH) {
      get().addToast({ type: "info", message: "Maximum drill-in depth reached" });
      return;
    }
    const currentGraph = resolveGraphAtStack(danGraph, layerStack);
    if (!currentGraph) {
      set({ layerStack: [], selectedNodeId: null, selectedEdgeId: null });
      const { nodes: rootNodes, edges: rootEdges } = danGraphToReactFlow(danGraph);
      set({ nodes: rootNodes, edges: rootEdges, loopGroups: [] });
      get().addToast({ type: "warning", message: "Navigation error — reset to root" });
      return;
    }
    const subGraph = currentGraph.sub_graphs?.[graphKey] as unknown as DanGraph | undefined;
    if (!subGraph) return;
    const newStack = [...layerStack, { graphKey, nodeId, nodeName: d.name }];
    const sg = subGraph;
    const { nodes: rfNodes, edges: rfEdges } = danGraphToReactFlow(sg);

    if (d.node_type === "while_loop" || d.node_type === "for_each") {
      const entryIds = sg.entry_points ?? [];
      const exitIds = sg.exit_points ?? [];
      const feedbackStyle = { stroke: "#9ca3af", strokeDasharray: "6 3", strokeWidth: 1.5 };
      const feedbackMarker = { type: MarkerType.ArrowClosed, color: "#9ca3af" };
      const syntheticEdges: Edge[] = [];
      let matchedCount = 0;

      for (const exitId of exitIds) {
        const exitRf = rfNodes.find((n) => n.id === exitId);
        if (!exitRf) continue;
        const exitData = exitRf.data as unknown as DanNode;
        const exitPorts = exitData.output_ports ?? [];

        for (const entryId of entryIds) {
          const entryRf = rfNodes.find((n) => n.id === entryId);
          if (!entryRf) continue;
          const entryData = entryRf.data as unknown as DanNode;
          const entryPorts = entryData.input_ports ?? [];

          for (const ep of exitPorts) {
            const match = entryPorts.find((ip) => ip.name === ep.name);
            if (match) {
              matchedCount++;
              syntheticEdges.push({
                id: `synthetic-feedback-${exitId}-${entryId}-${ep.name}`,
                source: exitId,
                target: entryId,
                sourceHandle: `port:${ep.name}`,
                targetHandle: `port:${ep.name}`,
                type: "smoothstep",
                animated: true,
                style: feedbackStyle,
                label: "feedback",
                markerEnd: feedbackMarker,
                data: { synthetic: true, feedback: true },
              });
            }
          }
        }
      }

      if (matchedCount === 0 && exitIds.length > 0 && entryIds.length > 0) {
        syntheticEdges.push({
          id: `synthetic-feedback-${exitIds[0]}-${entryIds[0]}-unmapped`,
          source: exitIds[0],
          target: entryIds[0],
          sourceHandle: undefined,
          targetHandle: undefined,
          type: "smoothstep",
          animated: true,
          style: feedbackStyle,
          label: "feedback (unmapped)",
          markerEnd: feedbackMarker,
          data: { synthetic: true, feedback: true, unmapped: true },
        });
      }

      rfEdges.push(...syntheticEdges);
    }

    let finalNodes = needsAutoLayout(rfNodes) ? layoutGraph(rfNodes, rfEdges) : rfNodes;
    let finalEdges = rfEdges;
    const sgGroups = (sg.metadata as unknown as Record<string, unknown>)?.loop_groups as LoopGroup[] | undefined;
    const loadedGroups = sgGroups ?? [];
    if (loadedGroups.length > 0) {
      const injected = injectLoopGroups(finalNodes, finalEdges, loadedGroups);
      finalNodes = injected.nodes;
      finalEdges = injected.edges;
    }
    set({ layerStack: newStack, nodes: finalNodes, edges: finalEdges, loopGroups: loadedGroups, selectedNodeId: null, selectedEdgeId: null });
  },

  drillOut: () => {
    const { danGraph, layerStack } = get();
    if (!danGraph || layerStack.length === 0) return;
    const newStack = layerStack.slice(0, -1);
    const currentGraph = resolveGraphAtStack(danGraph, newStack);
    if (!currentGraph) {
      set({ layerStack: [], selectedNodeId: null, selectedEdgeId: null });
      const { nodes: rootNodes, edges: rootEdges } = danGraphToReactFlow(danGraph);
      set({ nodes: rootNodes, edges: rootEdges, loopGroups: [] });
      get().addToast({ type: "warning", message: "Navigation error — reset to root" });
      return;
    }
    const { nodes: rfNodes, edges: rfEdges } = danGraphToReactFlow(currentGraph);
    let finalNodes = needsAutoLayout(rfNodes) ? layoutGraph(rfNodes, rfEdges) : rfNodes;
    let finalEdges = rfEdges;
    const groups = (currentGraph.metadata as unknown as Record<string, unknown>)?.loop_groups as LoopGroup[] | undefined;
    const loadedGroups = groups ?? [];
    if (loadedGroups.length > 0) {
      const injected = injectLoopGroups(finalNodes, finalEdges, loadedGroups);
      finalNodes = injected.nodes;
      finalEdges = injected.edges;
    }
    set({ layerStack: newStack, nodes: finalNodes, edges: finalEdges, loopGroups: loadedGroups, selectedNodeId: null, selectedEdgeId: null });
  },

  jumpToLayer: (index) => {
    const { danGraph, layerStack } = get();
    if (!danGraph) return;
    let targetGraph: DanGraph;
    let newStack: typeof layerStack;
    if (index < 0 || index >= layerStack.length) {
      newStack = [];
      targetGraph = danGraph;
    } else {
      newStack = layerStack.slice(0, index + 1);
      const resolved = resolveGraphAtStack(danGraph, newStack);
      if (!resolved) {
        newStack = [];
        targetGraph = danGraph;
        get().addToast({ type: "warning", message: "Navigation error — reset to root" });
      } else {
        targetGraph = resolved;
      }
    }
    const { nodes: rfNodes, edges: rfEdges } = danGraphToReactFlow(targetGraph);
    let finalNodes = needsAutoLayout(rfNodes) ? layoutGraph(rfNodes, rfEdges) : rfNodes;
    let finalEdges = rfEdges;
    const groups = (targetGraph.metadata as unknown as Record<string, unknown>)?.loop_groups as LoopGroup[] | undefined;
    const loadedGroups = groups ?? [];
    if (loadedGroups.length > 0) {
      const injected = injectLoopGroups(finalNodes, finalEdges, loadedGroups);
      finalNodes = injected.nodes;
      finalEdges = injected.edges;
    }
    set({ layerStack: newStack, nodes: finalNodes, edges: finalEdges, loopGroups: loadedGroups, selectedNodeId: null, selectedEdgeId: null });
  },

  // -- 5-4: Build palette -------------------------------------------------------

  setSelectedEdgeType: (type) => set({ selectedEdgeType: type }),

  addTemplateNode: (templateId, position) => {
    const { danGraph } = get();
    if (!danGraph) return;
    get().pushSnapshot();
    const template = PREDEFINED_AGENT_TEMPLATES.find((t) => t.id === templateId);
    if (!template) return;
    const result = template.factory(position);
    set({
      danGraph: {
        ...danGraph,
        sub_graphs: { ...danGraph.sub_graphs, ...result.subGraphs },
      },
      dirty: true,
    });
    get().addNode(result.node);
  },

  // -- 6-7: Workflow as node ------------------------------------------------

  addGraphAsNode: async (graphId, position) => {
    const { danGraph, graphId: currentGraphId } = get();
    if (!danGraph) return;
    if (graphId === currentGraphId) {
      get().addToast({ type: "error", message: "Cannot insert a graph into itself" });
      return;
    }
    try {
      const { data } = await api.getGraph(graphId, { layout: true });
      const importedGraph = data as unknown as DanGraph;
      if (!importedGraph?.nodes || !importedGraph?.edges) {
        get().addToast({ type: "error", message: "Invalid graph data — cannot import" });
        return;
      }
      const { graphAsCompositeNode } = await import("../lib/graphImporter");
      const result = graphAsCompositeNode(graphId, importedGraph, position);
      get().pushSnapshot();
      set({
        danGraph: {
          ...danGraph,
          sub_graphs: { ...danGraph.sub_graphs, ...(result.subGraphs as Record<string, DanGraph>) },
        },
        dirty: true,
      });
      get().addNode(result.node);
      get().addToast({ type: "success", message: `Imported "${graphId}" as node` });
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to import graph" });
    }
  },

  // -- 7-9: Create empty sub-graph for parallel_subagents branch -------------

  createEmptySubGraph: (graphKey: string) => {
    const { danGraph, layerStack } = get();
    if (!danGraph) return;
    const currentGraph = resolveGraphAtStack(danGraph, layerStack);
    if (!currentGraph || currentGraph.sub_graphs?.[graphKey]) return;
    get().pushSnapshot();
    const emptySub: DanGraph = {
      version: "dan_graph_v1",
      metadata: { name: graphKey },
      nodes: [],
      edges: [],
      sub_graphs: {},
      entry_points: [],
      exit_points: [],
      shared_context: [],
      artifact_refs: [],
    };
    const updatedCurrent = {
      ...currentGraph,
      sub_graphs: { ...currentGraph.sub_graphs, [graphKey]: emptySub },
    };
    const updated = deepSetSubGraph(danGraph, layerStack, updatedCurrent);
    set({ danGraph: updated, dirty: true });
  },

  // -- 9-3: Boundary validators -----------------------------------------------

  addBoundaryValidators: async (nodeId) => {
    const { graphId } = get();
    if (!graphId) return;
    try {
      const saved = await get().saveGraph();
      if (!saved) return;
      await api.addBoundaryValidators(graphId, nodeId);
      await get().loadGraph(graphId);
      get().addToast({ type: "success", message: "Boundary validators inserted" });
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to add boundary validators" });
    }
  },

  // -- 6-5: InputNode ephemeral values -----------------------------------------

  setInputNodeValue: (nodeId, varName, value) => {
    set((s) => ({
      inputNodeValues: {
        ...s.inputNodeValues,
        [nodeId]: { ...s.inputNodeValues[nodeId], [varName]: value },
      },
    }));
  },

  // -- 6-5: Command palette ---------------------------------------------------

  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),

  // -- 5-5: UI polish ---------------------------------------------------------

  addToast: (toast) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }));
  },

  removeToast: (id) => {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },

  applyAutoLayout: () => {
    const { nodes, edges } = get();
    if (nodes.length === 0) return;
    get().pushSnapshot();
    const laid = layoutGraph(nodes, edges);
    set({ nodes: laid, dirty: true });
  },

  updateEdgeData: (edgeId, data) => {
    get().pushSnapshot();
    set((s) => ({
      edges: s.edges.map((e) => {
        if (e.id !== edgeId) return e;
        const prev = (e.data?.danEdge ?? {}) as Record<string, unknown>;
        const merged = { ...prev, ...data };
        const edgeType = (merged.edge_type as string) ?? "data";
        return {
          ...e,
          animated: edgeType === "context",
          label: edgeType !== "data" ? edgeType : undefined,
          style: { ...e.style, stroke: EDGE_COLORS[edgeType] ?? "#94a3b8" },
          data: { ...e.data, danEdge: merged },
        };
      }),
      dirty: true,
    }));
  },

  // -- 6-5: Sub-graph grouping -------------------------------------------------

  groupIntoComposite: () => {
    const { selectedNodeIds, nodes, edges, danGraph } = get();
    if (selectedNodeIds.size < 2 || !danGraph) {
      get().addToast({ type: "warning", message: "Select 2+ nodes to group" });
      return;
    }

    const selIds = selectedNodeIds;
    const selNodes = nodes.filter((n) => selIds.has(n.id));
    const allDanEdges = edges.map(
      (e) => (e.data?.danEdge ?? { id: e.id, edge_type: "data", source_node_id: e.source, source_port: "", target_node_id: e.target, target_port: "" }) as Record<string, unknown>,
    );

    const internalEdges = allDanEdges.filter(
      (e) => selIds.has(e.source_node_id as string) && selIds.has(e.target_node_id as string),
    );
    const inboundCut = allDanEdges.filter(
      (e) => !selIds.has(e.source_node_id as string) && selIds.has(e.target_node_id as string),
    );
    const outboundCut = allDanEdges.filter(
      (e) => selIds.has(e.source_node_id as string) && !selIds.has(e.target_node_id as string),
    );

    const cutEdges = [...inboundCut, ...outboundCut];
    const nonDataCut = cutEdges.find((e) => e.edge_type !== "data");
    if (nonDataCut) {
      get().addToast({ type: "error", message: "Cannot group: control/context edges cross the boundary." });
      return;
    }

    get().pushSnapshot();

    const compositeId = `composite_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const bodyKey = `sub_${compositeId}`;

    const makeUnique = (base: string, existing: Set<string>): string => {
      if (!existing.has(base)) { existing.add(base); return base; }
      let i = 2;
      while (existing.has(`${base}_${i}`)) i++;
      const name = `${base}_${i}`;
      existing.add(name);
      return name;
    };

    const inputPortNames = new Set<string>();
    const outputPortNames = new Set<string>();
    const inputMappings: Record<string, string> = {};
    const outputMappings: Record<string, string> = {};

    const newInboundEdges: Record<string, unknown>[] = [];
    for (const e of inboundCut) {
      const tp = e.target_port as string;
      const portName = makeUnique(`in_${tp}`, inputPortNames);
      inputMappings[portName] = tp;
      newInboundEdges.push({
        id: `e-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        edge_type: "data",
        source_node_id: e.source_node_id,
        source_port: e.source_port,
        target_node_id: compositeId,
        target_port: portName,
        ui: {},
        metadata: {},
      });
    }

    const newOutboundEdges: Record<string, unknown>[] = [];
    for (const e of outboundCut) {
      const sp = e.source_port as string;
      const portName = makeUnique(`out_${sp}`, outputPortNames);
      outputMappings[sp] = portName;
      newOutboundEdges.push({
        id: `e-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        edge_type: "data",
        source_node_id: compositeId,
        source_port: portName,
        target_node_id: e.target_node_id,
        target_port: e.target_port,
        ui: {},
        metadata: {},
      });
    }

    const cx = selNodes.reduce((s, n) => s + n.position.x, 0) / selNodes.length;
    const cy = selNodes.reduce((s, n) => s + n.position.y, 0) / selNodes.length;

    const compositeNode: DanNode = {
      id: compositeId,
      node_type: "composite",
      name: "Grouped",
      description: "",
      input_ports: [...inputPortNames].map((n) => ({ name: n, schema: {}, required: false })),
      output_ports: [...outputPortNames].map((n) => ({ name: n, schema: {} })),
      position: { x: cx, y: cy },
      ui: {},
      metadata: {},
      body_graph: bodyKey,
      input_mappings: inputMappings,
      output_mappings: outputMappings,
      is_blackbox: false,
    } as DanNode;

    const entryPoints = [...new Set(inboundCut.map((e) => e.target_node_id as string))];
    const exitPoints = [...new Set(outboundCut.map((e) => e.source_node_id as string))];

    const subGraphNodes = selNodes.map((n) => n.data as unknown as DanNode);
    const subGraphEdges = internalEdges as unknown as DanEdge[];

    const subGraph: DanGraph = {
      version: "dan_graph_v1",
      metadata: { name: bodyKey },
      nodes: subGraphNodes,
      edges: subGraphEdges,
      sub_graphs: {},
      entry_points: entryPoints,
      exit_points: exitPoints,
      shared_context: [],
      artifact_refs: [],
    };

    const cutEdgeIds = new Set(cutEdges.map((e) => e.id as string));
    const selIdSet = selIds;
    const remainingRfNodes = nodes.filter((n) => !selIdSet.has(n.id));
    const remainingRfEdges = edges.filter(
      (e) =>
        !selIdSet.has(e.source) &&
        !selIdSet.has(e.target) &&
        !cutEdgeIds.has(e.id),
    );

    const rfComposite = danNodeToReactFlow(compositeNode);
    const rfNewIn = newInboundEdges.map((e) => danEdgeToReactFlow(e as unknown as DanEdge));
    const rfNewOut = newOutboundEdges.map((e) => danEdgeToReactFlow(e as unknown as DanEdge));

    set({
      nodes: [...remainingRfNodes, rfComposite],
      edges: [...remainingRfEdges, ...rfNewIn, ...rfNewOut],
      danGraph: {
        ...danGraph,
        sub_graphs: { ...danGraph.sub_graphs, [bodyKey]: subGraph },
      },
      selectedNodeId: compositeId,
      selectedNodeIds: new Set<string>(),
      dirty: true,
    });
  },

  // -- 6-10: Loop groups -------------------------------------------------------

  createLoopGroup: (gateNodeId, memberNodeIds, label) => {
    get().pushSnapshot();
    const groupId = `lg-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    const newGroup: LoopGroup = {
      id: groupId,
      label: label ?? "Loop Group",
      gateNodeId,
      memberNodeIds,
      collapsed: false,
    };
    const updatedGroups = [...get().loopGroups, newGroup];
    const { nodes: clean, edges: cleanEdges } = stripLoopGroups(get().nodes, get().edges);
    const { nodes, edges } = injectLoopGroups(clean, cleanEdges, updatedGroups);
    set({ loopGroups: updatedGroups, nodes, edges, dirty: true });
  },

  toggleLoopGroup: (groupId) => {
    get().pushSnapshot();
    const updatedGroups = get().loopGroups.map((g) =>
      g.id === groupId ? { ...g, collapsed: !g.collapsed } : g,
    );
    const { nodes: clean, edges: cleanEdges } = stripLoopGroups(get().nodes, get().edges);
    const { nodes, edges } = injectLoopGroups(clean, cleanEdges, updatedGroups);
    set({ loopGroups: updatedGroups, nodes, edges, dirty: true });
  },

  removeLoopGroup: (groupId) => {
    get().pushSnapshot();
    const updatedGroups = get().loopGroups.filter((g) => g.id !== groupId);
    const { nodes: clean, edges: cleanEdges } = stripLoopGroups(get().nodes, get().edges);
    const { nodes, edges } = injectLoopGroups(clean, cleanEdges, updatedGroups);
    set({ loopGroups: updatedGroups, nodes, edges, dirty: true });
  },

  // -- 10-9 / 12-2: Multi-mode chat
  setChatMode: (mode) => setGraphChatMode(set, mode),
  openDebugWithError: (errorContext) =>
    openDebugWithErrorForGraphStore(set, errorContext),
  focusLogPanel: () => {
    focusLogPanelForGraphStore(set);
  },
  loadRunLogs: async (runId) => {
    await loadRunLogsForGraphStore(set, get, runId);
  },
  focusHistoryPanel: (runId) => {
    focusHistoryPanelForGraphStore(set, runId);
  },

  // -- 18-4: Token analytics actions -------------------------------------------

  setTokenHeatmapEnabled: (enabled) => set({ tokenHeatmapEnabled: enabled }),
  setShowEdgeTokenLabels: (enabled) => set({ showEdgeTokenLabels: enabled }),

  fetchTokenAnalytics: async () => {
    const { runId, runStatus } = get();
    if (!runId || (runStatus !== "completed" && runStatus !== "failed")) return;
    set({ analyticsLoading: true });
    try {
      const [breakdownRes, reportRes, mutationsRes] = await Promise.allSettled([
        api.fetchTokenBreakdown(runId),
        api.fetchOptimizationReport(runId),
        api.fetchOptimizationMutations(runId),
      ]);
      const updates: Partial<GraphState> = { analyticsLoading: false };
      if (breakdownRes.status === "fulfilled") {
        updates.tokenBreakdowns = breakdownRes.value.nodes as Record<string, TokenBreakdown>;
      }
      if (reportRes.status === "fulfilled") {
        updates.wasteFindings = (reportRes.value.report?.findings ?? []) as WasteFinding[];
      }
      if (mutationsRes.status === "fulfilled") {
        updates.optimizationMutations = (mutationsRes.value.mutations ?? []) as OptimizationMutation[];
      }
      set(updates);

      // Compute estimated token counts per data edge
      const { edges, nodeOutputs, nodeUsage } = get();
      const breakdowns = updates.tokenBreakdowns ?? get().tokenBreakdowns;
      const incomingDataCount = new Map<string, number>();
      const outgoingDataCount = new Map<string, number>();
      for (const e of edges) {
        const de = e.data?.danEdge as Record<string, unknown> | undefined;
        if ((de?.edge_type ?? "data") !== "data") continue;
        incomingDataCount.set(e.target, (incomingDataCount.get(e.target) ?? 0) + 1);
        outgoingDataCount.set(e.source, (outgoingDataCount.get(e.source) ?? 0) + 1);
      }
      const edgeTokenCounts: Record<string, number> = {};
      for (const edge of edges) {
        const danEdge = edge.data?.danEdge as Record<string, unknown> | undefined;
        if ((danEdge?.edge_type ?? "data") !== "data") continue;
        const targetBd = breakdowns[edge.target];
        if (targetBd?.context_edge_tokens && targetBd.context_edge_tokens > 0) {
          const inCount = incomingDataCount.get(edge.target) ?? 1;
          edgeTokenCounts[edge.id] = Math.round(targetBd.context_edge_tokens / Math.max(inCount, 1));
          continue;
        }
        const output = nodeOutputs[edge.source];
        if (output) {
          const len = JSON.stringify(output).length;
          edgeTokenCounts[edge.id] = Math.ceil(len / 4);
          continue;
        }
        const srcUsage = nodeUsage[edge.source];
        if (srcUsage?.total_tokens) {
          const outCount = outgoingDataCount.get(edge.source) ?? 1;
          edgeTokenCounts[edge.id] = Math.round(srcUsage.total_tokens * 0.3 / Math.max(outCount, 1));
        }
      }
      set({ edgeTokenCounts });
    } catch {
      set({ analyticsLoading: false });
    }
  },

  applyOptimizationMutation: async (index) => {
    const { optimizationMutations, graphId, addToast, loadGraph } = get();
    const mutation = optimizationMutations[index];
    if (!mutation || !graphId) return;
    try {
      const result = await api.applyMutation(graphId, mutation.mutation_plan as Record<string, unknown>);
      if (result.success) {
        addToast({ type: "success", message: `Applied: ${mutation.finding.suggestion.slice(0, 60)}` });
        // Remove the applied mutation from the list
        set((s) => ({
          optimizationMutations: s.optimizationMutations.filter((_, i) => i !== index),
        }));
        // Reload graph to reflect mutations
        await loadGraph(graphId);
      } else {
        const errMsg = result.errors?.[0]?.message ?? "Unknown error";
        addToast({ type: "error", message: `Failed to apply optimization: ${errMsg}` });
      }
    } catch (err: unknown) {
      addToast({ type: "error", message: `Optimization error: ${(err as Error).message}` });
    }
  },

  openBuildWithAI: async () => {
    await openBuildWithAIForGraphStore(set, get);
  },

  // -- 6-1: History (undo/redo) -------------------------------------------------

  pushSnapshot: () => {
    const { nodes, edges, danGraph, runStatus, _history } = get();
    if (runStatus === "running") return;
    const snapshot: GraphSnapshot = {
      nodes: structuredClone(nodes),
      edges: structuredClone(edges),
      danGraph: danGraph ? structuredClone(danGraph) : null,
    };
    const past = [..._history.past, snapshot].slice(-MAX_HISTORY);
    set({ _history: { past, future: [] } });
  },

  undo: () => {
    const { _history, nodes, edges, danGraph, runStatus } = get();
    if (runStatus === "running" || _history.past.length === 0) return;
    const current: GraphSnapshot = {
      nodes: structuredClone(nodes),
      edges: structuredClone(edges),
      danGraph: danGraph ? structuredClone(danGraph) : null,
    };
    const past = [..._history.past];
    const snapshot = past.pop()!;
    set({
      nodes: snapshot.nodes,
      edges: snapshot.edges,
      danGraph: snapshot.danGraph,
      _history: { past, future: [current, ..._history.future] },
      dirty: true,
    });
  },

  redo: () => {
    const { _history, nodes, edges, danGraph, runStatus } = get();
    if (runStatus === "running" || _history.future.length === 0) return;
    const current: GraphSnapshot = {
      nodes: structuredClone(nodes),
      edges: structuredClone(edges),
      danGraph: danGraph ? structuredClone(danGraph) : null,
    };
    const [snapshot, ...rest] = _history.future;
    set({
      nodes: snapshot.nodes,
      edges: snapshot.edges,
      danGraph: snapshot.danGraph,
      _history: { past: [..._history.past, current], future: rest },
      dirty: true,
    });
  },

  // -- 6-9: Tab lifecycle actions -----------------------------------------------

  openTab: async (graphId) => {
    const { activeTabId } = get();

    if (activeTabId) {
      const snapshot = _snapshotActiveTab();
      set((s) => ({ tabCache: { ...s.tabCache, [activeTabId]: snapshot } }));
    }

    closeGraphRunSocket(get().ws);
    set({ ws: null });

    const isBlank = graphId === "blank";
    const tabId = crypto.randomUUID();
    const newTab: TabInfo = {
      id: tabId,
      graphId: isBlank ? "" : graphId,
      graphName: isBlank ? "blank" : graphId,
      cachedRunStatus: null,
    };

    set((s) => ({
      tabs: [...s.tabs, newTab],
      activeTabId: tabId,
      graphId: isBlank ? null : s.graphId,
      danGraph: isBlank ? null : s.danGraph,
      graphRevision: isBlank ? null : s.graphRevision,
      nodes: isBlank ? [] : s.nodes,
      edges: isBlank ? [] : s.edges,
      dirty: false,
      ...createEmptyGraphEditorState(),
      chatMode: "auto" as const,
      chatFocusTrigger: 0,
    }));

    if (!isBlank) {
      await get().loadGraph(graphId);
    }
    _persistTabState();
  },

  openTabFromTemplate: async (templateId) => {
    const template = PREDEFINED_AGENT_TEMPLATES.find((t) => t.id === templateId);
    if (!template) return;
    const result = template.factory({ x: 200, y: 200 });
    const danGraph: DanGraph = {
      version: "dan_graph_v1",
      metadata: { name: template.label },
      nodes: [result.node],
      edges: [],
      sub_graphs: result.subGraphs,
      entry_points: [result.node.id],
      exit_points: [result.node.id],
      shared_context: [],
      artifact_refs: [],
    };
    const { nodes, edges } = danGraphToReactFlow(danGraph);
    const { activeTabId } = get();
    if (activeTabId) {
      const snapshot = _snapshotActiveTab();
      set((s) => ({ tabCache: { ...s.tabCache, [activeTabId]: snapshot } }));
    }
    closeGraphRunSocket(get().ws);
    set({ ws: null });
    const tabId = crypto.randomUUID();
    const newTab: TabInfo = {
      id: tabId,
      graphId: "",
      graphName: template.label,
      cachedRunStatus: null,
    };
    set((s) => ({
      tabs: [...s.tabs, newTab],
      activeTabId: tabId,
      graphId: null,
      danGraph,
      graphRevision: null,
      nodes,
      edges,
      dirty: true,
      ...createEmptyGraphEditorState(),
    }));
    get().addToast({ type: "success", message: `Opened "${template.label}" starter` });
    _persistTabState();
  },

  replaceActiveTabGraph: async (graphId) => {
    const { activeTabId, dirty, tabs } = get();
    if (!activeTabId) {
      await get().openTab(graphId);
      return;
    }

    const activeTab = tabs.find((t) => t.id === activeTabId);
    if (!activeTab) {
      await get().openTab(graphId);
      return;
    }
    if (activeTab.graphId === graphId) return;

    if (dirty && !window.confirm("Unsaved changes will be lost. Switch template in this tab?")) return;

    closeGraphRunSocket(get().ws);
    set({ ws: null });

    set((s) => {
      const newCache = { ...s.tabCache };
      delete newCache[activeTabId];
      return {
        tabCache: newCache,
        tabs: s.tabs.map((t) =>
          t.id === activeTabId ? { ...t, graphId, graphName: graphId, cachedRunStatus: null } : t,
        ),
        ...createEmptyGraphEditorState(),
      };
    });

    await get().loadGraph(graphId);
    _persistTabState();
  },

  switchTab: async (tabId) => {
    const { activeTabId, tabs } = get();
    if (tabId === activeTabId) return;

    const targetTab = tabs.find((t) => t.id === tabId);
    if (!targetTab) return;

    if (activeTabId) {
      const snapshot = _snapshotActiveTab();
      const updatedTabs = tabs.map((t) =>
        t.id === activeTabId ? { ...t, cachedRunStatus: get().runStatus } : t,
      );
      set((s) => ({
        tabCache: { ...s.tabCache, [activeTabId]: snapshot },
        tabs: updatedTabs,
      }));
    }

    closeGraphRunSocket(get().ws);
    set({ ws: null });

    const cached = get().tabCache[tabId];
    if (cached) {
      _restoreTab(cached);
      set((s) => {
        const newCache = { ...s.tabCache };
        delete newCache[tabId];
        return { tabCache: newCache, activeTabId: tabId };
      });
    } else {
      set({ activeTabId: tabId });
      await get().loadGraph(targetTab.graphId);
    }

    const { runId: restoredRunId, runStatus: restoredRunStatus } = get();
    if (restoredRunId && (restoredRunStatus === "running" || restoredRunStatus === "pending")) {
      const ws = api.connectRunEvents(
        restoredRunId,
        (event) => get().handleRunEvent(event),
        () => set({ ws: null }),
      );
      set({ ws });
    }

    _persistTabState();
  },

  closeTab: async (tabId) => {
    const { tabs, activeTabId, tabCache } = get();

    const isDirty = tabId === activeTabId
      ? get().dirty
      : tabCache[tabId]?.dirty ?? false;
    if (isDirty && !window.confirm("Unsaved changes will be lost. Close anyway?")) return;

    if (tabs.length <= 1) {
      set((s) => {
        const newCache = { ...s.tabCache };
        delete newCache[tabId];
        return { tabs: s.tabs.filter((t) => t.id !== tabId), tabCache: newCache };
      });
      await get().openTab("blank");
      return;
    }

    if (tabId === activeTabId) {
      const idx = tabs.findIndex((t) => t.id === tabId);
      const nextTab = tabs[idx + 1] ?? tabs[idx - 1];
      if (nextTab) await get().switchTab(nextTab.id);
    }

    set((s) => {
      const newCache = { ...s.tabCache };
      delete newCache[tabId];
      return {
        tabs: s.tabs.filter((t) => t.id !== tabId),
        tabCache: newCache,
      };
    });

    _persistTabState();
  },

  refreshTab: async () => {
    const { graphId } = get();
    if (!graphId) {
      get().addToast({ type: "warning", message: "No graph loaded in this tab" });
      return;
    }

    closeGraphRunSocket(get().ws);
    set({
      ws: null,
      ...createEmptyGraphEditorState(),
    });

    await get().loadGraph(graphId);
    _persistTabState();
    get().addToast({ type: "success", message: "Tab refreshed" });
  },

  restoreTabs: async () => {
    const stored = readPersistedGraphTabState();

    if (stored?.tabs?.length) {
      set({ tabs: stored.tabs, activeTabId: stored.activeTabId });

      const activeTab = stored.tabs.find((t) => t.id === stored!.activeTabId);
      if (activeTab?.graphId) {
        await get().loadGraph(activeTab.graphId);
      }

      const activeRunRef = stored.runs?.[stored.activeTabId!];
      if (activeRunRef?.runId) {
        try {
          const info = await api.getRun(activeRunRef.runId);
          set({
            runId: activeRunRef.runId,
            runStatus: info.status,
            nodeStatuses: info.node_statuses ?? {},
            nodeOutputs: info.outputs ? { _final: info.outputs } : {},
          });
          if (info.status === "pending" || info.status === "running") {
            const ws = api.connectRunEvents(
              activeRunRef.runId,
              (event) => get().handleRunEvent(event),
              () => set({ ws: null }),
            );
            set({ ws });
            get().addToast({ type: "info", message: `Reconnected to run ${activeRunRef.runId.slice(0, 8)}` });
          }
        } catch { /* run may no longer exist */ }
      }

      _persistTabState();
      return;
    }

    // Migration: check for legacy single-run key
    try {
      const raw = sessionStorage.getItem("dan_active_run");
      if (raw) {
        sessionStorage.removeItem("dan_active_run");
        const legacy = JSON.parse(raw) as { runId: string; graphId: string };
        if (legacy.graphId !== get().graphId) {
          await get().loadGraph(legacy.graphId);
        }
        const { activeTabId: curTabId } = get();
        if (curTabId) {
          set((s) => ({
            tabs: s.tabs.map((t) =>
              t.id === curTabId ? { ...t, graphId: legacy.graphId, graphName: legacy.graphId } : t,
            ),
          }));
        }
        try {
          const info = await api.getRun(legacy.runId);
          set({
            runId: legacy.runId,
            runStatus: info.status,
            nodeStatuses: info.node_statuses ?? {},
            nodeOutputs: info.outputs ? { _final: info.outputs } : {},
          });
          if (info.status === "pending" || info.status === "running") {
            const ws = api.connectRunEvents(
              legacy.runId,
              (event) => get().handleRunEvent(event),
              () => set({ ws: null }),
            );
            set({ ws });
            get().addToast({ type: "info", message: `Reconnected to run ${legacy.runId.slice(0, 8)}` });
          }
        } catch { /* run may no longer exist */ }
      }
    } catch { /* corrupt */ }

    _persistTabState();
  },

  };
});
