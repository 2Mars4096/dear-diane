/**
 * API client for the DAN backend server.
 */

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

// -- Graph CRUD --------------------------------------------------------------

export interface GraphListItem {
  graph_id: string;
  name: string;
  description: string;
  updated_at: string | null;
}

export const listGraphs = () =>
  request<{ graphs: GraphListItem[]; last_opened: string | null }>("/graphs");

export const getGraph = (id: string) =>
  request<{ graph_id: string; data: Record<string, unknown> }>(`/graphs/${id}`);

export const createGraph = (graphId: string, data?: Record<string, unknown>) =>
  request<{ graph_id: string; data: Record<string, unknown> }>("/graphs", {
    method: "POST",
    body: JSON.stringify({ graph_id: graphId, data }),
  });

export const updateGraph = (id: string, data: Record<string, unknown>) =>
  request<{ graph_id: string; status: string }>(`/graphs/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });

export const deleteGraph = (id: string) =>
  request<{ graph_id: string; status: string }>(`/graphs/${id}`, {
    method: "DELETE",
  });

// -- Validation --------------------------------------------------------------

export interface ValidationIssue {
  node_id?: string;
  edge_id?: string;
  message: string;
}

export interface ValidationResult {
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export const validateGraph = (graphId: string) =>
  request<ValidationResult>(`/graphs/${graphId}/validate`, { method: "POST" });

// -- Runs --------------------------------------------------------------------

export interface RunInfo {
  run_id: string;
  graph_id: string;
  status: string;
  node_statuses: Record<string, string>;
  started_at: number;
  finished_at: number | null;
  success: boolean | null;
  errors: Record<string, string>;
  outputs: Record<string, unknown>;
}

export const startRun = (graphId: string, inputs?: Record<string, unknown>) =>
  request<{ run_id: string; status: string }>("/runs", {
    method: "POST",
    body: JSON.stringify({ graph_id: graphId, inputs }),
  });

export const resumeRun = (runId: string, graphId: string) =>
  request<{ run_id: string; status: string }>(`/runs/${runId}/resume`, {
    method: "POST",
    body: JSON.stringify({ graph_id: graphId }),
  });

export const getRun = (runId: string) =>
  request<RunInfo>(`/runs/${runId}`);

export const submitHumanInput = (
  runId: string,
  requestId: string,
  nodeId: string,
  response: string,
) =>
  request<{ status: string; request_id: string }>(
    `/runs/${runId}/human-input`,
    {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, node_id: nodeId, response }),
    },
  );

// -- WebSocket ---------------------------------------------------------------

export function connectRunEvents(
  runId: string,
  onEvent: (event: Record<string, unknown>) => void,
  onClose?: () => void,
): WebSocket {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/api/runs/${runId}/events`);
  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch { /* ignore parse errors */ }
  };
  ws.onclose = () => onClose?.();
  return ws;
}
