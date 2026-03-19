import { create } from "zustand";
import { persist } from "zustand/middleware";

import { detectCurrentPlatform, getBuiltinTerminalProfiles } from "../lib/terminalProfiles";

export interface TerminalProfile {
  id: string;
  name: string;
  shell: string;
  args?: string[];
  cwd?: string;
  env?: Record<string, string>;
  isDefault?: boolean;
}

const DEFAULT_TERMINAL_PROFILES: TerminalProfile[] = getBuiltinTerminalProfiles(
  detectCurrentPlatform(),
);

function _defaultTerminalProfileId(profiles: TerminalProfile[]): string {
  return profiles.find((profile) => profile.isDefault)?.id || profiles[0]?.id || "default";
}

function _isLegacyUnixTerminalProfiles(profiles: TerminalProfile[]): boolean {
  if (profiles.length !== 3) return false;
  const expected = [
    { id: "zsh", shell: "/bin/zsh" },
    { id: "bash", shell: "/bin/bash" },
    { id: "sh", shell: "/bin/sh" },
  ];
  return expected.every((entry, index) => {
    const profile = profiles[index];
    return (
      profile?.id === entry.id
      && profile?.shell === entry.shell
      && !profile?.args?.length
    );
  });
}

export function normalizeTerminalSettings(
  state: Pick<EditorSettings, "terminalProfiles" | "defaultTerminalProfile">,
  rawPlatform = detectCurrentPlatform(),
): Pick<EditorSettings, "terminalProfiles" | "defaultTerminalProfile"> {
  const builtinProfiles = getBuiltinTerminalProfiles(rawPlatform);
  const profiles = Array.isArray(state.terminalProfiles) ? state.terminalProfiles : [];
  const replacedLegacyProfiles =
    profiles.length === 0 || _isLegacyUnixTerminalProfiles(profiles);
  const normalizedProfiles = replacedLegacyProfiles ? builtinProfiles : profiles;
  const defaultTerminalProfile = replacedLegacyProfiles
    ? _defaultTerminalProfileId(normalizedProfiles)
    : normalizedProfiles.some((profile) => profile.id === state.defaultTerminalProfile)
      ? state.defaultTerminalProfile
      : _defaultTerminalProfileId(normalizedProfiles);

  return {
    terminalProfiles: normalizedProfiles,
    defaultTerminalProfile,
  };
}

export interface EditorSettings {
  theme: "system" | "vs-dark" | "vs" | "hc-black";
  fontSize: number;
  fontFamily: string;
  tabSize: number;
  wordWrap: "on" | "off" | "wordWrapColumn" | "bounded";
  minimap: boolean;
  lineNumbers: "on" | "off" | "relative" | "interval";
  renderWhitespace: "none" | "boundary" | "selection" | "trailing" | "all";
  bracketPairColorization: boolean;
  cursorBlinking: "blink" | "smooth" | "phase" | "expand" | "solid";
  cursorStyle:
    | "line"
    | "block"
    | "underline"
    | "line-thin"
    | "block-outline"
    | "underline-thin";
  formatOnSave: boolean;
  autoSaveDelay: number;
  scrollBeyondLastLine: boolean;
  stickyScroll: boolean;
  indentGuides: boolean;
  bracketPairGuides: boolean;
  terminalProfiles: TerminalProfile[];
  defaultTerminalProfile: string;
  inlineCompletionEnabled: boolean;
  aiActionsEnabled: boolean;
  codeActionsOnSave: boolean;
  iconTheme: string;
  researchPdfRoots: string[];
  researchNoteRoots: string[];
  messagingOnboardingOffered: boolean;
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
  fontSize: 13,
  fontFamily: "SF Mono, Menlo, Monaco, monospace",
  tabSize: 2,
  wordWrap: "off",
  minimap: true,
  lineNumbers: "on",
  renderWhitespace: "selection",
  bracketPairColorization: true,
  cursorBlinking: "blink",
  cursorStyle: "line",
  formatOnSave: false,
  autoSaveDelay: 1000,
  scrollBeyondLastLine: false,
  stickyScroll: true,
  indentGuides: true,
  bracketPairGuides: true,
  terminalProfiles: DEFAULT_TERMINAL_PROFILES,
  defaultTerminalProfile: _defaultTerminalProfileId(DEFAULT_TERMINAL_PROFILES),
  inlineCompletionEnabled: true,
  aiActionsEnabled: true,
  codeActionsOnSave: true,
  iconTheme: "default",
  researchPdfRoots: [],
  researchNoteRoots: [],
  messagingOnboardingOffered: false,
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
      version: 3,
      migrate: (persistedState, version) => {
        const state = persistedState as Partial<SettingsState> | undefined;
        if (!state) return persistedState as SettingsState;

        let migrated = state;

        if (version < 2 && (migrated.theme === undefined || migrated.theme === "vs-dark")) {
          migrated = { ...migrated, theme: "system" };
        }

        if (version < 3) {
          migrated = {
            ...migrated,
            ...normalizeTerminalSettings({
              terminalProfiles: migrated.terminalProfiles ?? [],
              defaultTerminalProfile: migrated.defaultTerminalProfile ?? "",
            }),
          };
        }

        return migrated as SettingsState;
      },
    },
  ),
);
