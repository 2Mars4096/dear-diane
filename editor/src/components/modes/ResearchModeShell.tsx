import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import {
  BookMarked,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clipboard,
  Clock,
  Eye,
  FileText,
  Flame,
  FlaskConical,
  FolderOpen,
  Gauge,
  GitCompare,
  GraduationCap,
  List,
  Loader2,
  MessageSquareText,
  Pause,
  PauseCircle,
  PenLine,
  Plus,
  Search,
  Settings2,
  StickyNote,
  X,
  XCircle,
} from "lucide-react";
import {
  useResearchStore,
  type PipelinePreset,
  type PipelineStage,
  type PipelineStageDetails,
  type TrainingSession,
} from "../../store/useResearchStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import DomainProfileSelector, {
  getActiveProfile,
} from "../research/DomainProfile";

const WritingPane = lazy(() => import("../research/WritingPane"));
const SplitPdfReader = lazy(() => import("../research/SplitPdfReader"));
const ReferencePanel = lazy(() => import("../research/ReferencePanel"));
const ReviewPanel = lazy(() => import("../research/ReviewPanel"));
const OutlinePanel = lazy(() => import("../research/OutlinePanel"));
const NotesPanel = lazy(() => import("../research/NotesPanel"));
const DistillationTab = lazy(() => import("../research/DistillationTab"));
const QuickStartPanel = lazy(() => import("../research/QuickStartPanel"));

function PanelLoader() {
  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 size={20} className="animate-spin text-gray-500" />
    </div>
  );
}

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
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  if (minutes < 60) return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function estimateRemaining(pipeline: PipelineStage[], nowMs: number): string {
  const completed = pipeline.filter(
    (stage) => stage.status === "completed" && stage.startedAt && stage.completedAt,
  );
  if (completed.length === 0) return "";

  const avgDuration =
    completed.reduce(
      (sum, stage) => sum + (stage.completedAt! - stage.startedAt!),
      0,
    ) / completed.length;
  const remaining = pipeline.filter((stage) => stage.status === "queued").length;
  const active = pipeline.find((stage) => stage.status === "active");
  let estimate = remaining * avgDuration;
  if (active?.startedAt) {
    estimate += Math.max(0, avgDuration - (nowMs - active.startedAt));
  }

  return estimate > 0 ? formatDuration(estimate) : "";
}

function getStageDetails(stage: PipelineStage): PipelineStageDetails | null {
  if (!stage.details) return null;
  if (typeof stage.details === "string") return null;
  return stage.details;
}

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
      className={`flex items-center gap-1.5 whitespace-nowrap px-3 py-1.5 text-[11px] font-medium transition-colors ${
        active
          ? "border-b-2 border-purple-500 bg-purple-50 text-purple-700 dark:bg-[#1e1e1e] dark:text-gray-200"
          : "border-b-2 border-transparent text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-transparent dark:hover:text-gray-300"
      }`}
    >
      {children}
    </button>
  );
}

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
        onClick={() => setOpen((value) => !value)}
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
      className={`flex w-full items-start gap-2 border-l-2 px-4 py-1.5 pl-7 text-left transition-colors ${
        active
          ? "border-purple-500 bg-purple-50 dark:bg-purple-900/20"
          : "border-transparent hover:bg-gray-100 dark:hover:bg-gray-800/40"
      }`}
    >
      <span
        className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
          status === "unread"
            ? "bg-purple-400"
            : status === "reading"
              ? "bg-blue-400"
              : "bg-gray-600"
        }`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1">
          <p className="flex-1 truncate text-[11px] text-gray-800 dark:text-gray-300">
            {label}
          </p>
          {badge && (
            <span className="shrink-0 rounded bg-amber-400/10 px-1 text-[8px] text-amber-400">
              {badge}
            </span>
          )}
        </div>
        <p className="truncate text-[9px] text-gray-500 dark:text-gray-600">{subtitle}</p>
      </div>
    </button>
  );
}

type RailSection = "library" | "plan" | "training";

const RAIL_SECTIONS: Array<{
  id: RailSection;
  label: string;
  icon: ReactNode;
}> = [
  { id: "library", label: "Library", icon: <FolderOpen size={14} /> },
  { id: "plan", label: "Plan", icon: <Clipboard size={14} /> },
  { id: "training", label: "Training", icon: <FlaskConical size={14} /> },
];

function LibrarySection() {
  const papers = useResearchStore((state) => state.papers);
  const annotations = useResearchStore((state) => state.annotations);
  const noteCount = useResearchStore((state) => state.notes.length);
  const activePaperId = useResearchStore((state) => state.activePaperId);
  const setActivePaper = useResearchStore((state) => state.setActivePaper);
  const setPrimaryTab = useResearchStore((state) => state.setPrimaryTab);
  const pdfRoots = useSettingsStore((state) => state.researchPdfRoots);
  const noteRoots = useSettingsStore((state) => state.researchNoteRoots);
  const wsPdfRoots = useWorkspaceStore((state) => {
    const workspace = state.workspaces.find((item) => item.id === state.activeWorkspaceId);
    return workspace?.researchConfig?.pdfRoots;
  });
  const wsNoteRoots = useWorkspaceStore((state) => {
    const workspace = state.workspaces.find((item) => item.id === state.activeWorkspaceId);
    return workspace?.researchConfig?.noteRoots;
  });

  const effectiveRoots = useMemo(() => {
    const pdf = wsPdfRoots && wsPdfRoots.length > 0 ? wsPdfRoots : pdfRoots;
    const note = wsNoteRoots && wsNoteRoots.length > 0 ? wsNoteRoots : noteRoots;
    return { pdf, note };
  }, [noteRoots, pdfRoots, wsNoteRoots, wsPdfRoots]);

  const annotatedPaperIds = useMemo(
    () => new Set(annotations.map((annotation) => annotation.paperId)),
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
      {(effectiveRoots.pdf.length > 0 || effectiveRoots.note.length > 0) && (
        <div className="border-b border-gray-200 px-3 py-1.5 dark:border-gray-800/50">
          {effectiveRoots.pdf.map((root) => (
            <div
              key={root}
              className="flex items-center gap-1 truncate py-0.5 text-[9px] text-gray-500 dark:text-gray-600"
              title={root}
            >
              <FileText size={9} className="shrink-0 text-gray-500 dark:text-gray-700" />
              {root.split("/").pop()}
            </div>
          ))}
          {effectiveRoots.note.map((root) => (
            <div
              key={root}
              className="flex items-center gap-1 truncate py-0.5 text-[9px] text-gray-500 dark:text-gray-600"
              title={root}
            >
              <StickyNote size={9} className="shrink-0 text-gray-500 dark:text-gray-700" />
              {root.split("/").pop()}
            </div>
          ))}
        </div>
      )}

      <NavSection icon={<FileText size={14} />} label="Papers" count={papers.length}>
        {papers.length === 0 ? (
          <p className="px-7 py-2 text-[10px] italic text-gray-600">
            No papers yet - drag a PDF or use a quick-start
          </p>
        ) : (
          papers.map((paper) => {
            const displayStatus = paperStatus(paper.id, paper.status);
            return (
              <NavItem
                key={paper.id}
                label={paper.title}
                subtitle={`${paper.authors[0] ?? "Unknown"} ${paper.year}`}
                status={displayStatus === "annotated" ? "read" : paper.status}
                badge={displayStatus === "annotated" ? "annotated" : undefined}
                active={activePaperId === paper.id}
                onClick={() => {
                  setActivePaper(paper.id);
                  setPrimaryTab("reader");
                }}
              />
            );
          })
        )}
      </NavSection>

      <NavSection icon={<StickyNote size={14} />} label="Notes" count={noteCount}>
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

function PlanSection() {
  const documentContent = useResearchStore((state) => state.documentContent);
  const setShowContextPanel = useResearchStore((state) => state.setShowContextPanel);
  const setContextTab = useResearchStore((state) => state.setContextTab);
  const setPrimaryTab = useResearchStore((state) => state.setPrimaryTab);

  const headings = useMemo(() => {
    if (!documentContent) return [];
    return documentContent
      .split("\n")
      .filter((line) => /^#{1,3}\s/.test(line))
      .map((line) => ({
        level: line.match(/^(#+)/)![1].length as 1 | 2 | 3,
        text: line.replace(/^#+\s*/, ""),
      }));
  }, [documentContent]);

  return (
    <div className="flex-1 overflow-y-auto">
      <NavSection icon={<List size={14} />} label="Outline" count={headings.length}>
        {headings.length === 0 ? (
          <p className="px-7 py-2 text-[10px] italic text-gray-600">
            Start writing to see outline
          </p>
        ) : (
          headings.slice(0, 20).map((heading, index) => (
            <button
              key={`${heading.text}-${index}`}
              onClick={() => {
                setPrimaryTab("editor");
                setShowContextPanel(true);
                setContextTab("outline");
              }}
              className="w-full truncate px-4 py-0.5 text-left text-[11px] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
              style={{ paddingLeft: `${12 + (heading.level - 1) * 10}px` }}
            >
              {heading.text}
            </button>
          ))
        )}
      </NavSection>

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

function TrainingSection() {
  const sessions = useResearchStore((state) => state.trainingSessions);
  const setPrimaryTab = useResearchStore((state) => state.setPrimaryTab);

  const openFurnace = () => setPrimaryTab("furnace");

  return (
    <div className="flex-1 overflow-y-auto">
      <NavSection icon={<FlaskConical size={14} />} label="Sessions" count={sessions.length}>
        {sessions.length === 0 ? (
          <div className="px-7 py-2">
            <p className="mb-2 text-[10px] italic text-gray-600">No training sessions yet</p>
            <button
              onClick={openFurnace}
              className="flex items-center gap-1 text-[10px] text-purple-500 hover:text-purple-600 dark:text-purple-400 dark:hover:text-purple-300"
            >
              <Plus size={10} /> Start a session
            </button>
          </div>
        ) : (
          sessions.map((session) => (
            <TrainingSessionItem key={session.id} session={session} onClick={openFurnace} />
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
        <span className={statusColors[session.status]}>{statusIcons[session.status]}</span>
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
        <div className="mt-0.5 h-0.5 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
          <div
            className="h-full rounded-full bg-purple-500/70 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </button>
  );
}

export function ResearchModeFunctionRail() {
  const activeSection = useResearchStore((state) => state.activeRailSection);
  const setActiveSection = useResearchStore((state) => state.setActiveRailSection);
  const [showProfileSelector, setShowProfileSelector] = useState(false);
  const activeProfile = getActiveProfile();

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex border-b border-gray-200 dark:border-gray-800">
        {RAIL_SECTIONS.map((section) => (
          <button
            key={section.id}
            onClick={() => setActiveSection(section.id)}
            className={`flex flex-1 items-center justify-center gap-1.5 py-2 text-[10px] font-medium transition-colors ${
              activeSection === section.id
                ? "border-b-2 border-purple-500 bg-purple-50 text-purple-700 dark:bg-[#1e1e1e] dark:text-gray-200"
                : "border-b-2 border-transparent text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-transparent dark:hover:text-gray-300"
            }`}
            title={section.label}
          >
            {section.icon}
            <span className="hidden sm:inline">{section.label}</span>
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {activeSection === "library" && <LibrarySection />}
        {activeSection === "plan" && <PlanSection />}
        {activeSection === "training" && <TrainingSection />}
      </div>

      <div className="shrink-0 border-t border-gray-200 dark:border-gray-800">
        <button
          onClick={() => setShowProfileSelector((value) => !value)}
          className="flex w-full items-center gap-2 px-3 py-2 text-[11px] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/60 dark:hover:text-gray-200"
        >
          <Settings2 size={12} />
          <span className="flex-1 truncate text-left">{activeProfile.name}</span>
          <span className="rounded-full bg-gray-100 px-1.5 text-[9px] text-gray-500 dark:bg-gray-800 dark:text-gray-600">
            {activeProfile.citationStyle.toUpperCase()}
          </span>
          {showProfileSelector ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        </button>
        {showProfileSelector && <DomainProfileSelector onSelect={() => setShowProfileSelector(false)} />}
      </div>
    </div>
  );
}

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
  {
    primary?: "editor" | "reader";
    context?: "references" | "reviews" | "outline" | "notes" | "distillation";
  }
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

function StageIcon({ status }: { status: PipelineStage["status"] }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 size={14} className="shrink-0 text-green-500" />;
    case "active":
      return <Loader2 size={14} className="shrink-0 animate-spin text-blue-400" />;
    case "failed":
      return <XCircle size={14} className="shrink-0 text-red-500" />;
    case "paused":
      return <PauseCircle size={14} className="shrink-0 text-yellow-500" />;
    default:
      return <Circle size={14} className="shrink-0 text-gray-600" />;
  }
}

function StageDetail({
  stage,
  nowMs,
}: {
  stage: PipelineStage;
  nowMs: number;
}) {
  const details = getStageDetails(stage);
  const duration =
    stage.completedAt && stage.startedAt
      ? formatDuration(stage.completedAt - stage.startedAt)
      : stage.startedAt
        ? formatDuration(nowMs - stage.startedAt)
        : "-";

  return (
    <div className="ml-6 mt-1 animate-in rounded border-l-2 border-gray-700 bg-gray-800/30 p-2">
      <div className="space-y-1 text-[10px]">
        <div className="flex justify-between text-gray-500">
          <span>Duration</span>
          <span>{duration}</span>
        </div>
        {details && (
          <>
            <div className="flex justify-between text-gray-500">
              <span>Nodes run</span>
              <span>{details.nodeCount ?? "-"}</span>
            </div>
            <div className="flex justify-between text-gray-500">
              <span>Tokens used</span>
              <span>{details.tokenCount ?? "-"}</span>
            </div>
            {details.outputs && details.outputs.length > 0 && (
              <div className="mt-1">
                <span className="text-gray-600">Outputs:</span>
                <ul className="mt-0.5 space-y-0.5">
                  {details.outputs.map((output, index) => (
                    <li key={`${output}-${index}`} className="pl-2 text-gray-500">
                      * {output}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
        {typeof stage.details === "string" && stage.details && (
          <p className="italic text-gray-500">{stage.details}</p>
        )}
      </div>
    </div>
  );
}

export function ResearchModePipelineProgress() {
  const pipeline = useResearchStore((state) => state.pipeline);
  const pipelinePreset = useResearchStore((state) => state.pipelinePreset);
  const setPipelinePreset = useResearchStore((state) => state.setPipelinePreset);
  const setPrimaryTab = useResearchStore((state) => state.setPrimaryTab);
  const setContextTab = useResearchStore((state) => state.setContextTab);
  const addStageAction = useResearchStore((state) => state.addStage);
  const removeStageAction = useResearchStore((state) => state.removeStage);
  const nowMs = useNow(1000);

  const [expandedStage, setExpandedStage] = useState<string | null>(null);
  const [addingStage, setAddingStage] = useState(false);
  const [newStageName, setNewStageName] = useState("");

  const stages = pipeline.length > 0 ? pipeline : getDefaultStages(pipelinePreset);
  const completed = stages.filter((stage) => stage.status === "completed").length;
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

  const handleRemoveStage = (event: ReactMouseEvent, stageId: string) => {
    event.stopPropagation();
    removeStageAction(stageId);
    if (expandedStage === stageId) setExpandedStage(null);
  };

  const handlePresetSwitch = (preset: PipelinePreset) => {
    setPipelinePreset(preset);
    if (pipeline.length === 0) return;
    const oldDefault = getDefaultStages(
      preset === "research" ? "distillation" : "research",
    );
    const isOldDefault =
      pipeline.length === oldDefault.length &&
      pipeline.every(
        (stage, index) =>
          stage.id === oldDefault[index].id && stage.status === "queued",
      );
    if (isOldDefault) {
      useResearchStore.getState().setPipeline([]);
    }
  };

  const progressPct = stages.length > 0 ? (completed / stages.length) * 100 : 0;

  return (
    <div className="shrink-0 border-t border-gray-200 dark:border-gray-800">
      <div className="flex items-center justify-between px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-gray-500">
        <div className="flex items-center gap-1.5">
          <Gauge size={11} />
          Pipeline
        </div>
        <span className="font-normal normal-case text-gray-600">
          {completed}/{stages.length}
        </span>
      </div>

      <div className="flex gap-1 px-3 pb-1.5">
        {(["research", "distillation"] as const).map((preset) => (
          <button
            key={preset}
            onClick={() => handlePresetSwitch(preset)}
            className={`flex-1 rounded py-0.5 text-[9px] transition-colors ${
              pipelinePreset === preset
                ? "border border-purple-500/30 bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300"
                : "border border-transparent text-gray-600 hover:border-gray-300 hover:text-gray-800 dark:hover:border-gray-700 dark:hover:text-gray-400"
            }`}
          >
            {preset === "research" ? "Research" : "Distillation"}
          </button>
        ))}
      </div>

      <div className="px-3 pb-1.5">
        <div className="h-1 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
          <div
            className="h-full rounded-full bg-gradient-to-r from-purple-600 to-blue-500 transition-all duration-700 ease-out"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {eta && (
        <div className="flex items-center gap-1 px-3 pb-1 text-[9px] text-gray-600">
          <Clock size={9} />
          <span>~{eta} remaining</span>
        </div>
      )}

      <div className="space-y-0.5 px-3 pb-2">
        {stages.map((stage) => (
          <div key={stage.id}>
            <div className="group flex items-center">
              <button
                onClick={() => handleStageClick(stage.id)}
                className="flex flex-1 items-center gap-2 rounded px-1 py-1 text-left transition-colors hover:bg-gray-100 dark:hover:bg-gray-800/50"
              >
                <StageIcon status={stage.status} />
                <span
                  className={`flex-1 text-[11px] transition-colors ${
                    stage.status === "completed"
                      ? "text-gray-500 line-through"
                      : stage.status === "active"
                        ? "font-medium text-blue-300"
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
                  onClick={(event) => handleRemoveStage(event, stage.id)}
                  className="p-0.5 text-gray-600 opacity-0 transition-all hover:text-red-400 group-hover:opacity-100"
                  title="Remove stage"
                >
                  <X size={10} />
                </button>
              )}
            </div>
            {expandedStage === stage.id && <StageDetail stage={stage} nowMs={nowMs} />}
          </div>
        ))}
      </div>

      <div className="flex gap-1 px-3 pb-1">
        {addingStage ? (
          <div className="flex flex-1 gap-1">
            <input
              autoFocus
              value={newStageName}
              onChange={(event) => setNewStageName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") handleAddStage();
                if (event.key === "Escape") {
                  setAddingStage(false);
                  setNewStageName("");
                }
              }}
              placeholder="Stage name..."
              className="flex-1 rounded border border-gray-300 bg-white px-1.5 py-0.5 text-[10px] text-gray-800 placeholder-gray-500 outline-none focus:border-purple-500/50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:placeholder-gray-600"
            />
            <button
              onClick={handleAddStage}
              className="px-1 text-[9px] text-purple-400 hover:text-purple-300"
            >
              Add
            </button>
            <button
              onClick={() => {
                setAddingStage(false);
                setNewStageName("");
              }}
              className="px-1 text-[9px] text-gray-600 hover:text-gray-400"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setAddingStage(true)}
            className="flex items-center gap-0.5 text-[9px] text-gray-600 transition-colors hover:text-gray-400"
          >
            <Plus size={9} /> Add stage
          </button>
        )}
      </div>

      <div className="flex gap-1.5 px-3 pb-2">
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

export function ResearchModePrimaryPanel({
  furnacePanel,
}: {
  furnacePanel: ReactNode;
}) {
  const primaryTab = useResearchStore((state) => state.primaryTab);
  const setPrimaryTab = useResearchStore((state) => state.setPrimaryTab);
  const papers = useResearchStore((state) => state.papers);
  const notes = useResearchStore((state) => state.notes);
  const documentContent = useResearchStore((state) => state.documentContent);
  const activeQuickStart = useResearchStore((state) => state.activeQuickStart);
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
    <div className="flex h-full flex-col">
      <div className="flex border-b border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-[#252526]">
        <TabButton active={primaryTab === "editor"} onClick={() => setPrimaryTab("editor")}>
          <PenLine size={12} /> Editor
        </TabButton>
        <TabButton active={primaryTab === "reader"} onClick={() => setPrimaryTab("reader")}>
          <BookOpen size={12} /> PDF Reader
        </TabButton>
        <TabButton active={primaryTab === "furnace"} onClick={() => setPrimaryTab("furnace")}>
          <Flame size={12} /> Furnace
        </TabButton>
      </div>
      <div className="min-h-0 flex-1">
        <Suspense fallback={<PanelLoader />}>
          {primaryTab === "editor" && <WritingPane />}
          {primaryTab === "reader" && <SplitPdfReader />}
          {primaryTab === "furnace" && furnacePanel}
        </Suspense>
      </div>
    </div>
  );
}

const CONTEXT_TABS = [
  { id: "references" as const, label: "References", icon: <BookMarked size={11} /> },
  { id: "reviews" as const, label: "Reviews", icon: <MessageSquareText size={11} /> },
  { id: "outline" as const, label: "Outline", icon: <List size={11} /> },
  { id: "notes" as const, label: "Notes", icon: <StickyNote size={11} /> },
  { id: "distillation" as const, label: "Distillation", icon: <Flame size={11} /> },
];

export function ResearchModeContextPanel() {
  const contextTab = useResearchStore((state) => state.contextTab);
  const setContextTab = useResearchStore((state) => state.setContextTab);

  return (
    <div className="flex h-full flex-col border-l border-gray-200 dark:border-gray-800">
      <div className="flex overflow-x-auto border-b border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-[#252526]">
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
      <div className="min-h-0 flex-1 overflow-y-auto">
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

function ResearchEmptyStateFallback() {
  const quickStarts = [
    {
      icon: <Search size={20} />,
      title: "New Literature Review",
      description: "Provide a topic -> search, read, synthesize, write",
    },
    {
      icon: <FileText size={20} />,
      title: "New Paper",
      description: "From topic/abstract -> outline, write, review",
    },
    {
      icon: <Eye size={20} />,
      title: "Review This Paper",
      description: "Upload PDF -> structured review",
    },
    {
      icon: <Flame size={20} />,
      title: "Learn 100 Papers",
      description: "Long-running domain learning",
    },
    {
      icon: <GitCompare size={20} />,
      title: "Compare Papers",
      description: "Upload 2+ PDFs -> comparison matrix",
    },
  ];

  return (
    <div className="flex h-full items-center justify-center">
      <div className="max-w-lg px-4 text-center">
        <GraduationCap size={40} className="mx-auto mb-4 text-purple-400" />
        <h2 className="mb-2 text-xl font-semibold text-gray-200">Research Workspace</h2>
        <p className="mb-6 text-sm text-gray-400">
          Choose a starting point or drag a PDF to begin
        </p>
        <div className="grid grid-cols-2 gap-3">
          {quickStarts.map((quickStart) => (
            <button
              key={quickStart.title}
              className="flex items-start gap-3 rounded-lg border border-gray-700/50 bg-gray-800/50 p-3 text-left transition-colors hover:border-purple-500/30 hover:bg-purple-900/10"
            >
              <span className="mt-0.5 text-purple-400">{quickStart.icon}</span>
              <div>
                <p className="text-sm font-medium text-gray-200">{quickStart.title}</p>
                <p className="mt-0.5 text-[11px] text-gray-500">{quickStart.description}</p>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
