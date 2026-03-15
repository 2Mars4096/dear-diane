/**
 * Research mode: purpose-built workspace for academic papers, literature reviews,
 * and systematic research.
 *
 * Layout (1-7 simplification): Left Rail (Library / Plan / Training) | Center Desk
 * (Editor / Reader / Furnace) | Right Drawer (on-demand) | Terminal (bottom).
 */
import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  lazy,
  Suspense,
  type ReactNode,
} from "react";
import { Allotment } from "allotment";
import { useResearchEvents } from "../../hooks/useResearchEvents";
import {
  FileText,
  StickyNote,
  Flame,
  CheckCircle2,
  Loader2,
  XCircle,
  PauseCircle,
  Circle,
  PenLine,
  BookOpen,
  BookMarked,
  MessageSquareText,
  List,
  Search,
  Eye,
  GitCompare,
  GraduationCap,
  ChevronDown,
  ChevronRight,
  Plus,
  PanelBottomClose,
  PanelBottomOpen,
  PanelRightClose,
  PanelRightOpen,
  Gauge,
  Clock,
  X,
  Settings2,
  FolderOpen,
  Clipboard,
  FlaskConical,
  Pause,
  Terminal as TerminalIcon,
  Package,
  Play,
  SquarePen,
} from "lucide-react";
import {
  useResearchStore,
  type PipelineStage,
  type PipelineStageDetails,
  type PipelinePreset,
  type TrainingSession,
} from "../../store/useResearchStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import { useCodeStore } from "../../store/useCodeStore";
import DomainProfileSelector, {
  getActiveProfile,
} from "../research/DomainProfile";
import TerminalPanel from "../code/TerminalPanel";

/* ------------------------------------------------------------------ */
/*  Lazy-loaded panel components                                       */
/* ------------------------------------------------------------------ */

const WritingPane = lazy(() => import("../research/WritingPane"));
const SplitPdfReader = lazy(() => import("../research/SplitPdfReader"));
const ReferencePanel = lazy(() => import("../research/ReferencePanel"));
const ReviewPanel = lazy(() => import("../research/ReviewPanel"));
const OutlinePanel = lazy(() => import("../research/OutlinePanel"));
const NotesPanel = lazy(() => import("../research/NotesPanel"));
const DistillationTab = lazy(() => import("../research/DistillationTab"));

const QuickStartPanel = lazy(
  () => import("../research/QuickStartPanel"),
);

function PanelLoader() {
  return (
    <div className="h-full flex items-center justify-center">
      <Loader2 size={20} className="text-gray-500 animate-spin" />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Utilities                                                          */
/* ------------------------------------------------------------------ */

function useNow(interval = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(id);
  }, [interval]);
  return now;
}

function formatDuration(ms: number): string {
  if (ms <= 0) return "0s";
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
}

function estimateRemaining(pipeline: PipelineStage[], nowMs: number): string {
  const completed = pipeline.filter(
    (s) => s.status === "completed" && s.startedAt && s.completedAt,
  );
  if (completed.length === 0) return "";

  const avgDuration =
    completed.reduce((sum, s) => sum + (s.completedAt! - s.startedAt!), 0) /
    completed.length;
  const remaining = pipeline.filter((s) => s.status === "queued").length;
  const active = pipeline.find((s) => s.status === "active");
  let estimate = remaining * avgDuration;
  if (active?.startedAt)
    estimate += Math.max(0, avgDuration - (nowMs - active.startedAt));

  return estimate > 0 ? formatDuration(estimate) : "";
}

function getStageDetails(
  stage: PipelineStage,
): PipelineStageDetails | null {
  if (!stage.details) return null;
  if (typeof stage.details === "string") return null;
  return stage.details;
}

/* ------------------------------------------------------------------ */
/*  Shared tab button                                                  */
/* ------------------------------------------------------------------ */

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium whitespace-nowrap transition-colors ${
        active
          ? "text-gray-200 border-b-2 border-purple-500 bg-[#1e1e1e]"
          : "text-gray-500 hover:text-gray-300 border-b-2 border-transparent"
      }`}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  NavSection (collapsible sidebar section)                           */
/* ------------------------------------------------------------------ */

function NavSection({
  icon,
  label,
  count,
  children,
}: {
  icon: ReactNode;
  label: string;
  count: number;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] font-medium text-gray-400 hover:text-gray-200 hover:bg-gray-800/60 transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {icon}
        <span className="flex-1 text-left">{label}</span>
        {count > 0 && (
          <span className="text-[9px] text-gray-600 bg-gray-800 px-1.5 rounded-full">
            {count}
          </span>
        )}
      </button>
      {open && <div>{children}</div>}
    </div>
  );
}

function NavItem({
  label,
  subtitle,
  status,
  badge,
  active,
  onClick,
}: {
  label: string;
  subtitle: string;
  status: "unread" | "reading" | "read";
  badge?: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 pl-7 py-1.5 flex items-start gap-2 transition-colors ${
        active
          ? "bg-purple-900/20 border-l-2 border-purple-500"
          : "hover:bg-gray-800/40 border-l-2 border-transparent"
      }`}
    >
      <span
        className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${
          status === "unread"
            ? "bg-purple-400"
            : status === "reading"
              ? "bg-blue-400"
              : "bg-gray-600"
        }`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1">
          <p className="text-[11px] text-gray-300 truncate flex-1">{label}</p>
          {badge && (
            <span className="text-[8px] text-amber-400 bg-amber-400/10 px-1 rounded shrink-0">
              {badge}
            </span>
          )}
        </div>
        <p className="text-[9px] text-gray-600 truncate">{subtitle}</p>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Rail section buttons (Library / Plan / Training)                   */
/* ------------------------------------------------------------------ */

type RailSection = "library" | "plan" | "training";

const RAIL_SECTIONS: {
  id: RailSection;
  label: string;
  icon: ReactNode;
}[] = [
  { id: "library", label: "Library", icon: <FolderOpen size={14} /> },
  { id: "plan", label: "Plan", icon: <Clipboard size={14} /> },
  { id: "training", label: "Training", icon: <FlaskConical size={14} /> },
];

/* ------------------------------------------------------------------ */
/*  Library Section                                                    */
/* ------------------------------------------------------------------ */

function LibrarySection() {
  const papers = useResearchStore((s) => s.papers);
  const annotations = useResearchStore((s) => s.annotations);
  const noteCount = useResearchStore((s) => s.notes.length);
  const activePaperId = useResearchStore((s) => s.activePaperId);
  const setActivePaper = useResearchStore((s) => s.setActivePaper);
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);
  const pdfRoots = useSettingsStore((s) => s.researchPdfRoots);
  const noteRoots = useSettingsStore((s) => s.researchNoteRoots);
  const wsPdfRoots = useWorkspaceStore((s) => {
    const ws = s.workspaces.find((w) => w.id === s.activeWorkspaceId);
    return ws?.researchConfig?.pdfRoots;
  });
  const wsNoteRoots = useWorkspaceStore((s) => {
    const ws = s.workspaces.find((w) => w.id === s.activeWorkspaceId);
    return ws?.researchConfig?.noteRoots;
  });

  const effectiveRoots = useMemo(() => {
    const pdf = wsPdfRoots && wsPdfRoots.length > 0 ? wsPdfRoots : pdfRoots;
    const note = wsNoteRoots && wsNoteRoots.length > 0 ? wsNoteRoots : noteRoots;
    return { pdf, note };
  }, [pdfRoots, noteRoots, wsPdfRoots, wsNoteRoots]);

  const annotatedPaperIds = useMemo(
    () => new Set(annotations.map((a) => a.paperId)),
    [annotations],
  );

  const paperStatus = useCallback(
    (paperId: string, status: "unread" | "reading" | "read") => {
      if (annotatedPaperIds.has(paperId)) return "annotated" as const;
      return status;
    },
    [annotatedPaperIds],
  );

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Configured roots */}
      {(effectiveRoots.pdf.length > 0 || effectiveRoots.note.length > 0) && (
        <div className="px-3 py-1.5 border-b border-gray-800/50">
          {effectiveRoots.pdf.map((root) => (
            <div
              key={root}
              className="text-[9px] text-gray-600 truncate flex items-center gap-1 py-0.5"
              title={root}
            >
              <FileText size={9} className="shrink-0 text-gray-700" />
              {root.split("/").pop()}
            </div>
          ))}
          {effectiveRoots.note.map((root) => (
            <div
              key={root}
              className="text-[9px] text-gray-600 truncate flex items-center gap-1 py-0.5"
              title={root}
            >
              <StickyNote size={9} className="shrink-0 text-gray-700" />
              {root.split("/").pop()}
            </div>
          ))}
        </div>
      )}

      {/* Papers */}
      <NavSection
        icon={<FileText size={14} />}
        label="Papers"
        count={papers.length}
      >
        {papers.length === 0 ? (
          <p className="px-7 py-2 text-[10px] text-gray-600 italic">
            No papers yet — drag a PDF or use a quick-start
          </p>
        ) : (
          papers.map((p) => {
            const displayStatus = paperStatus(p.id, p.status);
            return (
              <NavItem
                key={p.id}
                label={p.title}
                subtitle={`${p.authors[0] ?? "Unknown"} ${p.year}`}
                status={displayStatus === "annotated" ? "read" : p.status}
                badge={displayStatus === "annotated" ? "annotated" : undefined}
                active={activePaperId === p.id}
                onClick={() => {
                  setActivePaper(p.id);
                  setPrimaryTab("reader");
                }}
              />
            );
          })
        )}
      </NavSection>

      {/* Notes */}
      <NavSection
        icon={<StickyNote size={14} />}
        label="Notes"
        count={noteCount}
      >
        <button
          onClick={() => {
            useResearchStore.getState().setShowContextPanel(true);
            useResearchStore.getState().setContextTab("notes");
          }}
          className="w-full text-left px-4 pl-7 py-1 text-[11px] text-gray-400 hover:bg-gray-800/40"
        >
          View all notes
        </button>
      </NavSection>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Plan Section                                                       */
/* ------------------------------------------------------------------ */

function PlanSection() {
  const documentContent = useResearchStore((s) => s.documentContent);
  const setShowContextPanel = useResearchStore((s) => s.setShowContextPanel);
  const setContextTab = useResearchStore((s) => s.setContextTab);
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);

  const headings = useMemo(() => {
    if (!documentContent) return [];
    return documentContent
      .split("\n")
      .filter((l) => /^#{1,3}\s/.test(l))
      .map((l) => ({
        level: (l.match(/^(#+)/)![1].length as 1 | 2 | 3),
        text: l.replace(/^#+\s*/, ""),
      }));
  }, [documentContent]);

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Outline */}
      <NavSection icon={<List size={14} />} label="Outline" count={headings.length}>
        {headings.length === 0 ? (
          <p className="px-7 py-2 text-[10px] text-gray-600 italic">
            Start writing to see outline
          </p>
        ) : (
          headings.slice(0, 20).map((h, i) => (
            <button
              key={i}
              onClick={() => {
                setPrimaryTab("editor");
                setShowContextPanel(true);
                setContextTab("outline");
              }}
              className="w-full text-left px-4 py-0.5 text-[11px] text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 truncate transition-colors"
              style={{ paddingLeft: `${12 + (h.level - 1) * 10}px` }}
            >
              {h.text}
            </button>
          ))
        )}
      </NavSection>

      {/* Quick access */}
      <NavSection icon={<MessageSquareText size={14} />} label="Review" count={0}>
        <button
          onClick={() => {
            setShowContextPanel(true);
            setContextTab("reviews");
          }}
          className="w-full text-left px-4 pl-7 py-1 text-[11px] text-gray-400 hover:bg-gray-800/40"
        >
          Open reviews
        </button>
      </NavSection>

      <NavSection icon={<BookMarked size={14} />} label="References" count={0}>
        <button
          onClick={() => {
            setShowContextPanel(true);
            setContextTab("references");
          }}
          className="w-full text-left px-4 pl-7 py-1 text-[11px] text-gray-400 hover:bg-gray-800/40"
        >
          Open references
        </button>
      </NavSection>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Training Section                                                   */
/* ------------------------------------------------------------------ */

function TrainingSection() {
  const sessions = useResearchStore((s) => s.trainingSessions);
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);

  const openFurnace = () => setPrimaryTab("furnace");

  return (
    <div className="flex-1 overflow-y-auto">
      <NavSection
        icon={<FlaskConical size={14} />}
        label="Sessions"
        count={sessions.length}
      >
        {sessions.length === 0 ? (
          <div className="px-7 py-2">
            <p className="text-[10px] text-gray-600 italic mb-2">
              No training sessions yet
            </p>
            <button
              onClick={openFurnace}
              className="text-[10px] text-purple-400 hover:text-purple-300 flex items-center gap-1"
            >
              <Plus size={10} /> Start a session
            </button>
          </div>
        ) : (
          sessions.map((sess) => (
            <TrainingSessionItem
              key={sess.id}
              session={sess}
              onClick={openFurnace}
            />
          ))
        )}
      </NavSection>

      <button
        onClick={openFurnace}
        className="w-full text-left px-4 pl-7 py-1 text-[11px] text-gray-400 hover:bg-gray-800/40"
      >
        Open Furnace
      </button>
    </div>
  );
}

function TrainingSessionItem({
  session,
  onClick,
}: {
  session: TrainingSession;
  onClick: () => void;
}) {
  const pct =
    session.targetPapers > 0
      ? Math.round((session.processedPapers / session.targetPapers) * 100)
      : 0;

  const statusColors: Record<TrainingSession["status"], string> = {
    idle: "text-gray-500",
    running: "text-blue-400",
    paused: "text-yellow-400",
    completed: "text-green-400",
    failed: "text-red-400",
  };

  const statusIcons: Record<TrainingSession["status"], ReactNode> = {
    idle: <Circle size={10} />,
    running: <Loader2 size={10} className="animate-spin" />,
    paused: <Pause size={10} />,
    completed: <CheckCircle2 size={10} />,
    failed: <XCircle size={10} />,
  };

  return (
    <button
      onClick={onClick}
      className="w-full text-left px-4 pl-7 py-1.5 hover:bg-gray-800/40 transition-colors group"
    >
      <div className="flex items-center gap-2">
        <span className={statusColors[session.status]}>
          {statusIcons[session.status]}
        </span>
        <span className="text-[11px] text-gray-300 truncate flex-1">
          {session.name}
        </span>
      </div>
      <div className="ml-5 mt-0.5">
        <div className="flex items-center gap-2 text-[9px] text-gray-600">
          <span>
            {session.processedPapers}/{session.targetPapers} papers
          </span>
          <span>{pct}%</span>
        </div>
        <div className="mt-0.5 h-0.5 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-purple-500/70 rounded-full transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Function-first Left Rail                                           */
/* ------------------------------------------------------------------ */

function FunctionRail() {
  const activeSection = useResearchStore((s) => s.activeRailSection);
  const setActiveSection = useResearchStore((s) => s.setActiveRailSection);
  const [showProfileSelector, setShowProfileSelector] = useState(false);
  const activeProfile = getActiveProfile();

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Section tabs */}
      <div className="flex border-b border-gray-800">
        {RAIL_SECTIONS.map((sec) => (
          <button
            key={sec.id}
            onClick={() => setActiveSection(sec.id)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[10px] font-medium transition-colors ${
              activeSection === sec.id
                ? "text-gray-200 border-b-2 border-purple-500 bg-[#1e1e1e]"
                : "text-gray-500 hover:text-gray-300 border-b-2 border-transparent"
            }`}
            title={sec.label}
          >
            {sec.icon}
            <span className="hidden sm:inline">{sec.label}</span>
          </button>
        ))}
      </div>

      {/* Section content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeSection === "library" && <LibrarySection />}
        {activeSection === "plan" && <PlanSection />}
        {activeSection === "training" && <TrainingSection />}
      </div>

      {/* Domain profile (always visible) */}
      <div className="border-t border-gray-800 shrink-0">
        <button
          onClick={() => setShowProfileSelector((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2 text-[11px] text-gray-400 hover:text-gray-200 hover:bg-gray-800/60 transition-colors"
        >
          <Settings2 size={12} />
          <span className="flex-1 text-left truncate">
            {activeProfile.name}
          </span>
          <span className="text-[9px] text-gray-600 bg-gray-800 px-1.5 rounded-full">
            {activeProfile.citationStyle.toUpperCase()}
          </span>
          {showProfileSelector ? (
            <ChevronDown size={10} />
          ) : (
            <ChevronRight size={10} />
          )}
        </button>
        {showProfileSelector && (
          <DomainProfileSelector
            onSelect={() => setShowProfileSelector(false)}
          />
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Pipeline presets                                                    */
/* ------------------------------------------------------------------ */

const RESEARCH_STAGES: PipelineStage[] = [
  { id: "search", label: "Search", status: "queued" },
  { id: "read", label: "Read", status: "queued" },
  { id: "analyze", label: "Analyze", status: "queued" },
  { id: "outline", label: "Outline", status: "queued" },
  { id: "write", label: "Write", status: "queued" },
  { id: "review", label: "Review", status: "queued" },
  { id: "finalize", label: "Finalize", status: "queued" },
];

const DISTILLATION_STAGES: PipelineStage[] = [
  { id: "discover", label: "Discover", status: "queued" },
  { id: "ingest", label: "Ingest", status: "queued" },
  { id: "extract", label: "Extract", status: "queued" },
  { id: "distill", label: "Distill", status: "queued" },
  { id: "evaluate", label: "Evaluate", status: "queued" },
];

function getDefaultStages(preset: PipelinePreset): PipelineStage[] {
  return preset === "distillation" ? DISTILLATION_STAGES : RESEARCH_STAGES;
}

const STAGE_PANEL_MAP: Record<
  string,
  { primary?: "editor" | "reader"; context?: "references" | "reviews" | "outline" | "notes" | "distillation" }
> = {
  search: { context: "references" },
  read: { primary: "reader" },
  analyze: { context: "notes" },
  outline: { context: "outline" },
  write: { primary: "editor" },
  review: { context: "reviews" },
  finalize: { primary: "editor" },
  discover: { context: "references" },
  ingest: { primary: "reader" },
  extract: { context: "notes" },
  distill: { context: "distillation" },
  evaluate: { context: "reviews" },
};

/* ------------------------------------------------------------------ */
/*  Pipeline Progress (left sidebar — bottom section)                  */
/* ------------------------------------------------------------------ */

function StageIcon({ status }: { status: PipelineStage["status"] }) {
  switch (status) {
    case "completed":
      return (
        <CheckCircle2 size={14} className="text-green-500 shrink-0" />
      );
    case "active":
      return (
        <Loader2
          size={14}
          className="text-blue-400 animate-spin shrink-0"
        />
      );
    case "failed":
      return <XCircle size={14} className="text-red-500 shrink-0" />;
    case "paused":
      return (
        <PauseCircle size={14} className="text-yellow-500 shrink-0" />
      );
    default:
      return <Circle size={14} className="text-gray-600 shrink-0" />;
  }
}

function StageDetail({ stage, nowMs }: { stage: PipelineStage; nowMs: number }) {
  const details = getStageDetails(stage);
  const duration =
    stage.completedAt && stage.startedAt
      ? formatDuration(stage.completedAt - stage.startedAt)
      : stage.startedAt
        ? formatDuration(nowMs - stage.startedAt)
        : "—";

  return (
    <div className="ml-6 mt-1 p-2 bg-gray-800/30 rounded border-l-2 border-gray-700 animate-in">
      <div className="space-y-1 text-[10px]">
        <div className="flex justify-between text-gray-500">
          <span>Duration</span>
          <span>{duration}</span>
        </div>
        {details && (
          <>
            <div className="flex justify-between text-gray-500">
              <span>Nodes run</span>
              <span>{details.nodeCount ?? "—"}</span>
            </div>
            <div className="flex justify-between text-gray-500">
              <span>Tokens used</span>
              <span>{details.tokenCount ?? "—"}</span>
            </div>
            {details.outputs && details.outputs.length > 0 && (
              <div className="mt-1">
                <span className="text-gray-600">Outputs:</span>
                <ul className="mt-0.5 space-y-0.5">
                  {details.outputs.map((o: string, i: number) => (
                    <li key={i} className="text-gray-500 pl-2">
                      • {o}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
        {typeof stage.details === "string" && stage.details && (
          <p className="text-gray-500 italic">{stage.details}</p>
        )}
      </div>
    </div>
  );
}

function PipelineProgress() {
  const pipeline = useResearchStore((s) => s.pipeline);
  const pipelinePreset = useResearchStore((s) => s.pipelinePreset);
  const setPipelinePreset = useResearchStore((s) => s.setPipelinePreset);
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);
  const setContextTab = useResearchStore((s) => s.setContextTab);
  const addStageAction = useResearchStore((s) => s.addStage);
  const removeStageAction = useResearchStore((s) => s.removeStage);
  const nowMs = useNow(1000);

  const [expandedStage, setExpandedStage] = useState<string | null>(null);
  const [addingStage, setAddingStage] = useState(false);
  const [newStageName, setNewStageName] = useState("");

  const stages =
    pipeline.length > 0 ? pipeline : getDefaultStages(pipelinePreset);
  const completed = stages.filter((s) => s.status === "completed").length;
  const eta = estimateRemaining(stages, nowMs);

  const handleStageClick = (stageId: string) => {
    setExpandedStage((prev) => (prev === stageId ? null : stageId));

    const mapping = STAGE_PANEL_MAP[stageId];
    if (!mapping) return;
    if (mapping.primary) setPrimaryTab(mapping.primary);
    if (mapping.context) {
      setContextTab(mapping.context);
      useResearchStore.getState().setShowContextPanel(true);
    }
  };

  const handleAddStage = () => {
    const name = newStageName.trim();
    if (!name) return;
    addStageAction({
      id: `custom-${Date.now()}`,
      label: name,
      status: "queued",
    });
    setNewStageName("");
    setAddingStage(false);
  };

  const handleRemoveStage = (
    e: React.MouseEvent,
    stageId: string,
  ) => {
    e.stopPropagation();
    removeStageAction(stageId);
    if (expandedStage === stageId) setExpandedStage(null);
  };

  const handlePresetSwitch = (preset: PipelinePreset) => {
    setPipelinePreset(preset);
    if (pipeline.length === 0) return;
    /* Only reset if pipeline matches the old default exactly */
    const oldDefault = getDefaultStages(
      preset === "research" ? "distillation" : "research",
    );
    const isOldDefault =
      pipeline.length === oldDefault.length &&
      pipeline.every(
        (s, i) =>
          s.id === oldDefault[i].id && s.status === "queued",
      );
    if (isOldDefault) {
      useResearchStore.getState().setPipeline([]);
    }
  };

  const progressPct =
    stages.length > 0 ? (completed / stages.length) * 100 : 0;

  return (
    <div className="border-t border-gray-800 flex-shrink-0">
      {/* Header */}
      <div className="px-3 py-2 text-[10px] font-semibold text-gray-500 uppercase tracking-wider flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Gauge size={11} />
          Pipeline
        </div>
        <span className="text-gray-600 font-normal normal-case">
          {completed}/{stages.length}
        </span>
      </div>

      {/* Preset toggle */}
      <div className="px-3 pb-1.5 flex gap-1">
        {(["research", "distillation"] as const).map((preset) => (
          <button
            key={preset}
            onClick={() => handlePresetSwitch(preset)}
            className={`flex-1 py-0.5 text-[9px] rounded transition-colors ${
              pipelinePreset === preset
                ? "bg-purple-900/40 text-purple-300 border border-purple-500/30"
                : "text-gray-600 hover:text-gray-400 border border-transparent hover:border-gray-700"
            }`}
          >
            {preset === "research" ? "Research" : "Distillation"}
          </button>
        ))}
      </div>

      {/* Progress bar */}
      <div className="px-3 pb-1.5">
        <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-purple-600 to-blue-500 rounded-full transition-all duration-700 ease-out"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* ETA */}
      {eta && (
        <div className="px-3 pb-1 flex items-center gap-1 text-[9px] text-gray-600">
          <Clock size={9} />
          <span>~{eta} remaining</span>
        </div>
      )}

      {/* Stages list */}
      <div className="px-3 pb-2 space-y-0.5">
        {stages.map((stage) => (
          <div key={stage.id}>
            <div className="flex items-center group">
              <button
                onClick={() => handleStageClick(stage.id)}
                className="flex-1 flex items-center gap-2 py-1 px-1 rounded hover:bg-gray-800/50 transition-colors text-left"
              >
                <StageIcon status={stage.status} />
                <span
                  className={`text-[11px] flex-1 transition-colors ${
                    stage.status === "completed"
                      ? "text-gray-500 line-through"
                      : stage.status === "active"
                        ? "text-blue-300 font-medium"
                        : stage.status === "failed"
                          ? "text-red-400"
                          : "text-gray-500"
                  }`}
                >
                  {stage.label}
                </span>
                {stage.status === "active" && stage.startedAt && (
                  <span className="text-[9px] text-gray-600">
                    {formatDuration(nowMs - stage.startedAt)}
                  </span>
                )}
                <span className="text-gray-700">
                  {expandedStage === stage.id ? (
                    <ChevronDown size={10} />
                  ) : (
                    <ChevronRight size={10} />
                  )}
                </span>
              </button>
              {stage.id.startsWith("custom-") && (
                <button
                  onClick={(e) => handleRemoveStage(e, stage.id)}
                  className="opacity-0 group-hover:opacity-100 p-0.5 text-gray-600 hover:text-red-400 transition-all"
                  title="Remove stage"
                >
                  <X size={10} />
                </button>
              )}
            </div>
            {expandedStage === stage.id && (
              <StageDetail stage={stage} nowMs={nowMs} />
            )}
          </div>
        ))}
      </div>

      {/* Add stage */}
      <div className="px-3 pb-1 flex gap-1">
        {addingStage ? (
          <div className="flex-1 flex gap-1">
            <input
              autoFocus
              value={newStageName}
              onChange={(e) => setNewStageName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAddStage();
                if (e.key === "Escape") {
                  setAddingStage(false);
                  setNewStageName("");
                }
              }}
              placeholder="Stage name…"
              className="flex-1 bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-[10px] text-gray-300 placeholder-gray-600 outline-none focus:border-purple-500/50"
            />
            <button
              onClick={handleAddStage}
              className="text-[9px] text-purple-400 hover:text-purple-300 px-1"
            >
              Add
            </button>
            <button
              onClick={() => {
                setAddingStage(false);
                setNewStageName("");
              }}
              className="text-[9px] text-gray-600 hover:text-gray-400 px-1"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setAddingStage(true)}
            className="text-[9px] text-gray-600 hover:text-gray-400 flex items-center gap-0.5 transition-colors"
          >
            <Plus size={9} /> Add stage
          </button>
        )}
      </div>

      {/* Action buttons */}
      <div className="px-3 pb-2 flex gap-1.5">
        <button className="flex-1 py-1 text-[10px] text-gray-400 bg-gray-800 rounded hover:bg-gray-700 transition-colors">
          Pause
        </button>
        <button className="flex-1 py-1 text-[10px] text-gray-400 bg-gray-800 rounded hover:bg-gray-700 transition-colors">
          Cancel
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Primary Panel (Editor | PDF Reader | Furnace)                      */
/* ------------------------------------------------------------------ */

function PrimaryPanel() {
  const primaryTab = useResearchStore((s) => s.primaryTab);
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);
  const papers = useResearchStore((s) => s.papers);
  const notes = useResearchStore((s) => s.notes);
  const documentContent = useResearchStore((s) => s.documentContent);
  const activeQuickStart = useResearchStore((s) => s.activeQuickStart);
  const profile = getActiveProfile();

  const isEmpty =
    papers.length === 0 &&
    notes.length === 0 &&
    !documentContent &&
    !activeQuickStart &&
    primaryTab !== "furnace";

  if (isEmpty) {
    return (
      <Suspense fallback={<ResearchEmptyStateFallback />}>
        <QuickStartPanel visibleIds={profile.quickStarts} />
      </Suspense>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex border-b border-gray-800 bg-[#252526]">
        <TabButton
          active={primaryTab === "editor"}
          onClick={() => setPrimaryTab("editor")}
        >
          <PenLine size={12} /> Editor
        </TabButton>
        <TabButton
          active={primaryTab === "reader"}
          onClick={() => setPrimaryTab("reader")}
        >
          <BookOpen size={12} /> PDF Reader
        </TabButton>
        <TabButton
          active={primaryTab === "furnace"}
          onClick={() => setPrimaryTab("furnace")}
        >
          <Flame size={12} /> Furnace
        </TabButton>
      </div>
      <div className="flex-1 min-h-0">
        <Suspense fallback={<PanelLoader />}>
          {primaryTab === "editor" && <WritingPane />}
          {primaryTab === "reader" && <SplitPdfReader />}
          {primaryTab === "furnace" && <FurnacePanel />}
        </Suspense>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Context Panel (References | Reviews | Outline | Notes | Distil.)   */
/* ------------------------------------------------------------------ */

const CONTEXT_TABS = [
  {
    id: "references" as const,
    label: "References",
    icon: <BookMarked size={11} />,
  },
  {
    id: "reviews" as const,
    label: "Reviews",
    icon: <MessageSquareText size={11} />,
  },
  { id: "outline" as const, label: "Outline", icon: <List size={11} /> },
  {
    id: "notes" as const,
    label: "Notes",
    icon: <StickyNote size={11} />,
  },
  {
    id: "distillation" as const,
    label: "Distillation",
    icon: <Flame size={11} />,
  },
];

function ContextPanel() {
  const contextTab = useResearchStore((s) => s.contextTab);
  const setContextTab = useResearchStore((s) => s.setContextTab);

  return (
    <div className="h-full flex flex-col border-l border-gray-800">
      <div className="flex border-b border-gray-800 bg-[#252526] overflow-x-auto">
        {CONTEXT_TABS.map((tab) => (
          <TabButton
            key={tab.id}
            active={contextTab === tab.id}
            onClick={() => setContextTab(tab.id)}
          >
            {tab.icon} {tab.label}
          </TabButton>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <Suspense fallback={<PanelLoader />}>
          {contextTab === "references" && <ReferencePanel />}
          {contextTab === "reviews" && <ReviewPanel />}
          {contextTab === "outline" && <OutlinePanel />}
          {contextTab === "notes" && <NotesPanel />}
          {contextTab === "distillation" && <DistillationTab />}
        </Suspense>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Research Empty State / Quick Start (inline fallback)                */
/* ------------------------------------------------------------------ */

function ResearchEmptyStateFallback() {
  const quickStarts = [
    {
      icon: <Search size={20} />,
      title: "New Literature Review",
      description: "Provide a topic → search, read, synthesize, write",
    },
    {
      icon: <FileText size={20} />,
      title: "New Paper",
      description: "From topic/abstract → outline, write, review",
    },
    {
      icon: <Eye size={20} />,
      title: "Review This Paper",
      description: "Upload PDF → structured review",
    },
    {
      icon: <Flame size={20} />,
      title: "Learn 100 Papers",
      description: "Long-running domain learning",
    },
    {
      icon: <GitCompare size={20} />,
      title: "Compare Papers",
      description: "Upload 2+ PDFs → comparison matrix",
    },
  ];

  return (
    <div className="h-full flex items-center justify-center">
      <div className="max-w-lg text-center px-4">
        <GraduationCap
          size={40}
          className="text-purple-400 mx-auto mb-4"
        />
        <h2 className="text-xl font-semibold text-gray-200 mb-2">
          Research Workspace
        </h2>
        <p className="text-sm text-gray-400 mb-6">
          Choose a starting point or drag a PDF to begin
        </p>
        <div className="grid grid-cols-2 gap-3">
          {quickStarts.map((qs, i) => (
            <button
              key={i}
              className="flex items-start gap-3 p-3 bg-gray-800/50 border border-gray-700/50 rounded-lg hover:border-purple-500/30 hover:bg-purple-900/10 text-left transition-colors"
            >
              <span className="text-purple-400 mt-0.5">{qs.icon}</span>
              <div>
                <p className="text-sm text-gray-200 font-medium">
                  {qs.title}
                </p>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  {qs.description}
                </p>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Furnace Panel (recipe definition + ingredient management)          */
/* ------------------------------------------------------------------ */

const FURNACE_SESSION_STATUS_COLORS: Record<TrainingSession["status"], string> = {
  idle: "bg-gray-600",
  running: "bg-blue-500",
  paused: "bg-yellow-500",
  completed: "bg-green-500",
  failed: "bg-red-500",
};

function FurnacePanel() {
  const trainingSessions = useResearchStore((s) => s.trainingSessions);
  const addTrainingSession = useResearchStore((s) => s.addTrainingSession);
  const wsId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const wsResearchConfig = useWorkspaceStore((s) => {
    const ws = s.workspaces.find((w) => w.id === s.activeWorkspaceId);
    return ws?.researchConfig;
  });
  const updateWorkspace = useWorkspaceStore((s) => s.updateWorkspace);
  const pdfRoots = useSettingsStore((s) => s.researchPdfRoots);
  const noteRoots = useSettingsStore((s) => s.researchNoteRoots);
  const wsPdfRoots = wsResearchConfig?.pdfRoots;
  const wsNoteRoots = wsResearchConfig?.noteRoots;
  const corpusTopic = wsResearchConfig?.corpusTopic ?? "";

  const effectivePdfRoots = wsPdfRoots && wsPdfRoots.length > 0 ? wsPdfRoots : pdfRoots;
  const effectiveNoteRoots = wsNoteRoots && wsNoteRoots.length > 0 ? wsNoteRoots : noteRoots;

  const [topic, setTopic] = useState(corpusTopic);
  const [targetPapers, setTargetPapers] = useState(100);

  const handleStartSession = () => {
    if (!topic.trim()) return;
    if (wsId) {
      updateWorkspace(wsId, {
        researchConfig: { ...wsResearchConfig, corpusTopic: topic.trim() },
      });
    }
    addTrainingSession({
      name: topic.trim(),
      topic: topic.trim(),
      status: "idle",
      targetPapers,
      processedPapers: 0,
    });
  };

  return (
    <div className="h-full flex flex-col overflow-y-auto">
      <div className="max-w-3xl mx-auto w-full px-8 py-8 space-y-8">
        {/* Header */}
        <div>
          <div className="flex items-center gap-3 mb-2">
            <Flame size={24} className="text-orange-400" />
            <h2 className="text-lg font-semibold text-gray-200">Furnace</h2>
          </div>
          <p className="text-sm text-gray-500">
            Define a recipe, specify ingredients (papers to learn from), and run
            the distillation pipeline.
          </p>
        </div>

        {/* Recipe Definition */}
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <SquarePen size={14} /> Recipe Definition
          </h3>
          <div className="space-y-2">
            <label className="block text-[11px] text-gray-500">
              Domain / Topic
            </label>
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. Supply Chain Resilience, Network Economics, ..."
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-purple-500/50"
            />
          </div>
          <div className="flex gap-4">
            <div className="flex-1 space-y-1">
              <label className="block text-[11px] text-gray-500">
                Target Papers
              </label>
              <input
                type="number"
                min={1}
                max={500}
                value={targetPapers}
                onChange={(e) => setTargetPapers(Number(e.target.value) || 100)}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-purple-500/50"
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={handleStartSession}
                disabled={!topic.trim()}
                className="flex items-center gap-2 px-4 py-2 bg-orange-600/80 hover:bg-orange-600 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded-lg transition-colors"
              >
                <Play size={14} /> Start Session
              </button>
            </div>
          </div>
        </section>

        {/* Ingredients (configured roots) */}
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Package size={14} /> Ingredients (Paper & Note Roots)
          </h3>
          {effectivePdfRoots.length === 0 && effectiveNoteRoots.length === 0 ? (
            <p className="text-[11px] text-gray-600 italic">
              No roots configured. Set PDF and note roots in Settings or
              workspace config to specify where papers live.
            </p>
          ) : (
            <div className="space-y-1.5">
              {effectivePdfRoots.map((root) => (
                <div
                  key={root}
                  className="flex items-center gap-2 px-3 py-1.5 bg-gray-800/50 rounded-lg text-[11px] text-gray-400"
                  title={root}
                >
                  <FileText size={12} className="text-blue-400 shrink-0" />
                  <span className="truncate flex-1">{root}</span>
                  <span className="text-[9px] text-gray-600 bg-gray-800 px-1.5 rounded">
                    PDFs
                  </span>
                </div>
              ))}
              {effectiveNoteRoots.map((root) => (
                <div
                  key={root}
                  className="flex items-center gap-2 px-3 py-1.5 bg-gray-800/50 rounded-lg text-[11px] text-gray-400"
                  title={root}
                >
                  <StickyNote size={12} className="text-green-400 shrink-0" />
                  <span className="truncate flex-1">{root}</span>
                  <span className="text-[9px] text-gray-600 bg-gray-800 px-1.5 rounded">
                    Notes
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Training Sessions */}
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <FlaskConical size={14} /> Training Sessions
          </h3>
          {trainingSessions.length === 0 ? (
            <p className="text-[11px] text-gray-600 italic">
              No sessions yet. Define a topic above and start a session.
            </p>
          ) : (
            <div className="space-y-2">
              {trainingSessions.map((sess) => (
                <FurnaceSessionCard key={sess.id} session={sess} />
              ))}
            </div>
          )}
        </section>

        {/* Distillation Pipeline */}
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Gauge size={14} /> Distillation Pipeline
          </h3>
          <div className="grid grid-cols-5 gap-2">
            {["Normalize", "Extract", "Aggregate", "Infer Taste", "Project"].map(
              (step, i) => (
                <div
                  key={step}
                  className="flex flex-col items-center gap-1 p-3 bg-gray-800/40 rounded-lg border border-gray-700/50"
                >
                  <span className="text-[9px] text-gray-600 font-medium">
                    {i + 1}
                  </span>
                  <span className="text-[10px] text-gray-400 text-center">
                    {step}
                  </span>
                </div>
              ),
            )}
          </div>
          <p className="text-[10px] text-gray-600">
            Five-pass furnace loop: Normalize metadata, Extract facts &
            methods, Aggregate cross-paper, Infer taste & associations,
            Project recipe.md + evaluate.
          </p>
        </section>
      </div>
    </div>
  );
}

function FurnaceSessionCard({ session }: { session: TrainingSession }) {
  const pct =
    session.targetPapers > 0
      ? Math.round((session.processedPapers / session.targetPapers) * 100)
      : 0;

  return (
    <div className="p-3 bg-gray-800/50 border border-gray-700/50 rounded-lg">
      <div className="flex items-center gap-2 mb-2">
        <span
          className={`w-2 h-2 rounded-full ${FURNACE_SESSION_STATUS_COLORS[session.status]}`}
        />
        <span className="text-sm text-gray-200 font-medium flex-1 truncate">
          {session.name}
        </span>
        <span className="text-[10px] text-gray-500 capitalize">
          {session.status}
        </span>
      </div>
      <div className="flex items-center gap-3 text-[10px] text-gray-500 mb-1.5">
        <span>
          {session.processedPapers}/{session.targetPapers} papers
        </span>
        <span>{pct}%</span>
        {session.extractedPatterns !== undefined && (
          <span>{session.extractedPatterns} patterns</span>
        )}
        {session.extractedTerms !== undefined && (
          <span>{session.extractedTerms} terms</span>
        )}
      </div>
      <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-orange-500/70 rounded-full transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Keyboard shortcuts hook                                            */
/* ------------------------------------------------------------------ */

function useResearchShortcuts() {
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);
  const primaryTab = useResearchStore((s) => s.primaryTab);
  const setContextTab = useResearchStore((s) => s.setContextTab);
  const contextTab = useResearchStore((s) => s.contextTab);
  const toggleContextPanel = useResearchStore((s) => s.toggleContextPanel);
  const toggleTerminal = useCodeStore((s) => s.toggleTerminal);

  const handler = useCallback(
    (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;

      if (meta && e.key === "e" && !e.shiftKey) {
        e.preventDefault();
        setPrimaryTab(primaryTab === "editor" ? "reader" : "editor");
        return;
      }
      if (meta && e.shiftKey && e.key === "r") {
        e.preventDefault();
        setContextTab(
          contextTab === "references" ? "notes" : "references",
        );
        useResearchStore.getState().setShowContextPanel(true);
        return;
      }
      if (meta && e.shiftKey && e.key === "o") {
        e.preventDefault();
        setContextTab(
          contextTab === "outline" ? "notes" : "outline",
        );
        useResearchStore.getState().setShowContextPanel(true);
        return;
      }
      if (meta && e.key === "i" && !e.shiftKey) {
        e.preventDefault();
        toggleContextPanel();
        return;
      }
      if (meta && e.key === "`") {
        e.preventDefault();
        toggleTerminal();
        return;
      }
    },
    [setPrimaryTab, primaryTab, setContextTab, contextTab, toggleContextPanel, toggleTerminal],
  );

  useEffect(() => {
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [handler]);
}

/* ------------------------------------------------------------------ */
/*  Progressive disclosure: auto-show furnace on active training       */
/* ------------------------------------------------------------------ */

function useAutoShowFurnace() {
  const trainingSessions = useResearchStore((s) => s.trainingSessions);
  const pipeline = useResearchStore((s) => s.pipeline);
  const setActiveRailSection = useResearchStore((s) => s.setActiveRailSection);

  const hasActiveTraining = trainingSessions.some(
    (s) => s.status === "running" || s.status === "paused",
  );
  const hasActivePipeline = pipeline.some(
    (s) => s.status === "active" || s.status === "completed",
  );

  useEffect(() => {
    if (hasActiveTraining) {
      setActiveRailSection("training");
    }
  }, [hasActiveTraining, setActiveRailSection]);

  return { pipelineActive: hasActivePipeline };
}

/* ------------------------------------------------------------------ */
/*  Main layout                                                        */
/* ------------------------------------------------------------------ */

export default function ResearchMode() {
  useResearchEvents();
  useResearchShortcuts();
  const { pipelineActive } = useAutoShowFurnace();

  const showPipeline = useResearchStore((s) => s.showPipeline);
  const showContextPanel = useResearchStore((s) => s.showContextPanel);
  const togglePipeline = useResearchStore((s) => s.togglePipeline);
  const toggleContextPanel = useResearchStore((s) => s.toggleContextPanel);
  const showTerminal = useCodeStore((s) => s.showTerminal);
  const toggleTerminal = useCodeStore((s) => s.toggleTerminal);

  return (
    <div className="h-full flex bg-[#1e1e1e] text-gray-200">
      {/* Left Rail: Function-first nav + Pipeline */}
      <div className="w-56 border-r border-gray-800 flex flex-col bg-[#1e1e1e] shrink-0">
        <FunctionRail />

        {/* Toggle bar: pipeline + panels */}
        <div className="border-t border-gray-800 px-2 py-1 flex items-center gap-1 shrink-0">
          {pipelineActive && (
            <button
              onClick={togglePipeline}
              title={showPipeline ? "Hide pipeline" : "Show pipeline"}
              className="text-[10px] text-gray-500 hover:text-gray-300 flex items-center gap-1 transition-colors"
            >
              <Gauge size={11} />
              {showPipeline ? "Hide" : "Show"} Pipeline
            </button>
          )}
          <span className="flex-1" />
          <button
            onClick={toggleContextPanel}
            title={showContextPanel ? "Hide right drawer" : "Show right drawer"}
            className="text-gray-500 hover:text-gray-300 transition-colors"
          >
            {showContextPanel ? (
              <PanelRightClose size={13} />
            ) : (
              <PanelRightOpen size={13} />
            )}
          </button>
          <button
            onClick={toggleTerminal}
            title={showTerminal ? "Hide terminal (⌘`)" : "Show terminal (⌘`)"}
            className="text-gray-500 hover:text-gray-300 transition-colors"
          >
            {showTerminal ? (
              <PanelBottomClose size={13} />
            ) : (
              <PanelBottomOpen size={13} />
            )}
          </button>
        </div>

        {/* Pipeline progress — only rendered when pipeline is active AND toggled on */}
        {pipelineActive && showPipeline && <PipelineProgress />}
      </div>

      {/* Main area: Desk (center) + Right Drawer (optional) | Terminal (optional) */}
      <div className="flex-1 min-w-0 flex flex-col">
        <Allotment vertical>
          <Allotment.Pane minSize={200}>
            <Allotment>
              <Allotment.Pane minSize={300}>
                <PrimaryPanel />
              </Allotment.Pane>
              {showContextPanel && (
                <Allotment.Pane preferredSize={320} minSize={200}>
                  <ContextPanel />
                </Allotment.Pane>
              )}
            </Allotment>
          </Allotment.Pane>

          {showTerminal && (
            <Allotment.Pane preferredSize={200} minSize={100}>
              <div className="h-full flex flex-col border-t border-gray-800">
                <div className="flex items-center bg-[#252526] border-b border-gray-800 px-2 py-0.5 shrink-0">
                  <span className="text-[10px] text-gray-400 flex items-center gap-1.5">
                    <TerminalIcon size={11} /> Terminal
                  </span>
                  <span className="flex-1" />
                  <button
                    onClick={toggleTerminal}
                    className="text-gray-500 hover:text-gray-300 p-0.5 transition-colors"
                    title="Close terminal"
                  >
                    <X size={12} />
                  </button>
                </div>
                <div className="flex-1 min-h-0">
                  <TerminalPanel />
                </div>
              </div>
            </Allotment.Pane>
          )}
        </Allotment>
      </div>
    </div>
  );
}
