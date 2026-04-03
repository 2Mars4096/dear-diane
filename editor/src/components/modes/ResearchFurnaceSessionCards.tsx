import {
  useCallback,
  useMemo,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  Loader2,
  Play,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import {
  furnaceCancelSession,
  furnaceDeleteSession,
  furnaceGetRecipe,
  furnaceListSessions,
  furnacePauseSession,
  furnaceResumeSession,
  furnaceUpdateSessionTags,
} from "../../lib/api";
import {
  useResearchStore,
  type TrainingSession,
} from "../../store/useResearchStore";
import { getResearchFurnaceSessionStatusUi } from "./researchFurnaceSessionStatus";

export interface ResearchFurnaceSessionFamilyGroupData {
  key: string;
  displayName: string;
  topic: string;
  sessions: TrainingSession[];
  visibleSessions: TrainingSession[];
  totalCount: number;
  variantCount: number;
  latestAt: number;
  totalCostUsd: number;
}

function formatRelativeTime(timestamp?: number, nowMs = Date.now()): string | null {
  if (!timestamp) return null;
  const diff = nowMs - timestamp;
  if (diff < 60_000) return "just now";
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function normalizeTrainingSessionTag(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, " ").slice(0, 32);
}

function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function ResearchFurnaceSessionFamilyGroup({
  group,
  expanded,
  onToggle,
  onContinue,
  onVariant,
  knownTags,
  activeTagFilters,
  onToggleTagFilter,
}: {
  group: ResearchFurnaceSessionFamilyGroupData;
  expanded: boolean;
  onToggle: () => void;
  onContinue: (session: TrainingSession) => void;
  onVariant: (session: TrainingSession) => void;
  knownTags: string[];
  activeTagFilters: string[];
  onToggleTagFilter: (tag: string) => void;
}) {
  const latestText = formatRelativeTime(group.latestAt);

  return (
    <div className="overflow-hidden rounded-lg border border-gray-700/50 bg-gray-900/20">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-gray-800/40"
      >
        {expanded ? (
          <ChevronDown size={14} className="shrink-0 text-gray-500" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-gray-500" />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-gray-200">
            {group.displayName}
          </div>
          <div className="truncate text-[10px] text-gray-500">
            {group.topic || "Variant family"}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-1.5 text-[10px]">
          <span className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-300">
            {pluralize(group.totalCount, "session")}
          </span>
          {group.variantCount > 0 && (
            <span className="rounded-full border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-purple-300">
              {pluralize(group.variantCount, "variant")}
            </span>
          )}
          {latestText && (
            <span className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-400">
              updated {latestText}
            </span>
          )}
        </div>
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-gray-700/50 px-2 py-2">
          {group.visibleSessions.length < group.totalCount && (
            <div className="px-2 text-[10px] text-gray-500">
              Showing {group.visibleSessions.length} of {group.totalCount} sessions
            </div>
          )}
          {group.visibleSessions.map((session) => (
            <ResearchFurnaceSessionCard
              key={session.id}
              session={session}
              onContinue={onContinue}
              onVariant={onVariant}
              knownTags={knownTags}
              activeTagFilters={activeTagFilters}
              onToggleTagFilter={onToggleTagFilter}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function ResearchFurnaceSessionCard({
  session,
  onContinue,
  onVariant,
  knownTags,
  activeTagFilters,
  onToggleTagFilter,
}: {
  session: TrainingSession;
  onContinue: (session: TrainingSession) => void;
  onVariant: (session: TrainingSession) => void;
  knownTags: string[];
  activeTagFilters: string[];
  onToggleTagFilter: (tag: string) => void;
}) {
  const updateTrainingSession = useResearchStore((state) => state.updateTrainingSession);
  const removeTrainingSession = useResearchStore((state) => state.removeTrainingSession);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [tagEditorOpen, setTagEditorOpen] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const [tagSaving, setTagSaving] = useState(false);
  const [recipeExpanded, setRecipeExpanded] = useState(false);
  const [recipeContent, setRecipeContent] = useState<{
    pill: string;
    full: string;
    loaded: boolean;
  }>({ pill: "", full: "", loaded: false });
  const [recipeLoading, setRecipeLoading] = useState(false);
  const [recipeShowFull, setRecipeShowFull] = useState(false);

  const sid = session.sessionId ?? session.id;
  const pct =
    session.targetPapers > 0
      ? Math.round((session.processedPapers / session.targetPapers) * 100)
      : 0;
  const phaseLabel =
    session.currentPhase && session.currentPhase !== "idle"
      ? session.currentPhase.replace(/_/g, " ")
      : null;
  const totalSources = session.sourceCount ?? session.targetPapers;
  const lastActivityText = formatRelativeTime(session.lastActivityAt);
  const sessionTags = session.tags ?? [];
  const statusUi = getResearchFurnaceSessionStatusUi(session);
  const actionBusy = loading || deleting;
  const suggestedTags = useMemo(
    () => knownTags.filter((tag) => !sessionTags.includes(tag)).slice(0, 6),
    [knownTags, sessionTags],
  );

  const resolveBackendSessionId = useCallback(async () => {
    if (session.sessionId) return session.sessionId;
    const listed = await furnaceListSessions();
    const normalizedName = (session.name || "").trim().toLowerCase();
    const normalizedTopic = (session.topic || "").trim().toLowerCase();
    const normalizedVariant = (session.variantLabel || "").trim().toLowerCase();
    const fallback = listed.sessions.find((candidate) => {
      if (session.recipeId && candidate.recipe_id === session.recipeId) return true;
      if (
        session.familySessionId &&
        candidate.family_session_id === session.familySessionId &&
        (!normalizedVariant ||
          candidate.variant_label.trim().toLowerCase() === normalizedVariant)
      ) {
        return true;
      }
      const nameMatch = candidate.name.trim().toLowerCase() === normalizedName;
      const topicMatch = candidate.topic.trim().toLowerCase() === normalizedTopic;
      if (!nameMatch || !topicMatch) return false;
      return (
        !normalizedVariant ||
        candidate.variant_label.trim().toLowerCase() === normalizedVariant
      );
    });
    if (!fallback) return undefined;
    updateTrainingSession(session.id, {
      sessionId: fallback.session_id,
      recipeId: fallback.recipe_id || session.recipeId,
      parentSessionId: fallback.parent_session_id || session.parentSessionId,
      familySessionId: fallback.family_session_id || session.familySessionId,
      variantLabel: fallback.variant_label || session.variantLabel,
      tags: fallback.tags ?? session.tags,
    });
    return fallback.session_id;
  }, [
    session.familySessionId,
    session.id,
    session.name,
    session.parentSessionId,
    session.recipeId,
    session.sessionId,
    session.tags,
    session.topic,
    session.variantLabel,
    updateTrainingSession,
  ]);

  const persistTags = useCallback(
    async (nextTags: string[]) => {
      setTagSaving(true);
      try {
        const backendSid = await resolveBackendSessionId();
        if (!backendSid) {
          updateTrainingSession(session.id, { tags: nextTags });
          return;
        }
        const result = await furnaceUpdateSessionTags(backendSid, { tags: nextTags });
        updateTrainingSession(session.id, {
          sessionId: result.session.session_id,
          recipeId: result.session.recipe_id,
          parentSessionId: result.session.parent_session_id || session.parentSessionId,
          familySessionId: result.session.family_session_id || session.familySessionId,
          variantLabel: result.session.variant_label || session.variantLabel,
          tags: result.tags,
        });
      } catch (err) {
        const message =
          err instanceof Error && err.message
            ? err.message
            : `Could not update tags for "${session.name}".`;
        window.dispatchEvent(
          new CustomEvent("dan:notification", {
            detail: {
              type: "error",
              title: "Tag update failed",
              message,
            },
          }),
        );
      } finally {
        setTagSaving(false);
      }
    },
    [
      resolveBackendSessionId,
      session.familySessionId,
      session.id,
      session.name,
      session.parentSessionId,
      session.variantLabel,
      updateTrainingSession,
    ],
  );

  const handleAddTag = useCallback(
    async (rawTag: string) => {
      const tag = normalizeTrainingSessionTag(rawTag);
      if (!tag || sessionTags.includes(tag)) return;
      await persistTags([...sessionTags, tag]);
      setTagDraft("");
      setTagEditorOpen(false);
    },
    [persistTags, sessionTags],
  );

  const handleRemoveTag = useCallback(
    async (tag: string) => {
      await persistTags(sessionTags.filter((value) => value !== tag));
    },
    [persistTags, sessionTags],
  );

  const handleTagKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLInputElement>) => {
      if (event.key !== "Enter" && event.key !== ",") return;
      event.preventDefault();
      void handleAddTag(tagDraft);
    },
    [handleAddTag, tagDraft],
  );

  const handlePause = useCallback(async () => {
    if (!sid) return;
    setLoading(true);
    try {
      await furnacePauseSession(sid);
      updateTrainingSession(session.id, { status: "paused" });
    } finally {
      setLoading(false);
    }
  }, [sid, session.id, updateTrainingSession]);

  const handleResume = useCallback(async () => {
    const backendSid = await resolveBackendSessionId();
    if (!backendSid) {
      window.dispatchEvent(
        new CustomEvent("dan:notification", {
          detail: {
            type: "error",
            title: "Retry failed",
            message: `Could not resolve backend session for "${session.name}". Refresh and try again.`,
          },
        }),
      );
      return;
    }
    setLoading(true);
    try {
      await furnaceResumeSession(backendSid);
      updateTrainingSession(session.id, { status: "running" });
    } finally {
      setLoading(false);
    }
  }, [resolveBackendSessionId, session.id, session.name, updateTrainingSession]);

  const handleCancel = useCallback(async () => {
    if (!sid) return;
    setLoading(true);
    try {
      await furnaceCancelSession(sid);
      updateTrainingSession(session.id, { status: "failed" });
    } finally {
      setLoading(false);
    }
  }, [sid, session.id, updateTrainingSession]);

  const handleDelete = useCallback(async () => {
    const ok = window.confirm(
      `Delete session "${session.name}"?\n\nThis removes it from the Furnace session list.`,
    );
    if (!ok) return;

    setDeleting(true);
    try {
      const backendSid = await resolveBackendSessionId();

      if (!backendSid) {
        removeTrainingSession(session.id);
        window.dispatchEvent(
          new CustomEvent("dan:notification", {
            detail: {
              type: "info",
              title: "Session removed",
              message: `"${session.name}" was removed from local history.`,
            },
          }),
        );
        return;
      }

      if (session.status === "running" || session.status === "paused") {
        await furnaceCancelSession(backendSid);
      }
      await furnaceDeleteSession(backendSid, { delete_artifacts: true });
      removeTrainingSession(session.id);
    } catch (err) {
      const message =
        err instanceof Error && err.message
          ? err.message
          : `Could not delete session "${session.name}".`;
      window.dispatchEvent(
        new CustomEvent("dan:notification", {
          detail: {
            type: "error",
            title: "Delete failed",
            message,
          },
        }),
      );
    } finally {
      setDeleting(false);
    }
  }, [removeTrainingSession, resolveBackendSessionId, session.id, session.name, session.status]);

  const handleToggleRecipe = useCallback(async () => {
    if (recipeExpanded) {
      setRecipeExpanded(false);
      return;
    }
    setRecipeExpanded(true);
    if (recipeContent.loaded) return;
    setRecipeLoading(true);
    try {
      const backendSid = await resolveBackendSessionId();
      if (!backendSid) {
        setRecipeContent({ pill: "(Could not resolve session)", full: "", loaded: true });
        return;
      }
      const res = await furnaceGetRecipe(backendSid);
      const pill = res.recipe_md || "(No recipe generated yet)";
      const full = res.recipe_full_md || "";
      const skillSection = res.skill_md ? `\n\n---\n## Skill Profile\n${res.skill_md}` : "";
      setRecipeContent({
        pill: pill + skillSection,
        full: full ? full + skillSection : "",
        loaded: true,
      });
    } catch {
      setRecipeContent({ pill: "(Failed to load recipe)", full: "", loaded: true });
    } finally {
      setRecipeLoading(false);
    }
  }, [recipeContent.loaded, recipeExpanded, resolveBackendSessionId]);

  return (
    <div className="rounded-lg border border-gray-700/50 bg-gray-800/50 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span
          className={`h-2 w-2 rounded-full ${statusUi.dotClass} ${
            statusUi.pulse ? "animate-pulse" : ""
          }`}
        />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-gray-200">
          {session.name}
        </span>
        <div className="flex items-center gap-1.5">
          <span
            className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusUi.pillClass}`}
          >
            {statusUi.label}
          </span>
          {statusUi.reconnecting && (
            <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[10px] text-sky-200">
              Reconnecting
            </span>
          )}
        </div>
      </div>

      <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[10px]">
        <span className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-300">
          {session.processedPapers}/{totalSources} papers
        </span>
        <span className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-300">
          {pct}% done
        </span>
        {phaseLabel && (
          <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 capitalize text-blue-300">
            {phaseLabel}
          </span>
        )}
        {session.variantLabel && (
          <span className="rounded-full border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-purple-300">
            {session.variantLabel}
          </span>
        )}
        {session.totalCostUsd !== undefined && (
          <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-300">
            ${session.totalCostUsd.toFixed(3)}
          </span>
        )}
        {lastActivityText && (
          <span className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-400">
            updated {lastActivityText}
          </span>
        )}
        {session.extractedPatterns !== undefined && (
          <span className="rounded-full border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-purple-300">
            {session.extractedPatterns} patterns
          </span>
        )}
        {session.extractedTerms !== undefined && (
          <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-indigo-300">
            {session.extractedTerms} terms
          </span>
        )}
      </div>

      <div className="mb-2 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          {sessionTags.map((tag) => {
            const active = activeTagFilters.includes(tag);
            return (
              <span
                key={tag}
                className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] ${
                  active
                    ? "border-amber-500/40 bg-amber-500/15 text-amber-200"
                    : "border-gray-700 bg-gray-800 text-gray-300"
                }`}
              >
                <button
                  onClick={() => onToggleTagFilter(tag)}
                  className="hover:text-white"
                  title="Filter by this tag"
                >
                  #{tag}
                </button>
                <button
                  onClick={() => void handleRemoveTag(tag)}
                  disabled={tagSaving || deleting}
                  className="rounded-full p-0.5 text-gray-500 hover:bg-gray-700/60 hover:text-gray-200 disabled:opacity-50"
                  title="Remove tag"
                >
                  <X size={10} />
                </button>
              </span>
            );
          })}
          {!tagEditorOpen && (
            <button
              onClick={() => setTagEditorOpen(true)}
              disabled={tagSaving || deleting}
              className="inline-flex items-center gap-1 rounded-full border border-dashed border-gray-700 px-2 py-0.5 text-[10px] text-gray-400 hover:border-gray-500 hover:text-gray-200 disabled:opacity-50"
            >
              <Plus size={10} />
              Add tag
            </button>
          )}
        </div>
        {tagEditorOpen && (
          <div className="space-y-1.5 rounded-lg border border-gray-700 bg-gray-900/40 px-2 py-2">
            <div className="flex items-center gap-1.5">
              <input
                value={tagDraft}
                onChange={(event) => setTagDraft(event.target.value)}
                onKeyDown={handleTagKeyDown}
                placeholder="Type tag and press Enter"
                className="flex-1 rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-[11px] text-gray-200 placeholder-gray-500 outline-none focus:border-amber-500/40"
              />
              <button
                onClick={() => void handleAddTag(tagDraft)}
                disabled={!normalizeTrainingSessionTag(tagDraft) || tagSaving || deleting}
                className="rounded-md bg-amber-500/15 px-2 py-1 text-[10px] text-amber-200 hover:bg-amber-500/25 disabled:opacity-50"
              >
                Save
              </button>
              <button
                onClick={() => {
                  setTagDraft("");
                  setTagEditorOpen(false);
                }}
                className="rounded-md px-2 py-1 text-[10px] text-gray-400 hover:bg-gray-700/50 hover:text-gray-200"
              >
                Close
              </button>
            </div>
            {suggestedTags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {suggestedTags.map((tag) => (
                  <button
                    key={tag}
                    onClick={() => void handleAddTag(tag)}
                    disabled={tagSaving || deleting}
                    className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300 hover:bg-gray-700/60 hover:text-white disabled:opacity-50"
                  >
                    + #{tag}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="h-1.5 overflow-hidden rounded-full bg-gray-700">
        <div
          className="h-full rounded-full bg-orange-500/70 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      {session.statusMessage && (
        <p className={`mt-2 truncate text-[10px] ${statusUi.messageClass}`}>
          {statusUi.pulse && (
            <span className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400 align-middle" />
          )}
          {session.statusMessage}
        </p>
      )}

      {session.recentEvents && session.recentEvents.length > 0 && (
        <div className="mt-1.5 space-y-0.5">
          {session.recentEvents.slice(0, 4).map((entry, index) => (
            <div
              key={`${entry}-${index}`}
              className="truncate text-[10px] text-gray-500"
            >
              {entry}
            </div>
          ))}
        </div>
      )}

      {(session.status === "running" ||
        session.status === "paused" ||
        session.status === "failed") && (
        <div className="mt-2 flex gap-1.5">
          {session.status === "running" ? (
            <button
              onClick={() => void handlePause()}
              disabled={actionBusy}
              className="rounded bg-gray-700 px-2 py-1 text-[10px] text-gray-300 hover:bg-gray-600 disabled:opacity-50"
            >
              Pause
            </button>
          ) : session.status === "failed" ? (
            <button
              onClick={() => void handleResume()}
              disabled={actionBusy}
              className="inline-flex items-center gap-1 rounded bg-amber-900/30 px-2 py-1 text-[10px] text-amber-300 hover:bg-amber-900/50 disabled:opacity-50"
              title="Retry this failed session from its saved state"
            >
              <RotateCcw size={11} />
              Retry
            </button>
          ) : (
            <button
              onClick={() => void handleResume()}
              disabled={actionBusy}
              className="rounded bg-green-900/30 px-2 py-1 text-[10px] text-green-400 hover:bg-green-900/50 disabled:opacity-50"
            >
              Resume
            </button>
          )}
          {session.status !== "failed" && (
            <button
              onClick={() => void handleCancel()}
              disabled={actionBusy}
              className="rounded bg-red-900/30 px-2 py-1 text-[10px] text-red-400 hover:bg-red-900/50 disabled:opacity-50"
            >
              Cancel
            </button>
          )}
        </div>
      )}

      <div className="mt-2 flex items-center justify-between gap-2 border-t border-gray-700/50 pt-2">
        <div className="flex flex-wrap gap-1.5">
          {session.status === "completed" && (
            <button
              onClick={() => void handleToggleRecipe()}
              disabled={recipeLoading}
              className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-[10px] ${
                recipeExpanded
                  ? "border-orange-500/30 bg-orange-900/30 text-orange-300"
                  : "border-orange-500/20 bg-orange-500/10 text-orange-300 hover:bg-orange-500/15"
              } disabled:opacity-50`}
              title="View the generated recipe"
            >
              {recipeLoading ? (
                <Loader2 size={11} className="animate-spin" />
              ) : (
                <BookOpen size={11} />
              )}
              {recipeExpanded ? "Hide Recipe" : "View Recipe"}
            </button>
          )}
          {session.status !== "completed" && (
            <button
              onClick={() => onContinue(session)}
              disabled={actionBusy}
              className="inline-flex items-center gap-1 rounded border border-blue-500/20 bg-blue-500/10 px-2 py-1 text-[10px] text-blue-300 hover:bg-blue-500/15 disabled:opacity-50"
              title="Build on this session by adding new sources"
            >
              <Play size={11} />
              Continue
            </button>
          )}
          <button
            onClick={() => onVariant(session)}
            disabled={actionBusy}
            className="inline-flex items-center gap-1 rounded border border-purple-500/20 bg-purple-500/10 px-2 py-1 text-[10px] text-purple-300 hover:bg-purple-500/15 disabled:opacity-50"
            title={
              session.status === "completed"
                ? "Fork a new session from this completed run"
                : "Create a variant session from this one"
            }
          >
            <Sparkles size={11} />
            Variant
          </button>
        </div>
        <button
          onClick={() => void handleDelete()}
          disabled={actionBusy}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] text-gray-500 hover:bg-red-900/20 hover:text-red-400 disabled:opacity-50"
          title="Delete this session"
        >
          {deleting ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
          {deleting ? "Deleting..." : "Delete"}
        </button>
      </div>

      {recipeExpanded && (
        <div className="mt-3 border-t border-gray-700/50 pt-3">
          {recipeLoading ? (
            <div className="flex items-center gap-2 text-[11px] text-gray-400">
              <Loader2 size={12} className="animate-spin" /> Loading recipe…
            </div>
          ) : (
            <div className="space-y-2">
              <div className="max-h-[400px] overflow-y-auto whitespace-pre-wrap rounded-md bg-gray-900/50 p-3 font-mono text-[11px] leading-relaxed text-gray-300">
                {recipeShowFull && recipeContent.full
                  ? recipeContent.full
                  : recipeContent.pill}
              </div>
              <div className="flex gap-2">
                {recipeContent.full && (
                  <button
                    onClick={() => setRecipeShowFull(!recipeShowFull)}
                    className="text-[10px] text-gray-500 hover:text-gray-300"
                  >
                    {recipeShowFull ? "Show pill" : "Show full recipe"}
                  </button>
                )}
                <button
                  onClick={() => {
                    const text =
                      recipeShowFull && recipeContent.full
                        ? recipeContent.full
                        : recipeContent.pill;
                    navigator.clipboard.writeText(text);
                    window.dispatchEvent(
                      new CustomEvent("dan:notification", {
                        detail: {
                          type: "info",
                          title: "Copied",
                          message: "Recipe copied to clipboard.",
                        },
                      }),
                    );
                  }}
                  className="text-[10px] text-gray-500 hover:text-gray-300"
                >
                  Copy to clipboard
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
