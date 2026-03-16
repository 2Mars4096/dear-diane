import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import {
  Flame,
  Loader2,
  Search,
  Download,
  Eye,
  FileText,
  AlertTriangle,
  Pause,
  Play,
  X,
  Pin,
  Trash2,
  Package,
  BarChart3,
  BookOpen,
  Lightbulb,
  GitBranch,
  Map,
} from "lucide-react";
import { useResearchStore } from "../../store/useResearchStore";
import {
  furnaceConnectSSE,
  furnaceGetRecipe,
  furnaceCreateSession,
  furnaceAddSources,
  furnaceStartSession,
  furnacePauseSession,
  furnaceResumeSession,
  furnaceCancelSession,
} from "../../lib/api";
import { handleFurnaceSSEEvent } from "../../lib/researchEventRouter";
import { parseFurnaceSources } from "../../lib/furnaceSources";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DistillationConfig {
  topic: string;
  targetCount: number;
  criteria: string;
  sources: string[];
  exploratory: boolean;
}

interface DistillationProgress {
  discovered: number;
  ingested: number;
  read: number;
  extracted: number;
  distilled: number;
  failures: number;
  estimatedRemaining: string;
}

interface TermEntry {
  term: string;
  definition: string;
  frequency?: number;
}

interface MethodPattern {
  name: string;
  description: string;
  papers: number;
}

interface ConceptCluster {
  label: string;
  terms: string[];
  salience: number;
}

interface DistillationOutputs {
  terminology: TermEntry[];
  methodPatterns: MethodPattern[];
  citationNorms: string[];
  rhetoricalStyle: string;
  datasets: string[];
  canonicalQuestions: string[];
  openTensions: string[];
  conceptClusters: ConceptCluster[];
}

interface PinnedPaper {
  id: string;
  title: string;
  pinned: boolean;
  excluded: boolean;
}

type Mode = "setup" | "running" | "paused" | "results";

const DEFAULT_CONFIG: DistillationConfig = {
  topic: "",
  targetCount: 100,
  criteria: "",
  sources: [],
  exploratory: true,
};

const EMPTY_PROGRESS: DistillationProgress = {
  discovered: 0,
  ingested: 0,
  read: 0,
  extracted: 0,
  distilled: 0,
  failures: 0,
  estimatedRemaining: "",
};

const EMPTY_OUTPUTS: DistillationOutputs = {
  terminology: [],
  methodPatterns: [],
  citationNorms: [],
  rhetoricalStyle: "",
  datasets: [],
  canonicalQuestions: [],
  openTensions: [],
  conceptClusters: [],
};

// ---------------------------------------------------------------------------
// DistillationTab (default export)
// ---------------------------------------------------------------------------

export default function DistillationTab() {
  const trainingSessions = useResearchStore((s) => s.trainingSessions);
  const addTrainingSession = useResearchStore((s) => s.addTrainingSession);
  const updateTrainingSession = useResearchStore((s) => s.updateTrainingSession);
  const papersInLibrary = useResearchStore((s) => s.papers);
  const [mode, setMode] = useState<Mode>("setup");
  const [config, setConfig] = useState<DistillationConfig>(DEFAULT_CONFIG);
  const [progress, setProgress] = useState<DistillationProgress>(EMPTY_PROGRESS);
  const [outputs, setOutputs] = useState<DistillationOutputs>(EMPTY_OUTPUTS);
  const [papers, setPapers] = useState<PinnedPaper[]>([]);
  const [exportSessionId, setExportSessionId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const sseRef = useRef<EventSource | null>(null);

  const runningSession = useMemo(
    () => trainingSessions.find((s) => s.status === "running" || s.status === "paused"),
    [trainingSessions],
  );
  const completedSession = useMemo(
    () =>
      trainingSessions
        .filter((s) => s.status === "completed" && s.sessionId)
        .sort((a, b) => (b.lastActivityAt ?? 0) - (a.lastActivityAt ?? 0))[0],
    [trainingSessions],
  );

  useEffect(() => {
    if (runningSession) {
      setMode(runningSession.status === "paused" ? "paused" : "running");
    }
  }, [runningSession]);

  useEffect(() => {
    if (!runningSession?.sessionId) return;
    sseRef.current?.close();
    sseRef.current = furnaceConnectSSE(
      runningSession.sessionId,
      (ev) => handleFurnaceSSEEvent(ev as Record<string, unknown>),
      () => { sseRef.current = null; },
    );
    return () => {
      sseRef.current?.close();
      sseRef.current = null;
    };
  }, [runningSession?.sessionId]);

  useEffect(() => {
    const onUpdate = (e: Event) => {
      const d = (e as CustomEvent).detail as Record<string, unknown>;
      const type = d.type as string;
      setProgress((prev) => {
        const next = { ...prev };
        if (type === "source_status") {
          const status = d.status as string;
          if (status === "ingested") next.ingested += 1;
          if (status === "skipped") next.failures += 1;
        }
        if (type === "phase_completed") {
          const phase = d.phase as string;
          if (phase === "read") next.read = next.ingested;
          if (phase === "extract" || phase === "normalize" || phase === "aggregate" || phase === "infer")
            next.extracted = Math.max(next.extracted, next.ingested);
          if (phase === "project") next.distilled = next.extracted || next.ingested;
        }
        if (type === "session_completed" && d.session_id) {
          setExportSessionId(d.session_id as string);
        }
        return next;
      });
      if (type === "session_completed") {
        setMode("results");
      }
      if (type === "session_paused") setMode("paused");
      if (type === "session_started" || type === "session_resumed") setMode("running");
    };
    const onOutput = (e: Event) => {
      const d = (e as CustomEvent).detail as Record<string, unknown>;
      if (d.artifact_dir) setExportSessionId(d.session_id as string);
      setMode("results");
    };
    window.addEventListener("research:distillation-update", onUpdate);
    window.addEventListener("research:distillation-output", onOutput);
    return () => {
      window.removeEventListener("research:distillation-update", onUpdate);
      window.removeEventListener("research:distillation-output", onOutput);
    };
  }, []);

  const handleStart = useCallback(async () => {
    if (!config.topic.trim()) return;
    setStartError(null);
    setStarting(true);
    setProgress(EMPTY_PROGRESS);
    setOutputs(EMPTY_OUTPUTS);
    try {
      const { session } = await furnaceCreateSession({
        name: config.topic.trim(),
        topic: config.topic.trim(),
        description: config.criteria.trim(),
        target_count: config.targetCount,
      });
      const sid = String(session.session_id || "");
      if (!sid) throw new Error("Missing session_id from furnace create response");

      addTrainingSession({
        sessionId: sid,
        recipeId: String(session.recipe_id || ""),
        name: String(session.name || config.topic.trim()),
        topic: config.topic.trim(),
        status: "idle",
        targetPapers: config.targetCount,
        processedPapers: 0,
      });
      const localId = useResearchStore
        .getState()
        .trainingSessions.find((s) => s.sessionId === sid)?.id;

      const parsed = parseFurnaceSources(config.sources);
      const sourceIds = Array.from(
        new Set([
          ...parsed.source_ids,
          ...papersInLibrary.filter((p) => p.id).map((p) => p.id),
        ]),
      );
      const pdfPaths = Array.from(
        new Set([
          ...parsed.pdf_paths,
          ...papersInLibrary.filter((p) => p.filePath).map((p) => p.filePath!),
        ]),
      );
      if (sourceIds.length > 0 || pdfPaths.length > 0 || parsed.urls.length > 0) {
        await furnaceAddSources(sid, {
          source_ids: sourceIds,
          pdf_paths: pdfPaths,
          urls: parsed.urls,
        });
      }

      await furnaceStartSession(sid);
      if (localId) updateTrainingSession(localId, { status: "running" });
      setMode("running");
    } catch (err) {
      setStartError(err instanceof Error ? err.message : String(err));
      setMode("setup");
    } finally {
      setStarting(false);
    }
  }, [
    addTrainingSession,
    config.criteria,
    config.sources,
    config.targetCount,
    config.topic,
    papersInLibrary,
    updateTrainingSession,
  ]);

  const handlePause = useCallback(async () => {
    if (runningSession?.sessionId) {
      try {
        await furnacePauseSession(runningSession.sessionId);
        updateTrainingSession(runningSession.id, { status: "paused" });
      } catch { /* ignore */ }
    }
    setMode("paused");
  }, [runningSession, updateTrainingSession]);

  const handleResume = useCallback(async () => {
    if (runningSession?.sessionId) {
      try {
        await furnaceResumeSession(runningSession.sessionId);
        updateTrainingSession(runningSession.id, { status: "running" });
      } catch { /* ignore */ }
    }
    setMode("running");
  }, [runningSession, updateTrainingSession]);

  const handleCancel = useCallback(async () => {
    if (runningSession?.sessionId) {
      try {
        await furnaceCancelSession(runningSession.sessionId);
        updateTrainingSession(runningSession.id, { status: "failed" });
      } catch { /* ignore */ }
    }
    setMode("setup");
  }, [runningSession, updateTrainingSession]);

  const handleExportRecipe = useCallback(async () => {
    const sid = exportSessionId ?? completedSession?.sessionId;
    if (sid) {
      try {
        const res = await furnaceGetRecipe(sid);
        const parts: string[] = [];
        if (res.recipe_md) parts.push(`# Recipe\n\n${res.recipe_md}`);
        if (res.skill_md) parts.push(`\n# Skill\n\n${res.skill_md}`);
        const blob = new Blob(parts.length ? parts : [JSON.stringify({ config, outputs, exportedAt: new Date().toISOString() }, null, 2)], {
          type: parts.length ? "text/markdown" : "application/json",
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `recipe-${sid}.md`;
        a.click();
        URL.revokeObjectURL(url);
      } catch {
        const blob = new Blob(
          [JSON.stringify({ config, outputs, exportedAt: new Date().toISOString() }, null, 2)],
          { type: "application/json" },
        );
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `distillation-${config.topic.slice(0, 30).replace(/\s+/g, "-")}.json`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } else {
      const blob = new Blob(
        [JSON.stringify({ config, outputs, exportedAt: new Date().toISOString() }, null, 2)],
        { type: "application/json" },
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `distillation-${config.topic.slice(0, 30).replace(/\s+/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
    }
  }, [config, outputs, exportSessionId, completedSession?.sessionId]);

  const togglePinPaper = useCallback((id: string) => {
    setPapers((prev) =>
      prev.map((p) => (p.id === id ? { ...p, pinned: !p.pinned } : p)),
    );
  }, []);

  const toggleExcludePaper = useCallback((id: string) => {
    setPapers((prev) =>
      prev.map((p) => (p.id === id ? { ...p, excluded: !p.excluded } : p)),
    );
  }, []);

  if (mode === "setup") {
    return (
      <DistillationSetup
        config={config}
        setConfig={setConfig}
        onStart={() => void handleStart()}
        starting={starting}
        error={startError}
      />
    );
  }

  if (mode === "running" || mode === "paused") {
    return (
      <DistillationRunning
        progress={progress}
        paused={mode === "paused"}
        papers={papers}
        onPause={handlePause}
        onResume={handleResume}
        onCancel={handleCancel}
        onTogglePin={togglePinPaper}
        onToggleExclude={toggleExcludePaper}
      />
    );
  }

  return (
    <DistillationResults
      outputs={outputs}
      onExportRecipe={handleExportRecipe}
      onBack={() => setMode("setup")}
    />
  );
}

// ---------------------------------------------------------------------------
// Setup view
// ---------------------------------------------------------------------------

function DistillationSetup({
  config,
  setConfig,
  onStart,
  starting,
  error,
}: {
  config: DistillationConfig;
  setConfig: (c: DistillationConfig) => void;
  onStart: () => void;
  starting: boolean;
  error: string | null;
}) {
  const [sourceInput, setSourceInput] = useState("");

  const addSource = useCallback(() => {
    const trimmed = sourceInput.trim();
    if (trimmed && !config.sources.includes(trimmed)) {
      setConfig({ ...config, sources: [...config.sources, trimmed] });
      setSourceInput("");
    }
  }, [sourceInput, config, setConfig]);

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="max-w-md mx-auto">
        {/* Header */}
        <div className="flex items-center gap-2 mb-1">
          <Flame size={20} className="text-orange-400" />
          <h3 className="text-sm font-semibold text-gray-200">
            Learn 100 Papers
          </h3>
        </div>
        <p className="text-xs text-gray-400 mb-6">
          Define a topic, add sources, and run furnace directly from this tab.
          This starts a real backend session and can run in the background.
        </p>

        <div className="space-y-4">
          {error && (
            <div className="text-xs text-red-300 bg-red-900/30 border border-red-800/60 rounded px-3 py-2">
              {error}
            </div>
          )}
          {/* Topic */}
          <div>
            <label className="block text-xs text-gray-400 mb-1">
              Topic / Domain
            </label>
            <input
              value={config.topic}
              onChange={(e) =>
                setConfig({ ...config, topic: e.target.value })
              }
              placeholder="e.g., Supply chain resilience under disruption"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:border-gray-600 focus:outline-none"
            />
          </div>

          {/* Target count */}
          <div>
            <label className="block text-xs text-gray-400 mb-1">
              Target Paper Count
            </label>
            <input
              type="number"
              min={10}
              max={500}
              value={config.targetCount}
              onChange={(e) =>
                setConfig({
                  ...config,
                  targetCount: parseInt(e.target.value) || 100,
                })
              }
              className="w-24 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:border-gray-600 focus:outline-none"
            />
          </div>

          {/* Inclusion criteria */}
          <div>
            <label className="block text-xs text-gray-400 mb-1">
              Inclusion Criteria (optional)
            </label>
            <textarea
              value={config.criteria}
              onChange={(e) =>
                setConfig({ ...config, criteria: e.target.value })
              }
              placeholder="e.g., Published after 2015, in top OR/MS journals, empirical or analytical"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-xs text-gray-200 placeholder-gray-600 h-20 resize-none focus:border-gray-600 focus:outline-none"
            />
          </div>

          {/* Source URLs / folders */}
          <div>
            <label className="block text-xs text-gray-400 mb-1">
              Sources (optional — PDF path, URL, or source id)
            </label>
            <div className="flex gap-1.5">
              <input
                value={sourceInput}
                onChange={(e) => setSourceInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addSource();
                  }
                }}
                placeholder="Add source and press Enter"
                className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 placeholder-gray-600 focus:border-gray-600 focus:outline-none"
              />
              <button
                onClick={addSource}
                disabled={!sourceInput.trim()}
                className="px-2.5 py-1.5 text-xs bg-gray-700 text-gray-300 rounded hover:bg-gray-600 disabled:opacity-40"
              >
                Add
              </button>
            </div>
            <div className="mt-1.5 rounded border border-gray-700/60 bg-gray-900/40 px-2 py-1.5">
              <p className="text-[10px] text-gray-500 mb-1">Quick examples</p>
              <div className="space-y-0.5 text-[10px] text-gray-400 font-mono">
                <div>/Users/.../paper.pdf</div>
                <div>https://arxiv.org/abs/2401.12345</div>
                <div>source-id-or-slug</div>
              </div>
            </div>
            {config.sources.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {config.sources.map((s, i) => (
                  <span
                    key={i}
                    className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 bg-gray-800 text-gray-400 rounded"
                  >
                    {s.length > 40 ? `${s.slice(0, 40)}...` : s}
                    <button
                      onClick={() =>
                        setConfig({
                          ...config,
                          sources: config.sources.filter(
                            (_, j) => j !== i,
                          ),
                        })
                      }
                      className="text-gray-600 hover:text-red-400"
                    >
                      <X size={10} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Exploratory toggle */}
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input
              type="checkbox"
              checked={config.exploratory}
              onChange={(e) =>
                setConfig({ ...config, exploratory: e.target.checked })
              }
              className="rounded accent-orange-500"
            />
            Exploratory mode (discover related areas beyond the main
            topic)
          </label>

          {/* Start button */}
          <button
            onClick={onStart}
            disabled={!config.topic.trim() || starting}
            className="w-full py-2.5 bg-orange-600 hover:bg-orange-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium flex items-center justify-center gap-2 transition-colors"
          >
            {starting ? <Loader2 size={16} className="animate-spin" /> : <Flame size={16} />}
            Run Furnace
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Running view (also covers paused)
// ---------------------------------------------------------------------------

const STAGE_ICONS = [
  { label: "Discovered", key: "discovered" as const, icon: <Search size={12} /> },
  { label: "Ingested", key: "ingested" as const, icon: <Download size={12} /> },
  { label: "Read", key: "read" as const, icon: <Eye size={12} /> },
  { label: "Extracted", key: "extracted" as const, icon: <FileText size={12} /> },
  { label: "Distilled", key: "distilled" as const, icon: <Flame size={12} /> },
  { label: "Failures", key: "failures" as const, icon: <AlertTriangle size={12} /> },
];

function DistillationRunning({
  progress,
  paused,
  papers,
  onPause,
  onResume,
  onCancel,
  onTogglePin,
  onToggleExclude,
}: {
  progress: DistillationProgress;
  paused: boolean;
  papers: PinnedPaper[];
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
  onTogglePin: (id: string) => void;
  onToggleExclude: (id: string) => void;
}) {
  const [showPapers, setShowPapers] = useState(false);
  const total = progress.discovered || 100;
  const pct = Math.round((progress.distilled / total) * 100);

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="max-w-md mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            {paused ? (
              <Pause size={16} className="text-yellow-400" />
            ) : (
              <Loader2 size={16} className="text-orange-400 animate-spin" />
            )}
            <h3 className="text-sm font-semibold text-gray-200">
              {paused ? "Paused" : "Learning in Progress..."}
            </h3>
          </div>
          <div className="flex gap-1.5">
            {paused ? (
              <button
                onClick={onResume}
                className="px-2.5 py-1 text-[10px] bg-green-900/30 text-green-400 rounded hover:bg-green-900/50 flex items-center gap-1"
              >
                <Play size={10} /> Resume
              </button>
            ) : (
              <button
                onClick={onPause}
                className="px-2.5 py-1 text-[10px] bg-gray-800 text-gray-400 rounded hover:bg-gray-700 flex items-center gap-1"
              >
                <Pause size={10} /> Pause
              </button>
            )}
            <button
              onClick={onCancel}
              className="px-2.5 py-1 text-[10px] bg-red-900/30 text-red-400 rounded hover:bg-red-900/50"
            >
              Cancel
            </button>
          </div>
        </div>

        {/* Progress bar */}
        <div className="mb-4">
          <div className="flex justify-between text-[10px] text-gray-500 mb-1">
            <span>
              {progress.distilled} / {total} papers
            </span>
            <span>{pct}%</span>
          </div>
          <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                paused ? "bg-yellow-600" : "bg-orange-500"
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
          {progress.estimatedRemaining && (
            <p className="text-[10px] text-gray-600 mt-1">
              Est. remaining: {progress.estimatedRemaining}
            </p>
          )}
        </div>

        {/* Stage breakdown */}
        <div className="grid grid-cols-3 gap-2 mb-4">
          {STAGE_ICONS.map((s) => (
            <div
              key={s.key}
              className={`flex items-center gap-2 p-2 rounded ${
                s.key === "failures" && progress.failures > 0
                  ? "bg-red-900/20"
                  : "bg-gray-800/50"
              }`}
            >
              <span
                className={
                  s.key === "failures" && progress.failures > 0
                    ? "text-red-400"
                    : "text-gray-500"
                }
              >
                {s.icon}
              </span>
              <div>
                <p className="text-xs text-gray-300 font-medium">
                  {progress[s.key]}
                </p>
                <p className="text-[9px] text-gray-600">{s.label}</p>
              </div>
            </div>
          ))}
        </div>

        {/* Paper management (human steering) */}
        <div className="border border-gray-800 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowPapers((p) => !p)}
            className="w-full px-3 py-2 flex items-center justify-between text-xs text-gray-400 hover:bg-gray-800/50"
          >
            <span className="flex items-center gap-1.5">
              <BookOpen size={12} />
              Paper Management ({papers.length})
            </span>
            <span className="text-[10px] text-gray-600">
              {papers.filter((p) => p.pinned).length} pinned •{" "}
              {papers.filter((p) => p.excluded).length} excluded
            </span>
          </button>

          {showPapers && (
            <div className="max-h-48 overflow-y-auto border-t border-gray-800">
              {papers.length === 0 ? (
                <p className="px-3 py-3 text-[10px] text-gray-600 text-center">
                  Papers will appear here as they are discovered.
                </p>
              ) : (
                papers.map((p) => (
                  <div
                    key={p.id}
                    className={`px-3 py-1.5 flex items-center gap-2 border-b border-gray-800/30 ${
                      p.excluded ? "opacity-40" : ""
                    }`}
                  >
                    <span className="text-[10px] text-gray-300 flex-1 truncate">
                      {p.title}
                    </span>
                    <button
                      onClick={() => onTogglePin(p.id)}
                      className={`p-0.5 ${
                        p.pinned
                          ? "text-orange-400"
                          : "text-gray-600 hover:text-orange-400"
                      }`}
                      title={p.pinned ? "Unpin" : "Pin as important"}
                    >
                      <Pin size={11} />
                    </button>
                    <button
                      onClick={() => onToggleExclude(p.id)}
                      className={`p-0.5 ${
                        p.excluded
                          ? "text-red-400"
                          : "text-gray-600 hover:text-red-400"
                      }`}
                      title={p.excluded ? "Include" : "Exclude"}
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Results view
// ---------------------------------------------------------------------------

const RESULT_TABS = [
  { id: "terminology", label: "Terms", icon: <BookOpen size={11} /> },
  { id: "methods", label: "Methods", icon: <GitBranch size={11} /> },
  { id: "questions", label: "Questions", icon: <Lightbulb size={11} /> },
  { id: "tensions", label: "Tensions", icon: <AlertTriangle size={11} /> },
  { id: "clusters", label: "Concept Map", icon: <Map size={11} /> },
  { id: "benchmarks", label: "Benchmarks", icon: <BarChart3 size={11} /> },
] as const;

type ResultTabId = (typeof RESULT_TABS)[number]["id"];

function DistillationResults({
  outputs,
  onExportRecipe,
  onBack,
}: {
  outputs: DistillationOutputs;
  onExportRecipe: () => void;
  onBack: () => void;
}) {
  const [activeTab, setActiveTab] = useState<ResultTabId>("terminology");

  const hasAnyOutput = useMemo(
    () =>
      outputs.terminology.length > 0 ||
      outputs.methodPatterns.length > 0 ||
      outputs.canonicalQuestions.length > 0 ||
      outputs.openTensions.length > 0 ||
      outputs.conceptClusters.length > 0,
    [outputs],
  );

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Flame size={14} className="text-orange-400" />
          <span className="text-xs font-semibold text-gray-300">
            Distillation Results
          </span>
        </div>
        <div className="flex gap-1.5">
          <button
            onClick={onExportRecipe}
            className="flex items-center gap-1 px-2 py-1 text-[10px] text-orange-400 bg-orange-900/20 rounded hover:bg-orange-900/40"
          >
            <Package size={12} /> Export Recipe
          </button>
          <button
            onClick={onBack}
            className="px-2 py-1 text-[10px] text-gray-500 bg-gray-800 rounded hover:bg-gray-700"
          >
            New Run
          </button>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-gray-800 overflow-x-auto">
        {RESULT_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`flex items-center gap-1 px-3 py-1.5 text-[10px] whitespace-nowrap flex-shrink-0 ${
              activeTab === t.id
                ? "text-orange-400 border-b border-orange-400"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-3">
        {!hasAnyOutput ? (
          <EmptyResults />
        ) : (
          <>
            {activeTab === "terminology" && (
              <TerminologyList items={outputs.terminology} />
            )}
            {activeTab === "methods" && (
              <MethodsList items={outputs.methodPatterns} />
            )}
            {activeTab === "questions" && (
              <QuestionsList items={outputs.canonicalQuestions} />
            )}
            {activeTab === "tensions" && (
              <TensionsList items={outputs.openTensions} />
            )}
            {activeTab === "clusters" && (
              <ClustersList items={outputs.conceptClusters} />
            )}
            {activeTab === "benchmarks" && <BenchmarksPlaceholder />}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-views
// ---------------------------------------------------------------------------

function TerminologyList({ items }: { items: TermEntry[] }) {
  if (items.length === 0) return <TabEmpty label="terminology" />;
  return (
    <div className="space-y-1.5">
      {items.map((term, i) => (
        <div key={i} className="px-2 py-1.5 bg-gray-800/30 rounded">
          <div className="flex items-baseline justify-between">
            <span className="text-xs font-medium text-gray-200">
              {term.term}
            </span>
            {term.frequency != null && (
              <span className="text-[9px] text-gray-600">
                {term.frequency} papers
              </span>
            )}
          </div>
          <p className="text-[10px] text-gray-500 mt-0.5">
            {term.definition}
          </p>
        </div>
      ))}
    </div>
  );
}

function MethodsList({ items }: { items: MethodPattern[] }) {
  if (items.length === 0) return <TabEmpty label="method patterns" />;
  return (
    <div className="space-y-1.5">
      {items.map((m, i) => (
        <div key={i} className="px-2 py-1.5 bg-gray-800/30 rounded">
          <div className="flex items-baseline justify-between">
            <span className="text-xs font-medium text-gray-200">
              {m.name}
            </span>
            <span className="text-[9px] text-gray-600">
              {m.papers} papers
            </span>
          </div>
          <p className="text-[10px] text-gray-500 mt-0.5">
            {m.description}
          </p>
        </div>
      ))}
    </div>
  );
}

function QuestionsList({ items }: { items: string[] }) {
  if (items.length === 0) return <TabEmpty label="canonical questions" />;
  return (
    <div className="space-y-1.5">
      {items.map((q, i) => (
        <div
          key={i}
          className="px-2 py-1.5 bg-gray-800/30 rounded text-xs text-gray-300"
        >
          {i + 1}. {q}
        </div>
      ))}
    </div>
  );
}

function TensionsList({ items }: { items: string[] }) {
  if (items.length === 0) return <TabEmpty label="open tensions" />;
  return (
    <div className="space-y-2">
      {items.map((t, i) => (
        <div
          key={i}
          className="px-2 py-1.5 bg-yellow-900/10 border-l-2 border-yellow-600/30 rounded-r text-xs text-gray-300"
        >
          {t}
        </div>
      ))}
    </div>
  );
}

function ClustersList({ items }: { items: ConceptCluster[] }) {
  if (items.length === 0) return <TabEmpty label="concept clusters" />;
  return (
    <div className="space-y-2">
      {items.map((c, i) => (
        <div key={i} className="px-2 py-2 bg-gray-800/30 rounded">
          <div className="flex items-baseline justify-between mb-1">
            <span className="text-xs font-medium text-gray-200">
              {c.label}
            </span>
            <span className="text-[9px] text-gray-600">
              salience: {(c.salience * 100).toFixed(0)}%
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            {c.terms.map((t) => (
              <span
                key={t}
                className="text-[9px] px-1.5 py-0.5 bg-gray-700/50 text-gray-400 rounded"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function BenchmarksPlaceholder() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-2 text-center">
      <BarChart3 size={28} className="text-gray-700" />
      <p className="text-xs text-gray-600">
        Before/after writing-quality benchmarks will appear here once a
        comparison evaluation has been run.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty states
// ---------------------------------------------------------------------------

function EmptyResults() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-2 text-center">
      <Flame size={28} className="text-gray-700" />
      <p className="text-xs text-gray-600">
        No distillation outputs yet. Results will populate as papers are
        processed.
      </p>
    </div>
  );
}

function TabEmpty({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center gap-1">
      <p className="text-xs text-gray-600">
        No {label} extracted yet.
      </p>
    </div>
  );
}
