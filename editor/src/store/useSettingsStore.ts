import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface EditorSettings {
  theme: "system" | "vs-dark" | "vs" | "hc-black";
  workspaceSurfaceTheme: "original" | "industrial" | "factory-worn";
  workspaceSurfaceTone: "system" | "day" | "night";
}

interface SettingsState extends EditorSettings {
  updateSetting: <K extends keyof EditorSettings>(
    key: K,
    value: EditorSettings[K],
  ) => void;
  resetToDefaults: () => void;

  workspaceOverrides: Record<string, Partial<EditorSettings>>;
  setWorkspaceOverride: <K extends keyof EditorSettings>(
    rootPath: string,
    key: K,
    value: EditorSettings[K],
  ) => void;
  clearWorkspaceOverride: (rootPath: string, key: keyof EditorSettings) => void;
  clearAllWorkspaceOverrides: (rootPath: string) => void;
  getEffectiveSetting: <K extends keyof EditorSettings>(
    rootPath: string | undefined,
    key: K,
  ) => EditorSettings[K];
}

const DEFAULT_SETTINGS: EditorSettings = {
  theme: "system",
  workspaceSurfaceTheme: "factory-worn",
  workspaceSurfaceTone: "system",
};

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      ...DEFAULT_SETTINGS,
      updateSetting: (key, value) => set({ [key]: value }),
      resetToDefaults: () => set({ ...DEFAULT_SETTINGS, workspaceOverrides: get().workspaceOverrides }),

      workspaceOverrides: {},

      setWorkspaceOverride: (rootPath, key, value) =>
        set((s) => ({
          workspaceOverrides: {
            ...s.workspaceOverrides,
            [rootPath]: { ...s.workspaceOverrides[rootPath], [key]: value },
          },
        })),

      clearWorkspaceOverride: (rootPath, key) =>
        set((s) => {
          const current = { ...s.workspaceOverrides[rootPath] };
          delete current[key];
          const isEmpty = Object.keys(current).length === 0;
          const next = { ...s.workspaceOverrides };
          if (isEmpty) delete next[rootPath];
          else next[rootPath] = current;
          return { workspaceOverrides: next };
        }),

      clearAllWorkspaceOverrides: (rootPath) =>
        set((s) => {
          const next = { ...s.workspaceOverrides };
          delete next[rootPath];
          return { workspaceOverrides: next };
        }),

      getEffectiveSetting: (rootPath, key) => {
        const state = get();
        if (rootPath) {
          const wsVal = state.workspaceOverrides[rootPath]?.[key];
          if (wsVal !== undefined) return wsVal as EditorSettings[typeof key];
        }
        return state[key] as EditorSettings[typeof key];
      },
    }),
    {
      name: "dan-editor-settings",
      version: 6,
      migrate: (persistedState, version) => {
        const state = persistedState as Partial<SettingsState> | undefined;
        if (!state) return persistedState as SettingsState;

        let migrated = state;

        if (version < 2 && (migrated.theme === undefined || migrated.theme === "vs-dark")) {
          migrated = { ...migrated, theme: "system" };
        }

        if (version < 4 && migrated.workspaceSurfaceTheme === undefined) {
          migrated = { ...migrated, workspaceSurfaceTheme: "factory-worn" };
        }

        if (version < 5 && migrated.workspaceSurfaceTone === undefined) {
          migrated = { ...migrated, workspaceSurfaceTone: "system" };
        }

        if (version < 6) {
          migrated = {
            theme: migrated.theme ?? "system",
            workspaceSurfaceTheme: migrated.workspaceSurfaceTheme ?? "factory-worn",
            workspaceSurfaceTone: migrated.workspaceSurfaceTone ?? "system",
          };
        }

        return migrated as SettingsState;
      },
    },
  ),
);
