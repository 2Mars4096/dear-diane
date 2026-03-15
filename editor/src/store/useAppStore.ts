import { create } from "zustand";

export type AppMode = "chat" | "operations" | "research" | "development" | "analytics" | "content";

export interface ModeConfig {
  id: AppMode;
  label: string;
  shortcut: string;
  icon: string;
  enabled: boolean;
}

export const MODE_CONFIGS: ModeConfig[] = [
  { id: "chat", label: "Chat", shortcut: "1", icon: "MessageSquare", enabled: true },
  { id: "research", label: "Research", shortcut: "2", icon: "GraduationCap", enabled: true },
  { id: "development", label: "Development", shortcut: "3", icon: "Code", enabled: true },
  { id: "analytics", label: "Analytics", shortcut: "4", icon: "BarChart3", enabled: false },
  { id: "content", label: "Content", shortcut: "5", icon: "PenTool", enabled: false },
  { id: "operations", label: "Operations", shortcut: "6", icon: "Workflow", enabled: true },
];

/* ------------------------------------------------------------------ */
/*  Notification types                                                 */
/* ------------------------------------------------------------------ */

export interface AppNotification {
  id: string;
  type: "info" | "success" | "warning" | "error";
  title: string;
  message?: string;
  timestamp: number;
  read: boolean;
  action?: { label: string; callback: () => void };
  source?: "run" | "chat" | "system";
}

const MAX_NOTIFICATIONS = 50;
const AUTO_DISMISS_MS = 30_000;

/* ------------------------------------------------------------------ */
/*  Store                                                              */
/* ------------------------------------------------------------------ */

interface AppState {
  activeMode: AppMode;
  sidebarOpen: boolean;
  chatBarExpanded: boolean;

  activeChatThreadId: string | null;
  activeChatWorkflowId: string | null;

  pendingChatMessage: string | null;
  setPendingChatMessage: (msg: string | null) => void;

  globalPaletteVisible: boolean;
  setGlobalPaletteVisible: (v: boolean) => void;

  notifications: AppNotification[];
  unreadCount: number;
  addNotification: (n: Omit<AppNotification, "id" | "timestamp" | "read">) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  dismissNotification: (id: string) => void;
  clearAll: () => void;

  setMode: (mode: AppMode) => void;
  toggleSidebar: () => void;
  setChatBarExpanded: (expanded: boolean) => void;
  setActiveChatThread: (threadId: string | null, workflowId: string | null) => void;
}

function getModeFromHash(): AppMode {
  const hash = window.location.hash.replace("#", "");
  const valid = MODE_CONFIGS.find((m) => m.id === hash && m.enabled);
  return valid ? valid.id : "chat";
}

export const useAppStore = create<AppState>((set, get) => ({
  activeMode: getModeFromHash(),
  sidebarOpen: false,
  chatBarExpanded: false,

  activeChatThreadId: null,
  activeChatWorkflowId: null,

  pendingChatMessage: null,
  setPendingChatMessage: (msg) => set({ pendingChatMessage: msg }),

  globalPaletteVisible: false,
  setGlobalPaletteVisible: (v) => set({ globalPaletteVisible: v }),

  notifications: [],
  unreadCount: 0,

  addNotification: (n) => {
    const id = `notif-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const notification: AppNotification = { ...n, id, timestamp: Date.now(), read: false };

    set((s) => {
      const next = [notification, ...s.notifications].slice(0, MAX_NOTIFICATIONS);
      return { notifications: next, unreadCount: next.filter((x) => !x.read).length };
    });

    if (n.type === "info" || n.type === "success") {
      setTimeout(() => {
        const state = get();
        if (state.notifications.some((x) => x.id === id)) {
          state.dismissNotification(id);
        }
      }, AUTO_DISMISS_MS);
    }
  },

  markRead: (id) =>
    set((s) => {
      const notifications = s.notifications.map((n) => (n.id === id ? { ...n, read: true } : n));
      return { notifications, unreadCount: notifications.filter((x) => !x.read).length };
    }),

  markAllRead: () =>
    set((s) => ({
      notifications: s.notifications.map((n) => ({ ...n, read: true })),
      unreadCount: 0,
    })),

  dismissNotification: (id) =>
    set((s) => {
      const notifications = s.notifications.filter((n) => n.id !== id);
      return { notifications, unreadCount: notifications.filter((x) => !x.read).length };
    }),

  clearAll: () => set({ notifications: [], unreadCount: 0 }),

  setMode: (mode) => {
    const config = MODE_CONFIGS.find((m) => m.id === mode);
    if (!config?.enabled) return;
    window.location.hash = mode;
    set({ activeMode: mode });
  },

  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  setChatBarExpanded: (expanded) => set({ chatBarExpanded: expanded }),

  setActiveChatThread: (threadId, workflowId) =>
    set({ activeChatThreadId: threadId, activeChatWorkflowId: workflowId }),
}));

/** Convenience for pushing notifications from non-React code */
export function pushNotification(n: Omit<AppNotification, "id" | "timestamp" | "read">) {
  useAppStore.getState().addNotification(n);
}

// Sync URL hash changes back to store
if (typeof window !== "undefined") {
  window.addEventListener("hashchange", () => {
    const mode = getModeFromHash();
    useAppStore.getState().setMode(mode);
  });
}
