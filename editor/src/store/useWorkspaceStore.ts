import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { AppMode } from "./useAppStore";

export interface Workspace {
  id: string;
  name: string;
  icon?: string;
  color?: string;
  pinnedPaths: string[];
  lastActiveMode: AppMode;
  openThreadIds: string[];
  activeThreadId: string | null;
  createdAt: number;
  lastAccessedAt: number;
  researchConfig?: {
    pdfRoots?: string[];
    noteRoots?: string[];
    corpusTopic?: string;
  };
}

export const WORKSPACE_COLORS = [
  { id: "red", hex: "#ef4444" },
  { id: "orange", hex: "#f97316" },
  { id: "yellow", hex: "#eab308" },
  { id: "green", hex: "#22c55e" },
  { id: "blue", hex: "#3b82f6" },
  { id: "purple", hex: "#a855f7" },
  { id: "pink", hex: "#ec4899" },
  { id: "gray", hex: "#6b7280" },
] as const;

interface WorkspaceState {
  workspaces: Workspace[];
  activeWorkspaceId: string | null;

  createWorkspace: (name?: string, mode?: AppMode) => string;
  deriveWorkspaceName: (wsId: string) => void;
  removeWorkspace: (id: string) => void;
  setActiveWorkspace: (id: string) => void;
  updateWorkspace: (id: string, updates: Partial<Workspace>) => void;
  reorderWorkspace: (fromIndex: number, toIndex: number) => void;

  openThread: (threadId: string) => void;
  closeThread: (threadId: string) => void;
  setActiveThread: (threadId: string) => void;

  getActiveWorkspace: () => Workspace | undefined;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set, get) => ({
      workspaces: [],
      activeWorkspaceId: null,

      createWorkspace: (name, mode) => {
        const id = `ws-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        let defaultName: string;
        if (name) {
          defaultName = name;
        } else if (mode === "research") {
          defaultName = "Research Project";
        } else if (mode === "content") {
          defaultName = "Content Project";
        } else {
          defaultName = `Workspace ${get().workspaces.length + 1}`;
        }
        const workspace: Workspace = {
          id,
          name: defaultName,
          pinnedPaths: [],
          lastActiveMode: mode || "chat",
          openThreadIds: [],
          activeThreadId: null,
          createdAt: Date.now(),
          lastAccessedAt: Date.now(),
        };
        set((s) => ({
          workspaces: [...s.workspaces, workspace],
          activeWorkspaceId: id,
        }));
        return id;
      },

      deriveWorkspaceName: (wsId) => {
        const ws = get().workspaces.find((w) => w.id === wsId);
        if (!ws) return;

        if (ws.researchConfig?.corpusTopic) {
          get().updateWorkspace(wsId, { name: ws.researchConfig.corpusTopic });
          return;
        }

        if (ws.pinnedPaths.length > 0) {
          const lastSegment = ws.pinnedPaths[0].split("/").filter(Boolean).pop();
          if (lastSegment && lastSegment !== "Scratch") {
            const formatted = lastSegment
              .replace(/[-_]/g, " ")
              .replace(/\b\w/g, (c) => c.toUpperCase());
            get().updateWorkspace(wsId, { name: formatted });
          }
        }
      },

      removeWorkspace: (id) => {
        set((s) => {
          const filtered = s.workspaces.filter((w) => w.id !== id);
          const newActive =
            s.activeWorkspaceId === id
              ? filtered[0]?.id ?? null
              : s.activeWorkspaceId;
          return { workspaces: filtered, activeWorkspaceId: newActive };
        });
      },

      setActiveWorkspace: (id) => {
        set((s) => ({
          activeWorkspaceId: id,
          workspaces: s.workspaces.map((w) =>
            w.id === id ? { ...w, lastAccessedAt: Date.now() } : w,
          ),
        }));
      },

      updateWorkspace: (id, updates) => {
        set((s) => ({
          workspaces: s.workspaces.map((w) =>
            w.id === id ? { ...w, ...updates } : w,
          ),
        }));
      },

      reorderWorkspace: (from, to) => {
        set((s) => {
          const arr = [...s.workspaces];
          const [item] = arr.splice(from, 1);
          arr.splice(to, 0, item);
          return { workspaces: arr };
        });
      },

      openThread: (threadId) => {
        const ws = get().getActiveWorkspace();
        if (!ws) return;
        if (!ws.openThreadIds.includes(threadId)) {
          get().updateWorkspace(ws.id, {
            openThreadIds: [...ws.openThreadIds, threadId],
            activeThreadId: threadId,
          });
        } else {
          get().updateWorkspace(ws.id, { activeThreadId: threadId });
        }
      },

      closeThread: (threadId) => {
        const ws = get().getActiveWorkspace();
        if (!ws) return;
        const newOpen = ws.openThreadIds.filter((id) => id !== threadId);
        const newActive =
          ws.activeThreadId === threadId
            ? (newOpen[newOpen.length - 1] ?? null)
            : ws.activeThreadId;
        get().updateWorkspace(ws.id, {
          openThreadIds: newOpen,
          activeThreadId: newActive,
        });
      },

      setActiveThread: (threadId) => {
        const ws = get().getActiveWorkspace();
        if (ws) get().updateWorkspace(ws.id, { activeThreadId: threadId });
      },

      getActiveWorkspace: () => {
        const { workspaces, activeWorkspaceId } = get();
        return workspaces.find((w) => w.id === activeWorkspaceId);
      },
    }),
    { name: "dan-workspaces" },
  ),
);
