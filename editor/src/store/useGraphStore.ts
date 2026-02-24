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
import type { DanGraph, DanNode } from "../types/graph";
import {
  danGraphToReactFlow,
  danNodeToReactFlow,
  reactFlowToDanGraph,
  EDGE_COLORS,
} from "../lib/graphAdapter";
import { PREDEFINED_AGENT_TEMPLATES } from "../lib/paletteTemplates";
import { layoutGraph } from "../lib/layout";
import * as api from "../lib/api";

export interface LogEntry {
  timestamp: number;
  event_type: string;
  node_id?: string;
  message: string;
  data?: Record<string, unknown>;
}

// -- 5-3: Rich logging -------------------------------------------------------
export const EVENT_CATEGORY: Record<string, string> = {
  llm_thinking: "thinking",
  tool_call_started: "tool",
  tool_call_result: "tool",
  code_output: "output",
  intermediate_text: "output",
  node_failed: "error",
  run_failed: "error",
  node_started: "lifecycle",
  node_completed: "lifecycle",
  node_skipped: "lifecycle",
  node_output: "lifecycle",
  run_started: "lifecycle",
  run_completed: "lifecycle",
};

interface GraphState {
  // -- Graph identity
  graphId: string | null;
  danGraph: DanGraph | null;
  graphList: api.GraphListItem[];
  dirty: boolean;

  // -- React Flow state
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;

  // -- Run state
  runId: string | null;
  runStatus: string | null;
  nodeStatuses: Record<string, string>;
  nodeOutputs: Record<string, Record<string, unknown>>;
  logs: LogEntry[];
  ws: WebSocket | null;

  // -- Actions: graph lifecycle
  loadGraphList: () => Promise<void>;
  loadGraph: (graphId: string) => Promise<void>;
  createGraph: (graphId: string) => Promise<void>;
  deleteGraph: (graphId: string) => Promise<void>;
  saveGraph: () => Promise<boolean>;

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

  // -- Actions: run lifecycle
  startRun: (inputs?: Record<string, unknown>) => Promise<void>;
  resumeRun: () => Promise<void>;
  disconnectRun: () => void;

  // -- Actions: event handling
  handleRunEvent: (event: Record<string, unknown>) => void;

  // -- 5-3: Rich logging
  selectNodeFromLog: (nodeId: string) => void;

  // -- 5-1: Layer navigation
  layerStack: Array<{ graphKey: string; nodeId: string; nodeName?: string }>;
  drillIn: (nodeId: string) => void;
  drillOut: () => void;
  jumpToLayer: (index: number) => void;

  // -- 5-4: Build palette
  selectedEdgeType: "data" | "control" | "context";
  setSelectedEdgeType: (type: "data" | "control" | "context") => void;
  addTemplateNode: (templateId: string, position: { x: number; y: number }) => void;

  // -- 5-2: Live execution viz
  nodeTimings: Record<string, { start: number; end?: number }>;
  activeExecutionPath: Set<string>;

  // -- 5-5: UI polish
  toasts: Array<{ id: string; type: "success" | "error" | "info" | "warning"; message: string }>;
  addToast: (toast: { type: "success" | "error" | "info" | "warning"; message: string }) => void;
  removeToast: (id: string) => void;
  loadingGraph: boolean;
  savingGraph: boolean;
  applyAutoLayout: () => void;
  updateEdgeData: (edgeId: string, data: Partial<Record<string, unknown>>) => void;
}

export const useGraphStore = create<GraphState>((set, get) => ({
  graphId: null,
  danGraph: null,
  graphList: [],
  dirty: false,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  selectedEdgeId: null,
  runId: null,
  runStatus: null,
  nodeStatuses: {},
  nodeOutputs: {},
  logs: [],
  ws: null,
  layerStack: [],
  selectedEdgeType: "data" as const,
  nodeTimings: {},
  activeExecutionPath: new Set<string>(),
  toasts: [],
  loadingGraph: false,
  savingGraph: false,

  // -- Graph lifecycle -------------------------------------------------------

  loadGraphList: async () => {
    try {
      const { graphs, last_opened } = await api.listGraphs();
      set({ graphList: graphs });
      if (last_opened && !get().graphId) {
        await get().loadGraph(last_opened);
      }
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to load graph list" });
    }
  },

  loadGraph: async (graphId: string) => {
    set({ loadingGraph: true });
    try {
      const { data } = await api.getGraph(graphId);
      const danGraph = data as unknown as DanGraph;
      let { nodes, edges } = danGraphToReactFlow(danGraph);

      // Auto-layout when all nodes share the same position (e.g. builder-generated graphs)
      if (nodes.length > 1) {
        const allSamePos = nodes.every(
          (n) => n.position.x === nodes[0].position.x && n.position.y === nodes[0].position.y,
        );
        if (allSamePos) {
          nodes = layoutGraph(nodes, edges);
        }
      }

      set({ graphId, danGraph, nodes, edges, dirty: false, selectedNodeId: null, selectedEdgeId: null, layerStack: [] });
      get().addToast({ type: "success", message: `Loaded "${graphId}"` });
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to load graph" });
    } finally {
      set({ loadingGraph: false });
    }
  },

  createGraph: async (graphId: string) => {
    try {
      const { data } = await api.createGraph(graphId);
      const danGraph = data as unknown as DanGraph;
      const { nodes, edges } = danGraphToReactFlow(danGraph);
      set({ graphId, danGraph, nodes, edges, dirty: false, layerStack: [] });
      get().addToast({ type: "success", message: `Created "${graphId}"` });
      await get().loadGraphList();
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to create graph" });
    }
  },

  deleteGraph: async (graphId: string) => {
    try {
      await api.deleteGraph(graphId);
      if (get().graphId === graphId) {
        set({ graphId: null, danGraph: null, nodes: [], edges: [], dirty: false });
      }
      get().addToast({ type: "info", message: `Deleted "${graphId}"` });
      await get().loadGraphList();
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to delete graph" });
    }
  },

  saveGraph: async () => {
    const { graphId, danGraph, nodes, edges, layerStack } = get();
    if (!graphId || !danGraph) return false;
    set({ savingGraph: true });
    try {
      let updated: DanGraph;
      if (layerStack.length === 0) {
        updated = reactFlowToDanGraph(nodes, edges, danGraph);
      } else {
        // Layer-aware save: write current RF state back into the correct sub_graph
        const activeKey = layerStack[layerStack.length - 1].graphKey;
        const subBase = (danGraph.sub_graphs?.[activeKey] ?? danGraph) as unknown as DanGraph;
        const updatedSub = reactFlowToDanGraph(nodes, edges, subBase);
        updated = {
          ...danGraph,
          sub_graphs: { ...danGraph.sub_graphs, [activeKey]: updatedSub as unknown as DanGraph },
        };
      }
      await api.updateGraph(graphId, updated as unknown as Record<string, unknown>);
      set({ danGraph: updated, dirty: false });
      get().addToast({ type: "success", message: "Graph saved" });
      return true;
    } catch (err: unknown) {
      get().addToast({ type: "error", message: (err as Error).message ?? "Failed to save graph" });
      return false;
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
    const rfNode = danNodeToReactFlow(node);
    set((s) => ({ nodes: [...s.nodes, rfNode], dirty: true }));
  },

  updateNodeData: (nodeId, data) => {
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n,
      ),
      dirty: true,
    }));
  },

  deleteSelected: () => {
    const { selectedNodeId, selectedEdgeId } = get();
    if (selectedNodeId) {
      set((s) => ({
        nodes: s.nodes.filter((n) => n.id !== selectedNodeId),
        edges: s.edges.filter(
          (e) => e.source !== selectedNodeId && e.target !== selectedNodeId,
        ),
        selectedNodeId: null,
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

  // -- Run lifecycle ---------------------------------------------------------

  startRun: async (inputs) => {
    const { graphId } = get();
    if (!graphId) return;
    try {
      const saved = await get().saveGraph();
      if (!saved) return;
      const { run_id } = await api.startRun(graphId, inputs);
      set({ runId: run_id, runStatus: "running", nodeStatuses: {}, nodeOutputs: {}, nodeTimings: {}, activeExecutionPath: new Set(), logs: [] });
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
      set({ runStatus: "running", nodeStatuses: {}, nodeTimings: {}, activeExecutionPath: new Set(), logs: [] });
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

  disconnectRun: () => {
    get().ws?.close();
    set({ ws: null });
  },

  // -- 5-3: Rich logging -----------------------------------------------------
  selectNodeFromLog: (nodeId) => set({ selectedNodeId: nodeId, selectedEdgeId: null }),

  // -- Event handling --------------------------------------------------------

  handleRunEvent: (event) => {
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
        get().handleRunEvent(be);
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
      let newRunStatus = s.runStatus;

      if (nodeId && ["node_started", "node_completed", "node_failed", "node_skipped"].includes(eventType)) {
        newStatuses[nodeId] = eventType;
      }
      if (nodeId && eventType === "node_output" && data.outputs) {
        newOutputs[nodeId] = data.outputs as Record<string, unknown>;
      }
      if (eventType === "run_completed") newRunStatus = "completed";
      if (eventType === "run_failed") newRunStatus = "failed";

      // -- 5-2: Track node timings
      if (nodeId && eventType === "node_started") {
        newTimings[nodeId] = { start: ts };
      }
      if (nodeId && (eventType === "node_completed" || eventType === "node_failed")) {
        if (newTimings[nodeId]) {
          newTimings[nodeId] = { ...newTimings[nodeId], end: ts };
        }
      }

      return {
        nodeStatuses: newStatuses,
        nodeOutputs: newOutputs,
        nodeTimings: newTimings,
        activeExecutionPath: new Set(Object.keys(newStatuses)),
        runStatus: newRunStatus,
        logs: [...s.logs.slice(-499), logMsg],
      };
    });
  },

  // -- 5-1: Layer navigation ---------------------------------------------------

  drillIn: (nodeId) => {
    const { danGraph, nodes, layerStack } = get();
    if (!danGraph) return;
    const rfNode = nodes.find((n) => n.id === nodeId);
    if (!rfNode) return;
    const d = rfNode.data as unknown as DanNode;
    const bodyGraphKey = (d as Record<string, unknown>).body_graph as string | undefined;
    const isBlackbox = (d as Record<string, unknown>).is_blackbox as boolean | undefined;
    if (!bodyGraphKey || isBlackbox) return;
    const subGraph = danGraph.sub_graphs?.[bodyGraphKey];
    if (!subGraph) return;
    const newStack = [...layerStack, { graphKey: bodyGraphKey, nodeId, nodeName: d.name }];
    const { nodes: rfNodes, edges: rfEdges } = danGraphToReactFlow(subGraph as unknown as DanGraph);
    set({ layerStack: newStack, nodes: rfNodes, edges: rfEdges, selectedNodeId: null, selectedEdgeId: null });
  },

  drillOut: () => {
    const { danGraph, layerStack } = get();
    if (!danGraph || layerStack.length === 0) return;
    const newStack = layerStack.slice(0, -1);
    let currentGraph: DanGraph;
    if (newStack.length === 0) {
      currentGraph = danGraph;
    } else {
      const lastKey = newStack[newStack.length - 1].graphKey;
      currentGraph = (danGraph.sub_graphs?.[lastKey] as unknown as DanGraph) ?? danGraph;
    }
    const { nodes: rfNodes, edges: rfEdges } = danGraphToReactFlow(currentGraph);
    set({ layerStack: newStack, nodes: rfNodes, edges: rfEdges, selectedNodeId: null, selectedEdgeId: null });
  },

  jumpToLayer: (index) => {
    const { danGraph, layerStack } = get();
    if (!danGraph) return;
    if (index < 0 || index >= layerStack.length) {
      const { nodes: rfNodes, edges: rfEdges } = danGraphToReactFlow(danGraph);
      set({ layerStack: [], nodes: rfNodes, edges: rfEdges, selectedNodeId: null, selectedEdgeId: null });
      return;
    }
    const newStack = layerStack.slice(0, index + 1);
    const lastKey = newStack[newStack.length - 1].graphKey;
    const currentGraph = (danGraph.sub_graphs?.[lastKey] as unknown as DanGraph) ?? danGraph;
    const { nodes: rfNodes, edges: rfEdges } = danGraphToReactFlow(currentGraph);
    set({ layerStack: newStack, nodes: rfNodes, edges: rfEdges, selectedNodeId: null, selectedEdgeId: null });
  },

  // -- 5-4: Build palette -------------------------------------------------------

  setSelectedEdgeType: (type) => set({ selectedEdgeType: type }),

  addTemplateNode: (templateId, position) => {
    const { danGraph } = get();
    if (!danGraph) return;
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
    const laid = layoutGraph(nodes, edges);
    set({ nodes: laid, dirty: true });
  },

  updateEdgeData: (edgeId, data) => {
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
}));
