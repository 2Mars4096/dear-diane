import * as api from "../lib/api";
import type {
  OptimizationMutation,
  TierInfo,
  TokenBreakdown,
  WasteFinding,
} from "../types/graph";
import { useAppStore } from "./useAppStore";
import { createEmptyGraphRunState } from "./graphSessionState";

export type GraphChatMode = "ask" | "agent" | "plan" | "debug" | "auto";

type StoreSetter<PartialState, StoreState extends PartialState = PartialState> = (
  partial: Partial<PartialState> | ((state: StoreState) => Partial<PartialState>),
) => void;

type GraphLogEntry = {
  timestamp: number;
  event_type: string;
  node_id?: string;
  message: string;
  data?: Record<string, unknown>;
};

interface GraphToastState {
  addToast: (toast: {
    type: "success" | "error" | "info" | "warning";
    message: string;
  }) => void;
}

interface GraphPanelFocusState {
  chatMode: GraphChatMode;
  chatFocusTrigger: number;
  chatPrefill: string | null;
  logFocusCounter: number;
  historyFocusCounter: number;
  historyFocusRunId: string | null;
}

interface GraphRunHydrationState extends GraphToastState, GraphPanelFocusState {
  runId: string | null;
  runStatus: string | null;
  nodeStatuses: Record<string, string>;
  nodeOutputs: Record<string, Record<string, unknown>>;
  nodeTimings: Record<string, { start: number; end?: number }>;
  nodeUsage: Record<
    string,
    { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  >;
  nodeCosts: Record<string, number>;
  nodeTiers: Record<string, TierInfo>;
  activeExecutionPath: Set<string>;
  logs: GraphLogEntry[];
  runSummary: {
    elapsed_seconds?: number;
    total_prompt_tokens?: number;
    total_completion_tokens?: number;
    total_tokens?: number;
  } | null;
  nodeIterations: Record<string, { current: number; total?: number; condition?: string }>;
  streamingOutputs: Record<string, string>;
  pendingHumanInput: {
    requestId: string;
    nodeId: string;
    prompt: string;
  } | null;
  tokenBreakdowns: Record<string, TokenBreakdown>;
  wasteFindings: WasteFinding[];
  optimizationMutations: OptimizationMutation[];
  edgeTokenCounts: Record<string, number>;
  handleRunEvent: (
    event: Record<string, unknown>,
    options?: { historical?: boolean },
  ) => void;
}

interface GraphBuildWithAIState extends GraphPanelFocusState {
  graphId: string | null;
  createGraph: (graphId: string) => Promise<void>;
}

export function createInitialGraphPanelState(): GraphPanelFocusState {
  return {
    chatMode: "auto",
    chatFocusTrigger: 0,
    chatPrefill: null,
    logFocusCounter: 0,
    historyFocusCounter: 0,
    historyFocusRunId: null,
  };
}

export function setGraphChatMode<State extends GraphPanelFocusState>(
  set: StoreSetter<GraphPanelFocusState, State>,
  mode: GraphChatMode,
): void {
  set({ chatMode: mode } as Partial<State>);
}

export function openDebugWithErrorForGraphStore<State extends GraphPanelFocusState>(
  set: StoreSetter<GraphPanelFocusState, State>,
  errorContext: string,
): void {
  set((state) => ({
    chatMode: "debug",
    chatPrefill: errorContext,
    chatFocusTrigger: state.chatFocusTrigger + 1,
  }) as Partial<State>);
}

export function focusLogPanelForGraphStore<State extends GraphPanelFocusState>(
  set: StoreSetter<GraphPanelFocusState, State>,
): void {
  useAppStore.getState().setMode("operations");
  set((state) => ({
    logFocusCounter: state.logFocusCounter + 1,
  }) as Partial<State>);
}

export function focusHistoryPanelForGraphStore<State extends GraphPanelFocusState>(
  set: StoreSetter<GraphPanelFocusState, State>,
  runId?: string,
): void {
  useAppStore.getState().setMode("operations");
  set((state) => ({
    historyFocusCounter: state.historyFocusCounter + 1,
    historyFocusRunId: runId ?? null,
  }) as Partial<State>);
}

export async function loadRunLogsForGraphStore<State extends GraphRunHydrationState>(
  set: StoreSetter<GraphRunHydrationState, State>,
  get: () => State,
  runId: string,
): Promise<void> {
  useAppStore.getState().setMode("operations");
  set((state) => ({
    logFocusCounter: state.logFocusCounter + 1,
  }) as Partial<State>);
  try {
    const [runInfo, eventResponse] = await Promise.all([
      api.getRun(runId),
      api.getRunEvents(runId),
    ]);
    const runSummary = runInfo as api.RunSummary;
    const normalizedNodeUsage = Object.fromEntries(
      Object.entries(runSummary.node_usage ?? {}).map(([nodeId, usage]) => [
        nodeId,
        {
          prompt_tokens: Number((usage as Record<string, unknown>)?.prompt_tokens ?? 0),
          completion_tokens: Number(
            (usage as Record<string, unknown>)?.completion_tokens ?? 0,
          ),
          total_tokens: Number((usage as Record<string, unknown>)?.total_tokens ?? 0),
        },
      ]),
    ) as GraphRunHydrationState["nodeUsage"];
    const baseSummary = {
      elapsed_seconds: runSummary.elapsed_seconds ?? undefined,
      total_prompt_tokens: runSummary.total_prompt_tokens,
      total_completion_tokens: runSummary.total_completion_tokens,
      total_tokens: runSummary.total_tokens,
    };
    const resetRunState: Partial<GraphRunHydrationState> = {
      ...createEmptyGraphRunState(),
      runId: runInfo.run_id,
      runStatus: runInfo.status,
      nodeUsage: normalizedNodeUsage,
      runSummary: baseSummary,
    };
    set(resetRunState as Partial<State>);
    for (const event of eventResponse.events) {
      get().handleRunEvent(event as Record<string, unknown>, { historical: true });
    }
    set((state) => {
      const finalStatuses =
        Object.keys(state.nodeStatuses).length > 0
          ? state.nodeStatuses
          : runInfo.node_statuses ?? {};
      const finalSummary = {
        elapsed_seconds: state.runSummary?.elapsed_seconds ?? baseSummary.elapsed_seconds,
        total_prompt_tokens:
          state.runSummary?.total_prompt_tokens ?? baseSummary.total_prompt_tokens,
        total_completion_tokens:
          state.runSummary?.total_completion_tokens ??
          baseSummary.total_completion_tokens,
        total_tokens: state.runSummary?.total_tokens ?? baseSummary.total_tokens,
      };
      return {
        nodeStatuses: finalStatuses,
        activeExecutionPath: new Set(Object.keys(finalStatuses)),
        runStatus: runInfo.status,
        runSummary: finalSummary,
      } as Partial<State>;
    });
  } catch (error: unknown) {
    get().addToast({
      type: "error",
      message: (error as Error).message ?? "Failed to load run logs",
    });
  }
}

export async function openBuildWithAIForGraphStore<
  State extends GraphBuildWithAIState,
>(
  set: StoreSetter<GraphBuildWithAIState, State>,
  get: () => State,
): Promise<void> {
  const autoId = `build-${Math.random().toString(36).slice(2, 8)}`;
  await get().createGraph(autoId);
  if (get().graphId === autoId) {
    set((state) => ({
      chatMode: "agent",
      chatFocusTrigger: state.chatFocusTrigger + 1,
    }) as Partial<State>);
  }
}
