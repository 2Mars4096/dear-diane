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
  { id: "research", label: "Research", shortcut: "2", icon: "GraduationCap", enabled: false },
  { id: "development", label: "Development", shortcut: "3", icon: "Code", enabled: false },
  { id: "analytics", label: "Analytics", shortcut: "4", icon: "BarChart3", enabled: false },
  { id: "content", label: "Content", shortcut: "5", icon: "PenTool", enabled: false },
  { id: "operations", label: "Operations", shortcut: "6", icon: "Workflow", enabled: true },
];

interface AppState {
  activeMode: AppMode;
  sidebarOpen: boolean;
  chatBarExpanded: boolean;

  setMode: (mode: AppMode) => void;
  toggleSidebar: () => void;
  setChatBarExpanded: (expanded: boolean) => void;
}

function getModeFromHash(): AppMode {
  const hash = window.location.hash.replace("#", "");
  const valid = MODE_CONFIGS.find((m) => m.id === hash && m.enabled);
  return valid ? valid.id : "chat";
}

export const useAppStore = create<AppState>((set) => ({
  activeMode: getModeFromHash(),
  sidebarOpen: false,
  chatBarExpanded: false,

  setMode: (mode) => {
    const config = MODE_CONFIGS.find((m) => m.id === mode);
    if (!config?.enabled) return;
    window.location.hash = mode;
    set({ activeMode: mode });
  },

  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  setChatBarExpanded: (expanded) => set({ chatBarExpanded: expanded }),
}));

// Sync URL hash changes back to store
if (typeof window !== "undefined") {
  window.addEventListener("hashchange", () => {
    const mode = getModeFromHash();
    useAppStore.getState().setMode(mode);
  });
}
