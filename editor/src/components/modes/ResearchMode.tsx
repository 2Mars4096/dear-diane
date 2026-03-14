/**
 * Research mode: purpose-built workspace for academic papers, literature reviews,
 * and systematic research. Layout: ResearchNav + Pipeline | Primary + Context | Secondary.
 */
import {
  useState,
  useEffect,
  useCallback,
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
  Code2,
  Image as ImageIcon,
  Database,
  Search,
  Eye,
  GitCompare,
  GraduationCap,
  ChevronDown,
  ChevronRight,
  Plus,
  Trash2,
  PanelBottomClose,
  PanelBottomOpen,
  Gauge,
  Clock,
  X,
  Settings2,
} from "lucide-react";
import {
  useResearchStore,
  type PipelineStage,
  type PipelineStageDetails,
  type PipelinePreset,
} from "../../store/useResearchStore";
import DomainProfileSelector, {
  getActiveProfile,
} from "../research/DomainProfile";

/* ------------------------------------------------------------------ */
/*  Lazy-loaded panel components                                       */
/* ------------------------------------------------------------------ */

const WritingPane = lazy(() => import("../research/WritingPane"));
const PdfReader = lazy(() => import("../research/PdfReader"));
const SplitPdfReader = lazy(() => import("../research/SplitPdfReader"));
const ReferencePanel = lazy(() => import("../research/ReferencePanel"));
const ReviewPanel = lazy(() => import("../research/ReviewPanel"));
const OutlinePanel = lazy(() => import("../research/OutlinePanel"));
const NotesPanel = lazy(() => import("../research/NotesPanel"));
const DistillationTab = lazy(() => import("../research/DistillationTab"));
const CodeCells = lazy(() => import("../research/CodeCells"));

const FigureGallery = lazy(() =>
  import("../research/FigureGallery").catch(() => ({
    default: () => (
      <PlaceholderPane label="Figure Gallery" icon={<ImageIcon size={24} />} />
    ),
  })),
);
const DataBrowser = lazy(() =>
  import("../research/DataBrowser").catch(() => ({
    default: () => (
      <PlaceholderPane label="Data Browser" icon={<Database size={24} />} />
    ),
  })),
);
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

function estimateRemaining(pipeline: PipelineStage[]): string {
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
    estimate += Math.max(0, avgDuration - (Date.now() - active.startedAt));

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
  active,
  onClick,
}: {
  label: string;
  subtitle: string;
  status: "unread" | "reading" | "read";
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
      <div className="min-w-0">
        <p className="text-[11px] text-gray-300 truncate">{label}</p>
        <p className="text-[9px] text-gray-600 truncate">{subtitle}</p>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Research Nav (left sidebar — top section)                          */
/* ------------------------------------------------------------------ */

function ResearchNav() {
  const papers = useResearchStore((s) => s.papers);
  const notes = useResearchStore((s) => s.notes);
  const activePaperId = useResearchStore((s) => s.activePaperId);
  const setActivePaper = useResearchStore((s) => s.setActivePaper);
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);
  const setContextTab = useResearchStore((s) => s.setContextTab);
  const [showProfileSelector, setShowProfileSelector] = useState(false);
  const activeProfile = getActiveProfile();

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="px-3 py-2 text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
        Research
      </div>

      <NavSection
        icon={<FileText size={14} />}
        label="Papers"
        count={papers.length}
      >
        {papers.length === 0 ? (
          <p className="px-7 py-2 text-[10px] text-gray-600 italic">
            No papers yet
          </p>
        ) : (
          papers.map((p) => (
            <NavItem
              key={p.id}
              label={p.title}
              subtitle={`${p.authors[0] ?? "Unknown"} ${p.year}`}
              status={p.status}
              active={activePaperId === p.id}
              onClick={() => {
                setActivePaper(p.id);
                setPrimaryTab("reader");
              }}
            />
          ))
        )}
      </NavSection>

      <NavSection
        icon={<StickyNote size={14} />}
        label="Notes"
        count={notes.length}
      >
        <button
          onClick={() => setContextTab("notes")}
          className="w-full text-left px-4 pl-7 py-1 text-[11px] text-gray-400 hover:bg-gray-800/40"
        >
          View all notes
        </button>
      </NavSection>

      <NavSection icon={<Flame size={14} />} label="Learn" count={0}>
        <button
          onClick={() => setContextTab("distillation")}
          className="w-full text-left px-4 pl-7 py-1 text-[11px] text-gray-400 hover:bg-gray-800/40"
        >
          100 Papers Learning
        </button>
      </NavSection>

      {/* Domain profile selector */}
      <div className="border-t border-gray-800 mt-1">
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
  { primary?: "editor" | "reader"; context?: string }
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

function StageDetail({ stage }: { stage: PipelineStage }) {
  const details = getStageDetails(stage);
  const duration =
    stage.completedAt && stage.startedAt
      ? formatDuration(stage.completedAt - stage.startedAt)
      : stage.startedAt
        ? formatDuration(Date.now() - stage.startedAt)
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

  const [expandedStage, setExpandedStage] = useState<string | null>(null);
  const [addingStage, setAddingStage] = useState(false);
  const [newStageName, setNewStageName] = useState("");

  const stages =
    pipeline.length > 0 ? pipeline : getDefaultStages(pipelinePreset);
  const completed = stages.filter((s) => s.status === "completed").length;
  const eta = estimateRemaining(stages);

  const handleStageClick = (stageId: string) => {
    setExpandedStage((prev) => (prev === stageId ? null : stageId));

    const mapping = STAGE_PANEL_MAP[stageId];
    if (!mapping) return;
    if (mapping.primary) setPrimaryTab(mapping.primary);
    if (mapping.context) setContextTab(mapping.context as any);
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
                    {formatDuration(Date.now() - stage.startedAt)}
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
              <StageDetail stage={stage} />
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
/*  Placeholder panel (fallback for missing components)                */
/* ------------------------------------------------------------------ */

function PlaceholderPane({
  label,
  icon,
}: {
  label: string;
  icon: ReactNode;
}) {
  return (
    <div className="h-full flex items-center justify-center text-gray-500">
      <div className="text-center space-y-2">
        <div className="mx-auto opacity-40">{icon}</div>
        <p className="text-xs">{label}</p>
        <p className="text-[10px] text-gray-600">Coming soon</p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Primary Panel (Editor | PDF Reader)                                */
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
    !activeQuickStart;

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
      </div>
      <div className="flex-1 min-h-0">
        <Suspense fallback={<PanelLoader />}>
          {primaryTab === "editor" && <WritingPane />}
          {primaryTab === "reader" && <SplitPdfReader />}
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
/*  Secondary Panel (Code | Figures | Data)                            */
/* ------------------------------------------------------------------ */

function SecondaryPanel() {
  const secondaryTab = useResearchStore((s) => s.secondaryTab);
  const setSecondaryTab = useResearchStore((s) => s.setSecondaryTab);

  return (
    <div className="h-full flex flex-col border-t border-gray-800">
      <div className="flex border-b border-gray-800 bg-[#252526]">
        <TabButton
          active={secondaryTab === "code"}
          onClick={() => setSecondaryTab("code")}
        >
          <Code2 size={12} /> Code
        </TabButton>
        <TabButton
          active={secondaryTab === "figures"}
          onClick={() => setSecondaryTab("figures")}
        >
          <ImageIcon size={12} /> Figures
        </TabButton>
        <TabButton
          active={secondaryTab === "data"}
          onClick={() => setSecondaryTab("data")}
        >
          <Database size={12} /> Data
        </TabButton>
      </div>
      <div className="flex-1 min-h-0">
        <Suspense fallback={<PanelLoader />}>
          {secondaryTab === "code" && <CodeCells />}
          {secondaryTab === "figures" && <FigureGallery />}
          {secondaryTab === "data" && <DataBrowser />}
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
/*  Keyboard shortcuts hook                                            */
/* ------------------------------------------------------------------ */

function useResearchShortcuts() {
  const setPrimaryTab = useResearchStore((s) => s.setPrimaryTab);
  const primaryTab = useResearchStore((s) => s.primaryTab);
  const setContextTab = useResearchStore((s) => s.setContextTab);
  const contextTab = useResearchStore((s) => s.contextTab);

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
        return;
      }
      if (meta && e.shiftKey && e.key === "o") {
        e.preventDefault();
        setContextTab(
          contextTab === "outline" ? "notes" : "outline",
        );
        return;
      }
    },
    [setPrimaryTab, primaryTab, setContextTab, contextTab],
  );

  useEffect(() => {
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [handler]);
}

/* ------------------------------------------------------------------ */
/*  Main layout                                                        */
/* ------------------------------------------------------------------ */

export default function ResearchMode() {
  useResearchEvents();
  useResearchShortcuts();

  const showPipeline = useResearchStore((s) => s.showPipeline);
  const showSecondary = useResearchStore((s) => s.showSecondary);
  const togglePipeline = useResearchStore((s) => s.togglePipeline);
  const toggleSecondary = useResearchStore((s) => s.toggleSecondary);

  return (
    <div className="h-full flex bg-[#1e1e1e] text-gray-200">
      {/* Left sidebar: Research Nav + Pipeline Progress */}
      <div className="w-56 border-r border-gray-800 flex flex-col bg-[#1e1e1e] shrink-0">
        <ResearchNav />
        <div className="border-t border-gray-800 px-2 py-1 flex items-center gap-1">
          <button
            onClick={togglePipeline}
            title={showPipeline ? "Hide pipeline" : "Show pipeline"}
            className="text-[10px] text-gray-500 hover:text-gray-300 flex items-center gap-1 transition-colors"
          >
            <Gauge size={11} />
            {showPipeline ? "Hide" : "Show"} Pipeline
          </button>
          <span className="flex-1" />
          <button
            onClick={toggleSecondary}
            title={
              showSecondary ? "Hide bottom panel" : "Show bottom panel"
            }
            className="text-gray-500 hover:text-gray-300 transition-colors"
          >
            {showSecondary ? (
              <PanelBottomClose size={13} />
            ) : (
              <PanelBottomOpen size={13} />
            )}
          </button>
        </div>
        {showPipeline && <PipelineProgress />}
      </div>

      {/* Main area: Primary + Context (top) | Secondary (bottom) */}
      <div className="flex-1 min-w-0 flex flex-col">
        <Allotment vertical>
          <Allotment.Pane minSize={200}>
            <Allotment>
              <Allotment.Pane minSize={300}>
                <PrimaryPanel />
              </Allotment.Pane>
              <Allotment.Pane preferredSize={320} minSize={200}>
                <ContextPanel />
              </Allotment.Pane>
            </Allotment>
          </Allotment.Pane>

          {showSecondary && (
            <Allotment.Pane preferredSize={250} minSize={100}>
              <SecondaryPanel />
            </Allotment.Pane>
          )}
        </Allotment>
      </div>
    </div>
  );
}
