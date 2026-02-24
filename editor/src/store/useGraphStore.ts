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
} from "@xyflow/react";
import type { DanGraph, DanNode } from "../types/graph";
import {
  danGraphToReactFlow,
  danNodeToReactFlow,
  reactFlowToDanGraph,
} from "../lib/graphAdapter";
import * as api from "../lib/api";

export interface LogEntry {
  timestamp: number;
  event_type: string;
  node_id?: string;
  message: string;
  data?: Record<string, unknown>;
}

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
  saveGraph: () => Promise<void>;

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

  // -- Graph lifecycle -------------------------------------------------------

  loadGraphList: async () => {
    const { graphs, last_opened } = await api.listGraphs();
    set({ graphList: graphs });
    if (last_opened && !get().graphId) {
      await get().loadGraph(last_opened);
    }
  },

  loadGraph: async (graphId: string) => {
    const { data } = await api.getGraph(graphId);
    const danGraph = data as unknown as DanGraph;
    const { nodes, edges } = danGraphToReactFlow(danGraph);
    set({ graphId, danGraph, nodes, edges, dirty: false, selectedNodeId: null, selectedEdgeId: null });
  },

  createGraph: async (graphId: string) => {
    const { data } = await api.createGraph(graphId);
    const danGraph = data as unknown as DanGraph;
    const { nodes, edges } = danGraphToReactFlow(danGraph);
    set({ graphId, danGraph, nodes, edges, dirty: false });
    await get().loadGraphList();
  },

  deleteGraph: async (graphId: string) => {
    await api.deleteGraph(graphId);
    if (get().graphId === graphId) {
      set({ graphId: null, danGraph: null, nodes: [], edges: [], dirty: false });
    }
    await get().loadGraphList();
  },

  saveGraph: async () => {
    const { graphId, danGraph, nodes, edges } = get();
    if (!graphId || !danGraph) return;
    const updated = reactFlowToDanGraph(nodes, edges, danGraph);
    await api.updateGraph(graphId, updated as unknown as Record<string, unknown>);
    set({ danGraph: updated, dirty: false });
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
    set((s) => ({
      edges: addEdge(
        {
          ...conn,
          id: edgeId,
          type: "smoothstep",
          data: {
            danEdge: {
              id: edgeId,
              edge_type: "data",
              source_node_id: conn.source,
              source_port: conn.sourceHandle?.replace("port:", "") ?? "",
              target_node_id: conn.target,
              target_port: conn.targetHandle?.replace("port:", "") ?? "",
              ui: {},
              metadata: {},
            },
          },
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
    await get().saveGraph();
    const { run_id } = await api.startRun(graphId, inputs);
    set({ runId: run_id, runStatus: "running", nodeStatuses: {}, nodeOutputs: {}, logs: [] });

    const ws = api.connectRunEvents(
      run_id,
      (event) => get().handleRunEvent(event),
      () => set({ ws: null }),
    );
    set({ ws });
  },

  resumeRun: async () => {
    const { graphId, runId } = get();
    if (!graphId || !runId) return;
    await api.resumeRun(runId, graphId);
    set({ runStatus: "running", nodeStatuses: {}, logs: [] });
    const ws = api.connectRunEvents(
      runId,
      (event) => get().handleRunEvent(event),
      () => set({ ws: null }),
    );
    set({ ws });
  },

  disconnectRun: () => {
    get().ws?.close();
    set({ ws: null });
  },

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
      let newRunStatus = s.runStatus;

      if (nodeId && ["node_started", "node_completed", "node_failed", "node_skipped"].includes(eventType)) {
        newStatuses[nodeId] = eventType;
      }
      if (nodeId && eventType === "node_output" && data.outputs) {
        newOutputs[nodeId] = data.outputs as Record<string, unknown>;
      }
      if (eventType === "run_completed") newRunStatus = "completed";
      if (eventType === "run_failed") newRunStatus = "failed";

      return {
        nodeStatuses: newStatuses,
        nodeOutputs: newOutputs,
        runStatus: newRunStatus,
        logs: [...s.logs.slice(-499), logMsg],
      };
    });
  },
}));
