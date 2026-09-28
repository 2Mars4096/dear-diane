/**
 * API client for the DAN backend server.
 */

import { requestJson, type RequestOptions } from "./http";

const BASE = "/api";

function resolveApiWebSocketBase(): string {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  if (import.meta.env.DEV) {
    // In Vite dev, the HMR page can hold a separate websocket open already.
    // Route chat/run streams straight to the backend instead of the proxy.
    return `${protocol}//${location.hostname}:8000`;
  }
  return `${protocol}//${location.host}`;
}

export function buildApiWebSocketUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${resolveApiWebSocketBase()}${normalizedPath}`;
}

const request = <T>(path: string, init?: RequestOptions) => requestJson<T>(`${BASE}${path}`, init);

export function isApiStatusError(error: unknown, status: number): boolean {
  return error instanceof Error && error.message.startsWith(`${status}:`);
}

export interface ServerHealth {
  status: string;
  pid: number;
  timestamp: number;
}

export const getServerHealth = () => request<ServerHealth>("/health");

export interface WorkspaceNoteSummary {
  path: string;
  relative_path: string;
  title: string;
  layout?: string;
  section?: string;
  tags?: string[];
  categories?: string[];
  citations?: string[];
  page_id?: string;
  date?: string;
  lastmod?: string;
  draft?: boolean;
  size: number;
  mtime: number;
}

export interface WorkspaceLearnSession {
  id: string;
  title: string;
  summary: string;
  duration_minutes: number;
  source_heading: string;
  body: string;
  objectives: string[];
  practice: string[];
}

export interface WorkspaceLearnCourse {
  version: number;
  course_id: string;
  note_path: string;
  note_relative_path: string;
  note_title: string;
  note_mtime: number;
  content_signature: string;
  source: string;
  generated_at: string;
  updated_at: string;
  stale?: boolean;
  sessions: WorkspaceLearnSession[];
  progress: {
    active_session_id: string | null;
    completed_session_ids: string[];
  };
}

export interface WorkspaceFileEntry {
  path: string;
  relative_path: string;
  name: string;
  parent: string;
  is_directory: boolean;
  size: number;
  mtime: number;
  depth: number;
}

export interface WorkspaceSkillSuggestion {
  token: string;
  name: string;
  description: string;
  source_scope: string;
}

export type WorkspaceRootSuggestionKind =
  | "current"
  | "match"
  | "nearby"
  | "workspace"
  | "recent";

export interface WorkspaceRootSuggestion {
  path: string;
  name: string;
  label: string;
  kind: WorkspaceRootSuggestionKind;
}

export interface WorkspaceWireGuardStatus {
  service: "wireguard";
  mode: string;
  interface: string;
  launchd_label: string;
  config_path: string;
  config_present: boolean;
  active: boolean;
  status: string;
  wg_present: boolean;
  wg_exit_code: number | null;
  stdout: string;
  stderr: string;
  mutating_actions_enabled: boolean;
  safe_actions: string[];
  conflict_policy: string;
}

export const listWorkspaceNotes = () =>
  request<{ root: string; notes: WorkspaceNoteSummary[] }>("/workspace-notes");

export const readWorkspaceNote = (path: string, init?: RequestOptions) =>
  request<{ note: WorkspaceNoteSummary; content: string }>(
    `/workspace-notes/read?path=${encodeURIComponent(path)}`,
    init,
  );

export const writeWorkspaceNote = (path: string, content: string) =>
  request<{ status: string; note: WorkspaceNoteSummary | null }>(
    "/workspace-notes/write",
    {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    },
  );

export const getWorkspaceNoteLearnCourse = (path: string) =>
  request<{
    status: string;
    root: string;
    course_path: string;
    note: WorkspaceNoteSummary;
    course: WorkspaceLearnCourse | null;
  }>(`/workspace-notes/learn/course?path=${encodeURIComponent(path)}`);

export const generateWorkspaceNoteLearnCourse = (path: string) =>
  request<{
    status: string;
    root: string;
    course_path: string;
    note: WorkspaceNoteSummary;
    course: WorkspaceLearnCourse;
  }>("/workspace-notes/learn/course", {
    method: "POST",
    body: JSON.stringify({ path }),
    timeoutMs: 30000,
  });

export const updateWorkspaceNoteLearnProgress = (
  path: string,
  body: {
    active_session_id?: string | null;
    completed_session_ids: string[];
  },
) =>
  request<{
    status: string;
    root: string;
    course_path: string;
    course: WorkspaceLearnCourse;
  }>("/workspace-notes/learn/course/progress", {
    method: "PUT",
    body: JSON.stringify({ path, ...body }),
  });

export const moveWorkspaceNotePath = (source: string, destination: string) =>
  request<{ status: string; root: string; note: WorkspaceNoteSummary | null }>(
    "/workspace-notes/move",
    {
      method: "POST",
      body: JSON.stringify({ source, destination }),
    },
  );

export const listWorkspaceFileTree = (rootPath?: string) =>
  request<{ root: string; entries: WorkspaceFileEntry[] }>(
    rootPath ? `/workspace-files?root_path=${encodeURIComponent(rootPath)}` : "/workspace-files",
  );

export const listWorkspaceSkillSuggestions = (rootPath?: string, limit = 80) => {
  const params = new URLSearchParams();
  if (rootPath?.trim()) params.set("root_path", rootPath.trim());
  params.set("limit", String(limit));
  return request<{ root: string; skills: WorkspaceSkillSuggestion[] }>(
    `/workspace-skills?${params.toString()}`,
  );
};

export const listWorkspaceRootSuggestions = (query?: string, limit?: number) => {
  const params = new URLSearchParams();
  if (query?.trim()) params.set("query", query.trim());
  if (limit !== undefined) params.set("limit", String(limit));
  const suffix = params.toString();
  return request<{ root: string; suggestions: WorkspaceRootSuggestion[] }>(
    `/workspace-roots${suffix ? `?${suffix}` : ""}`,
  );
};

export const getWorkspaceWireGuardStatus = () =>
  request<WorkspaceWireGuardStatus>("/workspace-wireguard");

export const readWorkspaceFile = (path: string, rootPath?: string) => {
  const params = new URLSearchParams({ path });
  if (rootPath) params.set("root_path", rootPath);
  return request<{
    root: string;
    file: WorkspaceFileEntry | null;
    content: string;
    truncated: boolean;
  }>(`/workspace-files/read?${params.toString()}`);
};

const encodeBase64Url = (value: string) => {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
};

export const workspaceFilePreviewUrl = (
  path: string,
  rootPath?: string,
  relativePath?: string,
) => {
  const token = rootPath?.trim() ? encodeBase64Url(rootPath.trim()) : "-";
  const normalizedPath = (relativePath || path).replace(/\\/g, "/").replace(/^\/+/, "");
  const encodedPath = normalizedPath
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `${BASE}/workspace-files/preview/${token}/${encodedPath || encodeURIComponent(path)}`;
};

export const createWorkspaceFolder = (path: string, rootPath?: string) =>
  request<{ status: string; root: string; file: WorkspaceFileEntry | null }>(
    "/workspace-files/mkdir",
    {
      method: "POST",
      body: JSON.stringify({ path, root_path: rootPath }),
    },
  );

export const moveWorkspacePath = (
  source: string,
  destination: string,
  rootPath?: string,
) =>
  request<{ status: string; root: string; file: WorkspaceFileEntry | null }>(
    "/workspace-files/move",
    {
      method: "POST",
      body: JSON.stringify({ source, destination, root_path: rootPath }),
    },
  );
