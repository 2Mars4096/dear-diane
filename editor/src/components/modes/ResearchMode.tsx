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
  useRef,
  lazy,
  Suspense,
  type KeyboardEvent as ReactKeyboardEvent,
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
  Trash2,
  Settings2,
  FolderOpen,
  Clipboard,
  FlaskConical,
  Pause,
  Terminal as TerminalIcon,
  Package,
  Play,
  RotateCcw,
  Sparkles,
  SquarePen,
} from "lucide-react";
import {
  useResearchStore,
  type PipelineStage,
  type PipelineStageDetails,
  type PipelinePreset,
  type TrainingSession,
} from "../../store/useResearchStore";
import {
  furnaceCreateSession,
  furnaceAddSources,
  furnaceStartSession,
  furnaceConnectSSE,
  furnaceListSessions,
  furnacePauseSession,
  furnaceResumeSession,
  furnaceCancelSession,
  furnaceDeleteSession,
  furnaceUpdateSessionTags,
} from "../../lib/api";
import { handleFurnaceSSEEvent } from "../../lib/researchEventRouter";
import { parseFurnaceSources, splitSourceTextBlock } from "../../lib/furnaceSources";
import { useSettingsStore } from "../../store/useSettingsStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import { useCodeStore } from "../../store/useCodeStore";
import { useAppStore } from "../../store/useAppStore";
import DomainProfileSelector, {
  getActiveProfile,
} from "../research/DomainProfile";
import TerminalPanel from "../code/TerminalPanel";
import ModeChatSidebar from "../shared/ModeChatSidebar";

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
          ? "border-b-2 border-purple-500 bg-purple-50 text-purple-700 dark:bg-[#1e1e1e] dark:text-gray-200"
          : "border-b-2 border-transparent text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-transparent dark:hover:text-gray-300"
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
        className="flex w-full items-center gap-2 px-3 py-1.5 text-[11px] font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/60 dark:hover:text-gray-200"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {icon}
        <span className="flex-1 text-left">{label}</span>
        {count > 0 && (
          <span className="rounded-full bg-gray-100 px-1.5 text-[9px] text-gray-500 dark:bg-gray-800 dark:text-gray-600">
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
          ? "bg-purple-50 border-l-2 border-purple-500 dark:bg-purple-900/20"
          : "hover:bg-gray-100 border-l-2 border-transparent dark:hover:bg-gray-800/40"
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
          <p className="flex-1 truncate text-[11px] text-gray-800 dark:text-gray-300">{label}</p>
          {badge && (
            <span className="text-[8px] text-amber-400 bg-amber-400/10 px-1 rounded shrink-0">
              {badge}
            </span>
          )}
        </div>
        <p className="truncate text-[9px] text-gray-500 dark:text-gray-600">{subtitle}</p>
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
        <div className="px-3 py-1.5 border-b border-gray-200 dark:border-gray-800/50">
          {effectiveRoots.pdf.map((root) => (
            <div
              key={root}
              className="flex items-center gap-1 py-0.5 text-[9px] text-gray-500 truncate dark:text-gray-600"
              title={root}
            >
              <FileText size={9} className="shrink-0 text-gray-500 dark:text-gray-700" />
              {root.split("/").pop()}
            </div>
          ))}
          {effectiveRoots.note.map((root) => (
            <div
              key={root}
              className="flex items-center gap-1 py-0.5 text-[9px] text-gray-500 truncate dark:text-gray-600"
              title={root}
            >
              <StickyNote size={9} className="shrink-0 text-gray-500 dark:text-gray-700" />
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
          className="w-full px-4 py-1 pl-7 text-left text-[11px] text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800/40"
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
              className="w-full truncate px-4 py-0.5 text-left text-[11px] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
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
          className="w-full px-4 py-1 pl-7 text-left text-[11px] text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800/40"
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
          className="w-full px-4 py-1 pl-7 text-left text-[11px] text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800/40"
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
              className="flex items-center gap-1 text-[10px] text-purple-500 hover:text-purple-600 dark:text-purple-400 dark:hover:text-purple-300"
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
        className="w-full px-4 py-1 pl-7 text-left text-[11px] text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800/40"
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
      className="group w-full px-4 py-1.5 pl-7 text-left transition-colors hover:bg-gray-100 dark:hover:bg-gray-800/40"
    >
      <div className="flex items-center gap-2">
        <span className={statusColors[session.status]}>
          {statusIcons[session.status]}
        </span>
        <span className="flex-1 truncate text-[11px] text-gray-800 dark:text-gray-300">
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
        <div className="mt-0.5 h-0.5 rounded-full bg-gray-200 overflow-hidden dark:bg-gray-800">
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
      <div className="flex border-b border-gray-200 dark:border-gray-800">
        {RAIL_SECTIONS.map((sec) => (
          <button
            key={sec.id}
            onClick={() => setActiveSection(sec.id)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[10px] font-medium transition-colors ${
              activeSection === sec.id
                ? "border-b-2 border-purple-500 bg-purple-50 text-purple-700 dark:bg-[#1e1e1e] dark:text-gray-200"
                : "border-b-2 border-transparent text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-transparent dark:hover:text-gray-300"
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
      <div className="border-t border-gray-200 shrink-0 dark:border-gray-800">
        <button
          onClick={() => setShowProfileSelector((v) => !v)}
          className="flex w-full items-center gap-2 px-3 py-2 text-[11px] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/60 dark:hover:text-gray-200"
        >
          <Settings2 size={12} />
          <span className="flex-1 text-left truncate">
            {activeProfile.name}
          </span>
          <span className="rounded-full bg-gray-100 px-1.5 text-[9px] text-gray-500 dark:bg-gray-800 dark:text-gray-600">
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
    <div className="border-t border-gray-200 flex-shrink-0 dark:border-gray-800">
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
                ? "border border-purple-500/30 bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300"
                : "border border-transparent text-gray-600 hover:border-gray-300 hover:text-gray-800 dark:hover:border-gray-700 dark:hover:text-gray-400"
            }`}
          >
            {preset === "research" ? "Research" : "Distillation"}
          </button>
        ))}
      </div>

      {/* Progress bar */}
      <div className="px-3 pb-1.5">
        <div className="h-1 rounded-full bg-gray-200 overflow-hidden dark:bg-gray-800">
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
                className="flex-1 flex items-center gap-2 py-1 px-1 rounded text-left transition-colors hover:bg-gray-100 dark:hover:bg-gray-800/50"
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
              className="flex-1 rounded border border-gray-300 bg-white px-1.5 py-0.5 text-[10px] text-gray-800 placeholder-gray-500 outline-none focus:border-purple-500/50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:placeholder-gray-600"
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
        <button className="flex-1 rounded bg-gray-100 py-1 text-[10px] text-gray-600 transition-colors hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-700">
          Pause
        </button>
        <button className="flex-1 rounded bg-gray-100 py-1 text-[10px] text-gray-600 transition-colors hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-700">
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
      <div className="flex border-b border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-[#252526]">
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
    <div className="h-full flex flex-col border-l border-gray-200 dark:border-gray-800">
      <div className="flex border-b border-gray-200 bg-gray-50 overflow-x-auto dark:border-gray-800 dark:bg-[#252526]">
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

type TrainingSessionFamilyGroup = {
  key: string;
  displayName: string;
  topic: string;
  sessions: TrainingSession[];
  visibleSessions: TrainingSession[];
  totalCount: number;
  variantCount: number;
  latestAt: number;
  totalCostUsd: number;
};

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

function normalizeTrainingSessionTag(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, " ").slice(0, 32);
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

function FurnacePanel() {
  const trainingSessions = useResearchStore((s) => s.trainingSessions);
  const addTrainingSession = useResearchStore((s) => s.addTrainingSession);
  const updateTrainingSession = useResearchStore((s) => s.updateTrainingSession);
  const wsId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const wsResearchConfig = useWorkspaceStore((s) => {
    const ws = s.workspaces.find((w) => w.id === s.activeWorkspaceId);
    return ws?.researchConfig;
  });
  const updateWorkspace = useWorkspaceStore((s) => s.updateWorkspace);
  const pdfRoots = useSettingsStore((s) => s.researchPdfRoots);
  const noteRoots = useSettingsStore((s) => s.researchNoteRoots);
  const papers = useResearchStore((s) => s.papers);
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
  const sseRefs = useRef<Map<string, EventSource>>(new Map());

  useEffect(() => {
    if (topic && !sessionName) setSessionName(topic.trim());
  }, [topic]);

  const upsertSessionSummary = useCallback(
    (summary: {
      session_id: string;
      recipe_id: string;
      name: string;
      topic: string;
      status: string;
      current_phase: string;
      source_count: number;
      processed_count: number;
      total_cost_usd: number;
      variant_label: string;
      parent_session_id: string;
      family_session_id: string;
      tags: string[];
      updated_at: number;
    }) => {
      const normalizedName = (summary.name || "").trim().toLowerCase();
      const normalizedTopic = (summary.topic || "").trim().toLowerCase();
      const existing = useResearchStore
        .getState()
        .trainingSessions.find((s) => {
          if (s.sessionId === summary.session_id) return true;
          // Backfill legacy local sessions that predate sessionId persistence.
          if (s.sessionId) return false;
          const nameMatch = (s.name || "").trim().toLowerCase() === normalizedName;
          const topicMatch = (s.topic || "").trim().toLowerCase() === normalizedTopic;
          return nameMatch && topicMatch;
        });
      if (!existing) {
        addTrainingSession({
          sessionId: summary.session_id,
          recipeId: summary.recipe_id || "",
          parentSessionId: summary.parent_session_id || undefined,
          familySessionId: summary.family_session_id || summary.session_id,
          variantLabel: summary.variant_label || undefined,
          tags: summary.tags ?? [],
          name: summary.name || summary.topic || "Untitled Session",
          topic: summary.topic || "",
          status: summary.status === "active" ? "running" : (summary.status as TrainingSession["status"]),
          targetPapers: Math.max(0, summary.source_count || 0),
          processedPapers: Math.max(0, summary.processed_count || 0),
          currentPhase: summary.current_phase,
          sourceCount: summary.source_count,
          totalCostUsd: summary.total_cost_usd,
          lastActivityAt: summary.updated_at ? summary.updated_at * 1000 : Date.now(),
          statusMessage: summary.current_phase
            ? `Current phase: ${summary.current_phase}`
            : undefined,
        });
        return;
      }
      updateTrainingSession(existing.id, {
        sessionId: summary.session_id,
        recipeId: summary.recipe_id || existing.recipeId,
        parentSessionId: summary.parent_session_id || existing.parentSessionId,
        familySessionId: summary.family_session_id || existing.familySessionId,
        variantLabel: summary.variant_label || existing.variantLabel,
        tags: summary.tags ?? existing.tags,
        name: summary.name || existing.name,
        topic: summary.topic || existing.topic,
        status: summary.status === "active" ? "running" : (summary.status as TrainingSession["status"]),
        targetPapers: summary.source_count || existing.targetPapers,
        processedPapers: summary.processed_count ?? existing.processedPapers,
        currentPhase: summary.current_phase || existing.currentPhase,
        sourceCount: summary.source_count ?? existing.sourceCount,
        totalCostUsd: summary.total_cost_usd ?? existing.totalCostUsd,
        lastActivityAt: summary.updated_at ? summary.updated_at * 1000 : Date.now(),
      });
    },
    [addTrainingSession, updateTrainingSession],
  );

  useEffect(() => {
    let cancelled = false;
    const syncSessions = async () => {
      try {
        const data = await furnaceListSessions();
        if (cancelled) return;
        for (const session of data.sessions) {
          upsertSessionSummary({
            session_id: session.session_id,
            recipe_id: session.recipe_id,
            name: session.name,
            topic: session.topic,
            status: session.status,
            current_phase: session.current_phase,
            source_count: session.source_count,
            processed_count: session.processed_count,
            total_cost_usd: session.total_cost_usd,
            variant_label: session.variant_label,
            parent_session_id: session.parent_session_id,
            family_session_id: session.family_session_id,
            tags: session.tags ?? [],
            updated_at: session.updated_at,
          });
        }
      } catch {
        // Ignore transient refresh errors.
      }
    };

    void syncSessions();
    const interval = window.setInterval(syncSessions, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [upsertSessionSummary]);

  useEffect(() => {
    const activeSessionIds = new Set(
      trainingSessions
        .filter((s) => (s.status === "running" || s.status === "paused") && s.sessionId)
        .map((s) => s.sessionId as string),
    );

    for (const [sid, es] of sseRefs.current) {
      if (!activeSessionIds.has(sid)) {
        es.close();
        sseRefs.current.delete(sid);
      }
    }

    for (const sid of activeSessionIds) {
      if (sseRefs.current.has(sid)) continue;
      const es = furnaceConnectSSE(
        sid,
        (ev) => handleFurnaceSSEEvent(ev as Record<string, unknown>),
        () => {
          const activeNow = useResearchStore
            .getState()
            .trainingSessions.some(
              (s) => s.sessionId === sid && (s.status === "running" || s.status === "paused"),
            );
          if (activeNow) {
            window.setTimeout(() => {
              if (sseRefs.current.has(sid)) return;
              const retry = furnaceConnectSSE(
                sid,
                (ev) => handleFurnaceSSEEvent(ev as Record<string, unknown>),
                () => {
                  sseRefs.current.delete(sid);
                },
              );
              sseRefs.current.set(sid, retry);
            }, 2000);
          }
          sseRefs.current.delete(sid);
        },
      );
      sseRefs.current.set(sid, es);
    }
  }, [trainingSessions]);

  const resolveSessionId = useCallback(
    async (seedId: string | undefined, name: string, topicValue: string) => {
      if (seedId) return seedId;
      const listed = await furnaceListSessions();
      const normalizedName = (name || "").trim().toLowerCase();
      const normalizedTopic = (topicValue || "").trim().toLowerCase();
      const match = listed.sessions.find((s) => {
        if (s.name.trim().toLowerCase() === normalizedName) return true;
        return s.topic.trim().toLowerCase() === normalizedTopic;
      });
      return match?.session_id;
    },
    [],
  );

  const connectSessionSSE = useCallback((sid: string) => {
    if (sseRefs.current.has(sid)) return;
    const es = furnaceConnectSSE(
      sid,
      (ev) => handleFurnaceSSEEvent(ev as Record<string, unknown>),
      () => { sseRefs.current.delete(sid); },
    );
    sseRefs.current.set(sid, es);
  }, []);

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
          ...papers.filter((p) => p.id).map((p) => p.id),
        ]),
      );
      const pdfPaths = Array.from(
        new Set([
          ...manual.pdf_paths,
          ...papers.filter((p) => p.filePath).map((p) => p.filePath!),
        ]),
      );
      const urls = Array.from(new Set(manual.urls));
      const hasAny = sourceIds.length > 0 || pdfPaths.length > 0 || urls.length > 0;

      if (draftAction.kind === "continue") {
        const sid = await resolveSessionId(draftAction.sessionId, draftAction.name, draftAction.topic);
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
          connectSessionSSE(sid);
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
          .trainingSessions.find((s) => s.sessionId === sid)?.id;
        if (localId) {
          updateTrainingSession(localId, { status: "running" });
        }
        connectSessionSSE(sid);
        setDraftAction({ kind: "new" });
        return;
      }

      let variantParentSessionId: string | undefined;
      if (draftAction.kind === "variant") {
        variantParentSessionId = await resolveSessionId(
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
        variant_label: draftAction.kind === "variant"
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
          (sessionMetadata.parent_session_id as string | undefined) ??
          variantParentSessionId,
        familySessionId:
          (sessionMetadata.family_session_id as string | undefined) ??
          sid,
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
        .trainingSessions.find((s) => s.sessionId === sid)?.id;
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
      connectSessionSSE(sid);
      if (draftAction.kind === "variant") {
        setDraftAction({ kind: "new" });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }, [
    connectSessionSSE,
    draftAction,
    resolveSessionId,
    topic,
    sessionName,
    targetPapers,
    sourceText,
    wsId,
    wsResearchConfig,
    papers,
    addTrainingSession,
    updateTrainingSession,
    updateWorkspace,
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
      running: trainingSessions.filter((s) => s.status === "running").length,
      paused: trainingSessions.filter((s) => s.status === "paused").length,
      completed: trainingSessions.filter((s) => s.status === "completed").length,
      failed: trainingSessions.filter((s) => s.status === "failed").length,
      variant: trainingSessions.filter((s) => isVariantTrainingSession(s)).length,
      base: trainingSessions.filter((s) => !isVariantTrainingSession(s)).length,
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
        byTag.set(tag, { tag, count: sessionTagCounts.find((entry) => entry.tag === tag)?.count ?? 0 });
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

  const visibleSessionFamilies = useMemo<TrainingSessionFamilyGroup[]>(() => {
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
          members.find((s) => (s.sessionId || s.id) === key) ??
          members.find((s) => !s.parentSessionId) ??
          members[0];
        const orderedMembers = [
          root,
          ...members
            .filter((s) => s !== root)
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
        const filteredByTag = orderedMembers.filter((s) =>
          matchesTrainingSessionFilter(s, sessionFilter),
        );
        const filteredByUserTags = filteredByTag.filter(
          (s) =>
            activeTagFilters.length === 0 ||
            activeTagFilters.some((tag) => sessionHasTag(s, tag)),
        );
        const visibleMembers =
          !normalizedQuery || familySearchText.includes(normalizedQuery)
            ? filteredByUserTags
            : filteredByUserTags.filter((s) =>
                buildTrainingSessionSearchText(s).includes(normalizedQuery),
              );

        const latestAt = Math.max(
          ...orderedMembers.map((s) => s.lastActivityAt ?? s.startedAt ?? 0),
        );
        const totalCostUsd = orderedMembers.reduce(
          (sum, s) => sum + (s.totalCostUsd ?? 0),
          0,
        );

        return {
          key,
          displayName: root?.name || root?.topic || "Session family",
          topic: root?.topic || "",
          sessions: orderedMembers,
          visibleSessions: visibleMembers,
          totalCount: orderedMembers.length,
          variantCount: orderedMembers.filter((s) => isVariantTrainingSession(s)).length,
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
              ...a.visibleSessions.map((s) => getTrainingSessionProgress(s)),
            );
            const bProgress = Math.max(
              ...b.visibleSessions.map((s) => getTrainingSessionProgress(s)),
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
            (s) => s.status === "running" || s.status === "paused",
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

  useEffect(() => {
    return () => {
      for (const [, es] of sseRefs.current) es.close();
      sseRefs.current.clear();
    };
  }, []);

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
          {draftAction.kind !== "new" && (
            <div className="flex items-center justify-between rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-2">
              <div className="text-[11px] text-blue-200">
                {draftAction.kind === "continue"
                  ? `Build on existing session: ${draftAction.name}`
                  : `Create variant from: ${draftAction.baseName}`}
              </div>
              <button
                onClick={() => {
                  setDraftAction({ kind: "new" });
                  setError(null);
                }}
                className="text-[10px] text-blue-300 hover:text-blue-100"
              >
                Clear
              </button>
            </div>
          )}
          {error && (
            <p className="text-xs text-red-400 bg-red-900/20 px-3 py-2 rounded">
              {error}
            </p>
          )}
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
          <div className="space-y-2">
            <label className="block text-[11px] text-gray-500">
              Session Name
            </label>
            <input
              value={sessionName}
              onChange={(e) => setSessionName(e.target.value)}
              placeholder="Pre-filled from topic"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-purple-500/50"
            />
          </div>
          <div className="space-y-2">
            <label className="block text-[11px] text-gray-500">
              Sources (optional, one per line: PDF path / URL / source id)
            </label>
            <textarea
              value={sourceText}
              onChange={(e) => setSourceText(e.target.value)}
              placeholder={"/Users/.../paper.pdf\nhttps://arxiv.org/abs/...."}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 outline-none focus:border-purple-500/50 h-20 resize-y"
            />
            <p className="text-[10px] text-gray-600">
              Tip: you can paste multiple PDF paths/URLs. We will also include papers already in your library list.
            </p>
            <div className="rounded-lg border border-gray-700/60 bg-gray-900/40 px-3 py-2">
              <p className="text-[10px] text-gray-500 mb-1">Quick run examples</p>
              <div className="space-y-1 text-[10px] text-gray-400 font-mono">
                <div>/Users/.../elliott2022supply.pdf</div>
                <div>https://arxiv.org/abs/2401.12345</div>
                <div>supply-network-fragility</div>
              </div>
            </div>
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
                onClick={() => void handleRunSession()}
                disabled={!topic.trim() || starting}
                className="flex items-center gap-2 px-4 py-2 bg-orange-600/80 hover:bg-orange-600 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded-lg transition-colors"
              >
                {starting ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Play size={14} />
                )}{" "}
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
            <div className="space-y-3">
              <div className="rounded-lg border border-gray-700/50 bg-gray-900/30 px-3 py-3 space-y-2">
                <div className="flex flex-col gap-2 md:flex-row md:items-center">
                  <div className="relative flex-1">
                    <Search
                      size={13}
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500"
                    />
                    <input
                      value={sessionQuery}
                      onChange={(e) => setSessionQuery(e.target.value)}
                      placeholder="Search pills by name, topic, recipe id, or variant..."
                      className="w-full rounded-lg border border-gray-700 bg-gray-800 pl-8 pr-8 py-2 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-purple-500/50"
                    />
                    {sessionQuery && (
                      <button
                        onClick={() => setSessionQuery("")}
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300 hover:bg-gray-700/50"
                        title="Clear search"
                      >
                        <X size={12} />
                      </button>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase tracking-wide text-gray-500">
                      Sort
                    </span>
                    <select
                      value={sessionSort}
                      onChange={(e) => setSessionSort(e.target.value as SessionSortKey)}
                      className="rounded-lg border border-gray-700 bg-gray-800 px-2 py-2 text-xs text-gray-200 outline-none focus:border-purple-500/50"
                    >
                      {SESSION_SORT_OPTIONS.map((option) => (
                        <option key={option.key} value={option.key}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {SESSION_FILTER_OPTIONS.map((option) => (
                    <button
                      key={option.key}
                      onClick={() => setSessionFilter(option.key)}
                      className={`rounded-full border px-2.5 py-1 text-[10px] transition-colors ${
                        sessionFilter === option.key
                          ? "border-purple-500/40 bg-purple-500/15 text-purple-200"
                          : "border-gray-700 bg-gray-800 text-gray-400 hover:bg-gray-700/60 hover:text-gray-200"
                      }`}
                    >
                      {option.label}{" "}
                      <span className="text-[9px] opacity-80">
                        {sessionFilterCounts[option.key]}
                      </span>
                    </button>
                  ))}
                </div>
                {visibleTagFilters.length > 0 && (
                  <div className="space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] uppercase tracking-wide text-gray-500">
                        Tags
                      </span>
                      {activeTagFilters.length > 0 && (
                        <button
                          onClick={() => setActiveTagFilters([])}
                          className="text-[10px] text-gray-400 hover:text-gray-200"
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
                                ? "border-amber-500/40 bg-amber-500/15 text-amber-200"
                                : "border-gray-700 bg-gray-800 text-gray-400 hover:bg-gray-700/60 hover:text-gray-200"
                            }`}
                          >
                            #{tag}{" "}
                            <span className="text-[9px] opacity-80">{count}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
              {visibleSessionFamilies.length === 0 ? (
                <p className="text-[11px] text-gray-600 italic">
                  No pills match the current search or filter.
                </p>
              ) : (
                <div className="space-y-2">
                  {visibleSessionFamilies.map((group) =>
                    group.totalCount > 1 ? (
                      <FurnaceSessionFamilyGroup
                        key={group.key}
                        group={group}
                        expanded={
                          forceExpandSessionFamilies ||
                          (expandedFamilies[group.key] ??
                            group.sessions.some(
                              (s) =>
                                s.status === "running" || s.status === "paused",
                            ))
                        }
                        onToggle={() => toggleFamilyExpanded(group.key)}
                        onContinue={handlePrepareContinue}
                        onVariant={handlePrepareVariant}
                        knownTags={sessionTagCounts.map(({ tag }) => tag)}
                        activeTagFilters={activeTagFilters}
                        onToggleTagFilter={toggleTagFilter}
                      />
                    ) : (
                      <FurnaceSessionCard
                        key={group.visibleSessions[0].id}
                        session={group.visibleSessions[0]}
                        onContinue={handlePrepareContinue}
                        onVariant={handlePrepareVariant}
                        knownTags={sessionTagCounts.map(({ tag }) => tag)}
                        activeTagFilters={activeTagFilters}
                        onToggleTagFilter={toggleTagFilter}
                      />
                    ),
                  )}
                </div>
              )}
            </div>
          )}
        </section>

        {/* Distillation Pipeline */}
        <section className="space-y-3">
          <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-2">
            <Gauge size={14} /> Distillation Pipeline
          </h3>
          <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-700/50 bg-gray-50 dark:bg-gray-800/30 px-2 py-2">
            {["Normalize", "Extract", "Aggregate", "Infer Taste", "Project"].map(
              (step, i, all) => (
                <div key={step} className="flex items-center gap-1.5">
                  <span className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2.5 py-1 text-[11px] font-medium text-gray-700 dark:text-gray-300">
                    {step}
                  </span>
                  {i < all.length - 1 && (
                    <span className="text-[10px] text-gray-400 dark:text-gray-500">
                      →
                    </span>
                  )}
                </div>
              ),
            )}
          </div>
          <p className="text-[10px] text-gray-600 dark:text-gray-500">
            Five-pass furnace loop: Normalize metadata, Extract facts &
            methods, Aggregate cross-paper, Infer taste & associations,
            Project recipe.md + evaluate.
          </p>
        </section>
      </div>
    </div>
  );
}

function FurnaceSessionFamilyGroup({
  group,
  expanded,
  onToggle,
  onContinue,
  onVariant,
  knownTags,
  activeTagFilters,
  onToggleTagFilter,
}: {
  group: TrainingSessionFamilyGroup;
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
    <div className="rounded-lg border border-gray-700/50 bg-gray-900/20 overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-800/40 transition-colors"
      >
        {expanded ? (
          <ChevronDown size={14} className="text-gray-500 shrink-0" />
        ) : (
          <ChevronRight size={14} className="text-gray-500 shrink-0" />
        )}
        <div className="min-w-0 flex-1">
          <div className="text-sm text-gray-200 font-medium truncate">
            {group.displayName}
          </div>
          <div className="text-[10px] text-gray-500 truncate">
            {group.topic || "Variant family"}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-1.5 text-[10px]">
          <span className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-300">
            {group.totalCount} pills
          </span>
          {group.variantCount > 0 && (
            <span className="rounded-full border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-purple-300">
              {group.variantCount} variants
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
        <div className="border-t border-gray-700/50 px-2 py-2 space-y-2">
          {group.visibleSessions.length < group.totalCount && (
            <div className="px-2 text-[10px] text-gray-500">
              Showing {group.visibleSessions.length} of {group.totalCount} pills
            </div>
          )}
          {group.visibleSessions.map((session) => (
            <FurnaceSessionCard
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

function FurnaceSessionCard({
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
  const updateTrainingSession = useResearchStore((s) => s.updateTrainingSession);
  const removeTrainingSession = useResearchStore((s) => s.removeTrainingSession);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [tagEditorOpen, setTagEditorOpen] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const [tagSaving, setTagSaving] = useState(false);
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
  const actionBusy = loading || deleting;
  const suggestedTags = useMemo(
    () =>
      knownTags
        .filter((tag) => !sessionTags.includes(tag))
        .slice(0, 6),
    [knownTags, sessionTags],
  );

  const resolveBackendSessionId = useCallback(async () => {
    if (session.sessionId) return session.sessionId;
    const listed = await furnaceListSessions();
    const normalizedName = (session.name || "").trim().toLowerCase();
    const normalizedTopic = (session.topic || "").trim().toLowerCase();
    const normalizedVariant = (session.variantLabel || "").trim().toLowerCase();
    const fallback = listed.sessions.find(
      (candidate) => {
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
      },
    );
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

      // If no backend match exists, still allow local cleanup so UI can recover.
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
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[10px]">
        <span className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-300">
          {session.processedPapers}/{totalSources} papers
        </span>
        <span className="rounded-full border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-300">
          {pct}% done
        </span>
        {phaseLabel && (
          <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 text-blue-300 capitalize">
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
          <div className="rounded-lg border border-gray-700 bg-gray-900/40 px-2 py-2 space-y-1.5">
            <div className="flex items-center gap-1.5">
              <input
                value={tagDraft}
                onChange={(e) => setTagDraft(e.target.value)}
                onKeyDown={handleTagKeyDown}
                placeholder="Type tag and press Enter"
                className="flex-1 rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-[11px] text-gray-200 placeholder-gray-500 outline-none focus:border-amber-500/40"
              />
              <button
                onClick={() => void handleAddTag(tagDraft)}
                disabled={
                  !normalizeTrainingSessionTag(tagDraft) || tagSaving || deleting
                }
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
      <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-orange-500/70 rounded-full transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      {session.statusMessage && (
        <p className="mt-2 text-[10px] text-gray-400">
          {session.statusMessage}
        </p>
      )}
      {session.recentEvents && session.recentEvents.length > 0 && (
        <div className="mt-1.5 space-y-1">
          {session.recentEvents.slice(0, 3).map((entry, idx) => (
            <div key={`${entry}-${idx}`} className="text-[10px] text-gray-500 truncate">
              • {entry}
            </div>
          ))}
        </div>
      )}
      {(session.status === "running" ||
        session.status === "paused" ||
        session.status === "failed") && (
        <div className="flex gap-1.5 mt-2">
          {session.status === "running" ? (
            <button
              onClick={() => void handlePause()}
              disabled={actionBusy}
              className="px-2 py-1 text-[10px] bg-gray-700 text-gray-300 rounded hover:bg-gray-600 disabled:opacity-50"
            >
              Pause
            </button>
          ) : session.status === "failed" ? (
            <button
              onClick={() => void handleResume()}
              disabled={actionBusy}
              className="inline-flex items-center gap-1 px-2 py-1 text-[10px] bg-amber-900/30 text-amber-300 rounded hover:bg-amber-900/50 disabled:opacity-50"
              title="Retry this failed session from its saved state"
            >
              <RotateCcw size={11} />
              Retry
            </button>
          ) : (
            <button
              onClick={() => void handleResume()}
              disabled={actionBusy}
              className="px-2 py-1 text-[10px] bg-green-900/30 text-green-400 rounded hover:bg-green-900/50 disabled:opacity-50"
            >
              Resume
            </button>
          )}
          {session.status !== "failed" && (
            <button
              onClick={() => void handleCancel()}
              disabled={actionBusy}
              className="px-2 py-1 text-[10px] bg-red-900/30 text-red-400 rounded hover:bg-red-900/50 disabled:opacity-50"
            >
              Cancel
            </button>
          )}
        </div>
      )}
      <div className="mt-2 flex items-center justify-between">
        <div className="flex gap-1.5">
          {session.status !== "completed" && (
            <button
              onClick={() => onContinue(session)}
              disabled={actionBusy}
              className="inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] text-blue-300 hover:bg-blue-500/10 disabled:opacity-50"
              title="Build on this session by adding new sources"
            >
              <Play size={11} />
              Continue
            </button>
          )}
          <button
            onClick={() => onVariant(session)}
            disabled={actionBusy}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] text-purple-300 hover:bg-purple-500/10 disabled:opacity-50"
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
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] text-gray-500 hover:text-red-400 hover:bg-red-900/20 disabled:opacity-50"
          title="Delete this session"
        >
          {deleting ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
          {deleting ? "Deleting..." : "Delete"}
        </button>
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

  const [showChatSidebar, setShowChatSidebar] = useState(false);
  const showPipeline = useResearchStore((s) => s.showPipeline);
  const showContextPanel = useResearchStore((s) => s.showContextPanel);
  const togglePipeline = useResearchStore((s) => s.togglePipeline);
  const toggleContextPanel = useResearchStore((s) => s.toggleContextPanel);
  const showTerminal = useCodeStore((s) => s.showTerminal);
  const toggleTerminal = useCodeStore((s) => s.toggleTerminal);

  useEffect(() => {
    const handleChatToggle = () => {
      if (useAppStore.getState().activeMode !== "research") return;
      setShowChatSidebar((v) => !v);
    };
    window.addEventListener("app:toggleModeChatSidebar", handleChatToggle);
    return () => {
      window.removeEventListener("app:toggleModeChatSidebar", handleChatToggle);
    };
  }, []);

  return (
    <div className="h-full flex bg-gray-50 text-gray-900 dark:bg-[#1e1e1e] dark:text-gray-200">
      {/* Left Rail: Function-first nav + Pipeline */}
      <div className="w-56 border-r border-gray-200 flex flex-col bg-white shrink-0 dark:border-gray-800 dark:bg-[#1e1e1e]">
        <FunctionRail />

        {/* Toggle bar: pipeline + panels */}
        <div className="border-t border-gray-200 px-2 py-1 flex items-center gap-1 shrink-0 dark:border-gray-800">
          {pipelineActive && (
            <button
              onClick={togglePipeline}
              title={showPipeline ? "Hide pipeline" : "Show pipeline"}
              className="flex items-center gap-1 text-[10px] text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-300"
            >
              <Gauge size={11} />
              {showPipeline ? "Hide" : "Show"} Pipeline
            </button>
          )}
          <span className="flex-1" />
          <button
            onClick={() => setShowChatSidebar((v) => !v)}
            title={showChatSidebar ? "Hide AI chat (⌘J)" : "Show AI chat (⌘J)"}
            className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] font-medium transition-colors ${
              showChatSidebar
                ? "border-blue-500/30 bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300"
                : "border-transparent text-blue-500 hover:bg-blue-50 hover:text-blue-600 dark:text-blue-400 dark:hover:bg-white/5 dark:hover:text-blue-300"
            }`}
          >
            <MessageSquareText size={11} />
            Chat
          </button>
          <button
            onClick={toggleContextPanel}
            title={showContextPanel ? "Hide context drawer (⌘I)" : "Show context drawer (⌘I)"}
            className={`flex items-center gap-1 text-[10px] transition-colors ${showContextPanel ? "text-blue-500 dark:text-blue-400" : "text-gray-500 hover:text-gray-900 dark:hover:text-gray-300"}`}
          >
            {showContextPanel ? <PanelRightClose size={11} /> : <PanelRightOpen size={11} />}
            Context
          </button>
          <button
            onClick={toggleTerminal}
            title={showTerminal ? "Hide terminal (⌘`)" : "Show terminal (⌘`)"}
            className="text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-300"
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

      {/* Main area: Desk (center) + Right Drawer (optional) | Terminal (optional) | Chat sidebar */}
      <div className="flex-1 min-w-0 flex">
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex items-center justify-end gap-2 border-b border-gray-200 bg-gray-50 px-3 py-1.5 shrink-0 dark:border-gray-800 dark:bg-[#252526]">
            <button
              onClick={() => setShowChatSidebar((v) => !v)}
              title={showChatSidebar ? "Hide AI chat (⌘J)" : "Show AI chat (⌘J)"}
              className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                showChatSidebar
                  ? "border-blue-500/30 bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300"
                  : "border-transparent text-blue-500 hover:bg-blue-50 hover:text-blue-600 dark:text-blue-400 dark:hover:bg-white/5 dark:hover:text-blue-300"
              }`}
            >
              <MessageSquareText size={13} />
              <span>AI Chat</span>
              <span className="text-[10px] text-gray-500">⌘J</span>
            </button>
          </div>
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
                <div className="h-full flex flex-col border-t border-gray-200 dark:border-gray-800">
                  <div className="flex items-center bg-gray-50 border-b border-gray-200 px-2 py-0.5 shrink-0 dark:border-gray-800 dark:bg-[#252526]">
                    <span className="text-[10px] text-gray-400 flex items-center gap-1.5">
                      <TerminalIcon size={11} /> Terminal
                    </span>
                    <span className="flex-1" />
                    <button
                      onClick={toggleTerminal}
                      className="p-0.5 text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-300"
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
        {showChatSidebar && (
          <div className="w-[350px] min-w-[250px] max-w-[500px] border-l border-gray-200 shrink-0 dark:border-gray-800">
            <ModeChatSidebar
              mode="research"
              onClose={() => setShowChatSidebar(false)}
              contextProvider={() => {
                const rs = useResearchStore.getState();
                const lines: string[] = ["[Research Context]"];
                if (rs.activePaperId) {
                  const paper = rs.papers.find((p) => p.id === rs.activePaperId);
                  if (paper) {
                    lines.push(`Active paper: ${paper.title}`);
                    if (paper.filePath) lines.push(`Paper path: ${paper.filePath}`);
                    if (paper.authors.length > 0) {
                      lines.push(`Authors: ${paper.authors.join(", ")}`);
                    }
                    if (paper.year) lines.push(`Year: ${paper.year}`);
                  }
                  const annotations = rs.annotations
                    .filter((annotation) => annotation.paperId === rs.activePaperId)
                    .slice(-3);
                  if (annotations.length > 0) {
                    lines.push(
                      "",
                      "[Recent annotations]",
                      ...annotations.map(
                        (annotation) => `- Page ${annotation.page}: ${annotation.text.slice(0, 240)}`,
                      ),
                    );
                  }
                  const figureMentions = Object.values(rs.pageSummaries)
                    .filter((summary) => summary.paperId === rs.activePaperId)
                    .flatMap((summary) =>
                      summary.figures.map(
                        (figure) => `- Page ${summary.page}: ${figure}`,
                      ),
                    )
                    .slice(0, 5);
                  if (figureMentions.length > 0) {
                    lines.push("", "[Figure / table mentions]", ...figureMentions);
                  }
                }
                lines.push(`Rail section: ${rs.activeRailSection}`);
                lines.push(`Primary tab: ${rs.primaryTab}`);
                if (rs.documentContent) {
                  lines.push("", "[Document excerpt]", rs.documentContent.slice(0, 2000));
                }
                return lines.join("\n");
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
