import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Clock3,
  Flame,
  FolderGit2,
  GitBranch,
  Loader2,
  RefreshCcw,
  Sparkles,
  Workflow,
} from "lucide-react";
import {
  furnaceListSessions,
  type FurnaceSessionSummary,
} from "../../lib/api";
import { useAppStore } from "../../store/useAppStore";
import { useCodeStore } from "../../store/useCodeStore";
import { useGraphStore } from "../../store/useGraphStore";
import { useResearchStore } from "../../store/useResearchStore";

export type DevelopmentSidebarPanel =
  | "explorer"
  | "search"
  | "git"
  | "extensions"
  | "timeline"
  | "tasks"
  | "testing"
  | "outline"
  | "debug"
  | "workflow"
  | "furnace";

export const PREVIEW_ONLY_CODE_PANELS = new Set<DevelopmentSidebarPanel>([
  "explorer",
  "search",
  "git",
  "timeline",
  "tasks",
  "testing",
  "outline",
  "debug",
]);

const PREVIEW_PANEL_COPY: Record<
  DevelopmentSidebarPanel,
  { eyebrow: string; title: string; body: string }
> = {
  explorer: {
    eyebrow: "Desktop runtime",
    title: "Folder navigation needs the desktop app",
    body:
      "Browser preview keeps the shell visible, but it cannot mount your local filesystem or watch folders.",
  },
  search: {
    eyebrow: "Desktop runtime",
    title: "Workspace search is native in desktop",
    body:
      "Search uses ripgrep against your pinned roots. In browser preview, use the workflow and furnace handoff panels instead.",
  },
  git: {
    eyebrow: "Desktop runtime",
    title: "Source control stays desktop-only",
    body:
      "Git status, diff, blame, and commit flows depend on the native bridge and local repository access.",
  },
  extensions: {
    eyebrow: "Preview",
    title: "Extensions preview",
    body: "Extension browsing remains visible here, but installs and runtime hooks still depend on the desktop shell.",
  },
  timeline: {
    eyebrow: "Desktop runtime",
    title: "Local history follows real files",
    body:
      "Timeline and history views are most useful once DAN can read and watch files from the desktop app.",
  },
  tasks: {
    eyebrow: "Desktop runtime",
    title: "Tasks need a runnable workspace",
    body:
      "Build and test commands launch through the native terminal bridge, so browser preview only shows the shell layout.",
  },
  testing: {
    eyebrow: "Desktop runtime",
    title: "Test discovery needs the desktop app",
    body:
      "Browser preview can show the shell, but test collection and execution stay disabled without local project access.",
  },
  outline: {
    eyebrow: "Desktop runtime",
    title: "Outline depends on live editor state",
    body:
      "Outline is most accurate once a real workspace and language services are active in the desktop runtime.",
  },
  debug: {
    eyebrow: "Desktop runtime",
    title: "Debugging is wired to native adapters",
    body:
      "Launch configs, breakpoints, and debug transport all use the Electron bridge, so browser preview cannot run them.",
  },
  workflow: {
    eyebrow: "DAN surface",
    title: "Workflows",
    body: "Open saved workflows and hand them off to Operations mode for full canvas editing and runs.",
  },
  furnace: {
    eyebrow: "DAN surface",
    title: "Furnace",
    body: "Review live training sessions here, then jump into Research mode for deeper session controls.",
  },
};

function formatRelativeTime(value: string | number | null | undefined): string {
  if (value == null) return "No recent activity";
  const timestamp =
    typeof value === "number"
      ? (value > 1_000_000_000_000 ? value : value * 1000)
      : Date.parse(value);
  if (!Number.isFinite(timestamp)) return "No recent activity";
  const deltaMs = Date.now() - timestamp;
  const deltaMin = Math.floor(deltaMs / 60_000);
  if (deltaMin < 1) return "Updated just now";
  if (deltaMin < 60) return `Updated ${deltaMin}m ago`;
  const deltaHours = Math.floor(deltaMin / 60);
  if (deltaHours < 24) return `Updated ${deltaHours}h ago`;
  const deltaDays = Math.floor(deltaHours / 24);
  if (deltaDays < 7) return `Updated ${deltaDays}d ago`;
  return `Updated ${new Date(timestamp).toLocaleDateString()}`;
}

function StatusBadge({
  tone,
  children,
}: {
  tone: "slate" | "blue" | "emerald" | "amber";
  children: string;
}) {
  const tones = {
    slate:
      "border-gray-200 bg-white/80 text-gray-600 dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-300",
    blue:
      "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300",
    emerald:
      "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300",
    amber:
      "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200",
  } as const;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

function SidebarHeader({
  title,
  subtitle,
  onRefresh,
  refreshing,
}: {
  title: string;
  subtitle: string;
  onRefresh?: () => void;
  refreshing?: boolean;
}) {
  return (
    <div className="border-b border-gray-200 bg-white/80 px-3 py-2 backdrop-blur dark:border-gray-800 dark:bg-gray-900/80">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
            {title}
          </div>
          <div className="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400">
            {subtitle}
          </div>
        </div>
        {onRefresh && (
          <button
            onClick={onRefresh}
            title="Refresh"
            className="rounded-md border border-gray-200 p-1 text-gray-500 transition-colors hover:border-gray-300 hover:text-gray-800 dark:border-gray-700 dark:text-gray-400 dark:hover:border-gray-600 dark:hover:text-gray-200"
          >
            {refreshing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCcw size={13} />}
          </button>
        )}
      </div>
    </div>
  );
}

export function WorkflowSidebarPanel() {
  const graphList = useGraphStore((s) => s.graphList);
  const graphId = useGraphStore((s) => s.graphId);
  const tabs = useGraphStore((s) => s.tabs);
  const loadGraphList = useGraphStore((s) => s.loadGraphList);
  const openTab = useGraphStore((s) => s.openTab);
  const switchTab = useGraphStore((s) => s.switchTab);
  const setMode = useAppStore((s) => s.setMode);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshGraphs = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      await loadGraphList();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load workflows.");
    } finally {
      setRefreshing(false);
    }
  }, [loadGraphList]);

  useEffect(() => {
    void refreshGraphs();
  }, [refreshGraphs]);

  const orderedGraphs = useMemo(() => {
    const list = [...graphList].sort(
      (a, b) => Date.parse(b.updated_at ?? "") - Date.parse(a.updated_at ?? ""),
    );
    if (!graphId) return list;
    return list.sort((a, b) => {
      if (a.graph_id === graphId) return -1;
      if (b.graph_id === graphId) return 1;
      return 0;
    });
  }, [graphId, graphList]);

  const openWorkflow = useCallback(
    async (targetGraphId: string) => {
      const existingTab = tabs.find((tab) => tab.graphId === targetGraphId);
      if (existingTab) {
        await switchTab(existingTab.id);
      } else {
        await openTab(targetGraphId);
      }
      setMode("operations");
    },
    [openTab, setMode, switchTab, tabs],
  );

  return (
    <div className="flex h-full flex-col text-gray-800 dark:text-gray-300">
      <SidebarHeader
        title="Workflows"
        subtitle="Saved graphs you can hand off to Operations"
        onRefresh={() => void refreshGraphs()}
        refreshing={refreshing}
      />
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        <div className="rounded-xl border border-gray-200 bg-gradient-to-br from-white via-blue-50/70 to-emerald-50/70 p-3 dark:border-gray-800 dark:from-gray-900 dark:via-blue-500/5 dark:to-emerald-500/5">
          <div className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-100">
            <Workflow size={15} className="text-blue-600 dark:text-blue-300" />
            Workflow inventory
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <StatusBadge tone="blue">{`${orderedGraphs.length} saved`}</StatusBadge>
            <StatusBadge tone={graphId ? "emerald" : "slate"}>
              {graphId ? `Current: ${graphId}` : "No workflow loaded"}
            </StatusBadge>
          </div>
          <p className="mt-2 text-xs leading-5 text-gray-600 dark:text-gray-400">
            Development mode keeps this as a quick handoff surface. Open a workflow in
            Operations when you want full graph editing, run controls, or log inspection.
          </p>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
            {error}
          </div>
        )}

        {refreshing && orderedGraphs.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-xs text-gray-500 dark:text-gray-400">
            <Loader2 size={14} className="mr-2 animate-spin" />
            Loading workflows…
          </div>
        ) : orderedGraphs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-200 px-3 py-6 text-center text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400">
            No saved workflows yet. Build one in Chat or Operations and it will show up here.
          </div>
        ) : (
          <div className="space-y-2">
            {orderedGraphs.map((graph) => {
              const isCurrent = graph.graph_id === graphId;
              return (
                <button
                  key={graph.graph_id}
                  onClick={() => void openWorkflow(graph.graph_id)}
                  className={`w-full rounded-xl border px-3 py-2.5 text-left transition-colors ${
                    isCurrent
                      ? "border-blue-200 bg-blue-50/80 dark:border-blue-500/30 dark:bg-blue-500/10"
                      : "border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-900 dark:hover:border-gray-700 dark:hover:bg-gray-800/70"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 rounded-lg bg-gray-100 p-2 text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                      <Workflow size={14} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                          {graph.name || graph.graph_id}
                        </span>
                        {isCurrent && <StatusBadge tone="blue">Current</StatusBadge>}
                      </div>
                      {graph.description && (
                        <div className="mt-1 line-clamp-2 text-xs leading-5 text-gray-600 dark:text-gray-400">
                          {graph.description}
                        </div>
                      )}
                      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-gray-500 dark:text-gray-400">
                        <span className="truncate">{graph.graph_id}</span>
                        <span>{formatRelativeTime(graph.updated_at)}</span>
                      </div>
                    </div>
                    <ArrowRight size={14} className="mt-1 shrink-0 text-gray-400" />
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function sessionTone(status: string): "blue" | "emerald" | "amber" | "slate" {
  if (status === "running" || status === "active") return "emerald";
  if (status === "paused") return "amber";
  if (status === "completed") return "blue";
  return "slate";
}

export function FurnaceSidebarPanel() {
  const setMode = useAppStore((s) => s.setMode);
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);
  const setActiveRailSection = useResearchStore((s) => s.setActiveRailSection);
  const [sessions, setSessions] = useState<FurnaceSessionSummary[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshSessions = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const response = await furnaceListSessions();
      setSessions(
        [...response.sessions].sort((a, b) => b.updated_at - a.updated_at),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load furnace sessions.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
    const interval = window.setInterval(() => {
      void refreshSessions();
    }, 15_000);
    return () => window.clearInterval(interval);
  }, [refreshSessions]);

  const openResearch = useCallback(() => {
    setActiveRailSection("training");
    setPrimaryTab("furnace");
    setMode("research");
  }, [setActiveRailSection, setMode, setPrimaryTab]);

  const counts = useMemo(
    () => ({
      total: sessions.length,
      running: sessions.filter((session) => session.status === "running" || session.status === "active").length,
      paused: sessions.filter((session) => session.status === "paused").length,
    }),
    [sessions],
  );

  return (
    <div className="flex h-full flex-col text-gray-800 dark:text-gray-300">
      <SidebarHeader
        title="Furnace"
        subtitle="Live training sessions with a clean Research handoff"
        onRefresh={() => void refreshSessions()}
        refreshing={refreshing}
      />
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        <div className="rounded-xl border border-gray-200 bg-gradient-to-br from-white via-orange-50/70 to-amber-50/80 p-3 dark:border-gray-800 dark:from-gray-900 dark:via-orange-500/5 dark:to-amber-500/5">
          <div className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-100">
            <Flame size={15} className="text-orange-500" />
            Training control room
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <StatusBadge tone="amber">{`${counts.total} sessions`}</StatusBadge>
            {counts.running > 0 && <StatusBadge tone="emerald">{`${counts.running} running`}</StatusBadge>}
            {counts.paused > 0 && <StatusBadge tone="blue">{`${counts.paused} paused`}</StatusBadge>}
          </div>
          <p className="mt-2 text-xs leading-5 text-gray-600 dark:text-gray-400">
            This panel gives Development mode a real training snapshot. Jump into
            Research for full recipe editing, citations, and live session detail.
          </p>
          <button
            onClick={openResearch}
            className="mt-3 inline-flex items-center gap-2 rounded-lg border border-orange-200 bg-white px-3 py-1.5 text-xs font-medium text-orange-700 transition-colors hover:border-orange-300 hover:bg-orange-50 dark:border-orange-500/30 dark:bg-transparent dark:text-orange-200 dark:hover:bg-orange-500/10"
          >
            <BookOpen size={13} />
            Open Research mode
          </button>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
            {error}
          </div>
        )}

        {refreshing && sessions.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-xs text-gray-500 dark:text-gray-400">
            <Loader2 size={14} className="mr-2 animate-spin" />
            Loading furnace sessions…
          </div>
        ) : sessions.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-200 px-3 py-6 text-center text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400">
            No furnace sessions yet. Start one in Research mode and it will appear here.
          </div>
        ) : (
          <div className="space-y-2">
            {sessions.map((session) => (
              <button
                key={session.session_id}
                onClick={openResearch}
                className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-left transition-colors hover:border-gray-300 hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-900 dark:hover:border-gray-700 dark:hover:bg-gray-800/70"
              >
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 rounded-lg bg-gray-100 p-2 text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                    <Flame size={14} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                        {session.name || session.topic || session.session_id}
                      </span>
                      <StatusBadge tone={sessionTone(session.status)}>
                        {session.status}
                      </StatusBadge>
                    </div>
                    {session.topic && (
                      <div className="mt-1 line-clamp-2 text-xs leading-5 text-gray-600 dark:text-gray-400">
                        {session.topic}
                      </div>
                    )}
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-gray-500 dark:text-gray-400">
                      <span className="inline-flex items-center gap-1">
                        <Clock3 size={11} />
                        {formatRelativeTime(session.updated_at)}
                      </span>
                      <span>{`${session.processed_count}/${session.source_count} sources`}</span>
                      {session.current_phase && <span className="truncate">{session.current_phase}</span>}
                    </div>
                  </div>
                  <ArrowRight size={14} className="mt-1 shrink-0 text-gray-400" />
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function DevelopmentPreviewPanel({
  panel,
}: {
  panel: DevelopmentSidebarPanel;
}) {
  const setActiveSidebarPanel = useCodeStore((s) => s.setActiveSidebarPanel);
  const setMode = useAppStore((s) => s.setMode);
  const copy = PREVIEW_PANEL_COPY[panel];

  return (
    <div className="flex h-full flex-col text-gray-800 dark:text-gray-300">
      <SidebarHeader title={copy.eyebrow} subtitle={copy.title} />
      <div className="flex-1 overflow-y-auto px-3 py-3">
        <div className="rounded-2xl border border-amber-200 bg-gradient-to-br from-amber-50 via-white to-orange-50 p-4 shadow-sm dark:border-amber-500/20 dark:from-amber-500/10 dark:via-gray-900 dark:to-orange-500/10">
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-amber-100 p-2 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200">
              <AlertTriangle size={16} />
            </div>
            <div className="min-w-0">
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-amber-700 dark:text-amber-200">
                Browser preview
              </div>
              <h3 className="mt-1 text-base font-semibold text-gray-900 dark:text-gray-100">
                {copy.title}
              </h3>
              <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-400">
                {copy.body}
              </p>
            </div>
          </div>

          <div className="mt-4 rounded-xl border border-gray-200 bg-white/80 p-3 dark:border-gray-800 dark:bg-gray-900/80">
            <div className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
              <Sparkles size={14} className="text-blue-600 dark:text-blue-300" />
              Browser-safe surfaces
            </div>
            <div className="mt-3 grid gap-2">
              <button
                onClick={() => setActiveSidebarPanel("workflow")}
                className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-left text-sm transition-colors hover:border-blue-200 hover:bg-blue-50/70 dark:border-gray-800 dark:hover:border-blue-500/30 dark:hover:bg-blue-500/10"
              >
                <span className="flex items-center gap-2">
                  <FolderGit2 size={14} className="text-blue-600 dark:text-blue-300" />
                  Workflow inventory
                </span>
                <ArrowRight size={13} className="text-gray-400" />
              </button>
              <button
                onClick={() => setActiveSidebarPanel("furnace")}
                className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-left text-sm transition-colors hover:border-orange-200 hover:bg-orange-50/70 dark:border-gray-800 dark:hover:border-orange-500/30 dark:hover:bg-orange-500/10"
              >
                <span className="flex items-center gap-2">
                  <Flame size={14} className="text-orange-500" />
                  Furnace session snapshot
                </span>
                <ArrowRight size={13} className="text-gray-400" />
              </button>
              <button
                onClick={() => setMode("research")}
                className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-left text-sm transition-colors hover:border-emerald-200 hover:bg-emerald-50/70 dark:border-gray-800 dark:hover:border-emerald-500/30 dark:hover:bg-emerald-500/10"
              >
                <span className="flex items-center gap-2">
                  <BookOpen size={14} className="text-emerald-600 dark:text-emerald-300" />
                  Research mode
                </span>
                <ArrowRight size={13} className="text-gray-400" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function DevelopmentModeStatusStrip({
  electron,
  currentBranch,
  pinnedRoots,
}: {
  electron: boolean;
  currentBranch: string;
  pinnedRoots: string[];
}) {
  const workspaceRoot = pinnedRoots[0];
  const workspaceLabel = workspaceRoot
    ? workspaceRoot.split("/").filter(Boolean).at(-1) || workspaceRoot
    : "No folder pinned";

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 bg-gradient-to-r from-white via-slate-50 to-blue-50/80 px-3 py-2 text-[11px] dark:border-[#3c3c3c] dark:from-[#252526] dark:via-[#202327] dark:to-blue-500/5">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <StatusBadge tone={electron ? "blue" : "amber"}>
          {electron ? "Desktop runtime" : "Browser preview"}
        </StatusBadge>
        <StatusBadge tone="slate">{workspaceLabel}</StatusBadge>
        {currentBranch && (
          <StatusBadge tone="slate">{`Branch ${currentBranch}`}</StatusBadge>
        )}
        <span className="truncate text-gray-500 dark:text-gray-400">
          {electron
            ? "Full workspace, git, terminal, and language tooling are active."
            : "Shell layout and DAN handoff panels stay active here; filesystem, git, terminal, and LSP stay desktop-only."}
        </span>
      </div>
      <div className="flex items-center gap-3 text-gray-500 dark:text-gray-400">
        <span className="inline-flex items-center gap-1">
          <MessageHint /> Chat sidecar cmd+j
        </span>
        <span className="inline-flex items-center gap-1">
          <GitBranch size={11} /> Terminal cmd+`
        </span>
      </div>
    </div>
  );
}

function MessageHint() {
  return <Sparkles size={11} className="text-blue-600 dark:text-blue-300" />;
}
