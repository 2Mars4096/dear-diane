import { create } from "zustand";

export type DebugStatus = "idle" | "running" | "paused" | "stopped";

export interface DebugThread {
  id: number;
  name: string;
}

export interface StackFrame {
  id: number;
  name: string;
  source?: { path?: string; name?: string };
  line: number;
  column: number;
}

export interface DebugVariable {
  name: string;
  value: string;
  type?: string;
  variablesReference: number;
}

export interface WatchExpression {
  id: string;
  expression: string;
  value?: string;
  error?: string;
}

export interface Breakpoint {
  line: number;
  condition?: string;
  logMessage?: string;
  verified: boolean;
}

export interface InlineValue {
  line: number;
  name: string;
  value: string;
}

export interface LaunchConfig {
  name: string;
  type: string;
  request: "launch" | "attach";
  program?: string;
  args?: string[];
  cwd?: string;
  env?: Record<string, string>;
  [key: string]: any;
}

const BREAKPOINTS_KEY = "dan-debug-breakpoints";
const WATCH_KEY = "dan-debug-watch";
const LAUNCH_CONFIGS_KEY = "dan-debug-launch-configs";

function loadBreakpoints(): Record<string, Breakpoint[]> {
  try {
    const stored = localStorage.getItem(BREAKPOINTS_KEY);
    return stored ? JSON.parse(stored) : {};
  } catch {
    return {};
  }
}

function saveBreakpoints(bps: Record<string, Breakpoint[]>) {
  try {
    localStorage.setItem(BREAKPOINTS_KEY, JSON.stringify(bps));
  } catch { /* quota exceeded */ }
}

function loadWatch(): WatchExpression[] {
  try {
    const stored = localStorage.getItem(WATCH_KEY);
    return stored ? JSON.parse(stored) : [];
  } catch {
    return [];
  }
}

function saveWatch(watches: WatchExpression[]) {
  try {
    localStorage.setItem(WATCH_KEY, JSON.stringify(watches));
  } catch { /* quota exceeded */ }
}

function loadLaunchConfigs(): LaunchConfig[] {
  try {
    const stored = localStorage.getItem(LAUNCH_CONFIGS_KEY);
    return stored ? JSON.parse(stored) : [];
  } catch {
    return [];
  }
}

function saveLaunchConfigs(configs: LaunchConfig[]) {
  try {
    localStorage.setItem(LAUNCH_CONFIGS_KEY, JSON.stringify(configs));
  } catch { /* quota exceeded */ }
}

interface DebugState {
  status: DebugStatus;
  threads: DebugThread[];
  activeThreadId: number | null;
  callStack: StackFrame[];
  activeFrameId: number | null;
  variables: DebugVariable[];
  scopes: Array<{ name: string; variablesReference: number; expensive: boolean }>;
  watchExpressions: WatchExpression[];
  breakpoints: Record<string, Breakpoint[]>;
  debugConsoleOutput: string[];
  inlineValues: InlineValue[];
  launchConfigs: LaunchConfig[];
  activeLaunchConfigIndex: number;
  pausedFile: string | null;
  pausedLine: number | null;

  setStatus: (status: DebugStatus) => void;
  setThreads: (threads: DebugThread[]) => void;
  setActiveThreadId: (id: number | null) => void;
  setCallStack: (frames: StackFrame[]) => void;
  setActiveFrameId: (id: number | null) => void;
  setVariables: (vars: DebugVariable[]) => void;
  setScopes: (scopes: Array<{ name: string; variablesReference: number; expensive: boolean }>) => void;
  setPausedLocation: (file: string | null, line: number | null) => void;
  setInlineValues: (vals: InlineValue[]) => void;

  toggleBreakpoint: (filePath: string, line: number) => void;
  setBreakpointCondition: (filePath: string, line: number, condition: string) => void;
  setBreakpointLogMessage: (filePath: string, line: number, logMessage: string) => void;
  removeBreakpoint: (filePath: string, line: number) => void;
  clearBreakpoints: (filePath: string) => void;
  updateVerifiedBreakpoints: (filePath: string, verified: Array<{ line: number; verified: boolean }>) => void;

  addWatchExpression: (expression: string) => void;
  removeWatchExpression: (id: string) => void;
  updateWatchValue: (id: string, value?: string, error?: string) => void;
  clearWatchExpressions: () => void;

  appendConsoleOutput: (text: string) => void;
  clearConsoleOutput: () => void;

  addLaunchConfig: (config: LaunchConfig) => void;
  removeLaunchConfig: (index: number) => void;
  setActiveLaunchConfigIndex: (index: number) => void;
  updateLaunchConfig: (index: number, config: LaunchConfig) => void;

  resetSession: () => void;
}

export const useDebugStore = create<DebugState>((set, _get) => ({
  status: "idle",
  threads: [],
  activeThreadId: null,
  callStack: [],
  activeFrameId: null,
  variables: [],
  scopes: [],
  watchExpressions: loadWatch(),
  breakpoints: loadBreakpoints(),
  debugConsoleOutput: [],
  inlineValues: [],
  launchConfigs: loadLaunchConfigs(),
  activeLaunchConfigIndex: 0,
  pausedFile: null,
  pausedLine: null,

  setStatus: (status) => set({ status }),
  setThreads: (threads) => set({ threads }),
  setActiveThreadId: (id) => set({ activeThreadId: id }),
  setCallStack: (frames) => set({ callStack: frames }),
  setActiveFrameId: (id) => set({ activeFrameId: id }),
  setVariables: (vars) => set({ variables: vars }),
  setScopes: (scopes) => set({ scopes }),
  setPausedLocation: (file, line) => set({ pausedFile: file, pausedLine: line }),
  setInlineValues: (vals) => set({ inlineValues: vals }),

  toggleBreakpoint: (filePath, line) =>
    set((s) => {
      const existing = s.breakpoints[filePath] ?? [];
      const idx = existing.findIndex((b) => b.line === line);
      let next: Breakpoint[];
      if (idx >= 0) {
        next = existing.filter((_, i) => i !== idx);
      } else {
        next = [...existing, { line, verified: false }];
      }
      const bps = { ...s.breakpoints, [filePath]: next };
      if (next.length === 0) delete bps[filePath];
      saveBreakpoints(bps);
      return { breakpoints: bps };
    }),

  setBreakpointCondition: (filePath, line, condition) =>
    set((s) => {
      const existing = s.breakpoints[filePath] ?? [];
      const bps = {
        ...s.breakpoints,
        [filePath]: existing.map((b) =>
          b.line === line ? { ...b, condition: condition || undefined } : b,
        ),
      };
      saveBreakpoints(bps);
      return { breakpoints: bps };
    }),

  setBreakpointLogMessage: (filePath, line, logMessage) =>
    set((s) => {
      const existing = s.breakpoints[filePath] ?? [];
      const bps = {
        ...s.breakpoints,
        [filePath]: existing.map((b) =>
          b.line === line ? { ...b, logMessage: logMessage || undefined } : b,
        ),
      };
      saveBreakpoints(bps);
      return { breakpoints: bps };
    }),

  removeBreakpoint: (filePath, line) =>
    set((s) => {
      const existing = s.breakpoints[filePath] ?? [];
      const next = existing.filter((b) => b.line !== line);
      const bps = { ...s.breakpoints, [filePath]: next };
      if (next.length === 0) delete bps[filePath];
      saveBreakpoints(bps);
      return { breakpoints: bps };
    }),

  clearBreakpoints: (filePath) =>
    set((s) => {
      const bps = { ...s.breakpoints };
      delete bps[filePath];
      saveBreakpoints(bps);
      return { breakpoints: bps };
    }),

  updateVerifiedBreakpoints: (filePath, verified) =>
    set((s) => {
      const existing = s.breakpoints[filePath] ?? [];
      const bps = {
        ...s.breakpoints,
        [filePath]: existing.map((b) => {
          const v = verified.find((vb) => vb.line === b.line);
          return v ? { ...b, line: v.line, verified: v.verified } : b;
        }),
      };
      saveBreakpoints(bps);
      return { breakpoints: bps };
    }),

  addWatchExpression: (expression) =>
    set((s) => {
      const watch = [
        ...s.watchExpressions,
        { id: `w-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, expression },
      ];
      saveWatch(watch);
      return { watchExpressions: watch };
    }),

  removeWatchExpression: (id) =>
    set((s) => {
      const watch = s.watchExpressions.filter((w) => w.id !== id);
      saveWatch(watch);
      return { watchExpressions: watch };
    }),

  updateWatchValue: (id, value, error) =>
    set((s) => ({
      watchExpressions: s.watchExpressions.map((w) =>
        w.id === id ? { ...w, value, error } : w,
      ),
    })),

  clearWatchExpressions: () => {
    saveWatch([]);
    set({ watchExpressions: [] });
  },

  appendConsoleOutput: (text) =>
    set((s) => ({
      debugConsoleOutput: [...s.debugConsoleOutput, text].slice(-500),
    })),

  clearConsoleOutput: () => set({ debugConsoleOutput: [] }),

  addLaunchConfig: (config) =>
    set((s) => {
      const configs = [...s.launchConfigs, config];
      saveLaunchConfigs(configs);
      return { launchConfigs: configs, activeLaunchConfigIndex: configs.length - 1 };
    }),

  removeLaunchConfig: (index) =>
    set((s) => {
      const configs = s.launchConfigs.filter((_, i) => i !== index);
      saveLaunchConfigs(configs);
      return {
        launchConfigs: configs,
        activeLaunchConfigIndex: Math.min(s.activeLaunchConfigIndex, Math.max(0, configs.length - 1)),
      };
    }),

  setActiveLaunchConfigIndex: (index) => set({ activeLaunchConfigIndex: index }),

  updateLaunchConfig: (index, config) =>
    set((s) => {
      const configs = [...s.launchConfigs];
      configs[index] = config;
      saveLaunchConfigs(configs);
      return { launchConfigs: configs };
    }),

  resetSession: () =>
    set({
      status: "idle",
      threads: [],
      activeThreadId: null,
      callStack: [],
      activeFrameId: null,
      variables: [],
      scopes: [],
      debugConsoleOutput: [],
      inlineValues: [],
      pausedFile: null,
      pausedLine: null,
    }),
}));
