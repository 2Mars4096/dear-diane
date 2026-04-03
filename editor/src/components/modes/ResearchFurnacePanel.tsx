import {
  useState,
  useEffect,
  useCallback,
  useMemo,
} from "react";
import {
  FileText,
  Flame,
  Gauge,
  Loader2,
  Play,
  Search,
  StickyNote,
  X,
  FlaskConical,
} from "lucide-react";
import {
  useResearchStore,
  type TrainingSession,
} from "../../store/useResearchStore";
import {
  furnaceCreateSession,
  furnaceAddSources,
  furnaceStartSession,
  furnaceResumeSession,
} from "../../lib/api";
import { parseFurnaceSources, splitSourceTextBlock } from "../../lib/furnaceSources";
import { useSettingsStore } from "../../store/useSettingsStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import {
  ResearchFurnaceSessionCard,
  ResearchFurnaceSessionFamilyGroup,
  type ResearchFurnaceSessionFamilyGroupData,
} from "./ResearchFurnaceSessionCards";
import type { ResearchFurnaceSessionsController } from "./useResearchFurnaceSessions";

type FurnaceDraftAction =
  | { kind: "new" }
  | {
      kind: "continue";
      sessionId?: string;
      name: string;
      topic: string;
      status: TrainingSession["status"];
    }
  | { kind: "variant"; baseSessionId?: string; baseName: string; topic: string };

type SessionFilterKey =
  | "all"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "variant"
  | "base";

type SessionSortKey =
  | "updated_desc"
  | "updated_asc"
  | "name_asc"
  | "progress_desc"
  | "cost_desc";

const SESSION_FILTER_OPTIONS: Array<{ key: SessionFilterKey; label: string }> = [
  { key: "all", label: "All" },
  { key: "running", label: "Running" },
  { key: "paused", label: "Paused" },
  { key: "completed", label: "Completed" },
  { key: "failed", label: "Failed" },
  { key: "variant", label: "Variants" },
  { key: "base", label: "Base" },
];

const SESSION_SORT_OPTIONS: Array<{ key: SessionSortKey; label: string }> = [
  { key: "updated_desc", label: "Newest" },
  { key: "updated_asc", label: "Oldest" },
  { key: "name_asc", label: "Name" },
  { key: "progress_desc", label: "Progress" },
  { key: "cost_desc", label: "Cost" },
];

function isVariantTrainingSession(session: TrainingSession): boolean {
  return Boolean(session.parentSessionId || session.variantLabel);
}

function getTrainingSessionFamilyKey(session: TrainingSession): string {
  return session.familySessionId || session.parentSessionId || session.sessionId || session.id;
}

function getTrainingSessionProgress(session: TrainingSession): number {
  const total = Math.max(session.sourceCount ?? session.targetPapers ?? 0, 0);
  return total > 0 ? Math.round((session.processedPapers / total) * 100) : 0;
}

function matchesTrainingSessionFilter(
  session: TrainingSession,
  filter: SessionFilterKey,
): boolean {
  if (filter === "all") return true;
  if (filter === "variant") return isVariantTrainingSession(session);
  if (filter === "base") return !isVariantTrainingSession(session);
  return session.status === filter;
}

function buildTrainingSessionSearchText(session: TrainingSession): string {
  return [
    session.name,
    session.topic,
    session.recipeId,
    session.variantLabel,
    ...(session.tags ?? []),
    session.status,
    session.currentPhase,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function sessionHasTag(session: TrainingSession, tag: string): boolean {
  return (session.tags ?? []).includes(tag);
}

function compareTrainingSessions(
  a: TrainingSession,
  b: TrainingSession,
  sort: SessionSortKey,
): number {
  switch (sort) {
    case "updated_asc":
      return (a.lastActivityAt ?? a.startedAt ?? 0) - (b.lastActivityAt ?? b.startedAt ?? 0);
    case "name_asc":
      return (a.name || a.topic || "").localeCompare(b.name || b.topic || "");
    case "progress_desc":
      return getTrainingSessionProgress(b) - getTrainingSessionProgress(a);
    case "cost_desc":
      return (b.totalCostUsd ?? 0) - (a.totalCostUsd ?? 0);
    case "updated_desc":
    default:
      return (b.lastActivityAt ?? b.startedAt ?? 0) - (a.lastActivityAt ?? a.startedAt ?? 0);
  }
}

function FurnaceSurface({
  eyebrow,
  title,
  description,
  tone = "neutral",
  badges = [],
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  tone?: "neutral" | "warm" | "cool";
  badges?: string[];
  children: React.ReactNode;
}) {
  const toneClasses =
    tone === "warm"
      ? "border-orange-200/80 bg-orange-50/80 dark:border-orange-500/20 dark:bg-orange-500/5"
      : tone === "cool"
        ? "border-sky-200/80 bg-sky-50/60 dark:border-sky-500/20 dark:bg-sky-500/5"
        : "border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900/30";

  const eyebrowClasses =
    tone === "warm"
      ? "text-orange-700 dark:text-orange-300"
      : tone === "cool"
        ? "text-sky-700 dark:text-sky-300"
        : "text-gray-500 dark:text-gray-400";

  return (
    <section className={`rounded-2xl border p-4 shadow-sm ${toneClasses}`}>
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <p className={`text-[10px] font-semibold uppercase tracking-[0.22em] ${eyebrowClasses}`}>
            {eyebrow}
          </p>
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</div>
          <p className="max-w-2xl text-xs leading-5 text-gray-600 dark:text-gray-400">
            {description}
          </p>
        </div>
        {badges.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {badges.map((badge) => (
              <span
                key={badge}
                className="rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[10px] text-gray-600 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-300"
              >
                {badge}
              </span>
            ))}
          </div>
        )}
      </div>
      {children}
    </section>
  );
}

function getDraftActionLabel(draftAction: FurnaceDraftAction): string {
  if (draftAction.kind === "continue") return "Continue Existing Session";
  if (draftAction.kind === "variant") return "Variant Branch";
  return "New Furnace Session";
}

export default function ResearchFurnacePanel({
  sessionsController,
}: {
  sessionsController: ResearchFurnaceSessionsController;
}) {
  const trainingSessions = useResearchStore((state) => state.trainingSessions);
  const addTrainingSession = useResearchStore((state) => state.addTrainingSession);
  const updateTrainingSession = useResearchStore((state) => state.updateTrainingSession);
  const papers = useResearchStore((state) => state.papers);
  const wsId = useWorkspaceStore((state) => state.activeWorkspaceId);
  const wsResearchConfig = useWorkspaceStore((state) => {
    const workspace = state.workspaces.find((item) => item.id === state.activeWorkspaceId);
    return workspace?.researchConfig;
  });
  const updateWorkspace = useWorkspaceStore((state) => state.updateWorkspace);
  const pdfRoots = useSettingsStore((state) => state.researchPdfRoots);
  const noteRoots = useSettingsStore((state) => state.researchNoteRoots);
  const wsPdfRoots = wsResearchConfig?.pdfRoots;
  const wsNoteRoots = wsResearchConfig?.noteRoots;
  const corpusTopic = wsResearchConfig?.corpusTopic ?? "";
  const effectivePdfRoots = wsPdfRoots && wsPdfRoots.length > 0 ? wsPdfRoots : pdfRoots;
  const effectiveNoteRoots = wsNoteRoots && wsNoteRoots.length > 0 ? wsNoteRoots : noteRoots;

  const [topic, setTopic] = useState(corpusTopic);
  const [sessionName, setSessionName] = useState("");
  const [targetPapers, setTargetPapers] = useState(100);
  const [sourceText, setSourceText] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftAction, setDraftAction] = useState<FurnaceDraftAction>({ kind: "new" });
  const [sessionQuery, setSessionQuery] = useState("");
  const [sessionFilter, setSessionFilter] = useState<SessionFilterKey>("all");
  const [sessionSort, setSessionSort] = useState<SessionSortKey>("updated_desc");
  const [activeTagFilters, setActiveTagFilters] = useState<string[]>([]);
  const [expandedFamilies, setExpandedFamilies] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (topic && !sessionName) {
      setSessionName(topic.trim());
    }
  }, [topic, sessionName]);

  const handleRunSession = useCallback(async () => {
    if (!topic.trim()) return;

    setError(null);
    setStarting(true);

    try {
      if (wsId) {
        updateWorkspace(wsId, {
          researchConfig: { ...wsResearchConfig, corpusTopic: topic.trim() },
        });
      }

      const manual = parseFurnaceSources(splitSourceTextBlock(sourceText));
      const sourceIds = Array.from(
        new Set([
          ...manual.source_ids,
          ...papers.filter((paper) => paper.id).map((paper) => paper.id),
        ]),
      );
      const pdfPaths = Array.from(
        new Set([
          ...manual.pdf_paths,
          ...papers.filter((paper) => paper.filePath).map((paper) => paper.filePath!),
        ]),
      );
      const urls = Array.from(new Set(manual.urls));
      const hasAny = sourceIds.length > 0 || pdfPaths.length > 0 || urls.length > 0;

      if (draftAction.kind === "continue") {
        const sid = await sessionsController.resolveSessionId(
          draftAction.sessionId,
          draftAction.name,
          draftAction.topic,
        );
        if (!sid) {
          throw new Error("Could not resolve existing session. Refresh and try again.");
        }
        if (draftAction.status === "completed") {
          const stamp = new Date().toISOString().slice(11, 16).replace(":", "");
          setSessionName(`${draftAction.name || draftAction.topic || "session"} / variant-${stamp}`);
          setDraftAction({
            kind: "variant",
            baseSessionId: sid,
            baseName: draftAction.name,
            topic: draftAction.topic,
          });
          window.dispatchEvent(
            new CustomEvent("dan:notification", {
              detail: {
                type: "info",
                title: "Switched to Variant",
                message: "Completed sessions branch into a new variant run instead of mutating the original.",
              },
            }),
          );
          return;
        }
        if (hasAny) {
          await furnaceAddSources(sid, {
            source_ids: sourceIds,
            pdf_paths: pdfPaths,
            urls,
          });
        }
        if (draftAction.status === "running") {
          sessionsController.connectSessionSSE(sid);
          setDraftAction({ kind: "new" });
          return;
        }
        if (draftAction.status === "idle") {
          await furnaceStartSession(sid);
        } else {
          await furnaceResumeSession(sid);
        }
        const localId = useResearchStore
          .getState()
          .trainingSessions.find((session) => session.sessionId === sid)?.id;
        if (localId) {
          updateTrainingSession(localId, { status: "running" });
        }
        sessionsController.connectSessionSSE(sid);
        setDraftAction({ kind: "new" });
        return;
      }

      let variantParentSessionId: string | undefined;
      if (draftAction.kind === "variant") {
        variantParentSessionId = await sessionsController.resolveSessionId(
          draftAction.baseSessionId,
          draftAction.baseName,
          draftAction.topic,
        );
        if (!variantParentSessionId) {
          throw new Error("Could not resolve the base session for this variant. Refresh and try again.");
        }
      }

      const { session } = await furnaceCreateSession({
        name: sessionName.trim() || topic.trim(),
        topic: topic.trim(),
        parent_session_id: variantParentSessionId,
        inherit_sources: draftAction.kind === "variant",
        variant_label:
          draftAction.kind === "variant"
            ? sessionName.trim() || draftAction.baseName || topic.trim()
            : undefined,
        target_count: targetPapers,
      });
      const sid = session.session_id as string;
      const rid = session.recipe_id as string;
      const name = (session.name as string) || topic.trim();
      const status = session.status === "active" ? "running" : "idle";
      const sessionMetadata =
        session.metadata && typeof session.metadata === "object"
          ? (session.metadata as Record<string, unknown>)
          : {};

      addTrainingSession({
        sessionId: sid,
        recipeId: rid,
        parentSessionId:
          (sessionMetadata.parent_session_id as string | undefined) ?? variantParentSessionId,
        familySessionId:
          (sessionMetadata.family_session_id as string | undefined) ?? sid,
        variantLabel:
          (session.variant_label as string | undefined) ??
          (draftAction.kind === "variant"
            ? sessionName.trim() || draftAction.baseName || topic.trim()
            : undefined),
        tags: Array.isArray(session.tags) ? (session.tags as string[]) : [],
        name,
        topic: topic.trim(),
        status,
        targetPapers,
        processedPapers: (session.processed_count as number) ?? 0,
      });

      const localId = useResearchStore
        .getState()
        .trainingSessions.find((existing) => existing.sessionId === sid)?.id;

      if (hasAny) {
        await furnaceAddSources(sid, {
          source_ids: sourceIds,
          pdf_paths: pdfPaths,
          urls,
        });
      }

      await furnaceStartSession(sid);
      if (localId) {
        updateTrainingSession(localId, { status: "running" });
      }
      sessionsController.connectSessionSSE(sid);
      if (draftAction.kind === "variant") {
        setDraftAction({ kind: "new" });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }, [
    addTrainingSession,
    draftAction,
    papers,
    sessionName,
    sessionsController,
    sourceText,
    targetPapers,
    topic,
    updateTrainingSession,
    updateWorkspace,
    wsId,
    wsResearchConfig,
  ]);

  const handlePrepareContinue = useCallback((session: TrainingSession) => {
    setError(null);
    if (session.status === "completed") {
      const stamp = new Date().toISOString().slice(11, 16).replace(":", "");
      setTopic(session.topic || "");
      setSessionName(`${session.name || session.topic || "session"} / variant-${stamp}`);
      setTargetPapers(Math.max(1, session.sourceCount ?? session.targetPapers ?? 100));
      setSourceText("");
      setDraftAction({
        kind: "variant",
        baseSessionId: session.sessionId,
        baseName: session.name,
        topic: session.topic,
      });
      return;
    }
    setTopic(session.topic || "");
    setSessionName(session.name || session.topic || "Untitled Session");
    setTargetPapers(Math.max(1, session.sourceCount ?? session.targetPapers ?? 100));
    setSourceText("");
    setDraftAction({
      kind: "continue",
      sessionId: session.sessionId,
      name: session.name,
      topic: session.topic,
      status: session.status,
    });
  }, []);

  const handlePrepareVariant = useCallback((session: TrainingSession) => {
    setError(null);
    const stamp = new Date().toISOString().slice(11, 16).replace(":", "");
    setTopic(session.topic || "");
    setSessionName(`${session.name || session.topic || "session"} / variant-${stamp}`);
    setTargetPapers(Math.max(1, session.sourceCount ?? session.targetPapers ?? 100));
    setSourceText("");
    setDraftAction({
      kind: "variant",
      baseSessionId: session.sessionId,
      baseName: session.name,
      topic: session.topic,
    });
  }, []);

  const sessionFilterCounts = useMemo(
    () => ({
      all: trainingSessions.length,
      running: trainingSessions.filter((session) => session.status === "running").length,
      paused: trainingSessions.filter((session) => session.status === "paused").length,
      completed: trainingSessions.filter((session) => session.status === "completed").length,
      failed: trainingSessions.filter((session) => session.status === "failed").length,
      variant: trainingSessions.filter((session) => isVariantTrainingSession(session)).length,
      base: trainingSessions.filter((session) => !isVariantTrainingSession(session)).length,
    }),
    [trainingSessions],
  );

  const sessionTagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const session of trainingSessions) {
      for (const tag of session.tags ?? []) {
        counts.set(tag, (counts.get(tag) ?? 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .sort((a, b) => {
        if (b[1] !== a[1]) return b[1] - a[1];
        return a[0].localeCompare(b[0]);
      })
      .map(([tag, count]) => ({ tag, count }));
  }, [trainingSessions]);

  const visibleTagFilters = useMemo(() => {
    const topTags = sessionTagCounts.slice(0, 12);
    const byTag = new Map(topTags.map((entry) => [entry.tag, entry]));
    for (const tag of activeTagFilters) {
      if (!byTag.has(tag)) {
        byTag.set(tag, {
          tag,
          count: sessionTagCounts.find((entry) => entry.tag === tag)?.count ?? 0,
        });
      }
    }
    return Array.from(byTag.values()).sort((a, b) => {
      const aActive = activeTagFilters.includes(a.tag) ? 1 : 0;
      const bActive = activeTagFilters.includes(b.tag) ? 1 : 0;
      if (bActive !== aActive) return bActive - aActive;
      if (b.count !== a.count) return b.count - a.count;
      return a.tag.localeCompare(b.tag);
    });
  }, [activeTagFilters, sessionTagCounts]);

  useEffect(() => {
    setActiveTagFilters((prev) =>
      prev.filter((tag) => sessionTagCounts.some((entry) => entry.tag === tag)),
    );
  }, [sessionTagCounts]);

  const toggleTagFilter = useCallback((tag: string) => {
    setActiveTagFilters((prev) =>
      prev.includes(tag) ? prev.filter((value) => value !== tag) : [...prev, tag],
    );
  }, []);

  const visibleSessionFamilies = useMemo<ResearchFurnaceSessionFamilyGroupData[]>(() => {
    const grouped = new Map<string, TrainingSession[]>();
    for (const session of trainingSessions) {
      const key = getTrainingSessionFamilyKey(session);
      const bucket = grouped.get(key) ?? [];
      bucket.push(session);
      grouped.set(key, bucket);
    }

    const normalizedQuery = sessionQuery.trim().toLowerCase();

    return Array.from(grouped.entries())
      .map(([key, members]) => {
        const root =
          members.find((session) => (session.sessionId || session.id) === key) ??
          members.find((session) => !session.parentSessionId) ??
          members[0];
        const orderedMembers = [
          root,
          ...members
            .filter((session) => session !== root)
            .sort((a, b) => compareTrainingSessions(a, b, sessionSort)),
        ];
        const familySearchText = [
          root?.name,
          root?.topic,
          root?.recipeId,
          root?.variantLabel,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        const filteredByStatus = orderedMembers.filter((session) =>
          matchesTrainingSessionFilter(session, sessionFilter),
        );
        const filteredByTags = filteredByStatus.filter(
          (session) =>
            activeTagFilters.length === 0 ||
            activeTagFilters.some((tag) => sessionHasTag(session, tag)),
        );
        const visibleMembers =
          !normalizedQuery || familySearchText.includes(normalizedQuery)
            ? filteredByTags
            : filteredByTags.filter((session) =>
                buildTrainingSessionSearchText(session).includes(normalizedQuery),
              );
        const latestAt = Math.max(
          ...orderedMembers.map((session) => session.lastActivityAt ?? session.startedAt ?? 0),
        );
        const totalCostUsd = orderedMembers.reduce(
          (sum, session) => sum + (session.totalCostUsd ?? 0),
          0,
        );

        return {
          key,
          displayName: root?.name || root?.topic || "Session family",
          topic: root?.topic || "",
          sessions: orderedMembers,
          visibleSessions: visibleMembers,
          totalCount: orderedMembers.length,
          variantCount: orderedMembers.filter((session) => isVariantTrainingSession(session)).length,
          latestAt,
          totalCostUsd,
        };
      })
      .filter((group) => group.visibleSessions.length > 0)
      .sort((a, b) => {
        switch (sessionSort) {
          case "updated_asc":
            return a.latestAt - b.latestAt;
          case "name_asc":
            return a.displayName.localeCompare(b.displayName);
          case "progress_desc": {
            const aProgress = Math.max(
              ...a.visibleSessions.map((session) => getTrainingSessionProgress(session)),
            );
            const bProgress = Math.max(
              ...b.visibleSessions.map((session) => getTrainingSessionProgress(session)),
            );
            return bProgress - aProgress;
          }
          case "cost_desc":
            return b.totalCostUsd - a.totalCostUsd;
          case "updated_desc":
          default:
            return b.latestAt - a.latestAt;
        }
      });
  }, [activeTagFilters, sessionFilter, sessionQuery, sessionSort, trainingSessions]);

  useEffect(() => {
    setExpandedFamilies((prev) => {
      const next = { ...prev };
      for (const group of visibleSessionFamilies) {
        if (next[group.key] !== undefined) continue;
        next[group.key] =
          group.totalCount <= 1 ||
          group.sessions.some(
            (session) => session.status === "running" || session.status === "paused",
          );
      }
      for (const key of Object.keys(next)) {
        if (!visibleSessionFamilies.some((group) => group.key === key)) {
          delete next[key];
        }
      }
      return next;
    });
  }, [visibleSessionFamilies]);

  const toggleFamilyExpanded = useCallback((key: string) => {
    setExpandedFamilies((prev) => ({ ...prev, [key]: !(prev[key] ?? false) }));
  }, []);

  const forceExpandSessionFamilies =
    sessionQuery.trim().length > 0 ||
    sessionFilter !== "all" ||
    activeTagFilters.length > 0;

  const sessionDeskBadges = [
    `${trainingSessions.length} total`,
    `${sessionFilterCounts.running + sessionFilterCounts.paused} live`,
    `${sessionFilterCounts.completed} completed`,
  ];
  const knownTags = sessionTagCounts.map(({ tag }) => tag);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-6 py-6 lg:px-8">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="rounded-2xl border border-orange-200 bg-orange-50 p-2 text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
                <Flame size={18} />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                  Furnace
                </h2>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  Compose a training run, watch long-lived sessions, and branch variants without losing the research desk.
                </p>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <span className="rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[10px] text-gray-600 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-300">
              {papers.length} library papers ready
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[10px] text-gray-600 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-300">
              {effectivePdfRoots.length + effectiveNoteRoots.length} configured roots
            </span>
          </div>
        </div>

        <FurnaceSurface
          eyebrow="Compose Run"
          title="Define the next Furnace session"
          description="Use this lane to set the research topic, name the run, and add seed sources. The session desk below is where you resume, branch, or monitor existing runs."
          tone="warm"
          badges={[
            getDraftActionLabel(draftAction),
            `${targetPapers} target papers`,
          ]}
        >
          <div className="space-y-4">
            {draftAction.kind !== "new" && (
              <div className="flex flex-col gap-2 rounded-xl border border-blue-200 bg-blue-50 px-3 py-3 dark:border-blue-500/30 dark:bg-blue-500/10 md:flex-row md:items-center md:justify-between">
                <div className="text-xs text-blue-700 dark:text-blue-200">
                  {draftAction.kind === "continue"
                    ? `Build on existing session: ${draftAction.name}`
                    : `Create variant from: ${draftAction.baseName}`}
                </div>
                <button
                  onClick={() => {
                    setDraftAction({ kind: "new" });
                    setError(null);
                  }}
                  className="self-start rounded-full px-2 py-1 text-[10px] font-medium text-blue-700 hover:bg-blue-100 dark:text-blue-200 dark:hover:bg-blue-500/10"
                >
                  Clear draft mode
                </button>
              </div>
            )}

            {error && (
              <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
                {error}
              </p>
            )}

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(260px,0.8fr)]">
              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="block text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    Domain / Topic
                  </label>
                  <input
                    value={topic}
                    onChange={(event) => setTopic(event.target.value)}
                    placeholder="e.g. Supply Chain Resilience, Network Economics, ..."
                    className="w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-orange-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:focus:border-orange-500/60"
                  />
                </div>
                <div className="space-y-2">
                  <label className="block text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    Session Name
                  </label>
                  <input
                    value={sessionName}
                    onChange={(event) => setSessionName(event.target.value)}
                    placeholder="Pre-filled from topic"
                    className="w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-orange-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:focus:border-orange-500/60"
                  />
                </div>
                <div className="space-y-2">
                  <label className="block text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    Seed Sources
                  </label>
                  <textarea
                    value={sourceText}
                    onChange={(event) => setSourceText(event.target.value)}
                    placeholder={"/Users/.../paper.pdf\nhttps://arxiv.org/abs/...."}
                    className="h-28 w-full resize-y rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-orange-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:focus:border-orange-500/60"
                  />
                  <p className="text-[11px] leading-5 text-gray-600 dark:text-gray-400">
                    Paste PDF paths, URLs, or source ids. Furnace will also pull in papers already present in the current library.
                  </p>
                </div>
              </div>

              <div className="space-y-4 rounded-2xl border border-gray-200 bg-white/80 p-4 dark:border-gray-800 dark:bg-gray-950/40">
                <div className="space-y-2">
                  <div className="text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    Launch Controls
                  </div>
                  <div className="space-y-2 rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 dark:border-gray-800 dark:bg-gray-900/50">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-xs text-gray-600 dark:text-gray-400">Current mode</span>
                      <span className="rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[10px] font-medium text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-200">
                        {getDraftActionLabel(draftAction)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-xs text-gray-600 dark:text-gray-400">Library papers</span>
                      <span className="text-xs font-medium text-gray-900 dark:text-gray-100">
                        {papers.length}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <label
                        htmlFor="furnace-target-papers"
                        className="text-xs text-gray-600 dark:text-gray-400"
                      >
                        Target papers
                      </label>
                      <input
                        id="furnace-target-papers"
                        type="number"
                        min={1}
                        max={500}
                        value={targetPapers}
                        onChange={(event) => setTargetPapers(Number(event.target.value) || 100)}
                        className="w-24 rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-right text-sm text-gray-900 outline-none focus:border-orange-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:focus:border-orange-500/60"
                      />
                    </div>
                  </div>
                </div>
                <div className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 dark:border-gray-800 dark:bg-gray-900/50">
                  <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    Quick examples
                  </p>
                  <div className="space-y-1 font-mono text-[11px] text-gray-500 dark:text-gray-400">
                    <div>/Users/.../elliott2022supply.pdf</div>
                    <div>https://arxiv.org/abs/2401.12345</div>
                    <div>supply-network-fragility</div>
                  </div>
                </div>
                <button
                  onClick={() => void handleRunSession()}
                  disabled={!topic.trim() || starting}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-orange-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-orange-500 disabled:cursor-not-allowed disabled:bg-gray-300 disabled:text-gray-500 dark:disabled:bg-gray-700 dark:disabled:text-gray-500"
                >
                  {starting ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Play size={14} />
                  )}
                  {starting
                    ? draftAction.kind === "continue"
                      ? "Continuing..."
                      : draftAction.kind === "variant"
                        ? "Creating Variant..."
                        : "Running..."
                    : draftAction.kind === "continue"
                      ? "Continue Training"
                      : draftAction.kind === "variant"
                        ? "Create Variant"
                        : "Run Furnace"}
                </button>
              </div>
            </div>
          </div>
        </FurnaceSurface>

        <FurnaceSurface
          eyebrow="Research Inputs"
          title="Configured paper and note roots"
          description="These folders act as the standing ingredient shelf for Furnace. Session-specific pasted sources above are additive, not exclusive."
          badges={[
            `${effectivePdfRoots.length} PDF roots`,
            `${effectiveNoteRoots.length} note roots`,
          ]}
        >
          {effectivePdfRoots.length === 0 && effectiveNoteRoots.length === 0 ? (
            <p className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-3 py-3 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-400">
              No roots configured yet. Set PDF and note roots in Settings or workspace config so Furnace knows where to look for standing source material.
            </p>
          ) : (
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                  <FileText size={13} />
                  PDF Roots
                </div>
                <div className="space-y-2">
                  {effectivePdfRoots.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-3 py-2 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-500">
                      No PDF roots configured.
                    </div>
                  ) : (
                    effectivePdfRoots.map((root) => (
                      <div
                        key={root}
                        title={root}
                        className="flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600 dark:border-gray-800 dark:bg-gray-950/40 dark:text-gray-300"
                      >
                        <FileText size={12} className="shrink-0 text-blue-500 dark:text-blue-300" />
                        <span className="min-w-0 flex-1 truncate">{root}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                  <StickyNote size={13} />
                  Note Roots
                </div>
                <div className="space-y-2">
                  {effectiveNoteRoots.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-3 py-2 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-500">
                      No note roots configured.
                    </div>
                  ) : (
                    effectiveNoteRoots.map((root) => (
                      <div
                        key={root}
                        title={root}
                        className="flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600 dark:border-gray-800 dark:bg-gray-950/40 dark:text-gray-300"
                      >
                        <StickyNote size={12} className="shrink-0 text-emerald-500 dark:text-emerald-300" />
                        <span className="min-w-0 flex-1 truncate">{root}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}
        </FurnaceSurface>

        <FurnaceSurface
          eyebrow="Session Desk"
          title="Monitor, resume, and branch existing sessions"
          description="Live sessions stay readable here even when you move back to writing or reading inside Research mode. Use this desk to filter, inspect, continue, and fork runs."
          tone="cool"
          badges={sessionDeskBadges}
        >
          {trainingSessions.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-sky-200 bg-white px-4 py-4 dark:border-sky-500/20 dark:bg-gray-950/30">
              <div className="mb-2 flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
                <FlaskConical size={15} className="text-sky-600 dark:text-sky-300" />
                No sessions yet
              </div>
              <p className="text-sm leading-6 text-gray-600 dark:text-gray-400">
                Start with the composer above. Once you have a first run, this desk becomes the place to continue training, create a variant, inspect the generated recipe, and manage session history.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-2xl border border-gray-200 bg-white px-3 py-3 shadow-sm dark:border-gray-800 dark:bg-gray-950/30">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
                  <div className="relative flex-1">
                    <Search
                      size={13}
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500"
                    />
                    <input
                      value={sessionQuery}
                      onChange={(event) => setSessionQuery(event.target.value)}
                      placeholder="Search sessions by name, topic, recipe id, or variant..."
                      className="w-full rounded-xl border border-gray-300 bg-gray-50 py-2 pl-8 pr-8 text-sm text-gray-900 outline-none focus:border-sky-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:focus:border-sky-500/60"
                    />
                    {sessionQuery && (
                      <button
                        onClick={() => setSessionQuery("")}
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                        title="Clear search"
                      >
                        <X size={12} />
                      </button>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      Sort
                    </span>
                    <select
                      value={sessionSort}
                      onChange={(event) => setSessionSort(event.target.value as SessionSortKey)}
                      className="rounded-lg border border-gray-300 bg-gray-50 px-2 py-2 text-xs text-gray-900 outline-none focus:border-sky-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:focus:border-sky-500/60"
                    >
                      {SESSION_SORT_OPTIONS.map((option) => (
                        <option key={option.key} value={option.key}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap gap-1.5">
                  {SESSION_FILTER_OPTIONS.map((option) => (
                    <button
                      key={option.key}
                      onClick={() => setSessionFilter(option.key)}
                      className={`rounded-full border px-2.5 py-1 text-[10px] transition-colors ${
                        sessionFilter === option.key
                          ? "border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-500/40 dark:bg-sky-500/15 dark:text-sky-200"
                          : "border-gray-300 bg-gray-50 text-gray-600 hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/70 dark:hover:text-gray-200"
                      }`}
                    >
                      {option.label}{" "}
                      <span className="text-[9px] opacity-80">{sessionFilterCounts[option.key]}</span>
                    </button>
                  ))}
                </div>

                {visibleTagFilters.length > 0 && (
                  <div className="mt-3 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                        Tags
                      </span>
                      {activeTagFilters.length > 0 && (
                        <button
                          onClick={() => setActiveTagFilters([])}
                          className="text-[10px] text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200"
                        >
                          Clear tags
                        </button>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {visibleTagFilters.map(({ tag, count }) => {
                        const active = activeTagFilters.includes(tag);
                        return (
                          <button
                            key={tag}
                            onClick={() => toggleTagFilter(tag)}
                            className={`rounded-full border px-2.5 py-1 text-[10px] transition-colors ${
                              active
                                ? "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-200"
                                : "border-gray-300 bg-gray-50 text-gray-600 hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/70 dark:hover:text-gray-200"
                            }`}
                          >
                            #{tag} <span className="text-[9px] opacity-80">{count}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>

              {visibleSessionFamilies.length === 0 ? (
                <p className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-3 py-3 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-400">
                  No sessions match the current search or filter.
                </p>
              ) : (
                <div className="space-y-2">
                  {visibleSessionFamilies.map((group) =>
                    group.totalCount > 1 ? (
                      <ResearchFurnaceSessionFamilyGroup
                        key={group.key}
                        group={group}
                        expanded={
                          forceExpandSessionFamilies ||
                          (expandedFamilies[group.key] ??
                            group.sessions.some(
                              (session) => session.status === "running" || session.status === "paused",
                            ))
                        }
                        onToggle={() => toggleFamilyExpanded(group.key)}
                        onContinue={handlePrepareContinue}
                        onVariant={handlePrepareVariant}
                        knownTags={knownTags}
                        activeTagFilters={activeTagFilters}
                        onToggleTagFilter={toggleTagFilter}
                      />
                    ) : (
                      <ResearchFurnaceSessionCard
                        key={group.visibleSessions[0].id}
                        session={group.visibleSessions[0]}
                        onContinue={handlePrepareContinue}
                        onVariant={handlePrepareVariant}
                        knownTags={knownTags}
                        activeTagFilters={activeTagFilters}
                        onToggleTagFilter={toggleTagFilter}
                      />
                    ),
                  )}
                </div>
              )}
            </div>
          )}
        </FurnaceSurface>

        <FurnaceSurface
          eyebrow="Pipeline"
          title="Five-pass distillation loop"
          description="Normalize metadata, extract facts and methods, aggregate cross-paper structure, infer the author's taste, then project the resulting recipe and evaluation artifacts."
          badges={["Normalize", "Extract", "Aggregate", "Infer Taste", "Project"]}
        >
          <div className="flex flex-wrap items-center gap-1.5 rounded-2xl border border-gray-200 bg-gray-50 px-3 py-3 dark:border-gray-800 dark:bg-gray-900/40">
            {["Normalize", "Extract", "Aggregate", "Infer Taste", "Project"].map(
              (step, index, all) => (
                <div key={step} className="flex items-center gap-1.5">
                  <span className="rounded-lg border border-gray-200 bg-white px-3 py-1 text-[11px] font-medium text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">
                    {step}
                  </span>
                  {index < all.length - 1 && (
                    <span className="text-[10px] text-gray-400 dark:text-gray-500">→</span>
                  )}
                </div>
              ),
            )}
          </div>
          <div className="mt-3 flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400">
            <Gauge size={13} />
            Furnace keeps the writing desk separate from session management: authoring happens above, monitoring happens in the session desk, and this pipeline stays as the quiet execution model underneath both.
          </div>
        </FurnaceSurface>
      </div>
    </div>
  );
}
