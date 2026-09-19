import { ProjectMenu } from "../workbench/ProjectMenu";
import { SidecarChat } from "../workbench/SidecarChat";
import { LeadAgentMenu } from "../workbench/LeadAgentMenu";
import { DanSettings } from "../workbench/DanSettings";
import { ImportNativeSessions } from "../workbench/ImportNativeSessions";
import { NativeWorkerSettings, loadWorkerProfiles, type WorkerProfiles } from "../workbench/NativeWorkers";
import { TeamPanel, TeamStrip, useTeamWorkers } from "../workbench/TeamProgress";
import { ProjectSettings } from "../workbench/ProjectSettings";
import { streamedMessageContent, completedMessageContent } from "../workbench/eventPresentation";
import { WorkbenchNavigation } from "../workbench/WorkbenchNavigation";
import { WorkbenchConversation, WorkbenchActivity } from "../workbench/WorkbenchConversation";
import "../workbench/workbench.css";
import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type CSSProperties,
  type DragEvent,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
  type SyntheticEvent,
  type WheelEvent,
} from "react";
import {
  Activity,
  Archive,
  ArrowUp,
  Bot,
  Cable,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  Eye,
  File,
  FileText,
  Folder,
  FolderPlus,
  FolderOpen,
  GitBranch as GitBranchIcon,
  Image as ImageIcon,
  Link as LinkIcon,
  Lightbulb,
  Loader2,
  Maximize2,
  MessageSquareText,
  Minus,
  MoreHorizontal,
  NotebookPen,
  PanelLeft,
  PanelRight,
  Plus,
  RotateCcw,
  ScrollText,
  Search,
  Send,
  Shield,
  ShieldOff,
  Square,
  TerminalSquare,
  Trash2,
  WandSparkles,
  X,
} from "lucide-react";
import {
  listWorkspaceFileTree,
  listWorkspaceNotes,
  listWorkspaceRootSuggestions,
  listWorkspaceSkillSuggestions,
  createWorkspaceFolder,
  generateWorkspaceNoteLearnCourse,
  getWorkspaceNoteLearnCourse,
  getWorkspaceWireGuardStatus,
  moveWorkspaceNotePath,
  moveWorkspacePath,
  readWorkspaceFile,
  readWorkspaceNote,
  updateWorkspaceNoteLearnProgress,
  workspaceFilePreviewUrl,
  writeWorkspaceNote,
  type WorkspaceLearnCourse,
  type WorkspaceLearnSession,
  type WorkspaceFileEntry,
  type WorkspaceNoteSummary,
  type WorkspaceRootSuggestion,
  type WorkspaceSkillSuggestion,
  type WorkspaceWireGuardStatus,
} from "../../lib/api";
import {
  archiveChatV2Thread,
  connectChatV2AgentRunEvents,
  createChatV2AgentRun,
  createChatV2Thread,
  deleteChatV2Thread,
  executeChatV2AgentRun,
  getChatV2AgentRun,
  getChatV2AgentRunEvents,
  getChatV2ThreadPromptLog,
  getChatV2Thread,
  listChatV2Tasks,
  listChatV2ThreadTasks,
  listChatV2Threads,
  normalizeChatV2History,
  postChatV2AgentRunCommand,
  saveChatV2Thread,
  type ChatV2AgentRunRecord,
  type ChatV2AgentRunEvent,
  type ChatV2TaskSnapshot,
  type ChatV2ThreadSummary,
} from "../../lib/chatV2Api";
import {
  composerDraftToChatAttachment,
  normalizeAttachmentDrafts,
  resolveAttachmentName,
  type ComposerAttachmentDraft,
} from "../../lib/composerAttachments";
import {
  isElectron,
  nativeDialog,
  nativeFs,
  nativeShell,
  nativeWatch,
} from "../../lib/electronBridge";
import {
  normalizeExecutionAttemptProjections,
  normalizeTaskBlueprintProjection,
  taskFamilyPresentation,
  type ExecutionAttemptProjection,
  type TaskBlueprintContractProjection,
  type TaskFamily,
} from "../../lib/taskBlueprintProjection";
import { workspaceSurfaceThemeClassName } from "../../lib/workspaceSurfaceTheme";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import type { ChatMessage, RunEventPayload } from "../../types/chat";
import MarkdownRenderer from "../shared/MarkdownRenderer";

const DEFAULT_WORKFLOW_ID = "_scratch";
const SUPER_DAN_BACKEND = "super_dan";
const CODEX_BACKEND = "codex";
const SUPER_TUI_PROFILE = "super_tui";
const NOTES_STORAGE_KEY = "dan.chunkWorkspace.notes.v1";
const NOTE_CONTENT_CACHE_STORAGE_KEY = "dan.chunkWorkspace.noteContentCache.v1";
const LAST_THREAD_STORAGE_KEY = "dan.chunkWorkspace.lastThread.v1";
const ROOT_SUGGESTION_STORAGE_KEY = "dan.chunkWorkspace.roots.v1";
const THREAD_WORKSPACE_STORAGE_KEY = "dan.chunkWorkspace.threadWorkspaces.v1";
const SESSION_RESPONSE_SEEN_STORAGE_KEY = "dan.chunkWorkspace.sessionResponseSeen.v1";
const COMPOSER_DRAFT_STORAGE_KEY = "dan.chunkWorkspace.composerDrafts.v1";
const WORKSPACE_COMPOSER_MAX_SCREENSHOTS = 4;
const LAYOUT_STORAGE_KEY = "dan.chunkWorkspace.layout.v3";
const UI_STATE_STORAGE_KEY = "dan.chunkWorkspace.uiState.v1";
const AGENT_SELECTION_STORAGE_KEY = "dan.chunkWorkspace.agentSelection.v1";
const MODEL_SELECTION_STORAGE_KEY = "dan.chunkWorkspace.modelSelection.v1";
const MODEL_SELECTIONS_BY_AGENT_STORAGE_KEY = "dan.chunkWorkspace.modelSelectionsByAgent.v1";
const AUTONOMY_MODE_STORAGE_KEY = "dan.chunkWorkspace.autonomyMode.v1";
const WORKSPACE_ROOT_ALIASES = new Map([
  [
    "/Volumes/data/Dropbox/Projects/deep-agent-network",
    "/Users/lizhi/Downloads/local_projects/deep-agent-network",
  ],
]);
const WORKSPACE_SURFACE_TYPE = "frontend";
const WORKSPACE_SURFACE_ID = "chunk-workspace";
const WORKSPACE_SURFACE = `${WORKSPACE_SURFACE_TYPE}:${WORKSPACE_SURFACE_ID}`;
const NOTES_WORKSPACE_RULES = [
  "Treat the notes root as a Hugo content tree, not a scratch folder.",
  "Edit Markdown or MDX pages in place and preserve YAML frontmatter unless the operator asks to change metadata.",
  "Prefer Hugo bundle pages such as folder/index.md or index.mdx when creating durable notes.",
  "Preserve existing fields including title, layout, date, lastmod, pageID, tags, categories, aliases, images, math, toc, and draft.",
  "Use pageID and @pageID citations for cross-note references when available.",
  "Keep note reads, writes, and moves inside the configured notes root.",
];
const NOTES_WORKSPACE_WRITE_POLICY =
  "Use the notes workspace as context by default; create or edit notes only when the operator asks for notes, memory, documentation, or a saved artifact.";
type WorkspaceAgentSelectionId = "native" | "codex" | "claude" | "antigravity";
type WorkspaceCodexReasoningEffort = "low" | "medium" | "high" | "xhigh";
type WorkspaceAutonomyMode = "plan" | "auto" | "full";
type WorkspaceAgentOption = {
  id: WorkspaceAgentSelectionId;
  label: string;
  shortLabel: string;
  backend: typeof SUPER_DAN_BACKEND | typeof CODEX_BACKEND | "native_codex" | "claude" | "antigravity";
};
type WorkspaceModelOption = {
  id: string;
  agentId: WorkspaceAgentSelectionId;
  label: string;
  shortLabel: string;
  model?: string;
  reasoningEffort?: WorkspaceCodexReasoningEffort;
  baseUrl?: string;
};
type WorkspaceModelSelectionByAgent = Record<WorkspaceAgentSelectionId, string>;
type WorkspaceAutonomyOption = {
  id: WorkspaceAutonomyMode;
  label: string;
  shortLabel: string;
  description: string;
};
type StoredWorkspaceSelection = {
  agentId: WorkspaceAgentSelectionId;
  modelId: string;
  modelSelectionsByAgent: WorkspaceModelSelectionByAgent;
};
const DEFAULT_AGENT_SELECTION_ID: WorkspaceAgentSelectionId = "native";
const DEFAULT_AUTONOMY_MODE: WorkspaceAutonomyMode = "auto";
// One DAN mode per intent; each maps to the closest Codex sandbox / Claude permission mode.
const WORKSPACE_AUTONOMY_OPTIONS: WorkspaceAutonomyOption[] = [
  { id: "plan", label: "Plan", shortLabel: "Plan", description: "Read and propose. Nothing changes until you decide." },
  { id: "auto", label: "Auto", shortLabel: "Auto", description: "Edit files and run commands inside this project." },
  { id: "full", label: "Full access", shortLabel: "Full", description: "No sandbox or approvals. Use only in trusted projects." },
];
/** DAN's own runtime only distinguishes pausing for decisions from acting on its own. */
function danAutonomyMode(mode: WorkspaceAutonomyMode): "auto" | "review" {
  return mode === "plan" ? "review" : "auto";
}
const WORKSPACE_AGENT_OPTIONS: WorkspaceAgentOption[] = [
  { id: "codex", label: "Codex", shortLabel: "Codex", backend: "native_codex" },
  { id: "claude", label: "Claude Code", shortLabel: "Claude", backend: "claude" },
  { id: "antigravity", label: "Antigravity", shortLabel: "Antigravity", backend: "antigravity" },
  {
    id: "native",
    label: "Super DAN",
    shortLabel: "DAN",
    backend: SUPER_DAN_BACKEND,
  },
];
const DEFAULT_MODEL_SELECTION_BY_AGENT: Record<WorkspaceAgentSelectionId, string> = {
  native: "native_default",
  codex: "codex_default",
  claude: "claude_default",
  antigravity: "antigravity_default",
};
const WORKSPACE_MODEL_OPTIONS: WorkspaceModelOption[] = [
  { id: "codex_default", agentId: "codex", label: "CLI default", shortLabel: "CLI default" },
  { id: "claude_default", agentId: "claude", label: "CLI default", shortLabel: "CLI default" },
  { id: "antigravity_default", agentId: "antigravity", label: "CLI default", shortLabel: "CLI default" },
  { id: "openrouter_deepseek_v41_flash", agentId: "native", label: "OpenRouter · DeepSeek V4.1 Flash", shortLabel: "DeepSeek V4.1 Flash", model: "deepseek/deepseek-v4.1-flash", baseUrl: "https://openrouter.ai/api/v1" },
  { id: "openrouter_kimi_k26", agentId: "native", label: "OpenRouter · Kimi K2.6", shortLabel: "Kimi K2.6", model: "moonshotai/kimi-k2.6", baseUrl: "https://openrouter.ai/api/v1" },
  {
    id: "native_default",
    agentId: "native",
    label: "Default",
    shortLabel: "Default",
  },
  {
    id: "native_kimi_k26",
    agentId: "native",
    label: "Kimi K2.6",
    shortLabel: "Kimi K2.6",
    model: "kimi-k2.6",
  },
];

function isWorkspaceAgentSelectionId(value: string | null | undefined): value is WorkspaceAgentSelectionId {
  return WORKSPACE_AGENT_OPTIONS.some((option) => option.id === value);
}

function legacyWorkspaceSelection(value: string | null | undefined): {
  agentId: WorkspaceAgentSelectionId;
  modelId: string;
} {
  if (value === "codex") {
    return { agentId: "native", modelId: DEFAULT_MODEL_SELECTION_BY_AGENT.native };
  }
  if (value === "super_dan_kimi_k26") {
    return { agentId: "native", modelId: "native_kimi_k26" };
  }
  return { agentId: "native", modelId: DEFAULT_MODEL_SELECTION_BY_AGENT.native };
}

function workspaceAgentOptionForId(id: string | null | undefined): WorkspaceAgentOption {
  return (
    WORKSPACE_AGENT_OPTIONS.find((option) => option.id === id) ??
    WORKSPACE_AGENT_OPTIONS.find((option) => option.id === DEFAULT_AGENT_SELECTION_ID) ??
    WORKSPACE_AGENT_OPTIONS[0]
  );
}

function isWorkspaceAutonomyMode(value: string | null | undefined): value is WorkspaceAutonomyMode {
  return value === "plan" || value === "auto" || value === "full";
}

function workspaceAutonomyOptionForId(id: string | null | undefined): WorkspaceAutonomyOption {
  return (
    WORKSPACE_AUTONOMY_OPTIONS.find((option) => option.id === id) ??
    WORKSPACE_AUTONOMY_OPTIONS.find((option) => option.id === DEFAULT_AUTONOMY_MODE) ??
    WORKSPACE_AUTONOMY_OPTIONS[0]
  );
}

function readStoredWorkspaceAutonomyMode(): WorkspaceAutonomyMode {
  if (typeof window === "undefined") return DEFAULT_AUTONOMY_MODE;
  const stored = window.localStorage.getItem(AUTONOMY_MODE_STORAGE_KEY);
  if (stored === "review") return "plan";
  return isWorkspaceAutonomyMode(stored) ? stored : DEFAULT_AUTONOMY_MODE;
}

function workspaceModelOptionsForAgent(agentId: WorkspaceAgentSelectionId) {
  return WORKSPACE_MODEL_OPTIONS.filter((option) => option.agentId === agentId);
}

function workspaceModelOptionForId(
  id: string | null | undefined,
  agentId: WorkspaceAgentSelectionId,
): WorkspaceModelOption {
  return (
    WORKSPACE_MODEL_OPTIONS.find((option) => option.id === id && option.agentId === agentId) ??
    WORKSPACE_MODEL_OPTIONS.find(
      (option) => option.id === DEFAULT_MODEL_SELECTION_BY_AGENT[agentId] && option.agentId === agentId,
    ) ??
    workspaceModelOptionsForAgent(agentId)[0]
  );
}

function defaultWorkspaceModelSelectionsByAgent(): WorkspaceModelSelectionByAgent {
  return { ...DEFAULT_MODEL_SELECTION_BY_AGENT };
}

function normalizeWorkspaceModelSelectionsByAgent(value: unknown): Partial<WorkspaceModelSelectionByAgent> {
  const normalized: Partial<WorkspaceModelSelectionByAgent> = {};
  if (!value || typeof value !== "object" || Array.isArray(value)) return normalized;
  const record = value as Record<string, unknown>;
  for (const agentOption of WORKSPACE_AGENT_OPTIONS) {
    const modelId = record[agentOption.id];
    if (typeof modelId !== "string") continue;
    const modelOption = WORKSPACE_MODEL_OPTIONS.find(
      (option) => option.id === modelId && option.agentId === agentOption.id,
    );
    if (modelOption) {
      normalized[agentOption.id] = modelOption.id;
    }
  }
  return normalized;
}

function parseWorkspaceModelSelectionsByAgent(
  value: string | null | undefined,
): Partial<WorkspaceModelSelectionByAgent> {
  if (!value) return {};
  try {
    return normalizeWorkspaceModelSelectionsByAgent(JSON.parse(value));
  } catch {
    return {};
  }
}

function workspaceSelectionFromStorageValues({
  storedAgent = null,
  storedModel = null,
  storedModelsByAgent = null,
}: {
  storedAgent?: string | null;
  storedModel?: string | null;
  storedModelsByAgent?: string | null;
} = {}): StoredWorkspaceSelection {
  const legacySelection = legacyWorkspaceSelection(storedAgent);
  const agentId = isWorkspaceAgentSelectionId(storedAgent) ? storedAgent : legacySelection.agentId;
  const storedModelSelectionsByAgent = parseWorkspaceModelSelectionsByAgent(storedModelsByAgent);
  const modelSelectionsByAgent = defaultWorkspaceModelSelectionsByAgent();

  for (const agentOption of WORKSPACE_AGENT_OPTIONS) {
    const storedModelId = storedModelSelectionsByAgent[agentOption.id];
    if (storedModelId) {
      modelSelectionsByAgent[agentOption.id] = storedModelId;
    }
  }

  if (!storedModelSelectionsByAgent[legacySelection.agentId]) {
    modelSelectionsByAgent[legacySelection.agentId] = workspaceModelOptionForId(
      legacySelection.modelId,
      legacySelection.agentId,
    ).id;
  }

  const storedGlobalModelId =
    storedModel && workspaceModelOptionForId(storedModel, agentId).id === storedModel
      ? storedModel
      : null;
  const modelId = workspaceModelOptionForId(
    storedModelSelectionsByAgent[agentId] ?? storedGlobalModelId ?? modelSelectionsByAgent[agentId],
    agentId,
  ).id;
  modelSelectionsByAgent[agentId] = modelId;

  return { agentId, modelId, modelSelectionsByAgent };
}

function readStoredWorkspaceSelection(): StoredWorkspaceSelection {
  if (typeof window === "undefined") {
    return workspaceSelectionFromStorageValues();
  }
  return workspaceSelectionFromStorageValues({
    storedAgent: window.localStorage.getItem(AGENT_SELECTION_STORAGE_KEY),
    storedModel: window.localStorage.getItem(MODEL_SELECTION_STORAGE_KEY),
    storedModelsByAgent: window.localStorage.getItem(MODEL_SELECTIONS_BY_AGENT_STORAGE_KEY),
  });
}

function buildWorkspaceAgentExecutePayload(
  agentOption: WorkspaceAgentOption,
  modelOption: WorkspaceModelOption,
  autonomyMode: WorkspaceAutonomyMode = DEFAULT_AUTONOMY_MODE,
) {
  const autonomyOption = workspaceAutonomyOptionForId(autonomyMode);
  const danMode = danAutonomyMode(autonomyOption.id);
  const profilePolicy: Record<string, unknown> = {
    backend: agentOption.backend,
    surface_profile: SUPER_TUI_PROFILE,
    autonomy_mode: danMode,
    attention_resolution_mode: danMode,
    permission_mode: autonomyOption.id,
  };
  if (modelOption.baseUrl) profilePolicy.base_url = modelOption.baseUrl;
  if (modelOption.model) {
    if (agentOption.backend === CODEX_BACKEND) {
      profilePolicy.codex_model = modelOption.model;
    } else {
      profilePolicy.model = modelOption.model;
    }
  }
  if (agentOption.backend === CODEX_BACKEND && modelOption.reasoningEffort) {
    profilePolicy.codex_reasoning_effort = modelOption.reasoningEffort;
  }
  if (agentOption.id !== "native") {
    profilePolicy.auto_backend_continuation = false;
  }
  return {
    backend: agentOption.backend,
    surface_profile: SUPER_TUI_PROFILE,
    background: true,
    profile_policy: profilePolicy,
    approval_policy: {
      mode: danMode === "review" ? "ask_on_attention" : "auto_within_workspace",
      attention_resolution: danMode,
    },
    metadata: {
      backend: agentOption.backend,
      surface_profile: SUPER_TUI_PROFILE,
      compatibility_profile: SUPER_TUI_PROFILE,
      surface: "gui:chunk-workspace",
      requested_from: "chunk_workspace",
      autonomy_mode: danMode,
      permission_mode: autonomyOption.id,
      attention_resolution_mode: danMode,
      attention_resolution_label: autonomyOption.label,
      gui_for: agentOption.id === "native" ? "dan super-tui" : `${agentOption.id} CLI`,
      selected_backend: agentOption.backend,
      selected_agent: agentOption.id,
      selected_agent_label: agentOption.label,
      selected_model_option: modelOption.id,
      selected_model_label: modelOption.label,
      ...(modelOption.model ? { selected_model: modelOption.model } : {}),
      ...(modelOption.reasoningEffort ? { selected_reasoning_effort: modelOption.reasoningEffort } : {}),
    },
  };
}
const LEFT_RAIL_DEFAULT_WIDTH = 292;
const LEFT_RAIL_MIN_WIDTH = 220;
const LEFT_RAIL_MAX_WIDTH = 420;
const ROOT_PICKER_DEFAULT_WIDTH = 420;
const ROOT_PICKER_MIN_WIDTH = 300;
const ROOT_PICKER_MAX_WIDTH = 760;
const ROOT_PICKER_DEFAULT_HEIGHT = 192;
const ROOT_PICKER_MIN_HEIGHT = 128;
const ROOT_PICKER_MAX_HEIGHT = 360;
const COLLAPSED_PANE_WIDTH = 44;
const RECENT_MODIFIED_LIMIT = 5;

type NoteSource = "local" | "disk" | "server";
type NoteStatus = "clean" | "dirty" | "saving" | "error" | "loading";

interface WorkspaceNote {
  id: string;
  title: string;
  path?: string;
  relativePath?: string;
  source: NoteSource;
  content: string;
  loaded: boolean;
  status: NoteStatus;
  updatedAt: number;
  size?: number;
  section?: string;
  layout?: string;
  pageID?: string;
  date?: string;
  lastmod?: string;
  draft?: boolean;
  temporary?: boolean;
  draftTargetSection?: string;
  tags: string[];
  categories: string[];
  citations: string[];
  error?: string;
}

interface HugoPageMeta {
  title: string;
  subtitle: string;
  abstract: string;
  date: string;
  lastmod: string;
  author: string;
  pageID: string;
  link: string;
  figure: string;
  layout: string;
  math: string;
  draft: string;
  tags: string[];
  categories: string[];
}

interface KnowledgeGraphNode {
  id: string;
  title: string;
  section: string;
  degree: number;
  note: WorkspaceNote;
  x: number;
  y: number;
  radius: number;
}

interface KnowledgeGraphLink {
  source: string;
  target: string;
}

interface ParsedHugoPage {
  body: string;
  meta: HugoPageMeta;
  hasFrontmatter: boolean;
}

interface NoteCacheEntry {
  content: string;
  updatedAt: number;
  size: number;
}

type ChunkKind = "chat" | "agent" | "code" | "diff";
type ChunkStatus = "clean" | "dirty" | "running" | "queued" | "error";
type BlueprintNodeStatus = "done" | "active" | "ready" | "future" | "queued" | "blocked";
type BlueprintNodeKind =
  | "request"
  | "understanding"
  | "plan"
  | "task"
  | "worktree"
  | "build"
  | "tool"
  | "change"
  | "validation"
  | "repair"
  | "decision"
  | "artifact"
  | "gate"
  | "hypothesis"
  | "evidence"
  | "variant"
  | "approval"
  | "loop"
  | "composite"
  | "answer"
  | "queue";
type WorkspacePane = "work" | "notes";
type PhonePage =
  | "chat"
  | "sessions"
  | "files"
  | "preview"
  | "note-list"
  | "note-edit"
  | "note-preview"
  | "note-learn";
type NoteRailView = "pages" | "tags" | "sections";
type NoteCollectionSort = "recent" | "title";
type NoteCollectionLayout = "list" | "grid";
type ActiveRunPlacement = "steer" | "queue";
type ComposerSubmitMode = ActiveRunPlacement;
type WorkspaceComposerTrigger = "/" | "$" | "@";

interface WorkspaceComposerToken {
  trigger: WorkspaceComposerTrigger;
  query: string;
  start: number;
  end: number;
  key: string;
}

interface WorkspaceComposerSuggestion {
  id: string;
  type: "command" | "skill" | "file";
  insertText: string;
  label: string;
  detail: string;
  meta: string;
}

const DEFAULT_PLAN_GENERATION_QUEUE_LENGTH = 4;
const DEFAULT_PLAN_EXECUTION_QUEUE_LENGTH = 4;
const DEFAULT_TASK_EXECUTION_QUEUE_LENGTH = 4;
const WORKSPACE_COMPOSER_COMMANDS: Array<{ command: string; description: string }> = [
  { command: "/plan", description: "Show the deterministic contract" },
  { command: "/progress", description: "Narrate current or recent work" },
  { command: "/tasks", description: "Show active, queued, and recent work" },
  { command: "/status", description: "Show visible task status" },
  { command: "/inside", description: "Inspect recent internal activity" },
  { command: "/skills", description: "Browse skill mentions" },
  { command: "/reset", description: "Archive workspace context or state" },
  { command: "/help", description: "Show commands" },
  { command: "/new", description: "Start a separate task turn" },
  { command: "/append", description: "Add instructions to active work" },
  { command: "/pause", description: "Pause active work at checkpoints" },
  { command: "/resume", description: "Resume paused work" },
  { command: "/stop", description: "Request stop for active work" },
  { command: "/focus", description: "Choose a visible task target" },
];

interface WorkspaceChunk {
  id: string;
  kind: ChunkKind;
  title: string;
  body: string;
  filePath?: string;
  status: ChunkStatus;
  meta: string;
  role?: ChatMessage["role"];
  taskId?: string | null;
  runId?: string | null;
}

interface PromptLogPreview {
  threadId: string;
  title: string;
  body: string;
  path: string;
  entryCount: number;
  status: "idle" | "loading" | "error";
}

type WorkspaceFilePreviewKind = "image" | "pdf" | "html" | "markdown" | "text";
type WorkspacePreviewArtifactSource = "file" | "artifact" | "session";

interface WorkspacePreviewArtifact {
  id: string;
  entry: WorkspaceFileEntry;
  kind: WorkspaceFilePreviewKind;
  source: WorkspacePreviewArtifactSource;
}

interface BlueprintPlanTask {
  taskId: string;
  parentId?: string;
  planId?: string;
  branchId?: string;
  nodeType?: string;
  goal: string;
  dependsOn: string[];
  generationDependsOn?: string[];
  ownedPaths: string[];
  deliverables: string[];
  validation: string[];
  status: string;
  state?: string;
  parallelSafe: boolean;
  description?: string;
  topologyRole?: string;
  capabilityRequirements?: string[];
  criterionIds?: string[];
  loopPolicy?: string[];
  supersedes?: string[];
  supersededBy?: string[];
}

interface BlueprintPlanContext {
  taskGraph: BlueprintPlanTask[];
  readyTaskIds: string[];
  deferredTaskIds: string[];
  assignedTaskIds: string[];
  activeTaskIds: string[];
  completedTaskIds: string[];
  parallelWorktreeTaskIds: string[];
  dependencyRevisions: string[];
  planFiles: string[];
  planRootRelative: string;
  graphRevision: number | null;
  graphVersionId: string;
  graphRootVersionId: string;
  graphBaseVersionId: string;
  graphParentVersionIds: string[];
  graphSource: string;
  graphUpdateReason: string;
  graphUpdateScope: string;
  graphChangedTaskIds: string[];
  graphChangedBranchIds: string[];
  parallelGroups: string[][];
  graphBranches: string[];
  graphBranchRefs: string[];
  planGenerationQueueLength: number;
  planExecutionQueueLength: number;
  taskExecutionQueueLength: number;
  canonicalBlueprint?: boolean;
  blueprintId?: string;
  blueprintTaskId?: string;
  blueprintRevisionId?: string;
  taskFamily?: TaskFamily;
  contract?: TaskBlueprintContractProjection;
  executionAttempts?: ExecutionAttemptProjection[];
  blueprintEdges?: LiveTaskGraphEdge[];
  entryNodeIds?: string[];
  terminalNodeIds?: string[];
  boundedLoopNodeIds?: string[];
  requiredCriterionIds?: string[];
  uncoveredCriterionIds?: string[];
}

interface BlueprintNode {
  id: string;
  title: string;
  detail: string;
  meta: string;
  body: string;
  previewBody?: string;
  rawRequest?: string;
  status: BlueprintNodeStatus;
  kind: BlueprintNodeKind;
  compact?: boolean;
  depth?: number;
  dependencyIds?: string[];
  graphTaskId?: string;
  parentGraphTaskId?: string;
  branchId?: string;
  graphContext?: BlueprintPlanContext;
  graphHistory?: BlueprintPlanContext[];
  sourceChunkId?: string;
  taskId?: string | null;
  runId?: string | null;
}

interface LiveTaskTreeItem {
  node: BlueprintNode;
  children: LiveTaskTreeItem[];
}

interface LiveTaskTreeSnapshot {
  id: string;
  title: string;
  status: BlueprintNodeStatus;
  children: LiveTaskTreeSnapshot[];
}

interface LiveTaskGraphBranch {
  id: string;
  label: string;
  nodes: BlueprintNode[];
}

interface LiveTaskGraphEdge {
  from: string;
  to: string;
  kind?: string;
  condition?: string;
  loopNodeId?: string;
}

interface LiveTaskGraphRevision {
  id: string;
  label: string;
  meta: string;
  reason: string;
  branches: LiveTaskGraphBranch[];
  planEdges: LiveTaskGraphEdge[];
}

interface SessionGroup {
  id: string;
  name: string;
  workspaceId: string | null;
  root: string;
  threads: ChatV2ThreadSummary[];
  defaultCollapsed?: boolean;
  archived?: boolean;
  subgroups?: SessionGroup[];
  unassigned?: boolean;
}

interface QueueRow {
  id: string;
  label: string;
  detail: string;
  rawDetail?: string;
  status: string;
  active: boolean;
  kind: "task" | "followup";
  lane: "task" | "append" | "continue_after_current";
  taskId?: string | null;
  runId?: string | null;
  sourceChunkId?: string;
}

function userChunkBody(chunk: WorkspaceChunk) {
  return chunk.kind === "chat" && chunk.role === "user" ? chunk.body.trim() : "";
}

function sentenceCaseQueueText(text: string) {
  const trimmed = text.replace(/\s+/g, " ").trim();
  if (!trimmed) return "";
  return `${trimmed.charAt(0).toUpperCase()}${trimmed.slice(1)}`;
}

function finishQueueSentence(text: string) {
  const trimmed = text.trim().replace(/\s+([,.!?])/g, "$1");
  if (!trimmed) return "";
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function cleanQueueRequestText(text: string) {
  return text
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^(?:oh|okay|ok|yeah|yes|right|hmm|um|uh)[,.\s]+/i, "")
    .replace(/^i\s+think\s+you(?:'re| are)\s+right[,.!\s]*/i, "")
    .replace(/^you(?:'re| are)\s+right[,.!\s]*/i, "")
    .replace(/^(?:but|and|also|then)\s+/i, "")
    .replace(/^(?:please\s+)?(?:can|could|would)\s+you\s+/i, "")
    .replace(/^(?:please\s+)?(?:try to|kindly|maybe)\s+/i, "")
    .replace(/\btry to\s+/gi, "")
    .replace(/\brevert back\b/gi, "review reverting")
    .replace(/\bdont\b/gi, "do not")
    .replace(/\bdan super biology\b/gi, "DAN Super Biology")
    .replace(/\bjust check\b[.?!]?$/i, "")
    .replace(/\s*,\s*$/g, "")
    .trim();
}

function joinQueueTargets(targets: string[]) {
  const unique = Array.from(new Set(targets.filter(Boolean)));
  if (unique.length <= 1) return unique[0] ?? "";
  if (unique.length === 2) return `${unique[0]} and ${unique[1]}`;
  return `${unique.slice(0, -1).join(", ")}, and ${unique[unique.length - 1]}`;
}

function specificQueueIntentSummary(rawText: string) {
  const text = rawText.toLowerCase();
  if (text.includes("git history") && /\brevert(?: back)?\b/.test(text)) {
    const targets: string[] = [];
    if (/\bdan super biology\b/i.test(rawText)) targets.push("DAN Super Biology");
    if (/\bmoving dots?\b/i.test(rawText) || /\bnetworks?\b/i.test(rawText)) {
      targets.push("moving dots/network states");
    }
    const targetText = joinQueueTargets(targets);
    return targetText
      ? `Check git history for earlier ${targetText}.`
      : "Check git history and identify a safer revert point.";
  }
  if (
    /\b(?:do not|don't|dont|no)\b[^.?!]*(?:edit|modify|change|write|touch)\b/i.test(rawText) &&
    /\bsummari[sz]e\b/i.test(rawText)
  ) {
    return "Summarize the project without editing files.";
  }
  return "";
}

function queueDisplayDetail(rawText: string, fallback = "Queued work") {
  const raw = rawText.trim();
  if (!raw) return fallback;
  const specific = specificQueueIntentSummary(raw);
  if (specific) return specific;
  let cleaned = cleanQueueRequestText(raw);
  cleaned = cleaned
    .replace(/^add\b/i, "Add")
    .replace(/^check\b/i, "Check")
    .replace(/^inspect\b/i, "Inspect")
    .replace(/^look at\b/i, "Inspect")
    .replace(/^summari[sz]e\b/i, "Summarize")
    .replace(/^use the\b/i, "Use a")
    .replace(/^use\b/i, "Use");
  cleaned = sentenceCaseQueueText(cleaned);
  if (!cleaned || normalizeComparableText(cleaned) === normalizeComparableText(raw)) {
    return fallback;
  }
  return finishQueueSentence(cleaned);
}

function attachQueueRowSourceChunks(queueRows: QueueRow[], chunks: WorkspaceChunk[]) {
  if (queueRows.length === 0 || chunks.length === 0) return queueRows;
  const assignedChunkIds = new Set(queueRows.map((row) => row.sourceChunkId).filter(Boolean));
  const pendingUserChunks = chunks.filter((chunk) => userChunkBody(chunk) && !chunk.runId);
  if (pendingUserChunks.length === 0) return queueRows;
  let changed = false;
  const rows = queueRows.map((row) => {
    if (row.sourceChunkId || row.kind !== "followup") return row;
    const detail = (row.rawDetail ?? row.detail).trim();
    if (!detail) return row;
    const match = [...pendingUserChunks]
      .reverse()
      .find((chunk) => !assignedChunkIds.has(chunk.id) && userChunkBody(chunk) === detail);
    if (!match) return row;
    assignedChunkIds.add(match.id);
    changed = true;
    return { ...row, sourceChunkId: match.id };
  });
  return changed ? rows : queueRows;
}

function queueSourceChunkIds(queueRows: QueueRow[]) {
  return new Set(queueRows.map((row) => row.sourceChunkId).filter(Boolean));
}

interface BlueprintTimelineNodeItem {
  kind: "node";
  id: string;
  node: BlueprintNode;
}

interface BlueprintTimelineConversationItem {
  kind: "conversation";
  id: string;
  chunk: WorkspaceChunk;
}

type BlueprintTimelineItem = BlueprintTimelineNodeItem | BlueprintTimelineConversationItem;

interface SessionSwipeState {
  key: string;
  pointerId: number;
  startX: number;
  thread: ChatV2ThreadSummary;
  archived: boolean;
}

interface LayoutPreferences {
  showSessionRail: boolean;
  showFileExplorer: boolean;
  showConversationChunks: boolean;
  showSidecarPreview: boolean;
  showNotesRail: boolean;
  showNoteEditor: boolean;
  showNotesPreview: boolean;
  showLearnPanel: boolean;
  leftRailWidth: number;
  rootPickerWidth: number;
  rootPickerHeight: number;
}

interface WorkspaceUiState {
  activePane: WorkspacePane;
  selectedChunkId: string | null;
  activeFilePath: string | null;
  threadQuery: string;
  noteQuery: string;
  noteFacet: string;
  noteFacetPage: string | null;
  noteArticleOriginFacet: string | null;
  noteRailView: NoteRailView;
  devFileQuery: string;
  expandedFileDirs: Record<string, boolean>;
  expandedNoteFolders: Record<string, boolean>;
  collapsedThreadGroups: Record<string, boolean>;
}

type DevFileStatus = "idle" | "loading" | "error" | "binary";

interface FileTreeNode extends WorkspaceFileEntry {
  children: FileTreeNode[];
}

interface NoteTreeNode {
  id: string;
  label: string;
  pathLabel: string;
  isFolder: boolean;
  note: WorkspaceNote | null;
  children: NoteTreeNode[];
}

interface PageIdSuggestion {
  pageID: string;
  title: string;
  path: string;
  section: string;
}

interface NoteMentionState {
  start: number;
  end: number;
  query: string;
}

interface RecentModifiedNote {
  id: string;
  note: WorkspaceNote;
  title: string;
  path: string;
  section: string;
  updatedAt: number;
  updatedLabel: string;
}

interface WorkingNoteCard {
  id: string;
  note: WorkspaceNote;
  title: string;
  state: string;
  path: string;
  target: string;
  snippet: string;
  status: NoteStatus | "agent";
}

const terminalTaskStatuses = new Set(["completed", "failed", "blocked", "stopped"]);
const terminalQueueStatuses = new Set(["completed", "done", "failed", "cancelled", "canceled", "stopped"]);
const STALE_RUNNING_TASK_MS = 24 * 60 * 60 * 1000;

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function railCardTone(active: boolean, dropTarget = false) {
  return active
    ? "border-slate-900 bg-white text-slate-950 shadow-sm dark:border-slate-100 dark:bg-slate-900 dark:text-slate-100"
    : dropTarget
      ? "border-sky-200 bg-sky-50 text-sky-900 ring-1 ring-sky-200 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-100 dark:ring-sky-900"
      : "border-transparent bg-slate-50 text-slate-600 hover:border-slate-200 hover:bg-white hover:shadow-sm dark:bg-slate-950/40 dark:text-slate-300 dark:hover:border-slate-800 dark:hover:bg-slate-900";
}

function PaneHeaderButton({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900 dark:hover:text-slate-200"
    >
      {children}
    </button>
  );
}

function CollapsedPaneRail({
  label,
  title,
  onClick,
  children,
  edge = "right",
  className,
}: {
  label: string;
  title: string;
  onClick: () => void;
  children: ReactNode;
  edge?: "left" | "right";
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className={cx(
        "dan-collapsed-pane-rail hidden min-h-0 w-full shrink-0 flex-col items-center justify-center gap-2 bg-white/70 text-slate-500 transition hover:bg-white hover:text-slate-900 dark:bg-slate-950/80 dark:hover:bg-slate-900 dark:hover:text-slate-100 md:flex",
        edge === "right"
          ? "border-r border-slate-200/80 dark:border-slate-800"
          : "border-l border-slate-200/80 dark:border-slate-800",
        className,
      )}
      style={{ width: COLLAPSED_PANE_WIDTH }}
    >
      <span className="dan-collapsed-pane-rail-icon grid h-7 w-7 place-items-center rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-950">
        {children}
      </span>
      <span className="rotate-180 whitespace-nowrap text-[10px] font-bold uppercase tracking-[0.16em] [writing-mode:vertical-rl]">
        {label}
      </span>
    </button>
  );
}

function clampLeftRailWidth(value: number) {
  if (!Number.isFinite(value)) return LEFT_RAIL_DEFAULT_WIDTH;
  return Math.min(LEFT_RAIL_MAX_WIDTH, Math.max(LEFT_RAIL_MIN_WIDTH, Math.round(value)));
}

function clampRootPickerWidth(value: number) {
  if (!Number.isFinite(value)) return ROOT_PICKER_DEFAULT_WIDTH;
  return Math.min(ROOT_PICKER_MAX_WIDTH, Math.max(ROOT_PICKER_MIN_WIDTH, Math.round(value)));
}

function clampRootPickerHeight(value: number) {
  if (!Number.isFinite(value)) return ROOT_PICKER_DEFAULT_HEIGHT;
  return Math.min(ROOT_PICKER_MAX_HEIGHT, Math.max(ROOT_PICKER_MIN_HEIGHT, Math.round(value)));
}

function usePhoneViewport() {
  const [isPhone, setIsPhone] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia("(max-width: 767px)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const update = () => setIsPhone(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return isPhone;
}

function wireGuardDisplay(status: WorkspaceWireGuardStatus | null) {
  if (!status) return { label: "WG", detail: "checking", tone: "unknown" };
  const label = status.mode === "alias-nywg" ? "WG nywg" : "WG";
  if (status.active) return { label, detail: "active", tone: "ok" };
  if (status.config_present) return { label, detail: "inactive", tone: "warn" };
  return { label, detail: "setup", tone: "unknown" };
}

function nowId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Keep readable progress phrases; drop raw event names such as "codex: turn.started". */
export function liveActionTextForTest(summary: string | undefined) {
  return liveActionText(summary);
}

function liveActionText(summary: string | undefined): string {
  const text = (summary ?? "").trim().replace(/[.…]+$/, "");
  if (!text || text.length > 90 || /^[\w-]+:\s*[\w.]+$/.test(text) || /^[a-z]+([._][a-z]+)+$/.test(text)) return "";
  return text;
}

function AutonomyIcon({ mode, size, className }: { mode: WorkspaceAutonomyMode; size: number; className?: string }) {
  if (mode === "plan") return <Shield size={size} className={className} />;
  if (mode === "full") return <ShieldOff size={size} className={className} />;
  return <WandSparkles size={size} className={className} />;
}

function makeMessage(role: ChatMessage["role"], content: string): ChatMessage {
  return {
    id: nowId(role),
    role,
    content,
    timestamp: Date.now(),
  };
}

function dataUrlFromFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read clipboard image"));
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
        return;
      }
      reject(new Error("Unexpected clipboard image read result"));
    };
    reader.readAsDataURL(file);
  });
}

function extensionForImageMimeType(mimeType?: string) {
  switch ((mimeType || "").toLowerCase()) {
    case "image/jpeg":
      return ".jpg";
    case "image/webp":
      return ".webp";
    case "image/gif":
      return ".gif";
    case "image/png":
    default:
      return ".png";
  }
}

function formatAttachmentSize(size?: number) {
  if (!Number.isFinite(size ?? NaN) || !size) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function workspaceAttachmentOnlyPrompt(attachments: ComposerAttachmentDraft[]) {
  if (attachments.length <= 1) return "Please review the attached screenshot.";
  return "Please review the attached screenshots.";
}

function workspaceAttachmentDisplayText(attachments: ComposerAttachmentDraft[]) {
  if (attachments.length <= 1) {
    const name = attachments[0]?.name || "Screenshot";
    return `Attached ${name}`;
  }
  return `Attached ${attachments.length} screenshots`;
}

function workspaceAttachmentPayload(attachment: ComposerAttachmentDraft): Record<string, unknown> {
  const name = resolveAttachmentName(attachment.name, attachment.mimeType);
  const path = typeof attachment.path === "string" && attachment.path.trim()
    ? attachment.path.trim()
    : "";
  return {
    id: attachment.id,
    kind: attachment.kind === "figure" ? "figure" : "document",
    name,
    display_name: name,
    path: path || null,
    local_path: path || null,
    mime_type: attachment.mimeType ?? null,
    size_bytes: attachment.size ?? null,
    caption: attachment.caption ?? null,
    source: attachment.source ?? null,
    ...(!path && attachment.dataUrl ? { data_url: attachment.dataUrl } : {}),
  };
}

function workspaceAttachmentPayloads(attachments: ComposerAttachmentDraft[]) {
  return attachments.map(workspaceAttachmentPayload);
}

function workspaceAttachmentSurfaceContext(attachments: ComposerAttachmentDraft[]) {
  if (!attachments.length) return {};
  return {
    attachment_count: attachments.length,
    appended_attachments: workspaceAttachmentPayloads(attachments),
  };
}

function titleFromText(text: string) {
  const compact = text.replace(/\s+/g, " ").trim();
  if (!compact) return "Workspace thread";
  return compact.length > 58 ? `${compact.slice(0, 55)}...` : compact;
}

function displayChatContent(content: string) {
  if (content.includes("surface conflicts with surface_id")) {
    return "Historical request failed because the workspace surface metadata was rejected.";
  }
  return formatStructuredAgentSummary(content) || normalizeStructuredMarkdown(content);
}

function fileName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function pathParts(path: string) {
  return path.split(/[\\/]/).map((part) => part.trim()).filter(Boolean);
}

function noteTitleFromPath(path: string) {
  return fileName(path).replace(/\.mdx?$/i, "") || "Note";
}

function joinPath(root: string, child: string) {
  const separator = root.includes("\\") ? "\\" : "/";
  return `${root.replace(/[\\/]+$/, "")}${separator}${child.replace(/^[\\/]+/, "")}`;
}

function parentPath(path: string) {
  const normalized = path.replace(/[\\/]+$/, "");
  const index = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  return index > 0 ? normalized.slice(0, index) : "";
}

function safeRelativePath(path: string) {
  return path
    .trim()
    .replace(/\\/g, "/")
    .replace(/^[\\/]+/, "")
    .replace(/[\\/]+$/, "");
}

function slugifyPathPart(value: string, fallback = "untitled") {
  const slug = value
    .trim()
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/['"]/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  return slug || fallback;
}

function compactDateStamp(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
    "-",
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
  ].join("");
}

function yamlQuote(value: string) {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function yamlStringList(values: string[]) {
  const clean = values.map((value) => value.trim()).filter(Boolean);
  return clean.length > 0 ? `[${clean.map(yamlQuote).join(", ")}]` : "[]";
}

function hugoFrontmatterTemplate(args: {
  title: string;
  pageID: string;
  date: string;
  author?: string;
  draft?: boolean;
  tags?: string[];
  categories?: string[];
}) {
  return [
    "---",
    `title: ${yamlQuote(args.title)}`,
    'subtitle: ""',
    `date: ${args.date}`,
    `lastmod: ${args.date}`,
    `draft: ${args.draft ? "true" : "false"}`,
    `author: ${yamlQuote(args.author || "Adam")}`,
    'abstract: ""',
    'summary: ""',
    'description: ""',
    'link: ""',
    `pageID: ${yamlQuote(args.pageID)}`,
    `tags: ${yamlStringList(args.tags ?? [])}`,
    `categories: ${yamlStringList(args.categories ?? [])}`,
    "series: []",
    "aliases: []",
    "images: []",
    "featured: false",
    "math: true",
    "toc: true",
    "---",
    "",
    "",
  ].join("\n");
}

function relativeFileLabel(path: string, root: string) {
  if (!root || !path.startsWith(root)) return path;
  return path.slice(root.length).replace(/^[\\/]+/, "") || fileName(path);
}

function noteRelativePath(note: WorkspaceNote, root: string) {
  if (note.relativePath) return note.relativePath;
  if (note.path) return relativeFileLabel(note.path, root);
  return note.title;
}

function noteDisplayPath(note: WorkspaceNote, root: string) {
  return isTemporaryDraftNote(note) ? "Uncategorized draft" : noteRelativePath(note, root);
}

function noteSection(note: WorkspaceNote, root: string) {
  if (isTemporaryDraftNote(note)) return note.draftTargetSection || "uncategorized";
  if (note.section) return note.section;
  const parts = pathParts(noteRelativePath(note, root));
  return parts.length > 1 ? parts[0] : "root";
}

function noteRoutePath(note: WorkspaceNote, root: string) {
  const parts = pathParts(noteRelativePath(note, root));
  if (parts.length === 0) return "/";
  const last = parts[parts.length - 1] ?? "";
  const routeParts = /^index\.mdx?$/i.test(last)
    ? parts.slice(0, -1)
    : [...parts.slice(0, -1), last.replace(/\.mdx?$/i, "")];
  return `/${routeParts.join("/")}${routeParts.length ? "/" : ""}`;
}

function isTemporaryDraftNote(note: WorkspaceNote) {
  return (
    note.temporary === true ||
    (note.source === "local" &&
      (note.path?.startsWith("tmp://notes/") ||
        note.relativePath?.startsWith("tmp/drafts/")))
  );
}

function isCatalogNote(note: WorkspaceNote) {
  return !isTemporaryDraftNote(note);
}

function catalogNotes(notes: WorkspaceNote[]) {
  return notes.filter(isCatalogNote);
}

function isDraftPlaceholderTitle(title: string) {
  return /^(untitled|untitled draft|new note|draft)$/i.test(title.trim());
}

function frontmatterBodyStartOffset(content: string) {
  const match = /^---\s*\r?\n[\s\S]*?\r?\n---\s*(?:\r?\n|$)/.exec(content);
  return match ? match[0].length : 0;
}

function cleanDraftTitle(value: string) {
  const cleaned = value
    .replace(/^#+\s+/, "")
    .replace(/^[`*_~\s]+|[`*_~\s]+$/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[.?!:;,-]+$/g, "")
    .trim();
  if (!cleaned) return "";
  return `${cleaned.charAt(0).toUpperCase()}${cleaned.slice(1)}`.slice(0, 96);
}

function firstDraftBodyTitle(body: string) {
  const heading = /^#\s+(.+)$/m.exec(body)?.[1]?.trim() || "";
  if (heading) return cleanDraftTitle(heading);
  const snippet = compactNoteSnippet(body);
  const firstSentence = snippet.split(/[.!?]\s/)[0] ?? "";
  return cleanDraftTitle(firstSentence);
}

function notesComposerRequestsNewDraft(text: string) {
  return /^(?:please\s+)?(?:can you\s+)?(?:new|create|draft|write|generate|make)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:note|page|doc|document|file)\b/i.test(
    text.trim(),
  ) || /^(?:note|page|doc|document|file)\s*:\s*\S/i.test(text.trim());
}

function draftSeedFromComposerText(text: string) {
  const trimmed = text.trim();
  if (!trimmed) return { titleHint: "", body: "" };
  const colonMatch = /^(?:note|page|doc|document|file)\s*:\s*([\s\S]+)$/i.exec(trimmed);
  if (colonMatch) {
    const body = colonMatch[1].trim();
    return { titleHint: firstDraftBodyTitle(body), body };
  }
  const withoutCommand = trimmed
    .replace(
      /^(?:please\s+)?(?:can you\s+)?(?:new|create|draft|write|generate|make)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:note|page|doc|document|file)\b/i,
      "",
    )
    .trim();
  const remainder = withoutCommand.replace(/^(?:about|on|for|called|titled)\s+/i, "").trim();
  if (remainder.startsWith(":")) {
    const body = remainder.slice(1).trim();
    return { titleHint: firstDraftBodyTitle(body), body };
  }
  return { titleHint: cleanDraftTitle(remainder), body: "" };
}

function draftFacetMetadata(facet: string) {
  const [kind, ...rest] = facet.split(":");
  const value = rest.join(":").trim();
  if (!value) return { tags: [] as string[], categories: [] as string[] };
  if (kind === "tag") return { tags: [value], categories: [] as string[] };
  if (kind === "category") return { tags: [] as string[], categories: [value] };
  return { tags: [] as string[], categories: [] as string[] };
}

function draftTargetSectionFromContext(args: {
  facet?: string;
  activeSection?: string;
  fallback?: string;
}) {
  const facetSection =
    args.facet?.startsWith("section:") ? args.facet.slice("section:".length) : "";
  const raw = facetSection || args.activeSection || args.fallback || "notes";
  const section = safeRelativePath(raw);
  if (!section || section === "root" || section === "tmp") return "notes";
  return section.startsWith("tmp/") ? "notes" : section;
}

function pageIdFromRelativePath(relativePath: string, fallback: string) {
  const base = relativePath
    .replace(/\/index\.mdx?$/i, "")
    .replace(/\.mdx?$/i, "")
    .replace(/[\\/]+/g, "-")
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base || fallback;
}

function uniqueDraftRelativePath(args: {
  section: string;
  slug: string;
  notes: WorkspaceNote[];
  root: string;
  excludeId: string;
}) {
  const existing = new Set(
    args.notes
      .filter((note) => note.id !== args.excludeId)
      .map((note) => noteRelativePath(note, args.root).toLowerCase()),
  );
  for (let index = 0; index < 200; index += 1) {
    const folderSlug = index === 0 ? args.slug : `${args.slug}-${index + 1}`;
    const relativePath = `${args.section}/${folderSlug}/index.md`;
    if (!existing.has(relativePath.toLowerCase())) return relativePath;
  }
  return `${args.section}/${args.slug}-${Date.now()}/index.md`;
}

function createTemporaryDraftNote(args: {
  now?: Date;
  seedText?: string;
  targetSection?: string;
  facet?: string;
} = {}): WorkspaceNote {
  const now = args.now ?? new Date();
  const seed = draftSeedFromComposerText(args.seedText ?? "");
  const metadata = draftFacetMetadata(args.facet ?? "");
  const title = seed.titleHint || "Untitled draft";
  const stamp = compactDateStamp(now);
  const slug = slugifyPathPart(title, "untitled-draft");
  const content = `${hugoFrontmatterTemplate({
    title,
    pageID: "",
    date: now.toISOString(),
    draft: true,
    tags: metadata.tags,
    categories: metadata.categories,
  })}${seed.body ? `${seed.body.trim()}\n` : ""}`;
  return {
    id: nowId("draft-note"),
    title,
    path: `tmp://notes/${stamp}`,
    relativePath: `tmp/drafts/${slug}-${stamp}.md`,
    source: "local",
    content,
    loaded: true,
    status: "dirty",
    updatedAt: now.getTime(),
    size: content.length,
    section: "tmp",
    pageID: "",
    date: now.toISOString(),
    lastmod: now.toISOString(),
    draft: true,
    temporary: true,
    draftTargetSection: args.targetSection || "notes",
    tags: metadata.tags,
    categories: metadata.categories,
    citations: extractPageIdCitations(content),
  };
}

function materializeTemporaryDraftNote(args: {
  note: WorkspaceNote;
  notes: WorkspaceNote[];
  root: string;
  facet?: string;
  activeSection?: string;
  now?: Date;
}) {
  const root = normalizeRootPath(args.root);
  if (!root) return null;
  const now = args.now ?? new Date();
  const page = extractHugoPage(args.note.content || "", args.note.title);
  const body = page.body.trimStart();
  const inferredTitle =
    (page.meta.title && !isDraftPlaceholderTitle(page.meta.title) ? cleanDraftTitle(page.meta.title) : "") ||
    firstDraftBodyTitle(body) ||
    (args.note.title && !isDraftPlaceholderTitle(args.note.title) ? cleanDraftTitle(args.note.title) : "") ||
    "Untitled note";
  const section = draftTargetSectionFromContext({
    facet: args.facet,
    activeSection: args.note.draftTargetSection || args.activeSection,
    fallback: "notes",
  });
  const slug = slugifyPathPart(inferredTitle, "untitled-note");
  const relativePath = uniqueDraftRelativePath({
    section,
    slug,
    notes: args.notes,
    root,
    excludeId: args.note.id,
  });
  const pageID = page.meta.pageID || pageIdFromRelativePath(relativePath, `note-${compactDateStamp(now)}`);
  const facetMetadata = draftFacetMetadata(args.facet ?? "");
  const tags = page.meta.tags.length > 0 ? page.meta.tags : facetMetadata.tags;
  const categories =
    page.meta.categories.length > 0 ? page.meta.categories : facetMetadata.categories;
  const date = now.toISOString();
  const content = `${hugoFrontmatterTemplate({
    title: inferredTitle,
    pageID,
    date,
    draft: false,
    tags,
    categories,
  })}${body ? `${body.trim()}\n` : ""}`;
  return {
    title: inferredTitle,
    path: joinPath(root, relativePath),
    relativePath,
    section,
    pageID,
    date,
    content,
    tags,
    categories,
  };
}

function workspaceDisplayName(workspace: { name?: string; pinnedPaths?: string[] } | null | undefined) {
  const rootName = fileName(normalizeRootPath(workspace?.pinnedPaths?.[0] ?? ""));
  return rootName || workspace?.name || "Workspace";
}

function noteMovablePath(note: WorkspaceNote) {
  const path = note.path || note.relativePath || "";
  if (/^index\.mdx?$/i.test(fileName(path))) return parentPath(path) || path;
  return path;
}

function isGeneratedNoteBundleName(name: string) {
  const normalized = name.toLowerCase();
  return (
    /^note-\d{10,}$/.test(normalized) ||
    /^note-\d{8}-\d{6}$/.test(normalized) ||
    /^untitled-\d{8}-\d{6}$/.test(normalized) ||
    /^folder-\d{8}-\d{6}$/.test(normalized)
  );
}

function noteTitleBundleMove(note: WorkspaceNote) {
  const path = note.path || "";
  if (!/^index\.mdx?$/i.test(fileName(path))) return null;
  const bundlePath = parentPath(path);
  const bundleName = fileName(bundlePath);
  if (!bundlePath || !isGeneratedNoteBundleName(bundleName)) return null;

  const title = extractHugoPage(note.content, "").meta.title.trim();
  const titleSlug = slugifyPathPart(title, "");
  if (!titleSlug || titleSlug === bundleName) return null;

  const destinationParent = parentPath(bundlePath);
  if (!destinationParent) return null;
  const destination = joinPath(destinationParent, titleSlug);
  if (destination === bundlePath) return null;
  return { source: bundlePath, destination };
}

function noteDropFolderPath(pathLabel: string) {
  if (/^index\.mdx?$/i.test(fileName(pathLabel))) return parentPath(pathLabel);
  return pathLabel;
}

function noteParentKeys(note: WorkspaceNote, root: string) {
  const parts = pathParts(noteRelativePath(note, root));
  const lastPart = parts[parts.length - 1] ?? "";
  const parentCount = lastPart.toLowerCase().match(/^index\.mdx?$/)
    ? Math.max(0, parts.length - 2)
    : Math.max(0, parts.length - 1);
  const keys: string[] = [];
  for (let index = 0; index < parentCount; index += 1) {
    keys.push(parts.slice(0, index + 1).join("/"));
  }
  return keys;
}

function stripYamlComment(value: string) {
  let inQuote: "'" | '"' | "" = "";
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    const previous = value[index - 1];
    if ((char === "'" || char === '"') && previous !== "\\") {
      inQuote = inQuote === char ? "" : inQuote || char;
    }
    if (char === "#" && !inQuote && (index === 0 || /\s/.test(value[index - 1] ?? ""))) {
      return value.slice(0, index).trim();
    }
  }
  return value.trim();
}

function parseYamlScalar(value: string): string | boolean | string[] {
  const cleaned = stripYamlComment(value);
  if (!cleaned) return "";
  if (/^(true|false)$/i.test(cleaned)) return cleaned.toLowerCase() === "true";
  if (cleaned.startsWith("[") && cleaned.endsWith("]")) {
    const inner = cleaned.slice(1, -1).trim();
    if (!inner) return [];
    return inner
      .split(",")
      .map((item) => stripYamlComment(item).replace(/^['"]|['"]$/g, "").trim())
      .filter(Boolean);
  }
  return cleaned.replace(/^['"]|['"]$/g, "").trim();
}

function parseYamlFrontmatter(raw: string) {
  const data: Record<string, string | boolean | string[]> = {};
  let activeListKey = "";
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const listMatch = /^\s*-\s+(.+?)\s*$/.exec(line);
    if (listMatch && activeListKey) {
      const current = data[activeListKey];
      const next = parseYamlScalar(listMatch[1]);
      const item = Array.isArray(next) ? next.join(", ") : String(next);
      data[activeListKey] = [...(Array.isArray(current) ? current : []), item];
      continue;
    }
    const match = /^([A-Za-z0-9_-]+):\s*(.*?)\s*$/.exec(line);
    if (!match) continue;
    const key = match[1];
    const rawValue = match[2] ?? "";
    activeListKey = rawValue ? "" : key;
    data[key] = rawValue ? parseYamlScalar(rawValue) : [];
  }
  return data;
}

function metaString(data: Record<string, string | boolean | string[]>, key: string) {
  const value = data[key];
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "true" : "false";
  return typeof value === "string" ? value : "";
}

function metaList(data: Record<string, string | boolean | string[]>, key: string) {
  const value = data[key];
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value.trim()) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function formatHugoDate(value: string) {
  if (!value) return "";
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatNoteUpdatedDate(note: WorkspaceNote | null, lastmod: string) {
  const frontmatterDate = formatHugoDate(lastmod);
  if (frontmatterDate) return frontmatterDate;
  if (!note?.updatedAt) return "";
  const parsed = new Date(note.updatedAt);
  if (!Number.isFinite(parsed.getTime())) return "";
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function extractHugoPage(content: string, fallbackTitle: string): ParsedHugoPage {
  const match = /^---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)/.exec(content);
  const data = match ? parseYamlFrontmatter(match[1]) : {};
  const body = match ? content.slice(match[0].length).trimStart() : content;
  const headingTitle = /^#\s+(.+)$/m.exec(body)?.[1]?.trim() || "";
  return {
    body,
    hasFrontmatter: Boolean(match),
    meta: {
      title: metaString(data, "title") || headingTitle || fallbackTitle,
      subtitle: metaString(data, "subtitle"),
      abstract: metaString(data, "abstract"),
      date: metaString(data, "date"),
      lastmod: metaString(data, "lastmod"),
      author: metaString(data, "author"),
      pageID: metaString(data, "pageID"),
      link: metaString(data, "link"),
      figure: metaString(data, "figure"),
      layout: metaString(data, "layout"),
      math: metaString(data, "math"),
      draft: metaString(data, "draft"),
      tags: metaList(data, "tags"),
      categories: metaList(data, "categories"),
    },
  };
}

function escapeHugoPreviewHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function calloutToneClasses(kind: string) {
  switch (kind.toLowerCase()) {
    case "warning":
    case "caution":
      return {
        shell:
          "border-amber-300/80 bg-amber-50/90 text-amber-950 dark:border-amber-700/80 dark:bg-amber-950/35 dark:text-amber-100",
        header:
          "border-amber-200/80 bg-amber-100/70 text-amber-900 dark:border-amber-800/80 dark:bg-amber-900/35 dark:text-amber-100",
        marker:
          "border-amber-500/70 bg-amber-200 text-amber-950 dark:border-amber-500/70 dark:bg-amber-700/40 dark:text-amber-100",
      };
    case "danger":
    case "error":
      return {
        shell:
          "border-red-300/80 bg-red-50/90 text-red-950 dark:border-red-800/80 dark:bg-red-950/35 dark:text-red-100",
        header:
          "border-red-200/80 bg-red-100/70 text-red-900 dark:border-red-800/80 dark:bg-red-900/35 dark:text-red-100",
        marker:
          "border-red-500/70 bg-red-200 text-red-950 dark:border-red-500/70 dark:bg-red-700/40 dark:text-red-100",
      };
    default:
      return {
        shell:
          "border-slate-300/80 bg-slate-50/90 text-slate-900 dark:border-slate-700/80 dark:bg-slate-900/60 dark:text-slate-100",
        header:
          "border-slate-200/80 bg-slate-100/70 text-slate-800 dark:border-slate-700/80 dark:bg-slate-800/60 dark:text-slate-100",
        marker:
          "border-slate-400/70 bg-slate-200 text-slate-900 dark:border-slate-500/70 dark:bg-slate-700 dark:text-slate-100",
      };
  }
}

function formatHugoPreviewBody(body: string) {
  const formatCallout = (_match: string, rawArgs: string, inner: string) => {
    const args = String(rawArgs || "").trim();
    const titleMatch = /"([^"]+)"/.exec(args);
    const title = titleMatch?.[1]?.trim() || "";
    const variant = args.replace(/"[^"]*"/g, "").trim().split(/\s+/)[0] || "note";
    const variantLabel = variant && variant !== "note" ? variant.charAt(0).toUpperCase() + variant.slice(1) : "";
    const safeKind = /^[a-z0-9_-]+$/i.test(variant) ? variant.toLowerCase() : "note";
    const label = [variantLabel, title].filter(Boolean).join(": ") || "Note";
    const tone = calloutToneClasses(safeKind);
    const bodyHtml = String(inner || "")
      .trim()
      .split(/\n{2,}/)
      .map((paragraph) => paragraph.replace(/\s*\r?\n\s*/g, " ").trim())
      .filter(Boolean)
      .map(
        (paragraph) =>
          `<p class="m-0 leading-7 text-current">${escapeHugoPreviewHtml(paragraph)}</p>`,
      )
      .join("");
    return (
      `<aside class="dan-markdown-callout my-4 overflow-hidden rounded-md border shadow-sm ${tone.shell}" data-callout-kind="${escapeHugoPreviewHtml(safeKind)}">` +
      `<div class="dan-markdown-callout-header flex items-center gap-2 border-b px-3 py-2 ${tone.header}">` +
      `<span class="dan-markdown-callout-marker grid h-5 w-5 flex-none place-items-center rounded border text-[11px] font-bold leading-none ${tone.marker}">!</span>` +
      `<span class="text-sm font-semibold leading-5">${escapeHugoPreviewHtml(label)}</span>` +
      `</div>` +
      `<div class="dan-markdown-callout-body space-y-2 px-3 py-3 text-base leading-7">${bodyHtml || `<p class="m-0 leading-7 text-current">No callout content.</p>`}</div>` +
      `</aside>`
    );
  };

  return body
    .replace(/{{<\s*summary\s+"([^"]+)"\s*>}}/g, (_, pageId) =>
      `> Summary transclusion: @${pageId}`,
    )
    .replace(/{{<\s*callout\b([^>]*)>}}([\s\S]*?){{<\s*\/callout\s*>}}/g, formatCallout)
    .replace(/{{<\s*([^>\s]+)([\s\S]*?)>}}/g, (_, shortcode, args) =>
      `\`${shortcode}${String(args || "").trim() ? ` ${String(args).trim()}` : ""}\``,
    )
    .replace(/(^|[\s(])@([A-Za-z0-9][A-Za-z0-9_-]+)/g, "$1[@$2](#$2)");
}

type HugoPreviewBodyBlock =
  | { kind: "markdown"; content: string }
  | { kind: "paper-pdf"; filename: string; heightPx: number };

function hugoShortcodeNamedArgument(args: string, name: string) {
  const match = new RegExp(
    `(?:^|\\s)${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s]+))`,
    "i",
  ).exec(args);
  return (match?.[1] ?? match?.[2] ?? match?.[3] ?? "").trim();
}

function hugoPaperPdfHeight(value: string) {
  const match = /^(\d{2,4})(?:px)?$/i.exec(value.trim());
  if (!match) return 800;
  return Math.min(1200, Math.max(360, Number(match[1])));
}

function splitHugoPreviewBody(body: string): HugoPreviewBodyBlock[] {
  const blocks: HugoPreviewBodyBlock[] = [];
  const shortcode = /{{<\s*paperPDF\b([^>]*)>}}/gi;
  let cursor = 0;

  for (const match of body.matchAll(shortcode)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      blocks.push({ kind: "markdown", content: body.slice(cursor, start) });
    }

    const filename = hugoShortcodeNamedArgument(match[1] || "", "filename");
    if (/^[A-Za-z0-9][A-Za-z0-9._-]*\.pdf$/i.test(filename)) {
      blocks.push({
        kind: "paper-pdf",
        filename,
        heightPx: hugoPaperPdfHeight(
          hugoShortcodeNamedArgument(match[1] || "", "height"),
        ),
      });
    } else {
      blocks.push({ kind: "markdown", content: match[0] });
    }
    cursor = start + match[0].length;
  }

  if (cursor < body.length) {
    blocks.push({ kind: "markdown", content: body.slice(cursor) });
  }
  return blocks.length > 0 ? blocks : [{ kind: "markdown", content: body }];
}

function knowledgeBaseRootFromNotesRoot(notesRoot: string) {
  const normalized = notesRoot.trim().replace(/[\\/]+$/, "");
  const match = /^(.*)[\\/]content(?:[\\/].*)?$/i.exec(normalized);
  return match?.[1] || "";
}

function hugoPaperPdfPreviewUrl(notesRoot: string, filename: string) {
  const knowledgeBaseRoot = knowledgeBaseRootFromNotesRoot(notesRoot);
  if (!knowledgeBaseRoot) return "";
  const relativePath = `static/papers/${filename}`;
  return workspaceFilePreviewUrl(
    joinPath(knowledgeBaseRoot, relativePath),
    knowledgeBaseRoot,
    relativePath,
  );
}

function HugoNotePreviewBody({
  body,
  notesRoot,
  renderMathCodeSpans,
  onClick,
}: {
  body: string;
  notesRoot: string;
  renderMathCodeSpans: boolean;
  onClick: (event: MouseEvent<HTMLDivElement>) => void;
}) {
  const blocks = useMemo(() => splitHugoPreviewBody(body), [body]);

  return (
    <div className="space-y-4">
      {blocks.map((block, index) => {
        if (block.kind === "markdown") {
          const content = formatHugoPreviewBody(block.content);
          if (!content.trim()) return null;
          return (
            <MarkdownRenderer
              key={`markdown:${index}`}
              content={content}
              renderMathCodeSpans={renderMathCodeSpans}
              onClick={onClick}
            />
          );
        }

        const previewUrl = hugoPaperPdfPreviewUrl(notesRoot, block.filename);
        if (!previewUrl) {
          return (
            <MarkdownRenderer
              key={`paper-pdf-fallback:${index}`}
              content={`\`paperPDF filename="${block.filename}"\``}
            />
          );
        }
        const documentUrl = `${previewUrl}#view=FitH`;
        return (
          <section
            key={`paper-pdf:${block.filename}:${index}`}
            className="my-5 overflow-hidden rounded-md border border-slate-300 bg-slate-100/70 shadow-sm dark:border-slate-700 dark:bg-slate-900/70"
          >
            <div className="flex h-10 items-center justify-between gap-3 border-b border-slate-300 bg-slate-50 px-3 dark:border-slate-700 dark:bg-slate-900">
              <div className="flex min-w-0 items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-200">
                <FileText size={14} className="shrink-0 text-slate-500" aria-hidden="true" />
                <span className="truncate">{block.filename}</span>
              </div>
              <a
                href={documentUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="grid h-7 w-7 shrink-0 place-items-center rounded border border-slate-300 bg-white text-slate-500 transition hover:border-slate-400 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-400 dark:hover:text-slate-100"
                title="Open PDF"
                aria-label={`Open ${block.filename}`}
              >
                <Maximize2 size={13} aria-hidden="true" />
              </a>
            </div>
            <iframe
              src={documentUrl}
              title={block.filename}
              className="block w-full bg-slate-200 dark:bg-slate-950"
              style={{ height: `${block.heightPx}px` }}
            />
          </section>
        );
      })}
    </div>
  );
}

function noteModifiedTimestamp(note: WorkspaceNote) {
  return Number.isFinite(note.updatedAt) ? note.updatedAt : 0;
}

function formatRecentModifiedLabel(timestamp: number) {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return "No timestamp";
  const parsed = new Date(timestamp);
  if (!Number.isFinite(parsed.getTime())) return "No timestamp";
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function recentModifiedNotes(
  notes: WorkspaceNote[],
  root: string,
  limit = RECENT_MODIFIED_LIMIT,
): RecentModifiedNote[] {
  return catalogNotes(notes)
    .map((note) => {
      const updatedAt = noteModifiedTimestamp(note);
      return {
        id: note.id,
        note,
        title: note.title,
        path: noteRelativePath(note, root),
        section: noteSection(note, root),
        updatedAt,
        updatedLabel: formatRecentModifiedLabel(updatedAt),
      };
    })
    .sort(
      (a, b) =>
        b.updatedAt - a.updatedAt ||
        a.path.localeCompare(b.path, undefined, { sensitivity: "base" }),
    )
    .slice(0, Math.max(0, limit));
}

function compactNoteSnippet(value: string) {
  return value
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]+\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[\s>*+-]+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

function noteWorkingState(note: WorkspaceNote | null, activeTask: ChatV2TaskSnapshot | null) {
  if (!note) return null;
  if (note.status === "dirty") return "Editing";
  if (note.status === "saving") return "Saving";
  if (note.status === "loading") return "Loading";
  if (note.status === "error") return "Needs attention";
  if (activeTask) return "Agent";
  return null;
}

function noteWorkingTargetAndSnippet(
  note: WorkspaceNote,
  state: string,
  activeTask: ChatV2TaskSnapshot | null,
) {
  if (note.status === "error") {
    return {
      target: "page",
      snippet: note.error || "The selected page needs attention.",
    };
  }
  if (activeTask && state === "Agent") {
    return {
      target: "agent run",
      snippet:
        compactNoteSnippet(activeTask.latest_progress || activeTask.phase || "") ||
        "Super DAN is working in the Notes workspace.",
    };
  }
  const page = extractHugoPage(note.content || "", note.title);
  const bodySnippet = compactNoteSnippet(page.body);
  if (bodySnippet) {
    return {
      target: "body",
      snippet: bodySnippet,
    };
  }
  const frontmatterSnippet = compactNoteSnippet(
    [page.meta.title, page.meta.subtitle, page.meta.abstract, page.meta.pageID]
      .filter(Boolean)
      .join(" · "),
  );
  return {
    target: "frontmatter",
    snippet: frontmatterSnippet || note.title || "No note text loaded yet.",
  };
}

function workingNoteCards(
  note: WorkspaceNote | null,
  root: string,
  activeTask: ChatV2TaskSnapshot | null = null,
): WorkingNoteCard[] {
  const state = noteWorkingState(note, activeTask);
  if (!note || !state) return [];
  const { target, snippet } = noteWorkingTargetAndSnippet(note, state, activeTask);
  return [
    {
      id: note.id,
      note,
      title: note.title,
      state,
      path: noteDisplayPath(note, root),
      target,
      snippet,
      status: state === "Agent" ? "agent" : note.status,
    },
  ];
}

function extractPageIdCitations(content: string) {
  const seen = new Set<string>();
  const citations: string[] = [];
  for (const match of content.matchAll(/(^|[\s([{"'])@([A-Za-z0-9][A-Za-z0-9_-]{2,})/g)) {
    const pageID = match[2];
    if (!seen.has(pageID)) {
      seen.add(pageID);
      citations.push(pageID);
    }
  }
  return citations;
}

function noteMetaItems(note: WorkspaceNote | null, page: ParsedHugoPage) {
  const meta = page.meta;
  return [
    ["Date", formatHugoDate(meta.date)],
    ["Last Update", formatNoteUpdatedDate(note, meta.lastmod)],
    ["Author", meta.author],
    ["PageID", meta.pageID],
    ["Link", meta.link],
    ["Figure", meta.figure],
    ["Draft", meta.draft === "true" ? "draft" : ""],
    ["Path", note ? noteDisplayPath(note, "") : ""],
  ].filter((item): item is [string, string] => Boolean(item[1]));
}

function noteFacetKey(kind: "section" | "category" | "tag", value: string) {
  return `${kind}:${value}`;
}

function noteMatchesFacet(note: WorkspaceNote, root: string, facet: string) {
  if (facet === "all") return true;
  const [kind, ...rest] = facet.split(":");
  const value = rest.join(":");
  if (!value) return true;
  if (kind === "section") return noteSection(note, root) === value;
  if (kind === "category") return note.categories.includes(value);
  if (kind === "tag") return note.tags.includes(value);
  return true;
}

function noteFacetTitle(facet: string) {
  if (facet === "all") return "All content";
  const [kind, ...rest] = facet.split(":");
  const value = rest.join(":");
  if (!value) return "All content";
  if (kind === "section") return `Category · ${value}`;
  if (kind === "category") return `Category · ${value}`;
  if (kind === "tag") return `Tag · ${value}`;
  return value;
}

function noteRailViewForFacet(facet: string): NoteRailView | null {
  const [kind, ...rest] = facet.split(":");
  const value = rest.join(":");
  if (!value) return null;
  if (kind === "tag") return "tags";
  if (kind === "section" || kind === "category") return "sections";
  return null;
}

function noteCollectionFacet(facet: string) {
  return noteRailViewForFacet(facet) ? facet : null;
}

function noteArticleUpFacet(
  note: WorkspaceNote | null,
  root: string,
  originFacet: string | null,
) {
  const collectionOrigin = noteCollectionFacet(originFacet ?? "");
  if (collectionOrigin) return collectionOrigin;
  if (!note) return null;
  const section = noteSection(note, root);
  return section ? noteFacetKey("section", section) : null;
}

function noteCollectionTimestamp(note: WorkspaceNote) {
  const metadataTimestamp = Date.parse(note.lastmod || note.date || "");
  return Number.isFinite(metadataTimestamp) ? metadataTimestamp : note.updatedAt;
}

function sortNotesForCollection(
  notes: WorkspaceNote[],
  sort: NoteCollectionSort,
) {
  return [...notes].sort((a, b) => {
    if (sort === "title") {
      return (
        a.title.localeCompare(b.title, undefined, { sensitivity: "base" }) ||
        noteCollectionTimestamp(b) - noteCollectionTimestamp(a)
      );
    }
    return (
      noteCollectionTimestamp(b) - noteCollectionTimestamp(a) ||
      a.title.localeCompare(b.title, undefined, { sensitivity: "base" })
    );
  });
}

function relatedNoteCollectionFacets(
  notes: WorkspaceNote[],
  root: string,
  facet: string,
) {
  const kind = facet.split(":")[0];
  const relatedKind = kind === "tag" ? "section" : "tag";
  const counts = new Map<string, number>();
  for (const note of notes) {
    if (!noteMatchesFacet(note, root, facet)) continue;
    const values = relatedKind === "section" ? [noteSection(note, root)] : note.tags;
    for (const value of new Set(values.filter(Boolean))) {
      counts.set(value, (counts.get(value) ?? 0) + 1);
    }
  }
  return {
    kind: relatedKind as "section" | "tag",
    entries: [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
  };
}

function noteCollectionAccentColor(note: WorkspaceNote, root: string) {
  const value = noteSection(note, root) || "root";
  let hash = 0;
  for (const character of value) {
    hash = (hash * 31 + character.charCodeAt(0)) % 360;
  }
  return `hsl(${hash} 48% 48%)`;
}

function noteCollectionDescription(note: WorkspaceNote, root: string) {
  if (note.loaded && note.content.trim()) {
    const page = extractHugoPage(note.content, note.title);
    const summary = page.meta.subtitle || page.meta.abstract;
    if (summary) return compactNoteSnippet(summary);
  }
  return noteDisplayPath(note, root);
}

function pageIdFromNote(note: WorkspaceNote) {
  return note.pageID?.trim() || "";
}

function noteMentionAt(content: string, caret: number): NoteMentionState | null {
  const before = content.slice(0, caret);
  const match = /(^|[\s([{"'])@([A-Za-z0-9_-]*)$/.exec(before);
  if (!match) return null;
  const query = match[2] ?? "";
  return {
    start: caret - query.length - 1,
    end: caret,
    query,
  };
}

function noteParentPathLabel(note: WorkspaceNote, root: string) {
  const parts = pathParts(noteRelativePath(note, root));
  if (parts.length <= 1) return "";
  const lastPart = parts[parts.length - 1] ?? "";
  const folderParts = lastPart.toLowerCase().match(/^index\.mdx?$/)
    ? parts.slice(0, -2)
    : parts.slice(0, -1);
  return folderParts.join("/");
}

function normalizeRootPath(path: string) {
  const trimmed = path.trim();
  if (trimmed === "/" || /^[a-z]:[\\/]$/i.test(trimmed)) return trimmed;
  const normalized = trimmed.replace(/[\\/]+$/, "");
  return WORKSPACE_ROOT_ALIASES.get(normalized) ?? normalized;
}

function rootSuggestion(
  path: string,
  kind: WorkspaceRootSuggestion["kind"],
): WorkspaceRootSuggestion | null {
  const normalized = normalizeRootPath(path);
  if (!normalized) return null;
  return {
    path: normalized,
    name: fileName(normalized) || normalized,
    label: normalized,
    kind,
  };
}

function parentRootPath(path: string) {
  const normalized = normalizeRootPath(path);
  if (!normalized || normalized === "/" || /^[a-z]:[\\/]$/i.test(normalized)) return "";
  const slashIndex = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  if (slashIndex < 0) return "";
  if (slashIndex === 0) return "/";
  return normalized.slice(0, slashIndex);
}

function browsingRootPath(path: string) {
  const normalized = normalizeRootPath(path);
  if (!normalized) return "";
  if (normalized === "/" || /^[a-z]:[\\/]$/i.test(normalized)) return normalized;
  return `${normalized}/`;
}

function mergeRootSuggestions(
  ...groups: Array<Array<WorkspaceRootSuggestion | null | undefined>>
) {
  const seen = new Set<string>();
  const suggestions: WorkspaceRootSuggestion[] = [];
  for (const group of groups) {
    for (const suggestion of group) {
      if (!suggestion) continue;
      const path = normalizeRootPath(suggestion.path);
      if (!path || seen.has(path)) continue;
      seen.add(path);
      suggestions.push({ ...suggestion, path });
    }
  }
  return suggestions;
}

function workspaceIdForTask(
  task: ChatV2TaskSnapshot,
  workspaces: Array<{ id: string; pinnedPaths: string[] }>,
) {
  const rawTask = task as ChatV2TaskSnapshot & {
    workspace_id?: unknown;
    workspace_root?: unknown;
  };
  const workspaceId = textValue(task.metadata?.workspace_id) || textValue(rawTask.workspace_id);
  if (workspaceId && workspaces.some((item) => item.id === workspaceId)) return workspaceId;
  const roots = new Map(
    workspaces.flatMap((item) => {
      const root = normalizeRootPath(item.pinnedPaths[0] ?? "");
      return root ? [[root, item.id] as const] : [];
    }),
  );
  const workspaceRoot = workspaceRootForTask(task);
  if (workspaceRoot && roots.has(workspaceRoot)) return roots.get(workspaceRoot) ?? "";
  const idAsRoot = normalizeRootPath(workspaceId);
  return idAsRoot && roots.has(idAsRoot) ? roots.get(idAsRoot) ?? "" : "";
}

function workspaceRootForTask(task: ChatV2TaskSnapshot) {
  const rawTask = task as ChatV2TaskSnapshot & { workspace_root?: unknown };
  return normalizeRootPath(
    textValue(task.metadata?.workspace_root) || textValue(rawTask.workspace_root),
  );
}

function workspaceRootForWorkspaceId(
  workspaces: Array<{ id: string; pinnedPaths: string[] }>,
  workspaceId: string,
) {
  return normalizeRootPath(
    workspaces.find((item) => item.id === workspaceId)?.pinnedPaths[0] ?? "",
  );
}

function dominantTaskWorkspaceValue(
  tasks: ChatV2TaskSnapshot[],
  valueForTask: (task: ChatV2TaskSnapshot) => string,
) {
  const buckets = new Map<string, { count: number; newest: number; oldest: number }>();
  for (const task of tasks) {
    const value = valueForTask(task);
    if (!value) continue;
    const createdAt = timestampValue(taskRunStartedAt(task), Number.NaN);
    const updatedAt = timestampValue(taskRunUpdatedAt(task), Number.NaN);
    const newest = Number.isFinite(updatedAt)
      ? updatedAt
      : Number.isFinite(createdAt)
        ? createdAt
        : 0;
    const oldest = Number.isFinite(createdAt)
      ? createdAt
      : Number.isFinite(updatedAt)
        ? updatedAt
        : Number.MAX_SAFE_INTEGER;
    const bucket = buckets.get(value) ?? { count: 0, newest: 0, oldest: Number.MAX_SAFE_INTEGER };
    bucket.count += 1;
    bucket.newest = Math.max(bucket.newest, newest);
    bucket.oldest = Math.min(bucket.oldest, oldest);
    buckets.set(value, bucket);
  }
  return (
    [...buckets.entries()].sort(
      (a, b) =>
        b[1].count - a[1].count ||
        a[1].oldest - b[1].oldest ||
        b[1].newest - a[1].newest ||
        a[0].localeCompare(b[0]),
    )[0]?.[0] ?? ""
  );
}

function workspaceIdForTasks(
  tasks: ChatV2TaskSnapshot[],
  workspaces: Array<{ id: string; pinnedPaths: string[] }>,
) {
  return dominantTaskWorkspaceValue(tasks, (task) => workspaceIdForTask(task, workspaces));
}

function workspaceRootForTasks(tasks: ChatV2TaskSnapshot[]) {
  return dominantTaskWorkspaceValue(tasks, workspaceRootForTask);
}

function readStoredRoots() {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(ROOT_SUGGESTION_STORAGE_KEY) || "[]",
    ) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item) => {
      if (typeof item !== "string") return [];
      const path = normalizeRootPath(item);
      return path ? [path] : [];
    }).slice(0, 8);
  } catch {
    return [];
  }
}

function rememberRoot(previous: string[], root: string) {
  const normalized = normalizeRootPath(root);
  if (!normalized) return previous;
  const next = [normalized, ...previous.filter((item) => normalizeRootPath(item) !== normalized)]
    .slice(0, 8);
  try {
    window.localStorage.setItem(ROOT_SUGGESTION_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Root history is a convenience layer.
  }
  return next;
}

function threadWorkspaceKey(workflowId: string, threadId: string) {
  return `${workflowId}:${threadId}`;
}

function readStoredThreadWorkspaces() {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(THREAD_WORKSPACE_STORAGE_KEY) || "{}",
    ) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).flatMap(([key, value]) =>
        typeof value === "string" && key.includes(":") ? [[key, value]] : [],
      ),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}

function readStoredSessionResponseSeen() {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(SESSION_RESPONSE_SEEN_STORAGE_KEY) || "{}",
    ) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).flatMap(([key, value]) =>
        typeof value === "string" && key.includes(":") ? [[key, value]] : [],
      ),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}

function persistThreadWorkspaces(bindings: Record<string, string>) {
  try {
    window.localStorage.setItem(THREAD_WORKSPACE_STORAGE_KEY, JSON.stringify(bindings));
  } catch {
    // Workspace/session grouping stays local and best effort.
  }
}

function persistSessionResponseSeen(seen: Record<string, string>) {
  try {
    window.localStorage.setItem(SESSION_RESPONSE_SEEN_STORAGE_KEY, JSON.stringify(seen));
  } catch {
    // Response-read state is local and best effort.
  }
}

function readStoredLayout(): LayoutPreferences {
  const defaults: LayoutPreferences = {
    showSessionRail: true,
    showFileExplorer: false,
    showConversationChunks: true,
    showSidecarPreview: false,
    showNotesRail: true,
    showNoteEditor: true,
    showNotesPreview: true,
    showLearnPanel: false,
    leftRailWidth: LEFT_RAIL_DEFAULT_WIDTH,
    rootPickerWidth: ROOT_PICKER_DEFAULT_WIDTH,
    rootPickerHeight: ROOT_PICKER_DEFAULT_HEIGHT,
  };
  try {
    const saved = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    const legacy = JSON.parse(window.localStorage.getItem("dan.chunkWorkspace.layout.v2") || window.localStorage.getItem("dan.chunkWorkspace.layout.v1") || "{}");
    const parsed = (saved ? JSON.parse(saved) : { ...legacy, showSessionRail: true, showFileExplorer: false, showConversationChunks: true, showSidecarPreview: false }) as
      Partial<LayoutPreferences>;
    return {
      showSessionRail:
        typeof parsed.showSessionRail === "boolean"
          ? parsed.showSessionRail
          : defaults.showSessionRail,
      showFileExplorer:
        typeof parsed.showFileExplorer === "boolean"
          ? parsed.showFileExplorer
          : defaults.showFileExplorer,
      showConversationChunks:
        typeof parsed.showConversationChunks === "boolean"
          ? parsed.showConversationChunks
          : defaults.showConversationChunks,
      showSidecarPreview:
        typeof parsed.showSidecarPreview === "boolean"
          ? parsed.showSidecarPreview
          : defaults.showSidecarPreview,
      showNotesRail:
        typeof parsed.showNotesRail === "boolean"
          ? parsed.showNotesRail
          : defaults.showNotesRail,
      showNoteEditor:
        typeof parsed.showNoteEditor === "boolean"
          ? parsed.showNoteEditor
          : defaults.showNoteEditor,
      showNotesPreview:
        typeof parsed.showNotesPreview === "boolean"
          ? parsed.showNotesPreview
          : defaults.showNotesPreview,
      showLearnPanel:
        typeof parsed.showLearnPanel === "boolean"
          ? parsed.showLearnPanel
          : defaults.showLearnPanel,
      leftRailWidth:
        typeof parsed.leftRailWidth === "number"
          ? clampLeftRailWidth(parsed.leftRailWidth)
          : defaults.leftRailWidth,
      rootPickerWidth:
        typeof parsed.rootPickerWidth === "number"
          ? clampRootPickerWidth(parsed.rootPickerWidth)
          : defaults.rootPickerWidth,
      rootPickerHeight:
        typeof parsed.rootPickerHeight === "number"
          ? clampRootPickerHeight(parsed.rootPickerHeight)
          : defaults.rootPickerHeight,
    };
  } catch {
    return defaults;
  }
}

function persistLayout(layout: LayoutPreferences) {
  try {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // Layout persistence is best effort.
  }
}

function booleanRecord(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, item]) =>
      typeof item === "boolean" ? [[key, item]] : [],
    ),
  ) as Record<string, boolean>;
}

function readStoredUiState(): WorkspaceUiState {
  const defaults: WorkspaceUiState = {
    activePane: "work",
    selectedChunkId: null,
    activeFilePath: null,
    threadQuery: "",
    noteQuery: "",
    noteFacet: "all",
    noteFacetPage: null,
    noteArticleOriginFacet: null,
    noteRailView: "pages",
    devFileQuery: "",
    expandedFileDirs: {},
    expandedNoteFolders: {},
    collapsedThreadGroups: { "workspace:unassigned": true },
  };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(UI_STATE_STORAGE_KEY) || "{}") as
      Partial<WorkspaceUiState>;
    const storedNoteRailView = (parsed as { noteRailView?: unknown }).noteRailView;
    const storedNoteFacet = typeof parsed.noteFacet === "string" ? parsed.noteFacet : "all";
    const normalizedNoteRailView =
      storedNoteRailView === "tags" || storedNoteRailView === "sections"
        ? storedNoteRailView
        : storedNoteRailView === "categories"
          ? "sections"
          : "pages";
    const restoredFacetRailView = noteRailViewForFacet(storedNoteFacet);
    return {
      activePane: parsed.activePane === "notes" ? "notes" : defaults.activePane,
      selectedChunkId:
        typeof parsed.selectedChunkId === "string" ? parsed.selectedChunkId : null,
      activeFilePath:
        typeof parsed.activeFilePath === "string" ? parsed.activeFilePath : null,
      threadQuery: typeof parsed.threadQuery === "string" ? parsed.threadQuery : "",
      noteQuery: typeof parsed.noteQuery === "string" ? parsed.noteQuery : "",
      noteFacet: storedNoteFacet,
      noteFacetPage:
        typeof parsed.noteFacetPage === "string" &&
        parsed.noteFacetPage === storedNoteFacet
          ? noteCollectionFacet(parsed.noteFacetPage)
          : null,
      noteArticleOriginFacet:
        typeof parsed.noteArticleOriginFacet === "string"
          ? noteCollectionFacet(parsed.noteArticleOriginFacet)
          : null,
      noteRailView:
        normalizedNoteRailView === "pages" && restoredFacetRailView
          ? restoredFacetRailView
          : normalizedNoteRailView,
      devFileQuery: typeof parsed.devFileQuery === "string" ? parsed.devFileQuery : "",
      expandedFileDirs: booleanRecord(parsed.expandedFileDirs),
      expandedNoteFolders: booleanRecord(parsed.expandedNoteFolders),
      collapsedThreadGroups: {
        ...defaults.collapsedThreadGroups,
        ...booleanRecord(parsed.collapsedThreadGroups),
      },
    };
  } catch {
    return defaults;
  }
}

function persistUiState(state: WorkspaceUiState) {
  try {
    window.localStorage.setItem(UI_STATE_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // UI restore is a convenience layer.
  }
}

function projectLabelFromWorkflowId(workflowId: string) {
  const normalized = workflowId.trim();
  if (!normalized) return "Unknown Project";
  if (normalized === DEFAULT_WORKFLOW_ID || normalized === "_unassigned") return "Other chats";
  return fileName(normalized).replace(/[_-]+/g, " ");
}

function compactThreadTime(value: string) {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const ageMinutes = Math.max(0, Math.round((Date.now() - timestamp) / 60000));
  if (ageMinutes < 1) return "now";
  if (ageMinutes < 60) return `${ageMinutes}m`;
  if (ageMinutes < 1440) return `${Math.round(ageMinutes / 60)}h`;
  return `${Math.round(ageMinutes / 1440)}d`;
}

function compactElapsedDuration(milliseconds: number) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "";
  const seconds = Math.max(1, Math.round(milliseconds / 1000));
  if (seconds < 60) return "<1m";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function formatElapsedCounter(milliseconds: number) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "";
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const paddedSeconds = String(seconds).padStart(2, "0");
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${paddedSeconds}`;
  }
  return `${minutes}:${paddedSeconds}`;
}

function compactFileSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function workspaceFileCardMeta(entry: WorkspaceFileEntry) {
  if (entry.is_directory) return entry.parent || "folder";
  return [entry.parent || "top level", compactFileSize(entry.size)].filter(Boolean).join(" · ");
}

function noteCardMeta(note: WorkspaceNote, root: string) {
  const section = noteSection(note, root);
  const location = noteParentPathLabel(note, root) || (section === "root" ? "top level" : section);
  const status = note.status === "clean" ? "" : noteStatusText(note).toLowerCase();
  return [location, compactFileSize(note.size ?? 0), status].filter(Boolean).join(" · ");
}

function buildFileTree(entries: WorkspaceFileEntry[]): FileTreeNode[] {
  const nodes = new Map<string, FileTreeNode>();
  for (const entry of entries) nodes.set(entry.relative_path, { ...entry, children: [] });
  const roots: FileTreeNode[] = [];
  for (const node of nodes.values()) {
    if (node.parent && nodes.has(node.parent)) {
      nodes.get(node.parent)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const sortNodes = (items: FileTreeNode[]) => {
    items.sort((a, b) => {
      if (a.is_directory !== b.is_directory) return a.is_directory ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });
    items.forEach((item) => sortNodes(item.children));
  };
  sortNodes(roots);
  return roots;
}

function devFileByPath(nodes: FileTreeNode[], path: string): FileTreeNode | null {
  for (const node of nodes) {
    if (node.path === path) return node;
    const child = devFileByPath(node.children, path);
    if (child) return child;
  }
  return null;
}

function noteFolderLabel(part: string) {
  return part.replace(/[-_]+/g, " ").trim() || part;
}

function buildNoteTree(notes: WorkspaceNote[], root: string): NoteTreeNode[] {
  const roots: NoteTreeNode[] = [];
  const folders = new Map<string, NoteTreeNode>();

  const sortedNotes = [...notes].sort((a, b) =>
    noteRelativePath(a, root).localeCompare(noteRelativePath(b, root), undefined, {
      sensitivity: "base",
    }),
  );

  const ensureFolder = (
    part: string,
    key: string,
    children: NoteTreeNode[],
  ): NoteTreeNode => {
    const existing = folders.get(key);
    if (existing) return existing;
    const node: NoteTreeNode = {
      id: `folder:${key}`,
      label: noteFolderLabel(part),
      pathLabel: key,
      isFolder: true,
      note: null,
      children: [],
    };
    folders.set(key, node);
    children.push(node);
    return node;
  };

  for (const note of sortedNotes) {
    const relativePath = noteRelativePath(note, root);
    const parts = pathParts(relativePath);
    const lastPart = parts[parts.length - 1] ?? note.title;
    const isIndexNote = /^index\.mdx?$/i.test(lastPart) && parts.length > 1;
    const folderParts = isIndexNote ? parts.slice(0, -1) : parts.slice(0, -1);
    let children = roots;
    let folderNode: NoteTreeNode | null = null;

    for (let index = 0; index < folderParts.length; index += 1) {
      const key = folderParts.slice(0, index + 1).join("/");
      folderNode = ensureFolder(folderParts[index], key, children);
      children = folderNode.children;
    }

    if (isIndexNote && folderNode) {
      folderNode.note = note;
      folderNode.label = note.title || folderNode.label;
      folderNode.pathLabel = relativePath;
      continue;
    }

    children.push({
      id: note.id,
      label: note.title,
      pathLabel: relativePath,
      isFolder: false,
      note,
      children: [],
    });
  }

  const sortNodes = (items: NoteTreeNode[]) => {
    items.sort((a, b) => {
      if (a.isFolder !== b.isFolder) return a.isFolder ? -1 : 1;
      return a.label.localeCompare(b.label, undefined, { sensitivity: "base" });
    });
    items.forEach((item) => sortNodes(item.children));
  };
  sortNodes(roots);
  return roots;
}

function createLocalNote(title = "Scratch note"): WorkspaceNote {
  return {
    id: nowId("note"),
    title,
    source: "local",
    content: `# ${title}\n\n`,
    loaded: true,
    status: "clean",
    updatedAt: Date.now(),
    tags: [],
    categories: [],
    citations: [],
  };
}

function readStoredNotes(): { notes: WorkspaceNote[]; activeId: string | null } {
  try {
    const raw = window.localStorage.getItem(NOTES_STORAGE_KEY);
    if (!raw) {
      const note = createLocalNote("Workspace notes");
      return { notes: [note], activeId: note.id };
    }
    const parsed = JSON.parse(raw) as {
      notes?: Array<Partial<WorkspaceNote>>;
      activeId?: string;
    };
    const notes = (parsed.notes ?? []).flatMap((note) => {
      if (!note.id || !note.title) return [];
      return [
        {
          id: String(note.id),
          title: String(note.title),
          path: typeof note.path === "string" ? note.path : undefined,
          relativePath:
            typeof note.relativePath === "string" ? note.relativePath : undefined,
          source: (note.source === "disk" || note.source === "server"
            ? note.source
            : "local") as NoteSource,
          content:
            note.source === "disk" || note.source === "server"
              ? ""
              : typeof note.content === "string"
                ? note.content
                : "",
          loaded: note.source !== "disk" && note.source !== "server",
          status: "clean" as NoteStatus,
          updatedAt: typeof note.updatedAt === "number" ? note.updatedAt : Date.now(),
          size: typeof note.size === "number" ? note.size : undefined,
          section: typeof note.section === "string" ? note.section : undefined,
          layout: typeof note.layout === "string" ? note.layout : undefined,
          pageID: typeof note.pageID === "string" ? note.pageID : undefined,
          date: typeof note.date === "string" ? note.date : undefined,
          lastmod: typeof note.lastmod === "string" ? note.lastmod : undefined,
          draft: typeof note.draft === "boolean" ? note.draft : undefined,
          temporary: typeof note.temporary === "boolean" ? note.temporary : undefined,
          draftTargetSection:
            typeof note.draftTargetSection === "string"
              ? note.draftTargetSection
              : undefined,
          tags: Array.isArray(note.tags)
            ? note.tags.filter((item): item is string => typeof item === "string")
            : [],
          categories: Array.isArray(note.categories)
            ? note.categories.filter((item): item is string => typeof item === "string")
            : [],
          citations: Array.isArray(note.citations)
            ? note.citations.filter((item): item is string => typeof item === "string")
            : [],
          error: undefined,
        },
      ];
    });
    if (notes.length > 0) return { notes, activeId: parsed.activeId ?? notes[0].id };
  } catch {
    // Fall through to a fresh note.
  }
  const note = createLocalNote("Workspace notes");
  return { notes: [note], activeId: note.id };
}

function persistNotes(notes: WorkspaceNote[], activeId: string | null) {
  try {
    window.localStorage.setItem(
      NOTES_STORAGE_KEY,
      JSON.stringify({
        activeId,
        notes: notes.map((note) => ({
          id: note.id,
          title: note.title,
          path: note.path,
          relativePath: note.relativePath,
          source: note.source,
          content: note.source === "local" ? note.content : undefined,
          updatedAt: note.updatedAt,
          size: note.size,
          section: note.section,
          layout: note.layout,
          pageID: note.pageID,
          date: note.date,
          lastmod: note.lastmod,
          draft: note.draft,
          temporary: note.temporary,
          draftTargetSection: note.draftTargetSection,
          tags: note.tags,
          categories: note.categories,
          citations: note.citations,
        })),
      }),
    );
  } catch {
    // Local persistence is best effort.
  }
}

function readStoredNoteContentCache() {
  try {
    const parsed = JSON.parse(
      window.sessionStorage.getItem(NOTE_CONTENT_CACHE_STORAGE_KEY) || "{}",
    ) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).flatMap(([path, value]) => {
        if (!value || typeof value !== "object" || Array.isArray(value)) return [];
        const entry = value as Partial<NoteCacheEntry>;
        if (
          typeof entry.content !== "string" ||
          typeof entry.updatedAt !== "number" ||
          typeof entry.size !== "number"
        ) {
          return [];
        }
        return [[path, { content: entry.content, updatedAt: entry.updatedAt, size: entry.size }]];
      }),
    ) as Record<string, NoteCacheEntry>;
  } catch {
    return {};
  }
}

function persistNoteContentCache(cache: Record<string, NoteCacheEntry>) {
  try {
    const entries = Object.entries(cache)
      .sort((a, b) => b[1].updatedAt - a[1].updatedAt)
      .slice(0, 30);
    window.sessionStorage.setItem(
      NOTE_CONTENT_CACHE_STORAGE_KEY,
      JSON.stringify(Object.fromEntries(entries)),
    );
  } catch {
    // Browser cache is only a switching-speed optimization.
  }
}

function noteFromServerSummary(summary: WorkspaceNoteSummary): WorkspaceNote {
  return {
    id: `server:${summary.path}`,
    title: summary.title || noteTitleFromPath(summary.path),
    path: summary.path,
    relativePath: summary.relative_path,
    source: "server",
    content: "",
    loaded: false,
    status: "clean",
    updatedAt: Math.floor(summary.mtime * 1000),
    size: summary.size,
    section: summary.section,
    layout: summary.layout,
    pageID: summary.page_id,
    date: summary.date,
    lastmod: summary.lastmod,
    draft: summary.draft,
    tags: summary.tags ?? [],
    categories: summary.categories ?? [],
    citations: summary.citations ?? [],
  };
}

function eventSummary(event: ChatV2AgentRunEvent) {
  return event.summary || event.source_event_type || event.type || "Super DAN event";
}

function runEventPayloadFromAgentEvent(event: ChatV2AgentRunEvent): RunEventPayload {
  return {
    type: event.type,
    event_type: event.source_event_type || event.type,
    summary: eventSummary(event),
    detail: {
      run_id: event.run_id ?? "",
      task_id: event.task_id ?? "",
      payload: event.payload ?? {},
      artifact_refs: event.artifact_refs ?? [],
    },
  };
}

function agentRunEventIsTerminal(event: ChatV2AgentRunEvent) {
  return terminalTaskStatuses.has(event.type);
}

function taskRunId(task: ChatV2TaskSnapshot) {
  const runId = task.metadata?.active_run_id;
  return typeof runId === "string" ? runId : "";
}

function metadataObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function taskRunUpdatedAt(task: ChatV2TaskSnapshot) {
  return (
    textValue(task.metadata?.run_updated_at) ||
    textValue(task.metadata?.["_run_updated_at"]) ||
    textValue(task.metadata?.updated_at) ||
    textValue(task.metadata?.stop_requested_at) ||
    textValue(task.metadata?.thread_updated_at) ||
    textValue(task.metadata?.["_thread_updated_at"])
  );
}

function taskRunStartedAt(task: ChatV2TaskSnapshot) {
  return (
    textValue(task.metadata?.run_created_at) ||
    textValue(task.metadata?.["_run_created_at"]) ||
    textValue(task.metadata?.created_at) ||
    textValue(task.metadata?.thread_created_at) ||
    textValue(task.metadata?.["_thread_created_at"])
  );
}

function taskElapsedMilliseconds(task: ChatV2TaskSnapshot, now = Date.now()) {
  const startedAt = taskRunStartedAt(task);
  if (!startedAt) return 0;
  const start = timestampValue(startedAt, Number.NaN);
  if (!Number.isFinite(start)) return 0;
  const end = isTaskRunning(task) ? now : timestampValue(taskRunUpdatedAt(task), Number.NaN);
  if (!Number.isFinite(end) || end < start) return 0;
  return end - start;
}

function taskLastUpdateAge(task: ChatV2TaskSnapshot) {
  const updatedAt = taskRunUpdatedAt(task);
  return updatedAt ? compactThreadTime(updatedAt) : "";
}

function taskStopRequested(task?: ChatV2TaskSnapshot | null) {
  const metadata = task?.metadata ?? {};
  return Boolean(metadata.stop_requested || metadata.stop_requested_at || metadata.stop_command);
}

function taskIsStopControlState(task?: ChatV2TaskSnapshot | null) {
  return Boolean(task && (task.status === "stop_requested" || taskStopRequested(task)));
}

function taskIsStaleRunning(task?: ChatV2TaskSnapshot | null) {
  if (task?.status !== "running") return false;
  const updatedAt = taskRunUpdatedAt(task);
  if (!updatedAt) return false;
  const timestamp = new Date(updatedAt).getTime();
  return Number.isFinite(timestamp) && Date.now() - timestamp > STALE_RUNNING_TASK_MS;
}

function isTaskRunning(task?: ChatV2TaskSnapshot | null) {
  return Boolean(task?.status === "running" && !taskStopRequested(task) && !taskIsStaleRunning(task));
}

function isTaskTerminal(task?: ChatV2TaskSnapshot | null) {
  return Boolean(task && terminalTaskStatuses.has(task.status));
}

function agentRunEventSettlesTask(event: ChatV2AgentRunEvent) {
  const type = event.type.trim().toLowerCase();
  if (type === "failed" || type === "blocked" || type === "stopped") return true;
  if (type !== "completed") return false;
  const source = eventSource(event).trim().toLowerCase();
  return (
    source === "completed" ||
    /(?:^|\.)turn\.completed$/.test(source) ||
    /(?:^|\.)run\.completed$/.test(source) ||
    /(?:^|\.)run\.log\.completed$/.test(source)
  );
}

function agentRunEventMatchesTask(event: ChatV2AgentRunEvent, task: ChatV2TaskSnapshot) {
  const eventTaskId = event.task_id || "";
  if (eventTaskId && eventTaskId === task.task_id) return true;
  const eventRunId = event.run_id || "";
  return Boolean(eventRunId && eventRunId === taskRunId(task));
}

function settleTaskSnapshotsFromEvents(
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
) {
  if (tasks.length === 0 || events.length === 0) return tasks;
  let changed = false;
  const settled = tasks.map((task) => {
    if (isTaskTerminal(task)) return task;
    const terminalEvent = [...events]
      .reverse()
      .find((event) => agentRunEventSettlesTask(event) && agentRunEventMatchesTask(event, task));
    if (!terminalEvent) return task;
    changed = true;
    return applyRunEventToTaskSnapshot(task, {
      ...terminalEvent,
      task_id: terminalEvent.task_id || task.task_id,
      run_id: terminalEvent.run_id || taskRunId(task),
    });
  });
  return changed ? settled : tasks;
}

function taskNeedsAttention(task?: ChatV2TaskSnapshot | null) {
  return Boolean(
    task &&
      (["failed", "blocked", "stopped"].includes(task.status) ||
        taskStopRequested(task) ||
        taskIsStaleRunning(task)),
  );
}

function selectActiveRunningTask(tasks: ChatV2TaskSnapshot[]) {
  return tasks.find(isTaskRunning) ?? null;
}

function runningTaskMapByThreadId(tasks: ChatV2TaskSnapshot[]) {
  const entries = tasks
    .filter(isTaskRunning)
    .map((task) => [task.thread_id, task] as const);
  return new Map(entries);
}

function taskSnapshotTime(task: ChatV2TaskSnapshot) {
  return Math.max(
    timestampValue(taskRunUpdatedAt(task), 0),
    timestampValue(taskRunStartedAt(task), 0),
  );
}

function taskStatusRank(task: ChatV2TaskSnapshot) {
  if (isTaskTerminal(task)) return 4;
  if (isTaskRunning(task)) return 3;
  if (task.status === "needs_input" || task.status === "waiting_dependency") return 2;
  if (task.status === "queued") return 1;
  return 0;
}

function preferredTaskSnapshot(
  current: ChatV2TaskSnapshot | undefined,
  incoming: ChatV2TaskSnapshot,
) {
  if (!current) return incoming;
  const currentTime = taskSnapshotTime(current);
  const incomingTime = taskSnapshotTime(incoming);
  if (incomingTime !== currentTime) return incomingTime > currentTime ? incoming : current;
  const currentRank = taskStatusRank(current);
  const incomingRank = taskStatusRank(incoming);
  if (incomingRank !== currentRank) return incomingRank > currentRank ? incoming : current;
  return incoming;
}

function mergeTaskSnapshots(...groups: ChatV2TaskSnapshot[][]) {
  const byId = new Map<string, ChatV2TaskSnapshot>();
  for (const group of groups) {
    for (const task of group) {
      if (!task.task_id) continue;
      byId.set(task.task_id, preferredTaskSnapshot(byId.get(task.task_id), task));
    }
  }
  return [...byId.values()];
}

function applyRunEventToTaskSnapshot(
  task: ChatV2TaskSnapshot,
  event: ChatV2AgentRunEvent,
): ChatV2TaskSnapshot {
  const eventTaskId = event.task_id || "";
  if (!eventTaskId || task.task_id !== eventTaskId) return task;
  const progress = humanEventSummary(event) || event.summary || event.type;
  return {
    ...task,
    status: event.type || task.status,
    latest_progress: progress || task.latest_progress,
    blocker: event.type === "blocked" || event.type === "failed" ? progress || task.blocker : task.blocker,
    metadata: {
      ...(task.metadata ?? {}),
      ...(event.run_id ? { active_run_id: event.run_id, run_status: event.type } : {}),
      run_updated_at: new Date().toISOString(),
    },
  };
}

function applyRunEventToTaskSnapshots(
  tasks: ChatV2TaskSnapshot[],
  event: ChatV2AgentRunEvent,
) {
  if (!event.task_id) return tasks;
  let changed = false;
  const next = tasks.map((task) => {
    if (task.task_id !== event.task_id) return task;
    changed = true;
    return applyRunEventToTaskSnapshot(task, event);
  });
  return changed ? next : tasks;
}

function taskRequestText(task?: ChatV2TaskSnapshot | null) {
  if (!task) return "";
  const lastSurfaceTurn = metadataObject(task.metadata?.last_surface_turn);
  return (
    textValue(lastSurfaceTurn.text) ||
    textValue(task.metadata?.request_text) ||
    textValue(task.metadata?.prompt) ||
    textValue(task.metadata?.objective)
  );
}

function newestSessionTask(tasks: ChatV2TaskSnapshot[]) {
  return [...tasks].sort(
    (a, b) => timestampValue(taskRunUpdatedAt(b), 0) - timestampValue(taskRunUpdatedAt(a), 0),
  )[0] ?? null;
}

function sessionTaskStatusLabel(task?: ChatV2TaskSnapshot | null) {
  if (!task) return "";
  if (taskStopRequested(task)) return "stop requested";
  if (taskIsStaleRunning(task)) return "stale";
  if (taskNeedsAttention(task)) return task.status;
  if (task.status === "completed") return "done";
  if (task.status === "waiting_dependency") return "waiting";
  if (task.status === "needs_input") return "needs input";
  return task.status.replace(/_/g, " ");
}

function sessionElapsedWorkLabel(task: ChatV2TaskSnapshot | null | undefined, elapsedLabel: string) {
  if (!task || !elapsedLabel) return "";
  if (isTaskRunning(task)) return `working ${elapsedLabel}`;
  if (task.status === "queued" || task.status === "waiting_dependency") return "";
  return `worked ${elapsedLabel}`;
}

function taskGroupElapsedMilliseconds(tasks: ChatV2TaskSnapshot[], now = Date.now()) {
  return tasks.reduce((total, task) => total + taskElapsedMilliseconds(task, now), 0);
}

function taskGroupElapsedWorkLabel(tasks: ChatV2TaskSnapshot[]) {
  const statusTask = selectActiveRunningTask(tasks) ?? newestSessionTask(tasks);
  if (!statusTask) return "";
  const elapsed = taskGroupElapsedMilliseconds(tasks);
  return sessionElapsedWorkLabel(statusTask, compactElapsedDuration(elapsed));
}

function taskGroupElapsedCounter(tasks: ChatV2TaskSnapshot[], now = Date.now()): WorkElapsedCounter | null {
  const statusTask = selectActiveRunningTask(tasks) ?? newestSessionTask(tasks);
  if (!statusTask || statusTask.status === "queued" || statusTask.status === "waiting_dependency") {
    return null;
  }
  const value = formatElapsedCounter(taskGroupElapsedMilliseconds(tasks, now));
  if (!value) return null;
  return {
    value,
    active: isTaskRunning(statusTask),
  };
}

function sessionCardDisplay(thread: ChatV2ThreadSummary, tasks: ChatV2TaskSnapshot[]) {
  const latestTask = newestSessionTask(tasks);
  const title = thread.title?.trim() || "Untitled";
  const requestTitle = taskRequestText(latestTask);
  const displayTitle =
    title === "New Super DAN Session" && requestTitle ? titleFromText(requestTitle) : title;
  const kind = thread.mode === "agent" ? "Super DAN" : "Chat";
  const updated = compactThreadTime(thread.updated_at);
  const timeSuffix = updated ? ` · ${updated}` : "";
  if (tasks.length > 0) {
    const runLabel = `${tasks.length} ${tasks.length === 1 ? "run" : "runs"}`;
    const status = sessionTaskStatusLabel(latestTask);
    const workTime = taskGroupElapsedWorkLabel(tasks);
    return {
      title: displayTitle,
      detail: `${kind} · ${runLabel}${status ? ` · ${status}` : ""}${workTime ? ` · total ${workTime}` : ""}${timeSuffix}`,
    };
  }
  if (thread.mode === "agent" && thread.message_count === 0) {
    return {
      title: displayTitle,
      detail: `${kind} · No request yet${timeSuffix}`,
    };
  }
  return {
    title: displayTitle,
    detail: `${kind} · ${thread.message_count} ${thread.message_count === 1 ? "message" : "messages"}${timeSuffix}`,
  };
}

function taskReadyResponseSummary(task: ChatV2TaskSnapshot) {
  const result = metadataObject(task.metadata?.backend_result);
  const candidates = [
    ...detailItemsFromValue(task.latest_progress),
    ...detailItemsFromValue(task.metadata?.latest_summary),
    ...detailItemsForKeys(result, [
      "answer",
      "final_answer",
      "public_response",
      "final_response",
      "response",
      "summary",
      "change_summary",
      "completion_summary",
      "outcome",
      "message",
      "result_summary",
    ]),
  ];
  return candidates.find((item) => item && !isGenericCompletionText(item)) ?? "";
}

function sessionReadyResponseAt(tasks: ChatV2TaskSnapshot[]) {
  const completedWithResponse = tasks.filter(
    (task) => task.status === "completed" && taskReadyResponseSummary(task),
  );
  if (completedWithResponse.length === 0) return "";
  return completedWithResponse
    .map((task) => taskRunUpdatedAt(task))
    .filter(Boolean)
    .sort((a, b) => timestampValue(b, 0) - timestampValue(a, 0))[0] ?? "";
}

function sessionHasNewReadyResponse(tasks: ChatV2TaskSnapshot[], seenAt?: string) {
  const readyAt = sessionReadyResponseAt(tasks);
  if (!readyAt) return false;
  const readyTimestamp = timestampValue(readyAt, 0);
  const seenTimestamp = timestampValue(seenAt, 0);
  return readyTimestamp > 0 && readyTimestamp > seenTimestamp;
}

function shouldAutoRestoreSession(args: {
  activeThreadPresent: boolean;
  creatingSession: boolean;
  loadingThreadId?: string | null;
  restoringThreadId?: string | null;
  targetThreadId?: string | null;
  threadCount: number;
}) {
  return Boolean(
    !args.activeThreadPresent &&
      !args.creatingSession &&
      !args.loadingThreadId &&
      !args.restoringThreadId &&
      args.targetThreadId &&
      args.threadCount > 0,
  );
}

type ActiveThreadSelection = {
  id: string;
  workflowId: string;
  title?: string;
} | null;

type ThreadIdentity = {
  id: string;
  workflow_id?: string;
  workflowId?: string;
};

function threadIdentityWorkflowId(thread: ThreadIdentity) {
  return thread.workflow_id ?? thread.workflowId ?? "";
}

function composerDraftKey(thread: ThreadIdentity | null | undefined) {
  if (!thread?.id) return "";
  const workflowId = threadIdentityWorkflowId(thread) || DEFAULT_WORKFLOW_ID;
  return threadWorkspaceKey(workflowId, thread.id);
}

function composerDraftForThread(
  drafts: Record<string, string>,
  thread: ThreadIdentity | null | undefined,
) {
  const key = composerDraftKey(thread);
  return key ? drafts[key] ?? "" : "";
}

function composerDraftsWithValue(
  drafts: Record<string, string>,
  thread: ThreadIdentity | null | undefined,
  value: string,
) {
  const key = composerDraftKey(thread);
  if (!key) return drafts;
  if (value.length === 0) {
    if (!(key in drafts)) return drafts;
    const next = { ...drafts };
    delete next[key];
    return next;
  }
  if (drafts[key] === value) return drafts;
  return {
    ...drafts,
    [key]: value,
  };
}

function readStoredComposerDrafts() {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(COMPOSER_DRAFT_STORAGE_KEY) || "{}",
    ) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).flatMap(([key, value]) =>
        typeof value === "string" && key.includes(":") ? [[key, value]] : [],
      ),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}

function persistComposerDrafts(drafts: Record<string, string>) {
  try {
    window.localStorage.setItem(COMPOSER_DRAFT_STORAGE_KEY, JSON.stringify(drafts));
  } catch {
    // Composer drafts are a convenience layer.
  }
}

function savedThreadSelectionMatches(
  saved: { threadId?: string; workflowId?: string } | null,
  thread: ThreadIdentity,
) {
  if (!saved?.threadId || saved.threadId !== thread.id) return false;
  const workflowId = threadIdentityWorkflowId(thread);
  return !saved.workflowId || !workflowId || saved.workflowId === workflowId;
}

function threadMatchesTarget(
  thread: ChatV2ThreadSummary,
  targetThreadId?: string | null,
  targetWorkflowId?: string | null,
) {
  return Boolean(
    targetThreadId &&
      thread.id === targetThreadId &&
      (!targetWorkflowId || thread.workflow_id === targetWorkflowId),
  );
}

function findThreadTarget(
  threads: ChatV2ThreadSummary[],
  targetThreadId?: string | null,
  targetWorkflowId?: string | null,
) {
  return (
    threads.find((thread) => threadMatchesTarget(thread, targetThreadId, targetWorkflowId)) ??
    null
  );
}

function restorableThreadTarget(
  threads: ChatV2ThreadSummary[],
  targetThreadId?: string | null,
  targetWorkflowId?: string | null,
) {
  const match = findThreadTarget(threads, targetThreadId, targetWorkflowId);
  return match && !match.archived ? match : null;
}

function activeThreadArchivedSummary(
  selection: ActiveThreadSelection,
  threads: ChatV2ThreadSummary[],
) {
  if (!selection) return null;
  return (
    threads.find(
      (thread) =>
        thread.archived &&
        thread.id === selection.id &&
        thread.workflow_id === selection.workflowId,
    ) ?? null
  );
}

function buildSessionGroups(args: {
  threads: ChatV2ThreadSummary[];
  workspaces: Array<{
    id: string;
    name?: string;
    pinnedPaths: string[];
    activeThreadId?: string | null;
    openThreadIds: string[];
    removedFromDan?: boolean;
  }>;
  threadQuery: string;
  threadWorkspaces: Record<string, string>;
  taskWorkspaceByThreadId: Map<string, string>;
  taskWorkspaceRootByThreadId: Map<string, string>;
}) {
  const query = args.threadQuery.trim().toLowerCase();
  const workspaceGroups: SessionGroup[] = args.workspaces.filter((item) => !item.removedFromDan).map((item) => ({
    id: `workspace:${item.id}`,
    name: workspaceDisplayName(item),
    workspaceId: item.id,
    root: normalizeRootPath(item.pinnedPaths[0] ?? ""),
    threads: [],
  }));
  const groupByWorkspaceId = new Map(workspaceGroups.map((group) => [group.workspaceId, group]));
  const workspaceById = new Map(args.workspaces.map((workspace) => [workspace.id, workspace]));
  const workspaceOrder = new Map(args.workspaces.map((workspace, index) => [workspace.id, index]));
  const storedThreadGroupById = new Map<string, string>();
  for (const item of args.workspaces) {
    for (const threadId of [item.activeThreadId, ...item.openThreadIds]) {
      if (threadId) storedThreadGroupById.set(threadId, item.id);
    }
  }

  const projectGroups = new Map<string, SessionGroup>();
  const archivedGroups = new Map<string, SessionGroup>();
  const recoveredProjectRoot = (thread: ChatV2ThreadSummary) =>
    args.taskWorkspaceRootByThreadId.get(thread.id) ?? "";

  const workspaceIdMatchesRecoveredRoot = (workspaceId: string, recoveredRoot: string) => {
    if (!workspaceId || !recoveredRoot) return true;
    const workspaceRoot = normalizeRootPath(workspaceById.get(workspaceId)?.pinnedPaths[0] ?? "");
    return !workspaceRoot || workspaceRoot === recoveredRoot;
  };

  const resolvedWorkspaceId = (thread: ChatV2ThreadSummary) => {
    const recoveredRoot = recoveredProjectRoot(thread);
    const taskWorkspaceId = args.taskWorkspaceByThreadId.get(thread.id) ?? "";
    if (taskWorkspaceId && workspaceById.has(taskWorkspaceId) && workspaceIdMatchesRecoveredRoot(taskWorkspaceId, recoveredRoot)) {
      return taskWorkspaceId;
    }
    const storedWorkspaceId =
      args.threadWorkspaces[threadWorkspaceKey(thread.workflow_id, thread.id)] ??
      storedThreadGroupById.get(thread.id) ??
      "";
    if (storedWorkspaceId && workspaceById.has(storedWorkspaceId) && workspaceIdMatchesRecoveredRoot(storedWorkspaceId, recoveredRoot)) {
      return storedWorkspaceId;
    }
    if (workspaceById.has(thread.workflow_id) && workspaceIdMatchesRecoveredRoot(thread.workflow_id, recoveredRoot)) return thread.workflow_id;
    if (recoveredRoot) {
      const matches = args.workspaces.filter((item) => normalizeRootPath(item.pinnedPaths[0] ?? "") === normalizeRootPath(recoveredRoot));
      return (matches.find((item) => !item.removedFromDan) ?? matches[0])?.id ?? "";
    }
    return "";
  };

  const projectGroupFor = (thread: ChatV2ThreadSummary) => {
    const recoveredRoot = recoveredProjectRoot(thread);
    if (recoveredRoot) {
      const projectId = `project-root:${recoveredRoot}`;
      const projectGroup =
        projectGroups.get(projectId) ??
        ({
          id: projectId,
          name: fileName(recoveredRoot) || recoveredRoot,
          workspaceId: null,
          root: recoveredRoot,
          threads: [],
          defaultCollapsed: false,
        } satisfies SessionGroup);
      projectGroups.set(projectId, projectGroup);
      return projectGroup;
    }
    const workflowId = thread.workflow_id || "unknown";
    const projectId = `project:${workflowId}`;
    const projectGroup =
      projectGroups.get(projectId) ??
      ({
        id: projectId,
        name: workflowId === DEFAULT_WORKFLOW_ID || workflowId === "_unassigned"
          ? "Other chats" : `Project: ${projectLabelFromWorkflowId(workflowId)}`,
        workspaceId: null,
        root: workflowId === DEFAULT_WORKFLOW_ID || workflowId === "_unassigned" ? "" : workflowId,
        unassigned: workflowId === DEFAULT_WORKFLOW_ID || workflowId === "_unassigned",
        threads: [],
        defaultCollapsed: true,
      } satisfies SessionGroup);
    projectGroups.set(projectId, projectGroup);
    return projectGroup;
  };

  const archivedGroupFor = (thread: ChatV2ThreadSummary, workspaceId: string) => {
    const workspace = workspaceById.get(workspaceId);
    if (workspace) {
      const groupId = `archived:workspace:${workspace.id}`;
      const group =
        archivedGroups.get(groupId) ??
        ({
          id: groupId,
          name: workspaceDisplayName(workspace),
          workspaceId: workspace.id,
          root: workspace.pinnedPaths[0] ?? "",
          threads: [],
          defaultCollapsed: !query,
          archived: true,
        } satisfies SessionGroup);
      archivedGroups.set(groupId, group);
      return group;
    }
    const recoveredRoot = recoveredProjectRoot(thread);
    if (recoveredRoot) {
      const groupId = `archived:project-root:${recoveredRoot}`;
      const group =
        archivedGroups.get(groupId) ??
        ({
          id: groupId,
          name: fileName(recoveredRoot) || recoveredRoot,
          workspaceId: null,
          root: recoveredRoot,
          threads: [],
          defaultCollapsed: !query,
          archived: true,
        } satisfies SessionGroup);
      archivedGroups.set(groupId, group);
      return group;
    }
    const workflowId = thread.workflow_id || "unknown";
    const groupId = `archived:project:${workflowId}`;
    const group =
      archivedGroups.get(groupId) ??
      ({
        id: groupId,
        name: projectLabelFromWorkflowId(workflowId),
        workspaceId: null,
        root: workflowId === DEFAULT_WORKFLOW_ID || workflowId === "_unassigned" ? "" : workflowId,
        unassigned: workflowId === DEFAULT_WORKFLOW_ID || workflowId === "_unassigned",
        threads: [],
        defaultCollapsed: !query,
        archived: true,
      } satisfies SessionGroup);
    archivedGroups.set(groupId, group);
    return group;
  };

  for (const thread of args.threads) {
    const haystack = `${thread.title} ${thread.workflow_id} ${thread.id}`.toLowerCase();
    if (query && !haystack.includes(query)) continue;
    const workspaceId = resolvedWorkspaceId(thread);
    if (workspaceById.get(workspaceId)?.removedFromDan) continue;
    if (thread.archived) {
      archivedGroupFor(thread, workspaceId).threads.push(thread);
      continue;
    }
    const group = groupByWorkspaceId.get(workspaceId);
    if (group) {
      group.threads.push(thread);
      continue;
    }
    projectGroupFor(thread).threads.push(thread);
  }

  const visibleGroups = workspaceGroups.filter((group) => !query || group.threads.length > 0);
  const visibleProjectGroups = [...projectGroups.values()]
    .filter((group) => group.threads.length > 0)
    .sort((a, b) => b.threads.length - a.threads.length || a.name.localeCompare(b.name));
  const visibleArchivedGroups = [...archivedGroups.values()]
    .filter((group) => group.threads.length > 0)
    .sort((a, b) => {
      const aOrder = a.workspaceId
        ? workspaceOrder.get(a.workspaceId) ?? Number.MAX_SAFE_INTEGER
        : Number.MAX_SAFE_INTEGER;
      const bOrder = b.workspaceId
        ? workspaceOrder.get(b.workspaceId) ?? Number.MAX_SAFE_INTEGER
        : Number.MAX_SAFE_INTEGER;
      if (aOrder !== bOrder) return aOrder - bOrder;
      return b.threads.length - a.threads.length || a.name.localeCompare(b.name);
    });
  const archivedThreads = visibleArchivedGroups.flatMap((group) => group.threads);
  const archivedGroup =
    archivedThreads.length > 0
      ? [
          {
            id: "archived",
            name: "Archived",
            workspaceId: null,
            root: "",
            threads: archivedThreads,
            defaultCollapsed: !query,
            archived: true,
            subgroups: visibleArchivedGroups,
          } satisfies SessionGroup,
        ]
      : [];

  return visibleGroups.concat(visibleProjectGroups, archivedGroup);
}

function textValue(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function stringList(value: unknown) {
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === "string" ? item.trim() : ""))
      .filter(Boolean);
  }
  const text = textValue(value);
  return text ? [text] : [];
}

function parseJsonObject(text: string): Record<string, unknown> | null {
  let trimmed = text.trim();
  const fencedJson = trimmed.match(
    /^```[ \t]*(?:json)?[ \t]*(?:\r?\n)?([\s\S]*?)(?:\r?\n)?```[ \t]*$/i,
  );
  if (fencedJson) trimmed = fencedJson[1]?.trim() ?? "";
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function normalizeSummaryLine(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function statusLineFingerprint(value: string) {
  return normalizeSummaryLine(value)
    .replace(/[.。]+$/g, "")
    .toLowerCase();
}

function isDuplicateStatusLine(left: string, right: string) {
  const leftKey = statusLineFingerprint(left);
  const rightKey = statusLineFingerprint(right);
  return Boolean(leftKey && rightKey && leftKey === rightKey);
}

const FLATTENED_PIPE_TABLE_SEPARATOR_RE = /\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?/;

function hasFlattenedPipeTable(value: string) {
  return FLATTENED_PIPE_TABLE_SEPARATOR_RE.test(value);
}

function normalizePipeTableLine(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const framed = `${trimmed.startsWith("|") ? "" : "| "}${trimmed}${trimmed.endsWith("|") ? "" : " |"}`;
  return framed
    .replace(/\s*\|\s*/g, " | ")
    .replace(/^\s*\|\s*/, "| ")
    .replace(/\s*\|\s*$/, " |")
    .trim();
}

function normalizeFlattenedPipeTables(value: string) {
  if (!hasFlattenedPipeTable(value)) return value;

  const withRowBreaks = value
    .replace(/\|\|\s*(?=:?-{3,}:?\s*(?:\||$))/g, "|\n|")
    .replace(/\|\|\s*(?=\S)/g, "|\n| ")
    .replace(/[ \t]+(\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?)/g, "\n$1")
    .replace(/(\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?)[ \t]+(?=\|)/g, "$1\n")
    .replace(/\n{3,}/g, "\n\n");

  return withRowBreaks
    .split("\n")
    .map((line) => {
      const trimmed = line.trim();
      if (!trimmed.includes("|")) return line;
      if (!hasFlattenedPipeTable(withRowBreaks)) return line;
      if (
        FLATTENED_PIPE_TABLE_SEPARATOR_RE.test(trimmed) ||
        trimmed.startsWith("|") ||
        trimmed.split("|").length >= 3
      ) {
        return normalizePipeTableLine(trimmed);
      }
      return line;
    })
    .filter((line) => line.trim() !== "|")
    .join("\n");
}

function splitFlattenedMarkdownHeading(line: string) {
  const match = /^(#{2,6})\s+(.+)$/.exec(line.trim());
  if (!match) return line;
  const marker = match[1];
  const body = match[2].trim();
  if (!body || body.length < 48) return line;

  const tablePipeIndex = body.indexOf("|");
  if (tablePipeIndex > 8 && hasFlattenedPipeTable(body.slice(tablePipeIndex))) {
    const title = body.slice(0, tablePipeIndex).trim().replace(/[:;,.\s]+$/, "");
    const table = normalizeFlattenedPipeTables(body.slice(tablePipeIndex));
    if (title && table.trim()) return `${marker} ${title}\n\n${table}`;
  }

  const splitCandidates = [
    body.search(/\s+(?=[-*]\s+(?:\*\*)?(?:[A-Z]|`))/),
    body.search(/\s+(?=\d+\.\s+(?:\*\*)?(?:[A-Z]|`))/),
    body.search(
      /\s+(?=(?:This|That|The|It|They|There|DAN|Super DAN|Project|User|You|We|I)\b(?:\s+[a-z][a-z0-9-]*){0,3}\s+(?:is|are|was|were|has|have|will|can|should|needs|uses|includes|contains|remains|currently|now)\b)/,
    ),
  ].filter((index) => index > 8 && index < 96);

  const splitAt =
    splitCandidates.length > 0
      ? Math.min(...splitCandidates)
      : body.split(/\s+/).length > 12
        ? body.split(/\s+/).slice(0, 7).join(" ").length
        : -1;
  if (splitAt < 0) return line;

  const title = body.slice(0, splitAt).trim().replace(/[:;,.\s]+$/, "");
  const rest = body.slice(splitAt).trim();
  if (!title || !rest) return line;
  return `${marker} ${title}\n\n${rest}`;
}

function normalizeStructuredMarkdown(content: string) {
  const normalized = content
    .replace(/\r\n/g, "\n")
    .replace(/([^\n])\s+```([A-Za-z0-9_-]*)[ \t]+/g, "$1\n\n```$2\n")
    .replace(/([^\n])\s+```\s+(?=(?:#{2,6}\s|\d+[\.)]\s|[-*]\s|$))/g, "$1\n```\n\n")
    .replace(/\s*\*\*(Files|Risks|Checks):\*\*\s*/g, "\n\n### $1\n\n")
    .replace(/([^\n])\s+(#{2,6}\s+\S)/g, "$1\n\n$2")
    .split("\n")
    .map(splitFlattenedMarkdownHeading)
    .join("\n")
    .split("\n")
    .map(normalizeFlattenedPipeTables)
    .join("\n")
    .replace(/([^\n])\s+(\d+\.\s+(?=(?:\*\*)?(?:[A-Z]|`)))/g, "$1\n$2")
    .replace(/([^\n])\s+-\s+(?=(?:\*\*)?(?:[A-Z]|`))/g, "$1\n- ")
    .replace(/^(#{2,6}\s+[^\n]+)\n(?!\n)/gm, "$1\n\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return normalized || content;
}

const INTERNAL_CONTEXT_ECHO_RE =
  /(?:^|\s)(?:Context composer summary:|Shared evidence ledger:|Selected plan\/card context:|Active workspace context:|Recent chat turns:|Surface attachments:|-?\s*(?:selected_card|selected_chunk|active_note|active_file|workspace_root|workspace_id):\s*\{)/i;

function stripInternalContextEcho(content: string) {
  const match = INTERNAL_CONTEXT_ECHO_RE.exec(content);
  if (!match) return content;
  const matchText = match[0] ?? "";
  const leadingWhitespace = matchText.match(/^\s*/)?.[0]?.length ?? 0;
  const cutIndex = match.index + leadingWhitespace;
  const visible = content.slice(0, cutIndex).trimEnd();
  return visible || "_Internal context hidden from Preview._";
}

function previewMarkdownContent(content: string) {
  return stripInternalContextEcho(content || "_Waiting for output._").trim() || "_Waiting for output._";
}

function isGenericCompletionText(value: string) {
  return /^(completed|finished|run completed|run finished|(?:super\s+dan|codex) completed|(?:super\s+dan|codex) run completed)\.?$/i.test(
    value.trim(),
  );
}

function isGenericNeedsAttentionText(value: string) {
  return /^(?:super\s+)?dan\s+needs attention\.?$|^(?:work|execution|run|this step)\s+needs attention\.?$|^needs attention\.?$/i.test(
    value.trim(),
  );
}

function looksLikeHardAttentionItem(value: string) {
  return /\b(?:blocked?|blocker|failed?|failure|error|exception|denied|invalid|missing|required|unable|cannot|can't|needs approval|need you to|must choose|conflict|stopped)\b/i.test(
    value.trim(),
  );
}

function looksLikeOptionalContinuationItem(value: string) {
  const normalized = value.trim();
  if (!normalized || looksLikeHardAttentionItem(normalized)) return false;
  return /\b(?:if you want|if you'd like|if you would like|i can also|i could also|we can also|could also|optional|next steps?|follow[- ]?ups?|just say the word|say the word|when you want)\b/i.test(
    normalized,
  );
}

function markdownSectionTextAfterHeading(content: string, headingIndex: number) {
  const rest = content.slice(headingIndex);
  const firstLineEnd = rest.indexOf("\n");
  if (firstLineEnd < 0) return "";
  const bodyStart = headingIndex + firstLineEnd + 1;
  const nextHeading = content.slice(bodyStart).search(/^#{2,6}\s+\S/gm);
  return (nextHeading >= 0
    ? content.slice(bodyStart, bodyStart + nextHeading)
    : content.slice(bodyStart)
  ).trim();
}

function normalizeOptionalAttentionMarkdown(content: string) {
  return content.replace(
    /^(#{2,6}\s+)(?:Remaining Attention|Needs Attention)\s*$/gim,
    (match, prefix: string, offset: number, fullText: string) => {
      const sectionText = markdownSectionTextAfterHeading(fullText, offset);
      return sectionText && looksLikeOptionalContinuationItem(sectionText)
        ? `${prefix}Optional next steps`
        : match;
    },
  );
}

function pathBullets(paths: string[], verb: string) {
  return paths.map((path) => `${verb}: \`${path}\``);
}

interface StructuredAgentDisplay {
  body: string;
  previewBody: string;
}

function detailItemsForKeys(record: Record<string, unknown>, keys: string[]) {
  return uniqueStringList(
    keys.flatMap((key) =>
      detailItemsFromValue(record[key])
        .map(normalizeSummaryLine)
        .filter((item) => item && !isGenericCompletionText(item) && !isGenericNeedsAttentionText(item)),
    ),
  );
}

function pathItemsFromValue(value: unknown): string[] {
  if (Array.isArray(value)) return uniqueStringList(value.flatMap(pathItemsFromValue));
  const record = recordValue(value);
  if (record) {
    const path =
      scalarDetailText(record.path) ||
      scalarDetailText(record.file) ||
      scalarDetailText(record.relative_path) ||
      scalarDetailText(record.name);
    return path ? [path] : [];
  }
  const scalar = scalarDetailText(value);
  return scalar ? [scalar] : [];
}

function pathItemsForKeys(record: Record<string, unknown>, keys: string[]) {
  return uniqueStringList(keys.flatMap((key) => pathItemsFromValue(record[key])));
}

const DIRECT_FINAL_ANSWER_KEYS = [
  "answer",
  "final_answer",
  "public_response",
  "final_response",
  "response",
];

function structuredAgentDisplayFromRecord(data: Record<string, unknown>): StructuredAgentDisplay | null {
  const finalAnswerItems = detailItemsForKeys(data, DIRECT_FINAL_ANSWER_KEYS);
  const changeItems = detailItemsForKeys(data, [
    "summary",
    "change_summary",
    "completion_summary",
    "outcome",
    "message",
    "result_summary",
  ]);

  const created = pathItemsForKeys(data, [
    "files_created",
    "created_files",
    "artifacts_created",
  ]);
  const changed = pathItemsForKeys(data, [
    "files_changed",
    "changed_files",
    "files_modified",
    "modified_files",
  ]).filter((path) => !created.includes(path));
  const artifacts = pathItemsForKeys(data, ["artifacts", "artifact_refs"]).filter(
    (path) => !created.includes(path) && !changed.includes(path),
  );
  const fileItems = [
    ...pathBullets(created, "Created"),
    ...pathBullets(changed, "Changed"),
    ...pathBullets(artifacts, "Artifact"),
  ];
  const riskItems = detailItemsForKeys(data, ["risks", "risk", "warnings"]);
  const checkItems = detailItemsForKeys(data, [
    "validation",
    "validation_summary",
    "checks",
    "tests",
  ]);
  const optionalSeedItems = detailItemsForKeys(data, [
    "next_steps",
    "next_step",
    "optional_next_steps",
    "follow_up",
    "follow_ups",
    "suggestions",
  ]);
  const remainingSeedItems = detailItemsForKeys(data, [
    "remaining_work",
    "remaining",
    "blockers",
    "blocked_on",
    "attention_needed",
    "needs_attention",
  ]);
  const optionalItems = uniqueStringList([
    ...optionalSeedItems,
    ...remainingSeedItems.filter(looksLikeOptionalContinuationItem),
    ...riskItems.filter(looksLikeOptionalContinuationItem),
  ]);
  const remainingItems = remainingSeedItems.filter((item) => !looksLikeOptionalContinuationItem(item));
  const attentionItems = uniqueStringList([
    ...remainingItems,
    ...riskItems.filter((item) => !looksLikeOptionalContinuationItem(item)),
  ]);
  const bodyItems = finalAnswerItems.length
    ? finalAnswerItems
    : changeItems.length
      ? compactDetailItems(changeItems, 2)
      : fileItems.length
        ? compactDetailItems(fileItems, 2)
        : checkItems.length
          ? compactDetailItems(checkItems, 2)
          : attentionItems.length
            ? compactDetailItems(attentionItems, 2)
            : riskItems.length
              ? compactDetailItems(riskItems, 2)
              : [];
  const body =
    bodyItems.length === 1 ? bodyItems[0] : bodyItems.map((item) => `- ${item}`).join("\n");
  const hasPreviewSections =
    bodyItems.length > 0 ||
    changeItems.length > 0 ||
    fileItems.length > 0 ||
    checkItems.length > 0 ||
    attentionItems.length > 0 ||
    optionalItems.length > 0;
  const summaryItems = hasPreviewSections ? bodyItems : [];
  const previewBody = detailMarkdown("", [
    {
      title: "Summary",
      items: summaryItems,
    },
    {
      title: "What Changed",
      items: finalAnswerItems.length > 0 ? changeItems : [],
    },
    { title: "Files", items: fileItems },
    { title: "Checks", items: checkItems },
    { title: "Optional next steps", items: optionalItems },
    { title: "Needs Attention", items: attentionItems },
  ]);

  if (!body && !previewBody) return null;
  return {
    body: body || (previewBody ? "Run completed." : ""),
    previewBody: previewBody || body,
  };
}

function formatStructuredAgentDisplay(content: string): StructuredAgentDisplay | null {
  const data = parseJsonObject(content);
  return data ? structuredAgentDisplayFromRecord(data) : null;
}

function structuredAgentDisplayFromValue(
  value: unknown,
  seen = new Set<unknown>(),
  depth = 0,
): StructuredAgentDisplay | null {
  if (depth > 4 || seen.has(value)) return null;
  const record = recordValue(value);
  if (record) {
    seen.add(value);
    const direct = structuredAgentDisplayFromRecord(record);
    if (direct) return direct;
    for (const key of ["result", "final", "output", "outputs", "response", "data", "payload", "text"]) {
      const nested = structuredAgentDisplayFromValue(record[key], seen, depth + 1);
      if (nested) return nested;
    }
    return null;
  }
  if (Array.isArray(value)) {
    seen.add(value);
    for (const item of value) {
      const nested = structuredAgentDisplayFromValue(item, seen, depth + 1);
      if (nested) return nested;
    }
  }
  const scalar = scalarDetailText(value);
  if (scalar) return formatStructuredAgentDisplay(scalar);
  return null;
}

function formatStructuredAgentSummary(content: string) {
  const structured = formatStructuredAgentDisplay(content);
  if (structured) return structured.previewBody;
  return "";
}

function structuredValueHasDirectFinalAnswer(
  value: unknown,
  seen = new Set<unknown>(),
  depth = 0,
): boolean {
  if (depth > 4 || seen.has(value)) return false;
  const record = recordValue(value);
  if (record) {
    seen.add(value);
    if (detailItemsForKeys(record, DIRECT_FINAL_ANSWER_KEYS).length > 0) return true;
    for (const key of ["result", "final", "output", "outputs", "data", "payload"]) {
      if (structuredValueHasDirectFinalAnswer(record[key], seen, depth + 1)) return true;
    }
    return false;
  }
  if (Array.isArray(value)) {
    seen.add(value);
    return value.some((item) => structuredValueHasDirectFinalAnswer(item, seen, depth + 1));
  }
  return false;
}

function structuredValueHasAnswerSummary(
  value: unknown,
  seen = new Set<unknown>(),
  depth = 0,
): boolean {
  if (depth > 4 || seen.has(value)) return false;
  const record = recordValue(value);
  if (record) {
    seen.add(value);
    const summaryItems = detailItemsForKeys(record, ["summary", "result_summary", "message"]);
    if (summaryItems.some((item) => item && !looksLikeFileReceiptOnly(item))) return true;
    for (const key of ["result", "final", "output", "outputs", "data", "payload"]) {
      if (structuredValueHasAnswerSummary(record[key], seen, depth + 1)) return true;
    }
    return false;
  }
  if (Array.isArray(value)) {
    seen.add(value);
    return value.some((item) => structuredValueHasAnswerSummary(item, seen, depth + 1));
  }
  return false;
}

function looksLikeFileReceiptOnly(content: string) {
  const normalized = content.trim();
  if (!normalized) return false;
  const hasReceiptVerb =
    /^(?:created|updated|changed|wrote|fixed|added|deleted|modified|saved)\b/i.test(normalized) ||
    /\b(?:created|updated|changed|wrote|fixed|added|deleted|modified|saved):/i.test(normalized) ||
    /\b(?:workspace changes|files?|artifacts?)\b/i.test(normalized);
  const hasFileReference =
    /[`'"]?[\w./~-]+\.(?:md|txt|json|html|css|js|ts|tsx|jsx|py|csv|yaml|yml|toml)\b/i.test(normalized) ||
    /\b(?:readme|project_summary|project_overview|file)\b/i.test(normalized);
  return hasReceiptVerb && hasFileReference;
}

function looksLikeStructuredPayloadFragment(content: string) {
  const normalized = content.trim();
  if (!normalized) return false;
  if (/^```[ \t]*(?:json)?\b/i.test(normalized)) return true;
  if (/^[{[]/.test(normalized)) return true;
  return /^"?(?:answer|final_answer|public_response|final_response|response|summary|message|result_summary)"?\s*:/i.test(
    normalized,
  );
}

function looksLikeOperationalProgressPromise(content: string) {
  const normalized = content.replace(/\s+/g, " ").trim();
  if (!normalized) return false;
  return /^(?:i(?:'ll| will| am going to)|i[’']m going to|we(?:'ll| will| are going to)|let me)\s+(?:trace|scan|check|inspect|look|read|review|restore|revert|follow|run|test|build|implement|fix|update|create|edit|write|try|continue|start|open|use|compare|investigate|debug|validate|verify|find|search)\b/i.test(
    normalized,
  );
}

function userFacingAgentDisplay(content: string): StructuredAgentDisplay {
  const structured = formatStructuredAgentDisplay(content);
  if (structured) return structured;
  if (parseJsonObject(content)) {
    return {
      body: "No user-facing result was returned.",
      previewBody: "No user-facing result was returned.",
    };
  }
  const normalized = normalizeStructuredMarkdown(content);
  const previewBody = normalizeOptionalAttentionMarkdown(normalized);
  return {
    body: normalized,
    previewBody,
  };
}

function isUsableFinalResponseSource(
  content: string,
  options: { requireDirectAnswer?: boolean } = {},
) {
  const normalized = content.trim();
  if (!normalized || isGenericCompletionText(normalized) || isGenericNeedsAttentionText(normalized)) return false;
  if (isRuntimeOutputChunkLimitText(normalized) || isGraphTelemetryText(normalized)) return false;
  if (looksLikeOperationalProgressPromise(normalized)) return false;
  if (options.requireDirectAnswer && looksLikeFileReceiptOnly(normalized)) return false;
  const parsed = parseJsonObject(normalized);
  if (!parsed) return !looksLikeStructuredPayloadFragment(normalized);
  if (
    options.requireDirectAnswer &&
    !structuredValueHasDirectFinalAnswer(parsed) &&
    !structuredValueHasAnswerSummary(parsed)
  ) {
    return false;
  }
  return Boolean(formatStructuredAgentDisplay(normalized));
}

function missingFinalResponseMessage(hasWorkspaceEvidence: boolean, requireDirectAnswer = false) {
  if (requireDirectAnswer) {
    return hasWorkspaceEvidence
      ? "The run produced workspace or file evidence, but DAN did not return the in-session answer this request asked for."
      : "The run finished, but DAN did not return the in-session answer this request asked for.";
  }
  return hasWorkspaceEvidence
    ? "The run finished and workspace evidence was recorded, but DAN did not return the final answer for this request."
    : "The run finished, but DAN did not return the final answer for this request.";
}

function timestampValue(value: unknown, fallback: number) {
  const parsed =
    typeof value === "string" || typeof value === "number"
      ? new Date(value).getTime()
      : Number.NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

function objectiveFromRun(run: ChatV2AgentRunRecord | null, task: ChatV2TaskSnapshot) {
  const payload = run?.command?.payload ?? {};
  const metadata = task.metadata ?? {};
  const lastSurfaceTurn = metadataObject(metadata.last_surface_turn);
  const operatorContext = metadataObject(metadata.operator_context);
  const runOperatorContext = metadataObject(payload.operator_context);
  return (
    textValue(payload.text) ||
    textValue(payload.message) ||
    textValue(payload.objective) ||
    textValue(runOperatorContext.raw_text) ||
    textValue(runOperatorContext.follow_up_objective) ||
    textValue(metadata.run_command_text) ||
    textValue(metadata["_run_command_text"]) ||
    textValue(lastSurfaceTurn.text) ||
    textValue(operatorContext.raw_text) ||
    textValue(operatorContext.follow_up_objective) ||
    textValue(metadata.text) ||
    textValue(metadata.message) ||
    textValue(metadata.objective)
  );
}

function answerEventFromAgentEvents(events: ChatV2AgentRunEvent[]) {
  let latestCompletedIndex = -1;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "completed") continue;
    if (latestCompletedIndex < 0) latestCompletedIndex = index;
    if (humanEventSummary(event)) return event;
  }
  if (latestCompletedIndex >= 0) {
    for (let index = latestCompletedIndex - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (eventFinalResponseSource(event)) return event;
    }
  }
  return null;
}

function latestAgentMessageEventFromAgentEvents(events: ChatV2AgentRunEvent[]) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "model_text_delta") continue;
    const text = eventPayloadText(event, "text") || humanEventSummary(event);
    if (isUsableFinalResponseSource(text)) return event;
  }
  return null;
}

function eventSource(event: ChatV2AgentRunEvent) {
  return textValue(event.source_event_type) || event.type;
}

function eventPayload(event: ChatV2AgentRunEvent) {
  return event.payload ?? {};
}

function eventPayloadText(event: ChatV2AgentRunEvent, key: string) {
  return textValue(eventPayload(event)[key]);
}

function eventFinalResponseSource(event: ChatV2AgentRunEvent) {
  for (const key of [...DIRECT_FINAL_ANSWER_KEYS, "final_text", "text"]) {
    const text = eventPayloadText(event, key);
    if (text && isUsableFinalResponseSource(text)) return text;
  }
  const payloadStructured = structuredEventPayloadSummary(event);
  return payloadStructured && isUsableFinalResponseSource(payloadStructured) ? payloadStructured : "";
}

function structuredEventPayloadSummary(event: ChatV2AgentRunEvent) {
  return structuredAgentDisplayFromValue(eventPayload(event))?.previewBody || "";
}

function isMachineSummary(summary: string, event: ChatV2AgentRunEvent) {
  const source = eventSource(event);
  const text = summary.trim();
  if (!text) return true;
  if (text === source || text === event.type) return true;
  if (isGraphTelemetryText(text)) return true;
  if (/^Token usage\b/i.test(text)) return true;
  if (
    isGenericCompletionText(text) ||
    isGenericNeedsAttentionText(text) ||
    [
      "completed",
      "run completed",
      "run finished",
      "super dan completed",
      "acknowledged",
      "released",
      "model.requested",
      "tool.started",
      "denied",
    ].includes(text.toLowerCase())
  ) {
    return true;
  }
  return /^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$/i.test(text) && !/\s/.test(text);
}

function humanEventSummary(event: ChatV2AgentRunEvent) {
  const summary = eventSummary(event).trim();
  const structured = formatStructuredAgentSummary(summary);
  if (structured) return structured;
  const readableCommand = readableCodexCommandSummary(summary);
  if (readableCommand) return readableCommand;
  const payloadFinalText = eventPayloadText(event, "final_text");
  if (payloadFinalText && isMachineSummary(summary, event) && isUsableFinalResponseSource(payloadFinalText)) {
    return payloadFinalText;
  }
  const payloadAnswerText = eventFinalResponseSource(event);
  if (payloadAnswerText && isMachineSummary(summary, event)) return payloadAnswerText;
  const payloadStructured = structuredEventPayloadSummary(event);
  if (payloadStructured && (terminalTaskStatuses.has(event.type) || isMachineSummary(summary, event))) {
    return payloadStructured;
  }
  return isMachineSummary(summary, event) ? "" : summary;
}

function toolLabel(toolId: string) {
  return toolId.replace(/_/g, " ");
}

function readableToolPolicyReason(reason: string) {
  const normalized = reason.trim();
  if (!normalized) return "the current run policy blocked it";
  if (normalized === "operator_intent_blocks_directory_listing") {
    return "the current request blocks directory listing";
  }
  if (normalized === "operator_intent_blocks_git_context") {
    return "the current request blocks git context";
  }
  if (normalized === "operator_intent_blocks_shell_context") {
    return "the current request blocks shell commands";
  }
  if (normalized === "operator_intent_blocks_workspace_mutation") {
    return "the current request blocks workspace changes";
  }
  const fileRead = normalized.match(/^operator_intent_blocks_file_read:(.+)$/);
  if (fileRead) return `the current request blocks reading \`${fileRead[1]}\``;
  const fileWrite = normalized.match(/^operator_intent_blocks_file_write:(.+)$/);
  if (fileWrite) return `the current request blocks writing \`${fileWrite[1]}\``;
  const workspaceCheck = normalized.match(/^operator_intent_blocks_workspace_check:(.+)$/);
  if (workspaceCheck) return `the current request blocks checking \`${workspaceCheck[1]}\``;
  return normalized.replace(/_/g, " ");
}

function pathFromEvent(event: ChatV2AgentRunEvent) {
  const payload = eventPayload(event);
  const result = payload.result;
  const argumentsPayload = payload.arguments;
  if (result && typeof result === "object") {
    const path = textValue((result as Record<string, unknown>).path);
    if (path) return path;
  }
  if (argumentsPayload && typeof argumentsPayload === "object") {
    const path = textValue((argumentsPayload as Record<string, unknown>).path);
    if (path) return path;
  }
  return "";
}

function isExecutionTraceGraphUpdateEvent(event: ChatV2AgentRunEvent) {
  if (eventSource(event) !== "live.task_graph.updated") return false;
  const payload = eventPayload(event);
  if (isExecutionTracePlanContext(normalizePlanContext(payload))) return true;
  const graphState = recordValue(payload.task_graph_state);
  if (!graphState) return false;
  const source = printableTextValue(graphState.source).trim().toLowerCase();
  const scope = printableTextValue(graphState.update_scope).trim().toLowerCase();
  const tasks = planTaskGraphFromValue(graphState.tasks ?? graphState.task_graph);
  const hasPlanStructure = tasks.some(isBlueprintPlanTaskPlan);
  const hasCodexTraceIds = tasks.some((task) =>
    /^codex-(?:item|worker|request|final)(?:-|$)/i.test(task.taskId),
  );
  if (hasPlanStructure) return false;
  if (source === "codex" && scope === "observable_event") return true;
  return scope === "observable_event" && hasCodexTraceIds;
}

function isRuntimeOutputChunkLimitText(text: string) {
  const normalized = text.trim().toLowerCase();
  return (
    normalized.includes("separator is not found") &&
    /chunk exceed(?:ed|s)? the limit/.test(normalized)
  );
}

function isGraphTelemetryText(text: string) {
  return /^task graph updated(?:\s+to\s+\S+)?(?:\s+by\s+\S+)?\.?$/i.test(text.trim());
}

function rawCodexCommandFromSummary(text: string) {
  const match = text.trim().match(/^Codex ran\s+`([^`]+)`\.?$/i);
  return match?.[1]?.trim() || "";
}

function readableCodexCommandSummary(text: string) {
  const command = rawCodexCommandFromSummary(text);
  if (!command) return "";
  const normalized = command.toLowerCase();
  if (normalized.includes("playwright") || normalized.includes("pwcli") || normalized.includes("screenshot")) {
    return "Codex checked the local preview.";
  }
  if (normalized.includes("npm run build") || normalized.includes("vite build") || /\btsc\b/.test(normalized)) {
    return "Codex built the frontend.";
  }
  if (
    normalized.includes("npm test") ||
    normalized.includes("npm run test") ||
    normalized.includes("vitest") ||
    normalized.includes("jest")
  ) {
    return "Codex ran frontend tests.";
  }
  if (normalized.includes("pytest") || normalized.includes("python -m pytest")) {
    return "Codex ran Python tests.";
  }
  if (normalized.includes("git diff") || normalized.includes("git status")) {
    return "Codex checked workspace changes.";
  }
  if (normalized.includes("curl ")) {
    return "Codex checked a local endpoint.";
  }
  return "Codex ran a workspace command.";
}

function readableRuntimeIssueText(text: string, task?: ChatV2TaskSnapshot | null) {
  if (isRuntimeOutputChunkLimitText(text)) {
    return `${taskAgentDisplayLabel(task)} hit an output-size parsing limit while reading command output.`;
  }
  return text;
}

function eventActivityLine(event: ChatV2AgentRunEvent) {
  const source = eventSource(event);
  const payload = eventPayload(event);
  const readableCommand = readableCodexCommandSummary(eventSummary(event));
  if (readableCommand) return readableCommand;

  if (source === "model.requested") {
    const model = eventPayloadText(event, "model");
    const round = eventPayloadText(event, "round");
    return `Thinking${model ? ` with ${model}` : ""}${round ? ` · round ${round}` : ""}.`;
  }
  if (source === "model.responded") {
    const toolCalls = payload.tool_calls;
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      return `Planning ${toolCalls.length === 1 ? "the next tool call" : `${toolCalls.length} tool calls`}.`;
    }
    return "";
  }
  if (source === "live.task_graph.updated") {
    if (isExecutionTraceGraphUpdateEvent(event)) return "";
    const graphState = recordValue(payload.task_graph_state);
    const versionId = printableTextValue(graphState?.version_id);
    const revision = printableTextValue(graphState?.revision);
    const graphSource = printableTextValue(graphState?.source) || eventPayloadText(event, "source");
    return `Task graph updated${versionId ? ` to ${versionId}` : revision ? ` to r${revision}` : ""}${graphSource ? ` by ${graphSource}` : ""}.`;
  }
  if (source === "live.task_blueprint.updated") {
    const blueprint = normalizeTaskBlueprintProjection(payload);
    const revision = blueprint?.revisionId ||
      (blueprint?.revision !== null && blueprint?.revision !== undefined
        ? `r${blueprint.revision}`
        : "");
    return `Task blueprint updated${revision ? ` to ${revision}` : ""}.`;
  }
  if (source === "live.execution_attempt.updated") {
    const attempt = normalizeExecutionAttemptProjections(payload).slice(-1)[0];
    return attempt
      ? `Execution attempt ${attempt.attemptId} is ${attempt.status || attempt.phase || "active"}.`
      : "Execution attempt updated.";
  }
  if (source === "tool.started") {
    const toolId = eventPayloadText(event, "tool_id");
    return toolId ? `Using ${toolLabel(toolId)}.` : "Using a workspace tool.";
  }
  if (source === "tool.policy_denied") {
    const toolId = eventPayloadText(event, "tool_id");
    const path = pathFromEvent(event);
    const reason = readableToolPolicyReason(eventPayloadText(event, "reason"));
    return `${toolId ? toolLabel(toolId) : "Tool"} denied${path ? ` for \`${path}\`` : ""}: ${reason}.`;
  }
  if (source === "tool.denied") {
    const toolId = eventPayloadText(event, "tool_id");
    const path = pathFromEvent(event);
    const reason = eventPayloadText(event, "reason") || eventPayloadText(event, "error");
    if (!reason) return "";
    return `${toolId ? toolLabel(toolId) : "Tool"} denied${path ? ` for \`${path}\`` : ""}: ${readableToolPolicyReason(reason)}.`;
  }
  if (source === "tool.failed") {
    const toolId = eventPayloadText(event, "tool_id");
    const path = pathFromEvent(event);
    const reason = eventPayloadText(event, "error") || humanEventSummary(event);
    const readableReason = reason
      ? readableRuntimeIssueText(readableToolPolicyReason(reason))
      : "";
    return `${toolId ? toolLabel(toolId) : "Tool"} failed${path ? ` for \`${path}\`` : ""}${readableReason ? `: ${readableReason}` : ""}.`;
  }
  if (source === "tool.completed") {
    const toolId = eventPayloadText(event, "tool_id");
    const path = pathFromEvent(event);
    const result = payload.result;
    const changed =
      result && typeof result === "object" && Boolean((result as Record<string, unknown>).changed);
    if (changed && path) return `Changed \`${path}\`.`;
    return toolId ? `Finished ${toolLabel(toolId)}.` : "Finished a workspace tool.";
  }
  if (event.type === "failed" || event.type === "blocked") {
    const reason = attentionReasonFromValues([
      payload.blocker,
      payload.blocked_on,
      payload.blockers,
      payload.attention_reason,
      payload.failure_reason,
      payload.status_reason,
      payload.reason,
      payload.error,
      payload.errors,
      payload.exception,
      eventSummary(event),
    ]);
    const evidenceReason = reason
      ? ""
      : attentionReasonFromSharedEvidenceItems(sharedEvidenceItemsFromEvent(event, 0));
    const resolvedReason = reason || evidenceReason;
    return resolvedReason
      ? `Needs attention: ${resolvedReason}${/[.!?]$/.test(resolvedReason) ? "" : "."}`
      : "DAN needs attention, but did not emit a specific reason.";
  }
  const human = humanEventSummary(event);
  if (human && event.type !== "token_usage_recorded") return human;

  if (source === "super.heartbeat") {
    const detail = eventPayloadText(event, "detail");
    return detail ? `Working: ${detail}.` : "Working.";
  }
  if (source.includes("validation")) return "Checking changes.";
  if (source === "worker.started" || source === "live.generic_build.started") {
    return "Working in this workspace.";
  }
  if (event.type === "completed") return human || "";
  return "";
}

function eventChunkStatus(event: ChatV2AgentRunEvent): ChunkStatus {
  if (event.type === "failed" || event.type === "blocked") return "error";
  if (event.type === "queued" || event.type === "waiting_dependency") return "queued";
  if (terminalTaskStatuses.has(event.type)) return "clean";
  return "running";
}

function changedPathLines(events: ChatV2AgentRunEvent[]) {
  const paths = new Set<string>();
  for (const event of events) {
    if (eventSource(event) !== "tool.completed") continue;
    const result = eventPayload(event).result;
    const changed =
      result && typeof result === "object" && Boolean((result as Record<string, unknown>).changed);
    const path = pathFromEvent(event);
    if (changed && path) paths.add(path);
  }
  return [...paths].slice(-5).map((path) => `- Changed: \`${path}\``);
}

function artifactLines(events: ChatV2AgentRunEvent[]) {
  const paths = new Set<string>();
  for (const event of events) {
    for (const ref of event.artifact_refs ?? []) {
      const path = textValue(ref.path) || textValue(ref.uri) || textValue(ref.url);
      if (path) paths.add(path);
    }
  }
  return [...paths].slice(-5).map((path) => `- Artifact: \`${path}\``);
}

function compactAgentRunChunks(events: ChatV2AgentRunEvent[]) {
  const groups = new Map<string, ChatV2AgentRunEvent[]>();
  for (const event of events) {
    const key = event.run_id || event.task_id || "agent";
    groups.set(key, [...(groups.get(key) ?? []), event]);
  }

  const chunks: WorkspaceChunk[] = [];
  for (const [key, group] of groups) {
    const latest = group[group.length - 1];
    if (!latest) continue;
    const terminal = [...group].reverse().find((event) => terminalTaskStatuses.has(event.type));
    const latestAgentMessage = latestAgentMessageEventFromAgentEvents(group);
    const latestAgentMessageText = latestAgentMessage
      ? eventPayloadText(latestAgentMessage, "text") || humanEventSummary(latestAgentMessage)
      : "";
    const outcome = [...changedPathLines(group), ...artifactLines(group)];
    if (terminal) {
      const terminalAnswer = humanEventSummary(terminal);
      const answer = terminalAnswer || latestAgentMessageText;
      if (answer) {
        chunks.push({
          id: `agent-answer:${key}`,
          kind: "agent",
          title: terminal.type === "completed" ? "DAN · Answer" : `DAN · ${terminal.type}`,
          body: answer,
          status: eventChunkStatus(terminal),
          meta: latestAgentMessageText && !terminalAnswer && latestAgentMessage
            ? eventSource(latestAgentMessage)
            : eventSource(terminal),
          taskId: terminal.task_id || latestAgentMessage?.task_id,
          runId: terminal.run_id || latestAgentMessage?.run_id,
        });
      }
      if (outcome.length > 0) {
        chunks.push({
          id: `agent-outcome:${key}`,
          kind: "agent",
          title: "DAN · Outcome",
          body: outcome.join("\n"),
          status: eventChunkStatus(terminal),
          meta: "workspace changes",
          taskId: terminal.task_id,
          runId: terminal.run_id,
        });
      }
      if (!answer && outcome.length === 0) {
        chunks.push({
          id: `agent-terminal:${key}`,
          kind: "agent",
          title: `Super DAN · ${terminal.type}`,
          body: eventActivityLine(terminal) || "Run finished.",
          status: eventChunkStatus(terminal),
          meta: eventSource(terminal),
          taskId: terminal.task_id,
          runId: terminal.run_id,
        });
      }
      continue;
    }

    const latestActivity =
      [...group].reverse().map(eventActivityLine).find(Boolean) || "Working in this workspace.";
    const body = outcome.length > 0 ? `${latestActivity}\n\n${outcome.join("\n")}` : latestActivity;
    chunks.push({
      id: `agent-working:${key}`,
      kind: "agent",
      title: "Super DAN · Working",
      body,
      status: "running",
      meta: eventSource(latest),
      taskId: latest.task_id,
      runId: latest.run_id,
    });
  }
  return chunks.slice(-6);
}

function recordValue(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function printableTextValue(value: unknown) {
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return textValue(value);
}

function uniqueStringList(values: string[]) {
  const seen = new Set<string>();
  const unique: string[] = [];
  for (const value of values) {
    const trimmed = value.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    unique.push(trimmed);
  }
  return unique;
}

interface BlueprintDetailSection {
  title: string;
  items: string[];
}

interface SharedEvidenceItem {
  id: string;
  kind: string;
  status: string;
  title: string;
  summary: string;
  source: string;
  ref: string;
  timestamp: string;
}

function humanizeDetailKey(key: string) {
  return key.replace(/^_+/, "").replace(/_/g, " ");
}

function scalarDetailText(value: unknown) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function isLowValueDetailText(value: string) {
  return /^(do_or_explain|do-or-explain|n\/a|none|null)$/i.test(value.trim());
}

function recordDetailLine(record: Record<string, unknown>) {
  for (const pair of [
    ["check", "status"],
    ["branch_id", "status"],
    ["task_id", "status"],
    ["aspect", "request_comment"],
    ["criterion", "action"],
    ["question", "request_comment"],
    ["path", "status"],
    ["target", "status"],
    ["summary", "detail"],
    ["status", "detail"],
  ] as const) {
    const first = scalarDetailText(record[pair[0]]);
    const second = scalarDetailText(record[pair[1]]);
    if (first && second && !isLowValueDetailText(second)) return `${first}: ${second}`;
    if (first) return first;
    if (second && !isLowValueDetailText(second)) return second;
  }
  const entries = Object.entries(record)
    .map(([key, value]) => [humanizeDetailKey(key), scalarDetailText(value)] as const)
    .filter(([, value]) => value)
    .slice(0, 4);
  return entries.map(([key, value]) => `${key}: ${value}`).join(" · ");
}

function detailItemsFromValue(value: unknown) {
  if (Array.isArray(value)) {
    return uniqueStringList(
      value
        .map((item) => {
          const record = recordValue(item);
          return record ? recordDetailLine(record) : scalarDetailText(item);
        })
        .filter(Boolean),
    );
  }
  const record = recordValue(value);
  if (record) {
    const line = recordDetailLine(record);
    return line ? [line] : [];
  }
  const scalar = scalarDetailText(value);
  return scalar ? [scalar] : [];
}

function compactDetailItems(items: string[], limit = 8) {
  const unique = uniqueStringList(items).filter(Boolean);
  if (unique.length <= limit) return unique;
  return [...unique.slice(0, limit), `${unique.length - limit} more not shown here`];
}

function detailMarkdown(intro: string, sections: BlueprintDetailSection[]) {
  const lines = [intro.trim()].filter(Boolean);
  for (const section of sections) {
    const items = compactDetailItems(section.items);
    if (items.length === 0) continue;
    lines.push("", `### ${section.title}`, ...items.map((item) => `- ${item}`));
  }
  return lines.join("\n");
}

function truncateReadableText(value: string, limit = 96) {
  const text = value.trim().replace(/\s+/g, " ");
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
}

function sharedEvidenceItemFromRecord(
  record: Record<string, unknown>,
  index: number,
  fallbackSource = "",
): SharedEvidenceItem | null {
  const title =
    textValue(record.title) ||
    textValue(record.name) ||
    textValue(record.kind) ||
    "Evidence";
  const summary =
    textValue(record.summary) ||
    textValue(record.detail) ||
    textValue(record.preview) ||
    detailItemsFromValue(record.summary).join("; ") ||
    title;
  if (!title && !summary) return null;
  const ref =
    textValue(record.ref) ||
    textValue(record.path) ||
    textValue(record.uri) ||
    textValue(record.url);
  return {
    id: textValue(record.id) || `${fallbackSource || "evidence"}-${index}`,
    kind: textValue(record.kind) || "evidence",
    status: textValue(record.status) || "context",
    title,
    summary,
    source: textValue(record.source) || fallbackSource,
    ref,
    timestamp:
      textValue(record.created_at) ||
      textValue(record.updated_at) ||
      textValue(record.timestamp) ||
      textValue(record.time),
  };
}

function sharedEvidenceItemsFromTaskMetadata(task: ChatV2TaskSnapshot) {
  const normalized = recordValue(task.metadata?.normalized_request);
  const composer =
    recordValue(task.metadata?.context_composer) ||
    recordValue(normalized?.context_composer);
  const ledger =
    recordValue(task.metadata?.shared_evidence_context) ||
    recordValue(composer?.shared_evidence) ||
    recordValue(normalized?.shared_evidence_context);
  const rawItems = Array.isArray(ledger?.items) ? ledger.items : [];
  return rawItems
    .map((item, index) => {
      const record = recordValue(item);
      return record ? sharedEvidenceItemFromRecord(record, index, "context_composer") : null;
    })
    .filter((item): item is SharedEvidenceItem => Boolean(item));
}

function firstCapsuleRef(record: Record<string, unknown>) {
  const refs = record.raw_refs || record.refs || record.references;
  if (Array.isArray(refs)) {
    for (const ref of refs) {
      const refRecord = recordValue(ref);
      if (!refRecord) continue;
      const text =
        textValue(refRecord.path) ||
        textValue(refRecord.uri) ||
        textValue(refRecord.url) ||
        textValue(refRecord.source);
      if (text) return text;
    }
  }
  return (
    textValue(record.path) ||
    textValue(record.uri) ||
    textValue(record.url) ||
    textValue(record.source)
  );
}

function isUsefulSharedEvidenceText(text: string) {
  const normalized = text.trim();
  if (!normalized) return false;
  if (isGenericCompletionText(normalized) || isGenericNeedsAttentionText(normalized)) return false;
  if (isGraphTelemetryText(normalized) || isRuntimeOutputChunkLimitText(normalized)) return false;
  return true;
}

function sharedEvidenceItemsFromEvent(event: ChatV2AgentRunEvent, index: number) {
  const source = eventSource(event);
  const payload = eventPayload(event);
  const eventTimestamp =
    textValue(payload.created_at) ||
    textValue(payload.updated_at) ||
    textValue(payload.timestamp) ||
    textValue(payload.time);
  const items: SharedEvidenceItem[] = [];
  const capsules = payload.capsules || payload.context_capsules;
  if (Array.isArray(capsules)) {
    capsules.forEach((capsule, capsuleIndex) => {
      const record = recordValue(capsule);
      if (!record) return;
      const summary =
        textValue(record.summary) ||
        textValue(record.excerpt) ||
        textValue(record.text) ||
        textValue(record.title);
      if (!isUsefulSharedEvidenceText(summary)) return;
      items.push({
        id: textValue(record.capsule_id) || textValue(record.id) || `${source}-${index}-${capsuleIndex}`,
        kind: "worker_evidence",
        status: textValue(record.artifact_state) || textValue(record.status) || "worker context",
        title: textValue(record.title) || textValue(record.kind) || "Worker evidence",
        summary,
        source: "worker_context_capsule",
        ref: firstCapsuleRef(record),
        timestamp:
          textValue(record.created_at) ||
          textValue(record.updated_at) ||
          textValue(record.timestamp) ||
          eventTimestamp,
      });
    });
  }

  const summary = humanEventSummary(event);
  if (!isUsefulSharedEvidenceText(summary)) return items;
  if (source.includes("validation")) {
    items.push({
      id: `${source}-${index}-validation`,
      kind: "validation",
      status: event.type === "failed" || event.type === "blocked" ? "needs attention" : "validated",
      title: "Validation evidence",
      summary,
      source,
      ref: "",
      timestamp: eventTimestamp,
    });
  } else if (event.type === "failed" || event.type === "blocked") {
    items.push({
      id: `${source}-${index}-blocker`,
      kind: "blocker",
      status: "needs attention",
      title: "Execution blocker",
      summary,
      source,
      ref: "",
      timestamp: eventTimestamp,
    });
  } else if (terminalTaskStatuses.has(event.type) && isUsableFinalResponseSource(summary)) {
    items.push({
      id: `${source}-${index}-answer`,
      kind: "final_response",
      status: "accepted",
      title: "Final response evidence",
      summary,
      source,
      ref: "",
      timestamp: eventTimestamp,
    });
  }
  return items;
}

function uniqueSharedEvidenceItems(items: SharedEvidenceItem[]) {
  const seen = new Set<string>();
  const unique: SharedEvidenceItem[] = [];
  for (const item of items) {
    const signature = [item.kind, item.title, item.summary, item.ref].join("\n").toLowerCase();
    if (seen.has(signature)) continue;
    seen.add(signature);
    unique.push(item);
  }
  return unique;
}

function sharedEvidenceItemsForNode(
  node: BlueprintNode,
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
  activeTask: ChatV2TaskSnapshot | null,
) {
  const relatedTasks = tasks.filter((task) => taskMatchesNode(task, node));
  if (activeTask && taskMatchesNode(activeTask, node) && !relatedTasks.some((task) => task.task_id === activeTask.task_id)) {
    relatedTasks.push(activeTask);
  }
  const eventScope = relatedNodeEvents(node, events).slice(-80);
  return uniqueSharedEvidenceItems([
    ...relatedTasks.flatMap(sharedEvidenceItemsFromTaskMetadata),
    ...eventScope.flatMap((event, index) => sharedEvidenceItemsFromEvent(event, index)),
  ]).slice(0, 14);
}

function collectRecordsMatching(
  value: unknown,
  predicate: (record: Record<string, unknown>) => boolean,
  results: Record<string, unknown>[] = [],
  seen = new Set<unknown>(),
  depth = 0,
) {
  if (depth > 6 || seen.has(value)) return results;
  const record = recordValue(value);
  if (!record) {
    if (Array.isArray(value)) {
      seen.add(value);
      for (const item of value) collectRecordsMatching(item, predicate, results, seen, depth + 1);
    }
    return results;
  }
  seen.add(value);
  if (predicate(record)) results.push(record);
  for (const nested of Object.values(record)) {
    if (nested && (typeof nested === "object" || Array.isArray(nested))) {
      collectRecordsMatching(nested, predicate, results, seen, depth + 1);
    }
  }
  return results;
}

function collectNamedRecords(value: unknown, key: string) {
  const records: Record<string, unknown>[] = [];
  collectRecordsMatching(value, (record) => {
    const nested = recordValue(record[key]);
    if (nested) records.push(nested);
    return false;
  });
  return records;
}

function looksLikeRequestUnderstanding(record: Record<string, unknown>) {
  return Boolean(
    record.schema === "super_dan_request_understanding_v1" ||
      record.request_understanding_schema === "super_dan_request_understanding_v1" ||
      record.rule_generation_brief ||
      record.aspect_reviews ||
      record.confidence_scoped_acceptance ||
      record.stop_rule,
  );
}

function latestRequestUnderstanding(
  events: ChatV2AgentRunEvent[],
  tasks: ChatV2TaskSnapshot[],
) {
  const candidates: Record<string, unknown>[] = [];
  for (const event of events) {
    candidates.push(...collectRecordsMatching(eventPayload(event), looksLikeRequestUnderstanding));
  }
  for (const task of tasks) {
    candidates.push(...collectRecordsMatching(task.metadata, looksLikeRequestUnderstanding));
  }
  return candidates[candidates.length - 1] ?? null;
}

function operatorContextRecords(events: ChatV2AgentRunEvent[], tasks: ChatV2TaskSnapshot[]) {
  const records: Record<string, unknown>[] = [];
  for (const task of tasks) records.push(...collectNamedRecords(task.metadata, "operator_context"));
  for (const event of events) records.push(...collectNamedRecords(eventPayload(event), "operator_context"));
  return records;
}

function collectFieldItems(records: Array<Record<string, unknown> | null>, keys: string[]) {
  return uniqueStringList(
    records.flatMap((record) => {
      if (!record) return [];
      return keys.flatMap((key) => detailItemsFromValue(record[key]));
    }),
  );
}

function graphNodeTypeFromRecord(record: Record<string, unknown>) {
  const value = (
    printableTextValue(record.node_type) ||
    printableTextValue(record.graph_level) ||
    printableTextValue(record.level) ||
    printableTextValue(record.node_kind) ||
    printableTextValue(record.task_kind) ||
    (record.is_plan === true ? "plan" : "")
  )
    .trim()
    .toLowerCase();
  if (value === "phase") return "plan";
  return value;
}

function isBlueprintPlanTaskPlan(task: BlueprintPlanTask) {
  return ["plan", "phase"].includes((task.nodeType || "").toLowerCase());
}

function hasExplicitPlanTasks(context: BlueprintPlanContext | null | undefined) {
  return Boolean(context?.taskGraph.some(isBlueprintPlanTaskPlan));
}

function isExecutionTracePlanContext(context: BlueprintPlanContext | null | undefined) {
  if (!context) return false;
  const source = context.graphSource.trim().toLowerCase();
  const scope = context.graphUpdateScope.trim().toLowerCase();
  const hasPlanStructure = hasExplicitPlanTasks(context);
  const hasCodexTraceIds = context.taskGraph.some((task) =>
    /^codex-(?:item|worker|request|final)(?:-|$)/i.test(task.taskId),
  );
  if (hasPlanStructure) return false;
  if (source === "codex" && scope === "observable_event") return true;
  return scope === "observable_event" && hasCodexTraceIds;
}

function isSemanticPlanContext(context: BlueprintPlanContext | null | undefined) {
  return Boolean(context && !isExecutionTracePlanContext(context));
}

function planTaskGraphFromValue(value: unknown): BlueprintPlanTask[] {
  const container = recordValue(value);
  const rawItems = container ? container.tasks ?? container.task_graph : value;
  if (!Array.isArray(rawItems)) return [];
  const seen = new Set<string>();
  const tasks: BlueprintPlanTask[] = [];
  const visit = (
    item: unknown,
    inheritedPlanId = "",
    inheritedBranchId = "",
  ) => {
    const record = recordValue(item);
    if (!record) return;
    const taskId =
      printableTextValue(record.task_id) ||
      printableTextValue(record.id) ||
      printableTextValue(record.number);
    if (!taskId || seen.has(taskId)) return;
    seen.add(taskId);
    const parentId = printableTextValue(record.parent_id) || printableTextValue(record.parent);
    const branchId =
      printableTextValue(record.branch_id) ||
      printableTextValue(record.branch) ||
      inheritedBranchId;
    const nodeType = graphNodeTypeFromRecord(record);
    const explicitPlanId =
      printableTextValue(record.plan_id) ||
      printableTextValue(record.parent_plan_id) ||
      printableTextValue(record.container_plan_id) ||
      printableTextValue(record.container_id);
    const planId = explicitPlanId || inheritedPlanId;
    tasks.push({
      taskId,
      ...(parentId ? { parentId } : {}),
      ...(planId ? { planId } : {}),
      ...(branchId ? { branchId } : {}),
      ...(nodeType ? { nodeType } : {}),
      goal:
        printableTextValue(record.goal) ||
        printableTextValue(record.summary) ||
        printableTextValue(record.title) ||
        "Projected task",
      dependsOn: uniqueStringList([
        ...stringList(record.depends_on),
        ...stringList(record.dependencies),
      ]),
      generationDependsOn: uniqueStringList([
        ...stringList(record.generation_depends_on),
        ...stringList(record.planning_depends_on),
        ...stringList(record.plan_generation_depends_on),
      ]),
      ownedPaths: uniqueStringList([
        ...stringList(record.owned_paths),
        ...stringList(record.owner_paths),
        ...stringList(record.paths),
      ]),
      deliverables: uniqueStringList(stringList(record.deliverables)),
      validation: uniqueStringList([
        ...stringList(record.validation),
        ...stringList(record.checks),
        ...stringList(record.acceptance),
      ]),
      status: printableTextValue(record.status) || "planned",
      state: printableTextValue(record.state),
      parallelSafe: typeof record.parallel_safe === "boolean" ? record.parallel_safe : true,
    });
    const childItems =
      recordValue(record.tasks)?.tasks ||
      record.tasks ||
      record.task_graph ||
      record.children ||
      record.subtasks ||
      record.task_tree;
    if (Array.isArray(childItems)) {
      const childPlanId = nodeType === "plan" ? taskId : planId;
      for (const child of childItems) visit(child, childPlanId, branchId);
    }
  };
  for (const item of rawItems) visit(item);
  return tasks;
}

function graphStateFromRecord(record: Record<string, unknown>) {
  const directSchema = printableTextValue(record.schema);
  if (directSchema === "super_dan_task_graph_v1") return record;
  const nested = recordValue(record.task_graph_state);
  if (nested && printableTextValue(nested.schema) === "super_dan_task_graph_v1") return nested;
  return null;
}

function numericValue(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function positiveIntegerValue(value: unknown, fallback: number) {
  const parsed = numericValue(value);
  return parsed && parsed > 0 ? Math.floor(parsed) : fallback;
}

function parallelGroupsFromValue(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => uniqueStringList(stringList(item)))
    .filter((group) => group.length > 1);
}

function branchSummariesFromValue(value: unknown) {
  if (!Array.isArray(value)) return [];
  return uniqueStringList(
    value
      .map((item) => {
        const record = recordValue(item);
        if (!record) return scalarDetailText(item);
        const branchId = printableTextValue(record.branch_id) || printableTextValue(record.id);
        if (!branchId) return recordDetailLine(record);
        const taskCount = stringList(record.task_ids).length;
        const activeCount = stringList(record.active_task_ids).length;
        const readyCount = stringList(record.ready_task_ids).length;
        const doneCount = stringList(record.completed_task_ids).length;
        const deferredCount = stringList(record.deferred_task_ids).length;
        const bits = [
          taskCount ? `${taskCount} tasks` : "",
          activeCount ? `${activeCount} active` : "",
          readyCount ? `${readyCount} ready` : "",
          doneCount ? `${doneCount} done` : "",
          deferredCount ? `${deferredCount} deferred` : "",
        ].filter(Boolean);
        return `${branchId}: ${bits.join(", ") || "planned"}`;
      })
      .filter(Boolean),
  );
}

function branchRefsFromValue(value: unknown) {
  if (!Array.isArray(value)) return [];
  return uniqueStringList(
    value
      .map((item) => {
        const record = recordValue(item);
        if (!record) return scalarDetailText(item);
        const branchId = printableTextValue(record.branch_id) || printableTextValue(record.ref_name);
        const versionId = printableTextValue(record.version_id);
        if (!branchId || !versionId) return recordDetailLine(record);
        const parent = printableTextValue(record.parent_version_id);
        const changed = record.changed === true ? " changed" : "";
        return `${branchId}: ${versionId}${parent ? ` from ${parent}` : ""}${changed}`;
      })
      .filter(Boolean),
  );
}

function hasPlanContextShape(record: Record<string, unknown>) {
  return Boolean(
    record.plan_context ||
      record.task_blueprint ||
      record.task_graph_state ||
      printableTextValue(record.schema) === "dan_task_blueprint_v1" ||
      printableTextValue(record.schema) === "super_dan_task_graph_v1" ||
      record.task_graph ||
      record.tasks ||
      record.ready_task_ids ||
      record.assigned_task_ids ||
      record.deferred_task_ids ||
      record.first_build_slice ||
      record.plan_files,
  );
}

function normalizePlanContext(value: unknown): BlueprintPlanContext | null {
  const record = recordValue(value);
  if (!record || !hasPlanContextShape(record)) return null;
  const canonicalBlueprint = normalizeTaskBlueprintProjection(value);
  if (canonicalBlueprint) {
    const parallelReady = canonicalBlueprint.nodes
      .filter(
        (node) =>
          node.parallelSafe &&
          (canonicalBlueprint.readyNodeIds.includes(node.id) || node.status === "ready"),
      )
      .map((node) => node.id);
    const branchCounts = new Map<string, number>();
    for (const node of canonicalBlueprint.nodes) {
      const branch = node.branchId || "main";
      branchCounts.set(branch, (branchCounts.get(branch) ?? 0) + 1);
    }
    return {
      taskGraph: canonicalBlueprint.nodes.map((node) => ({
        taskId: node.id,
        ...(node.parentId ? { parentId: node.parentId } : {}),
        ...(node.branchId ? { branchId: node.branchId } : {}),
        nodeType: node.kind,
        goal: node.title,
        dependsOn: node.dependsOn,
        ownedPaths: node.artifacts,
        deliverables: node.artifacts,
        validation: node.validation,
        status: node.status,
        state: node.status,
        parallelSafe: node.parallelSafe,
        description: node.description,
        topologyRole: node.topologyRole,
        capabilityRequirements: node.capabilityRequirements,
        criterionIds: node.criterionIds,
        loopPolicy: node.loopPolicy,
        supersedes: node.supersedes,
        supersededBy: node.supersededBy,
      })),
      readyTaskIds: canonicalBlueprint.readyNodeIds,
      deferredTaskIds: canonicalBlueprint.deferredNodeIds,
      assignedTaskIds: [],
      activeTaskIds: canonicalBlueprint.activeNodeIds,
      completedTaskIds: canonicalBlueprint.completedNodeIds,
      parallelWorktreeTaskIds: [],
      dependencyRevisions: [],
      planFiles: [],
      planRootRelative: canonicalBlueprint.contract.goal,
      graphRevision: canonicalBlueprint.revision,
      graphVersionId: canonicalBlueprint.revisionId,
      graphRootVersionId: canonicalBlueprint.blueprintId,
      graphBaseVersionId: "",
      graphParentVersionIds: canonicalBlueprint.parentRevisionIds,
      graphSource: "task blueprint",
      graphUpdateReason: canonicalBlueprint.updateReason,
      graphUpdateScope: "blueprint revision",
      graphChangedTaskIds: canonicalBlueprint.nodes.map((node) => node.id),
      graphChangedBranchIds: [...branchCounts.keys()],
      parallelGroups: parallelReady.length > 1 ? [parallelReady] : [],
      graphBranches: [...branchCounts.entries()].map(
        ([branch, count]) => `${branch}: ${count} ${count === 1 ? "node" : "nodes"}`,
      ),
      graphBranchRefs: [],
      planGenerationQueueLength: DEFAULT_PLAN_GENERATION_QUEUE_LENGTH,
      planExecutionQueueLength: DEFAULT_PLAN_EXECUTION_QUEUE_LENGTH,
      taskExecutionQueueLength: DEFAULT_TASK_EXECUTION_QUEUE_LENGTH,
      canonicalBlueprint: true,
      blueprintId: canonicalBlueprint.blueprintId,
      blueprintTaskId: canonicalBlueprint.taskId,
      blueprintRevisionId: canonicalBlueprint.revisionId,
      taskFamily: canonicalBlueprint.family,
      contract: canonicalBlueprint.contract,
      executionAttempts: normalizeExecutionAttemptProjections(value),
      blueprintEdges: canonicalBlueprint.edges.map((edge) => ({
        from: edge.from,
        to: edge.to,
        kind: edge.kind,
        condition: edge.condition,
        loopNodeId: edge.loopNodeId,
      })),
      entryNodeIds: canonicalBlueprint.entryNodeIds,
      terminalNodeIds: canonicalBlueprint.terminalNodeIds,
      boundedLoopNodeIds: canonicalBlueprint.boundedLoopNodeIds,
      requiredCriterionIds: canonicalBlueprint.requiredCriterionIds,
      uncoveredCriterionIds: canonicalBlueprint.uncoveredCriterionIds,
    };
  }
  const validation = recordValue(record.validation);
  const graphState = graphStateFromRecord(record);
  const graphTaskGraph = planTaskGraphFromValue(graphState?.tasks);
  const directTaskGraph = graphTaskGraph.length
    ? graphTaskGraph
    : planTaskGraphFromValue(record.task_graph ?? record.tasks);
  const validationTaskGraph = directTaskGraph.length
    ? directTaskGraph
    : planTaskGraphFromValue(validation?.task_graph ?? validation?.tasks);
  const readyTaskIds = uniqueStringList([
    ...stringList(graphState?.ready_task_ids),
    ...stringList(record.ready_task_ids),
    ...stringList(record.first_build_slice),
    ...stringList(validation?.ready_task_ids),
    ...stringList(validation?.first_build_slice),
  ]);
  const context: BlueprintPlanContext = {
    taskGraph: validationTaskGraph,
    readyTaskIds,
    deferredTaskIds: uniqueStringList([
      ...stringList(graphState?.deferred_task_ids),
      ...stringList(record.deferred_task_ids),
      ...stringList(validation?.deferred_task_ids),
    ]),
    assignedTaskIds: uniqueStringList(stringList(record.assigned_task_ids)),
    activeTaskIds: uniqueStringList(stringList(graphState?.active_task_ids)),
    completedTaskIds: uniqueStringList(stringList(graphState?.completed_task_ids)),
    parallelWorktreeTaskIds: uniqueStringList(stringList(record.parallel_worktree_task_ids)),
    dependencyRevisions: uniqueStringList([
      ...detailItemsFromValue(graphState?.dependency_revisions),
      ...detailItemsFromValue(record.dependency_revisions),
      ...detailItemsFromValue(validation?.dependency_revisions),
    ]),
    planFiles: uniqueStringList(stringList(record.plan_files)),
    planRootRelative: printableTextValue(record.plan_root_relative),
    graphRevision:
      numericValue(graphState?.revision) ??
      numericValue(record.task_graph_revision) ??
      numericValue(record.graph_revision),
    graphVersionId: printableTextValue(graphState?.version_id),
    graphRootVersionId: printableTextValue(graphState?.root_version_id),
    graphBaseVersionId: printableTextValue(graphState?.base_version_id),
    graphParentVersionIds: uniqueStringList(stringList(graphState?.parent_version_ids)),
    graphSource: printableTextValue(graphState?.source),
    graphUpdateReason: printableTextValue(graphState?.update_reason),
    graphUpdateScope: printableTextValue(graphState?.update_scope),
    graphChangedTaskIds: uniqueStringList(stringList(graphState?.changed_task_ids)),
    graphChangedBranchIds: uniqueStringList(stringList(graphState?.changed_branch_ids)),
    parallelGroups: parallelGroupsFromValue(graphState?.parallel_groups),
    graphBranches: branchSummariesFromValue(graphState?.branches),
    graphBranchRefs: branchRefsFromValue(graphState?.branch_refs),
    planGenerationQueueLength: positiveIntegerValue(
      graphState?.plan_generation_queue_length ??
        graphState?.generation_queue_length ??
        record.plan_generation_queue_length,
      DEFAULT_PLAN_GENERATION_QUEUE_LENGTH,
    ),
    planExecutionQueueLength: positiveIntegerValue(
      graphState?.plan_execution_queue_length ??
        graphState?.execution_queue_length ??
        record.plan_execution_queue_length,
      DEFAULT_PLAN_EXECUTION_QUEUE_LENGTH,
    ),
    taskExecutionQueueLength: positiveIntegerValue(
      graphState?.task_execution_queue_length ??
        record.task_execution_queue_length,
      DEFAULT_TASK_EXECUTION_QUEUE_LENGTH,
    ),
  };
  const hasContent =
    context.taskGraph.length > 0 ||
    context.readyTaskIds.length > 0 ||
    context.deferredTaskIds.length > 0 ||
    context.assignedTaskIds.length > 0 ||
    context.activeTaskIds.length > 0 ||
    context.completedTaskIds.length > 0 ||
    context.graphRevision !== null ||
    context.planFiles.length > 0;
  return hasContent ? context : null;
}

function collectPlanContextCandidates(
  value: unknown,
  candidates: Record<string, unknown>[],
  seen = new Set<unknown>(),
  depth = 0,
) {
  if (depth > 5 || seen.has(value)) return;
  const record = recordValue(value);
  if (!record) return;
  seen.add(value);
  if (hasPlanContextShape(record)) candidates.push(record);
  for (const key of [
    "plan_context",
    "task_blueprint",
    "execution_attempt",
    "execution_attempts",
    "data",
    "input_payload",
    "raw_result",
    "backend_result",
    "validation",
    "payload",
  ]) {
    collectPlanContextCandidates(record[key], candidates, seen, depth + 1);
  }
}

function latestExecutionAttemptForBlueprint(context: BlueprintPlanContext) {
  const attempts = context.executionAttempts ?? [];
  const matching = attempts.filter((attempt) => {
    if (context.blueprintRevisionId && attempt.blueprintRevisionId) {
      return attempt.blueprintRevisionId === context.blueprintRevisionId;
    }
    if (context.graphRevision !== null && attempt.blueprintRevision !== null) {
      return attempt.blueprintRevision === context.graphRevision;
    }
    return !context.blueprintId || !attempt.blueprintId || attempt.blueprintId === context.blueprintId;
  });
  return matching[matching.length - 1] ?? attempts[attempts.length - 1] ?? null;
}

function blueprintNodeStatusFromAttempt(value: string) {
  const status = value.trim().toLowerCase();
  if (["completed", "complete", "done", "superseded", "skipped"].includes(status)) {
    return "done";
  }
  if (["active", "running", "executing", "validating", "repairing"].includes(status)) {
    return "active";
  }
  if (["ready", "runnable"].includes(status)) return "ready";
  if (["blocked", "failed", "cancelled", "canceled"].includes(status)) return "blocked";
  if (["queued", "pending"].includes(status)) return "queued";
  return "planned";
}

function overlayExecutionAttemptNodeStates(context: BlueprintPlanContext) {
  if (!context.canonicalBlueprint) return context;
  const attempt = latestExecutionAttemptForBlueprint(context);
  if (!attempt || Object.keys(attempt.nodeStates).length === 0) return context;
  const readyTaskIds = new Set(context.readyTaskIds);
  const deferredTaskIds = new Set(context.deferredTaskIds);
  const activeTaskIds = new Set(context.activeTaskIds);
  const completedTaskIds = new Set(context.completedTaskIds);
  const taskGraph = context.taskGraph.map((task) => {
    const attemptState = attempt.nodeStates[task.taskId];
    if (!attemptState) return task;
    const status = blueprintNodeStatusFromAttempt(attemptState);
    readyTaskIds.delete(task.taskId);
    deferredTaskIds.delete(task.taskId);
    activeTaskIds.delete(task.taskId);
    completedTaskIds.delete(task.taskId);
    if (status === "ready") readyTaskIds.add(task.taskId);
    else if (status === "active") activeTaskIds.add(task.taskId);
    else if (status === "done") completedTaskIds.add(task.taskId);
    else if (status === "planned") deferredTaskIds.add(task.taskId);
    return { ...task, status, state: status };
  });
  return {
    ...context,
    taskGraph,
    readyTaskIds: [...readyTaskIds],
    deferredTaskIds: [...deferredTaskIds],
    activeTaskIds: [...activeTaskIds],
    completedTaskIds: [...completedTaskIds],
  };
}

function extractBlueprintPlanContext(
  events: ChatV2AgentRunEvent[],
  tasks: ChatV2TaskSnapshot[] = [],
) {
  const merged: BlueprintPlanContext = {
    taskGraph: [],
    readyTaskIds: [],
    deferredTaskIds: [],
    assignedTaskIds: [],
    activeTaskIds: [],
    completedTaskIds: [],
    parallelWorktreeTaskIds: [],
    dependencyRevisions: [],
    planFiles: [],
    planRootRelative: "",
    graphRevision: null,
    graphVersionId: "",
    graphRootVersionId: "",
    graphBaseVersionId: "",
    graphParentVersionIds: [],
    graphSource: "",
    graphUpdateReason: "",
    graphUpdateScope: "",
    graphChangedTaskIds: [],
    graphChangedBranchIds: [],
    parallelGroups: [],
    graphBranches: [],
    graphBranchRefs: [],
    planGenerationQueueLength: DEFAULT_PLAN_GENERATION_QUEUE_LENGTH,
    planExecutionQueueLength: DEFAULT_PLAN_EXECUTION_QUEUE_LENGTH,
    taskExecutionQueueLength: DEFAULT_TASK_EXECUTION_QUEUE_LENGTH,
  };
  let found = false;
  for (const event of events) {
    const candidates: Record<string, unknown>[] = [];
    collectPlanContextCandidates(eventPayload(event), candidates);
    for (const candidate of candidates) {
      const context = normalizePlanContext(candidate);
      if (!context) continue;
      found = true;
      const newerGraph =
        context.graphRevision !== null &&
        (merged.graphRevision === null || context.graphRevision >= merged.graphRevision);
      const unversionedGraph = context.graphRevision === null && merged.graphRevision === null;
      if (context.taskGraph.length > 0 && (newerGraph || unversionedGraph || merged.taskGraph.length === 0)) {
        merged.taskGraph = context.taskGraph;
      }
      if (context.readyTaskIds.length > 0) merged.readyTaskIds = context.readyTaskIds;
      if (context.deferredTaskIds.length > 0) merged.deferredTaskIds = context.deferredTaskIds;
      if (context.assignedTaskIds.length > 0) merged.assignedTaskIds = context.assignedTaskIds;
      if (context.activeTaskIds.length > 0) merged.activeTaskIds = context.activeTaskIds;
      if (context.completedTaskIds.length > 0) merged.completedTaskIds = context.completedTaskIds;
      if (context.parallelWorktreeTaskIds.length > 0) {
        merged.parallelWorktreeTaskIds = context.parallelWorktreeTaskIds;
      }
      if (context.dependencyRevisions.length > 0) {
        merged.dependencyRevisions = context.dependencyRevisions;
      }
      if (context.planFiles.length > 0) merged.planFiles = context.planFiles;
      if (context.planRootRelative) merged.planRootRelative = context.planRootRelative;
      if (context.canonicalBlueprint && (newerGraph || unversionedGraph || !merged.canonicalBlueprint)) {
        merged.canonicalBlueprint = true;
        merged.blueprintId = context.blueprintId;
        merged.blueprintTaskId = context.blueprintTaskId;
        merged.blueprintRevisionId = context.blueprintRevisionId;
        merged.taskFamily = context.taskFamily;
        merged.contract = context.contract;
        merged.graphRevision = context.graphRevision;
        merged.graphVersionId = context.graphVersionId;
        merged.graphRootVersionId = context.graphRootVersionId;
        merged.graphParentVersionIds = context.graphParentVersionIds;
        merged.graphSource = context.graphSource;
        merged.graphUpdateReason = context.graphUpdateReason;
        merged.graphUpdateScope = context.graphUpdateScope;
        merged.graphChangedTaskIds = context.graphChangedTaskIds;
        merged.graphChangedBranchIds = context.graphChangedBranchIds;
        merged.parallelGroups = context.parallelGroups;
        merged.graphBranches = context.graphBranches;
        merged.blueprintEdges = context.blueprintEdges;
        merged.entryNodeIds = context.entryNodeIds;
        merged.terminalNodeIds = context.terminalNodeIds;
        merged.boundedLoopNodeIds = context.boundedLoopNodeIds;
        merged.requiredCriterionIds = context.requiredCriterionIds;
        merged.uncoveredCriterionIds = context.uncoveredCriterionIds;
      }
      if (context.executionAttempts?.length) {
        const attemptsById = new Map(
          (merged.executionAttempts ?? []).map((attempt) => [attempt.attemptId, attempt]),
        );
        for (const attempt of context.executionAttempts) attemptsById.set(attempt.attemptId, attempt);
        merged.executionAttempts = [...attemptsById.values()];
      }
      if (newerGraph) {
        merged.graphRevision = context.graphRevision;
        merged.graphVersionId = context.graphVersionId;
        merged.graphRootVersionId = context.graphRootVersionId;
        merged.graphBaseVersionId = context.graphBaseVersionId;
        merged.graphParentVersionIds = context.graphParentVersionIds;
        merged.graphSource = context.graphSource;
        merged.graphUpdateReason = context.graphUpdateReason;
        merged.graphUpdateScope = context.graphUpdateScope;
        merged.graphChangedTaskIds = context.graphChangedTaskIds;
        merged.graphChangedBranchIds = context.graphChangedBranchIds;
        merged.parallelGroups = context.parallelGroups;
        merged.graphBranches = context.graphBranches;
        merged.graphBranchRefs = context.graphBranchRefs;
        merged.planGenerationQueueLength = context.planGenerationQueueLength;
        merged.planExecutionQueueLength = context.planExecutionQueueLength;
        merged.taskExecutionQueueLength = context.taskExecutionQueueLength;
      }
    }
  }
  for (const task of tasks) {
    const candidates: Record<string, unknown>[] = [];
    collectPlanContextCandidates(task.metadata, candidates);
    for (const candidate of candidates) {
      const context = normalizePlanContext(candidate);
      if (!context) continue;
      found = true;
      const newerGraph =
        context.graphRevision !== null &&
        (merged.graphRevision === null || context.graphRevision >= merged.graphRevision);
      const unversionedGraph = context.graphRevision === null && merged.graphRevision === null;
      if (context.taskGraph.length > 0 && (newerGraph || unversionedGraph || merged.taskGraph.length === 0)) {
        merged.taskGraph = context.taskGraph;
      }
      if (context.readyTaskIds.length > 0) merged.readyTaskIds = context.readyTaskIds;
      if (context.deferredTaskIds.length > 0) merged.deferredTaskIds = context.deferredTaskIds;
      if (context.activeTaskIds.length > 0) merged.activeTaskIds = context.activeTaskIds;
      if (context.completedTaskIds.length > 0) merged.completedTaskIds = context.completedTaskIds;
      if (context.canonicalBlueprint && (newerGraph || unversionedGraph || !merged.canonicalBlueprint)) {
        Object.assign(merged, context);
      }
      if (context.executionAttempts?.length) {
        const attemptsById = new Map(
          (merged.executionAttempts ?? []).map((attempt) => [attempt.attemptId, attempt]),
        );
        for (const attempt of context.executionAttempts) attemptsById.set(attempt.attemptId, attempt);
        merged.executionAttempts = [...attemptsById.values()];
      }
    }
  }
  if (found) {
    const attemptsById = new Map(
      (merged.executionAttempts ?? []).map((attempt) => [attempt.attemptId, attempt]),
    );
    for (const event of events) {
      for (const attempt of normalizeExecutionAttemptProjections(eventPayload(event))) {
        attemptsById.set(attempt.attemptId, attempt);
      }
    }
    for (const task of tasks) {
      for (const attempt of normalizeExecutionAttemptProjections(task.metadata)) {
        attemptsById.set(attempt.attemptId, attempt);
      }
    }
    merged.executionAttempts = [...attemptsById.values()];
  }
  return found ? overlayExecutionAttemptNodeStates(merged) : null;
}

function graphContextIdentity(context: BlueprintPlanContext, fallbackIndex: number) {
  const version =
    context.graphVersionId ||
    (context.graphRevision !== null ? `r${context.graphRevision}` : "");
  if (version) return version;
  return `snapshot-${fallbackIndex + 1}`;
}

function graphContextDedupKey(context: BlueprintPlanContext) {
  const version =
    context.graphVersionId ||
    (context.graphRevision !== null ? `r${context.graphRevision}` : "");
  if (version) return version;
  const taskSignature = context.taskGraph
    .map((task) => `${task.taskId}:${task.branchId || ""}:${task.state || task.status}`)
    .join("|");
  return [
    taskSignature,
    context.readyTaskIds.join(","),
    context.activeTaskIds.join(","),
    context.completedTaskIds.join(","),
    context.deferredTaskIds.join(","),
  ].join(";");
}

function extractBlueprintPlanContextHistory(
  events: ChatV2AgentRunEvent[],
  tasks: ChatV2TaskSnapshot[] = [],
) {
  const contexts: BlueprintPlanContext[] = [];
  const seen = new Set<string>();
  for (const event of events) {
    const candidates: Record<string, unknown>[] = [];
    collectPlanContextCandidates(eventPayload(event), candidates);
    for (const candidate of candidates) {
      const context = normalizePlanContext(candidate);
      if (!context || context.taskGraph.length === 0) continue;
      const key = graphContextDedupKey(context);
      if (seen.has(key)) continue;
      seen.add(key);
      contexts.push(context);
    }
  }
  for (const task of tasks) {
    const candidates: Record<string, unknown>[] = [];
    collectPlanContextCandidates(task.metadata, candidates);
    for (const candidate of candidates) {
      const context = normalizePlanContext(candidate);
      if (!context || context.taskGraph.length === 0) continue;
      const key = graphContextDedupKey(context);
      if (seen.has(key)) continue;
      seen.add(key);
      contexts.push(context);
    }
  }
  return contexts;
}

function graphHistoryWithLatest(
  history: BlueprintPlanContext[],
  latest: BlueprintPlanContext | null,
) {
  if (!latest || latest.taskGraph.length === 0) return history;
  const latestKey = graphContextDedupKey(latest);
  const existingIndex = history.findIndex((context) => graphContextDedupKey(context) === latestKey);
  if (existingIndex >= 0) {
    return history.map((context, index) => (index === existingIndex ? latest : context));
  }
  return [...history, latest];
}

function planTaskStateLooksActive(task: BlueprintPlanTask) {
  const state = (task.state || task.status || "").toLowerCase();
  return ["active", "generating", "planning", "executing", "running", "validating", "repairing"].includes(state);
}

function settlePlanContextAfterFinalResponse(context: BlueprintPlanContext | null) {
  if (!context) return context;
  const staleActiveIds = new Set(context.activeTaskIds);
  for (const task of context.taskGraph) {
    if (planTaskStateLooksActive(task)) staleActiveIds.add(task.taskId);
  }
  if (staleActiveIds.size === 0 && context.activeTaskIds.length === 0) return context;
  const completedIds = new Set([...context.completedTaskIds, ...staleActiveIds]);
  const taskGraph = context.taskGraph.map((task) =>
    staleActiveIds.has(task.taskId)
      ? {
          ...task,
          state: "done",
          status: "done",
        }
      : task,
  );
  return {
    ...context,
    taskGraph,
    activeTaskIds: [],
    completedTaskIds: [...completedIds],
    readyTaskIds: context.readyTaskIds.filter((taskId) => !completedIds.has(taskId)),
    deferredTaskIds: context.deferredTaskIds.filter((taskId) => !completedIds.has(taskId)),
  };
}

function settleLatestPlanContextHistoryAfterFinalResponse(history: BlueprintPlanContext[]) {
  if (history.length === 0) return history;
  return history.map((context, index) =>
    index === history.length - 1 ? settlePlanContextAfterFinalResponse(context) ?? context : context,
  );
}

function hasEventSource(events: ChatV2AgentRunEvent[], match: string | ((source: string) => boolean)) {
  return events.some((event) => {
    const source = eventSource(event);
    return typeof match === "string" ? source === match : match(source);
  });
}

function latestEventSource(events: ChatV2AgentRunEvent[]) {
  const latest = events[events.length - 1];
  return latest ? eventSource(latest) : "";
}

function eventPlanTaskId(event: ChatV2AgentRunEvent) {
  const payload = eventPayload(event);
  return (
    eventPayloadText(event, "plan_task_id") ||
    eventPayloadText(event, "owner_scope") ||
    (eventSource(event).startsWith("live.worktree") ? textValue(payload.task_id) : "")
  );
}

function completedPlanTaskIdsFromEvents(
  events: ChatV2AgentRunEvent[],
  planContext: BlueprintPlanContext | null,
) {
  const completed = new Set<string>();
  for (const taskId of planContext?.completedTaskIds ?? []) completed.add(taskId);
  for (const task of planContext?.taskGraph ?? []) {
    if (
      ["done", "complete", "completed", "x"].includes(task.status.toLowerCase()) ||
      ["done", "complete", "completed", "x"].includes((task.state || "").toLowerCase())
    ) {
      completed.add(task.taskId);
    }
  }
  for (const event of events) {
    const source = eventSource(event);
    const payload = eventPayload(event);
    if (
      source === "live.worktree_task.completed" ||
      source === "live.worktree.diff_applied" ||
      source === "super.worktree.diff_admitted"
    ) {
      const taskId = eventPlanTaskId(event);
      if (taskId) completed.add(taskId);
    }
    for (const taskId of [
      ...stringList(payload.applied_task_ids),
      ...stringList(recordValue(payload.raw_result)?.applied_task_ids),
    ]) {
      completed.add(taskId);
    }
  }
  if (completed.size === 0 && events.some((event) => event.type === "completed")) {
    for (const taskId of planContext?.assignedTaskIds ?? []) completed.add(taskId);
  }
  return completed;
}

function activePlanTaskIdsFromEvents(
  events: ChatV2AgentRunEvent[],
  planContext: BlueprintPlanContext | null,
  hasActiveRun: boolean,
) {
  if (!hasActiveRun) return new Set<string>();
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!event) continue;
    const source = eventSource(event);
    if (source === "live.worktree_task.completed" || terminalTaskStatuses.has(event.type)) continue;
    const taskId = eventPlanTaskId(event);
    if (taskId) return new Set([taskId]);
  }
  if (planContext?.activeTaskIds.length) return new Set(planContext.activeTaskIds);
  if (planContext?.assignedTaskIds.length) return new Set(planContext.assignedTaskIds);
  if (planContext?.readyTaskIds.length) return new Set([planContext.readyTaskIds[0]!]);
  return new Set<string>();
}

function visiblePlanGraphTasks(context: BlueprintPlanContext) {
  if (!isSemanticPlanContext(context)) return [];
  if (context.canonicalBlueprint) return context.taskGraph;
  return hasExplicitPlanTasks(context)
    ? context.taskGraph.filter(isBlueprintPlanTaskPlan)
    : [];
}

function taskBelongsToPlan(task: BlueprintPlanTask, planTaskId: string) {
  if (task.taskId === planTaskId) return false;
  if (task.planId === planTaskId) return true;
  if (task.parentId === planTaskId) return true;
  return task.taskId.startsWith(`${planTaskId}-`);
}

function planChildTasksForNode(node: BlueprintNode) {
  const context = node.graphContext;
  const planTaskId = node.graphTaskId;
  if (!context || !planTaskId) return [];
  return context.taskGraph.filter(
    (task) => !isBlueprintPlanTaskPlan(task) && taskBelongsToPlan(task, planTaskId),
  );
}

function planTaskById(context: BlueprintPlanContext | undefined, taskId: string | undefined) {
  if (!context || !taskId) return null;
  return context.taskGraph.find((task) => task.taskId === taskId) ?? null;
}

function graphTaskKind(task: BlueprintPlanTask, context: BlueprintPlanContext): BlueprintNodeKind {
  if (isBlueprintPlanTaskPlan(task)) return "plan";
  const semanticRole = `${task.topologyRole || ""}_${task.nodeType || ""}`
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  const genericKind = (task.nodeType || "").toLowerCase().replace(/[\s-]+/g, "_");
  if (/approval|signoff|authorization/.test(semanticRole)) return "approval";
  if (/hypothesis|diagnosis|probe/.test(semanticRole)) return "hypothesis";
  if (/evidence|source|claim|finding|synthesis/.test(semanticRole)) return "evidence";
  if (/variant|concept|prototype|option|alternative/.test(semanticRole)) return "variant";
  if (/decision|choice|resolution|owner/.test(semanticRole)) return "decision";
  if (/artifact|output|deliverable|document|specification/.test(semanticRole)) return "artifact";
  if (/loop|iterate|revision_cycle/.test(semanticRole) || genericKind === "loop") return "loop";
  if (/composite|collapse|group/.test(semanticRole) || genericKind === "composite") {
    return "composite";
  }
  if (/gate|check|validator|validation|compliance|feasibility/.test(semanticRole)) return "gate";
  return context.parallelWorktreeTaskIds.includes(task.taskId) ? "worktree" : "task";
}

function graphTaskNodeId(task: BlueprintPlanTask) {
  return `blueprint:task:${task.taskId}`;
}

function taskBody(task: BlueprintPlanTask, context: BlueprintPlanContext) {
  const lines = [task.description || task.goal];
  const dependsOn = task.dependsOn ?? [];
  const generationDependsOn = task.generationDependsOn ?? [];
  const branchLine = [
    task.nodeType ? `type ${task.nodeType}` : "",
    task.planId ? `plan \`${task.planId}\`` : "",
    task.parentId ? `parent \`${task.parentId}\`` : "",
    task.branchId ? `branch \`${task.branchId}\`` : "",
    task.state ? `state ${task.state}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  if (branchLine) lines.push("", branchLine);
  if (dependsOn.length > 0) {
    lines.push("", `Depends on: ${dependsOn.map((item) => `\`${item}\``).join(", ")}`);
  }
  if (generationDependsOn.length > 0) {
    lines.push(
      "",
      `Generation waits for: ${generationDependsOn.map((item) => `\`${item}\``).join(", ")}`,
    );
  }
  if (task.ownedPaths.length > 0) {
    lines.push("", "Owned paths:", ...task.ownedPaths.map((path) => `- \`${path}\``));
  }
  if (task.deliverables.length > 0) {
    lines.push("", "Deliverables:", ...task.deliverables.map((path) => `- \`${path}\``));
  }
  if (task.validation.length > 0) {
    lines.push("", "Validation:", ...task.validation.map((check) => `- ${check}`));
  }
  if (task.capabilityRequirements?.length) {
    lines.push(
      "",
      "Required capabilities:",
      ...task.capabilityRequirements.map((capability) => `- ${capability}`),
    );
  }
  if (task.criterionIds?.length) {
    lines.push("", `Criteria: ${task.criterionIds.map((id) => `\`${id}\``).join(", ")}`);
  }
  if (task.loopPolicy?.length) {
    lines.push("", "Loop policy:", ...task.loopPolicy.map((item) => `- ${item}`));
  }
  if (task.supersedes?.length) {
    lines.push("", `Supersedes: ${task.supersedes.map((id) => `\`${id}\``).join(", ")}`);
  }
  if (task.supersededBy?.length) {
    lines.push("", `Superseded by: ${task.supersededBy.map((id) => `\`${id}\``).join(", ")}`);
  }
  if (context.dependencyRevisions.length > 0) {
    lines.push("", "Dependency revisions:", ...context.dependencyRevisions.map((item) => `- ${item}`));
  }
  return lines.join("\n");
}

function compactTaskDetail(task: BlueprintPlanTask) {
  const dependsOn = task.dependsOn ?? [];
  const paths = uniqueStringList([...task.ownedPaths, ...task.deliverables]).slice(0, 2);
  const suffix = paths.length > 0 ? ` · ${paths.join(", ")}` : "";
  const branch = task.branchId ? `branch ${task.branchId} · ` : "";
  const topology = task.topologyRole ? `${task.topologyRole.replace(/_/g, " ")} · ` : "";
  const dependency = dependsOn.length > 0
    ? `after ${dependsOn.join(", ")}`
    : isBlueprintPlanTaskPlan(task)
      ? "no execution blockers"
      : "ready when reached";
  return `${topology}${branch}${dependency}${suffix}`;
}

function phaseStatus(args: {
  started: boolean;
  completed: boolean;
  failed?: boolean;
  active: boolean;
  planned: boolean;
}): BlueprintNodeStatus {
  if (args.failed) return "blocked";
  if (args.completed) return "done";
  if (args.started && args.active) return "active";
  if (args.started) return "ready";
  return args.planned ? "future" : "queued";
}

function eventStartsExecutionPhase(source: string) {
  return [
    "live.generic_execution.started",
    "live.generic_build.started",
    "live.website_build.started",
    "live.builder_retry.started",
    "live.answer_recovery.started",
  ].includes(source);
}

function eventCompletesExecutionPhase(source: string) {
  return (
    source.includes("generic_execution.completed") ||
    source.includes("generic_build.completed") ||
    source.includes("website_build.completed") ||
    source.includes("builder_retry.completed") ||
    source.includes("answer_recovery.completed")
  );
}

function eventStartsToolWork(source: string) {
  return [
    "tool.started",
    "tool.completed",
    "tool.failed",
    "tool.denied",
    "tool.policy_denied",
  ].includes(source);
}

function threadTitleLooksPlaceholder(title: string) {
  const normalized = title.trim().toLowerCase();
  return Boolean(
    !normalized ||
      /^new\s+(super\s+dan\s+)?session$/.test(normalized) ||
      /^untitled(?:\s+(?:dan\s+super\s+)?session|\s+chat)?$/.test(normalized) ||
      normalized === "workspace thread",
  );
}

function textForbidsWorkspaceMutation(text: string) {
  const normalized = " ".concat(text.trim().toLowerCase().replace(/\s+/g, " "), " ");
  if (!normalized.trim()) return false;
  return (
    /\bread[-\s]?only\b/.test(normalized) ||
    /\bdo\s+not\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:anything|any\s+files?|any\s+workspace\s+files?|workspace\s+files?|files?)\b/.test(normalized) ||
    /\bdon['’]?t\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:anything|any\s+files?|any\s+workspace\s+files?|workspace\s+files?|files?)\b/.test(normalized) ||
    /\bdont\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:anything|any\s+files?|any\s+workspace\s+files?|workspace\s+files?|files?)\b/.test(normalized) ||
    /\bwithout\s+(?:editing|modifying|changing|writing|creating|deleting|touching|mutating)\s+(?:anything|any\s+files?|any\s+workspace\s+files?|workspace\s+files?|files?)\b/.test(normalized) ||
    /\bno\s+(?:file\s+)?(?:edits?|writes?|changes?|modifications?|mutations?)\b/.test(normalized)
  );
}

function textRequestsWorkspaceMutation(text: string) {
  const normalized = " ".concat(text.trim().toLowerCase().replace(/\s+/g, " "), " ");
  if (!normalized.trim()) return false;
  return (
    /\b(?:edit|modify|change|write|create|delete|touch|mutate|fix|repair|patch|patching|implement|build|add|update|save|export|materialize|redesign|refactor|enhance|enrich|complete|develop|code|clean)\b/.test(normalized) ||
    /\bmake\s+(?:a\s+)?(?:change|changes|edit|edits|fix|fixes|patch|patches|improvement|improvements)\b/.test(normalized) ||
    /\bmake\s+(?:a|an|the)\s+[^.?!]*(?:animation|demo|app|application|website|site|page|report|artifact|file|tool|component)\b/.test(normalized) ||
    /\bmake\s+[^.?!]*\bmore\s+\w+/.test(normalized) ||
    /\bmake\s+sure\s+(?:the\s+)?(?:website|site|app|application|game|software|code)\b/.test(normalized) ||
    /\bimprove\s+[^.?!]*\binto\b/.test(normalized) ||
    /\b(?:produce|generate)\s+(?:a\s+)?(?:[\w-]+\s+){0,4}(?:file|artifact|document|markdown|report|memo|patch|diff)\b/.test(normalized)
  );
}

function textRequestsProjectAnswer(text: string) {
  const normalized = " ".concat(text.trim().toLowerCase().replace(/\s+/g, " "), " ");
  if (!normalized.trim()) return false;
  const projectTarget = "(?:project|repo|repository|codebase|workspace)";
  return (
    new RegExp(`\\bwhat\\s+(?:is|does|are)\\s+(?:this|the)\\s+${projectTarget}\\s+(?:about|do|for)\\b`).test(normalized) ||
    new RegExp(`\\btell\\s+me\\s+about\\s+(?:this|the)\\s+${projectTarget}\\b`).test(normalized) ||
    new RegExp(`\\b${projectTarget}\\s+(?:summary|overview)\\b`).test(normalized) ||
    new RegExp(`\\b(?:summarize|summarise|summary)\\s+(?:of\\s+)?(?:this|the)?\\s*${projectTarget}\\b`).test(normalized) ||
    new RegExp(`\\b(?:give|write|create)\\s+(?:me\\s+)?(?:a\\s+)?(?:brief\\s+)?(?:summary|overview)\\s+of\\s+(?:this|the)\\s+${projectTarget}\\b`).test(normalized) ||
    new RegExp(`\\bhelp\\s+me\\s+(?:understand|summarize|summarise|summary)\\s+(?:what\\s+)?(?:this|the)?\\s*${projectTarget}\\s*(?:is\\s+)?(?:about)?\\b`).test(normalized)
  );
}

function textExplicitlyRequestsSavedAnswerArtifact(text: string) {
  const normalized = " ".concat(text.trim().toLowerCase().replace(/\s+/g, " "), " ");
  if (!normalized.trim()) return false;
  return (
    /(?<!\w)[\w./~-]+\.(?:md|txt|json|html|py|ts|tsx|jsx|csv|yaml|yml|toml)\b/.test(normalized) ||
    /\b(?:readme|file|artifact|document|markdown|md)\b/.test(normalized) ||
    /\b(?:edit|modify|change|update|fix|repair|implement|build|add|delete|touch|mutate)\b/.test(normalized) ||
    /\b(?:save|export|materialize)\b/.test(normalized)
  );
}

function textRequestsAssessmentOnly(text: string) {
  const normalized = " ".concat(text.trim().toLowerCase().replace(/\s+/g, " "), " ");
  if (!normalized.trim()) return false;
  const projectAnswer = textRequestsProjectAnswer(normalized);
  if (textRequestsWorkspaceMutation(normalized)) {
    if (!projectAnswer || textExplicitlyRequestsSavedAnswerArtifact(normalized)) return false;
  }
  return (
    /\b(?:review|audit|assess|evaluate|inspect|check|look\s+over|analyze|analyse|summarize|summarise|explain)\b/.test(normalized) ||
    /\bwhat\s+(?:do\s+you\s+think|is\s+going\s+on|is\s+the\s+status)\b/.test(normalized) ||
    /\bhelp\s+me\s+(?:understand|review|audit|assess|evaluate|inspect|check|analyze|analyse|summarize|summarise|summary)\b/.test(normalized) ||
    projectAnswer
  );
}

function textForbidsShellCommands(text: string) {
  const normalized = " ".concat(text.trim().toLowerCase().replace(/\s+/g, " "), " ");
  if (!normalized.trim()) return false;
  return (
    /\bdo\s+not\s+run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b/.test(normalized) ||
    /\bdo\s+not\b[^.?!]*(?:\bor\s+)?run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b/.test(normalized) ||
    /\bdon['’]?t\s+run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b/.test(normalized) ||
    /\bdon['’]?t\b[^.?!]*(?:\bor\s+)?run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b/.test(normalized) ||
    /\bdont\s+run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b/.test(normalized) ||
    /\bdont\b[^.?!]*(?:\bor\s+)?run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b/.test(normalized) ||
    /\bwithout\s+running\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b/.test(normalized) ||
    /\bno\s+(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b/.test(normalized)
  );
}

function requestTargetPaths(text: string) {
  const matches = [
    ...text.matchAll(
      /(?<![:/\w$])(?:\/|~\/(?:[^/\s"'`,;:()[\]{}<>]+\/)*)[^/\s"'`,;:()[\]{}<>]+(?:\/[^/\s"'`,;:()[\]{}<>]+)*/g,
    ),
    ...text.matchAll(
      /(?<![\w$])(?:[./~\w-]+\/)?[\w.-]+\.(?:html|css|js|md|txt|json|jsonl|yaml|yml|toml|ts|tsx|jsx|py|sh|csv)/g,
    ),
  ];
  return uniqueStringList(matches.map((match) => match[0].replace(/[.,;:()[\]{}"'?]+$/g, "")));
}

function addRequestTask(
  tasks: BlueprintPlanTask[],
  taskId: string,
  goal: string,
  dependsOn: string[],
  options: {
    ownedPaths?: string[];
    deliverables?: string[];
    validation?: string[];
    parallelSafe?: boolean;
  } = {},
) {
  tasks.push({
    taskId,
    goal,
    dependsOn,
    ownedPaths: uniqueStringList(options.ownedPaths ?? []),
    deliverables: uniqueStringList(options.deliverables ?? []),
    validation: uniqueStringList(options.validation ?? []),
    status: "projected",
    parallelSafe: options.parallelSafe ?? true,
  });
}

function deriveRequestPlanContext(text: string): BlueprintPlanContext | null {
  if (!text.trim() || textForbidsWorkspaceMutation(text) || !textRequestsWorkspaceMutation(text)) return null;
  const targets = requestTargetPaths(text);
  if (targets.length === 0) return null;
  const folderTargets = targets.filter((path) => !/\.[a-z0-9]+$/i.test(path));
  const mentionedFiles = targets.filter((path) => /\.[a-z0-9]+$/i.test(path));
  const primaryTarget = targets[0] ?? "";

  const tasks: BlueprintPlanTask[] = [];
  addRequestTask(tasks, "1", "Understand explicit target scope", [], {
    ownedPaths: targets,
    deliverables: targets,
    validation: ["Targets are carried into execution context"],
    parallelSafe: false,
  });
  addRequestTask(tasks, "2", "Execute the current target slice", ["1"], {
    ownedPaths: mentionedFiles.length > 0 ? mentionedFiles : folderTargets,
    deliverables: mentionedFiles.length > 0 ? mentionedFiles : folderTargets,
    validation: ["Current slice has concrete evidence before it is treated as done"],
    parallelSafe: false,
  });
  addRequestTask(tasks, "3", "Validate and summarize target coverage", ["2"], {
    ownedPaths: [],
    deliverables: targets,
    validation: ["Final response states covered targets, evidence, blockers, and remaining work"],
    parallelSafe: false,
  });

  const taskIds = tasks.map((task) => task.taskId);
  return {
    taskGraph: tasks,
    readyTaskIds: taskIds.slice(0, 1),
    deferredTaskIds: taskIds.slice(1),
    assignedTaskIds: [],
    activeTaskIds: [],
    completedTaskIds: [],
    parallelWorktreeTaskIds: [],
    dependencyRevisions: [],
    planFiles: [],
    planRootRelative: primaryTarget
      ? `request target: ${primaryTarget}`
      : "request-derived blueprint",
    graphRevision: null,
    graphVersionId: "",
    graphRootVersionId: "",
    graphBaseVersionId: "",
    graphParentVersionIds: [],
    graphSource: "",
    graphUpdateReason: "",
    graphUpdateScope: "",
    graphChangedTaskIds: [],
    graphChangedBranchIds: [],
    parallelGroups: [],
    graphBranches: [],
    graphBranchRefs: [],
    planGenerationQueueLength: DEFAULT_PLAN_GENERATION_QUEUE_LENGTH,
    planExecutionQueueLength: DEFAULT_PLAN_EXECUTION_QUEUE_LENGTH,
    taskExecutionQueueLength: DEFAULT_TASK_EXECUTION_QUEUE_LENGTH,
  };
}

function requestPreviewBody(args: {
  requestBody: string;
  understanding: Record<string, unknown> | null;
  operatorContexts: Record<string, unknown>[];
  readOnlyRun: boolean;
  assessmentOnlyRun: boolean;
  noShellRun: boolean;
}) {
  const records = [args.understanding, ...args.operatorContexts];
  const targetItems = uniqueStringList([
    ...collectFieldItems(records, ["target_paths", "target_artifacts"]),
    ...requestTargetPaths(args.requestBody),
  ]);
  const constraints = collectFieldItems(records, [
    "hard_constraints",
    "soft_constraints",
    "constraints",
  ]);
  if (args.assessmentOnlyRun) {
    constraints.push("Review response expected; file edits are not expected unless requested.");
  } else if (args.readOnlyRun) {
    constraints.push("Workspace file mutation is not expected for this request.");
  }
  if (args.noShellRun) constraints.push("Shell or terminal commands are not expected for this request.");
  return detailMarkdown("", [
    { title: "Targets", items: targetItems },
    { title: "Constraints", items: constraints },
    {
      title: "Validation Expectations",
      items: collectFieldItems(records, [
        "validation_requirements",
        "confidence_scoped_acceptance",
      ]),
    },
  ]);
}

function understandingPreviewBody(args: {
  requestBody: string;
  understanding: Record<string, unknown> | null;
  operatorContexts: Record<string, unknown>[];
}) {
  const records = [args.understanding, ...args.operatorContexts];
  const source = scalarDetailText(args.understanding?.source);
  const sourceLabel =
    source === "model_authored"
      ? "tailored by DAN"
      : source === "rule_generation_brief"
        ? "generating rules"
        : source === "deterministic_scaffold"
          ? "initial scaffold"
        : "";
  const requestKind = scalarDetailText(args.understanding?.request_kind);
  const summary = requestKind
    ? `Request kind: ${requestKind}${sourceLabel ? ` · ${sourceLabel}` : ""}`
    : "DAN is establishing the request scope, target, constraints, and evidence gates.";
  return detailMarkdown(summary, [
    {
      title: "Rules Brief",
      items: collectFieldItems(records, ["rule_generation_brief"]),
    },
    {
      title: "Aspect Review",
      items: collectFieldItems(records, ["aspect_reviews"]),
    },
    {
      title: "Acceptance Criteria",
      items: collectFieldItems(records, ["confidence_scoped_acceptance"]),
    },
    {
      title: "Stop Rule",
      items: collectFieldItems(records, ["stop_rule"]),
    },
    {
      title: "Original Request",
      items: uniqueStringList([
        scalarDetailText(args.understanding?.original_request),
        args.requestBody,
      ]),
    },
  ]);
}

function countNoun(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function statusSummaryForTasks(tasks: BlueprintPlanTask[], context: BlueprintPlanContext) {
  const counts = new Map<BlueprintNodeStatus, number>();
  for (const task of tasks) {
    const status = statusForPlanTask(task, context);
    counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  return (["active", "ready", "done", "future", "queued", "blocked"] as const)
    .map((status) => {
      const count = counts.get(status) ?? 0;
      return count > 0 ? `${count} ${graphTaskStatusLabel(status)}` : "";
    })
    .filter(Boolean)
    .join(", ");
}

function taskGoalForGroup(task: BlueprintPlanTask | undefined, fallbackId: string) {
  return truncateReadableText(task?.goal || fallbackId, 92);
}

function parallelGroupSummaryItems(
  planContext: BlueprintPlanContext,
  options: { maxTasksPerGroup?: number } = {},
) {
  const maxTasksPerGroup = options.maxTasksPerGroup ?? 3;
  const taskById = new Map(planContext.taskGraph.map((task) => [task.taskId, task]));
  return planContext.parallelGroups.map((group, index) => {
    const knownTasks = group
      .map((taskId) => taskById.get(taskId))
      .filter((task): task is BlueprintPlanTask => Boolean(task));
    const branches = uniqueStringList(knownTasks.map((task) => task.branchId || "").filter(Boolean));
    const branchText =
      branches.length === 0
        ? ""
        : branches.length === 1
          ? ` · branch ${branches[0]}`
          : ` · ${countNoun(branches.length, "branch")}`;
    const statusText = knownTasks.length > 0 ? statusSummaryForTasks(knownTasks, planContext) : "";
    const sample = group
      .slice(0, maxTasksPerGroup)
      .map((taskId) => taskGoalForGroup(taskById.get(taskId), taskId));
    const hiddenCount = Math.max(0, group.length - sample.length);
    const sampleText =
      sample.length > 0
        ? ` Includes ${sample.join("; ")}${hiddenCount > 0 ? `; ${countNoun(hiddenCount, "more task")}.` : "."}`
        : "";
    return [
      `Group ${index + 1}: ${countNoun(group.length, "task")} can run together${branchText}`,
      statusText ? ` · ${statusText}` : "",
      ".",
      sampleText,
    ].join("");
  });
}

function planPreviewBody(planContext: BlueprintPlanContext | null) {
  if (!planContext) return "Waiting for the planner to emit a task graph or frontier metadata.";
  if (!isSemanticPlanContext(planContext)) {
    return "Execution activity is being tracked as live status. The semantic plan graph appears here when DAN emits plans, subplans, or plan items.";
  }
  const graphLabel =
    planContext.graphVersionId ||
    (planContext.graphRevision !== null ? `r${planContext.graphRevision}` : "");
  const revisionItems = uniqueStringList([
    graphLabel
      ? `${graphLabel}${planContext.graphSource ? ` · ${planContext.graphSource}` : ""}${planContext.graphUpdateScope ? ` · ${planContext.graphUpdateScope}` : ""}`
      : "",
    planContext.graphRootVersionId ? `Root: ${planContext.graphRootVersionId}` : "",
    planContext.graphBaseVersionId ? `Base: ${planContext.graphBaseVersionId}` : "",
    planContext.graphParentVersionIds.length
      ? `Parents: ${planContext.graphParentVersionIds.join(", ")}`
      : "",
    planContext.graphRevision !== null ? `Event order: r${planContext.graphRevision}` : "",
    planContext.graphUpdateReason,
    planContext.graphChangedBranchIds.length
      ? `Branches: ${planContext.graphChangedBranchIds.map((id) => `\`${id}\``).join(", ")}`
      : "",
    planContext.graphChangedTaskIds.length
      ? `Changed: ${planContext.graphChangedTaskIds.map((id) => `\`${id}\``).join(", ")}`
      : "",
  ]);
  const contractSections: BlueprintDetailSection[] = planContext.canonicalBlueprint
    ? [
        { title: "Goal", items: planContext.contract?.goal ? [planContext.contract.goal] : [] },
        { title: "Non-goals", items: planContext.contract?.nonGoals ?? [] },
        { title: "Constraints", items: planContext.contract?.constraints ?? [] },
        { title: "Permissions", items: planContext.contract?.permissions ?? [] },
        { title: "Risks", items: planContext.contract?.risks ?? [] },
        { title: "Budget", items: planContext.contract?.budget ?? [] },
        { title: "Acceptance Criteria", items: planContext.contract?.acceptanceCriteria ?? [] },
      ]
    : [];
  const topologyItems = (planContext.blueprintEdges ?? []).map((edge) =>
    `${edge.from} → ${edge.to} · ${edge.kind || "dependency"}${edge.condition ? ` · ${edge.condition}` : ""}`,
  );
  return detailMarkdown(
    planContext.canonicalBlueprint
      ? "The blueprint carries the task contract and semantic route. Execution details belong to attempts."
      : "Planning determines the next visible frontier without claiming future work is done.",
    [
    ...contractSections,
    {
      title: "Graph Version",
      items: revisionItems,
    },
    {
      title: "Plan Files",
      items: planContext.planFiles.map((path) => `\`${path}\``),
    },
    ...(planContext.canonicalBlueprint
      ? []
      : [
          {
            title: "Queue Limits",
            items: [
              `Plan generation: ${planContext.planGenerationQueueLength}`,
              `Plan execution: ${planContext.planExecutionQueueLength}`,
              `Task execution: ${planContext.taskExecutionQueueLength}`,
            ],
          },
        ]),
    {
      title: "Ready Frontier",
      items: planContext.readyTaskIds.map((id) => `\`${id}\``),
    },
    {
      title: "Active Frontier",
      items: planContext.activeTaskIds.map((id) => `\`${id}\``),
    },
    {
      title: "Completed",
      items: planContext.completedTaskIds.map((id) => `\`${id}\``),
    },
    {
      title: "Parallel Groups",
      items: parallelGroupSummaryItems(planContext),
    },
    {
      title: "Branches",
      items: planContext.graphBranches,
    },
    {
      title: "Branch Refs",
      items: planContext.graphBranchRefs,
    },
    {
      title: "Topology",
      items: topologyItems,
    },
    {
      title: "Criterion Gaps",
      items: (planContext.uncoveredCriterionIds ?? []).map((id) => `\`${id}\``),
    },
    {
      title: "Deferred Frontier",
      items: planContext.deferredTaskIds.map((id) => `\`${id}\``),
    },
    {
      title: planContext.canonicalBlueprint ? "Blueprint Nodes" : "Projected Tasks",
      items: planContext.taskGraph.map((task) => `${task.taskId}: ${task.goal}`),
    },
  ]);
}

function recentActivityItems(events: ChatV2AgentRunEvent[]) {
  return uniqueStringList(
    events
      .slice(-8)
      .map(eventActivityLine)
      .filter(Boolean),
  );
}

function executionPreviewBody(args: {
  intro: string;
  events: ChatV2AgentRunEvent[];
  tasks: ChatV2TaskSnapshot[];
  activeTask: ChatV2TaskSnapshot | null;
}) {
  const taskItems = args.tasks.map((task) => {
    const label = taskMessageLabel(task);
    return `${task.status}${task.phase ? ` · ${task.phase}` : ""}${label ? `: ${label}` : ""}`;
  });
  const artifactItems = args.tasks.flatMap((task) =>
    task.latest_artifact_refs.flatMap((artifact) => detailItemsFromValue(artifact)),
  );
  return detailMarkdown(args.intro, [
    {
      title: "Active Task",
      items: args.activeTask
        ? [`${args.activeTask.task_id}: ${taskProgressLabel(args.activeTask)}`]
        : [],
    },
    { title: "Recent Activity", items: recentActivityItems(args.events) },
    { title: "Task State", items: taskItems },
    { title: "Artifacts", items: artifactItems },
  ]);
}

function validationPreviewBody(args: {
  latestValidation: ChatV2AgentRunEvent | undefined;
  understanding: Record<string, unknown> | null;
}) {
  const payload = args.latestValidation ? eventPayload(args.latestValidation) : {};
  const validationResultItems = uniqueStringList([
    typeof payload.passed === "boolean" ? (payload.passed ? "Passed" : "Needs attention") : "",
    ...collectFieldItems([payload], [
      "summary",
      "comparison_note",
      "validation_summary",
      "message",
      "status",
    ]),
  ]);
  const scopeItems = uniqueStringList([
    ...collectFieldItems([payload], ["validation_scope", "completion_scope"]),
    ...collectFieldItems([payload], ["validated_branch_ids"]).map((item) => `Branches: ${item}`),
    ...collectFieldItems([payload], ["validated_task_ids"]).map((item) => `Tasks: ${item}`),
  ]);
  const deterministicItems = uniqueStringList([
    ...collectFieldItems([payload], ["deterministic_checks"]),
    ...collectFieldItems([payload], ["deterministic_failures"]).map((item) => `Failed: ${item}`),
    ...collectFieldItems([payload], ["changed_required_files"]).map((item) => `Checked change: ${item}`),
  ]);
  const semanticItems = collectFieldItems([payload], ["llm_semantic_checks"]);
  const branchItems = collectFieldItems([payload], [
    "branch_results",
    "blocking_current_task_failures",
    "deferred_task_gaps",
    "ready_next_task_ids",
    "remaining_work",
  ]);
  const graphItems = collectFieldItems([payload], [
    "graph_level_validation",
    "task_graph_update",
    "dependency_revisions",
  ]);
  const acceptanceItems = collectFieldItems([args.understanding], ["confidence_scoped_acceptance"]);
  const rulesBriefItems = collectFieldItems([args.understanding], ["rule_generation_brief"]);
  return detailMarkdown(
    args.latestValidation && humanEventSummary(args.latestValidation)
      ? humanEventSummary(args.latestValidation)
      : "Validation checks the current frontier before final response.",
    [
      {
        title: "Validation Result",
        items: validationResultItems,
      },
      {
        title: "Validated Scope",
        items: scopeItems,
      },
      {
        title: "Deterministic Checks",
        items: deterministicItems,
      },
      {
        title: "LLM Semantic Checks",
        items: semanticItems,
      },
      {
        title: "Branch Results",
        items: branchItems,
      },
      {
        title: "Graph Validation",
        items: graphItems,
      },
      {
        title: "Aspect Coverage",
        items: collectFieldItems([payload], ["aspect_coverage"]),
      },
      {
        title: "Acceptance Criteria",
        items: acceptanceItems,
      },
      {
        title: "Rules Brief",
        items: acceptanceItems.length > 0 ? [] : rulesBriefItems,
      },
    ],
  );
}

function chunkMatchesRun(chunk: WorkspaceChunk, activeRunId: string) {
  return !activeRunId || !chunk.runId || chunk.runId === activeRunId;
}

function isFinalAnswerChunk(chunk: WorkspaceChunk) {
  if (chunk.kind !== "agent") return false;
  if (/outcome/i.test(chunk.title) || /^agent-outcome:/.test(chunk.id)) return false;
  if (isRuntimeOutputChunkLimitText(chunk.body) || isGraphTelemetryText(chunk.body)) return false;
  return /answer|terminal/i.test(chunk.title) || /^agent-(answer|terminal):/.test(chunk.id);
}

function isOutcomeChunk(chunk: WorkspaceChunk) {
  return chunk.kind === "agent" && (/outcome/i.test(chunk.title) || /^agent-outcome:/.test(chunk.id));
}

function latestMatchingChunk(
  chunks: WorkspaceChunk[],
  activeRunId: string,
  predicate: (chunk: WorkspaceChunk) => boolean,
) {
  return [...chunks]
    .reverse()
    .find((chunk) => predicate(chunk) && chunkMatchesRun(chunk, activeRunId));
}

function conversationUserChunks(chunks: WorkspaceChunk[], limit = 4) {
  return chunks
    .filter(
      (chunk) =>
        chunk.kind === "chat" &&
        chunk.role === "user" &&
        Boolean(chunk.body.trim()),
    )
    .slice(-limit);
}

function markdownListItems(content: string) {
  return uniqueStringList(
    content
      .split(/\r?\n/)
      .map((line) => line.trim().replace(/^[-*]\s+/, ""))
      .filter(Boolean),
  );
}

function buildBlueprintNodesForRunScope(args: {
  tasks: ChatV2TaskSnapshot[];
  agentEvents: ChatV2AgentRunEvent[];
  chunks: WorkspaceChunk[];
  activeRunId: string;
  activeRunningTask: ChatV2TaskSnapshot | null;
  queueRows: QueueRow[];
  activeThreadTitle: string;
}) {
  const {
    tasks,
    agentEvents,
    chunks,
    activeRunId: liveActiveRunId,
    activeRunningTask: liveActiveRunningTask,
    queueRows,
    activeThreadTitle,
  } = args;
  const queuedMessageChunkIds = queueSourceChunkIds(queueRows);
  const latestUserIndex = (() => {
    for (let index = chunks.length - 1; index >= 0; index -= 1) {
      const chunk = chunks[index];
      if (chunk?.role !== "user") continue;
      if (queuedMessageChunkIds.has(chunk.id)) continue;
      return index;
    }
    return -1;
  })();
  const latestUserChunk = latestUserIndex >= 0 ? chunks[latestUserIndex] : undefined;
  const priorAnswerIndex = (() => {
    if (latestUserIndex < 0) return -1;
    for (let index = latestUserIndex - 1; index >= 0; index -= 1) {
      const chunk = chunks[index];
      if (chunk && isFinalAnswerChunk(chunk)) return index;
    }
    return -1;
  })();
  const priorAnswerChunk = priorAnswerIndex >= 0 ? chunks[priorAnswerIndex] : undefined;
  const primaryUserIndex = (() => {
    if (priorAnswerIndex < 0) return -1;
    for (let index = priorAnswerIndex - 1; index >= 0; index -= 1) {
      if (chunks[index]?.role === "user") return index;
    }
    return -1;
  })();
  const appendingActiveFollowUp = Boolean(
    liveActiveRunId &&
      liveActiveRunningTask &&
      latestUserChunk &&
      priorAnswerChunk?.runId &&
      priorAnswerChunk.runId !== liveActiveRunId,
  );
  const primaryUserChunk =
    appendingActiveFollowUp && primaryUserIndex >= 0
      ? chunks[primaryUserIndex]
      : latestUserChunk;
  const activeRunId = appendingActiveFollowUp ? priorAnswerChunk?.runId || "" : liveActiveRunId;
  const activeRunningTask = appendingActiveFollowUp ? null : liveActiveRunningTask;
  const hasActiveRun = Boolean(activeRunId && activeRunningTask);
  const shouldScopeToRun = Boolean(
    activeRunId &&
      (appendingActiveFollowUp ||
        agentEvents.some((event) => event.run_id === activeRunId) ||
        tasks.some((task) => taskRunId(task) === activeRunId)),
  );
  const activeRunEvents =
    shouldScopeToRun
      ? agentEvents.filter((event) => event.run_id === activeRunId)
      : agentEvents;
  const activeRunTasks =
    shouldScopeToRun
      ? tasks.filter((task) => taskRunId(task) === activeRunId)
      : tasks;
  const extractedPlanContext = extractBlueprintPlanContext(activeRunEvents, activeRunTasks);
  const emittedPlanContext = isSemanticPlanContext(extractedPlanContext) ? extractedPlanContext : null;
  const latestAnswerChunk = latestMatchingChunk(chunks, activeRunId, isFinalAnswerChunk);
  const latestOutcomeChunk = latestMatchingChunk(chunks, activeRunId, isOutcomeChunk);
  const latestOutcomeItems = latestOutcomeChunk ? markdownListItems(latestOutcomeChunk.body) : [];
  const latestAnswerEvent = answerEventFromAgentEvents(activeRunEvents);
  const latestAnswerEventBody = latestAnswerEvent ? humanEventSummary(latestAnswerEvent) : "";
  const latestAgentMessageEvent = latestAgentMessageEventFromAgentEvents(activeRunEvents);
  const latestAgentMessageBody = latestAgentMessageEvent
    ? eventPayloadText(latestAgentMessageEvent, "text") || humanEventSummary(latestAgentMessageEvent)
    : "";
  const latestTerminalTaskWithProgress =
    [...activeRunTasks].reverse().find((task) => Boolean(humanTerminalTaskProgress(task))) ?? null;
  const latestTerminalTaskRaw = latestTerminalTaskWithProgress
    ? rawTerminalTaskProgress(latestTerminalTaskWithProgress)
    : "";
  const latestTerminalTaskBody = latestTerminalTaskWithProgress
    ? humanTerminalTaskProgress(latestTerminalTaskWithProgress)
    : "";
  const latestCompletedEvent = [...activeRunEvents].reverse().find((event) => event.type === "completed");
  const runCompleted = Boolean(
    latestCompletedEvent || activeRunTasks.some((task) => task.status === "completed"),
  );
  const latestWorkingChunk = [...chunks]
    .reverse()
    .find((chunk) => chunk.status === "running" && (!activeRunId || !chunk.runId || chunk.runId === activeRunId));
  const nodes: BlueprintNode[] = [];
  const latestSource = latestEventSource(activeRunEvents);
  const firstTask = activeRunTasks[0] ?? null;
  const attentionTask = activeRunTasks.find(taskNeedsAttention) ?? null;
  const attentionRunId = attentionTask ? taskRunId(attentionTask) : "";
  const attentionDetail = attentionTask
    ? taskAttentionDetail(attentionTask, activeRunEvents)
    : "";
  const hasRunEvidence = activeRunTasks.length > 0 || activeRunEvents.length > 0 || hasActiveRun;
  const taskRequestSources = activeRunTasks.length > 0 ? activeRunTasks : appendingActiveFollowUp ? [] : tasks;
  const taskRequestLabel = taskRequestSources
    .map(taskMessageLabel)
    .find((label) => label && !isGenericAgentStatusText(label) && !taskMessageLooksOperational(label));
  const fallbackThreadTitle =
    hasRunEvidence && !threadTitleLooksPlaceholder(activeThreadTitle) ? activeThreadTitle.trim() : "";
  const requestBody = primaryUserChunk?.body || taskRequestLabel || fallbackThreadTitle;
  const validationSaysNoMutation = activeRunEvents.some((event) => {
    if (eventSource(event) !== "live.validation.completed") return false;
    const comparison = textValue(eventPayload(event).comparison_note).toLowerCase();
    return comparison.includes("forbade workspace mutation");
  });
  const mutationRequested = textRequestsWorkspaceMutation(requestBody);
  const assessmentOnlyRun = textRequestsAssessmentOnly(requestBody);
  const projectAnswerRun = assessmentOnlyRun && textRequestsProjectAnswer(requestBody);
  const mutationForbiddenByRequest = textForbidsWorkspaceMutation(requestBody);
  const readOnlyRun = assessmentOnlyRun || mutationForbiddenByRequest || validationSaysNoMutation;
  const defaultReadOnlyProjection = Boolean(primaryUserChunk?.body) && !mutationRequested && !readOnlyRun;
  const noShellRun = textForbidsShellCommands(requestBody);
  const directAnswerRequired = readOnlyRun || assessmentOnlyRun;
  const latestAnswerEventRaw = latestAnswerEvent ? eventSummary(latestAnswerEvent).trim() : "";
  const latestAnswerEventSourceBody =
    latestAnswerEventRaw && formatStructuredAgentDisplay(latestAnswerEventRaw)
      ? latestAnswerEventRaw
      : latestAnswerEventBody;
  const finalAnswerSource = [
    latestAnswerChunk?.body || "",
    latestAnswerEventSourceBody,
    latestAgentMessageBody,
    latestTerminalTaskRaw,
    latestTerminalTaskBody,
  ].find((source) =>
    isUsableFinalResponseSource(source, { requireDirectAnswer: directAnswerRequired }),
  ) || "";
  const finalAnswerReady = Boolean(finalAnswerSource);
  const shouldSettlePlanGraph = finalAnswerReady && !attentionTask;
  const requestUnderstanding = latestRequestUnderstanding(activeRunEvents, activeRunTasks);
  const operatorContexts = operatorContextRecords(activeRunEvents, activeRunTasks);
  const requestPlanContext = readOnlyRun ? null : deriveRequestPlanContext(requestBody);
  const rawPlanContext = emittedPlanContext ?? requestPlanContext;
  const emittedGraphHistory = extractBlueprintPlanContextHistory(
    activeRunEvents,
    activeRunTasks,
  ).filter(isSemanticPlanContext);
  const rawGraphHistory = graphHistoryWithLatest(
    emittedGraphHistory.length > 0 ? emittedGraphHistory : requestPlanContext ? [requestPlanContext] : [],
    emittedPlanContext,
  );
  const planContext = shouldSettlePlanGraph
    ? settlePlanContextAfterFinalResponse(rawPlanContext)
    : rawPlanContext;
  const displayReadOnlyRun = readOnlyRun || (defaultReadOnlyProjection && !rawPlanContext);
  const graphHistory = shouldSettlePlanGraph
    ? settleLatestPlanContextHistoryAfterFinalResponse(rawGraphHistory)
    : rawGraphHistory;
  const completedTaskIds = completedPlanTaskIdsFromEvents(activeRunEvents, planContext);
  const activeTaskIds = activePlanTaskIdsFromEvents(activeRunEvents, planContext, hasActiveRun);
  const readyTaskIds = new Set(planContext?.readyTaskIds ?? []);
  const deferredTaskIds = new Set(planContext?.deferredTaskIds ?? []);
  const hasPlannedTaskGraph = Boolean(planContext?.taskGraph.length);
  const planningStarted = hasEventSource(activeRunEvents, (source) => source.startsWith("live.planning"));
  const executionPhaseStarted = hasEventSource(activeRunEvents, eventStartsExecutionPhase);
  const executionPhaseCompleted = hasEventSource(activeRunEvents, eventCompletesExecutionPhase);
  const toolWorkStarted = hasEventSource(activeRunEvents, eventStartsToolWork);
  const buildStarted = executionPhaseStarted || toolWorkStarted;
  const validationStarted = hasEventSource(activeRunEvents, (source) => source.startsWith("live.validation"));
  const validationCompleted = hasEventSource(activeRunEvents, "live.validation.completed");
  const buildCompleted =
    executionPhaseCompleted ||
    validationStarted ||
    validationCompleted ||
    Boolean((latestAnswerChunk || latestAnswerEvent || latestOutcomeChunk || runCompleted) && !hasActiveRun);
  const latestValidation = [...activeRunEvents]
    .reverse()
    .find((event) => eventSource(event).startsWith("live.validation"));
  const validationFailed = Boolean(
    latestValidation &&
      (latestValidation.type === "failed" ||
        latestValidation.type === "blocked" ||
        eventPayload(latestValidation).passed === false),
  );
  const repairStarted = hasEventSource(activeRunEvents, (source) =>
    source.includes("repair") || source.includes("retry"),
  );
  const latestNonUnderstandingCompletedEvent =
    latestCompletedEvent && !eventSource(latestCompletedEvent).startsWith("live.request_understanding")
      ? latestCompletedEvent
      : null;
  const hasAnswerOrTerminalEvidence = Boolean(
    latestAnswerChunk ||
      latestOutcomeChunk ||
      latestAnswerEvent ||
      latestTerminalTaskWithProgress ||
      activeRunTasks.some((task) => task.status === "completed") ||
      latestNonUnderstandingCompletedEvent,
  );
  const showActiveRunSkeleton = Boolean(
    requestBody &&
      hasActiveRun &&
      !displayReadOnlyRun &&
      !hasPlannedTaskGraph &&
      !buildStarted &&
      !executionPhaseCompleted &&
      !validationStarted &&
      !validationCompleted &&
      !repairStarted &&
      !attentionTask &&
      !hasAnswerOrTerminalEvidence,
  );
  const laterThanPlanning = Boolean(
    buildStarted ||
      executionPhaseCompleted ||
      validationStarted ||
      validationCompleted ||
      latestAnswerChunk ||
      latestOutcomeChunk ||
      (!hasActiveRun && runCompleted),
  );
  const planningCompletedExplicit = Boolean(
    planContext?.taskGraph.length ||
      hasEventSource(activeRunEvents, "live.plan_validation.completed") ||
      hasEventSource(activeRunEvents, "live.planning.completed"),
  );
  const planningCompleted = planningCompletedExplicit || laterThanPlanning;
  const laterThanUnderstanding = planningStarted || planningCompleted || laterThanPlanning;
  const directResponseDetail = assessmentOnlyRun
    ? projectAnswerRun
      ? "Project answer; no file edits expected"
      : "Review response; no file edits expected"
    : noShellRun
    ? "No file edits or shell commands allowed"
    : mutationForbiddenByRequest
    ? "No file edits allowed"
    : displayReadOnlyRun
    ? "Read-only response; no file edits expected"
    : "No file edits allowed";
  const requestTargetCount = requestBody ? requestTargetPaths(requestBody).length : 0;
  const requestNodeDetail = requestTargetCount
    ? `${requestTargetCount} explicit target${requestTargetCount === 1 ? "" : "s"} captured`
    : displayReadOnlyRun || noShellRun
      ? "Request constraints captured"
      : "Request captured";

  if (primaryUserChunk || requestBody) {
    nodes.push({
      id: primaryUserChunk?.id ? `blueprint:${primaryUserChunk.id}` : "blueprint:request",
      title: "Operator request",
      detail: requestNodeDetail,
      meta: "input",
      body: requestBody ? "Ready for request understanding." : "Waiting for a request.",
      previewBody: requestPreviewBody({
        requestBody,
        understanding: requestUnderstanding,
        operatorContexts,
        readOnlyRun: displayReadOnlyRun,
        assessmentOnlyRun,
        noShellRun,
      }),
      rawRequest: requestBody,
      status: "done",
      kind: "request",
      sourceChunkId: primaryUserChunk?.id,
      taskId: primaryUserChunk?.taskId ?? firstTask?.task_id,
      runId: primaryUserChunk?.runId ?? (firstTask ? taskRunId(firstTask) : null),
    });
  }

  if (requestBody && (hasRunEvidence || requestUnderstanding || operatorContexts.length > 0)) {
    const understandingDone = Boolean(requestUnderstanding);
    const understandingModelAuthored = scalarDetailText(requestUnderstanding?.source) === "model_authored";
    const understandingRuleBrief =
      scalarDetailText(requestUnderstanding?.source) === "rule_generation_brief";
    const understandingSettled = understandingModelAuthored || laterThanUnderstanding;
    nodes.push({
      id: "blueprint:understanding",
      title: "Understand request",
      detail: understandingSettled && !understandingModelAuthored
        ? "Request context handed into later work"
        : understandingDone
        ? understandingModelAuthored
          ? "Scope, constraints, targets, and acceptance gates tailored"
          : understandingRuleBrief
            ? "Rule-generation brief sent to DAN"
            : "Scope, constraints, targets, and acceptance gates scaffolded"
        : "Extracting scope, constraints, targets, and evidence gates",
      meta: scalarDetailText(requestUnderstanding?.request_kind) || "request contract",
      body: understandingSettled && !understandingModelAuthored
        ? "DAN moved from request understanding into planning or execution."
        : understandingDone
        ? understandingModelAuthored
          ? "DAN tailored the request-understanding rules for this run."
          : understandingRuleBrief
            ? "DAN is asking the model to generate request-specific rules for this run."
            : "DAN started from a request-understanding scaffold for this run."
        : "DAN is identifying the work contract before treating execution as complete.",
      previewBody: understandingPreviewBody({
        requestBody,
        understanding: requestUnderstanding,
        operatorContexts,
      }),
      status: understandingSettled ? "done" : hasActiveRun ? "active" : "ready",
      kind: "understanding",
      runId: activeRunId || (firstTask ? taskRunId(firstTask) : null),
      taskId: activeRunningTask?.task_id ?? firstTask?.task_id,
    });
  }

  const planningFallbackActive =
    hasActiveRun && laterThanUnderstanding && !planningStarted && !planningCompleted && !planContext;
  if (planningStarted || planningCompletedExplicit || planContext) {
    const familyPresentation = taskFamilyPresentation(planContext?.taskFamily ?? "general");
    const graphVersionLabel =
      planContext?.graphVersionId ||
      (planContext?.graphRevision !== null && planContext?.graphRevision !== undefined
        ? `r${planContext.graphRevision}`
        : "");
    nodes.push({
      id: "blueprint:planning",
      title: planContext?.canonicalBlueprint
        ? `${familyPresentation.label} blueprint`
        : "Blueprint planning",
      detail:
        planContext?.taskGraph.length
          ? `${graphVersionLabel ? `${graphVersionLabel} · ` : ""}${planContext.taskGraph.length} projected tasks · ${planContext.readyTaskIds.length || 0} ready now`
          : planContext?.planFiles.length
            ? `${planContext.planFiles.length} plan files emitted`
            : planningCompleted && !planningCompletedExplicit
              ? "No task graph emitted; later work continued"
            : "Predicting the task graph and ready frontier",
      meta:
        planContext?.canonicalBlueprint
          ? familyPresentation.lens
          : planContext && planContext.graphRevision !== null && planContext.graphSource
          ? planContext.graphSource
          : planContext?.planRootRelative || "plan frontier",
      body: [
        graphVersionLabel
          ? `Graph: ${graphVersionLabel}${planContext?.graphUpdateScope ? ` · ${planContext.graphUpdateScope}` : ""}`
          : "",
        planContext?.graphRootVersionId ? `Root: ${planContext.graphRootVersionId}` : "",
        planContext?.graphParentVersionIds.length
          ? `Parents: ${planContext.graphParentVersionIds.join(", ")}`
          : "",
        planContext?.graphUpdateReason || "",
        planContext?.planRootRelative ? `Plan root: \`${planContext.planRootRelative}\`` : "",
        planContext?.planFiles.length
          ? ["Plan files:", ...planContext.planFiles.map((path) => `- \`${path}\``)].join("\n")
          : "",
        planContext?.readyTaskIds.length
          ? `Ready now: ${planContext.readyTaskIds.map((id) => `\`${id}\``).join(", ")}`
          : "",
        planContext?.deferredTaskIds.length
          ? `Future/deferred: ${planContext.deferredTaskIds.map((id) => `\`${id}\``).join(", ")}`
          : "",
        planContext?.parallelGroups.length
          ? [
              "Parallel groups:",
              ...parallelGroupSummaryItems(planContext, { maxTasksPerGroup: 2 }).map((item) => `- ${item}`),
            ].join("\n")
          : "",
      ]
        .filter(Boolean)
        .join("\n\n") ||
        (planningCompleted && !planningCompletedExplicit
          ? "No separate task graph was emitted; the run continued through later work using broad phases."
          : planningFallbackActive
          ? "No separate task graph has been emitted yet; following the current run phases."
          : "Waiting for the planner to emit a task graph."),
      previewBody: planPreviewBody(planContext),
      status: phaseStatus({
        started: planningStarted || planningFallbackActive,
        completed: planningCompleted,
        active: hasActiveRun,
        planned: hasActiveRun,
      }),
      kind: "plan",
      compact: !planningCompleted,
      graphContext: planContext ?? undefined,
      graphHistory,
      runId: activeRunId,
      taskId: activeRunningTask?.task_id,
    });
  }

  if (planContext?.taskGraph.length) {
    for (const task of visiblePlanGraphTasks(planContext)) {
      const taskState = (task.state || task.status || "").toLowerCase();
      const done =
        completedTaskIds.has(task.taskId) ||
        ["done", "complete", "completed", "x"].includes(taskState);
      const active =
        (activeTaskIds.has(task.taskId) ||
          ["active", "generating", "planning", "executing", "running", "validating", "repairing"].includes(taskState)) &&
        !done;
      const ready = (readyTaskIds.has(task.taskId) || taskState === "ready") && !done && !active;
      const future =
        deferredTaskIds.has(task.taskId) ||
        ["deferred", "future", "planned", "projected", "generated", "blueprint", "waiting"].includes(taskState) ||
        (!done && !active && !ready);
      const kind: BlueprintNodeKind = graphTaskKind(task, planContext);
      const meta = uniqueStringList([
        kind === "plan" ? "plan" : "",
        planContext.canonicalBlueprint ? kind.replace(/_/g, " ") : "",
        task.topologyRole ? task.topologyRole.replace(/_/g, " ") : "",
        kind === "worktree" ? "parallel lane" : "",
        task.branchId ? `branch ${task.branchId}` : "",
        task.parallelSafe ? "parallel-safe" : "serial",
      ]).join(" · ");
      nodes.push({
        id: graphTaskNodeId(task),
        title: `${task.taskId}. ${task.goal}`,
        detail: compactTaskDetail(task),
        meta,
        body: taskBody(task, planContext),
        previewBody: taskBody(task, planContext),
        status: done ? "done" : active ? "active" : ready ? "ready" : future ? "future" : "queued",
        kind,
        compact: future,
        depth: task.parentId ? 1 : 0,
        dependencyIds: task.dependsOn,
        graphTaskId: task.taskId,
        parentGraphTaskId: task.parentId,
        branchId: task.branchId,
        graphContext: planContext,
        runId: activeRunId,
        taskId: activeRunningTask?.task_id,
      });
    }
  } else if (buildStarted || buildCompleted || attentionTask || showActiveRunSkeleton) {
    const buildHandoffBody =
      !displayReadOnlyRun && (validationStarted || validationCompleted)
        ? "Execution handed off to validation for the current frontier."
        : "";
    const buildDoneBody = !displayReadOnlyRun && buildCompleted && !hasActiveRun ? "Execution is complete." : "";
    const buildProgressBody =
      buildStarted && hasActiveRun && !buildHandoffBody
        ? latestWorkingChunk?.body || ""
        : "";
    const buildIntro =
      attentionDetail ||
      buildProgressBody ||
      buildHandoffBody ||
      buildDoneBody ||
      (displayReadOnlyRun ? directResponseDetail : "Execution details will appear as Super DAN emits events.");
    nodes.push({
      id: "blueprint:build",
      title: attentionTask
        ? "Execution needs attention"
        : planContext?.readyTaskIds.length
        ? `Execute ready frontier ${planContext.readyTaskIds.join(", ")}`
        : displayReadOnlyRun
          ? assessmentOnlyRun
            ? projectAnswerRun
              ? "Prepare answer"
              : "Prepare review response"
            : "Prepare direct response"
          : "Execute workspace change",
      detail:
        attentionDetail ||
        buildProgressBody ||
        buildHandoffBody ||
        buildDoneBody ||
        (displayReadOnlyRun ? directResponseDetail : "Use tools, edit files, and collect artifacts"),
      meta: attentionTask?.status || latestSource || "workspace lane",
      body: buildIntro,
      previewBody: executionPreviewBody({
        intro: buildIntro,
        events: activeRunEvents,
        tasks: activeRunTasks,
        activeTask: activeRunningTask || attentionTask,
      }),
      status: attentionTask
        ? "blocked"
        : phaseStatus({
            started: buildStarted,
            completed: buildCompleted,
            active: hasActiveRun,
            planned: true,
          }),
      kind: "build",
      sourceChunkId: buildProgressBody ? latestWorkingChunk?.id : undefined,
      runId: activeRunId || attentionRunId,
      taskId: activeRunningTask?.task_id || attentionTask?.task_id,
    });
  }

  if (validationStarted || validationCompleted || hasPlannedTaskGraph || showActiveRunSkeleton) {
    nodes.push({
      id: "blueprint:validation",
      title: "Validate current frontier",
      detail:
        latestValidation && humanEventSummary(latestValidation)
          ? humanEventSummary(latestValidation)
          : validationCompleted
            ? "Validation result emitted"
            : "Checks run after the current executable slice",
      meta: latestValidation ? eventSource(latestValidation) : "validation",
      body:
        latestValidation && humanEventSummary(latestValidation)
          ? humanEventSummary(latestValidation)
          : "Super DAN validates the current frontier before claiming the full future graph is done.",
      previewBody: validationPreviewBody({
        latestValidation,
        understanding: requestUnderstanding,
      }),
      status: phaseStatus({
        started: validationStarted,
        completed: validationCompleted && !validationFailed,
        failed: validationFailed,
        active: hasActiveRun,
        planned: true,
      }),
      kind: "validation",
      compact: !validationStarted,
      runId: activeRunId,
      taskId: activeRunningTask?.task_id,
    });
  }

  if (repairStarted) {
    nodes.push({
      id: "blueprint:repair",
      title: "Repair or retry",
      detail: "Validation requested a bounded correction",
      meta: latestSource,
      body: latestWorkingChunk?.body || "Super DAN is repairing a rejected or incomplete slice.",
      status: hasActiveRun ? "active" : "done",
      kind: "repair",
      sourceChunkId: latestWorkingChunk?.id,
      runId: activeRunId,
      taskId: activeRunningTask?.task_id,
    });
  }

  if (
    latestAnswerChunk ||
    latestOutcomeChunk ||
    latestAnswerEvent ||
    latestTerminalTaskWithProgress ||
    showActiveRunSkeleton ||
    (!hasActiveRun && nodes.length > 0 && hasRunEvidence)
  ) {
    const finalDone = Boolean(finalAnswerSource && !attentionTask);
    const finalMissing = Boolean(
      !finalDone && !hasActiveRun && !attentionTask && (runCompleted || latestOutcomeChunk),
    );
    const finalSourceBody =
      attentionTask
        ? attentionDetail || "Super DAN needs attention before it can answer."
        : finalAnswerSource ||
          (finalMissing
            ? missingFinalResponseMessage(latestOutcomeItems.length > 0, directAnswerRequired)
            : "This will become solid when Super DAN emits the final answer.");
    const finalDisplay = userFacingAgentDisplay(finalSourceBody);
    nodes.push({
      id: latestAnswerChunk?.id ? `blueprint:${latestAnswerChunk.id}` : "blueprint:answer",
      title: "Final response",
      detail:
        finalMissing
          ? "Final answer missing"
          : attentionTask
            ? "Stopped before final response"
            : latestAnswerChunk?.meta ||
            (latestAnswerEvent ? eventSource(latestAnswerEvent) : "") ||
            (runCompleted || latestOutcomeChunk ? "Run completed" : "") ||
            "Summarize what changed and what remains",
      meta:
        finalMissing
          ? "missing answer"
          : attentionTask?.status ||
            latestAnswerChunk?.meta ||
            (latestAnswerEvent ? eventSource(latestAnswerEvent) : "") ||
            "answer",
      body: finalDisplay.body,
      previewBody: detailMarkdown(finalDisplay.previewBody, [
        {
          title: "Workspace Changes",
          items: latestOutcomeItems,
        },
        {
          title: "Remaining Attention",
          items: [
            ...(finalMissing
              ? [
                  directAnswerRequired
                    ? "Ask DAN to answer in this session, or rerun the request without creating files."
                    : "Ask DAN to summarize this session or rerun the request.",
                ]
              : []),
            ...(attentionTask ? [attentionDetail || taskAttentionDetail(attentionTask)] : []),
          ],
        },
      ]),
      status: finalDone ? "done" : attentionTask || finalMissing ? "blocked" : hasActiveRun ? "future" : "queued",
      kind: "answer",
      compact: !finalDone && !finalMissing,
      sourceChunkId: latestAnswerChunk?.id || latestOutcomeChunk?.id,
      runId:
        latestAnswerChunk?.runId ||
        latestOutcomeChunk?.runId ||
        latestAnswerEvent?.run_id ||
        latestAgentMessageEvent?.run_id ||
        latestCompletedEvent?.run_id ||
        (latestTerminalTaskWithProgress ? taskRunId(latestTerminalTaskWithProgress) : "") ||
        activeRunId ||
        attentionRunId,
      taskId:
        latestAnswerChunk?.taskId ||
        latestOutcomeChunk?.taskId ||
        latestAnswerEvent?.task_id ||
        latestAgentMessageEvent?.task_id ||
        latestCompletedEvent?.task_id ||
        latestTerminalTaskWithProgress?.task_id ||
        activeRunningTask?.task_id ||
        attentionTask?.task_id,
    });
  }

  const activeFollowUpRow: QueueRow | null =
    appendingActiveFollowUp && liveActiveRunningTask
      ? (() => {
          const rawDetail = latestUserChunk?.body || taskMessageLabel(liveActiveRunningTask);
          return {
            id: `active-followup:${liveActiveRunningTask.task_id}:${liveActiveRunId}`,
            label: "Active follow-up",
            detail: queueDisplayDetail(rawDetail, "Active follow-up work."),
            rawDetail,
            status: liveActiveRunningTask.status || "running",
            active: true,
            kind: "followup",
            lane: "append",
            taskId: liveActiveRunningTask.task_id,
            runId: liveActiveRunId,
            sourceChunkId: latestUserChunk?.id,
          };
        })()
      : null;
  const visibleQueueRows = (
    activeFollowUpRow
      ? [
          activeFollowUpRow,
          ...queueRows.filter(
            (row) =>
              !(
                row.kind === "task" &&
                row.taskId === liveActiveRunningTask?.task_id &&
                row.runId === liveActiveRunId
              ),
          ),
        ]
      : queueRows
  ).filter(
    (row) =>
      !(
        row.kind === "task" &&
        activeRunId &&
        activeRunningTask &&
        row.taskId === activeRunningTask.task_id &&
        row.runId === activeRunId
      ),
  );

  visibleQueueRows.forEach((row, index) => {
    if (row.kind === "followup") {
      nodes.push(...followUpBlueprintNodes(row, index));
      return;
    }
    nodes.push({
      id: `blueprint:${row.id}`,
      title: row.label,
      detail: row.detail,
      meta: row.status,
      body: row.detail,
      status: queueBlueprintStatus(row.status),
      kind: "queue",
      compact: true,
      depth: 0,
      runId: row.runId,
      taskId: row.taskId,
      sourceChunkId: row.sourceChunkId,
    });
  });

  return nodes;
}

function taskRunCreatedAt(task: ChatV2TaskSnapshot) {
  return (
    taskRunStartedAt(task) ||
    taskRunUpdatedAt(task)
  );
}

function runSortTimeForTask(task: ChatV2TaskSnapshot) {
  return timestampValue(taskRunCreatedAt(task), Number.NaN);
}

function runScopedNodeId(runId: string, nodeId: string) {
  const safeRunId = runId.replace(/[^a-z0-9_-]+/gi, "-") || "run";
  return nodeId.startsWith(`blueprint:run:${safeRunId}:`)
    ? nodeId
    : `blueprint:run:${safeRunId}:${nodeId.replace(/^blueprint:/, "")}`;
}

function namespaceBlueprintRunNodes(nodes: BlueprintNode[], runId: string, shouldNamespace: boolean) {
  if (!shouldNamespace || !runId) return nodes;
  return nodes.map((node) => ({
    ...node,
    id: runScopedNodeId(runId, node.id),
  }));
}

const PENDING_CHUNK_RUN_PREFIX = "pending-chunk:";

function pendingChunkRunId(chunk: WorkspaceChunk) {
  return `${PENDING_CHUNK_RUN_PREFIX}${chunk.id}`;
}

function chunkBelongsToTimelineRun(chunk: WorkspaceChunk, runId: string) {
  if (chunk.runId) return chunk.runId === runId;
  return chunk.kind === "chat" && chunk.role === "user" && pendingChunkRunId(chunk) === runId;
}

function blueprintRunTimelineIds(args: {
  tasks: ChatV2TaskSnapshot[];
  agentEvents: ChatV2AgentRunEvent[];
  chunks: WorkspaceChunk[];
  queueRows: QueueRow[];
  activeRunId: string;
}) {
  const realRunIds = new Set<string>();
  const collectRealRun = (runId: string | null | undefined) => {
    const id = textValue(runId);
    if (id) realRunIds.add(id);
  };
  args.chunks.forEach((chunk) => collectRealRun(chunk.runId));
  args.tasks.forEach((task) => collectRealRun(taskRunId(task)));
  args.agentEvents.forEach((event) => collectRealRun(event.run_id));
  args.queueRows.forEach((row) => collectRealRun(row.runId));
  collectRealRun(args.activeRunId);

  const runOrder = new Map<string, { firstSeen: number; sortTime: number; conversationIndex: number }>();
  const addRun = (
    runId: string | null | undefined,
    firstSeen: number,
    sortTime = Number.NaN,
    conversationIndex = Number.NaN,
  ) => {
    const id = textValue(runId);
    if (!id) return;
    const previous = runOrder.get(id);
    const nextSortTime = Number.isFinite(sortTime) ? sortTime : previous?.sortTime ?? Number.NaN;
    const nextConversationIndex = Number.isFinite(conversationIndex)
      ? conversationIndex
      : previous?.conversationIndex ?? Number.NaN;
    if (!previous) {
      runOrder.set(id, { firstSeen, sortTime: nextSortTime, conversationIndex: nextConversationIndex });
      return;
    }
    previous.firstSeen = Math.min(previous.firstSeen, firstSeen);
    if (Number.isFinite(nextSortTime)) previous.sortTime = nextSortTime;
    if (Number.isFinite(nextConversationIndex)) {
      previous.conversationIndex = Number.isFinite(previous.conversationIndex)
        ? Math.min(previous.conversationIndex, nextConversationIndex)
        : nextConversationIndex;
    }
  };
  const includePendingChunks = realRunIds.size > 1;
  const queuedMessageChunkIds = queueSourceChunkIds(args.queueRows);

  args.chunks.forEach((chunk, index) => {
    const isUserTurn = chunk.kind === "chat" && chunk.role === "user";
    const isQueuedUserTurn = isUserTurn && queuedMessageChunkIds.has(chunk.id);
    const runId =
      chunk.runId || (includePendingChunks && isUserTurn && !isQueuedUserTurn ? pendingChunkRunId(chunk) : "");
    addRun(runId, index, Number.NaN, isUserTurn ? index : Number.NaN);
  });
  args.tasks.forEach((task, index) => {
    addRun(taskRunId(task), 10_000 + index, runSortTimeForTask(task));
  });
  args.agentEvents.forEach((event, index) => {
    addRun(event.run_id, 20_000 + index);
  });
  args.queueRows.forEach((row, index) => {
    addRun(row.runId, 30_000 + index);
  });
  addRun(args.activeRunId, 40_000);

  return [...runOrder.entries()]
    .sort(([, a], [, b]) => {
      const aHasConversation = Number.isFinite(a.conversationIndex);
      const bHasConversation = Number.isFinite(b.conversationIndex);
      if (aHasConversation && bHasConversation && a.conversationIndex !== b.conversationIndex) {
        return a.conversationIndex - b.conversationIndex;
      }
      const aHasTime = Number.isFinite(a.sortTime);
      const bHasTime = Number.isFinite(b.sortTime);
      if (aHasTime && bHasTime && a.sortTime !== b.sortTime) return a.sortTime - b.sortTime;
      if (aHasConversation !== bHasConversation) return aHasConversation ? -1 : 1;
      if (aHasTime !== bHasTime) return aHasTime ? -1 : 1;
      return a.firstSeen - b.firstSeen;
    })
    .map(([runId]) => runId);
}

function buildBlueprintNodes(args: {
  tasks: ChatV2TaskSnapshot[];
  agentEvents: ChatV2AgentRunEvent[];
  chunks: WorkspaceChunk[];
  activeRunId: string;
  activeRunningTask: ChatV2TaskSnapshot | null;
  queueRows: QueueRow[];
  activeThreadTitle: string;
}) {
  const normalizedArgs = {
    ...args,
    queueRows: attachQueueRowSourceChunks(args.queueRows, args.chunks),
  };
  const runIds = blueprintRunTimelineIds(normalizedArgs);
  if (runIds.length <= 1) {
    const runId =
      runIds[0] ||
      normalizedArgs.activeRunId ||
      (normalizedArgs.tasks[0] ? taskRunId(normalizedArgs.tasks[0]) : "");
    return namespaceBlueprintRunNodes(
      buildBlueprintNodesForRunScope(normalizedArgs),
      runId,
      false,
    );
  }

  const shouldNamespace = true;
  const nodes = runIds.flatMap((runId) => {
    const scopedTasks = normalizedArgs.tasks.filter((task) => taskRunId(task) === runId);
    const scopedEvents = normalizedArgs.agentEvents.filter((event) => event.run_id === runId);
    const scopedChunks = normalizedArgs.chunks.filter((chunk) => chunkBelongsToTimelineRun(chunk, runId));
    const scopedQueueRows = normalizedArgs.queueRows.filter((row) => row.runId === runId);
    const scopedActiveTask =
      normalizedArgs.activeRunningTask && taskRunId(normalizedArgs.activeRunningTask) === runId
        ? normalizedArgs.activeRunningTask
        : null;
    const scopedActiveRunId = normalizedArgs.activeRunId === runId ? runId : "";
    if (
      scopedTasks.length === 0 &&
      scopedEvents.length === 0 &&
      scopedChunks.length === 0 &&
      scopedQueueRows.length === 0
    ) {
      return [];
    }
    return namespaceBlueprintRunNodes(
      buildBlueprintNodesForRunScope({
        ...normalizedArgs,
        tasks: scopedTasks,
        agentEvents: scopedEvents,
        chunks: scopedChunks,
        activeRunId: scopedActiveRunId,
        activeRunningTask: scopedActiveTask,
        queueRows: scopedQueueRows,
        activeThreadTitle: "",
      }),
      runId,
      shouldNamespace,
    );
  });

  return nodes.length > 0 ? nodes : buildBlueprintNodesForRunScope(normalizedArgs);
}

export function buildBlueprintNodesForTest(args: Parameters<typeof buildBlueprintNodes>[0]) {
  return buildBlueprintNodes(args);
}

function workspaceComposerPlaceholder(args: {
  hasActiveRun: boolean;
  placement: ActiveRunPlacement;
  workspaceMode?: WorkspacePane;
  selectedBlueprintTitle?: string | null;
  selectedChunkTitle?: string | null;
  activeFilePath?: string | null;
  activeNoteTitle?: string | null;
  activeNotePath?: string | null;
}) {
  if (args.hasActiveRun) {
    return args.placement === "queue"
      ? "Type the next message to run after the current one."
      : "Type to steer the active run. Leave empty to stop.";
  }
  if (args.workspaceMode === "notes") {
    if (args.activeNoteTitle) return `Ask Super DAN about ${args.activeNoteTitle}`;
    if (args.activeNotePath) return `Ask Super DAN about ${args.activeNotePath}`;
    return "Ask Super DAN about this Hugo note";
  }
  if (args.selectedBlueprintTitle) return `Ask Super DAN about ${args.selectedBlueprintTitle}`;
  if (args.selectedChunkTitle) return `Ask Super DAN about ${args.selectedChunkTitle}`;
  if (args.activeFilePath) return `Ask Super DAN about ${args.activeFilePath}`;
  return "Ask Super DAN to work in this workspace";
}

function workspaceComposerPrimaryActionLabel(args: {
  hasActiveRun: boolean;
  placement: ActiveRunPlacement;
  isStop: boolean;
}) {
  if (args.isStop) return "Stop";
  if (!args.hasActiveRun) return "Send";
  return args.placement === "queue" ? "Next" : "Steer";
}

function workspaceComposerPlacementLabel(mode: ActiveRunPlacement, hasActiveRun: boolean) {
  if (mode === "queue") return "Next";
  return hasActiveRun ? "Steer" : "New";
}

function workspaceComposerTokenAt(text: string, caret: number): WorkspaceComposerToken | null {
  const boundedCaret = Math.max(0, Math.min(caret, text.length));
  const before = text.slice(0, boundedCaret);
  const match = /(^|[\s([{"'])([/@$])([^\s]*)$/.exec(before);
  if (!match) return null;
  const trigger = match[2] as WorkspaceComposerTrigger;
  const query = match[3] ?? "";
  const start = boundedCaret - query.length - 1;
  if (trigger === "/" && before.slice(0, start).trim()) return null;
  if (trigger === "$") {
    if (query && !/^[A-Za-z][A-Za-z0-9_-]*$/.test(query)) return null;
    if (query && /^[A-Z_][A-Z0-9_]*$/.test(query)) return null;
  }
  return {
    trigger,
    query,
    start,
    end: boundedCaret,
    key: `${trigger}:${start}:${query}`,
  };
}

function workspaceCommandSuggestions(query: string): WorkspaceComposerSuggestion[] {
  const normalized = query.toLowerCase();
  const prefix = `/${normalized}`;
  return WORKSPACE_COMPOSER_COMMANDS.filter((item) =>
    normalized ? item.command.startsWith(prefix) : true,
  )
    .slice(0, 9)
    .map((item) => ({
      id: `command:${item.command}`,
      type: "command" as const,
      insertText: item.command,
      label: item.command,
      detail: item.description,
      meta: "command",
    }));
}

function workspaceSkillSuggestionRank(skill: WorkspaceSkillSuggestion, query: string, index: number) {
  if (!query) return index;
  const token = skill.token.toLowerCase();
  const name = skill.name.toLowerCase();
  const description = skill.description.toLowerCase();
  const haystack = `${token} ${name} ${description}`;
  if (token.startsWith(query)) return index;
  if (name.startsWith(query)) return 100 + index;
  if (token.includes(query)) return 200 + index;
  if (name.includes(query)) return 300 + index;
  if (haystack.includes(query)) return 400 + index;
  return Number.POSITIVE_INFINITY;
}

function workspaceSkillSuggestions(
  skills: WorkspaceSkillSuggestion[],
  query: string,
): WorkspaceComposerSuggestion[] {
  const normalized = query.toLowerCase();
  return skills
    .map((skill, index) => ({
      skill,
      rank: workspaceSkillSuggestionRank(skill, normalized, index),
    }))
    .filter((item) => Number.isFinite(item.rank))
    .sort((a, b) => a.rank - b.rank || a.skill.token.localeCompare(b.skill.token))
    .slice(0, 9)
    .map(({ skill }) => ({
      id: `skill:${skill.token}`,
      type: "skill" as const,
      insertText: `$${skill.token}`,
      label: `$${skill.token}`,
      detail: skill.description || skill.name,
      meta: skill.source_scope || "skill",
    }));
}

function workspaceFileSuggestionRank(entry: WorkspaceFileEntry, query: string, index: number) {
  if (!query) return entry.is_directory ? 1000 + index : index;
  const path = entry.relative_path.toLowerCase();
  const name = entry.name.toLowerCase();
  if (path.startsWith(query)) return entry.is_directory ? 100 + index : index;
  if (name.startsWith(query)) return entry.is_directory ? 300 + index : 200 + index;
  if (path.includes(query)) return entry.is_directory ? 500 + index : 400 + index;
  if (name.includes(query)) return entry.is_directory ? 700 + index : 600 + index;
  return Number.POSITIVE_INFINITY;
}

function workspaceFileSuggestions(
  entries: WorkspaceFileEntry[],
  query: string,
): WorkspaceComposerSuggestion[] {
  const normalized = query.toLowerCase();
  return entries
    .map((entry, index) => ({
      entry,
      rank: workspaceFileSuggestionRank(entry, normalized, index),
    }))
    .filter((item) => Number.isFinite(item.rank))
    .sort((a, b) => a.rank - b.rank || a.entry.relative_path.localeCompare(b.entry.relative_path))
    .slice(0, 9)
    .map(({ entry }) => ({
      id: `file:${entry.path}`,
      type: "file" as const,
      insertText: `@${entry.relative_path}`,
      label: `@${entry.relative_path}`,
      detail: entry.is_directory ? "folder" : compactFileSize(entry.size),
      meta: entry.is_directory ? "folder" : "file",
    }));
}

function workspaceComposerSuggestions(
  token: WorkspaceComposerToken | null,
  args: {
    skills: WorkspaceSkillSuggestion[];
    files: WorkspaceFileEntry[];
  },
): WorkspaceComposerSuggestion[] {
  if (!token) return [];
  if (token.trigger === "/") return workspaceCommandSuggestions(token.query);
  if (token.trigger === "$") return workspaceSkillSuggestions(args.skills, token.query);
  return workspaceFileSuggestions(args.files, token.query);
}

function workspaceSelectedSkillInvocation(
  text: string,
  skills: WorkspaceSkillSuggestion[],
): { selectedTokens: string[]; objective: string } {
  const known = new Set(skills.map((skill) => skill.token.toLowerCase()));
  let remaining = text.trim();
  const selectedTokens: string[] = [];
  while (remaining.startsWith("$")) {
    const match = /^\$([A-Za-z][A-Za-z0-9_-]*)(?:\s+|$)/.exec(remaining);
    if (!match) break;
    const token = match[1].toLowerCase();
    if (!known.has(token)) break;
    if (!selectedTokens.includes(token)) selectedTokens.push(token);
    remaining = remaining.slice(match[0].length).trim();
  }
  return { selectedTokens, objective: remaining };
}

function workspaceMentionedFilesFromText(
  text: string,
  entries: WorkspaceFileEntry[],
): WorkspaceFileEntry[] {
  const selected: WorkspaceFileEntry[] = [];
  const seen = new Set<string>();
  const ordered = [...entries].sort((a, b) => b.relative_path.length - a.relative_path.length);
  for (const entry of ordered) {
    const token = `@${entry.relative_path}`;
    if (!text.includes(token) || seen.has(entry.path)) continue;
    selected.push(entry);
    seen.add(entry.path);
    if (selected.length >= 6) break;
  }
  return selected;
}

const WORKSPACE_PREVIEW_IMAGE_EXTENSIONS = new Set([
  "avif",
  "gif",
  "jpeg",
  "jpg",
  "png",
  "svg",
  "webp",
]);
const WORKSPACE_PREVIEW_MARKDOWN_EXTENSIONS = new Set(["markdown", "md", "mdx"]);
const WORKSPACE_PREVIEW_TEXT_EXTENSIONS = new Set(["csv", "json", "jsonl", "log", "txt"]);
const WORKSPACE_PREVIEW_OUTPUT_SEGMENTS = new Set([
  "artifact",
  "artifacts",
  "export",
  "exports",
  "figure",
  "figures",
  "output",
  "outputs",
  "plot",
  "plots",
  "preview",
  "previews",
  "report",
  "reports",
  "site",
  "website",
]);

function workspaceFileExtension(entry: WorkspaceFileEntry) {
  const name = entry.name || fileName(entry.relative_path || entry.path);
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index + 1).toLowerCase() : "";
}

function workspaceFilePreviewKind(entry: WorkspaceFileEntry): WorkspaceFilePreviewKind | null {
  if (entry.is_directory) return null;
  const extension = workspaceFileExtension(entry);
  if (WORKSPACE_PREVIEW_IMAGE_EXTENSIONS.has(extension)) return "image";
  if (extension === "pdf") return "pdf";
  if (extension === "html" || extension === "htm") return "html";
  if (WORKSPACE_PREVIEW_MARKDOWN_EXTENSIONS.has(extension)) return "markdown";
  if (WORKSPACE_PREVIEW_TEXT_EXTENSIONS.has(extension)) return "text";
  return null;
}

function workspaceFilePreviewNeedsText(entry: WorkspaceFileEntry | null) {
  if (!entry) return false;
  const kind = workspaceFilePreviewKind(entry);
  return kind === "markdown" || kind === "text";
}

function workspacePreviewKindLabel(kind: WorkspaceFilePreviewKind) {
  if (kind === "image") return "image";
  if (kind === "pdf") return "PDF";
  if (kind === "html") return "web";
  if (kind === "markdown") return "Markdown";
  return "text";
}

function absoluteWorkspacePreviewUrl(
  entry: WorkspaceFileEntry,
  root: string,
  baseHref = typeof window !== "undefined" ? window.location.href : "http://127.0.0.1/",
) {
  const url = workspaceFilePreviewUrl(entry.path, root || undefined, entry.relative_path);
  try {
    return new URL(url, baseHref).toString();
  } catch {
    return url;
  }
}

function workspacePreviewPathSegments(entry: WorkspaceFileEntry) {
  return (entry.relative_path || entry.path).replace(/\\/g, "/").split("/").filter(Boolean);
}

function workspacePreviewInOutputArea(entry: WorkspaceFileEntry) {
  return workspacePreviewPathSegments(entry)
    .slice(0, -1)
    .some((segment) => WORKSPACE_PREVIEW_OUTPUT_SEGMENTS.has(segment.toLowerCase()));
}

function workspacePreviewMarkdownLooksOutput(entry: WorkspaceFileEntry) {
  if (workspaceFilePreviewKind(entry) !== "markdown") return false;
  const name = (entry.name || fileName(entry.relative_path)).toLowerCase();
  return (
    workspacePreviewInOutputArea(entry) ||
    /(?:report|summary|preview|artifact|output|result|readme)\.(?:md|mdx|markdown)$/.test(name)
  );
}

function workspacePreviewCandidate(entry: WorkspaceFileEntry, source: WorkspacePreviewArtifactSource) {
  const kind = workspaceFilePreviewKind(entry);
  if (!kind) return null;
  if (source === "artifact" || source === "session") return kind;
  if (kind === "markdown" && !workspacePreviewMarkdownLooksOutput(entry)) return null;
  if (kind === "text" && !workspacePreviewInOutputArea(entry)) return null;
  return kind;
}

function normalizeWorkspacePreviewPath(path: string) {
  return path.trim().replace(/\\/g, "/").replace(/\/{2,}/g, "/");
}

function isAbsoluteWorkspacePreviewPath(path: string) {
  return path.startsWith("/") || /^[A-Za-z]:\//.test(path);
}

function workspacePreviewPathInsideRoot(path: string, root: string) {
  const normalizedRoot = normalizeWorkspacePreviewPath(root).replace(/\/+$/, "");
  const normalizedPath = normalizeWorkspacePreviewPath(path);
  return Boolean(
    normalizedRoot &&
      (normalizedPath === normalizedRoot || normalizedPath.startsWith(`${normalizedRoot}/`)),
  );
}

function workspacePreviewRelativePath(path: string, root: string) {
  const normalizedPath = normalizeWorkspacePreviewPath(path);
  const normalizedRoot = normalizeWorkspacePreviewPath(root).replace(/\/+$/, "");
  if (normalizedRoot && workspacePreviewPathInsideRoot(normalizedPath, normalizedRoot)) {
    return normalizedPath.slice(normalizedRoot.length).replace(/^\/+/, "");
  }
  return normalizedPath.replace(/^\.\//, "").replace(/^\/+/, "");
}

function workspacePreviewEntryFromPath(
  rawPath: string,
  root: string,
  index: number,
): WorkspaceFileEntry | null {
  const trimmed = rawPath.trim();
  if (!trimmed || /^https?:\/\//i.test(trimmed)) return null;
  const normalizedPath = normalizeWorkspacePreviewPath(trimmed);
  const isAbsolute = isAbsoluteWorkspacePreviewPath(normalizedPath);
  if (isAbsolute && root && !workspacePreviewPathInsideRoot(normalizedPath, root)) return null;
  const relativePath = workspacePreviewRelativePath(normalizedPath, root);
  if (!relativePath || relativePath === "." || relativePath.split("/").includes("..")) return null;
  const parent = relativePath.includes("/")
    ? relativePath.slice(0, relativePath.lastIndexOf("/"))
    : "";
  const path = isAbsolute
    ? normalizedPath
    : root
      ? `${normalizeWorkspacePreviewPath(root).replace(/\/+$/, "")}/${relativePath}`
      : relativePath;
  return {
    path,
    relative_path: relativePath,
    name: fileName(relativePath),
    parent,
    is_directory: false,
    size: 0,
    mtime: index,
    depth: Math.max(0, relativePath.split("/").length - 1),
  };
}

function workspacePreviewArtifactPathsFromRunData(
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
) {
  const paths: string[] = [];
  for (const task of tasks) {
    for (const ref of task.latest_artifact_refs ?? []) {
      const path = textValue(ref.path) || textValue(ref.uri) || textValue(ref.url);
      if (path) paths.push(path);
    }
  }
  for (const event of events) {
    for (const ref of event.artifact_refs ?? []) {
      const path = textValue(ref.path) || textValue(ref.uri) || textValue(ref.url);
      if (path) paths.push(path);
    }
  }
  return paths;
}

function addWorkspacePreviewPathKey(keys: Set<string>, rawPath: string, root: string) {
  const trimmed = rawPath.trim();
  if (!trimmed || /^https?:\/\//i.test(trimmed)) return;
  const normalizedPath = normalizeWorkspacePreviewPath(trimmed);
  if (!normalizedPath) return;
  const relativePath = workspacePreviewRelativePath(normalizedPath, root);
  for (const value of [normalizedPath, relativePath]) {
    const key = value.trim().toLowerCase();
    if (key && key !== "." && !key.split("/").includes("..")) keys.add(key);
  }
}

function workspacePreviewEntryMatchesPathKeys(
  entry: WorkspaceFileEntry,
  pathKeys: Set<string>,
  root: string,
) {
  const entryKeys = new Set<string>();
  addWorkspacePreviewPathKey(entryKeys, entry.path, root);
  addWorkspacePreviewPathKey(entryKeys, entry.relative_path, root);
  return [...entryKeys].some((key) => pathKeys.has(key));
}

function workspacePreviewPathsFromValue(value: unknown): string[] {
  if (Array.isArray(value)) return uniqueStringList(value.flatMap(workspacePreviewPathsFromValue));
  const record = recordValue(value);
  if (record) {
    const direct =
      textValue(record.path) ||
      textValue(record.file_path) ||
      textValue(record.relative_path) ||
      textValue(record.relativePath) ||
      textValue(record.uri) ||
      textValue(record.url);
    return direct ? [direct] : [];
  }
  return stringList(value);
}

function workspacePreviewSessionPathsFromTask(task: ChatV2TaskSnapshot) {
  const metadata = task.metadata ?? {};
  const surfaceContext = metadataObject(metadata.surface_context);
  const commandPayload = metadataObject(metadata.command_payload);
  const commandSurfaceContext = metadataObject(commandPayload.surface_context);
  const operatorContext = metadataObject(metadata.operator_context);
  const lastSurfaceTurn = metadataObject(metadata.last_surface_turn);
  return uniqueStringList([
    ...workspacePreviewPathsFromValue(surfaceContext.mentioned_files),
    ...workspacePreviewPathsFromValue(surfaceContext.active_file),
    ...workspacePreviewPathsFromValue(surfaceContext.selected_chunk),
    ...workspacePreviewPathsFromValue(commandSurfaceContext.mentioned_files),
    ...workspacePreviewPathsFromValue(commandSurfaceContext.active_file),
    ...workspacePreviewPathsFromValue(commandSurfaceContext.selected_chunk),
    ...workspacePreviewPathsFromValue(operatorContext.target_paths),
    ...workspacePreviewPathsFromValue(operatorContext.attachments),
    ...workspacePreviewPathsFromValue(metadata.attachments),
    ...workspacePreviewPathsFromValue(lastSurfaceTurn.attachments),
  ]);
}

function workspacePreviewSessionPathsFromEvents(events: ChatV2AgentRunEvent[]) {
  const paths: string[] = [];
  for (const event of events) {
    const payload = eventPayload(event);
    const result = recordValue(payload.result);
    if (eventSource(event) === "tool.completed" && Boolean(result?.changed)) {
      const path = pathFromEvent(event);
      if (path) paths.push(path);
    }
    if (result) {
      paths.push(
        ...workspacePreviewPathsFromValue(result.changed_files),
        ...workspacePreviewPathsFromValue(result.changed_paths),
        ...workspacePreviewPathsFromValue(result.files_changed),
        ...workspacePreviewPathsFromValue(result.modified_files),
        ...workspacePreviewPathsFromValue(result.created_files),
        ...workspacePreviewPathsFromValue(result.files_created),
        ...workspacePreviewPathsFromValue(result.artifacts),
        ...workspacePreviewPathsFromValue(result.artifact_refs),
      );
    }
    paths.push(
      ...workspacePreviewPathsFromValue(payload.changed_files),
      ...workspacePreviewPathsFromValue(payload.changed_paths),
      ...workspacePreviewPathsFromValue(payload.files_changed),
      ...workspacePreviewPathsFromValue(payload.modified_files),
      ...workspacePreviewPathsFromValue(payload.created_files),
      ...workspacePreviewPathsFromValue(payload.files_created),
    );
  }
  return uniqueStringList(paths);
}

function escapedRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function workspacePreviewTextMentionsPath(text: string, relativePath: string, uniqueName = false) {
  if (!text || !relativePath) return false;
  const normalizedText = text.replace(/\\/g, "/");
  const path = relativePath.replace(/\\/g, "/");
  if (normalizedText.includes(`@${path}`)) return true;
  const escapedPath = escapedRegExp(path);
  if (new RegExp(`(?:^|[\\s("'\\[])\`?${escapedPath}\`?(?=$|[\\s).,;:'"\\]])`).test(normalizedText)) {
    return true;
  }
  if (!uniqueName) return false;
  const name = fileName(path);
  if (!name || name === path) return false;
  const escapedName = escapedRegExp(name);
  return new RegExp(`(?:^|[\\s("'\\[])\`?@?${escapedName}\`?(?=$|[\\s).,;:'"\\]])`).test(
    normalizedText,
  );
}

function workspacePreviewMentionedEntryPathsFromTexts(
  entries: WorkspaceFileEntry[],
  texts: string[],
) {
  if (texts.length === 0 || entries.length === 0) return [];
  const previewableEntries = entries.filter((entry) => workspaceFilePreviewKind(entry));
  const nameCounts = new Map<string, number>();
  previewableEntries.forEach((entry) => {
    const name = (entry.name || fileName(entry.relative_path)).toLowerCase();
    if (name) nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1);
  });
  const selected: string[] = [];
  const seen = new Set<string>();
  for (const entry of [...previewableEntries].sort((a, b) => b.relative_path.length - a.relative_path.length)) {
    const path = entry.relative_path || entry.path;
    const name = (entry.name || fileName(path)).toLowerCase();
    const uniqueName = Boolean(name && nameCounts.get(name) === 1);
    if (!texts.some((text) => workspacePreviewTextMentionsPath(text, path, uniqueName))) continue;
    if (seen.has(path)) continue;
    seen.add(path);
    selected.push(path);
    if (selected.length >= 12) break;
  }
  return selected;
}

function workspacePreviewMentionedEntryPathsFromChunks(
  entries: WorkspaceFileEntry[],
  chunks: WorkspaceChunk[],
) {
  return workspacePreviewMentionedEntryPathsFromTexts(
    entries,
    chunks.map((chunk) => [chunk.body, chunk.filePath ?? ""].filter(Boolean).join("\n")),
  );
}

function workspacePreviewArtifactRank(artifact: WorkspacePreviewArtifact, index: number) {
  const { entry, kind, source } = artifact;
  const outputArea = workspacePreviewInOutputArea(entry);
  const name = (entry.name || "").toLowerCase();
  const kindRank =
    kind === "html" ? 0 : kind === "pdf" ? 20 : kind === "image" ? 30 : kind === "markdown" ? 60 : 90;
  const sourceRank = source === "artifact" ? -500 : source === "session" ? -260 : 0;
  const outputRank = outputArea ? -220 : 0;
  const nameRank = /^(index|preview|report|summary|dashboard|readme)\./.test(name) ? -40 : 0;
  return sourceRank + outputRank + kindRank + nameRank + entry.depth * 8 + index / 1000;
}

function workspacePreviewArtifacts(args: {
  entries: WorkspaceFileEntry[];
  root: string;
  tasks: ChatV2TaskSnapshot[];
  events: ChatV2AgentRunEvent[];
  chunks?: WorkspaceChunk[];
  texts?: string[];
  limit?: number;
}): WorkspacePreviewArtifact[] {
  const seen = new Set<string>();
  const candidates: WorkspacePreviewArtifact[] = [];
  const addEntry = (entry: WorkspaceFileEntry, source: WorkspacePreviewArtifactSource) => {
    const kind = workspacePreviewCandidate(entry, source);
    if (!kind) return;
    const key = normalizeWorkspacePreviewPath(entry.relative_path || entry.path).toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    candidates.push({
      id: `${source}:${key}`,
      entry,
      kind,
      source,
    });
  };

  workspacePreviewArtifactPathsFromRunData(args.tasks, args.events).forEach((path, index) => {
    const entry = workspacePreviewEntryFromPath(path, args.root, index);
    if (entry) addEntry(entry, "artifact");
  });
  const sessionPaths = uniqueStringList([
    ...args.tasks.flatMap(workspacePreviewSessionPathsFromTask),
    ...workspacePreviewSessionPathsFromEvents(args.events),
    ...workspacePreviewMentionedEntryPathsFromChunks(args.entries, args.chunks ?? []),
    ...workspacePreviewMentionedEntryPathsFromTexts(args.entries, args.texts ?? []),
  ]);
  const sessionPathKeys = new Set<string>();
  sessionPaths.forEach((path) => addWorkspacePreviewPathKey(sessionPathKeys, path, args.root));
  args.entries
    .filter((entry) => workspacePreviewEntryMatchesPathKeys(entry, sessionPathKeys, args.root))
    .forEach((entry) => addEntry(entry, "session"));
  sessionPaths.forEach((path, index) => {
    const entry = workspacePreviewEntryFromPath(path, args.root, index);
    if (entry) addEntry(entry, "session");
  });

  return candidates
    .map((artifact, index) => ({ artifact, rank: workspacePreviewArtifactRank(artifact, index) }))
    .sort(
      (a, b) =>
        a.rank - b.rank ||
        b.artifact.entry.mtime - a.artifact.entry.mtime ||
        a.artifact.entry.relative_path.localeCompare(b.artifact.entry.relative_path),
    )
    .slice(0, args.limit ?? 8)
    .map((item) => item.artifact);
}

export function workspaceComposerPlaceholderForTest(
  args: Parameters<typeof workspaceComposerPlaceholder>[0],
) {
  return workspaceComposerPlaceholder(args);
}

export function workspaceComposerPrimaryActionLabelForTest(
  args: Parameters<typeof workspaceComposerPrimaryActionLabel>[0],
) {
  return workspaceComposerPrimaryActionLabel(args);
}

export function workspaceComposerPlacementLabelForTest(mode: ActiveRunPlacement, hasActiveRun: boolean) {
  return workspaceComposerPlacementLabel(mode, hasActiveRun);
}

export function workspaceComposerTokenForTest(text: string, caret = text.length) {
  return workspaceComposerTokenAt(text, caret);
}

export function composerDraftForThreadForTest(
  drafts: Record<string, string>,
  thread: ThreadIdentity | null,
) {
  return composerDraftForThread(drafts, thread);
}

export function composerDraftsWithValueForTest(
  drafts: Record<string, string>,
  thread: ThreadIdentity | null,
  value: string,
) {
  return composerDraftsWithValue(drafts, thread, value);
}

export function workspaceComposerSuggestionsForTest(
  text: string,
  args: {
    skills?: WorkspaceSkillSuggestion[];
    files?: WorkspaceFileEntry[];
    caret?: number;
  } = {},
) {
  return workspaceComposerSuggestions(workspaceComposerTokenAt(text, args.caret ?? text.length), {
    skills: args.skills ?? [],
    files: args.files ?? [],
  });
}

export function workspaceSelectedSkillInvocationForTest(
  text: string,
  skills: WorkspaceSkillSuggestion[],
) {
  return workspaceSelectedSkillInvocation(text, skills);
}

export function workspaceMentionedFilesFromTextForTest(
  text: string,
  entries: WorkspaceFileEntry[],
) {
  return workspaceMentionedFilesFromText(text, entries);
}

export function workspacePreviewArtifactsForTest(
  args: Parameters<typeof workspacePreviewArtifacts>[0],
) {
  return workspacePreviewArtifacts(args).map((artifact) => ({
    path: artifact.entry.relative_path,
    kind: artifact.kind,
    source: artifact.source,
  }));
}

export function workspacePreviewOpenUrlForTest(args: {
  path: string;
  relativePath?: string;
  root?: string;
  href?: string;
}) {
  const relativePath = args.relativePath ?? workspacePreviewRelativePath(args.path, args.root ?? "");
  return absoluteWorkspacePreviewUrl(
    {
      path: args.path,
      relative_path: relativePath,
      name: fileName(relativePath || args.path),
      parent: relativePath.includes("/") ? relativePath.slice(0, relativePath.lastIndexOf("/")) : "",
      is_directory: false,
      size: 0,
      mtime: 0,
      depth: Math.max(0, relativePath.split("/").filter(Boolean).length - 1),
    },
    args.root ?? "",
    args.href,
  );
}

export function notesComposerRequestsNewDraftForTest(text: string) {
  return notesComposerRequestsNewDraft(text);
}

export function createTemporaryDraftNoteForTest(
  args: Parameters<typeof createTemporaryDraftNote>[0] = {},
) {
  const note = createTemporaryDraftNote(args);
  return {
    ...note,
    bodyStartOffset: frontmatterBodyStartOffset(note.content),
  };
}

export function materializeTemporaryDraftNoteForTest(
  args: Parameters<typeof materializeTemporaryDraftNote>[0],
) {
  return materializeTemporaryDraftNote(args);
}

export function catalogNotesForTest(notes: WorkspaceNote[]) {
  return catalogNotes(notes);
}

export function workspaceAgentOptionsForTest() {
  return WORKSPACE_AGENT_OPTIONS.map((option) => ({ ...option }));
}

export function workspaceModelOptionsForTest(agentId: WorkspaceAgentSelectionId = DEFAULT_AGENT_SELECTION_ID) {
  return workspaceModelOptionsForAgent(agentId).map((option) => ({ ...option }));
}

export function workspaceSelectionFromStorageForTest(
  args: Parameters<typeof workspaceSelectionFromStorageValues>[0] = {},
) {
  const selection = workspaceSelectionFromStorageValues(args);
  return {
    ...selection,
    modelSelectionsByAgent: { ...selection.modelSelectionsByAgent },
  };
}

export function workspaceAgentExecutePayloadForTest(agentId: string, modelId?: string) {
  const agentOption = workspaceAgentOptionForId(agentId);
  const modelOption = workspaceModelOptionForId(modelId, agentOption.id);
  return buildWorkspaceAgentExecutePayload(agentOption, modelOption);
}

export function workspaceAgentExecutePayloadWithAutonomyForTest(
  agentId: string,
  modelId: string | undefined,
  autonomyMode: WorkspaceAutonomyMode,
) {
  const agentOption = workspaceAgentOptionForId(agentId);
  const modelOption = workspaceModelOptionForId(modelId, agentOption.id);
  return buildWorkspaceAgentExecutePayload(agentOption, modelOption, autonomyMode);
}

export function workspaceSurfaceContextForTest(
  args: Partial<Parameters<typeof buildSurfaceContext>[0]> = {},
) {
  return buildSurfaceContext({
    note: null,
    selectedChunk: null,
    selectedBlueprintNode: null,
    workspaceRoot: "/tmp/workspace",
    workspaceId: "workspace",
    notesRoot: "/tmp/knowledge/content",
    workspaceMode: "work",
    agentSelection: workspaceAgentOptionForId(DEFAULT_AGENT_SELECTION_ID),
    modelSelection: workspaceModelOptionForId(
      DEFAULT_MODEL_SELECTION_BY_AGENT[DEFAULT_AGENT_SELECTION_ID],
      DEFAULT_AGENT_SELECTION_ID,
    ),
    autonomyMode: DEFAULT_AUTONOMY_MODE,
    activeFile: null,
    activeFileContent: "",
    wireGuardStatus: null,
    ...args,
  });
}

export function workspaceIntentClassificationForTest(text: string) {
  return {
    forbidsMutation: textForbidsWorkspaceMutation(text),
    requestsMutation: textRequestsWorkspaceMutation(text),
    assessmentOnly: textRequestsAssessmentOnly(text),
    hasRequestPlan: deriveRequestPlanContext(text) !== null,
  };
}

export function liveTaskTreeForTest(nodes: BlueprintNode[]) {
  return liveTaskTreeSnapshot(buildLiveTaskTree(nodes));
}

export function liveTaskGraphRevisionsForTest(nodes: BlueprintNode[]) {
  return buildLiveTaskGraphRevisions(nodes).map((revision) => ({
    id: revision.id,
    label: revision.label,
    meta: revision.meta,
    reason: revision.reason,
    planEdges: revision.planEdges,
    branches: revision.branches.map((branch) => ({
      id: branch.id,
      label: branch.label,
      nodes: branch.nodes.map((node) => ({
        id: node.id,
        title: treeDisplayTitle(node),
        kind: node.kind,
        status: node.status,
        meta: treeNodeMeta(node),
      })),
    })),
  }));
}

export function planTaskChecklistItemsForTest(node: BlueprintNode) {
  return planTaskChecklistItems(node).map(({ task, status }) => ({
    taskId: task.taskId,
    title: task.goal,
    status,
    branchId: task.branchId ?? "",
  }));
}

export function planCardChecklistItemsForTest(node: BlueprintNode) {
  return activePlanTaskChecklistItems(node).map(({ task, status }) => ({
    taskId: task.taskId,
    title: task.goal,
    status,
    branchId: task.branchId ?? "",
  }));
}

export function blueprintTimelineItemsForTest(
  nodes: BlueprintNode[],
  conversationChunks: WorkspaceChunk[],
) {
  return blueprintTimelineItems(nodes, conversationChunks).map((item) =>
    item.kind === "conversation"
      ? { kind: item.kind, id: item.id, chunkId: item.chunk.id, body: item.chunk.body }
      : { kind: item.kind, id: item.id, nodeId: item.node.id, title: item.node.title },
  );
}

export function conversationUserChunksForTest(chunks: WorkspaceChunk[], limit?: number) {
  return conversationUserChunks(chunks, limit);
}

export function queueRowsFromTasksForTest(tasks: ChatV2TaskSnapshot[]) {
  return queueRowsFromTasks(tasks);
}

export function queueDisplayDetailForTest(text: string, fallback?: string) {
  return queueDisplayDetail(text, fallback);
}

export function selectActiveRunningTaskForTest(tasks: ChatV2TaskSnapshot[]) {
  return selectActiveRunningTask(tasks);
}

export function settleTaskSnapshotsFromEventsForTest(
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
) {
  return settleTaskSnapshotsFromEvents(tasks, events);
}

export function sessionProgressTaskForTest(tasks: ChatV2TaskSnapshot[], threadId: string) {
  return runningTaskMapByThreadId(tasks).get(threadId) ?? null;
}

export function sessionReadyResponseAtForTest(tasks: ChatV2TaskSnapshot[]) {
  return sessionReadyResponseAt(tasks);
}

export function sessionHasNewReadyResponseForTest(
  tasks: ChatV2TaskSnapshot[],
  seenAt?: string,
) {
  return sessionHasNewReadyResponse(tasks, seenAt);
}

export function sessionStatusTasksForTest(
  selectedTasks: ChatV2TaskSnapshot[],
  backgroundTasks: ChatV2TaskSnapshot[],
) {
  return mergeTaskSnapshots(backgroundTasks, selectedTasks);
}

export function workPanelTasksForTest(
  selectedTasks: ChatV2TaskSnapshot[],
  backgroundTasks: ChatV2TaskSnapshot[],
  activeThreadId: string,
  events: ChatV2AgentRunEvent[] = [],
) {
  const settled = settleTaskSnapshotsFromEvents(selectedTasks, events);
  const selectedThreadBackgroundTasks = activeThreadId
    ? backgroundTasks.filter((task) => task.thread_id === activeThreadId)
    : [];
  return mergeTaskSnapshots(selectedThreadBackgroundTasks, settled);
}

export function sessionCardDisplayForTest(
  thread: ChatV2ThreadSummary,
  tasks: ChatV2TaskSnapshot[],
) {
  return sessionCardDisplay(thread, tasks);
}

export function taskGroupElapsedCounterForTest(tasks: ChatV2TaskSnapshot[], now?: number) {
  return taskGroupElapsedCounter(tasks, now);
}

export function shouldAutoRestoreSessionForTest(args: Parameters<typeof shouldAutoRestoreSession>[0]) {
  return shouldAutoRestoreSession(args);
}

export function restorableThreadTargetForTest(
  threads: ChatV2ThreadSummary[],
  targetThreadId?: string | null,
  targetWorkflowId?: string | null,
) {
  return restorableThreadTarget(threads, targetThreadId, targetWorkflowId);
}

export function activeThreadArchivedSummaryForTest(
  selection: ActiveThreadSelection,
  threads: ChatV2ThreadSummary[],
) {
  return activeThreadArchivedSummary(selection, threads);
}

export function buildSessionGroupsForTest(args: Parameters<typeof buildSessionGroups>[0]) {
  return buildSessionGroups(args);
}

export function workspaceIdForTasksForTest(
  tasks: ChatV2TaskSnapshot[],
  workspaces: Array<{ id: string; pinnedPaths: string[] }>,
) {
  return workspaceIdForTasks(tasks, workspaces);
}

export function workspaceRootForTasksForTest(tasks: ChatV2TaskSnapshot[]) {
  return workspaceRootForTasks(tasks);
}

export function noteRailViewForFacetForTest(facet: string) {
  return noteRailViewForFacet(facet);
}

export function noteCollectionFacetForTest(facet: string) {
  return noteCollectionFacet(facet);
}

export function noteArticleUpFacetForTest(
  note: WorkspaceNote | null,
  root: string,
  originFacet: string | null,
) {
  return noteArticleUpFacet(note, root, originFacet);
}

export function sortNotesForCollectionForTest(
  notes: WorkspaceNote[],
  sort: NoteCollectionSort,
) {
  return sortNotesForCollection(notes, sort);
}

export function relatedNoteCollectionFacetsForTest(
  notes: WorkspaceNote[],
  root: string,
  facet: string,
) {
  return relatedNoteCollectionFacets(notes, root, facet);
}

export function noteMetaItemsForTest(
  note: WorkspaceNote | null,
  content: string,
  fallbackTitle = "Note",
) {
  return noteMetaItems(note, extractHugoPage(content, fallbackTitle));
}

export function recentModifiedNotesForTest(
  notes: WorkspaceNote[],
  root = "",
  limit = RECENT_MODIFIED_LIMIT,
) {
  return recentModifiedNotes(notes, root, limit);
}

export function workingNoteCardsForTest(
  note: WorkspaceNote | null,
  root = "",
  activeTask: ChatV2TaskSnapshot | null = null,
) {
  return workingNoteCards(note, root, activeTask);
}

export function workPlanHeaderSubtitleForTest(node: BlueprintNode | null, fallbackTitle?: string | null) {
  return workPlanHeaderSubtitle(node, fallbackTitle);
}

export function normalizeStructuredMarkdownForTest(content: string) {
  return normalizeStructuredMarkdown(content);
}

export function previewMarkdownContentForTest(content: string) {
  return previewMarkdownContent(content);
}

export function formatHugoPreviewBodyForTest(content: string) {
  return formatHugoPreviewBody(content);
}

export function hugoPreviewBodyBlocksForTest(content: string) {
  return splitHugoPreviewBody(content);
}

export function hugoPaperPdfPreviewUrlForTest(notesRoot: string, filename: string) {
  return hugoPaperPdfPreviewUrl(notesRoot, filename);
}

export function statusLineUsesMarkdownForTest(content: string) {
  return statusLineUsesMarkdown(content);
}

export function statusMarkdownContentForTest(content: string) {
  return statusMarkdownContent(content);
}

export function blueprintLiveStatusForTest(
  node: BlueprintNode,
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
  activeTask: ChatV2TaskSnapshot | null,
) {
  return blueprintLiveStatus(node, tasks, events, activeTask);
}

export function blueprintCardContentForTest(
  node: BlueprintNode,
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
  activeTask: ChatV2TaskSnapshot | null,
) {
  return blueprintCardContent(node, tasks, events, activeTask);
}

export function sharedEvidenceItemsForTest(
  node: BlueprintNode,
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
  activeTask: ChatV2TaskSnapshot | null,
) {
  return sharedEvidenceItemsForNode(node, tasks, events, activeTask);
}

function blueprintAnchorNode(nodes: BlueprintNode[]) {
  return nodes.find((node) => node.status === "active") ?? null;
}

export function blueprintAnchorNodeForTest(nodes: BlueprintNode[]) {
  return blueprintAnchorNode(nodes);
}

function isMachineProgressText(text: string) {
  return Boolean(
    !text ||
      rawCodexCommandFromSummary(text) ||
      ["model.requested", "tool.started", "completed"].includes(text) ||
      isGraphTelemetryText(text) ||
      isGenericNeedsAttentionText(text) ||
      (/^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$/i.test(text) && !/\s/.test(text)) ||
      /^Token usage\b/i.test(text),
  );
}

function isGenericAgentStatusText(text: string) {
  return /^(?:(?:Super\s+)?DAN|Codex) (?:(?:is )?(?:working|queued|waiting|paused)|needs (?:input|attention)|completed|task)\b/i.test(
    text.trim(),
  );
}

function taskAgentDisplayLabel(task?: ChatV2TaskSnapshot | null) {
  const metadata = task?.metadata ?? {};
  const agentSelection = recordValue(metadata.agent_selection);
  const backend =
    textValue(metadata.selected_backend) ||
    textValue(metadata.backend) ||
    textValue(metadata.agent_backend) ||
    textValue(agentSelection?.backend);
  const selectedAgent =
    textValue(metadata.selected_agent) ||
    textValue(metadata.agent_id) ||
    textValue(agentSelection?.id);
  const label =
    textValue(metadata.selected_agent_label) ||
    textValue(metadata.agent_label) ||
    textValue(agentSelection?.label);
  const guiFor = textValue(metadata.gui_for);
  if (
    backend === CODEX_BACKEND ||
    selectedAgent === "codex" ||
    /\bcodex\b/i.test(label) ||
    /\bcodex\b/i.test(guiFor)
  ) {
    return "Codex";
  }
  return "DAN";
}

function wrapWorkAgentToken(
  text: string,
  pattern: RegExp,
  label: "Codex" | "DAN",
) {
  return text.replace(pattern, (match, offset: number, fullText: string) => {
    const previous = fullText[offset - 1] ?? "";
    const next = fullText[offset + match.length] ?? "";
    if (previous === "`" || next === "`") return match;
    return `\`${label}\``;
  });
}

function highlightWorkAgentText(text: string, task?: ChatV2TaskSnapshot | null) {
  const agentLabel = taskAgentDisplayLabel(task);
  if (agentLabel === "Codex") {
    return wrapWorkAgentToken(text, /\bCodex\b/g, "Codex");
  }
  return wrapWorkAgentToken(text, /\b(?:Super\s+)?DAN\b/g, "DAN");
}

function taskProgressFallbackLabel(task: ChatV2TaskSnapshot) {
  const agent = taskAgentDisplayLabel(task);
  if (task.status === "running") return `${agent} is working`;
  if (task.status === "queued") return `${agent} is queued`;
  if (task.status === "waiting_dependency") return `${agent} is waiting`;
  if (task.status === "needs_input") return `${agent} needs input`;
  if (task.status === "paused") return `${agent} is paused`;
  if (task.status === "completed") return `${agent} completed`;
  if (task.status === "failed" || task.status === "blocked") return `${agent} needs attention`;
  return task.phase || `${agent} task`;
}

function taskProgressLabel(task: ChatV2TaskSnapshot) {
  const text = textValue(task.latest_progress);
  const readableCommand = readableCodexCommandSummary(text);
  if (readableCommand) return readableCommand;
  if (isRuntimeOutputChunkLimitText(text)) return taskProgressFallbackLabel(task);
  if (isMachineProgressText(text)) return taskProgressFallbackLabel(task);
  const structured = formatStructuredAgentDisplay(text);
  if (structured) return structured.body;
  return parseJsonObject(text) ? taskProgressFallbackLabel(task) : text;
}

function taskMessageLabel(task: ChatV2TaskSnapshot) {
  const metadata = task.metadata ?? {};
  const lastSurfaceTurn = metadataObject(metadata.last_surface_turn);
  const operatorContext = metadataObject(metadata.operator_context);
  return (
    textValue(metadata.run_command_text) ||
    textValue(metadata["_run_command_text"]) ||
    textValue(lastSurfaceTurn.text) ||
    textValue(operatorContext.raw_text) ||
    textValue(operatorContext.follow_up_objective) ||
    textValue(metadata.message) ||
    textValue(metadata.objective) ||
    textValue(metadata.text) ||
    taskProgressLabel(task)
  );
}

function taskMessageLooksOperational(text: string) {
  return /^(?:(?:super\s+)?dan|codex|native)\s+(?:is\s+|completed|needs attention)/i.test(
    text.trim(),
  );
}

function normalizeComparableText(value: string) {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function isUsableAttentionReason(text: string, requestText = "") {
  const normalized = text.trim();
  if (!normalized) return false;
  if (isGenericCompletionText(normalized) || isGenericNeedsAttentionText(normalized)) return false;
  if (["blocked", "failed", "denied", "stopped"].includes(normalized.toLowerCase())) return false;
  if (isGenericAgentStatusText(normalized)) return false;
  if (isMachineProgressText(normalized)) return false;
  return !requestText || normalizeComparableText(normalized) !== normalizeComparableText(requestText);
}

function attentionReasonFromValues(values: unknown[], requestText = "") {
  for (const value of values) {
    const items = detailItemsFromValue(value).map(normalizeSummaryLine);
    for (const item of items) {
      if (isUsableAttentionReason(item, requestText)) return item;
    }
  }
  return "";
}

function taskAttentionReason(task: ChatV2TaskSnapshot) {
  const metadata = task.metadata ?? {};
  const requestText = taskRequestText(task);
  const reason = attentionReasonFromValues(
    [
      task.blocker,
      metadata.blocker,
      metadata.blocked_on,
      metadata.blockers,
      metadata.attention_reason,
      metadata.failure_reason,
      metadata.status_reason,
      metadata.reason,
      metadata.error,
      metadata.errors,
      metadata.latest_error,
      metadata.exception,
      task.latest_progress,
      taskProgressLabel(task),
    ],
    requestText,
  );
  return reason ? readableRuntimeIssueText(reason, task) : "";
}

function attentionReasonFromSharedEvidenceItems(items: SharedEvidenceItem[], requestText = "") {
  for (const item of [...items].reverse()) {
    const marker = normalizeSummaryLine(
      [item.kind, item.status, item.title, item.summary].filter(Boolean).join(" "),
    );
    if (!/\b(?:block|blocked|blocker|fail|failed|failure|error|exception|denied|invalid)\b/i.test(marker)) {
      continue;
    }
    const reason = attentionReasonFromValues([item.summary], requestText);
    if (reason) return reason;
  }
  return "";
}

function taskAttentionReasonFromEvents(
  task: ChatV2TaskSnapshot,
  events: ChatV2AgentRunEvent[] = [],
) {
  const requestText = taskRequestText(task);
  const runId = taskRunId(task);
  const relatedEvents = events.filter(
    (event) =>
      (runId && event.run_id === runId) ||
      (task.task_id && event.task_id === task.task_id),
  );
  for (const event of [...relatedEvents].reverse()) {
    const payload = eventPayload(event);
    const reason = attentionReasonFromValues(
      [
        payload.blocker,
        payload.blocked_on,
        payload.blockers,
        payload.attention_reason,
        payload.failure_reason,
        payload.status_reason,
        payload.reason,
        payload.error,
        payload.errors,
        payload.exception,
        eventSummary(event),
      ],
      requestText,
    );
    if (reason) return readableRuntimeIssueText(reason, task);
    const evidenceReason = attentionReasonFromSharedEvidenceItems(
      sharedEvidenceItemsFromEvent(event, 0),
      requestText,
    );
    if (evidenceReason) return readableRuntimeIssueText(evidenceReason, task);
  }
  return "";
}

function taskAttentionDetail(
  task: ChatV2TaskSnapshot,
  events: ChatV2AgentRunEvent[] = [],
) {
  const reason = taskAttentionReason(task) || taskAttentionReasonFromEvents(task, events);
  if (taskIsStaleRunning(task)) {
    const age = taskLastUpdateAge(task);
    return `Saved run still says running${age ? `, but last updated ${age} ago` : ""}.`;
  }
  if (taskStopRequested(task)) {
    return reason || "Stop was requested for this run.";
  }
  if (task.status === "failed") {
    return reason || "DAN failed this step but did not emit an error reason.";
  }
  if (task.status === "blocked") {
    return reason || "DAN blocked this step but did not emit a specific reason.";
  }
  if (task.status === "stopped") {
    return reason || "DAN stopped before this step completed.";
  }
  return reason || taskMessageLabel(task);
}

function humanTerminalTaskProgress(task: ChatV2TaskSnapshot) {
  if (task.status !== "completed") return "";
  const text = rawTerminalTaskProgress(task);
  if (!text || isGenericAgentStatusText(text)) return "";
  const structured = formatStructuredAgentDisplay(text);
  if (structured) return structured.body;
  return parseJsonObject(text) ? "" : text;
}

function rawTerminalTaskProgress(task: ChatV2TaskSnapshot) {
  if (task.status !== "completed") return "";
  const text = textValue(task.latest_progress);
  if (isRuntimeOutputChunkLimitText(text)) return "";
  return isMachineProgressText(text) ? "" : text;
}

function taskQueueLabel(status: string) {
  const normalized = status.trim().toLowerCase();
  if (normalized === "running") return "Active run";
  if (normalized === "stale_running") return "Stale run";
  if (normalized === "stop_requested") return "Stop requested";
  if (normalized === "queued") return "Queued run";
  if (normalized === "waiting_dependency") return "Waiting on dependency";
  if (normalized === "needs_input") return "Needs input";
  if (normalized === "paused") return "Paused";
  if (normalized === "blocked") return "Blocked";
  if (normalized === "stopped") return "Stopped";
  return normalized ? normalized.replace(/_/g, " ") : "Pending run";
}

function queueBlueprintStatus(status: string): BlueprintNodeStatus {
  const normalized = status.trim().toLowerCase();
  if (normalized === "running" || normalized === "injected") return "active";
  if (
    normalized === "needs_input" ||
    normalized === "paused" ||
    normalized === "blocked" ||
    normalized === "stale_running" ||
    normalized === "stop_requested" ||
    normalized === "stopped"
  ) {
    return "blocked";
  }
  return "queued";
}

function followUpPhaseStatuses(row: QueueRow): {
  request: BlueprintNodeStatus;
  plan: BlueprintNodeStatus;
  action: BlueprintNodeStatus;
  response: BlueprintNodeStatus;
} {
  const status = queueBlueprintStatus(row.status);
  if (status === "blocked") {
    return { request: "done", plan: "blocked", action: "future", response: "future" };
  }
  if (status === "active") {
    return { request: "done", plan: "done", action: "active", response: "future" };
  }
  if (status === "done") {
    return { request: "done", plan: "done", action: "done", response: "done" };
  }
  return { request: "done", plan: "future", action: "future", response: "future" };
}

function followUpTimingLabel(row: QueueRow) {
  return row.lane === "continue_after_current"
    ? "Starts after the current run completes"
    : "Joins the current run at a safe checkpoint";
}

function followUpPlanBody(row: QueueRow) {
  return detailMarkdown(followUpTimingLabel(row), [
    {
      title: "Follow-Up",
      items: [row.detail],
    },
  ]);
}

function followUpBlueprintNodes(row: QueueRow, _index: number): BlueprintNode[] {
  const status = followUpPhaseStatuses(row);
  const baseDepth = 0;
  const childDepth = 0;
  const runId = row.runId ?? null;
  const taskId = row.taskId ?? null;
  const timing = followUpTimingLabel(row);
  return [
    {
      id: `blueprint:${row.id}:request`,
      title: "Follow-up request",
      detail: row.label,
      meta: "follow-up",
      body: "Ready for follow-up planning.",
      previewBody: "",
      rawRequest: row.rawDetail ?? row.detail,
      status: status.request,
      kind: "request",
      compact: true,
      depth: baseDepth,
      runId,
      taskId,
      sourceChunkId: row.sourceChunkId,
    },
    {
      id: `blueprint:${row.id}:plan`,
      title: "Plan follow-up",
      detail: timing,
      meta: row.status,
      body: timing,
      previewBody: followUpPlanBody(row),
      status: status.plan,
      kind: "plan",
      compact: status.plan === "future",
      depth: childDepth,
      runId,
      taskId,
    },
    {
      id: `blueprint:${row.id}:action`,
      title: "Work on follow-up",
      detail:
        status.action === "active"
          ? "Applying the follow-up now"
          : "Waits until the follow-up is admitted",
      meta: row.status,
      body:
        status.action === "active"
          ? "Super DAN is working on this follow-up."
          : "This becomes solid when Super DAN reaches the follow-up.",
      previewBody: followUpPlanBody(row),
      status: status.action,
      kind: "build",
      compact: status.action === "future",
      depth: childDepth,
      runId,
      taskId,
    },
    {
      id: `blueprint:${row.id}:response`,
      title: "Follow-up response",
      detail: "Answer after the follow-up is handled",
      meta: "answer",
      body: "This becomes solid when Super DAN responds to the follow-up.",
      previewBody: followUpPlanBody(row),
      status: status.response,
      kind: "answer",
      compact: status.response === "future",
      depth: childDepth,
      runId,
      taskId,
    },
  ];
}

function queueRowsFromTasks(tasks: ChatV2TaskSnapshot[]) {
  const rows: QueueRow[] = [];
  for (const task of tasks) {
    if (!isTaskTerminal(task) && !taskIsStopControlState(task)) {
      const status = taskIsStaleRunning(task) ? "stale_running" : task.status || "queued";
      const rawDetail =
        status === task.status ? taskMessageLabel(task) : taskAttentionDetail(task);
      const polishTaskDetail =
        status === task.status &&
        ["running", "queued", "waiting_dependency"].includes(status.toLowerCase());
      rows.push({
        id: `task:${task.task_id}`,
        label: taskQueueLabel(status),
        detail: polishTaskDetail
          ? queueDisplayDetail(rawDetail, taskProgressFallbackLabel(task))
          : rawDetail,
        rawDetail,
        status,
        active: status === "running",
        kind: "task",
        lane: "task",
        taskId: task.task_id,
        runId: taskRunId(task),
      });
    }

    for (const item of task.metadata?.queue_items ?? []) {
      const status = item.status || "queued";
      if (terminalQueueStatuses.has(status.toLowerCase())) continue;
      const rawDetail = item.text.trim() || "Queued message";
      rows.push({
        id: `queue:${item.id}`,
        label: item.lane === "continue_after_current" ? "Next message" : "Steering message",
        detail: queueDisplayDetail(rawDetail, "Queued follow-up work."),
        rawDetail,
        status,
        active: false,
        kind: "followup",
        lane: item.lane,
        taskId: item.task_id || task.task_id,
        runId: taskRunId(task),
      });
    }
  }
  return rows.slice(0, 8);
}

function taskWithRunHistory(
  task: ChatV2TaskSnapshot,
  run: ChatV2AgentRunRecord | null,
  threadUpdatedAt?: string,
) {
  const payload = run?.command?.payload ?? {};
  const operatorContext = metadataObject(payload.operator_context);
  const runCommandText =
    textValue(payload.text) ||
    textValue(payload.message) ||
    textValue(payload.objective) ||
    textValue(operatorContext.raw_text) ||
    textValue(operatorContext.follow_up_objective);
  return {
    ...task,
    metadata: {
      ...(task.metadata ?? {}),
      ...(run?.created_at ? { run_created_at: run.created_at } : {}),
      ...(run?.updated_at ? { run_updated_at: run.updated_at } : {}),
      ...(run?.status ? { run_status: run.status } : {}),
      ...(runCommandText ? { run_command_text: runCommandText } : {}),
      ...(threadUpdatedAt ? { thread_updated_at: threadUpdatedAt } : {}),
    },
  };
}

async function loadSuperDanThreadHistory(threadId: string, title: string, threadUpdatedAt?: string) {
  const tasks = await listChatV2ThreadTasks(threadId);
  const items = await Promise.all(
    [...tasks].reverse().map(async (task) => {
      const runId = taskRunId(task);
      if (!runId) {
        return {
          task: taskWithRunHistory(task, null, threadUpdatedAt),
          run: null,
          events: [] as ChatV2AgentRunEvent[],
          runId: "",
        };
      }
      const [run, events] = await Promise.all([
        getChatV2AgentRun(runId).catch(() => null),
        getChatV2AgentRunEvents(runId).catch(() => [] as ChatV2AgentRunEvent[]),
      ]);
      return { task: taskWithRunHistory(task, run, threadUpdatedAt), run, events, runId };
    }),
  );
  const events = items.flatMap((item) => item.events);
  const loadedTasks = items.map((item) => item.task);
  const messages: ChatMessage[] = [];
  for (const item of items) {
    const objective =
      objectiveFromRun(item.run, item.task) || (items.length === 1 ? title.trim() : "");
    const timestamp = timestampValue(item.run?.created_at, Date.now());
    if (objective) {
      messages.push({
        id: `agent-history-user-${item.runId || item.task.task_id}`,
        role: "user",
        content: objective,
        timestamp,
        taskRunRef: {
          taskId: item.task.task_id,
          runId: item.runId || null,
          status: item.task.status,
          workspaceRoot: textValue(item.task.metadata?.workspace_root),
          workspaceId: textValue(item.task.metadata?.workspace_id),
        },
      });
    }
    const answerEvent = answerEventFromAgentEvents(item.events);
    const answer =
      answerEvent ? humanEventSummary(answerEvent) : isTaskTerminal(item.task) ? item.task.latest_progress : "";
    if (answer) {
      messages.push({
        id: `agent-history-assistant-${item.runId || item.task.task_id}`,
        role: "assistant",
        content: answer,
        timestamp: timestampValue(item.run?.updated_at, timestamp + 1),
        taskRunRef: {
          taskId: item.task.task_id,
          runId: item.runId || null,
          status: answerEvent?.type || item.task.status,
          workspaceRoot: textValue(item.task.metadata?.workspace_root),
          workspaceId: textValue(item.task.metadata?.workspace_id),
        },
        runEvents: item.events.slice(-20).map(runEventPayloadFromAgentEvent),
      });
    }
  }
  return { tasks: loadedTasks, events, messages };
}

function buildSurfaceContext(args: {
  note: WorkspaceNote | null;
  selectedChunk: WorkspaceChunk | null;
  selectedBlueprintNode: BlueprintNode | null;
  workspaceRoot: string;
  workspaceId?: string;
  notesRoot: string;
  workspaceMode: WorkspacePane;
  agentSelection?: WorkspaceAgentOption;
  modelSelection?: WorkspaceModelOption;
  autonomyMode?: WorkspaceAutonomyMode;
  activeFile: WorkspaceFileEntry | null;
  activeFileContent: string;
  wireGuardStatus: WorkspaceWireGuardStatus | null;
  selectedSkills?: string[];
  mentionedFiles?: WorkspaceFileEntry[];
  attachments?: ComposerAttachmentDraft[];
}) {
  const {
    note,
    selectedChunk,
    selectedBlueprintNode,
    workspaceRoot,
    workspaceId,
    notesRoot,
    workspaceMode,
    agentSelection,
    modelSelection,
    autonomyMode = DEFAULT_AUTONOMY_MODE,
    activeFile,
    activeFileContent,
    wireGuardStatus,
    selectedSkills = [],
    mentionedFiles = [],
    attachments = [],
  } = args;
  const selectedAgent = agentSelection ?? workspaceAgentOptionForId(DEFAULT_AGENT_SELECTION_ID);
  const selectedModel =
    modelSelection ??
    workspaceModelOptionForId(DEFAULT_MODEL_SELECTION_BY_AGENT[selectedAgent.id], selectedAgent.id);
  const autonomyOption = workspaceAutonomyOptionForId(autonomyMode);
  const parsedNote = note ? extractHugoPage(note.content, note.title) : null;
  const activeNoteContext = note
    ? {
        id: note.id,
        title: parsedNote?.meta.title || note.title,
        path: note.path ?? null,
        relative_path: noteRelativePath(note, notesRoot),
        section: noteSection(note, notesRoot),
        source: note.source,
        dirty: note.status === "dirty",
        layout: parsedNote?.meta.layout || note.layout || null,
        pageID: parsedNote?.meta.pageID || note.pageID || null,
        tags: parsedNote?.meta.tags.length ? parsedNote.meta.tags : note.tags,
        categories: parsedNote?.meta.categories.length ? parsedNote.meta.categories : note.categories,
        citations: note.citations,
        draft:
          parsedNote?.meta.draft === "true"
            ? true
            : parsedNote?.meta.draft === "false"
              ? false
              : note.draft ?? null,
        content_preview: note.content.slice(0, 1800),
      }
    : null;
  return {
    identity: { name: "DAN Workspace", role: "chunk_workspace" },
    workspace_root: workspaceRoot,
    workspace_id: workspaceId || workspaceRoot,
    notes_root: notesRoot,
    workspace_mode: workspaceMode,
    workspace_source: "chunk_workspace",
    ui_surface: "chunk_workspace",
    surface_profile: SUPER_TUI_PROFILE,
    agent_profile: SUPER_TUI_PROFILE,
    agent_backend: selectedAgent.backend,
    autonomy_mode: danAutonomyMode(autonomyOption.id),
    permission_mode: autonomyOption.id,
    attention_resolution_mode: danAutonomyMode(autonomyOption.id),
    attention_resolution: {
      mode: danAutonomyMode(autonomyOption.id),
      label: autonomyOption.label,
      description: autonomyOption.description,
    },
    agent_selection: {
      id: selectedAgent.id,
      label: selectedAgent.label,
      backend: selectedAgent.backend,
      model_option: selectedModel.id,
      model_label: selectedModel.label,
      ...(selectedModel.model ? { model: selectedModel.model } : {}),
      ...(selectedModel.reasoningEffort ? { reasoning_effort: selectedModel.reasoningEffort } : {}),
    },
    gui_for: selectedAgent.id === "native" ? "dan super-tui" : `${selectedAgent.id} CLI`,
    ...(selectedSkills.length > 0 ? { selected_skills: selectedSkills } : {}),
    ...workspaceAttachmentSurfaceContext(attachments),
    capabilities: [
      "notes",
      "markdown_preview",
      "chunk_selection",
      "background_agent_runs",
      "checkpoint_commands",
      "read_only_wireguard_status",
    ],
    notes_workspace: {
      kind: "hugo_notes",
      role: workspaceMode === "notes" ? "primary_workspace" : "context_workspace",
      active: workspaceMode === "notes",
      root: notesRoot,
      content_tree: true,
      active_note: activeNoteContext,
      rules: NOTES_WORKSPACE_RULES,
      write_policy: NOTES_WORKSPACE_WRITE_POLICY,
    },
    wireguard_service: wireGuardStatus
      ? {
          service: wireGuardStatus.service,
          mode: wireGuardStatus.mode,
          interface: wireGuardStatus.interface,
          status: wireGuardStatus.status,
          active: wireGuardStatus.active,
          configPresent: wireGuardStatus.config_present,
          safeActions: wireGuardStatus.safe_actions,
          conflictPolicy: wireGuardStatus.conflict_policy,
        }
      : null,
    active_note: activeNoteContext,
    selected_chunk: selectedChunk
      ? {
          id: selectedChunk.id,
          kind: selectedChunk.kind,
          title: selectedChunk.title,
          filePath: selectedChunk.filePath ?? null,
          taskId: selectedChunk.taskId ?? null,
          runId: selectedChunk.runId ?? null,
          preview: selectedChunk.body.slice(0, 1400),
        }
      : null,
    selected_blueprint_node: selectedBlueprintNode
      ? {
          id: selectedBlueprintNode.id,
          title: selectedBlueprintNode.title,
          kind: selectedBlueprintNode.kind,
          status: selectedBlueprintNode.status,
          detail: selectedBlueprintNode.detail,
          meta: selectedBlueprintNode.meta,
          taskId: selectedBlueprintNode.taskId ?? null,
          runId: selectedBlueprintNode.runId ?? null,
          preview: selectedBlueprintNode.body.slice(0, 1400),
        }
      : null,
    active_file: activeFile
      ? {
          path: activeFile.path,
          relativePath: activeFile.relative_path,
          name: activeFile.name,
          preview: activeFileContent.slice(0, 1800),
        }
      : null,
    ...(mentionedFiles.length > 0
      ? {
          mentioned_files: mentionedFiles.slice(0, 6).map((entry) => ({
            path: entry.path,
            relative_path: entry.relative_path,
            name: entry.name,
            kind: entry.is_directory ? "directory" : "file",
            size: entry.size,
            ...(activeFile?.path === entry.path ? { content: activeFileContent.slice(0, 1800) } : {}),
          })),
        }
      : {}),
  };
}

function blueprintStatusTone(status: BlueprintNodeStatus) {
  if (status === "active") {
    return {
      node: "border-cyan-400 bg-cyan-50/80 text-slate-950 shadow-[0_8px_24px_rgba(8,145,178,0.12)] ring-1 ring-cyan-200 dark:border-cyan-500 dark:bg-cyan-950/25 dark:text-cyan-50 dark:ring-cyan-800",
      marker: "border-cyan-500 bg-cyan-500 text-white",
      badge: "border-cyan-300 bg-cyan-100 text-cyan-800 dark:border-cyan-700 dark:bg-cyan-950 dark:text-cyan-100",
    };
  }
  if (status === "ready") {
    return {
      node: "border-amber-300 bg-amber-50/65 text-slate-900 dark:border-amber-700 dark:bg-amber-950/20 dark:text-amber-50",
      marker: "border-amber-500 bg-amber-400 text-amber-950",
      badge: "border-amber-300 bg-amber-100 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100",
    };
  }
  if (status === "blocked") {
    return {
      node: "border-rose-300 bg-rose-50/70 text-slate-950 dark:border-rose-800 dark:bg-rose-950/25 dark:text-rose-50",
      marker: "border-rose-500 bg-rose-500 text-white",
      badge: "border-rose-300 bg-rose-100 text-rose-800 dark:border-rose-700 dark:bg-rose-950 dark:text-rose-100",
    };
  }
  if (status === "future") {
    return {
      node: "border-dashed border-slate-300 bg-white/45 text-slate-500 opacity-75 dark:border-slate-700 dark:bg-slate-950/35 dark:text-slate-400",
      marker: "border-slate-300 bg-white text-slate-400 dark:border-slate-700 dark:bg-slate-950",
      badge: "border-slate-200 bg-white/70 text-slate-400 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-500",
    };
  }
  if (status === "queued") {
    return {
      node: "border-dashed border-violet-200 bg-violet-50/35 text-slate-600 dark:border-violet-900 dark:bg-violet-950/15 dark:text-violet-100",
      marker: "border-violet-300 bg-violet-100 text-violet-700 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-200",
      badge: "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-100",
    };
  }
  return {
    node: "border-emerald-200 bg-white text-slate-900 dark:border-emerald-900/70 dark:bg-slate-950 dark:text-slate-100",
    marker: "border-emerald-500 bg-emerald-500 text-white",
    badge: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-100",
  };
}

function blueprintKindIcon(kind: BlueprintNodeKind) {
  if (kind === "request") return MessageSquareText;
  if (kind === "understanding") return Lightbulb;
  if (kind === "plan") return Cable;
  if (kind === "task") return Activity;
  if (kind === "worktree") return FolderOpen;
  if (kind === "build") return TerminalSquare;
  if (kind === "tool") return WandSparkles;
  if (kind === "change") return FileText;
  if (kind === "validation") return Shield;
  if (kind === "repair") return WandSparkles;
  if (kind === "decision") return GitBranchIcon;
  if (kind === "artifact") return FileText;
  if (kind === "gate") return Shield;
  if (kind === "hypothesis") return Lightbulb;
  if (kind === "evidence") return ScrollText;
  if (kind === "variant") return WandSparkles;
  if (kind === "approval") return Check;
  if (kind === "loop") return RotateCcw;
  if (kind === "composite") return Cable;
  if (kind === "answer") return Bot;
  return Clock3;
}

function noteStatusText(note: WorkspaceNote | null) {
  if (!note) return "No note";
  if (isTemporaryDraftNote(note) && note.status === "dirty") return "Draft";
  if (isTemporaryDraftNote(note) && note.status === "clean") return "Staged draft";
  if (note.status === "dirty") return "Unsaved";
  if (note.status === "saving") return "Saving";
  if (note.status === "loading") return "Loading";
  if (note.status === "error") return "Save issue";
  return "Saved";
}

async function restoreFullNativeMessages(sourceMessages: ChatMessage[]): Promise<ChatMessage[]> {
  return Promise.all(sourceMessages.map(async (message) => {
          if (message.role !== "assistant" || !message.taskRunRef?.runId) return message;
          try {
            const run = await getChatV2AgentRun(message.taskRunRef.runId);
            const result = run.metadata?.backend_result as { backend?: string; summary?: string } | undefined;
            if (run.status === "completed" && ["codex", "claude", "antigravity"].includes(result?.backend || "") && result?.summary && result.summary.startsWith(message.content) && result.summary.length > message.content.length) {
              return { ...message, content: result.summary };
            }
          } catch { /* Keep saved text when run history is unavailable. */ }
          return message;
        }));
}

function WorkspaceComposerSuggestionPopup({
  suggestions,
  activeIndex,
  onActiveIndexChange,
  onSelect,
}: {
  suggestions: WorkspaceComposerSuggestion[];
  activeIndex: number;
  onActiveIndexChange: (index: number) => void;
  onSelect: (suggestion: WorkspaceComposerSuggestion) => void;
}) {
  if (suggestions.length === 0) return null;
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl shadow-slate-950/10 dark:border-slate-800 dark:bg-slate-950">
      {suggestions.map((suggestion, index) => {
        const active = index === activeIndex;
        const Icon =
          suggestion.type === "command"
            ? TerminalSquare
            : suggestion.type === "skill"
              ? WandSparkles
              : suggestion.meta === "folder"
                ? Folder
                : File;
        return (
          <button
            key={suggestion.id}
            type="button"
            onMouseDown={(event) => event.preventDefault()}
            onMouseEnter={() => onActiveIndexChange(index)}
            onClick={() => onSelect(suggestion)}
            className={cx(
              "flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition",
              active
                ? "bg-slate-100 text-slate-950 dark:bg-slate-800 dark:text-white"
                : "text-slate-600 hover:bg-slate-50 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900 dark:hover:text-white",
            )}
          >
            <Icon size={13} className="shrink-0" />
            <span className="min-w-0 flex-1 truncate text-xs font-semibold">{suggestion.label}</span>
            <span className="max-w-[45%] truncate text-[11px] text-slate-400">
              {suggestion.detail || suggestion.meta}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function FacetIndex({
  kind,
  entries,
  activeFacet,
  total,
  onSelect,
}: {
  kind: "tag" | "section";
  entries: Array<[string, number]>;
  activeFacet: string;
  total: number;
  onSelect: (facet: string) => void;
}) {
  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-200 bg-white px-3 py-4 text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-950">
        No {kind === "tag" ? "tags" : "categories"} yet.
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <button
        type="button"
        onClick={() => onSelect("all")}
        className={cx(
          "flex h-9 w-full items-center justify-between rounded-lg px-2.5 text-left text-[13px] transition",
          activeFacet === "all"
            ? "bg-slate-900 text-white shadow-sm dark:bg-slate-100 dark:text-slate-950"
            : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-900",
        )}
      >
        <span className="min-w-0 truncate">All content</span>
        <span className="shrink-0 text-[11px] opacity-70">{total}</span>
      </button>
      {entries.map(([value, count]) => {
        const facet = noteFacetKey(kind, value);
        const active = activeFacet === facet;
        return (
          <button
            key={facet}
            type="button"
            onClick={() => onSelect(facet)}
            className={cx(
              "flex min-h-9 w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition",
              active
                ? "bg-slate-900 text-white shadow-sm dark:bg-slate-100 dark:text-slate-950"
                : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-900",
            )}
          >
            <span className="min-w-0 truncate">
              {kind === "tag" ? `#${value}` : value}
            </span>
            <span className="shrink-0 rounded-full border border-current/15 px-1.5 py-0.5 text-[10px] opacity-70">
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function FacetArticlePanel({
  kind,
  facet,
  notes,
  activeNoteId,
  root,
  onBack,
  onSelectNote,
}: {
  kind: "tag" | "section" | "category";
  facet: string;
  notes: WorkspaceNote[];
  activeNoteId: string | null;
  root: string;
  onBack: () => void;
  onSelectNote: (note: WorkspaceNote) => void;
}) {
  const isTag = kind === "tag";
  const title = isTag
    ? noteFacetTitle(facet).replace(/^Tag · /, "#")
    : noteFacetTitle(facet).replace(/^Category · /, "");
  const articleLabel = notes.length === 1 ? "article" : "articles";
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onBack}
        className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-[12px] font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-900 dark:hover:text-slate-100"
      >
        <ChevronRight size={13} className="rotate-180" />
        <span>{isTag ? "All tags" : "All categories"}</span>
      </button>
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
        <div className="border-b border-slate-200 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/60">
          <div className="truncate text-[13px] font-semibold text-slate-900 dark:text-slate-100">
            {title}
          </div>
          <div className="mt-0.5 text-[11px] text-slate-400">
            {notes.length} {articleLabel}
          </div>
        </div>
        <div className="max-h-[55vh] space-y-1 overflow-auto p-1.5">
          {notes.length > 0 ? (
            notes.map((note) => {
              const active = note.id === activeNoteId;
              return (
                <button
                  key={note.id}
                  type="button"
                  onClick={() => onSelectNote(note)}
                  className={cx(
                    "dan-rail-card-row dan-note-tree-row flex w-full min-w-0 items-start gap-1.5 rounded-lg border px-1.5 py-2 text-left transition",
                    railCardTone(active),
                  )}
                >
                  <span className="h-3.5 w-3.5 shrink-0" />
                  <span className="dan-rail-card-kind mt-0.5">
                    <FileText size={12} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="dan-rail-card-title block truncate">
                      {note.title || fileName(note.relativePath || note.path || "Untitled")}
                    </span>
                    <span className="dan-rail-card-meta">{noteCardMeta(note, root)}</span>
                  </span>
                </button>
              );
            })
          ) : (
            <div className="px-2 py-3 text-sm text-slate-400">No articles found.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function NoteCollectionPage({
  facet,
  notes,
  root,
  query,
  onSelectNote,
  onSelectFacet,
}: {
  facet: string;
  notes: WorkspaceNote[];
  root: string;
  query: string;
  onSelectNote: (note: WorkspaceNote) => void;
  onSelectFacet: (facet: string) => void;
}) {
  const [sort, setSort] = useState<NoteCollectionSort>("recent");
  const [layout, setLayout] = useState<NoteCollectionLayout>("list");
  const [kind, ...valueParts] = facet.split(":");
  const value = valueParts.join(":");
  const isTag = kind === "tag";
  const sortedNotes = useMemo(
    () => sortNotesForCollection(notes, sort),
    [notes, sort],
  );
  const related = useMemo(
    () => relatedNoteCollectionFacets(notes, root, facet),
    [facet, notes, root],
  );
  const articleLabel = notes.length === 1 ? "article" : "articles";
  const queryLabel = query.trim();

  return (
    <section className="w-full min-w-0">
      <header className="border-b border-slate-200 pb-5 dark:border-slate-800">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-400">
              {isTag ? "Tag collection" : "Category collection"}
            </div>
            <h1 className="mt-2 flex flex-wrap items-baseline gap-x-2 text-2xl font-semibold leading-tight text-slate-950 dark:text-slate-100">
              <span className="break-words">{isTag ? `#${value}` : value}</span>
              <span className="font-mono text-sm font-medium tabular-nums text-slate-400">
                ({notes.length})
              </span>
            </h1>
            {queryLabel && (
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                {notes.length} {articleLabel} matching “{queryLabel}”
              </p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setSort((current) => current === "recent" ? "title" : "recent")}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-600 shadow-sm transition hover:border-slate-300 hover:text-slate-950 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300 dark:hover:border-slate-700 dark:hover:text-white"
              aria-label={`Sort collection by ${sort === "recent" ? "title" : "recent update"}`}
            >
              {sort === "recent" ? <Clock3 size={12} /> : <ArrowUp size={12} />}
              <span>{sort === "recent" ? "Recent" : "A–Z"}</span>
            </button>
            <button
              type="button"
              onClick={() => setLayout((current) => current === "list" ? "grid" : "list")}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-600 shadow-sm transition hover:border-slate-300 hover:text-slate-950 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300 dark:hover:border-slate-700 dark:hover:text-white"
              aria-label={`Show collection as a ${layout === "list" ? "grid" : "list"}`}
            >
              {layout === "list" ? <Square size={11} /> : <FileText size={12} />}
              <span>{layout === "list" ? "Grid" : "List"}</span>
            </button>
          </div>
        </div>

        {related.entries.length > 0 && (
          <div className="mt-4 overflow-x-auto overscroll-x-contain pb-1">
            <div className="grid w-max grid-flow-col grid-rows-2 auto-cols-max gap-x-1.5 gap-y-1.5 pr-1">
              {related.entries.map(([relatedValue, count]) => (
                <button
                  key={`${related.kind}:${relatedValue}`}
                  type="button"
                  onClick={() => onSelectFacet(noteFacetKey(related.kind, relatedValue))}
                  className={cx(
                    "inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-[11px] font-medium transition",
                    related.kind === "tag"
                      ? "border-sky-200 bg-sky-50 text-sky-800 hover:border-sky-300 hover:bg-white dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-200"
                      : "border-slate-200 bg-slate-50 text-slate-700 hover:border-slate-300 hover:bg-white dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
                  )}
                >
                  <span>{related.kind === "tag" ? `#${relatedValue}` : relatedValue}</span>
                  <span className="font-mono text-[9px] tabular-nums opacity-55">{count}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </header>

      {sortedNotes.length > 0 ? (
        <div
          className={cx(
            "mt-5",
            layout === "grid"
              ? "grid grid-cols-[repeat(auto-fill,minmax(min(100%,17rem),1fr))] gap-3"
              : "space-y-2.5",
          )}
        >
          {sortedNotes.map((note) => {
            const section = noteSection(note, root);
            const updated = formatNoteUpdatedDate(note, note.lastmod || note.date || "");
            const accentColor = noteCollectionAccentColor(note, root);
            return (
              <button
                key={note.id}
                type="button"
                onClick={() => onSelectNote(note)}
                className={cx(
                  "dan-note-collection-card group relative w-full overflow-hidden rounded-xl border border-slate-200 border-l-[3px] bg-white p-4 text-left shadow-sm shadow-slate-950/[0.025] transition duration-200 hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md hover:shadow-slate-950/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40 dark:border-slate-800 dark:bg-slate-950 dark:hover:border-slate-700",
                  layout === "grid" ? "flex min-h-44 flex-col" : "min-h-28",
                )}
                style={{
                  "--note-collection-accent": accentColor,
                  borderLeftColor: accentColor,
                } as CSSProperties}
              >
                <span className="flex min-w-0 items-center justify-between gap-3">
                  <span className="min-w-0 truncate text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
                    {section}
                  </span>
                  {updated && (
                    <span className="shrink-0 font-mono text-[10px] tabular-nums text-slate-400">
                      {updated}
                    </span>
                  )}
                </span>
                <span className="mt-2 flex min-w-0 items-start gap-2">
                  <span className="min-w-0 flex-1 text-[15px] font-semibold leading-5 text-slate-900 transition group-hover:text-sky-800 dark:text-slate-100 dark:group-hover:text-sky-200">
                    {note.title || fileName(note.relativePath || note.path || "Untitled")}
                  </span>
                  <ChevronRight
                    size={14}
                    className="mt-0.5 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-500 dark:text-slate-700 dark:group-hover:text-slate-400"
                  />
                </span>
                <span className="mt-1.5 line-clamp-2 break-words text-[12px] leading-5 text-slate-500 dark:text-slate-400">
                  {noteCollectionDescription(note, root)}
                </span>
                {note.tags.length > 0 && (
                  <span className={cx("mt-3 flex flex-wrap gap-1.5", layout === "grid" && "mt-auto pt-3")}>
                    {note.tags.slice(0, 5).map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-900 dark:text-slate-400"
                      >
                        #{tag}
                      </span>
                    ))}
                    {note.tags.length > 5 && (
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-400 dark:bg-slate-900">
                        +{note.tags.length - 5}
                      </span>
                    )}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="mt-5 rounded-xl border border-dashed border-slate-300 bg-slate-50/80 px-5 py-10 text-center dark:border-slate-800 dark:bg-slate-900/35">
          <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">
            No articles found
          </div>
          <div className="mt-1 text-xs text-slate-400">
            {queryLabel ? "Try a broader content search." : "This collection is currently empty."}
          </div>
        </div>
      )}
    </section>
  );
}

function formatLearnDate(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

const LEARN_COURSE_NOT_GENERATED_MESSAGE = "Course not generated yet.";

function learnPanelMessage(error: unknown, fallback = "Learning sessions are not ready yet.") {
  const raw = error instanceof Error ? error.message : String(error || "");
  const message = raw.trim();
  if (!message) return fallback;
  if (/not found/i.test(message) || /\b404\b/.test(message)) {
    return LEARN_COURSE_NOT_GENERATED_MESSAGE;
  }
  if (/course has not been generated/i.test(message)) {
    return LEARN_COURSE_NOT_GENERATED_MESSAGE;
  }
  return message;
}

function NoteLearningPanel({
  note,
  root,
  course,
  activeSession,
  completedSessionIds,
  status,
  error,
  onGenerate,
  onRegenerate,
  onSelectSession,
  onToggleSession,
  onOpenManual,
  onCollapse,
}: {
  note: WorkspaceNote | null;
  root: string;
  course: WorkspaceLearnCourse | null;
  activeSession: WorkspaceLearnSession | null;
  completedSessionIds: Set<string>;
  status: "idle" | "loading" | "generating" | "saving" | "error";
  error: string;
  onGenerate: () => void;
  onRegenerate: () => void;
  onSelectSession: (sessionId: string) => void;
  onToggleSession: (sessionId: string) => void;
  onOpenManual: () => void;
  onCollapse: () => void;
}) {
  const sessions = course?.sessions ?? [];
  const completedCount = sessions.filter((session) => completedSessionIds.has(session.id)).length;
  const progressPercent = sessions.length
    ? Math.round((completedCount / sessions.length) * 100)
    : 0;
  const busy = status === "loading" || status === "generating" || status === "saving";
  const canGenerate = note
    ? Boolean(note.path) && !isTemporaryDraftNote(note) && note.source !== "local" && !busy
    : false;
  const emptyCourseMessage = error || LEARN_COURSE_NOT_GENERATED_MESSAGE;
  const emptyCourseIsNotice = emptyCourseMessage === LEARN_COURSE_NOT_GENERATED_MESSAGE;

  return (
    <aside className="dan-phone-page flex min-h-0 flex-col border-l border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-950">
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-200/80 px-4 dark:border-slate-800">
        <div className="min-w-0">
          <div className="truncate text-[14px] font-semibold leading-5">Learn</div>
          <div className="truncate text-[11px] leading-4 text-slate-500">
            {note ? noteDisplayPath(note, root) : "No note selected"}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onOpenManual}
            disabled={!note}
            className="grid h-7 w-7 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition enabled:hover:border-slate-300 enabled:hover:text-slate-800 disabled:opacity-35 dark:border-slate-800 dark:bg-slate-950"
            title="Open manual"
            aria-label="Open manual"
          >
            <ScrollText size={13} />
          </button>
          <PaneHeaderButton title="Collapse Learn" onClick={onCollapse}>
            <ChevronRight size={13} />
          </PaneHeaderButton>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {!note ? (
          <div className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-sm text-slate-400 dark:border-slate-800">
            No note selected.
          </div>
        ) : status === "loading" ? (
          <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900">
            <Loader2 size={14} className="animate-spin" />
            <span>Loading sessions</span>
          </div>
        ) : !course ? (
          <div className="space-y-3">
            <div className="rounded-lg border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-800 dark:bg-slate-900/60">
              <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">
                Manual
              </div>
              <div className="mt-1 text-sm font-semibold text-slate-900 dark:text-slate-100">
                {note.title || "Untitled note"}
              </div>
              <div className="mt-1 truncate text-[12px] text-slate-500">
                {noteDisplayPath(note, root)}
              </div>
            </div>
            {emptyCourseMessage && (
              <div
                className={cx(
                  "rounded-lg border px-3 py-2 text-[12px] leading-5",
                  emptyCourseIsNotice
                    ? "border-slate-200 bg-white text-slate-500 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-400"
                    : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200",
                )}
              >
                {emptyCourseMessage}
              </div>
            )}
            <button
              type="button"
              onClick={onGenerate}
              disabled={!canGenerate}
              className="flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-slate-900 bg-slate-900 px-3 text-[13px] font-semibold text-white transition enabled:hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-100 dark:bg-slate-100 dark:text-slate-950 dark:enabled:hover:bg-white"
            >
              {status === "generating" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <WandSparkles size={14} />
              )}
              <span>Generate sessions</span>
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="rounded-lg border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-800 dark:bg-slate-900/60">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                    {course.note_title || note.title}
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-500">
                    {completedCount}/{sessions.length} sessions
                    {course.generated_at ? ` · ${formatLearnDate(course.generated_at)}` : ""}
                  </div>
                </div>
                <span className="shrink-0 rounded-full border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-500 dark:border-slate-800 dark:bg-slate-950">
                  {progressPercent}%
                </span>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-all"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              {course.stale && (
                <div className="mt-3 flex items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[12px] text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                  <span>Note changed after generation.</span>
                  <button
                    type="button"
                    onClick={onRegenerate}
                    disabled={busy}
                    className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-amber-300 bg-white px-2 font-semibold transition enabled:hover:bg-amber-100 disabled:opacity-50 dark:border-amber-800 dark:bg-amber-950/50"
                  >
                    <RotateCcw size={12} />
                    <span>Refresh</span>
                  </button>
                </div>
              )}
              {error && (
                <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-2 text-[12px] leading-5 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">
                  {error}
                </div>
              )}
            </div>

            <div className="space-y-1.5">
              {sessions.map((session, index) => {
                const active = session.id === activeSession?.id;
                const done = completedSessionIds.has(session.id);
                return (
                  <button
                    key={session.id}
                    type="button"
                    onClick={() => onSelectSession(session.id)}
                    className={cx(
                      "flex w-full min-w-0 items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition",
                      active
                        ? "border-slate-900 bg-slate-900 text-white shadow-sm dark:border-slate-100 dark:bg-slate-100 dark:text-slate-950"
                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300 dark:hover:bg-slate-900",
                    )}
                  >
                    <span
                      className={cx(
                        "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md border text-[10px]",
                        done
                          ? "border-emerald-500 bg-emerald-500 text-white"
                          : active
                            ? "border-current/30"
                            : "border-slate-200 text-slate-400 dark:border-slate-800",
                      )}
                    >
                      {done ? <Check size={12} /> : index + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-semibold">
                        {session.title}
                      </span>
                      <span className={cx("block truncate text-[11px]", active ? "opacity-70" : "text-slate-400")}>
                        {session.duration_minutes} min · {session.summary}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>

            {activeSession && (
              <article className="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-950">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-950 dark:text-slate-100">
                      {activeSession.title}
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-500">
                      {activeSession.duration_minutes} min
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => onToggleSession(activeSession.id)}
                    className={cx(
                      "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-lg border px-2 text-[12px] font-semibold transition",
                      completedSessionIds.has(activeSession.id)
                        ? "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200"
                        : "border-slate-200 bg-slate-50 text-slate-600 hover:border-slate-300 hover:bg-white dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300",
                    )}
                  >
                    <Check size={12} />
                    <span>{completedSessionIds.has(activeSession.id) ? "Done" : "Mark done"}</span>
                  </button>
                </div>
                {activeSession.objectives.length > 0 && (
                  <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/70">
                    <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
                      Objectives
                    </div>
                    <ul className="mt-1 space-y-1 text-[12px] leading-5 text-slate-600 dark:text-slate-300">
                      {activeSession.objectives.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="mt-3 max-h-72 overflow-auto rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/45">
                  <MarkdownRenderer
                    content={activeSession.body || activeSession.summary || "_No session text._"}
                    className="text-sm leading-6 [&_h1]:text-base [&_h2]:text-base [&_h3]:text-sm"
                  />
                </div>
                {activeSession.practice.length > 0 && (
                  <div className="mt-3">
                    <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
                      Practice
                    </div>
                    <div className="mt-1 space-y-1 text-[12px] leading-5 text-slate-600 dark:text-slate-300">
                      {activeSession.practice.map((item) => (
                        <div key={item}>{item}</div>
                      ))}
                    </div>
                  </div>
                )}
              </article>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

const KNOWLEDGE_GRAPH_COLORS: Record<string, string> = {
  papers: "#7c3aed",
  notes: "#0891b2",
  log: "#ea580c",
  "to-do": "#16a34a",
  blogs: "#2563eb",
  research: "#e11d48",
  root: "#64748b",
};

const KNOWLEDGE_GRAPH_MIN_WIDTH = 920;
const KNOWLEDGE_GRAPH_MIN_HEIGHT = 560;
const KNOWLEDGE_GRAPH_MIN_WORLD_WIDTH = 1800;
const KNOWLEDGE_GRAPH_MIN_WORLD_HEIGHT = 1100;
const KNOWLEDGE_GRAPH_MIN_ZOOM = 0.32;
const KNOWLEDGE_GRAPH_MAX_ZOOM = 2.8;

function clampKnowledgeGraphZoom(value: number) {
  return Math.max(KNOWLEDGE_GRAPH_MIN_ZOOM, Math.min(KNOWLEDGE_GRAPH_MAX_ZOOM, value));
}

function buildKnowledgeGraph(
  notes: WorkspaceNote[],
  root: string,
  viewSize?: { width: number; height: number },
): {
  nodes: KnowledgeGraphNode[];
  links: KnowledgeGraphLink[];
  sections: string[];
  viewWidth: number;
  viewHeight: number;
  worldWidth: number;
  worldHeight: number;
} {
  const pageNodes = notes.filter((note) => pageIdFromNote(note));
  const nodeById = new Map(pageNodes.map((note) => [pageIdFromNote(note), note]));
  const edgeSeen = new Set<string>();
  const links: KnowledgeGraphLink[] = [];
  const degree: Record<string, number> = {};

  for (const note of pageNodes) {
    const source = pageIdFromNote(note);
    for (const target of [...new Set(note.citations ?? [])]) {
      if (!nodeById.has(target) || target === source) continue;
      const key = `${source}->${target}`;
      if (edgeSeen.has(key)) continue;
      edgeSeen.add(key);
      links.push({ source, target });
      degree[source] = (degree[source] ?? 0) + 1;
      degree[target] = (degree[target] ?? 0) + 1;
    }
  }

  const sections = [...new Set(pageNodes.map((note) => noteSection(note, root)))].sort();
  const sectionIndex = new Map(sections.map((section, index) => [section, index]));
  const sectionCounts = new Map<string, number>();
  const viewWidth = Math.max(KNOWLEDGE_GRAPH_MIN_WIDTH, Math.round(viewSize?.width ?? 0));
  const viewHeight = Math.max(KNOWLEDGE_GRAPH_MIN_HEIGHT, Math.round(viewSize?.height ?? 0));
  const worldWidth = Math.max(KNOWLEDGE_GRAPH_MIN_WORLD_WIDTH, Math.round(viewWidth * 1.42));
  const worldHeight = Math.max(KNOWLEDGE_GRAPH_MIN_WORLD_HEIGHT, Math.round(viewHeight * 1.48));
  const centerX = worldWidth / 2;
  const centerY = worldHeight / 2;
  const sectionRadiusX = worldWidth * 0.30;
  const sectionRadiusY = worldHeight * 0.29;
  const nodes = pageNodes.map((note, index) => {
    const id = pageIdFromNote(note);
    const section = noteSection(note, root);
    const groupIndex = sectionIndex.get(section) ?? 0;
    const groupCount = Math.max(1, sectionCounts.get(section) ?? 0);
    sectionCounts.set(section, groupCount + 1);
    const groupAngle = (Math.PI * 2 * groupIndex) / Math.max(1, sections.length) - Math.PI / 2;
    const localAngle = (index * 2.399963229728653) % (Math.PI * 2);
    const localRadius = 34 + (groupCount % 11) * Math.max(18, Math.min(30, worldWidth / 88));
    const groupX = centerX + Math.cos(groupAngle) * sectionRadiusX;
    const groupY = centerY + Math.sin(groupAngle) * sectionRadiusY;
    const nodeDegree = degree[id] ?? 0;
    return {
      id,
      title: note.title,
      section,
      degree: nodeDegree,
      note,
      x: Math.max(42, Math.min(worldWidth - 42, groupX + Math.cos(localAngle) * localRadius)),
      y: Math.max(42, Math.min(worldHeight - 42, groupY + Math.sin(localAngle) * localRadius)),
      radius: Math.max(4.5, Math.min(15, 5 + nodeDegree * 1.4)),
    };
  });

  return { nodes, links, sections, viewWidth, viewHeight, worldWidth, worldHeight };
}

function KnowledgeGraphView({
  notes,
  root,
  onSelect,
}: {
  notes: WorkspaceNote[];
  root: string;
  onSelect: (note: WorkspaceNote) => void;
}) {
  const [section, setSection] = useState("");
  const [query, setQuery] = useState("");
  const [showLabels, setShowLabels] = useState(true);
  const [showOrphans, setShowOrphans] = useState(true);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const graphFrameRef = useRef<HTMLDivElement | null>(null);
  const graphContentKeyRef = useRef("");
  const graphCanvasInteractedRef = useRef(false);
  const panGestureRef = useRef({
    pointerId: -1,
    startClientX: 0,
    startClientY: 0,
    startX: 0,
    startY: 0,
  });
  const [graphSize, setGraphSize] = useState({
    width: KNOWLEDGE_GRAPH_MIN_WIDTH,
    height: KNOWLEDGE_GRAPH_MIN_HEIGHT,
  });
  const [canvasTransform, setCanvasTransform] = useState({
    x: 0,
    y: 0,
    scale: 1,
  });
  const [isPanning, setIsPanning] = useState(false);
  useEffect(() => {
    const frame = graphFrameRef.current;
    if (!frame) return;
    const updateSize = () => {
      const rect = frame.getBoundingClientRect();
      const width = Math.max(KNOWLEDGE_GRAPH_MIN_WIDTH, Math.round(rect.width));
      const height = Math.max(KNOWLEDGE_GRAPH_MIN_HEIGHT, Math.round(rect.height));
      setGraphSize((previous) =>
        previous.width === width && previous.height === height ? previous : { width, height },
      );
    };
    updateSize();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateSize);
      return () => window.removeEventListener("resize", updateSize);
    }
    const observer = new ResizeObserver(updateSize);
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);
  const graph = useMemo(() => buildKnowledgeGraph(notes, root, graphSize), [notes, root, graphSize]);
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const nodes = graph.nodes.filter((node) => {
      if (section && node.section !== section) return false;
      if (!showOrphans && node.degree === 0) return false;
      if (
        normalized &&
        !`${node.title} ${node.id} ${node.section}`.toLowerCase().includes(normalized)
      ) {
        return false;
      }
      return true;
    });
    const ids = new Set(nodes.map((node) => node.id));
    return {
      nodes,
      links: graph.links.filter((link) => ids.has(link.source) && ids.has(link.target)),
      ids,
    };
  }, [graph, query, section, showOrphans]);
  const visibleBounds = useMemo(() => {
    if (visible.nodes.length === 0) {
      return {
        minX: 0,
        minY: 0,
        maxX: graph.worldWidth,
        maxY: graph.worldHeight,
      };
    }
    const margin = 120;
    return visible.nodes.reduce(
      (bounds, node) => ({
        minX: Math.min(bounds.minX, node.x - node.radius - margin),
        minY: Math.min(bounds.minY, node.y - node.radius - margin),
        maxX: Math.max(bounds.maxX, node.x + node.radius + margin),
        maxY: Math.max(bounds.maxY, node.y + node.radius + margin),
      }),
      {
        minX: Number.POSITIVE_INFINITY,
        minY: Number.POSITIVE_INFINITY,
        maxX: Number.NEGATIVE_INFINITY,
        maxY: Number.NEGATIVE_INFINITY,
      },
    );
  }, [graph.worldHeight, graph.worldWidth, visible.nodes]);
  const nodeById = useMemo(
    () => new Map(visible.nodes.map((node) => [node.id, node])),
    [visible.nodes],
  );
  const neighborIds = useMemo(() => {
    if (!hoveredId) return null;
    const ids = new Set([hoveredId]);
    for (const link of visible.links) {
      if (link.source === hoveredId) ids.add(link.target);
      if (link.target === hoveredId) ids.add(link.source);
    }
    return ids;
  }, [hoveredId, visible.links]);
  const selectedNode = hoveredId ? nodeById.get(hoveredId) ?? null : null;
  const sectionCounts = useMemo(
    () =>
      new Map(
        graph.sections.map((item) => [
          item,
          graph.nodes.filter((node) => node.section === item).length,
        ]),
      ),
    [graph],
  );
  const graphContentKey = useMemo(
    () =>
      [
        root,
        ...notes
          .map((note) => {
            const pageId = pageIdFromNote(note) || note.id;
            const citations = [...(note.citations ?? [])].sort().join(",");
            return `${pageId}:${noteSection(note, root)}:${citations}`;
          })
          .sort(),
      ].join("|"),
    [notes, root],
  );
  const centerCanvasTransform = useCallback(
    () => ({
      scale: 1,
      x: graph.viewWidth / 2 - graph.worldWidth / 2,
      y: graph.viewHeight / 2 - graph.worldHeight / 2,
    }),
    [graph.viewHeight, graph.viewWidth, graph.worldHeight, graph.worldWidth],
  );
  const fitCanvas = useCallback(() => {
    graphCanvasInteractedRef.current = true;
    const boundsWidth = Math.max(1, visibleBounds.maxX - visibleBounds.minX);
    const boundsHeight = Math.max(1, visibleBounds.maxY - visibleBounds.minY);
    const padding = 72;
    const availableWidth = Math.max(1, graph.viewWidth - padding * 2);
    const availableHeight = Math.max(1, graph.viewHeight - padding * 2);
    const scale = clampKnowledgeGraphZoom(
      Math.min(availableWidth / boundsWidth, availableHeight / boundsHeight),
    );
    setCanvasTransform({
      scale,
      x: (graph.viewWidth - boundsWidth * scale) / 2 - visibleBounds.minX * scale,
      y: (graph.viewHeight - boundsHeight * scale) / 2 - visibleBounds.minY * scale,
    });
  }, [graph.viewHeight, graph.viewWidth, visibleBounds]);
  const resetCanvas = useCallback((markInteracted = true) => {
    graphCanvasInteractedRef.current = markInteracted;
    setCanvasTransform(centerCanvasTransform());
  }, [centerCanvasTransform]);
  const zoomCanvasAt = useCallback(
    (factor: number, anchor?: { x: number; y: number }) => {
      graphCanvasInteractedRef.current = true;
      const anchorPoint = anchor ?? { x: graph.viewWidth / 2, y: graph.viewHeight / 2 };
      setCanvasTransform((previous) => {
        const scale = clampKnowledgeGraphZoom(previous.scale * factor);
        const worldX = (anchorPoint.x - previous.x) / previous.scale;
        const worldY = (anchorPoint.y - previous.y) / previous.scale;
        return {
          scale,
          x: anchorPoint.x - worldX * scale,
          y: anchorPoint.y - worldY * scale,
        };
      });
    },
    [graph.viewHeight, graph.viewWidth],
  );
  const handleCanvasWheel = useCallback(
    (event: WheelEvent<SVGSVGElement>) => {
      event.preventDefault();
      const rect = event.currentTarget.getBoundingClientRect();
      zoomCanvasAt(Math.exp(-event.deltaY * 0.001), {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    },
    [zoomCanvasAt],
  );
  const handleCanvasPointerDown = useCallback((event: PointerEvent<SVGSVGElement>) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest("[data-knowledge-graph-node]")) return;
    event.preventDefault();
    graphCanvasInteractedRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    panGestureRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: canvasTransform.x,
      startY: canvasTransform.y,
    };
    setIsPanning(true);
  }, [canvasTransform.x, canvasTransform.y]);
  const handleCanvasPointerMove = useCallback((event: PointerEvent<SVGSVGElement>) => {
    const gesture = panGestureRef.current;
    if (gesture.pointerId !== event.pointerId) return;
    const nextX = gesture.startX + event.clientX - gesture.startClientX;
    const nextY = gesture.startY + event.clientY - gesture.startClientY;
    setCanvasTransform((previous) => ({ ...previous, x: nextX, y: nextY }));
  }, []);
  const finishCanvasPan = useCallback((event: PointerEvent<SVGSVGElement>) => {
    if (panGestureRef.current.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    panGestureRef.current.pointerId = -1;
    setIsPanning(false);
  }, []);
  useEffect(() => {
    if (graphContentKeyRef.current === graphContentKey) return;
    graphContentKeyRef.current = graphContentKey;
    graphCanvasInteractedRef.current = false;
    setCanvasTransform(centerCanvasTransform());
  }, [centerCanvasTransform, graphContentKey]);
  useEffect(() => {
    if (graphCanvasInteractedRef.current) return;
    setCanvasTransform(centerCanvasTransform());
  }, [centerCanvasTransform]);
  const safeCanvasScale = Math.max(canvasTransform.scale, 0.001);
  const labelFontSize = 12 / safeCanvasScale;
  const labelStrokeWidth = 2.4 / safeCanvasScale;
  const labelMaxChars = canvasTransform.scale < 0.75 ? 44 : 35;

  return (
    <div className="flex h-full min-h-[520px] w-full max-w-none flex-col">
      <div className="mb-4 space-y-3">
        <div className="min-w-0">
          <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-slate-400">
            Knowledge Graph
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950 dark:text-slate-100">
            Notes Knowledge Graph
          </h1>
          <div className="mt-1 text-xs text-slate-500">
            {visible.nodes.length} nodes · {visible.links.length} edges
          </div>
        </div>
        <div className="flex flex-wrap items-start gap-3">
          <div className="max-w-full shrink-0 overflow-x-auto pb-1">
            <div className="flex w-max flex-nowrap items-center justify-start gap-2">
              <select
                value={section}
                onChange={(event) => setSection(event.target.value)}
                className="h-9 shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 text-xs outline-none transition focus:border-slate-400 dark:border-slate-800 dark:bg-slate-950"
              >
                <option value="">All categories</option>
                {graph.sections.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Filter nodes..."
                className="h-9 w-48 shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 text-xs outline-none transition placeholder:text-slate-400 focus:border-slate-400 dark:border-slate-800 dark:bg-slate-950"
              />
              <label className="inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={showLabels}
                  onChange={(event) => setShowLabels(event.target.checked)}
                />
                Labels
              </label>
              <label className="inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={showOrphans}
                  onChange={(event) => setShowOrphans(event.target.checked)}
                />
                Orphans
              </label>
            </div>
          </div>
          <div className="ml-auto min-w-[min(100%,34rem)] flex-1 rounded-xl border border-slate-200 bg-white p-2.5 text-xs dark:border-slate-800 dark:bg-slate-950">
            <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2">
              <div className="min-w-[180px] shrink-0">
                <div className="text-xs font-semibold text-slate-500 dark:text-slate-300">Details</div>
                {selectedNode ? (
                  <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="max-w-[22rem] truncate font-semibold text-slate-900 dark:text-slate-100">
                      {selectedNode.title}
                    </span>
                    <span className="text-slate-500">
                      {selectedNode.section} · {selectedNode.degree} connections
                    </span>
                    <button
                      type="button"
                      onClick={() => onSelect(selectedNode.note)}
                      className="h-7 rounded-lg bg-slate-950 px-2.5 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-950"
                    >
                      Open page
                    </button>
                  </div>
                ) : (
                  <div className="mt-1 text-slate-500">
                    {visible.nodes.length} visible · {visible.links.length} linked
                  </div>
                )}
              </div>
              <div className="min-w-0 flex-1 overflow-x-auto">
                <div className="flex w-max min-w-full flex-nowrap items-center justify-start gap-4">
                  {graph.sections.map((item) => (
                    <div key={item} className="flex shrink-0 items-center gap-2 whitespace-nowrap">
                      <span
                        className="h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{ background: KNOWLEDGE_GRAPH_COLORS[item] || "#78716c" }}
                      />
                      <span className="text-slate-600 dark:text-slate-300">{item}</span>
                      <span
                        title={`${sectionCounts.get(item) ?? 0} ${item} notes`}
                        aria-label={`${sectionCounts.get(item) ?? 0} ${item} notes`}
                        className="dan-session-count-flap"
                      >
                        {sectionCounts.get(item) ?? 0}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div
        ref={graphFrameRef}
        className="relative min-h-[520px] flex-1 overflow-hidden rounded-xl border border-slate-200 bg-[#fbfaf7] shadow-inner dark:border-slate-800 dark:bg-slate-950"
      >
        <div className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-lg border border-slate-200 bg-white/90 p-1 shadow-sm backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
          <button
            type="button"
            onClick={() => zoomCanvasAt(0.84)}
            title="Zoom out"
            aria-label="Zoom out"
            className="grid h-7 w-7 place-items-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-900"
          >
            <Minus size={13} />
          </button>
          <div className="min-w-10 text-center font-mono text-[10px] text-slate-500">
            {Math.round(canvasTransform.scale * 100)}%
          </div>
          <button
            type="button"
            onClick={() => zoomCanvasAt(1.19)}
            title="Zoom in"
            aria-label="Zoom in"
            className="grid h-7 w-7 place-items-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-900"
          >
            <Plus size={13} />
          </button>
          <button
            type="button"
            onClick={fitCanvas}
            title="Fit graph"
            aria-label="Fit graph"
            className="grid h-7 w-7 place-items-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-900"
          >
            <Maximize2 size={13} />
          </button>
          <button
            type="button"
            onClick={() => resetCanvas()}
            title="Reset canvas"
            aria-label="Reset canvas"
            className="grid h-7 w-7 place-items-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-900"
          >
            <RotateCcw size={13} />
          </button>
        </div>
        {visible.nodes.length === 0 ? (
          <div className="grid h-full place-items-center text-sm text-slate-400">
            No matching nodes
          </div>
        ) : (
          <svg
            viewBox={`0 0 ${graph.viewWidth} ${graph.viewHeight}`}
            role="img"
            className={cx(
              "h-full min-h-[520px] w-full touch-none",
              isPanning ? "cursor-grabbing" : "cursor-grab",
            )}
            onWheel={handleCanvasWheel}
            onPointerDown={handleCanvasPointerDown}
            onPointerMove={handleCanvasPointerMove}
            onPointerUp={finishCanvasPan}
            onPointerCancel={finishCanvasPan}
          >
            <defs>
              <marker
                id="dan-graph-arrow"
                viewBox="0 -4 8 8"
                refX="18"
                refY="0"
                markerWidth="5"
                markerHeight="5"
                orient="auto"
              >
                <path d="M0,-3L7,0L0,3" fill="#c8c5c0" />
              </marker>
            </defs>
            <g transform={`translate(${canvasTransform.x} ${canvasTransform.y}) scale(${canvasTransform.scale})`}>
              <g>
                {visible.links.map((link) => {
                  const source = nodeById.get(link.source);
                  const target = nodeById.get(link.target);
                  if (!source || !target) return null;
                  const active =
                    !neighborIds || neighborIds.has(source.id) || neighborIds.has(target.id);
                  return (
                    <line
                      key={`${link.source}->${link.target}`}
                      x1={source.x}
                      y1={source.y}
                      x2={target.x}
                      y2={target.y}
                      stroke="#d6d3d1"
                      strokeWidth={1}
                      strokeOpacity={active ? 0.58 : 0.05}
                      markerEnd="url(#dan-graph-arrow)"
                    />
                  );
                })}
              </g>
              <g>
                {visible.nodes.map((node) => {
                  const active = !neighborIds || neighborIds.has(node.id);
                  const color = KNOWLEDGE_GRAPH_COLORS[node.section] || "#78716c";
                  return (
                    <g
                      key={node.id}
                      data-knowledge-graph-node="true"
                      transform={`translate(${node.x} ${node.y})`}
                      opacity={active ? 1 : 0.14}
                      className="cursor-pointer"
                      onMouseEnter={() => setHoveredId(node.id)}
                      onMouseLeave={() => setHoveredId(null)}
                      onClick={() => onSelect(node.note)}
                    >
                      <circle r={node.radius} fill={color} stroke="#ffffff" strokeWidth={1.6} />
                      {showLabels && (
                        <text
                          x={node.radius + 5}
                          y={labelFontSize * 0.34}
                          fontSize={labelFontSize}
                          fill="#44403c"
                          stroke="#fbfaf7"
                          strokeLinejoin="round"
                          strokeWidth={labelStrokeWidth}
                          paintOrder="stroke"
                          fontWeight={500}
                          className="select-none"
                        >
                          {node.title.length > labelMaxChars
                            ? `${node.title.slice(0, labelMaxChars - 3)}...`
                            : node.title}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            </g>
          </svg>
        )}
      </div>
    </div>
  );
}

function BlueprintNodePreview({
  node,
  tasks,
  events,
  activeTask,
}: {
  node: BlueprintNode;
  tasks: ChatV2TaskSnapshot[];
  events: ChatV2AgentRunEvent[];
  activeTask: ChatV2TaskSnapshot | null;
}) {
  const liveStatus = blueprintLiveStatus(node, tasks, events, activeTask);
  const sharedEvidence = sharedEvidenceItemsForNode(node, tasks, events, activeTask);
  if (node.kind === "request" && node.rawRequest) {
    const requestDetails = (node.previewBody || "").trim();
    return (
      <div className="space-y-4">
        <LiveStatusCard status={liveStatus} />
        <SharedEvidencePreview items={sharedEvidence} />
        <section className="rounded-md border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-800 dark:bg-slate-900/60">
          <div className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-800 dark:text-slate-200">
            {node.rawRequest}
          </div>
        </section>
        {requestDetails && (
          <MarkdownRenderer content={previewMarkdownContent(requestDetails)} autoHighlightCode={false} />
        )}
      </div>
    );
  }

  if (node.kind === "plan" && planTaskChecklistItems(node).length > 0) {
    return (
      <div className="space-y-4">
        <LiveStatusCard status={liveStatus} />
        <SharedEvidencePreview items={sharedEvidence} />
        <PlanChecklistPreview node={node} events={events} />
        <MarkdownRenderer
          content={previewMarkdownContent(node.previewBody || node.body)}
          autoHighlightCode={false}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <LiveStatusCard status={liveStatus} />
      <SharedEvidencePreview items={sharedEvidence} />
      <MarkdownRenderer
        content={previewMarkdownContent(node.previewBody || node.body)}
        autoHighlightCode={false}
      />
    </div>
  );
}

function strippedStepTitle(title: string) {
  return title.replace(/^\d+\.\s*/, "").trim();
}

function workPlanHeaderSubtitle(node: BlueprintNode | null, fallbackTitle?: string | null) {
  if (!node) {
    const fallback = fallbackTitle && !threadTitleLooksPlaceholder(fallbackTitle) ? fallbackTitle.trim() : "";
    return fallback || "Waiting for a request";
  }
  if (node.kind === "request") return "Request captured";
  if (node.kind === "understanding") {
    return node.status === "done" ? "Request understood" : "Understanding request";
  }
  if (node.kind === "plan") {
    if (node.graphTaskId) {
      return `${node.status === "active" ? "Current" : "Selected"} plan: ${strippedStepTitle(node.title)}`;
    }
    return node.status === "done" ? "Steps planned" : "Planning next steps";
  }
  if (node.kind === "validation") {
    return node.status === "done" ? "Checks complete" : "Checking the result";
  }
  if (node.kind === "answer") {
    if (node.status === "blocked") return node.detail || "Final answer needs attention";
    return node.status === "done" ? "Final response ready" : "Preparing final response";
  }
  if (node.kind === "repair") return "Repairing a step";
  if (node.kind === "queue") return "Waiting to run";
  if (node.kind === "build") {
    if (node.status === "blocked") return "Work needs attention";
    if (node.status === "done") return "Work completed";
    return "Working on the current step";
  }
  if (node.kind === "task" || node.kind === "worktree") {
    return `${node.status === "active" ? "Current" : "Selected"} step: ${strippedStepTitle(node.title)}`;
  }
  return strippedStepTitle(node.title) || "Work step selected";
}

function treeDisplayTitle(node: BlueprintNode) {
  if (node.kind === "plan") return node.graphTaskId ? strippedStepTitle(node.title) : "Plan";
  if (node.kind === "validation") return "Validation";
  if (node.kind === "repair") return "Repair / retry";
  if (node.kind === "answer") return "Final response";
  return strippedStepTitle(node.title) || node.title;
}

function primaryNodeByKind(nodes: BlueprintNode[], kind: BlueprintNodeKind) {
  const matching = nodes.filter((node) => node.kind === kind);
  return (
    matching.find((node) => node.status === "active" || node.status === "ready") ??
    [...matching].reverse().find((node) => node.status === "done") ??
    matching[0] ??
    null
  );
}

function attachTreeParent(
  parents: Map<string, string>,
  child: BlueprintNode | null,
  parent: BlueprintNode | null,
) {
  if (!child || !parent || child.id === parent.id || parents.has(child.id)) return;
  let cursor = parent.id;
  while (cursor) {
    if (cursor === child.id) return;
    cursor = parents.get(cursor) || "";
  }
  parents.set(child.id, parent.id);
}

function buildLiveTaskTree(nodes: BlueprintNode[]): LiveTaskTreeItem[] {
  const byGraphTaskId = new Map<string, BlueprintNode>();
  for (const node of nodes) {
    if ((node.kind === "task" || node.kind === "worktree") && node.graphTaskId) {
      byGraphTaskId.set(node.graphTaskId, node);
    }
  }

  const parents = new Map<string, string>();
  const taskNodes = nodes.filter((node) => node.kind === "task" || node.kind === "worktree");

  for (const node of taskNodes) {
    const explicitParent = node.parentGraphTaskId
      ? byGraphTaskId.get(node.parentGraphTaskId) ?? null
      : null;
    attachTreeParent(parents, node, explicitParent);
  }

  const nodeById = new Map(taskNodes.map((node) => [node.id, node]));
  const childrenByParent = new Map<string, BlueprintNode[]>();
  const roots: BlueprintNode[] = [];
  for (const node of taskNodes) {
    const parentId = parents.get(node.id);
    if (parentId && nodeById.has(parentId)) {
      const children = childrenByParent.get(parentId) ?? [];
      children.push(node);
      childrenByParent.set(parentId, children);
    } else {
      roots.push(node);
    }
  }

  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const sortByOriginalOrder = (items: BlueprintNode[]) =>
    [...items].sort((a, b) => (indexById.get(a.id) ?? 0) - (indexById.get(b.id) ?? 0));
  const buildItem = (node: BlueprintNode): LiveTaskTreeItem => ({
    node,
    children: sortByOriginalOrder(childrenByParent.get(node.id) ?? []).map(buildItem),
  });
  return sortByOriginalOrder(roots).map(buildItem);
}

function statusForPlanTask(task: BlueprintPlanTask, context: BlueprintPlanContext): BlueprintNodeStatus {
  const state = (task.state || task.status || "").toLowerCase();
  if (["failed", "blocked", "stopped"].includes(state)) return "blocked";
  if (
    context.completedTaskIds.includes(task.taskId) ||
    ["done", "complete", "completed", "x"].includes(state)
  ) {
    return "done";
  }
  if (
    context.activeTaskIds.includes(task.taskId) ||
    ["active", "generating", "planning", "executing", "running", "validating", "repairing"].includes(state)
  ) {
    return "active";
  }
  if (context.readyTaskIds.includes(task.taskId) || state === "ready") return "ready";
  if (
    context.deferredTaskIds.includes(task.taskId) ||
    ["deferred", "future", "planned", "projected", "generated", "blueprint", "waiting"].includes(state)
  ) {
    return "future";
  }
  return "queued";
}

function blueprintNodeFromPlanTask(
  task: BlueprintPlanTask,
  context: BlueprintPlanContext,
): BlueprintNode {
  const kind = graphTaskKind(task, context);
  const meta = uniqueStringList([
    isBlueprintPlanTaskPlan(task) ? "plan" : "",
    context.canonicalBlueprint ? kind.replace(/_/g, " ") : "",
    task.topologyRole ? task.topologyRole.replace(/_/g, " ") : "",
    task.branchId ? `branch ${task.branchId}` : "",
    task.dependsOn.length > 0 ? `after ${task.dependsOn.join(", ")}` : "",
    kind === "worktree" ? "parallel lane" : "",
  ]).join(" · ");
  return {
    id: graphTaskNodeId(task),
    title: `${task.taskId}. ${task.goal}`,
    detail: compactTaskDetail(task),
    meta,
    body: taskBody(task, context),
    previewBody: taskBody(task, context),
    status: statusForPlanTask(task, context),
    kind,
    compact: context.deferredTaskIds.includes(task.taskId),
    depth: task.parentId ? 1 : 0,
    dependencyIds: task.dependsOn,
    graphTaskId: task.taskId,
    parentGraphTaskId: task.parentId,
    branchId: task.branchId,
    graphContext: context,
  };
}

function liveTaskBranchesForNodes(nodes: BlueprintNode[]) {
  const groups = new Map<string, BlueprintNode[]>();
  for (const node of nodes) {
    const branchId = node.branchId || "main";
    const group = groups.get(branchId) ?? [];
    group.push(node);
    groups.set(branchId, group);
  }
  return [...groups.entries()].map(([branchId, branchNodes]) => ({
    id: branchId,
    label: liveTaskBranchLabel(branchId),
    nodes: branchNodes,
  }));
}

function liveTaskBranchLabel(branchId: string) {
  if (branchId === "main") return "Main path";
  if (/^\d+(?:[-.]\d+)*$/.test(branchId)) return `Branch ${branchId}`;
  return branchId
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function graphRevisionLabel(context: BlueprintPlanContext, index: number) {
  return (
    context.graphVersionId ||
    (context.graphRevision !== null ? `r${context.graphRevision}` : `Graph ${index + 1}`)
  );
}

function graphRevisionMeta(context: BlueprintPlanContext) {
  return uniqueStringList([
    context.graphSource,
    context.graphUpdateScope,
    context.graphChangedBranchIds.length
      ? `branches ${context.graphChangedBranchIds.join(", ")}`
      : "",
    context.graphChangedTaskIds.length ? `changed ${context.graphChangedTaskIds.join(", ")}` : "",
  ]).join(" · ");
}

function buildLiveTaskGraphRevisions(nodes: BlueprintNode[]): LiveTaskGraphRevision[] {
  const planNode =
    nodes.find((node) => node.kind === "plan" && !node.graphTaskId && node.graphHistory?.length) ??
    primaryNodeByKind(nodes, "plan");
  const history = planNode?.graphHistory?.filter((context) => context.taskGraph.length > 0) ?? [];
  if (history.length > 0) {
    return history.map((context, index) => {
      const revisionNodes = context.taskGraph.map((task) => blueprintNodeFromPlanTask(task, context));
      const branches = liveTaskBranchesForNodes(revisionNodes);
      return {
        id: graphContextIdentity(context, index),
        label: graphRevisionLabel(context, index),
        meta: graphRevisionMeta(context),
        reason: context.graphUpdateReason || context.planRootRelative || "",
        branches,
        planEdges:
          context.canonicalBlueprint && context.blueprintEdges?.length
            ? context.blueprintEdges.map((edge) => ({
                ...edge,
                from: `blueprint:task:${edge.from}`,
                to: `blueprint:task:${edge.to}`,
              }))
            : planDependencyEdgesForNodes(revisionNodes),
      };
    });
  }
  const taskNodes = nodes.filter(
    (node) => (node.kind === "task" || node.kind === "worktree") && node.graphTaskId,
  );
  if (taskNodes.length === 0) return [];
  return [
    {
      id: "current",
      label: "Current",
      meta: "",
      reason: "",
      branches: liveTaskBranchesForNodes(taskNodes),
      planEdges: planDependencyEdgesForNodes(taskNodes),
    },
  ];
}

function uniquePlanNodesForRevision(revision: LiveTaskGraphRevision) {
  const canonical = revision.branches.some((branch) =>
    branch.nodes.some((node) => node.graphContext?.canonicalBlueprint),
  );
  const byId = new Map<string, BlueprintNode>();
  for (const branch of revision.branches) {
    for (const node of branch.nodes) {
      if (!node.graphTaskId || (!canonical && node.kind !== "plan")) continue;
      if (!byId.has(node.id)) byId.set(node.id, node);
    }
  }
  return [...byId.values()];
}

function planDependencyEdgesForNodes(nodes: BlueprintNode[]): LiveTaskGraphEdge[] {
  const includeCanonicalNodes = nodes.some((node) => node.graphContext?.canonicalBlueprint);
  const planIds = new Set(
    nodes
      .filter(
        (node) => node.graphTaskId && (includeCanonicalNodes || node.kind === "plan"),
      )
      .map((node) => node.graphTaskId as string),
  );
  const nodeIdByTaskId = new Map(
    nodes
      .filter(
        (node) => node.graphTaskId && (includeCanonicalNodes || node.kind === "plan"),
      )
      .map((node) => [node.graphTaskId as string, node.id] as const),
  );
  const edges: LiveTaskGraphEdge[] = [];
  const seen = new Set<string>();
  for (const node of nodes) {
    if (!node.graphTaskId || (!includeCanonicalNodes && node.kind !== "plan")) continue;
    for (const dependency of node.dependencyIds ?? []) {
      if (!planIds.has(dependency)) continue;
      const from = nodeIdByTaskId.get(dependency);
      if (!from) continue;
      const key = `${from}->${node.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({ from, to: node.id });
    }
  }
  return edges;
}

function liveTaskTreeSnapshot(items: LiveTaskTreeItem[]): LiveTaskTreeSnapshot[] {
  return items.map((item) => ({
    id: item.node.id,
    title: treeDisplayTitle(item.node),
    status: item.node.status,
    children: liveTaskTreeSnapshot(item.children),
  }));
}

function treeNodeMeta(node: BlueprintNode) {
  const bits = uniqueStringList([
    node.graphContext?.canonicalBlueprint && !["task", "plan"].includes(node.kind)
      ? node.kind.replace(/_/g, " ")
      : "",
    node.branchId ? `branch ${node.branchId}` : "",
    node.dependencyIds?.length ? `after ${node.dependencyIds.join(", ")}` : "",
    node.kind === "worktree" ? "parallel lane" : "",
    node.status,
  ]);
  return bits.join(" · ");
}

function planGraphLayout(nodes: BlueprintNode[], edges: LiveTaskGraphEdge[]) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const incoming = new Map<string, string[]>();
  for (const edge of edges) {
    if (!byId.has(edge.from) || !byId.has(edge.to)) continue;
    const deps = incoming.get(edge.to) ?? [];
    deps.push(edge.from);
    incoming.set(edge.to, deps);
  }
  const levels = new Map<string, number>();
  const levelFor = (nodeId: string, stack = new Set<string>()): number => {
    if (levels.has(nodeId)) return levels.get(nodeId) ?? 0;
    if (stack.has(nodeId)) return 0;
    stack.add(nodeId);
    const deps = incoming.get(nodeId) ?? [];
    const level = deps.length ? Math.max(...deps.map((depId) => levelFor(depId, stack) + 1)) : 0;
    stack.delete(nodeId);
    levels.set(nodeId, level);
    return level;
  };
  for (const node of nodes) levelFor(node.id);
  const grouped = new Map<number, BlueprintNode[]>();
  for (const node of nodes) {
    const level = levels.get(node.id) ?? 0;
    const group = grouped.get(level) ?? [];
    group.push(node);
    grouped.set(level, group);
  }
  const cardWidth = 178;
  const cardHeight = 78;
  const columnGap = 74;
  const rowGap = 24;
  const padding = 12;
  const positions = new Map<string, { x: number; y: number; node: BlueprintNode }>();
  const maxLevel = Math.max(0, ...[...grouped.keys()]);
  let maxRows = 1;
  for (const [level, group] of grouped.entries()) {
    maxRows = Math.max(maxRows, group.length);
    group.forEach((node, rowIndex) => {
      positions.set(node.id, {
        node,
        x: padding + level * (cardWidth + columnGap),
        y: padding + rowIndex * (cardHeight + rowGap),
      });
    });
  }
  return {
    positions,
    width: padding * 2 + (maxLevel + 1) * cardWidth + maxLevel * columnGap,
    height: padding * 2 + maxRows * cardHeight + Math.max(0, maxRows - 1) * rowGap,
    cardWidth,
    cardHeight,
  };
}

function PlanDependencyGraph({
  revision,
  currentNodes,
  latestRevisionId,
  activeNodeId,
  selectedNodeId,
  onSelect,
}: {
  revision: LiveTaskGraphRevision;
  currentNodes: BlueprintNode[];
  latestRevisionId: string | null;
  activeNodeId: string | null;
  selectedNodeId: string | null;
  onSelect: (node: BlueprintNode) => void;
}) {
  const planNodes = uniquePlanNodesForRevision(revision);
  const currentById = new Map(currentNodes.map((node) => [node.id, node]));
  const layout = planGraphLayout(planNodes, revision.planEdges);
  const markerId = `plan-arrow-${revision.id.replace(/[^a-z0-9_-]+/gi, "-") || "current"}`;
  const canSelectLatest = revision.id === latestRevisionId;

  return (
    <div
      className="relative"
      style={{ width: layout.width, height: layout.height }}
      aria-label="Plan dependency graph"
    >
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full overflow-visible"
        width={layout.width}
        height={layout.height}
        aria-hidden="true"
      >
        <defs>
          <marker
            id={markerId}
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-slate-300 dark:fill-slate-600" />
          </marker>
        </defs>
        {revision.planEdges.map((edge) => {
          const from = layout.positions.get(edge.from);
          const to = layout.positions.get(edge.to);
          if (!from || !to) return null;
          const x1 = from.x + layout.cardWidth;
          const y1 = from.y + layout.cardHeight / 2;
          const x2 = to.x;
          const y2 = to.y + layout.cardHeight / 2;
          const mid = x1 + Math.max(24, (x2 - x1) / 2);
          return (
            <path
              key={`${edge.from}:${edge.kind || "dependency"}->${edge.to}`}
              data-edge-kind={edge.kind || "dependency"}
              d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2 - 6} ${y2}`}
              className={cx(
                "fill-none",
                edge.kind === "feedback"
                  ? "stroke-amber-500 dark:stroke-amber-400"
                  : edge.kind === "validates"
                    ? "stroke-cyan-500 dark:stroke-cyan-400"
                    : edge.kind === "alternative"
                      ? "stroke-violet-400 dark:stroke-violet-500"
                      : "stroke-slate-300 dark:stroke-slate-600",
              )}
              strokeWidth="1.5"
              strokeDasharray={
                edge.kind === "feedback" || edge.kind === "alternative" ? "5 4" : undefined
              }
              markerEnd={`url(#${markerId})`}
            >
              <title>
                {[edge.kind || "dependency", edge.condition].filter(Boolean).join(": ")}
              </title>
            </path>
          );
        })}
      </svg>
      {[...layout.positions.values()].map(({ node, x, y }) => {
        const tone = blueprintStatusTone(node.status);
        const Icon = blueprintKindIcon(node.kind);
        const active = node.id === activeNodeId;
        const selected = node.id === selectedNodeId;
        const currentNode = currentById.get(node.id) ?? node;
        const disabled = !canSelectLatest || !currentById.has(node.id);
        return (
          <button
            key={node.id}
            type="button"
            data-status={node.status}
            data-current={active ? "true" : undefined}
            disabled={disabled}
            onClick={() => {
              if (!disabled) onSelect(currentNode);
            }}
            className={cx(
              "dan-plan-graph-node absolute flex min-w-0 flex-col rounded-md border px-3 py-2 text-left text-xs outline-none transition focus-visible:ring-2 focus-visible:ring-slate-300 dark:focus-visible:ring-slate-600",
              tone.node,
              active &&
                "ring-2 ring-cyan-300 ring-offset-1 ring-offset-white dark:ring-cyan-500 dark:ring-offset-slate-950",
              selected && !active && "ring-1 ring-slate-300 dark:ring-slate-600",
              disabled && "cursor-default opacity-85",
            )}
            style={{ left: x, top: y, width: layout.cardWidth, height: layout.cardHeight }}
          >
            <span className="mb-1 flex min-w-0 items-center gap-2">
              <span className={cx("grid h-6 w-6 shrink-0 place-items-center rounded-full border", tone.marker)}>
                {node.status === "active" ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : node.status === "future" ? (
                  <Circle size={9} />
                ) : (
                  <Icon size={12} />
                )}
              </span>
              <span className="min-w-0 flex-1 truncate font-semibold text-slate-800 dark:text-slate-100">
                {treeDisplayTitle(node)}
              </span>
            </span>
            <span className="line-clamp-2 text-[10px] leading-4 text-slate-500 dark:text-slate-400">
              {treeNodeMeta(node)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function canonicalBlueprintContext(nodes: BlueprintNode[]) {
  return (
    nodes.find(
      (node) => node.kind === "plan" && !node.graphTaskId && node.graphContext?.canonicalBlueprint,
    )?.graphContext ??
    nodes.find((node) => node.graphContext?.canonicalBlueprint)?.graphContext ??
    null
  );
}

function taskFamilyIcon(family: TaskFamily) {
  if (family === "direct") return <ArrowUp size={11} />;
  if (family === "debugging") return <RotateCcw size={11} />;
  if (family === "research") return <ScrollText size={11} />;
  if (family === "design") return <WandSparkles size={11} />;
  if (family === "meeting") return <MessageSquareText size={11} />;
  if (family === "manufacturing") return <TerminalSquare size={11} />;
  return <Cable size={11} />;
}

function taskFamilyTone(family: TaskFamily) {
  if (family === "debugging") {
    return "border-orange-300 bg-orange-50/70 text-orange-900 dark:border-orange-800 dark:bg-orange-950/25 dark:text-orange-100";
  }
  if (family === "research") {
    return "border-blue-300 bg-blue-50/70 text-blue-900 dark:border-blue-800 dark:bg-blue-950/25 dark:text-blue-100";
  }
  if (family === "design") {
    return "border-fuchsia-300 bg-fuchsia-50/60 text-fuchsia-900 dark:border-fuchsia-800 dark:bg-fuchsia-950/20 dark:text-fuchsia-100";
  }
  if (family === "meeting") {
    return "border-emerald-300 bg-emerald-50/65 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-100";
  }
  if (family === "manufacturing") {
    return "border-amber-300 bg-amber-50/70 text-amber-950 dark:border-amber-700 dark:bg-amber-950/25 dark:text-amber-100";
  }
  return "border-cyan-300 bg-cyan-50/65 text-cyan-900 dark:border-cyan-800 dark:bg-cyan-950/20 dark:text-cyan-100";
}

function BlueprintContractPlate({ context }: { context: BlueprintPlanContext }) {
  const family = context.taskFamily ?? "general";
  const presentation = taskFamilyPresentation(family);
  const contract = context.contract;
  const facets = [
    { label: "Non-goals", items: contract?.nonGoals ?? [] },
    { label: "Constraints", items: contract?.constraints ?? [] },
    { label: "Permissions", items: contract?.permissions ?? [] },
    { label: "Risks", items: contract?.risks ?? [] },
    { label: "Budget", items: contract?.budget ?? [] },
    { label: "Done when", items: contract?.acceptanceCriteria ?? [] },
  ].filter((facet) => facet.items.length > 0);
  const edgeKindCounts = new Map<string, number>();
  for (const edge of context.blueprintEdges ?? []) {
    const kind = edge.kind || "dependency";
    edgeKindCounts.set(kind, (edgeKindCounts.get(kind) ?? 0) + 1);
  }
  return (
    <div
      className="dan-task-blueprint-contract rounded border border-slate-200/80 bg-[linear-gradient(135deg,rgba(255,255,255,0.92),rgba(248,250,252,0.68))] p-3 shadow-[inset_3px_0_0_rgba(245,158,11,0.65)] dark:border-slate-800 dark:bg-[linear-gradient(135deg,rgba(15,23,42,0.88),rgba(2,6,23,0.62))]"
      data-task-family={family}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cx(
                "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.11em]",
                taskFamilyTone(family),
              )}
            >
              {taskFamilyIcon(family)}
              {presentation.label}
            </span>
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
              {presentation.lens}
            </span>
          </div>
          <div className="mt-2 text-sm font-semibold leading-5 text-slate-900 dark:text-slate-100">
            {contract?.goal || "Goal carried by the current blueprint revision"}
          </div>
          <div className="mt-1 text-[11px] leading-4 text-slate-500 dark:text-slate-400">
            {presentation.blueprintHint}
          </div>
        </div>
        <div className="shrink-0 text-right font-mono text-[10px] text-slate-400">
          {context.blueprintRevisionId ||
            (context.graphRevision !== null ? `r${context.graphRevision}` : "current")}
        </div>
      </div>
      {(facets.length > 0 ||
        Boolean(context.boundedLoopNodeIds?.length) ||
        Boolean(context.uncoveredCriterionIds?.length)) && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {facets.map((facet) => (
            <span
              key={facet.label}
              title={facet.items.join("\n")}
              className="rounded border border-slate-200 bg-white/70 px-2 py-1 text-[10px] text-slate-600 dark:border-slate-800 dark:bg-slate-950/55 dark:text-slate-300"
            >
              <span className="font-semibold uppercase tracking-[0.08em] text-slate-400">
                {facet.label}
              </span>{" "}
              <span className="font-mono">{facet.items.length}</span>
            </span>
          ))}
          {Boolean(context.boundedLoopNodeIds?.length) && (
            <span className="rounded border border-amber-200 bg-amber-50/70 px-2 py-1 text-[10px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/25 dark:text-amber-100">
              {context.boundedLoopNodeIds?.length} bounded loop
              {context.boundedLoopNodeIds?.length === 1 ? "" : "s"}
            </span>
          )}
          {Boolean(context.uncoveredCriterionIds?.length) && (
            <span
              title={context.uncoveredCriterionIds?.join("\n")}
              className="rounded border border-rose-200 bg-rose-50/70 px-2 py-1 text-[10px] font-semibold text-rose-800 dark:border-rose-800 dark:bg-rose-950/25 dark:text-rose-100"
            >
              {context.uncoveredCriterionIds?.length} criterion gap
              {context.uncoveredCriterionIds?.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
      )}
      {edgeKindCounts.size > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-slate-200/70 pt-2 text-[10px] dark:border-slate-800">
          <span className="font-semibold uppercase tracking-[0.1em] text-slate-400">Topology</span>
          {[...edgeKindCounts.entries()].map(([kind, count]) => (
            <span
              key={kind}
              className={cx(
                "rounded-full border px-1.5 py-0.5 font-medium",
                kind === "feedback"
                  ? "border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200"
                  : kind === "alternative"
                    ? "border-violet-300 text-violet-800 dark:border-violet-800 dark:text-violet-200"
                    : kind === "validates"
                      ? "border-cyan-300 text-cyan-800 dark:border-cyan-800 dark:text-cyan-200"
                      : "border-slate-200 text-slate-500 dark:border-slate-800 dark:text-slate-400",
              )}
            >
              {kind.replace(/_/g, " ")} {count}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function BlueprintTreeOverview({
  nodes,
  activeNodeId,
  selectedNodeId,
  onSelect,
}: {
  nodes: BlueprintNode[];
  activeNodeId: string | null;
  selectedNodeId: string | null;
  onSelect: (node: BlueprintNode) => void;
}) {
  const revisions = useMemo(() => buildLiveTaskGraphRevisions(nodes), [nodes]);
  const [selectedRevisionId, setSelectedRevisionId] = useState<string>("");
  const latestRevision = revisions[revisions.length - 1] ?? null;
  const selectedRevision =
    revisions.find((revision) => revision.id === selectedRevisionId) ?? latestRevision;
  const selectedPlanNodes = selectedRevision ? uniquePlanNodesForRevision(selectedRevision) : [];
  const blueprintContext = useMemo(() => canonicalBlueprintContext(nodes), [nodes]);

  useEffect(() => {
    if (revisions.length === 0) {
      if (selectedRevisionId) setSelectedRevisionId("");
      return;
    }
    if (!selectedRevisionId || !revisions.some((revision) => revision.id === selectedRevisionId)) {
      setSelectedRevisionId(revisions[revisions.length - 1]?.id ?? "");
    }
  }, [revisions, selectedRevisionId]);

  const renderGraphNode = (node: BlueprintNode) => {
    const tone = blueprintStatusTone(node.status);
    const Icon = blueprintKindIcon(node.kind);
    const active = node.id === activeNodeId;
    const selected = node.id === selectedNodeId;
    const showingLatestRevision = selectedRevision?.id === latestRevision?.id;
    const canSelect =
      Boolean(showingLatestRevision) && nodes.some((candidate) => candidate.id === node.id);
    const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      if (!canSelect) return;
      onSelect(node);
    };

    return (
      <li key={node.id} className="min-w-0">
        <button
          type="button"
          data-status={node.status}
          data-current={active ? "true" : undefined}
          aria-current={active ? "step" : undefined}
          onClick={() => {
            if (canSelect) onSelect(node);
          }}
          onKeyDown={handleKeyDown}
          disabled={!canSelect}
          className={cx(
            "dan-live-task-tree-node flex w-full min-w-0 items-center gap-2 rounded-md border px-2.5 py-2 text-left text-xs outline-none transition focus-visible:ring-2 focus-visible:ring-slate-300 dark:focus-visible:ring-slate-600",
            tone.node,
            active &&
              "ring-2 ring-cyan-300 ring-offset-1 ring-offset-white dark:ring-cyan-500 dark:ring-offset-slate-950",
            selected && !active && "ring-1 ring-slate-300 dark:ring-slate-600",
            !canSelect && "cursor-default opacity-85",
          )}
        >
          <span
            className={cx(
              "grid h-6 w-6 shrink-0 place-items-center rounded-full border",
              tone.marker,
            )}
          >
            {node.status === "active" ? (
              <Loader2 size={12} className="animate-spin" />
            ) : node.status === "future" ? (
              <Circle size={9} />
            ) : (
              <Icon size={12} />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-semibold leading-4 text-slate-800 dark:text-slate-100">
              {treeDisplayTitle(node)}
            </span>
            <span className="mt-0.5 block truncate text-[10px] leading-3 text-slate-500 dark:text-slate-400">
              {treeNodeMeta(node)}
            </span>
          </span>
        </button>
      </li>
    );
  };

  return (
    <section className="mb-4" aria-label="Task blueprint">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
          <Cable size={13} />
          Task Blueprint
        </div>
        {revisions.length > 1 && (
          <div className="flex max-w-full flex-wrap items-center gap-1 text-[10px]">
            {revisions.map((revision) => {
              const active = revision.id === selectedRevision?.id;
              return (
                <button
                  type="button"
                  key={revision.id}
                  onClick={() => setSelectedRevisionId(revision.id)}
                  className={cx(
                    "rounded-full border px-2 py-0.5 font-semibold transition",
                    active
                      ? "border-amber-400 bg-amber-100 text-amber-950 dark:border-amber-500/80 dark:bg-amber-950/40 dark:text-amber-100"
                      : "border-slate-200 bg-white/70 text-slate-500 hover:border-slate-300 dark:border-slate-800 dark:bg-slate-950/50 dark:text-slate-400",
                  )}
                >
                  {revision.label}
                </button>
              );
            })}
          </div>
        )}
      </div>
      <div className="overflow-x-auto rounded-md border border-slate-200/80 bg-white/55 p-3 dark:border-slate-800 dark:bg-slate-950/45">
        {blueprintContext && (
          <div className="mb-3 min-w-[360px]">
            <BlueprintContractPlate context={blueprintContext} />
          </div>
        )}
        {selectedRevision ? (
          <div className="min-w-[360px] space-y-3">
            {(selectedRevision.meta || selectedRevision.reason) && (
              <div className="rounded border border-slate-200/70 bg-white/55 px-2.5 py-2 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-950/35 dark:text-slate-300">
                {selectedRevision.meta && (
                  <div className="font-medium">{selectedRevision.meta}</div>
                )}
                {selectedRevision.reason && (
                  <div className={cx("leading-5", selectedRevision.meta && "mt-1")}>
                    {selectedRevision.reason}
                  </div>
                )}
              </div>
            )}
            {selectedPlanNodes.length > 0 ? (
              <PlanDependencyGraph
                revision={selectedRevision}
                currentNodes={nodes}
                latestRevisionId={latestRevision?.id ?? null}
                activeNodeId={activeNodeId}
                selectedNodeId={selectedNodeId}
                onSelect={onSelect}
              />
            ) : (
              <div className="grid gap-2">
                {selectedRevision.branches.map((branch) => (
                  <div key={branch.id} className="min-w-0">
                    <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                      <GitBranchIcon />
                      <span className="truncate">{branch.label}</span>
                      <span className="rounded-full border border-slate-200 px-1.5 py-0.5 text-[9px] normal-case tracking-normal text-slate-500 dark:border-slate-800 dark:text-slate-400">
                        {branch.nodes.length} task{branch.nodes.length === 1 ? "" : "s"}
                      </span>
                    </div>
                    <ul className="grid min-w-0 gap-1.5">
                      {branch.nodes.map(renderGraphNode)}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="min-w-[360px] rounded border border-dashed border-slate-200/80 bg-white/45 p-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-950/30 dark:text-slate-400">
            Waiting for DAN to emit task nodes or branches. The execution attempt below shows live activity meanwhile.
          </div>
        )}
      </div>
    </section>
  );
}

interface BlueprintLiveStatus {
  status: string;
  now: string;
  latestUpdate: string;
  recentUpdates: string[];
  results: string[];
}

interface WorkElapsedCounter {
  value: string;
  active: boolean;
}

function displayStatusLabel(status: BlueprintNodeStatus) {
  if (status === "active") return "In progress";
  if (status === "done") return "Done";
  if (status === "ready") return "Ready";
  if (status === "future") return "Waiting";
  if (status === "queued") return "Queued";
  if (status === "blocked") return "Needs attention";
  return status;
}

function stepNowLabel(node: BlueprintNode, activeTask: ChatV2TaskSnapshot | null) {
  const activeMatchesNode =
    activeTask &&
    ((!node.runId && !node.taskId) ||
      (node.runId && taskRunId(activeTask) === node.runId) ||
      (node.taskId && activeTask.task_id === node.taskId));
  if (activeMatchesNode) return taskProgressLabel(activeTask);
  if (node.status === "done") return "This step is complete.";
  if (node.status === "blocked") return node.detail || "This step needs attention.";
  if (node.status === "ready") return "Ready to run when reached.";
  if (node.status === "future") return "Waiting for earlier steps.";
  if (node.status === "queued") return "Waiting in the queue.";
  if (node.status === "active") return node.detail || "Working on this step.";
  return workPlanHeaderSubtitle(node);
}

function eventMatchesNode(event: ChatV2AgentRunEvent, node: BlueprintNode) {
  if (node.runId && event.run_id === node.runId) return true;
  if (node.taskId && event.task_id === node.taskId) return true;
  return false;
}

function taskMatchesNode(task: ChatV2TaskSnapshot, node: BlueprintNode) {
  if (node.runId && taskRunId(task) === node.runId) return true;
  if (node.taskId && task.task_id === node.taskId) return true;
  return false;
}

function blueprintLiveStatus(
  node: BlueprintNode,
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
  activeTask: ChatV2TaskSnapshot | null,
): BlueprintLiveStatus {
  const relatedEvents = events.filter((event) => eventMatchesNode(event, node));
  const eventScope = relatedEvents.length > 0 ? relatedEvents : events;
  const relatedTasks = tasks.filter((task) => taskMatchesNode(task, node));
  const latestEventLine =
    [...eventScope].reverse().map(eventActivityLine).find(Boolean) || "";
  const latestTaskLine =
    [...relatedTasks]
      .reverse()
      .map(taskProgressLabel)
      .find((line) => line && !isGenericAgentStatusText(line)) || "";
  const now = stepNowLabel(node, activeTask);
  const hasLiveUpdateCandidate = Boolean(latestEventLine || latestTaskLine);
  const latestUpdate =
    [
      latestEventLine,
      latestTaskLine,
      hasLiveUpdateCandidate ? "" : node.detail,
    ].find(
      (line) => line && !isDuplicateStatusLine(line, now),
    ) || "";
  const recentUpdates = uniqueStringList(
    eventScope
      .slice(-8)
      .map(eventActivityLine)
      .filter(Boolean),
  )
    .filter(
      (line) =>
        !isDuplicateStatusLine(line, now) &&
        (!latestUpdate || !isDuplicateStatusLine(line, latestUpdate)),
    )
    .slice(-4);
  const results = uniqueStringList([
    ...changedPathLines(eventScope),
    ...artifactLines(eventScope),
  ]).slice(-5);

  return {
    status: displayStatusLabel(node.status),
    now,
    latestUpdate,
    recentUpdates,
    results,
  };
}

function isUsefulCardProgressLine(line: string) {
  const text = normalizeSummaryLine(line);
  if (!text) return false;
  if (isGraphTelemetryText(text)) return false;
  if (isRuntimeOutputChunkLimitText(text)) return false;
  if (/^This step is complete\.?$/i.test(text)) return false;
  if (/^Waiting for earlier steps\.?$/i.test(text)) return false;
  if (/^Ready to run when reached\.?$/i.test(text)) return false;
  if (/^Waiting in the queue\.?$/i.test(text)) return false;
  if (/^Checking changes\.?$/i.test(text)) return false;
  if (isGenericAgentStatusText(text) && !/\bis working\b/i.test(text)) return false;
  if (isMachineProgressText(text)) return false;
  return true;
}

function eventParallelLabel(event: ChatV2AgentRunEvent, fallbackBranchId?: string | null) {
  const payload = eventPayload(event);
  return (
    scalarDetailText(payload.branch_id) ||
    scalarDetailText(payload.branchId) ||
    scalarDetailText(payload.branch) ||
    scalarDetailText(payload.parallel_group) ||
    scalarDetailText(payload.parallelGroup) ||
    scalarDetailText(payload.task_graph_branch) ||
    fallbackBranchId ||
    scalarDetailText(event.task_id) ||
    scalarDetailText(event.run_id) ||
    "main"
  );
}

function relatedNodeEvents(node: BlueprintNode, events: ChatV2AgentRunEvent[]) {
  const matches = events.filter((event) => eventMatchesNode(event, node));
  return matches.length > 0 ? matches : events;
}

function workCardAgentTask(
  node: BlueprintNode,
  tasks: ChatV2TaskSnapshot[],
  activeTask: ChatV2TaskSnapshot | null,
) {
  if (activeTask && taskMatchesNode(activeTask, node)) return activeTask;
  return tasks.find((task) => taskMatchesNode(task, node)) ?? activeTask;
}

function activeCardParagraphs(
  node: BlueprintNode,
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
  activeTask: ChatV2TaskSnapshot | null,
) {
  const agentTask = workCardAgentTask(node, tasks, activeTask);
  const scopedEvents = relatedNodeEvents(node, events).slice(-10);
  const grouped = new Map<string, string[]>();
  for (const event of scopedEvents) {
    const line = eventActivityLine(event);
    if (!isUsefulCardProgressLine(line)) continue;
    const key = eventParallelLabel(event, node.branchId);
    const lines = grouped.get(key) ?? [];
    lines.push(normalizeSummaryLine(line));
    grouped.set(key, lines);
  }
  const groups = [...grouped.entries()]
    .map(([label, lines]) => [label, uniqueStringList(lines).slice(-2)] as const)
    .filter(([, lines]) => lines.length > 0);
  if (groups.length > 1) {
    return groups.map(
      ([label, lines]) => `**${label}:** ${highlightWorkAgentText(lines.join(" "), agentTask)}`,
    );
  }
  const liveStatus = blueprintLiveStatus(node, tasks, events, activeTask);
  return uniqueStringList([
    liveStatus.now,
    liveStatus.latestUpdate,
    ...liveStatus.recentUpdates.slice(-2),
  ])
    .filter(isUsefulCardProgressLine)
    .map((line) => highlightWorkAgentText(line, agentTask))
    .slice(0, 3);
}

function fallbackCardContent(node: BlueprintNode) {
  if (node.body && !isGenericCompletionText(node.body)) return node.body;
  if (node.detail && !isGenericCompletionText(node.detail)) return node.detail;
  return "";
}

function blueprintCardContent(
  node: BlueprintNode,
  tasks: ChatV2TaskSnapshot[],
  events: ChatV2AgentRunEvent[],
  activeTask: ChatV2TaskSnapshot | null,
) {
  if (node.status === "active") {
    const paragraphs = activeCardParagraphs(node, tasks, events, activeTask);
    if (paragraphs.length > 0) return paragraphs.join("\n\n");
    return fallbackCardContent(node) || "Working on this step.";
  }
  if (node.status === "done") {
    return fallbackCardContent(node) || `${strippedStepTitle(node.title)} is complete.`;
  }
  if (node.status === "blocked") {
    return fallbackCardContent(node) || "This step needs attention before DAN can continue.";
  }
  if (node.status === "future") {
    return fallbackCardContent(node) || "This planned step will run after earlier work is complete.";
  }
  if (node.status === "ready") {
    return fallbackCardContent(node) || "This step is ready to run when DAN reaches it.";
  }
  if (node.status === "queued") {
    return fallbackCardContent(node) || "This step is waiting in the queue.";
  }
  return fallbackCardContent(node) || "Waiting for output.";
}

const STATUS_TOOL_LABELS = [
  "browser click",
  "browser fill",
  "browser inspect",
  "browser screenshot",
  "file edit",
  "file read",
  "file write",
  "git diff",
  "git status",
  "http request",
  "list directory",
  "python eval",
  "shell command",
  "web fetch",
  "web search",
  "workspace check",
];

const STATUS_TOOL_LABEL_SET = new Set(STATUS_TOOL_LABELS);
const STATUS_TOKEN_PATTERN = new RegExp(
  `(\`[^\`]+\`|https?:\\/\\/[^\\s),]+|\\b(?:${STATUS_TOOL_LABELS.join("|")})\\b|\\b(?:denied|failed|blocked|needs attention)\\b)`,
  "gi",
);

function isStatusToolToken(value: string) {
  return STATUS_TOOL_LABEL_SET.has(value.trim().toLowerCase().replace(/_/g, " "));
}

function isStatusFileToken(value: string) {
  const text = value.trim();
  return Boolean(
    /[/\\]/.test(text) ||
      /\.(?:md|txt|json|html|css|js|ts|tsx|jsx|py|csv|yaml|yml|toml|gd|tscn|png|jpg|jpeg|svg|pdf)$/i.test(text) ||
      /^(?:readme|agents|package|tsconfig|vite\.config)\b/i.test(text),
  );
}

function statusTokenChip(
  text: string,
  kind: "tool" | "file" | "link" | "state" | "code",
  key: string,
) {
  if (kind === "link") {
    return (
      <a
        key={key}
        href={text}
        target="_blank"
        rel="noreferrer"
        className="dan-status-token dan-status-token-link mx-0.5 inline-flex max-w-full items-center gap-1 rounded border border-sky-300/60 bg-sky-50/70 px-1.5 py-0.5 align-baseline text-[0.86em] font-medium text-sky-800 no-underline hover:border-sky-400 hover:bg-sky-100 dark:border-sky-700/70 dark:bg-sky-950/40 dark:text-sky-200"
      >
        <LinkIcon size={11} />
        <span className="truncate">{text}</span>
      </a>
    );
  }
  const classes =
    kind === "tool"
      ? "border-cyan-300/60 bg-cyan-50/70 text-cyan-800 dark:border-cyan-700/70 dark:bg-cyan-950/40 dark:text-cyan-200"
      : kind === "file"
        ? "border-amber-300/60 bg-amber-50/70 text-amber-900 dark:border-amber-700/70 dark:bg-amber-950/40 dark:text-amber-200"
        : kind === "state"
          ? "border-rose-300/70 bg-rose-50/80 text-rose-800 dark:border-rose-700/70 dark:bg-rose-950/45 dark:text-rose-200"
          : "border-slate-300/70 bg-slate-100/70 text-slate-700 dark:border-slate-700 dark:bg-slate-950/60 dark:text-slate-200";
  const Icon = kind === "tool" ? TerminalSquare : kind === "file" ? FileText : kind === "state" ? Shield : null;
  return (
    <span
      key={key}
      className={cx(
        `dan-status-token dan-status-token-${kind}`,
        "mx-0.5 inline-flex max-w-full items-center gap-1 rounded px-1.5 py-0.5 align-baseline text-[0.86em] font-medium",
        "border",
        classes,
      )}
    >
      {Icon && <Icon size={11} />}
      <span className="truncate">{text}</span>
    </span>
  );
}

function StatusLine({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  const source = text || "";
  for (const match of source.matchAll(STATUS_TOKEN_PATTERN)) {
    const raw = match[0];
    const index = match.index ?? 0;
    if (index > lastIndex) parts.push(source.slice(lastIndex, index));
    if (raw.startsWith("`") && raw.endsWith("`")) {
      const value = raw.slice(1, -1);
      const kind = isStatusToolToken(value) ? "tool" : isStatusFileToken(value) ? "file" : "code";
      parts.push(statusTokenChip(value, kind, `${index}:${raw}`));
    } else if (/^https?:\/\//i.test(raw)) {
      parts.push(statusTokenChip(raw, "link", `${index}:${raw}`));
    } else if (isStatusToolToken(raw)) {
      parts.push(statusTokenChip(raw, "tool", `${index}:${raw}`));
    } else {
      parts.push(statusTokenChip(raw, "state", `${index}:${raw}`));
    }
    lastIndex = index + raw.length;
  }
  if (lastIndex < source.length) parts.push(source.slice(lastIndex));
  return <>{parts}</>;
}

function statusLineUsesMarkdown(text: string) {
  const trimmed = text.trim();
  if (!trimmed) return false;
  if (/^(?:#{2,6}\s|[-*]\s+|\d+\.\s+)/.test(trimmed)) return true;
  if (/\n\s*(?:#{2,6}\s|[-*]\s+|\d+\.\s+|```)/.test(trimmed)) return true;
  if (trimmed.length < 140) return false;
  return /(?:#{2,6}\s|\*\*|```|\[[^\]]+\]\(|`[^`]+`)/.test(trimmed);
}

function statusMarkdownContent(text: string) {
  return normalizeStructuredMarkdown(text);
}

function RichStatusLine({ text }: { text: string }) {
  if (!statusLineUsesMarkdown(text)) return <StatusLine text={text} />;
  return (
    <MarkdownRenderer
      content={statusMarkdownContent(text)}
      autoHighlightCode={false}
      className="max-w-full [overflow-wrap:anywhere] [&_code]:break-words [&_h2]:mb-1 [&_h2]:mt-2 [&_h2]:text-sm [&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-[11px] [&_h3]:uppercase [&_h3]:tracking-[0.1em] [&_li]:break-words [&_li]:leading-6 [&_ol]:my-1 [&_p]:my-1 [&_p]:break-words [&_p]:leading-6 [&_pre]:whitespace-pre-wrap [&_ul]:my-1"
    />
  );
}

function sharedEvidenceTimestampLabel(value: string) {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  return new Date(timestamp).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function SharedEvidencePreview({ items }: { items: SharedEvidenceItem[] }) {
  if (items.length === 0) return null;
  return (
    <section className="rounded-md border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-800 dark:bg-slate-900/60">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500 dark:text-slate-400">
          Evidence
        </div>
        <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-semibold text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">
          {items.length} item{items.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="space-y-2">
        {items.map((item) => {
          const timestampLabel = sharedEvidenceTimestampLabel(item.timestamp);
          return (
            <li
              key={`${item.id}:${item.title}:${item.summary}`}
              className="rounded border border-slate-200/80 bg-white/70 px-2.5 py-2 text-sm dark:border-slate-800 dark:bg-slate-950/40"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1 font-semibold text-slate-800 dark:text-slate-100">
                  {item.title}
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                  {timestampLabel && (
                    <span
                      className="text-[10px] font-medium text-slate-400 dark:text-slate-500"
                      title={new Date(item.timestamp).toLocaleString()}
                    >
                      {timestampLabel}
                    </span>
                  )}
                  <span className="rounded-full border border-slate-200 px-2 py-0.5 text-[10px] font-semibold text-slate-500 dark:border-slate-700 dark:text-slate-300">
                    {item.status}
                  </span>
                </div>
              </div>
              <div className="mt-1 leading-6 text-slate-700 dark:text-slate-300">
                <RichStatusLine text={item.summary} />
              </div>
              {(item.source || item.ref) && (
                <div className="mt-1 break-all text-[11px] text-slate-400">
                  {[item.source, item.ref].filter(Boolean).join(" · ")}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function LiveStatusCard({ status }: { status: BlueprintLiveStatus }) {
  return (
    <section className="rounded-md border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-800 dark:bg-slate-900/60">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500 dark:text-slate-400">
          Live Status
        </div>
        <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-semibold text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">
          {status.status}
        </span>
      </div>
      <div className="space-y-3 text-sm text-slate-700 dark:text-slate-300">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">
            Now
          </div>
          <div className="mt-1 leading-6">
            <RichStatusLine text={status.now} />
          </div>
        </div>
        {status.latestUpdate && (
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">
              Latest Update
            </div>
            <div className="mt-1 leading-6">
              <RichStatusLine text={status.latestUpdate} />
            </div>
          </div>
        )}
        {status.recentUpdates.length > 0 && (
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">
              Recent Updates
            </div>
            <ul className="mt-1 list-disc space-y-1 pl-5 leading-6">
              {status.recentUpdates.map((item) => (
                <li key={item}>
                  <RichStatusLine text={item} />
                </li>
              ))}
            </ul>
          </div>
        )}
        {status.results.length > 0 && (
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">
              Results So Far
            </div>
            <ul className="mt-1 list-disc space-y-1 pl-5 leading-6">
              {status.results.map((item) => (
                <li key={item}>
                  <RichStatusLine text={item.replace(/^- /, "")} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function graphTaskStatusLabel(status: BlueprintNodeStatus) {
  if (status === "active") return "in progress";
  if (status === "done") return "done";
  if (status === "ready") return "ready";
  if (status === "future") return "waiting";
  if (status === "blocked") return "needs attention";
  return "queued";
}

function planTaskChecklistItems(node: BlueprintNode) {
  const context = node.graphContext;
  if (!context) return [];
  const tasks = node.graphTaskId
    ? planChildTasksForNode(node)
    : hasExplicitPlanTasks(context)
      ? context.taskGraph.filter(isBlueprintPlanTaskPlan)
      : context.taskGraph.filter((task) => !isBlueprintPlanTaskPlan(task));
  return tasks.map((task) => ({
    task,
    status: statusForPlanTask(task, context),
  }));
}

function activePlanTaskChecklistItems(node: BlueprintNode) {
  return planTaskChecklistItems(node).filter((item) => item.status === "active");
}

function planHistoryItems(node: BlueprintNode, events: ChatV2AgentRunEvent[]) {
  const planId = node.graphTaskId || "";
  const related = events.filter((event) => {
    const payload = eventPayload(event);
    const eventPlanId =
      scalarDetailText(payload.plan_id) ||
      scalarDetailText(payload.parent_plan_id) ||
      scalarDetailText(payload.plan_task_id) ||
      scalarDetailText(payload.owner_scope);
    return !planId || eventPlanId === planId || eventMatchesNode(event, node);
  });
  return uniqueStringList((related.length ? related : events).slice(-8).map(eventActivityLine).filter(Boolean));
}

function planValidationItems(node: BlueprintNode, events: ChatV2AgentRunEvent[]) {
  const planId = node.graphTaskId || "";
  return uniqueStringList(
    events
      .filter((event) => {
        const source = eventSource(event);
        if (!source.includes("validation") && !source.includes("repair") && !source.includes("retry")) {
          return false;
        }
        const payload = eventPayload(event);
        const eventPlanId =
          scalarDetailText(payload.plan_id) ||
          scalarDetailText(payload.parent_plan_id) ||
          scalarDetailText(payload.plan_task_id) ||
          scalarDetailText(payload.owner_scope);
        return !eventPlanId || !planId || eventPlanId === planId;
      })
      .slice(-6)
      .map(eventActivityLine)
      .filter(Boolean),
  );
}

function PlanCardChecklist({ node }: { node: BlueprintNode }) {
  if (node.kind !== "plan") return null;
  const checklist = activePlanTaskChecklistItems(node);
  if (checklist.length === 0) return null;
  const allItems = planTaskChecklistItems(node);
  const visible = checklist.slice(0, 4);
  const remaining = checklist.length - visible.length;
  const inactiveCount = allItems.length - checklist.length;
  return (
    <div className="dan-plan-card-checklist mt-2 grid gap-1.5 text-[12px] leading-5">
      {visible.map(({ task, status }) => (
        <div
          key={task.taskId}
          className="flex min-w-0 items-start gap-2 rounded border border-slate-200/70 bg-white/45 px-2 py-1.5 text-slate-700 dark:border-slate-800/80 dark:bg-slate-950/30 dark:text-slate-300"
        >
          <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded border border-slate-300 bg-white text-slate-500 dark:border-slate-700 dark:bg-slate-950">
            {status === "done" ? (
              <Check size={11} strokeWidth={2.6} />
            ) : status === "active" ? (
              <Loader2 size={11} className="animate-spin text-cyan-500" />
            ) : (
              <Square size={11} />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span className="line-clamp-2 break-words font-medium">{task.goal}</span>
            <span className="text-[10px] uppercase tracking-[0.08em] text-slate-400">
              {graphTaskStatusLabel(status)}
              {task.branchId ? ` · ${task.branchId}` : ""}
            </span>
          </span>
        </div>
      ))}
      {(remaining > 0 || inactiveCount > 0) && (
        <div className="rounded border border-dashed border-slate-200/80 bg-white/30 px-2 py-1 text-[11px] font-medium text-slate-400 dark:border-slate-800 dark:bg-slate-950/20">
          {remaining > 0
            ? `${remaining} more active task${remaining === 1 ? "" : "s"} in Preview`
            : `${inactiveCount} other task${inactiveCount === 1 ? "" : "s"} in Preview`}
        </div>
      )}
    </div>
  );
}

function PlanChecklistPreview({
  node,
  events,
}: {
  node: BlueprintNode;
  events: ChatV2AgentRunEvent[];
}) {
  const context = node.graphContext;
  const planTask = planTaskById(context, node.graphTaskId);
  const checklist = planTaskChecklistItems(node);
  const activeItems = checklist.filter((item) => item.status === "active");
  const history = planHistoryItems(node, events);
  const validation = planValidationItems(node, events);
  return (
    <section className="rounded-md border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-800 dark:bg-slate-900/60">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500 dark:text-slate-400">
            Plan Detail
          </div>
          <div className="mt-1 truncate text-sm font-semibold text-slate-800 dark:text-slate-100">
            {planTask?.goal || strippedStepTitle(node.title)}
          </div>
        </div>
        {context && (
          <div className="flex flex-wrap gap-1 text-[10px] font-semibold text-slate-500 dark:text-slate-400">
            <span className="rounded-full border border-slate-200 bg-white/65 px-2 py-0.5 dark:border-slate-800 dark:bg-slate-950/60">
              plan gen {context.planGenerationQueueLength}
            </span>
            <span className="rounded-full border border-slate-200 bg-white/65 px-2 py-0.5 dark:border-slate-800 dark:bg-slate-950/60">
              plan exec {context.planExecutionQueueLength}
            </span>
            <span className="rounded-full border border-slate-200 bg-white/65 px-2 py-0.5 dark:border-slate-800 dark:bg-slate-950/60">
              task exec {context.taskExecutionQueueLength}
            </span>
          </div>
        )}
      </div>
      {activeItems.length > 1 && (
        <div className="mb-3 rounded border border-cyan-200 bg-cyan-50/65 px-2.5 py-2 text-xs text-cyan-900 dark:border-cyan-800 dark:bg-cyan-950/25 dark:text-cyan-100">
          <div className="mb-1 font-semibold">Parallel active sections</div>
          <div className="flex flex-wrap gap-1.5">
            {activeItems.map(({ task }) => (
              <span
                key={task.taskId}
                className="rounded-full border border-cyan-200 bg-white/65 px-2 py-0.5 dark:border-cyan-800 dark:bg-slate-950/50"
              >
                {task.branchId ? `${task.branchId}: ` : ""}
                {task.goal}
              </span>
            ))}
          </div>
        </div>
      )}
      {checklist.length > 0 ? (
        <ul className="space-y-2">
          {checklist.map(({ task, status }) => (
            <li
              key={task.taskId}
              data-status={status}
              className="flex gap-2 text-sm leading-5 text-slate-700 dark:text-slate-300"
            >
              <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded border border-slate-300 bg-white text-slate-500 dark:border-slate-700 dark:bg-slate-950">
                {status === "done" ? (
                  <Check size={13} strokeWidth={2.6} />
                ) : status === "active" ? (
                  <Loader2 size={13} className="animate-spin text-cyan-500" />
                ) : (
                  <Square size={13} />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="font-medium text-slate-800 dark:text-slate-100">
                  {task.taskId}. {task.goal}
                </span>
                <span className="mt-0.5 block text-xs text-slate-400">
                  {graphTaskStatusLabel(status)}
                  {task.branchId ? ` · branch ${task.branchId}` : ""}
                </span>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded border border-dashed border-slate-200 bg-white/50 p-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-950/40 dark:text-slate-400">
          DAN has not emitted task details inside this plan yet.
        </div>
      )}
      {(history.length > 0 || validation.length > 0) && (
        <div className="mt-4 grid gap-3 text-sm text-slate-700 dark:text-slate-300">
          {history.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">
                History
              </div>
              <ul className="mt-1 list-disc space-y-1 pl-5 leading-6">
                {history.map((item) => (
                  <li key={item}>
                    <RichStatusLine text={item} />
                  </li>
                ))}
              </ul>
            </div>
          )}
          {validation.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">
                Validation / Repair
              </div>
              <ul className="mt-1 list-disc space-y-1 pl-5 leading-6">
                {validation.map((item) => (
                  <li key={item}>
                    <RichStatusLine text={item} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function userConversationChunks(chunks: WorkspaceChunk[]) {
  return chunks.filter(
    (chunk) =>
      chunk.kind === "chat" &&
      chunk.role === "user" &&
      Boolean(chunk.body.trim()),
  );
}

function nodeConversationChunk(
  node: BlueprintNode,
  chunks: WorkspaceChunk[],
  assignedChunkIds: Set<string>,
) {
  const byId = new Map(chunks.map((chunk) => [chunk.id, chunk]));
  const explicit = node.sourceChunkId ? byId.get(node.sourceChunkId) : undefined;
  if (explicit && !assignedChunkIds.has(explicit.id)) return explicit;
  if (node.kind !== "request" || !node.rawRequest?.trim()) return null;
  const rawRequest = node.rawRequest.trim();
  return (
    chunks.find((chunk) => !assignedChunkIds.has(chunk.id) && chunk.body.trim() === rawRequest) ??
    null
  );
}

function blueprintTimelineItems(
  nodes: BlueprintNode[],
  conversationChunks: WorkspaceChunk[],
): BlueprintTimelineItem[] {
  const chunks = userConversationChunks(conversationChunks);
  const chunkIndexById = new Map(chunks.map((chunk, index) => [chunk.id, index]));
  const assignedChunkIds = new Set<string>();
  let latestAssignedChunkIndex = -1;
  const items: BlueprintTimelineItem[] = [];
  const visibleNodes = nodes.filter(isVisibleRunStepNode);
  for (const node of visibleNodes) {
    const chunk = nodeConversationChunk(node, chunks, assignedChunkIds);
    if (chunk) {
      assignedChunkIds.add(chunk.id);
      latestAssignedChunkIndex = Math.max(
        latestAssignedChunkIndex,
        chunkIndexById.get(chunk.id) ?? -1,
      );
      items.push({
        kind: "conversation",
        id: `conversation:${chunk.id}:before:${node.id}`,
        chunk,
      });
    }
    items.push({ kind: "node", id: `node:${node.id}`, node });
  }
  chunks.forEach((chunk, index) => {
    if (assignedChunkIds.has(chunk.id)) return;
    if (latestAssignedChunkIndex >= 0 && index <= latestAssignedChunkIndex) return;
    items.push({
      kind: "conversation",
      id: `conversation:${chunk.id}:unmatched`,
      chunk,
    });
  });
  return items;
}

function isVisibleRunStepNode(node: BlueprintNode) {
  return node.status === "done" || node.status === "active" || node.status === "blocked";
}

function ConversationTimelineCard({
  chunk,
  selected,
  onSelect,
}: {
  chunk: WorkspaceChunk;
  selected: boolean;
  onSelect: (chunk: WorkspaceChunk) => void;
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onSelect(chunk);
  };
  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onSelect(chunk)}
      onKeyDown={handleKeyDown}
      data-selected={selected ? "true" : undefined}
      className={cx(
        "dan-user-chat-box block w-full cursor-pointer rounded-md border px-3 py-2.5 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-cyan-300",
        selected
          ? "border-cyan-300 bg-cyan-50/70 text-slate-950 ring-1 ring-cyan-200 dark:border-cyan-500 dark:bg-cyan-950/20 dark:text-cyan-50 dark:ring-cyan-800"
          : "border-slate-200 bg-white/75 text-slate-700 hover:border-slate-300 dark:border-slate-800 dark:bg-slate-950/45 dark:text-slate-300 dark:hover:border-slate-700",
      )}
    >
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
        You
      </div>
      <MarkdownRenderer
        content={chunk.body}
        className="max-h-32 overflow-hidden text-sm leading-6 [overflow-wrap:anywhere] [&_code]:break-words [&_p]:my-0 [&_p]:break-words [&_p]:leading-6"
      />
    </article>
  );
}

function attemptStatusTone(status: string) {
  const normalized = status.toLowerCase();
  if (["running", "active", "executing", "validating"].includes(normalized)) {
    return "border-cyan-300 bg-cyan-100 text-cyan-900 dark:border-cyan-700 dark:bg-cyan-950/50 dark:text-cyan-100";
  }
  if (["completed", "complete", "done", "succeeded"].includes(normalized)) {
    return "border-emerald-300 bg-emerald-100 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/45 dark:text-emerald-100";
  }
  if (["failed", "blocked", "cancelled", "canceled"].includes(normalized)) {
    return "border-rose-300 bg-rose-100 text-rose-900 dark:border-rose-800 dark:bg-rose-950/45 dark:text-rose-100";
  }
  return "border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300";
}

function ExecutionAttemptPlate({ context }: { context: BlueprintPlanContext }) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const attempts = context.executionAttempts ?? [];
  const latest = latestExecutionAttemptForBlueprint(context);
  const visibleAttempts = attempts;
  if (!latest) {
    return (
      <div className="dan-execution-attempt-plate mb-3 rounded border border-dashed border-slate-300 bg-white/45 px-3 py-2.5 text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-950/35 dark:text-slate-400">
        No execution attempt has started for this blueprint revision.
      </div>
    );
  }
  const resourceFacets = [
    { label: "Workers", items: latest.workers },
    { label: "Models", items: latest.models },
    { label: "Tools", items: latest.tools },
  ].filter((facet) => facet.items.length > 0);
  const retries =
    latest.retryCount !== null
      ? `${latest.retryCount}${latest.maxRetries !== null ? ` / ${latest.maxRetries}` : ""}`
      : latest.maxRetries !== null
        ? `up to ${latest.maxRetries}`
        : "";
  return (
    <div className="dan-execution-attempt-plate mb-3 rounded border border-slate-200/80 bg-white/70 p-3 shadow-[inset_3px_0_0_rgba(6,182,212,0.55)] dark:border-slate-800 dark:bg-slate-950/55">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span
              className={cx(
                "rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em]",
                attemptStatusTone(latest.status),
              )}
            >
              {latest.status || "planned"}
            </span>
            {latest.phase && (
              <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-500 dark:text-slate-400">
                {latest.phase.replace(/_/g, " ")}
              </span>
            )}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[11px] text-slate-500 dark:text-slate-400">
            <span className="font-mono text-slate-700 dark:text-slate-200">
              {latest.attemptId}
            </span>
            {latest.backend && <span>{latest.backend}</span>}
            {latest.schedule && <span>{latest.schedule}</span>}
            {retries && <span>{retries} retries</span>}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] text-slate-400">
            {latest.blueprintRevisionId || context.blueprintRevisionId || "current blueprint"}
          </span>
          {visibleAttempts.length > 1 && (
            <button
              type="button"
              onClick={() => setHistoryOpen((open) => !open)}
              aria-expanded={historyOpen}
              className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white/70 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 transition hover:border-slate-300 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-400"
            >
              {visibleAttempts.length} attempts
              <ChevronDown size={10} className={cx("transition", historyOpen && "rotate-180")} />
            </button>
          )}
        </div>
      </div>
      {resourceFacets.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {resourceFacets.map((facet) => (
            <span
              key={facet.label}
              title={facet.items.join("\n")}
              className="rounded border border-slate-200 bg-slate-50/70 px-2 py-1 text-[10px] text-slate-600 dark:border-slate-800 dark:bg-slate-900/65 dark:text-slate-300"
            >
              <span className="font-semibold uppercase tracking-[0.08em] text-slate-400">
                {facet.label}
              </span>{" "}
              {facet.items.slice(0, 2).join(", ")}
              {facet.items.length > 2 ? ` +${facet.items.length - 2}` : ""}
            </span>
          ))}
        </div>
      )}
      {historyOpen && visibleAttempts.length > 1 && (
        <div className="mt-2 grid gap-1 border-t border-slate-200/80 pt-2 dark:border-slate-800">
          {[...visibleAttempts].reverse().map((attempt) => (
            <div
              key={`${attempt.attemptId}:${attempt.blueprintRevisionId}:${attempt.runId}`}
              className="flex items-center justify-between gap-2 rounded bg-slate-50/70 px-2 py-1.5 text-[10px] dark:bg-slate-900/55"
            >
              <span className="min-w-0 truncate font-mono text-slate-600 dark:text-slate-300">
                {attempt.attemptId}
              </span>
              <span className="shrink-0 text-slate-400">
                {[attempt.phase, attempt.backend, attempt.status].filter(Boolean).join(" · ")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BlueprintView({
  nodes,
  conversationChunks,
  tasks,
  agentEvents,
  activeTask,
  activeNodeId,
  selectedNodeId,
  selectedChunkId,
  elapsedCounter,
  loading,
  onSelect,
  onSelectConversationChunk,
}: {
  nodes: BlueprintNode[];
  conversationChunks: WorkspaceChunk[];
  tasks: ChatV2TaskSnapshot[];
  agentEvents: ChatV2AgentRunEvent[];
  activeTask: ChatV2TaskSnapshot | null;
  activeNodeId: string | null;
  selectedNodeId: string | null;
  selectedChunkId: string | null;
  elapsedCounter: WorkElapsedCounter | null;
  loading: boolean;
  onSelect: (node: BlueprintNode) => void;
  onSelectConversationChunk: (chunk: WorkspaceChunk) => void;
}) {
  const activeRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!activeNodeId) return;
    window.requestAnimationFrame(() => {
      activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }, [activeNodeId, nodes.length]);

  const visibleRunStepNodes = useMemo(() => nodes.filter(isVisibleRunStepNode), [nodes]);
  const counts = visibleRunStepNodes.reduce(
    (acc, node) => {
      acc[node.status] += 1;
      return acc;
    },
    {
      done: 0,
      active: 0,
      ready: 0,
      future: 0,
      queued: 0,
      blocked: 0,
    } satisfies Record<BlueprintNodeStatus, number>,
  );
  const timelineItems = useMemo(
    () => blueprintTimelineItems(nodes, conversationChunks),
    [nodes, conversationChunks],
  );
  const blueprintContext = useMemo(() => canonicalBlueprintContext(nodes), [nodes]);

  if (nodes.length === 0) {
    return (
      <div className="dan-blueprint-board grid min-h-52 place-items-center rounded-md border border-dashed border-slate-200 bg-white/70 p-5 text-center text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-950/70">
        {loading ? "Loading work..." : "No active work yet."}
      </div>
    );
  }

  return (
    <div className="dan-blueprint-board min-h-full rounded-md border border-slate-200/80 bg-[linear-gradient(to_right,rgba(148,163,184,0.10)_1px,transparent_1px),linear-gradient(to_bottom,rgba(148,163,184,0.10)_1px,transparent_1px)] bg-[size:28px_28px] p-4 dark:border-slate-800 dark:bg-slate-950">
      <BlueprintTreeOverview
        nodes={nodes}
        activeNodeId={activeNodeId}
        selectedNodeId={selectedNodeId}
        onSelect={onSelect}
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
          <Activity size={13} />
          Execution Attempt
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          {elapsedCounter && (
            <span
              className="dan-work-elapsed-counter inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white/60 px-2.5 py-0.5 font-semibold text-slate-500 dark:border-slate-800 dark:bg-slate-950"
              aria-label={`Total work time ${elapsedCounter.value}`}
            >
              {elapsedCounter.active ? (
                <Loader2 size={11} className="animate-spin" />
              ) : (
                <Clock3 size={11} />
              )}
              <span className="uppercase tracking-[0.12em]">Total</span>
              <span className="font-mono tabular-nums tracking-normal">{elapsedCounter.value}</span>
            </span>
          )}
          {(["active", "ready", "done", "future", "queued", "blocked"] as const).map((status) => {
            if (counts[status] === 0) return null;
            const tone = blueprintStatusTone(status);
            return (
              <span
                key={status}
                data-status={status}
                className={cx(
                  "dan-blueprint-status-count inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-semibold",
                  tone.badge,
                )}
              >
                {status === "done" ? <Check size={11} strokeWidth={2.6} /> : null}
                {status} {counts[status]}
              </span>
            );
          })}
        </div>
      </div>

      {blueprintContext && <ExecutionAttemptPlate context={blueprintContext} />}

      <ol className="relative space-y-2.5">
        <div className="dan-blueprint-rail-line absolute bottom-4 left-[18px] top-4 w-px bg-slate-200 dark:bg-slate-800" />
        {timelineItems.map((item) => {
          if (item.kind === "conversation") {
            const selected = item.chunk.id === selectedChunkId;
            return (
              <li key={item.id} className="relative">
                <ConversationTimelineCard
                  chunk={item.chunk}
                  selected={selected}
                  onSelect={onSelectConversationChunk}
                />
              </li>
            );
          }
          const { node } = item;
          const tone = blueprintStatusTone(node.status);
          const Icon = blueprintKindIcon(node.kind);
          const active = node.id === activeNodeId;
          const selected = node.id === selectedNodeId;
          const leftOffset = Math.min(node.depth ?? 0, 2) * 22;
          const cardContent = blueprintCardContent(node, tasks, agentEvents, activeTask);
          const showPlanCardChecklist =
            node.kind === "plan" && activePlanTaskChecklistItems(node).length > 0;
          const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            onSelect(node);
          };

          return (
            <li
              key={node.id}
              className="relative"
              style={{ marginLeft: leftOffset }}
            >
              <article
                ref={active ? activeRef : undefined}
                role="button"
                data-status={node.status}
                data-current={active ? "true" : undefined}
                tabIndex={0}
                aria-current={active ? "step" : undefined}
                aria-pressed={selected}
                onClick={() => onSelect(node)}
                onKeyDown={handleKeyDown}
                className={cx(
                  "dan-blueprint-node group relative max-w-full cursor-pointer overflow-hidden rounded-md border px-3 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-slate-300 dark:focus-visible:ring-slate-600",
                  node.compact ? "py-2" : "py-3",
                  tone.node,
                  active &&
                    "ring-2 ring-cyan-300 ring-offset-1 ring-offset-white dark:ring-cyan-500 dark:ring-offset-slate-950",
                  selected && !active && "ring-2 ring-slate-300 dark:ring-slate-600",
                  !selected && "hover:border-slate-400 dark:hover:border-slate-600",
                )}
              >
                <div
                  className={cx(
                    "dan-blueprint-marker absolute -left-[32px] top-3 grid h-7 w-7 place-items-center rounded-full border text-[11px] shadow-sm",
                    tone.marker,
                  )}
                >
                  {node.status === "active" ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : node.status === "future" ? (
                    <Circle size={10} />
                  ) : (
                    <Icon size={13} />
                  )}
                </div>
                <div className="flex min-w-0 items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div
                      className={cx(
                        "break-words font-semibold leading-5",
                        node.compact ? "text-[13px]" : "text-[14px]",
                      )}
                    >
                      {node.title}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span
                      className={cx(
                        "dan-blueprint-badge inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold",
                        tone.badge,
                      )}
                    >
                      {node.status === "done" ? <Check size={11} strokeWidth={2.6} /> : null}
                      {node.status}
                    </span>
                  </div>
                </div>
                {showPlanCardChecklist ? (
                  <PlanCardChecklist node={node} />
                ) : (
                  <div
                    className={cx(
                      "mt-2 max-w-full overflow-hidden text-sm leading-6 text-slate-700 [overflow-wrap:anywhere] dark:text-slate-300",
                      node.kind === "answer"
                        ? "max-h-80"
                        : node.status === "active"
                          ? "max-h-72"
                          : node.compact
                            ? "max-h-32"
                            : "max-h-48",
                    )}
                  >
                    <MarkdownRenderer
                      content={cardContent || "Waiting for output..."}
                      className="max-w-full overflow-hidden [&_code]:break-words [&_h3]:mb-1 [&_h3]:mt-0 [&_h3]:text-[11px] [&_h3]:uppercase [&_h3]:tracking-[0.12em] [&_li]:break-words [&_li]:leading-6 [&_ol]:my-1 [&_p]:my-0 [&_p]:break-words [&_p]:leading-6 [&_ul]:my-1.5"
                    />
                  </div>
                )}
                {node.dependencyIds && node.dependencyIds.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-slate-400">
                    {node.dependencyIds.slice(0, 4).map((dependency) => (
                      <span
                        key={dependency}
                        className="rounded-full border border-slate-200 bg-white/60 px-1.5 py-0.5 dark:border-slate-800 dark:bg-slate-950"
                      >
                        after {dependency}
                      </span>
                    ))}
                  </div>
                )}
              </article>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function WorkspaceFileTree({
  nodes,
  activePath,
  query,
  expanded,
  onToggle,
  onSelect,
  onMove,
}: {
  nodes: FileTreeNode[];
  activePath: string | null;
  query: string;
  expanded: Record<string, boolean>;
  onToggle: (path: string) => void;
  onSelect: (entry: WorkspaceFileEntry) => void;
  onMove: (entry: WorkspaceFileEntry, targetDirectory: WorkspaceFileEntry) => void;
}) {
  const normalizedQuery = query.trim().toLowerCase();
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const nodeMatches = useCallback(
    (node: FileTreeNode): boolean => {
      if (!normalizedQuery) return true;
      if (`${node.relative_path} ${node.name}`.toLowerCase().includes(normalizedQuery)) {
        return true;
      }
      return node.children.some(nodeMatches);
    },
    [normalizedQuery],
  );

  const renderNode = (node: FileTreeNode, depth: number) => {
    if (!nodeMatches(node)) return null;
    const isOpen = Boolean(expanded[node.relative_path] || normalizedQuery);
    const isActive = activePath === node.path;
    const isDropTarget = dropTarget === node.path;
    const visibleChildren = node.children.filter(nodeMatches);
    return (
      <div key={node.path}>
        <button
          type="button"
          draggable
          onDragStart={(event: DragEvent<HTMLButtonElement>) => {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("application/dan-work-file", node.path);
          }}
          onDragOver={(event) => {
            if (!node.is_directory) return;
            const source = event.dataTransfer.getData("application/dan-work-file");
            if (source && (source === node.path || node.path.startsWith(`${source}/`))) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
            setDropTarget(node.path);
          }}
          onDragLeave={() => {
            if (isDropTarget) setDropTarget(null);
          }}
          onDrop={(event) => {
            if (!node.is_directory) return;
            const source = event.dataTransfer.getData("application/dan-work-file");
            setDropTarget(null);
            if (!source || source === node.path) return;
            const sourceNode = devFileByPath(nodes, source);
            if (!sourceNode) return;
            event.preventDefault();
            onMove(sourceNode, node);
          }}
          onClick={() => (node.is_directory ? onToggle(node.relative_path) : onSelect(node))}
          data-active={isActive ? "true" : undefined}
          data-drop-target={isDropTarget ? "true" : undefined}
          className={cx(
            "dan-rail-card-row dan-work-file-row group/session flex w-full min-w-0 items-start gap-1.5 rounded-lg border px-1.5 py-2 text-left transition",
            railCardTone(isActive, isDropTarget),
          )}
          style={{ paddingLeft: 6 + depth * 14 }}
        >
          <span className="flex w-4 shrink-0 flex-col items-center pt-0.5">
            {node.is_directory ? (
              isOpen ? (
                <ChevronDown size={14} className="shrink-0 text-slate-400" />
              ) : (
                <ChevronRight size={14} className="shrink-0 text-slate-400" />
              )
            ) : (
              <span className="h-3.5 w-3.5 shrink-0" />
            )}
          </span>
          <span className="dan-rail-card-kind mt-0.5">
            {node.is_directory ? <Folder size={12} /> : <File size={12} />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="dan-rail-card-title block truncate">{node.name}</span>
            <span className="dan-rail-card-meta">{workspaceFileCardMeta(node)}</span>
          </span>
        </button>
        {node.is_directory && isOpen && visibleChildren.length > 0 && (
          <div>{visibleChildren.map((child) => renderNode(child, depth + 1))}</div>
        )}
      </div>
    );
  };

  return <div className="space-y-0.5">{nodes.map((node) => renderNode(node, 0))}</div>;
}

function NoteTree({
  nodes,
  activeId,
  query,
  expanded,
  root,
  onToggle,
  onSelect,
  onMove,
}: {
  nodes: NoteTreeNode[];
  activeId: string | null;
  query: string;
  expanded: Record<string, boolean>;
  root: string;
  onToggle: (id: string) => void;
  onSelect: (note: WorkspaceNote) => void;
  onMove: (sourcePath: string, targetFolderPath: string, sourceNoteId?: string) => void;
}) {
  const normalizedQuery = query.trim().toLowerCase();
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const nodeMatches = useCallback(
    (node: NoteTreeNode): boolean => {
      if (!normalizedQuery) return true;
      if (`${node.label} ${node.pathLabel}`.toLowerCase().includes(normalizedQuery)) return true;
      return node.children.some(nodeMatches);
    },
    [normalizedQuery],
  );

  const renderNode = (node: NoteTreeNode, depth: number) => {
    if (!nodeMatches(node)) return null;
    const visibleChildren = node.children.filter(nodeMatches);
    const hasChildren = visibleChildren.length > 0;
    const isOpen = Boolean(expanded[node.id] || normalizedQuery);
    const isActive = Boolean(node.note && node.note.id === activeId);
    const canSelect = Boolean(node.note);
    const folderPath = noteDropFolderPath(node.pathLabel);
    const targetFolderPath = folderPath ? joinPath(root, folderPath) : root;
    const sourcePath = node.note
      ? noteMovablePath(node.note)
      : folderPath
        ? joinPath(root, folderPath)
        : "";
    const isDropTarget = node.isFolder && dropTarget === targetFolderPath;
    return (
      <div key={node.id}>
        <div
          className={cx(
            "dan-rail-card-row dan-note-tree-row group/session flex min-w-0 items-start gap-1.5 rounded-lg border px-1.5 py-2 transition",
            railCardTone(isActive, isDropTarget),
          )}
          data-active={isActive ? "true" : undefined}
          data-drop-target={isDropTarget ? "true" : undefined}
          style={{ paddingLeft: 6 + depth * 14 }}
        >
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              if (hasChildren) onToggle(node.id);
              else if (node.note) onSelect(node.note);
            }}
            className={cx(
              "flex w-4 shrink-0 flex-col items-center pt-0.5 text-slate-400 transition hover:text-slate-700 dark:hover:text-slate-200",
              !hasChildren && "pointer-events-none opacity-0",
            )}
            aria-label={isOpen ? "Collapse note folder" : "Expand note folder"}
          >
            {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </button>
          <button
            type="button"
            draggable={Boolean(sourcePath)}
            onDragStart={(event: DragEvent<HTMLButtonElement>) => {
              if (!sourcePath) return;
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("application/dan-note-entry", sourcePath);
              if (node.note) {
                event.dataTransfer.setData("application/dan-note-id", node.note.id);
              }
            }}
            onDragOver={(event) => {
              if (!node.isFolder) return;
              const source = event.dataTransfer.getData("application/dan-note-entry");
              if (source && (source === targetFolderPath || targetFolderPath.startsWith(`${source}/`))) {
                return;
              }
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
              setDropTarget(targetFolderPath);
            }}
            onDragLeave={() => {
              if (isDropTarget) setDropTarget(null);
            }}
            onDrop={(event) => {
              if (!node.isFolder) return;
              const source = event.dataTransfer.getData("application/dan-note-entry");
              const sourceNoteId = event.dataTransfer.getData("application/dan-note-id") || undefined;
              setDropTarget(null);
              if (!source || source === targetFolderPath) return;
              event.preventDefault();
              onMove(source, targetFolderPath, sourceNoteId);
            }}
            onClick={() => (node.note ? onSelect(node.note) : onToggle(node.id))}
            className="flex min-w-0 flex-1 items-start gap-1.5 text-left"
            title={node.pathLabel}
          >
            <span className="dan-rail-card-kind mt-0.5">
              {node.isFolder ? (
                isOpen ? (
                  <FolderOpen size={12} />
                ) : (
                  <Folder size={12} />
                )
              ) : (
                <FileText size={12} />
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="dan-rail-card-title block truncate">{node.label}</span>
              <span className="dan-rail-card-meta">
                {node.note ? noteCardMeta(node.note, root) : node.pathLabel || "folder"}
              </span>
            </span>
            {canSelect && node.note?.status === "dirty" && (
              <Circle size={8} className="mt-1.5 shrink-0" fill="currentColor" />
            )}
          </button>
        </div>
        {hasChildren && isOpen && (
          <div className="mt-0.5 space-y-0.5">
            {visibleChildren.map((child) => renderNode(child, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  return <div className="space-y-0.5">{nodes.map((node) => renderNode(node, 0))}</div>;
}

function WorkspacePreviewIcon({ kind }: { kind: WorkspaceFilePreviewKind }) {
  if (kind === "image") return <File size={13} />;
  if (kind === "pdf") return <ScrollText size={13} />;
  if (kind === "html") return <PanelRight size={13} />;
  return <FileText size={13} />;
}

function WorkspacePreviewArtifactsCard({
  artifacts,
  selectedPath,
  onSelect,
}: {
  artifacts: WorkspacePreviewArtifact[];
  selectedPath: string | null;
  onSelect: (entry: WorkspaceFileEntry) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  return (
    <section className="mt-3 rounded-md border border-slate-200/80 bg-white/75 p-3 shadow-sm dark:border-slate-800 dark:bg-slate-950/65">
      <div className={cx("flex items-center justify-between gap-3", expanded && "mb-2")}>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          title={expanded ? "Collapse Preview Card" : "Expand Preview Card"}
          className="flex min-w-0 items-center gap-2 rounded px-1 py-0.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300 dark:text-slate-400 dark:hover:bg-slate-900 dark:hover:text-slate-200"
        >
          {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <Eye size={13} />
          Preview Card
        </button>
        <span className="rounded-full border border-slate-200 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-500 dark:border-slate-800 dark:bg-slate-950/70 dark:text-slate-400">
          {artifacts.length}
        </span>
      </div>
      {!expanded ? null : artifacts.length > 0 ? (
        <div className="grid gap-1.5">
          {artifacts.map((artifact) => {
            const selected =
              selectedPath === artifact.entry.path ||
              selectedPath === artifact.entry.relative_path;
            return (
              <button
                type="button"
                key={artifact.id}
                onClick={() => onSelect(artifact.entry)}
                data-selected={selected ? "true" : undefined}
                className={cx(
                  "flex min-w-0 items-center gap-2 rounded border px-2.5 py-2 text-left text-sm transition",
                  selected
                    ? "border-amber-300 bg-amber-50/75 text-amber-950 ring-1 ring-amber-200 dark:border-amber-700 dark:bg-amber-950/25 dark:text-amber-100 dark:ring-amber-900"
                    : "border-slate-200/80 bg-slate-50/70 text-slate-700 hover:border-slate-300 hover:bg-white dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-300 dark:hover:border-slate-700 dark:hover:bg-slate-900/70",
                )}
              >
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded border border-slate-200 bg-white text-slate-500 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
                  <WorkspacePreviewIcon kind={artifact.kind} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold">{artifact.entry.name}</span>
                  <span className="block truncate text-[11px] text-slate-400">
                    {artifact.entry.parent || artifact.entry.relative_path}
                  </span>
                </span>
                <span className="hidden shrink-0 items-center gap-1 rounded-full border border-slate-200 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-500 dark:border-slate-800 dark:bg-slate-950/70 dark:text-slate-300 sm:inline-flex">
                  {workspacePreviewKindLabel(artifact.kind)}
                  <LinkIcon size={10} />
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="rounded border border-dashed border-slate-200/80 bg-slate-50/60 px-3 py-2 text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-900/35">
          No previewable outputs yet.
        </div>
      )}
    </section>
  );
}

function WorkspaceFilePreviewPanel({
  entry,
  root,
  content,
  status,
}: {
  entry: WorkspaceFileEntry;
  root: string;
  content: string;
  status: DevFileStatus;
}) {
  const kind = workspaceFilePreviewKind(entry);
  const url = workspaceFilePreviewUrl(entry.path, root || undefined, entry.relative_path);
  const frameClass =
    "h-[72vh] min-h-[420px] w-full rounded-md border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950";
  if (kind === "image") {
    return (
      <div className="grid min-h-[320px] place-items-center rounded-md border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-800 dark:bg-slate-900/50">
        <img
          src={url}
          alt={entry.name}
          className="max-h-[72vh] max-w-full rounded object-contain shadow-sm"
        />
      </div>
    );
  }
  if (kind === "pdf") {
    return <iframe title={entry.name} src={url} className={frameClass} />;
  }
  if (kind === "html") {
    return (
      <iframe
        title={entry.name}
        src={url}
        sandbox="allow-forms allow-modals allow-popups allow-presentation allow-scripts"
        className={frameClass}
      />
    );
  }
  if (status === "loading") {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <Loader2 size={14} className="animate-spin" />
        Loading file
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="rounded-md border border-rose-200 bg-rose-50/75 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/25 dark:text-rose-200">
        {content || "File preview failed."}
      </div>
    );
  }
  if (kind === "markdown") {
    return <MarkdownRenderer content={content || "_empty file_"} />;
  }
  return (
    <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5 text-slate-700 dark:text-slate-300">
      {content || "_empty file_"}
    </pre>
  );
}

export default function ChunkWorkspaceApp() {
  const isPhoneViewport = usePhoneViewport();
  const initialNotes = useMemo(() => readStoredNotes(), []);
  const initialLayout = useMemo(() => readStoredLayout(), []);
  const initialUiState = useMemo(() => readStoredUiState(), []);
  const initialComposerDrafts = useMemo(() => readStoredComposerDrafts(), []);
  const [notes, setNotes] = useState<WorkspaceNote[]>(initialNotes.notes);
  const [activeNoteId, setActiveNoteId] = useState<string | null>(initialNotes.activeId);
  const [activePane, setActivePane] = useState<WorkspacePane>(initialUiState.activePane);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(
    initialUiState.selectedChunkId,
  );
  const [selectedBlueprintNodeId, setSelectedBlueprintNodeId] = useState<string | null>(null);
  const [threads, setThreads] = useState<ChatV2ThreadSummary[]>([]);
  const [workbenchSettings, setWorkbenchSettings] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [importNativeSessions, setImportNativeSessions] = useState<{ id: string; root: string } | null>(null);
  const [danSettings, setDanSettings] = useState(false);
  const [sidecarChat, setSidecarChat] = useState(false);
  const [sidecarSelection, setSidecarSelection] = useState({ text: "", token: 0 });
  const [workbenchActivity, setWorkbenchActivity] = useState(false);
  const [teamPanel, setTeamPanel] = useState(false);
  const [workbenchOutline, setWorkbenchOutline] = useState(false);
  const [deletingArchived, setDeletingArchived] = useState(false);
  const [archiveDeleteProgress, setArchiveDeleteProgress] = useState("");
  const [sessionShelfScope, setSessionShelfScope] = useState<"all" | "archived">("all");
  const [threadQuery, setThreadQuery] = useState(initialUiState.threadQuery);
  const [threadWorkspaces, setThreadWorkspaces] = useState<Record<string, string>>(
    () => readStoredThreadWorkspaces(),
  );
  const [sessionResponseSeen, setSessionResponseSeen] = useState<Record<string, string>>(
    () => readStoredSessionResponseSeen(),
  );
  const [collapsedThreadGroups, setCollapsedThreadGroups] = useState<Record<string, boolean>>(
    initialUiState.collapsedThreadGroups,
  );
  const [activeThread, setActiveThread] = useState<{
    id: string;
    workflowId: string;
    title?: string;
  } | null>(null);
  useEffect(() => { setSidecarSelection({ text: "", token: Date.now() }); }, [activeThread?.id]);
  const [loadingThreadId, setLoadingThreadId] = useState<string | null>(null);
  const [pendingAssistantIds, setPendingAssistantIds] = useState<Record<string, boolean>>({});
  const [liveActions, setLiveActions] = useState<Record<string, string>>({});
  const attachedRunKeyRef = useRef("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tasks, setTasks] = useState<ChatV2TaskSnapshot[]>([]);
  const [backgroundTasks, setBackgroundTasks] = useState<ChatV2TaskSnapshot[]>([]);
  const [agentEvents, setAgentEvents] = useState<ChatV2AgentRunEvent[]>([]);
  const [input, setInput] = useState("");
  const [composerReferences, setComposerReferences] = useState<Record<string, { text: string; title: string; workflowId: string; threadId: string; messageIds: string[] }>>({});
  const [composerAttachments, setComposerAttachments] = useState<ComposerAttachmentDraft[]>([]);
  const [composerCaret, setComposerCaret] = useState(0);
  const [composerSuggestionIndex, setComposerSuggestionIndex] = useState(0);
  const [composerSuggestionSuppressedFor, setComposerSuggestionSuppressedFor] = useState<string | null>(null);
  const [activeRunPlacement, setActiveRunPlacement] = useState<ActiveRunPlacement>("steer");
  const [status, setStatus] = useState("Ready");
  const [sending, setSending] = useState(false);
  const [noteQuery, setNoteQuery] = useState(initialUiState.noteQuery);
  const [noteFacet, setNoteFacet] = useState(initialUiState.noteFacet);
  const [noteFacetPage, setNoteFacetPage] = useState<string | null>(
    initialUiState.noteFacetPage,
  );
  const [noteArticleOriginFacet, setNoteArticleOriginFacet] = useState<string | null>(
    initialUiState.noteArticleOriginFacet,
  );
  const [noteRailView, setNoteRailView] = useState<NoteRailView>(
    initialUiState.noteRailView,
  );
  const [noteMention, setNoteMention] = useState<NoteMentionState | null>(null);
  const [noteMentionIndex, setNoteMentionIndex] = useState(0);
  const [notesRoot, setNotesRoot] = useState("");
  const [devRoot, setDevRoot] = useState("");
  const [devFiles, setDevFiles] = useState<WorkspaceFileEntry[]>([]);
  const [workspaceSkills, setWorkspaceSkills] = useState<WorkspaceSkillSuggestion[]>([]);
  const [devFileQuery, setDevFileQuery] = useState(initialUiState.devFileQuery);
  const [expandedFileDirs, setExpandedFileDirs] = useState<Record<string, boolean>>(
    initialUiState.expandedFileDirs,
  );
  const [expandedNoteFolders, setExpandedNoteFolders] = useState<Record<string, boolean>>(
    initialUiState.expandedNoteFolders,
  );
  const [activeFilePath, setActiveFilePath] = useState<string | null>(
    initialUiState.activeFilePath,
  );
  const [activeFileContent, setActiveFileContent] = useState("");
  const [activeFileStatus, setActiveFileStatus] = useState<DevFileStatus>("idle");
  const [promptLogPreview, setPromptLogPreview] = useState<PromptLogPreview | null>(null);
  const [rootEditing, setRootEditing] = useState(false);
  const [rootInput, setRootInput] = useState("");
  const [rootSuggestions, setRootSuggestions] = useState<WorkspaceRootSuggestion[]>([]);
  const [storedRoots, setStoredRoots] = useState<string[]>(() => readStoredRoots());
  const [loadingRoots, setLoadingRoots] = useState(false);
  const [noteCreateMenuOpen, setNoteCreateMenuOpen] = useState(false);
  const [fileCreateMenuOpen, setFileCreateMenuOpen] = useState(false);
  const [showSessionRail, setShowSessionRail] = useState(initialLayout.showSessionRail);
  const [showFileExplorer, setShowFileExplorer] = useState(initialLayout.showFileExplorer);
  const [showConversationChunks, setShowConversationChunks] = useState(
    initialLayout.showConversationChunks,
  );
  const [showSidecarPreview, setShowSidecarPreview] = useState(
    initialLayout.showSidecarPreview,
  );
  const [showNotesRail, setShowNotesRail] = useState(initialLayout.showNotesRail);
  const [showNoteEditor, setShowNoteEditor] = useState(initialLayout.showNoteEditor);
  const [showNotesPreview, setShowNotesPreview] = useState(initialLayout.showNotesPreview);
  const [showLearnPanel, setShowLearnPanel] = useState(initialLayout.showLearnPanel);
  const [learnCourse, setLearnCourse] = useState<WorkspaceLearnCourse | null>(null);
  const [learnStatus, setLearnStatus] = useState<"idle" | "loading" | "generating" | "saving" | "error">("idle");
  const [learnError, setLearnError] = useState("");
  const [activeLearnSessionId, setActiveLearnSessionId] = useState<string | null>(null);
  const [leftRailWidth, setLeftRailWidth] = useState(initialLayout.leftRailWidth);
  const [rootPickerWidth, setRootPickerWidth] = useState(initialLayout.rootPickerWidth);
  const [rootPickerHeight, setRootPickerHeight] = useState(initialLayout.rootPickerHeight);
  const [phonePage, setPhonePage] = useState<PhonePage>(
    initialUiState.activePane === "notes" ? "note-preview" : "chat",
  );
  const [wireGuardStatus, setWireGuardStatus] = useState<WorkspaceWireGuardStatus | null>(null);
  const [wireGuardLoading, setWireGuardLoading] = useState(false);
  const [sessionSwipeOffsets, setSessionSwipeOffsets] = useState<Record<string, number>>({});

  const allWorkspaces = useWorkspaceStore((state) => state.workspaces);
  const workspaces = useMemo(() => allWorkspaces.filter((item) => !item.removedFromDan), [allWorkspaces]);
  const hideWorkspace = useWorkspaceStore((state) => state.hideWorkspace);
  const activeWorkspaceId = useWorkspaceStore((state) => state.activeWorkspaceId);
  const createWorkspace = useWorkspaceStore((state) => state.createWorkspace);
  const setActiveWorkspace = useWorkspaceStore((state) => state.setActiveWorkspace);
  const updateWorkspace = useWorkspaceStore((state) => state.updateWorkspace);
  const workspaceSurfaceTheme = useSettingsStore((state) => state.workspaceSurfaceTheme);
  const workspaceSurfaceTone = useSettingsStore((state) => state.workspaceSurfaceTone);
  const workspaceSurfaceThemeClass = workspaceSurfaceThemeClassName(
    workspaceSurfaceTheme,
    workspaceSurfaceTone,
  );
  const workspace = useMemo(
    () => workspaces.find((item) => item.id === activeWorkspaceId),
    [activeWorkspaceId, workspaces],
  );

  const messagesRef = useRef<ChatMessage[]>([]);
  const activeThreadRef = useRef<typeof activeThread>(activeThread);
  const streamRef = useRef<WebSocket | null>(null);
  const agentStreamRef = useRef<WebSocket | null>(null);
  const sessionSelectionSeqRef = useRef(0);
  const creatingSessionRef = useRef(false);
  const autoRestoringThreadIdRef = useRef<string | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const composerDraftsRef = useRef<Record<string, string>>(initialComposerDrafts);
  const noteEditorRef = useRef<HTMLTextAreaElement | null>(null);
  const sessionSwipeRef = useRef<SessionSwipeState | null>(null);
  const suppressSessionClickRef = useRef<string | null>(null);
  const noteContentCacheRef = useRef<Record<string, NoteCacheEntry>>(
    readStoredNoteContentCache(),
  );
  const selfWriteAtRef = useRef<Record<string, number>>({});
  const serverNotesLoadedRef = useRef(false);

  useEffect(() => {
    activeThreadRef.current = activeThread;
  }, [activeThread]);

  const referenceKey = activeThread ? `${activeThread.workflowId}:${activeThread.id}` : "new";
  const composerReference = composerReferences[referenceKey];
  useLayoutEffect(() => {
    const node = composerRef.current;
    if (!node) return;
    const resize = () => {
      const style = getComputedStyle(node);
      const line = parseFloat(style.lineHeight) || 24;
      const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      node.style.height = "auto";
      const maximum = line * 6 + padding;
      node.style.height = `${Math.min(node.scrollHeight, maximum)}px`;
      node.style.overflowY = node.scrollHeight > maximum ? "auto" : "hidden";
    };
    resize();
    let width = node.getBoundingClientRect().width;
    const observer = new ResizeObserver(() => {
      const next = node.getBoundingClientRect().width;
      if (next !== width) { width = next; resize(); }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [input, activePane, activeThread?.id]);

  const commitComposerDraft = useCallback(
    (thread: ThreadIdentity | null | undefined, value: string) => {
      const next = composerDraftsWithValue(composerDraftsRef.current, thread, value);
      if (next === composerDraftsRef.current) return;
      composerDraftsRef.current = next;
      persistComposerDrafts(next);
    },
    [],
  );

  const setComposerInputValue = useCallback(
    (
      value: string,
      options: { persist?: boolean; thread?: ThreadIdentity | null } = {},
    ) => {
      setInput(value);
      if (options.persist === false) return;
      commitComposerDraft(options.thread ?? activeThreadRef.current, value);
    },
    [commitComposerDraft],
  );

  const loadComposerDraftForThread = useCallback(
    (thread: ThreadIdentity | null | undefined) => {
      const draft = composerDraftForThread(composerDraftsRef.current, thread);
      setInput(draft);
      setComposerCaret(draft.length);
      setComposerSuggestionIndex(0);
      setComposerSuggestionSuppressedFor(null);
    },
    [],
  );

  const activeNote = notes.find((note) => note.id === activeNoteId) ?? notes[0] ?? null;
  const activeNoteSection = activeNote ? noteSection(activeNote, notesRoot) : "";
  const catalogNoteItems = useMemo(() => catalogNotes(notes), [notes]);
  const deferredNoteContent = useDeferredValue(activeNote?.content ?? "");
  const parsedActiveNote = useMemo(
    () => extractHugoPage(deferredNoteContent, activeNote?.title || "Note"),
    [activeNote?.title, deferredNoteContent],
  );
  const activeNoteMathEnabled = parsedActiveNote.meta.math === "true";
  const completedLearnSessionIds = useMemo(
    () => new Set(learnCourse?.progress?.completed_session_ids ?? []),
    [learnCourse?.progress?.completed_session_ids],
  );
  const activeLearnSession = useMemo(() => {
    if (!learnCourse?.sessions.length) return null;
    return (
      learnCourse.sessions.find((session) => session.id === activeLearnSessionId) ??
      learnCourse.sessions.find(
        (session) => session.id === learnCourse.progress?.active_session_id,
      ) ??
      learnCourse.sessions[0]
    );
  }, [activeLearnSessionId, learnCourse]);
  useEffect(() => {
    for (const item of workspaces) {
      const canonicalPaths = item.pinnedPaths
        .map((path) => normalizeRootPath(path))
        .filter(Boolean);
      if (
        canonicalPaths.length !== item.pinnedPaths.length ||
        canonicalPaths.some((path, index) => path !== item.pinnedPaths[index])
      ) {
        updateWorkspace(item.id, {
          pinnedPaths: canonicalPaths,
          ...(canonicalPaths[0] ? { name: fileName(canonicalPaths[0]) } : {}),
        });
      }
    }
  }, [updateWorkspace, workspaces]);
  const isKnowledgeGraphPage = useMemo(() => {
    if (!activeNote) return false;
    const layout = parsedActiveNote.meta.layout || activeNote.layout || "";
    const relativePath = noteRelativePath(activeNote, notesRoot);
    return (
      layout === "graph" ||
      /^graph\.mdx?$/i.test(fileName(relativePath)) ||
      noteRoutePath(activeNote, notesRoot) === "/graph/"
    );
  }, [activeNote, notesRoot, parsedActiveNote.meta.layout]);
  const activeNoteMetaItems = useMemo(
    () => noteMetaItems(activeNote, parsedActiveNote),
    [activeNote, parsedActiveNote],
  );
  const pageIdSuggestions = useMemo<PageIdSuggestion[]>(
    () =>
      catalogNoteItems
        .flatMap((note) => {
          const pageID = pageIdFromNote(note);
          if (!pageID) return [];
          return [
            {
              pageID,
              title: note.title,
              path: noteRelativePath(note, notesRoot),
              section: noteSection(note, notesRoot),
            },
          ];
        })
        .sort((a, b) => a.pageID.localeCompare(b.pageID, undefined, { sensitivity: "base" })),
    [catalogNoteItems, notesRoot],
  );
  const activeMentionSuggestions = useMemo(() => {
    if (!noteMention) return [];
    const query = noteMention.query.toLowerCase();
    if (!query.trim()) return [];
    return pageIdSuggestions
      .filter((item) =>
        `${item.pageID} ${item.title} ${item.path} ${item.section}`.toLowerCase().includes(query),
      )
      .slice(0, 8);
  }, [noteMention, pageIdSuggestions]);
  const selectNote = useCallback((
    note: WorkspaceNote,
    originFacet: string | null = null,
  ) => {
    setActiveNoteId(note.id);
    setNoteFacetPage(null);
    setNoteArticleOriginFacet(noteCollectionFacet(originFacet ?? ""));
    setSelectedChunkId(null);
    setPromptLogPreview(null);
    if (note.status === "error" && note.path) {
      setNotes((previous) =>
        previous.map((item) =>
          item.id === note.id
            ? { ...item, content: "", loaded: false, status: "clean", error: undefined }
            : item,
        ),
      );
    }
  }, []);
  const selectNoteFacet = useCallback((facet: string) => {
    setNoteFacet(facet);
    setNoteFacetPage(noteCollectionFacet(facet));
    setNoteArticleOriginFacet(noteCollectionFacet(facet));
    const nextRailView = noteRailViewForFacet(facet);
    if (nextRailView) setNoteRailView(nextRailView);
    setShowNotesPreview(true);
    setPhonePage("note-list");
  }, []);
  const handleNotePreviewClick = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      const targetElement = event.target instanceof Element ? event.target : null;
      const anchor = targetElement?.closest("a");
      const href = anchor?.getAttribute("href") ?? "";
      if (!href.startsWith("#")) return;
      const pageID = decodeURIComponent(href.slice(1));
      const targetNote = notes.find((note) => pageIdFromNote(note) === pageID);
      if (!targetNote) return;
      event.preventDefault();
      selectNote(targetNote);
    },
    [notes, selectNote],
  );
  const workspaceRoot = normalizeRootPath(workspace?.pinnedPaths[0] ?? "");
  const developmentRoot = workspaceRoot || devRoot;
  const activeFileEntry = useMemo(
    () => devFiles.find((entry) => entry.path === activeFilePath) ?? null,
    [activeFilePath, devFiles],
  );
  const previewArtifacts = useMemo(
    () =>
      workspacePreviewArtifacts({
        entries: devFiles,
        root: developmentRoot,
        tasks,
        events: agentEvents,
        texts: messages.map((message) => message.content),
      }),
    [agentEvents, devFiles, developmentRoot, messages, tasks],
  );
  const activePreviewFileEntry = useMemo(
    () =>
      activeFileEntry ??
      previewArtifacts.find(
        (artifact) =>
          artifact.entry.path === activeFilePath ||
          artifact.entry.relative_path === activeFilePath,
      )?.entry ??
      null,
    [activeFileEntry, activeFilePath, previewArtifacts],
  );

  const saveNoteNow = useCallback(async (noteToSave: WorkspaceNote | null, force = false) => {
    if (!noteToSave) return;
    if (!force && noteToSave.status !== "dirty") return;
    if (noteToSave.status === "saving") return;
    if (isTemporaryDraftNote(noteToSave)) {
      if (!force) return;
      const materialized = materializeTemporaryDraftNote({
        note: noteToSave,
        notes,
        root: notesRoot,
        facet: noteFacet,
        activeSection: activeNoteSection,
      });
      if (!materialized) {
        setNotes((previous) =>
          previous.map((note) =>
            note.id === noteToSave.id
              ? { ...note, status: "clean", updatedAt: Date.now() }
              : note,
          ),
        );
        setStatus("Draft staged locally; no notes root is available");
        return;
      }

      setNotes((previous) =>
        previous.map((note) =>
          note.id === noteToSave.id ? { ...note, status: "saving" } : note,
        ),
      );

      try {
        const response = await writeWorkspaceNote(materialized.path, materialized.content);
        const summary = response.note;
        const finalNote: WorkspaceNote = summary
          ? {
              ...noteFromServerSummary(summary),
              content: materialized.content,
              loaded: true,
              status: "clean",
            }
          : {
              ...noteToSave,
              id: `server:${materialized.path}`,
              title: materialized.title,
              path: materialized.path,
              relativePath: materialized.relativePath,
              section: materialized.section,
              source: "server",
              content: materialized.content,
              loaded: true,
              status: "clean",
              updatedAt: Date.now(),
              size: materialized.content.length,
              pageID: materialized.pageID,
              date: materialized.date,
              lastmod: materialized.date,
              draft: false,
              temporary: undefined,
              draftTargetSection: undefined,
              tags: materialized.tags,
              categories: materialized.categories,
              citations: extractPageIdCitations(materialized.content),
              error: undefined,
            };

        selfWriteAtRef.current[materialized.path] = Date.now();
        noteContentCacheRef.current[materialized.path] = {
          content: materialized.content,
          updatedAt: finalNote.updatedAt,
          size: finalNote.size ?? materialized.content.length,
        };
        persistNoteContentCache(noteContentCacheRef.current);
        setNotes((previous) => [
          finalNote,
          ...previous.filter((note) => note.id !== noteToSave.id && note.id !== finalNote.id),
        ]);
        setActiveNoteId((current) => (current === noteToSave.id ? finalNote.id : current));
        setStatus(`Draft saved as ${finalNote.relativePath || materialized.relativePath}`);
      } catch (error) {
        setNotes((previous) =>
          previous.map((note) =>
            note.id === noteToSave.id
              ? {
                  ...note,
                  status: "error",
                  error: error instanceof Error ? error.message : "Draft save failed",
                  updatedAt: Date.now(),
                }
              : note,
          ),
        );
        setStatus(error instanceof Error ? error.message : "Draft save failed");
      }
      return;
    }
    if (noteToSave.source === "local" || !noteToSave.path) {
      setNotes((previous) =>
        previous.map((note) =>
          note.id === noteToSave.id ? { ...note, status: "clean" } : note,
        ),
      );
      return;
    }

    setNotes((previous) =>
      previous.map((note) =>
        note.id === noteToSave.id ? { ...note, status: "saving" } : note,
      ),
    );

    const write: Promise<{ ok: boolean; summary?: WorkspaceNoteSummary }> =
      noteToSave.source === "server"
        ? writeWorkspaceNote(noteToSave.path, noteToSave.content).then((payload) => ({
            ok: true,
            summary: payload.note ?? undefined,
          }))
        : nativeFs.writeFile(noteToSave.path, noteToSave.content).then((ok) => ({ ok }));

    try {
      const { ok, summary } = await write;
      let finalSummary = summary;
      let finalNoteId = noteToSave.id;
      if (ok) {
        selfWriteAtRef.current[noteToSave.path] = Date.now();
        noteContentCacheRef.current[noteToSave.path] = {
          content: noteToSave.content,
          updatedAt: summary ? Math.floor(summary.mtime * 1000) : Date.now(),
          size: summary?.size ?? noteToSave.content.length,
        };
        persistNoteContentCache(noteContentCacheRef.current);

        const titleMove = noteToSave.source === "server" ? noteTitleBundleMove(noteToSave) : null;
        if (titleMove) {
          try {
            const moved = await moveWorkspaceNotePath(titleMove.source, titleMove.destination);
            if (moved.note) {
              finalSummary = moved.note;
              finalNoteId = `server:${moved.note.path}`;
              delete selfWriteAtRef.current[noteToSave.path];
              selfWriteAtRef.current[moved.note.path] = Date.now();
              delete noteContentCacheRef.current[noteToSave.path];
              noteContentCacheRef.current[moved.note.path] = {
                content: noteToSave.content,
                updatedAt: Math.floor(moved.note.mtime * 1000),
                size: moved.note.size,
              };
              persistNoteContentCache(noteContentCacheRef.current);
              setActiveNoteId((current) => (current === noteToSave.id ? finalNoteId : current));
              setStatus(`Saved as ${moved.note.relative_path}`);
            }
          } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            setStatus(`Saved; folder rename skipped (${detail})`);
          }
        }
      }
      setNotes((previous) =>
        previous.map((note) =>
          note.id === noteToSave.id
            ? {
                ...note,
                id: finalNoteId,
                path: finalSummary?.path || note.path,
                title: finalSummary?.title || note.title,
                relativePath: finalSummary?.relative_path || note.relativePath,
                section: finalSummary?.section || note.section,
                layout: finalSummary?.layout ?? note.layout,
                pageID: finalSummary?.page_id || note.pageID,
                date: finalSummary?.date || note.date,
                lastmod: finalSummary?.lastmod || note.lastmod,
                draft:
                  typeof finalSummary?.draft === "boolean" ? finalSummary.draft : note.draft,
                tags: finalSummary?.tags ?? note.tags,
                categories: finalSummary?.categories ?? note.categories,
                citations: finalSummary?.citations ?? note.citations,
                status: ok ? "clean" : "error",
                updatedAt: finalSummary ? Math.floor(finalSummary.mtime * 1000) : Date.now(),
                size: finalSummary?.size ?? noteToSave.content.length,
                error: ok ? undefined : note.error,
              }
            : note,
        ),
      );
    } catch {
      setNotes((previous) =>
        previous.map((note) =>
          note.id === noteToSave.id
            ? { ...note, status: "error", updatedAt: Date.now() }
            : note,
        ),
      );
    }
  }, [activeNoteSection, noteFacet, notes, notesRoot]);

  useEffect(() => {
    if (!showLearnPanel || !activeNote?.path || isTemporaryDraftNote(activeNote)) {
      setLearnCourse(null);
      setActiveLearnSessionId(null);
      setLearnStatus("idle");
      setLearnError("");
      return;
    }
    let cancelled = false;
    setLearnStatus("loading");
    setLearnError("");
    void getWorkspaceNoteLearnCourse(activeNote.path)
      .then((payload) => {
        if (cancelled) return;
        const course = payload.course;
        setLearnCourse(course);
        setActiveLearnSessionId(
          course?.progress?.active_session_id || course?.sessions[0]?.id || null,
        );
        setLearnStatus("idle");
      })
      .catch((error) => {
        if (cancelled) return;
        const message = learnPanelMessage(error, LEARN_COURSE_NOT_GENERATED_MESSAGE);
        setLearnCourse(null);
        setActiveLearnSessionId(null);
        setLearnStatus(message === LEARN_COURSE_NOT_GENERATED_MESSAGE ? "idle" : "error");
        setLearnError(message);
      });
    return () => {
      cancelled = true;
    };
  }, [activeNote?.path, activeNote?.updatedAt, showLearnPanel]);

  const generateLearnCourse = useCallback(async () => {
    if (!activeNote?.path || isTemporaryDraftNote(activeNote)) return;
    setLearnStatus("generating");
    setLearnError("");
    try {
      if (activeNote.status === "dirty") {
        await saveNoteNow(activeNote, true);
      }
      const payload = await generateWorkspaceNoteLearnCourse(activeNote.path);
      setLearnCourse(payload.course);
      setActiveLearnSessionId(
        payload.course.progress?.active_session_id || payload.course.sessions[0]?.id || null,
      );
      setLearnStatus("idle");
      setStatus("Learning sessions ready");
    } catch (error) {
      const message = learnPanelMessage(error, "Session generation failed");
      setLearnStatus("error");
      setLearnError(message);
      setStatus(message);
    }
  }, [activeNote, saveNoteNow]);

  const updateLearnProgress = useCallback(
    async (activeSessionId: string | null, completedIds: string[]) => {
      if (!activeNote?.path || !learnCourse) return;
      setLearnStatus("saving");
      setLearnError("");
      try {
        const payload = await updateWorkspaceNoteLearnProgress(activeNote.path, {
          active_session_id: activeSessionId,
          completed_session_ids: completedIds,
        });
        setLearnCourse(payload.course);
        setActiveLearnSessionId(activeSessionId || payload.course.sessions[0]?.id || null);
        setLearnStatus("idle");
      } catch (error) {
        const message = learnPanelMessage(error, "Could not save progress");
        setLearnStatus("error");
        setLearnError(message);
      }
    },
    [activeNote?.path, learnCourse],
  );

  const selectLearnSession = useCallback(
    (sessionId: string) => {
      setActiveLearnSessionId(sessionId);
      if (!learnCourse) return;
      void updateLearnProgress(sessionId, learnCourse.progress?.completed_session_ids ?? []);
    },
    [learnCourse, updateLearnProgress],
  );

  const toggleLearnSession = useCallback(
    (sessionId: string) => {
      if (!learnCourse) return;
      const completed = new Set(learnCourse.progress?.completed_session_ids ?? []);
      if (completed.has(sessionId)) completed.delete(sessionId);
      else completed.add(sessionId);
      void updateLearnProgress(activeLearnSessionId || sessionId, [...completed]);
    },
    [activeLearnSessionId, learnCourse, updateLearnProgress],
  );

  useEffect(() => {
    if (!rootEditing) setRootInput(developmentRoot);
  }, [developmentRoot, rootEditing]);

  useEffect(() => {
    if (allWorkspaces.length === 0) createWorkspace("DAN Workspace", "chat");
  }, [createWorkspace, allWorkspaces.length]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    persistNotes(notes, activeNoteId);
  }, [activeNoteId, notes]);

  useEffect(() => {
    persistThreadWorkspaces(threadWorkspaces);
  }, [threadWorkspaces]);

  useEffect(() => {
    persistSessionResponseSeen(sessionResponseSeen);
  }, [sessionResponseSeen]);

  useEffect(() => {
    setNoteMentionIndex(0);
  }, [noteMention?.query]);

  useEffect(() => {
    if (noteMentionIndex >= activeMentionSuggestions.length) setNoteMentionIndex(0);
  }, [activeMentionSuggestions.length, noteMentionIndex]);

  useEffect(() => {
    persistLayout({
      showSessionRail,
      showFileExplorer,
      showConversationChunks,
      showSidecarPreview,
      showNotesRail,
      showNoteEditor,
      showNotesPreview,
      showLearnPanel,
      leftRailWidth,
      rootPickerWidth,
      rootPickerHeight,
    });
  }, [
    showSessionRail,
    showFileExplorer,
    showConversationChunks,
    showSidecarPreview,
    showNotesRail,
    showNoteEditor,
    showNotesPreview,
    showLearnPanel,
    leftRailWidth,
    rootPickerWidth,
    rootPickerHeight,
  ]);

  useEffect(() => {
    persistUiState({
      activePane,
      selectedChunkId,
      activeFilePath,
      threadQuery,
      noteQuery,
      noteFacet,
      noteFacetPage,
      noteArticleOriginFacet,
      noteRailView,
      devFileQuery,
      expandedFileDirs,
      expandedNoteFolders,
      collapsedThreadGroups,
    });
  }, [
    activeFilePath,
    activePane,
    collapsedThreadGroups,
    devFileQuery,
    expandedFileDirs,
    expandedNoteFolders,
    noteFacet,
    noteFacetPage,
    noteArticleOriginFacet,
    noteQuery,
    noteRailView,
    selectedChunkId,
    threadQuery,
  ]);

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await listChatV2Threads());
    } catch {
      setThreads([]);
    }
  }, []);

  const mergeBackgroundTasks = useCallback((nextTasks: ChatV2TaskSnapshot[]) => {
    if (nextTasks.length === 0) return;
    setBackgroundTasks((previous) => mergeTaskSnapshots(previous, nextTasks).slice(0, 160));
  }, []);

  const refreshBackgroundTasks = useCallback(async () => {
    try {
      const next = await listChatV2Tasks({ limit: 160 });
      setBackgroundTasks(next);
      return next;
    } catch {
      setBackgroundTasks([]);
      return [];
    }
  }, []);

  useEffect(() => {
    void refreshThreads();
  }, [refreshThreads]);

  useEffect(() => {
    void refreshBackgroundTasks();
    const timer = window.setInterval(() => void refreshBackgroundTasks(), 10_000);
    return () => window.clearInterval(timer);
  }, [refreshBackgroundTasks]);

  const refreshTasks = useCallback(async (threadId: string) => {
    const isSelectedThread = () => activeThreadRef.current?.id === threadId;
    try {
      const next = await listChatV2ThreadTasks(threadId);
      mergeBackgroundTasks(next);
      if (isSelectedThread()) setTasks(next);
      return next;
    } catch {
      if (isSelectedThread()) setTasks([]);
      return [];
    }
  }, [mergeBackgroundTasks]);

  const clearActiveSessionView = useCallback((statusText?: string) => {
    streamRef.current?.close();
    streamRef.current = null;
    agentStreamRef.current?.close();
    agentStreamRef.current = null;
    setActiveThread(null);
    setLoadingThreadId(null);
    setMessages([]);
    messagesRef.current = [];
    setPendingAssistantIds({});
    setTasks([]);
    setAgentEvents([]);
    setSelectedChunkId(null);
    setSelectedBlueprintNodeId(null);
    loadComposerDraftForThread(null);
    if (statusText) setStatus(statusText);
  }, [loadComposerDraftForThread]);

  const clearStoredThreadSelection = useCallback((thread: ThreadIdentity) => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(LAST_THREAD_STORAGE_KEY) || "null") as
        | { threadId?: string; workflowId?: string }
        | null;
      if (savedThreadSelectionMatches(saved, thread)) {
        window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
      }
    } catch {
      window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
    }
  }, []);

  const removeThreadFromWorkspaceSlots = useCallback(
    (threadId: string) => {
      for (const item of workspaces) {
        const nextOpenThreadIds = item.openThreadIds.filter((id) => id !== threadId);
        const nextActiveThreadId = item.activeThreadId === threadId ? null : item.activeThreadId;
        if (
          nextActiveThreadId !== item.activeThreadId ||
          nextOpenThreadIds.length !== item.openThreadIds.length
        ) {
          updateWorkspace(item.id, {
            activeThreadId: nextActiveThreadId,
            openThreadIds: nextOpenThreadIds,
          });
        }
      }
    },
    [updateWorkspace, workspaces],
  );

  useEffect(() => {
    const saved = (() => {
      try {
        return JSON.parse(window.localStorage.getItem(LAST_THREAD_STORAGE_KEY) || "null") as
          | { threadId?: string; workflowId?: string }
          | null;
      } catch {
        return null;
      }
    })();
    const targetThreadId = workspace?.activeThreadId || saved?.threadId;
    const targetWorkflowId = workspace?.activeThreadId ? undefined : saved?.workflowId;
    const archivedTarget = findThreadTarget(threads, targetThreadId, targetWorkflowId);
    if (archivedTarget?.archived) {
      clearStoredThreadSelection(archivedTarget);
      removeThreadFromWorkspaceSlots(archivedTarget.id);
      return;
    }
    const match = restorableThreadTarget(threads, targetThreadId, targetWorkflowId);
    if (
      !shouldAutoRestoreSession({
        activeThreadPresent: Boolean(activeThread),
        creatingSession: creatingSessionRef.current,
        loadingThreadId,
        restoringThreadId: autoRestoringThreadIdRef.current,
        targetThreadId: match?.id,
        threadCount: threads.length,
      })
    ) {
      return;
    }
    if (!match) return;
    const selectionSeq = ++sessionSelectionSeqRef.current;
    autoRestoringThreadIdRef.current = match.id;
    void getChatV2Thread(match.workflow_id, match.id)
      .then(async (thread) => {
        const history = await loadSuperDanThreadHistory(thread.id, thread.title, thread.updated_at).catch(() => ({
          tasks: [] as ChatV2TaskSnapshot[],
          events: [] as ChatV2AgentRunEvent[],
          messages: [] as ChatMessage[],
        }));
        if (sessionSelectionSeqRef.current !== selectionSeq) return;
        const restoredMessages = await restoreFullNativeMessages(thread.messages.length > 0 ? thread.messages : history.messages);
        const restoredWorkspaceId =
          (workspace?.activeThreadId === thread.id ? workspace.id : "") ||
          workspaceIdForTasks(history.tasks, workspaces);
        if (restoredWorkspaceId) {
          const restoredWorkspace = workspaces.find((item) => item.id === restoredWorkspaceId);
          setActiveWorkspace(restoredWorkspaceId);
          setThreadWorkspaces((previous) => ({
            ...previous,
            [threadWorkspaceKey(thread.workflow_id, thread.id)]: restoredWorkspaceId,
          }));
          updateWorkspace(restoredWorkspaceId, {
            activeThreadId: thread.id,
            openThreadIds: restoredWorkspace?.openThreadIds.includes(thread.id)
              ? restoredWorkspace.openThreadIds
              : [...(restoredWorkspace?.openThreadIds ?? []), thread.id],
          });
        }
        setPendingAssistantIds({});
        setSelectedChunkId(null);
        setSelectedBlueprintNodeId(null);
        setPromptLogPreview(null);
        const restoredThread = {
          id: thread.id,
          workflowId: thread.workflow_id,
          title: thread.title,
        };
        setActiveThread(restoredThread);
        loadComposerDraftForThread(restoredThread);
        setMessages(restoredMessages);
        messagesRef.current = restoredMessages;
        setTasks(history.tasks);
        mergeBackgroundTasks(history.tasks);
        setAgentEvents(history.events);
        if (thread.messages.length === 0 && restoredMessages.length > 0) {
          void saveChatV2Thread(thread.workflow_id, thread.id, {
            messages: restoredMessages,
            mode: "agent",
          }).then(refreshThreads);
        }
        window.localStorage.setItem(
          LAST_THREAD_STORAGE_KEY,
          JSON.stringify({ threadId: thread.id, workflowId: thread.workflow_id }),
        );
      })
      .catch(() => {
        if (sessionSelectionSeqRef.current === selectionSeq) {
          setStatus("Session restore failed");
        }
      })
      .finally(() => {
        if (autoRestoringThreadIdRef.current === match.id) {
          autoRestoringThreadIdRef.current = null;
        }
        if (sessionSelectionSeqRef.current === selectionSeq) setLoadingThreadId(null);
      });
  }, [
    activeThread,
    clearStoredThreadSelection,
    loadComposerDraftForThread,
    loadingThreadId,
    mergeBackgroundTasks,
    removeThreadFromWorkspaceSlots,
    refreshThreads,
    setActiveWorkspace,
    threads,
    updateWorkspace,
    workspace?.activeThreadId,
    workspace?.id,
    workspaces,
  ]);

  useEffect(() => {
    const archivedSelection = activeThreadArchivedSummary(activeThread, threads);
    if (!archivedSelection) return;
    sessionSelectionSeqRef.current += 1;
    clearStoredThreadSelection(archivedSelection);
    removeThreadFromWorkspaceSlots(archivedSelection.id);
    clearActiveSessionView("Session archived");
  }, [
    activeThread,
    clearActiveSessionView,
    clearStoredThreadSelection,
    removeThreadFromWorkspaceSlots,
    threads,
  ]);

  const addServerNotes = useCallback((summaries: WorkspaceNoteSummary[]) => {
    setNotes((previous) => {
      const byId = new Map(previous.map((note) => [note.id, note]));
      const serverNotes = summaries.map(noteFromServerSummary);
      for (const note of serverNotes) {
        const existing = byId.get(note.id);
        const isLocalEdit =
          existing?.status === "dirty" || existing?.status === "saving";
        const selfWriteRecent = note.path
          ? Date.now() - (selfWriteAtRef.current[note.path] ?? 0) < 5000
          : false;
        const serverChanged =
          Boolean(existing?.loaded) &&
          !isLocalEdit &&
          !selfWriteRecent &&
          (existing?.updatedAt !== note.updatedAt || existing?.size !== note.size);
        const cached =
          note.path &&
          noteContentCacheRef.current[note.path]?.updatedAt === note.updatedAt &&
          noteContentCacheRef.current[note.path]?.size === note.size
            ? noteContentCacheRef.current[note.path]
            : null;
        byId.set(
          note.id,
          existing
            ? {
                ...note,
                content: serverChanged ? cached?.content ?? "" : existing.content,
                loaded: serverChanged ? Boolean(cached) : existing.loaded,
                status: isLocalEdit
                  ? existing.status
                  : serverChanged && !cached
                    ? "clean"
                    : existing.loaded
                      ? existing.status
                      : "clean",
                error: isLocalEdit || (!serverChanged && existing.loaded)
                  ? existing.error
                  : undefined,
              }
            : note,
        );
      }
      const localNotes = previous.filter((note) => note.source === "local");
      const next = [...serverNotes.map((note) => byId.get(note.id)!), ...localNotes];
      if (serverNotes.length > 0) {
        setActiveNoteId((current) => {
          const currentNote = current ? byId.get(current) : null;
          return currentNote?.source === "server" ||
            currentNote?.source === "disk" ||
            (currentNote && isTemporaryDraftNote(currentNote))
            ? current
            : serverNotes[0].id;
        });
      }
      return next.length > 0 ? next : previous;
    });
  }, []);

  useEffect(() => {
    if (serverNotesLoadedRef.current) return;
    serverNotesLoadedRef.current = true;
    void listWorkspaceNotes()
      .then((payload) => {
        setNotesRoot(payload.root || "");
        addServerNotes(payload.notes ?? []);
      })
      .catch(() => {
        // Electron-only sessions can still discover notes from pinned folders.
      });
  }, [addServerNotes]);

  const refreshDevFiles = useCallback(async () => {
    try {
      const payload = await listWorkspaceFileTree(workspaceRoot || undefined);
      setDevRoot(payload.root || workspaceRoot || "");
      setDevFiles(payload.entries ?? []);
      setActiveFilePath((current) => {
        if (current && (payload.entries ?? []).some((entry) => entry.path === current)) {
          return current;
        }
        return null;
      });
    } catch {
      setDevFiles([]);
    }
  }, [workspaceRoot]);

  useEffect(() => {
    void refreshDevFiles();
  }, [refreshDevFiles]);

  const refreshWorkspaceSkills = useCallback(async () => {
    try {
      const payload = await listWorkspaceSkillSuggestions(workspaceRoot || undefined);
      setWorkspaceSkills(payload.skills ?? []);
    } catch {
      setWorkspaceSkills([]);
    }
  }, [workspaceRoot]);

  useEffect(() => {
    void refreshWorkspaceSkills();
  }, [refreshWorkspaceSkills]);

  useEffect(() => {
    if (!activeFilePath || !activePreviewFileEntry) {
      setActiveFileContent("");
      setActiveFileStatus("idle");
      return;
    }
    if (!workspaceFilePreviewNeedsText(activePreviewFileEntry)) {
      setActiveFileContent("");
      setActiveFileStatus("idle");
      return;
    }
    let cancelled = false;
    setActiveFileStatus("loading");
    void readWorkspaceFile(activePreviewFileEntry.path, developmentRoot || undefined)
      .then((payload) => {
        if (cancelled) return;
        setActiveFileContent(
          payload.truncated ? `${payload.content}\n\n... file truncated in preview ...` : payload.content,
        );
        setActiveFileStatus("idle");
      })
      .catch((error) => {
        if (cancelled) return;
        setActiveFileContent(error instanceof Error ? error.message : "File preview failed.");
        setActiveFileStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [activeFilePath, activePreviewFileEntry, developmentRoot]);

  useEffect(() => {
    if (!activeNote || activeNote.loaded || !activeNote.path) return;
    let cancelled = false;
    const cached = noteContentCacheRef.current[activeNote.path];
    if (
      cached &&
      cached.updatedAt === activeNote.updatedAt &&
      cached.size === (activeNote.size ?? cached.size)
    ) {
      setNotes((previous) =>
        previous.map((note) =>
          note.id === activeNote.id
            ? {
                ...note,
                content: cached.content,
                loaded: true,
                status: "clean",
                error: undefined,
              }
            : note,
        ),
      );
      return;
    }
    setNotes((previous) =>
      previous.map((note) =>
        note.id === activeNote.id
          ? { ...note, status: "loading", error: undefined }
          : note,
      ),
    );
    const read: Promise<{ content: string | null; summary?: WorkspaceNoteSummary }> =
      activeNote.source === "server"
        ? readWorkspaceNote(activeNote.path, { timeoutMs: 30000 }).then((payload) => ({
            content: payload.content,
            summary: payload.note,
          }))
        : nativeFs.readFile(activeNote.path).then((content) => ({ content }));
    void read.then(({ content, summary }) => {
      if (cancelled) return;
      if (activeNote.path) {
        noteContentCacheRef.current[activeNote.path] = {
          content: content ?? "",
          updatedAt: activeNote.updatedAt,
          size: activeNote.size ?? (content ?? "").length,
        };
        persistNoteContentCache(noteContentCacheRef.current);
      }
      setNotes((previous) =>
        previous.map((note) =>
          note.id === activeNote.id
            ? {
                ...note,
                title: summary?.title || note.title,
                relativePath: summary?.relative_path || note.relativePath,
                section: summary?.section || note.section,
                layout: summary?.layout ?? note.layout,
                pageID: summary?.page_id || note.pageID,
                date: summary?.date || note.date,
                lastmod: summary?.lastmod || note.lastmod,
                draft: typeof summary?.draft === "boolean" ? summary.draft : note.draft,
                tags: summary?.tags ?? note.tags,
                categories: summary?.categories ?? note.categories,
                citations: summary?.citations ?? note.citations,
                size: summary?.size ?? note.size,
                content: content ?? "",
                loaded: true,
                status: "clean",
                error: undefined,
                updatedAt: Date.now(),
              }
            : note,
        ),
      );
    }).catch((error) => {
      if (cancelled) return;
      setNotes((previous) =>
        previous.map((note) =>
          note.id === activeNote.id
            ? {
                ...note,
                content: "",
                loaded: true,
                status: "error",
                error: error instanceof Error ? error.message : "Note load failed.",
                updatedAt: Date.now(),
              }
            : note,
        ),
      );
    });
    return () => {
      cancelled = true;
    };
  }, [activeNote?.id, activeNote?.loaded, activeNote?.path, activeNote?.source]);

  useEffect(() => {
    if (!activeNote?.path || activeNote.source !== "disk" || !isElectron()) return;
    let cancelled = false;
    void nativeWatch.start(activeNote.path);
    const off = nativeWatch.onChange((path) => {
      if (cancelled || path !== activeNote.path) return;
      if (Date.now() - (selfWriteAtRef.current[path] ?? 0) < 1200) return;
      void nativeFs.readFile(path).then((content) => {
        if (cancelled || content == null) return;
        noteContentCacheRef.current[path] = {
          content,
          updatedAt: Date.now(),
          size: content.length,
        };
        persistNoteContentCache(noteContentCacheRef.current);
        setNotes((previous) =>
          previous.map((note) =>
            note.id === activeNote.id
              ? {
                  ...note,
                  content,
                  loaded: true,
                  status: "clean",
                  error: undefined,
                  updatedAt: Date.now(),
                }
              : note,
          ),
        );
      });
    });
    return () => {
      cancelled = true;
      off();
      void nativeWatch.stop(activeNote.path!);
    };
  }, [activeNote?.id, activeNote?.path]);

  useEffect(() => {
    if (!activeNote || activeNote.status !== "dirty") return;
    const timer = window.setTimeout(() => {
      void saveNoteNow(activeNote);
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [activeNote, saveNoteNow]);

  const messageChunks = useMemo<WorkspaceChunk[]>(() => {
    const visibleMessages = messages.slice(-16);
    const inferredRunRefForMessage = (index: number) => {
      const message = visibleMessages[index];
      if (!message || message.role !== "user" || message.taskRunRef?.runId) {
        return message?.taskRunRef ?? null;
      }
      for (let cursor = index + 1; cursor < visibleMessages.length; cursor += 1) {
        const candidate = visibleMessages[cursor];
        if (!candidate) continue;
        if (candidate.role === "user") break;
        if (candidate.taskRunRef?.runId || candidate.taskRunRef?.taskId) return candidate.taskRunRef ?? null;
      }
      return message.taskRunRef ?? null;
    };
    return visibleMessages.map((message, index) => {
      const runRef = inferredRunRefForMessage(index);
      return {
        id: `message:${message.id}`,
        kind: "chat",
        title:
          message.role === "user"
            ? "You · Request"
            : pendingAssistantIds[message.id]
              ? "Super DAN · Working"
              : message.taskRunRef?.runId
                ? "DAN · Answer"
                : "DAN · Chat",
        body:
          (message.content ? displayChatContent(message.content) : "") ||
          (pendingAssistantIds[message.id] ? "Waiting for output..." : "_No response captured._"),
        status:
          message.role === "assistant" && !message.content
            ? pendingAssistantIds[message.id]
              ? "running"
              : "error"
            : "clean",
        meta: message.role,
        role: message.role,
        taskId: runRef?.taskId,
        runId: runRef?.runId,
      };
    });
  }, [messages, pendingAssistantIds]);

  const eventChunks = useMemo<WorkspaceChunk[]>(
    () => compactAgentRunChunks(agentEvents),
    [agentEvents],
  );

  const chunks = useMemo(
    () => messageChunks.concat(eventChunks),
    [eventChunks, messageChunks],
  );
  const userConversationChunks = useMemo(
    () => conversationUserChunks(chunks),
    [chunks],
  );

  const selectedChunk = chunks.find((chunk) => chunk.id === selectedChunkId) ?? null;

  useEffect(() => {
    if (!selectedChunkId || chunks.length === 0) return;
    if (!chunks.some((chunk) => chunk.id === selectedChunkId)) setSelectedChunkId(null);
  }, [chunks, selectedChunkId]);

  const settledTasks = useMemo(
    () => settleTaskSnapshotsFromEvents(tasks, agentEvents),
    [agentEvents, tasks],
  );
  const selectedThreadBackgroundTasks = useMemo(
    () =>
      activeThread
        ? backgroundTasks.filter((task) => task.thread_id === activeThread.id)
        : [],
    [activeThread, backgroundTasks],
  );
  const workPanelTasks = useMemo(
    () => mergeTaskSnapshots(selectedThreadBackgroundTasks, settledTasks),
    [selectedThreadBackgroundTasks, settledTasks],
  );
  const activeRunningTask = selectActiveRunningTask(workPanelTasks);
  const activeRunId = activeRunningTask ? taskRunId(activeRunningTask) : "";
  const teamParentIds = useMemo(() => [...new Set(tasks.map(taskRunId).filter(Boolean))], [tasks]);
  const team = useTeamWorkers(teamParentIds, Boolean(activeRunningTask));
  const sessionStatusTasks = useMemo(
    () => mergeTaskSnapshots(backgroundTasks, workPanelTasks),
    [backgroundTasks, workPanelTasks],
  );
  const runningTaskByThreadId = useMemo(
    () => runningTaskMapByThreadId(sessionStatusTasks),
    [sessionStatusTasks],
  );
  const tasksByThreadId = useMemo(() => {
    const grouped = new Map<string, ChatV2TaskSnapshot[]>();
    for (const task of sessionStatusTasks) {
      if (!task.thread_id) continue;
      grouped.set(task.thread_id, [...(grouped.get(task.thread_id) ?? []), task]);
    }
    return grouped;
  }, [sessionStatusTasks]);
  const taskWorkspaceByThreadId = useMemo(() => {
    const entries = [...tasksByThreadId.entries()].flatMap(([threadId, threadTasks]) => {
      const workspaceId = workspaceIdForTasks(threadTasks, workspaces);
      return workspaceId ? [[threadId, workspaceId] as const] : [];
    });
    return new Map(entries);
  }, [tasksByThreadId, workspaces]);
  const taskWorkspaceRootByThreadId = useMemo(() => {
    const entries = [...tasksByThreadId.entries()].flatMap(([threadId, threadTasks]) => {
      const workspaceRoot = workspaceRootForTasks(threadTasks);
      return workspaceRoot ? [[threadId, workspaceRoot] as const] : [];
    });
    return new Map(entries);
  }, [tasksByThreadId]);

  const markSessionResponseSeen = useCallback(
    (thread: ThreadIdentity, threadTasks: ChatV2TaskSnapshot[]) => {
      const readyAt = sessionReadyResponseAt(threadTasks);
      if (!readyAt) return;
      const key = threadWorkspaceKey(threadIdentityWorkflowId(thread), thread.id);
      setSessionResponseSeen((previous) => {
        const currentTimestamp = timestampValue(previous[key], 0);
        const readyTimestamp = timestampValue(readyAt, 0);
        if (readyTimestamp <= currentTimestamp) return previous;
        return { ...previous, [key]: readyAt };
      });
    },
    [],
  );

  useEffect(() => {
    if (!activeThread) return;
    markSessionResponseSeen(activeThread, workPanelTasks);
  }, [activeThread, markSessionResponseSeen, workPanelTasks]);

  const noteFacetOptions = useMemo(() => {
    const sections = new Map<string, number>();
    const tags = new Map<string, number>();
    for (const note of catalogNoteItems) {
      const section = noteSection(note, notesRoot);
      sections.set(section, (sections.get(section) ?? 0) + 1);
      for (const tag of note.tags) tags.set(tag, (tags.get(tag) ?? 0) + 1);
    }
    const sortEntries = (entries: IterableIterator<[string, number]>) =>
      [...entries].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    return {
      sections: sortEntries(sections.entries()),
      tags: sortEntries(tags.entries()),
    };
  }, [catalogNoteItems, notesRoot]);
  const visibleTagFacetOptions = useMemo(() => {
    const query = noteQuery.trim().toLowerCase();
    if (!query) return noteFacetOptions.tags;
    return noteFacetOptions.tags.filter(([tag]) => tag.toLowerCase().includes(query));
  }, [noteFacetOptions.tags, noteQuery]);
  const visibleSectionFacetOptions = useMemo(() => {
    const query = noteQuery.trim().toLowerCase();
    if (!query) return noteFacetOptions.sections;
    return noteFacetOptions.sections.filter(([section]) =>
      section.toLowerCase().includes(query),
    );
  }, [noteFacetOptions.sections, noteQuery]);

  const visibleNotes = useMemo(() => {
    const query = noteQuery.trim().toLowerCase();
    return catalogNoteItems.filter((note) => {
      if (!noteMatchesFacet(note, notesRoot, noteFacet)) return false;
      if (!query) return true;
      return `${note.title} ${note.path ?? ""} ${note.relativePath ?? ""} ${note.pageID ?? ""} ${noteSection(note, notesRoot)} ${note.tags.join(" ")} ${note.categories.join(" ")}`
        .toLowerCase()
        .includes(query);
    });
  }, [catalogNoteItems, noteFacet, noteQuery, notesRoot]);
  const recentModifiedNoteItems = useMemo(
    () => recentModifiedNotes(catalogNoteItems, notesRoot, RECENT_MODIFIED_LIMIT),
    [catalogNoteItems, notesRoot],
  );
  const noteWorkingCards = useMemo(
    () =>
      workingNoteCards(
        activeNote,
        notesRoot,
        activePane === "notes" ? activeRunningTask : null,
      ),
    [activeNote, activePane, activeRunningTask, notesRoot],
  );
  const noteTree = useMemo(
    () => buildNoteTree(visibleNotes, notesRoot),
    [notesRoot, visibleNotes],
  );
  const activeVisibleNoteIndex = activeNote
    ? visibleNotes.findIndex((note) => note.id === activeNote.id)
    : -1;
  const previousNote =
    activeVisibleNoteIndex > 0 ? visibleNotes[activeVisibleNoteIndex - 1] : null;
  const nextNote =
    activeVisibleNoteIndex >= 0 && activeVisibleNoteIndex < visibleNotes.length - 1
      ? visibleNotes[activeVisibleNoteIndex + 1]
      : null;
  const upperCollectionFacet = noteArticleUpFacet(
    activeNote,
    notesRoot,
    noteArticleOriginFacet,
  );

  useEffect(() => {
    if (!activeNote) return;
    const parentKeys = noteParentKeys(activeNote, notesRoot);
    if (parentKeys.length === 0) return;
    setExpandedNoteFolders((previous) => {
      let changed = false;
      const next = { ...previous };
      for (const key of parentKeys) {
        const folderId = `folder:${key}`;
        if (!next[folderId]) {
          next[folderId] = true;
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [activeNote, notesRoot]);

  const fileTree = useMemo(() => buildFileTree(devFiles), [devFiles]);
  const devFileCount = useMemo(
    () => devFiles.filter((entry) => !entry.is_directory).length,
    [devFiles],
  );
  const workspaceSlots = useMemo(() => workspaces.slice(0, 9), [workspaces]);
  const sessionGroups = useMemo(
    () =>
      buildSessionGroups({
        threads,
        workspaces: allWorkspaces,
        threadQuery,
        threadWorkspaces,
        taskWorkspaceByThreadId,
        taskWorkspaceRootByThreadId,
      }),
    [taskWorkspaceByThreadId, taskWorkspaceRootByThreadId, threadQuery, threadWorkspaces, threads, allWorkspaces],
  );
  const workbenchProjects = useMemo(() => {
    const groups = buildSessionGroups({ threads, workspaces: allWorkspaces, threadQuery: "", threadWorkspaces, taskWorkspaceByThreadId, taskWorkspaceRootByThreadId });
    return workspaces.map((item) => ({
      id: item.id, name: item.name || workspaceDisplayName(item), root: item.pinnedPaths[0] || "",
      sessions: groups.filter((group) => !group.archived && group.workspaceId === item.id)
        .flatMap((group) => group.threads).filter((thread) => !thread.archived)
        .map((thread) => ({ id: threadWorkspaceKey(thread.workflow_id, thread.id), title: thread.title, createdAt: thread.created_at })),
    }));
  }, [threads, workspaces, allWorkspaces, threadWorkspaces, taskWorkspaceByThreadId, taskWorkspaceRootByThreadId]);
  const rootOptions = useMemo(
    () =>
      mergeRootSuggestions(
        [
          rootSuggestion(developmentRoot, "workspace"),
          rootSuggestion(devRoot, "current"),
          ...workspaceSlots.flatMap((item) =>
            item.pinnedPaths.map((path) => rootSuggestion(path, "workspace")),
          ),
          ...storedRoots.map((path) => rootSuggestion(path, "recent")),
        ],
        rootSuggestions,
      ),
    [developmentRoot, devRoot, rootSuggestions, storedRoots, workspaceSlots],
  );
  const rootBrowsePath = normalizeRootPath(rootInput || developmentRoot || devRoot);
  const rootParentPath = parentRootPath(rootBrowsePath);
  const hasActiveRun = Boolean(activeRunId && activeRunningTask);
  const [nativeWorkerProfiles, setNativeWorkerProfiles] = useState<WorkerProfiles>(loadWorkerProfiles);
  useEffect(() => { localStorage.setItem("dan.nativeWorkerProfiles.v1", JSON.stringify(nativeWorkerProfiles)); }, [nativeWorkerProfiles]);
  const [leadProfiles, setLeadProfiles] = useState<WorkerProfiles>(() => loadWorkerProfiles("dan.leadProfiles.v1"));
  useEffect(() => { localStorage.setItem("dan.leadProfiles.v1", JSON.stringify(leadProfiles)); }, [leadProfiles]);
  const [selectedAgentId, setSelectedAgentId] = useState<WorkspaceAgentSelectionId>(() => {
    return readStoredWorkspaceSelection().agentId;
  });
  const [autonomyMode, setAutonomyMode] = useState<WorkspaceAutonomyMode>(() =>
    readStoredWorkspaceAutonomyMode(),
  );
  const [modelSelectionsByAgent, setModelSelectionsByAgent] = useState<WorkspaceModelSelectionByAgent>(() => {
    return readStoredWorkspaceSelection().modelSelectionsByAgent;
  });
  const [autonomyMenuOpen, setAutonomyMenuOpen] = useState(false);
  const selectedAgentOption = useMemo(
    () => workspaceAgentOptionForId(selectedAgentId),
    [selectedAgentId],
  );
  const selectedModelId = workspaceModelOptionForId(
    modelSelectionsByAgent[selectedAgentId],
    selectedAgentId,
  ).id;
  const selectedModelOption = useMemo(
    () => workspaceModelOptionForId(selectedModelId, selectedAgentId),
    [selectedAgentId, selectedModelId],
  );
  const selectedAutonomyOption = useMemo(
    () => workspaceAutonomyOptionForId(autonomyMode),
    [autonomyMode],
  );
  const persistedModelSelectionsByAgent = useMemo(() => {
    const next = defaultWorkspaceModelSelectionsByAgent();
    for (const agentOption of WORKSPACE_AGENT_OPTIONS) {
      next[agentOption.id] = workspaceModelOptionForId(
        modelSelectionsByAgent[agentOption.id],
        agentOption.id,
      ).id;
    }
    next[selectedAgentId] = selectedModelOption.id;
    return next;
  }, [modelSelectionsByAgent, selectedAgentId, selectedModelOption.id]);
  useEffect(() => {
    window.localStorage.setItem(AGENT_SELECTION_STORAGE_KEY, selectedAgentId);
  }, [selectedAgentId]);

  useEffect(() => {
    window.localStorage.setItem(AUTONOMY_MODE_STORAGE_KEY, autonomyMode);
  }, [autonomyMode]);
  useEffect(() => {
    window.localStorage.setItem(MODEL_SELECTION_STORAGE_KEY, selectedModelOption.id);
    window.localStorage.setItem(
      MODEL_SELECTIONS_BY_AGENT_STORAGE_KEY,
      JSON.stringify(persistedModelSelectionsByAgent),
    );
  }, [persistedModelSelectionsByAgent, selectedModelOption.id]);
  const [elapsedCounterNow, setElapsedCounterNow] = useState(() => Date.now());
  useEffect(() => {
    if (!activeRunningTask) return undefined;
    const tick = () => setElapsedCounterNow(Date.now());
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [activeRunId, activeRunningTask?.task_id]);
  const composerText = input.trim();
  const composerHasPayload = Boolean(composerText || composerAttachments.length > 0);
  const composerActionIsStop = Boolean(activeThread && activeRunningTask && !composerHasPayload);
  const queueRows = useMemo(() => queueRowsFromTasks(workPanelTasks), [workPanelTasks]);
  const visibleQueueRows = queueRows.filter((row) => !row.active);
  const showAgentQueuePanel = visibleQueueRows.length > 0;
  const elapsedCounter = useMemo(
    () => taskGroupElapsedCounter(workPanelTasks, elapsedCounterNow),
    [elapsedCounterNow, workPanelTasks],
  );
  const blueprintNodes = useMemo(
    () =>
      buildBlueprintNodes({
        tasks: workPanelTasks,
        agentEvents,
        chunks,
        activeRunId,
        activeRunningTask,
        queueRows,
        activeThreadTitle: activeThread?.title || "",
      }),
    [activeRunId, activeRunningTask, activeThread?.title, agentEvents, chunks, queueRows, workPanelTasks],
  );
  const activeBlueprintNode = useMemo(() => blueprintAnchorNode(blueprintNodes), [blueprintNodes]);
  const selectedBlueprintNode =
    blueprintNodes.find((node) => node.id === selectedBlueprintNodeId) ?? null;
  const composerPlaceholder = workspaceComposerPlaceholder({
    hasActiveRun,
    placement: activeRunPlacement,
    workspaceMode: activePane,
    selectedBlueprintTitle: selectedBlueprintNode?.title,
    selectedChunkTitle: selectedChunk?.title,
    activeFilePath: activePreviewFileEntry?.relative_path,
    activeNoteTitle: activeNote?.title,
    activeNotePath: activeNote ? noteDisplayPath(activeNote, notesRoot) : null,
  });
  const activeComposerToken = useMemo(
    () => workspaceComposerTokenAt(input, composerCaret),
    [composerCaret, input],
  );
  const rawComposerSuggestions = useMemo(
    () =>
      workspaceComposerSuggestions(activeComposerToken, {
        skills: workspaceSkills,
        files: devFiles,
      }),
    [activeComposerToken, devFiles, workspaceSkills],
  );
  const composerSuggestions = useMemo(
    () =>
      activeComposerToken?.key === composerSuggestionSuppressedFor
        ? []
        : rawComposerSuggestions,
    [activeComposerToken?.key, composerSuggestionSuppressedFor, rawComposerSuggestions],
  );
  const selectedComposerSuggestion =
    composerSuggestions[composerSuggestionIndex] ?? composerSuggestions[0] ?? null;

  useEffect(() => {
    setComposerSuggestionIndex(0);
  }, [activeComposerToken?.key]);

  useEffect(() => {
    if (composerSuggestionIndex >= composerSuggestions.length) {
      setComposerSuggestionIndex(0);
    }
  }, [composerSuggestionIndex, composerSuggestions.length]);

  useEffect(() => {
    if (!selectedBlueprintNodeId) return;
    if (!blueprintNodes.some((node) => node.id === selectedBlueprintNodeId)) {
      setSelectedBlueprintNodeId(null);
    }
  }, [blueprintNodes, selectedBlueprintNodeId]);

  const notesGridTemplate = [
    showNotesRail ? `${leftRailWidth}px` : `${COLLAPSED_PANE_WIDTH}px`,
    showNoteEditor ? "minmax(0,1fr)" : `${COLLAPSED_PANE_WIDTH}px`,
    showNotesPreview ? "minmax(0,1fr)" : `${COLLAPSED_PANE_WIDTH}px`,
    showLearnPanel ? "minmax(300px,380px)" : `${COLLAPSED_PANE_WIDTH}px`,
  ].join(" ");
  const renderSessionRail = isPhoneViewport ? phonePage === "sessions" : showSessionRail;
  const renderFileExplorer = isPhoneViewport ? phonePage === "files" : showFileExplorer;
  const renderWorkMain = isPhoneViewport ? phonePage === "chat" : showConversationChunks;
  const renderSidecarPreview = isPhoneViewport ? phonePage === "preview" : showSidecarPreview;
  const renderNotesRail = isPhoneViewport ? phonePage === "note-list" : showNotesRail;
  const renderNoteEditor = isPhoneViewport ? phonePage === "note-edit" : showNoteEditor;
  const renderNotesPreview = isPhoneViewport ? phonePage === "note-preview" : showNotesPreview;
  const renderLearnPanel = isPhoneViewport ? phonePage === "note-learn" : showLearnPanel;
  const phoneNotesGridTemplate = "minmax(0,1fr)";
  const effectiveNotesGridTemplate = isPhoneViewport
    ? phoneNotesGridTemplate
    : notesGridTemplate || "minmax(0,1fr)";
  const wireGuardUi = wireGuardDisplay(wireGuardStatus);
  const startLeftRailResize = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = leftRailWidth;
      const handleMove = (moveEvent: globalThis.PointerEvent) => {
        setLeftRailWidth(clampLeftRailWidth(startWidth + moveEvent.clientX - startX));
      };
      const handleUp = () => {
        window.removeEventListener("pointermove", handleMove);
        window.removeEventListener("pointerup", handleUp);
      };
      window.addEventListener("pointermove", handleMove);
      window.addEventListener("pointerup", handleUp);
    },
    [leftRailWidth],
  );
  const startRootPickerResize = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();
      const startX = event.clientX;
      const startY = event.clientY;
      const startWidth = rootPickerWidth;
      const startHeight = rootPickerHeight;
      const handleMove = (moveEvent: globalThis.PointerEvent) => {
        setRootPickerWidth(clampRootPickerWidth(startWidth + moveEvent.clientX - startX));
        setRootPickerHeight(clampRootPickerHeight(startHeight + moveEvent.clientY - startY));
      };
      const handleUp = () => {
        window.removeEventListener("pointermove", handleMove);
        window.removeEventListener("pointerup", handleUp);
      };
      window.addEventListener("pointermove", handleMove);
      window.addEventListener("pointerup", handleUp);
    },
    [rootPickerHeight, rootPickerWidth],
  );

  const refreshWireGuardStatus = useCallback(async () => {
    setWireGuardLoading(true);
    try {
      const next = await getWorkspaceWireGuardStatus();
      setWireGuardStatus(next);
    } catch (error) {
      setWireGuardStatus((current) => ({
        service: "wireguard",
        mode: current?.mode ?? "unknown",
        interface: current?.interface ?? "unknown",
        launchd_label: current?.launchd_label ?? "",
        config_path: current?.config_path ?? "",
        config_present: current?.config_present ?? false,
        active: false,
        status: "unavailable",
        wg_present: current?.wg_present ?? false,
        wg_exit_code: current?.wg_exit_code ?? null,
        stdout: "",
        stderr: error instanceof Error ? error.message : "WireGuard status unavailable.",
        mutating_actions_enabled: false,
        safe_actions: ["status"],
        conflict_policy: "Read-only WireGuard status failed; no service action was attempted.",
      }));
    } finally {
      setWireGuardLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!hasActiveRun) setActiveRunPlacement("steer");
    else if (selectedAgentId !== "native") setActiveRunPlacement("queue");
  }, [hasActiveRun, selectedAgentId]);

  useEffect(() => {
    void refreshWireGuardStatus();
  }, [refreshWireGuardStatus]);

  useEffect(() => {
    if (activePane === "work" && !["chat", "sessions", "files", "preview"].includes(phonePage)) {
      setPhonePage("chat");
    }
    if (
      activePane === "notes" &&
      !["note-list", "note-edit", "note-preview", "note-learn"].includes(phonePage)
    ) {
      setPhonePage("note-preview");
    }
  }, [activePane, phonePage]);

  useEffect(() => {
    if (!rootEditing) {
      setLoadingRoots(false);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoadingRoots(true);
      void listWorkspaceRootSuggestions(rootInput)
        .then((payload) => {
          if (cancelled) return;
          setRootSuggestions(payload.suggestions ?? []);
          setDevRoot((current) => current || payload.root || "");
          setLoadingRoots(false);
        })
        .catch(() => {
          if (cancelled) return;
          setRootSuggestions([]);
          setLoadingRoots(false);
        });
    }, 140);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [rootEditing, rootInput]);

  const toggleFileDir = useCallback((relativePath: string) => {
    setExpandedFileDirs((previous) => ({
      ...previous,
      [relativePath]: !previous[relativePath],
    }));
  }, []);

  const bindThreadToWorkspace = useCallback(
    (workflowId: string, threadId: string, workspaceId?: string | null) => {
      const targetWorkspaceId = workspaceId || activeWorkspaceId;
      if (!targetWorkspaceId) return;
      const targetWorkspace = workspaces.find((item) => item.id === targetWorkspaceId);
      setThreadWorkspaces((previous) => ({
        ...previous,
        [threadWorkspaceKey(workflowId, threadId)]: targetWorkspaceId,
      }));
      updateWorkspace(targetWorkspaceId, {
        activeThreadId: threadId,
        openThreadIds: targetWorkspace?.openThreadIds.includes(threadId)
          ? targetWorkspace.openThreadIds
          : [...(targetWorkspace?.openThreadIds ?? []), threadId],
      });
    },
    [activeWorkspaceId, updateWorkspace, workspaces],
  );

  const updateActiveNote = useCallback(
    (content: string) => {
      if (!activeNote) return;
      const titleMatch = /^#\s+(.+)$/m.exec(content);
      const parsedPage = extractHugoPage(content, activeNote.title);
      const parsedTitle = parsedPage.meta.title;
      setNotes((previous) =>
        previous.map((note) =>
          note.id === activeNote.id
            ? {
                ...note,
                title: parsedTitle || titleMatch?.[1]?.trim() || note.title,
                layout: parsedPage.meta.layout || note.layout,
                pageID: parsedPage.meta.pageID || note.pageID,
                tags: parsedPage.meta.tags,
                categories: parsedPage.meta.categories,
                citations: extractPageIdCitations(content),
                content,
                loaded: true,
                status: "dirty",
                size: content.length,
                error: undefined,
                updatedAt: Date.now(),
              }
            : note,
        ),
      );
    },
    [activeNote],
  );

  const updateNoteMentionState = useCallback((element: HTMLTextAreaElement, content: string) => {
    setNoteMention(noteMentionAt(content, element.selectionStart));
  }, []);

  const insertPageIdMention = useCallback(
    (suggestion: PageIdSuggestion) => {
      if (!activeNote || !noteMention) return;
      const mentionText = `@${suggestion.pageID}`;
      const nextContent = [
        activeNote.content.slice(0, noteMention.start),
        mentionText,
        activeNote.content.slice(noteMention.end),
      ].join("");
      const nextCaret = noteMention.start + mentionText.length;
      updateActiveNote(nextContent);
      setNoteMention(null);
      window.requestAnimationFrame(() => {
        const editor = noteEditorRef.current;
        if (!editor) return;
        editor.focus();
        editor.setSelectionRange(nextCaret, nextCaret);
      });
    },
    [activeNote, noteMention, updateActiveNote],
  );

  const handleNoteEditorKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void saveNoteNow(activeNote, true);
        return;
      }

      if (!noteMention || activeMentionSuggestions.length === 0) return;

      if (event.key === "ArrowDown") {
        event.preventDefault();
        setNoteMentionIndex((index) => (index + 1) % activeMentionSuggestions.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setNoteMentionIndex(
          (index) =>
            (index - 1 + activeMentionSuggestions.length) % activeMentionSuggestions.length,
        );
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setNoteMention(null);
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        const suggestion = activeMentionSuggestions[noteMentionIndex] ?? activeMentionSuggestions[0];
        if (suggestion) insertPageIdMention(suggestion);
      }
    },
    [
      activeMentionSuggestions,
      activeNote,
      insertPageIdMention,
      noteMention,
      noteMentionIndex,
      saveNoteNow,
    ],
  );

  const focusNoteTitleField = useCallback((content: string) => {
    window.requestAnimationFrame(() => {
      const editor = noteEditorRef.current;
      if (!editor) return;
      const marker = 'title: "';
      const start = content.indexOf(marker);
      if (start < 0) {
        editor.focus();
        return;
      }
      const selectionStart = start + marker.length;
      const selectionEnd = content.indexOf('"', selectionStart);
      editor.focus();
      editor.setSelectionRange(selectionStart, selectionEnd > selectionStart ? selectionEnd : selectionStart);
    });
  }, []);

  const focusNoteBodyField = useCallback((content: string) => {
    window.requestAnimationFrame(() => {
      const editor = noteEditorRef.current;
      if (!editor) return;
      const selectionStart = frontmatterBodyStartOffset(content);
      editor.focus();
      editor.setSelectionRange(selectionStart, selectionStart);
    });
  }, []);

  const createDraftNote = useCallback(
    (seedText = "") => {
      const targetSection = draftTargetSectionFromContext({
        facet: noteFacet,
        activeSection: activeNoteSection,
        fallback: "notes",
      });
      const note = createTemporaryDraftNote({
        seedText,
        targetSection,
        facet: noteFacet,
      });
      setNotes((previous) => [note, ...previous.filter((item) => item.id !== note.id)]);
      setActiveNoteId(note.id);
      setNoteFacetPage(null);
      setNoteArticleOriginFacet(null);
      setSelectedChunkId(null);
      setActivePane("notes");
      setPhonePage("note-edit");
      setShowNoteEditor(true);
      setShowNotesPreview(true);
      setNoteRailView("pages");
      setStatus("Draft ready");
      focusNoteBodyField(note.content);
    },
    [activeNoteSection, focusNoteBodyField, noteFacet],
  );

  const createNote = useCallback(
    async (kind: "note" | "folder" = "note") => {
      if (kind === "note") {
        createDraftNote();
        return;
      }
      const now = new Date();
      const stamp = compactDateStamp(now);
      const rawFolderTitle =
        kind === "folder" ? window.prompt("Folder page title or path", "") : "";
      if (rawFolderTitle === null) return;
      const folderTitle = rawFolderTitle.trim();
      if (kind === "folder" && !folderTitle) return;

      const facetSection = noteFacet.startsWith("section:")
        ? noteFacet.slice("section:".length)
        : "";
      const section = safeRelativePath(
        (facetSection || activeNoteSection || "notes") === "root"
          ? "notes"
          : facetSection || activeNoteSection || "notes",
      );
      const folderSlug =
        kind === "folder"
          ? safeRelativePath(folderTitle)
              .split("/")
              .filter(Boolean)
              .map((part) => slugifyPathPart(part, "folder"))
              .join("/")
          : `note-${stamp}`;
      const relativePath = `${section}/${folderSlug}/index.md`;
      const serverPath = notesRoot ? joinPath(notesRoot, relativePath) : "";
      if (
        notes.some(
          (note) =>
            noteRelativePath(note, notesRoot).toLowerCase() === relativePath.toLowerCase(),
        )
      ) {
        setStatus("Note folder already exists");
        return;
      }

      const pageID = `${section}-${folderSlug}`
        .replace(/[\\/]+/g, "-")
        .replace(/[^A-Za-z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "");
      const title = kind === "folder" ? folderTitle.split(/[\\/]/).filter(Boolean).pop() || "" : "";
      const content = hugoFrontmatterTemplate({
        title,
        pageID,
        date: now.toISOString(),
      });

      if (!notesRoot) {
        const local = {
          ...createLocalNote(title || "Untitled note"),
          content,
          loaded: true,
          status: "dirty" as const,
        };
        setNotes((previous) => [local, ...previous]);
        setActiveNoteId(local.id);
        setNoteFacetPage(null);
        setNoteArticleOriginFacet(null);
        setSelectedChunkId(null);
        setActivePane("notes");
        setPhonePage("note-edit");
        focusNoteTitleField(content);
        return;
      }

      setStatus(kind === "folder" ? "Creating note folder" : "Creating note");
      try {
        const response = await writeWorkspaceNote(serverPath, content);
        const summary = response.note;
        const note = summary
          ? {
              ...noteFromServerSummary(summary),
              content,
              loaded: true,
              status: "clean" as const,
            }
          : ({
              id: `server:${serverPath}`,
              title: title || noteFolderLabel(folderSlug),
              path: serverPath,
              relativePath,
              section,
              source: "server" as const,
              content,
              loaded: true,
              status: "clean" as const,
              updatedAt: Date.now(),
              size: content.length,
              date: now.toISOString(),
              draft: false,
              tags: [],
              categories: [],
              citations: [],
            } satisfies WorkspaceNote);
        setNotes((previous) => [note, ...previous.filter((item) => item.id !== note.id)]);
        setActiveNoteId(note.id);
        setNoteFacetPage(null);
        setNoteArticleOriginFacet(null);
        setSelectedChunkId(null);
        setActivePane("notes");
        setPhonePage("note-edit");
        setShowNoteEditor(true);
        setShowNotesPreview(true);
        setNoteRailView("pages");
        const parentKeys = pathParts(relativePath).slice(0, -1);
        setExpandedNoteFolders((previous) => {
          const next = { ...previous };
          for (let index = 0; index < parentKeys.length; index += 1) {
            next[`folder:${parentKeys.slice(0, index + 1).join("/")}`] = true;
          }
          return next;
        });
        noteContentCacheRef.current[serverPath] = {
          content,
          updatedAt: note.updatedAt,
          size: content.length,
        };
        persistNoteContentCache(noteContentCacheRef.current);
        setStatus(kind === "folder" ? "Note folder created" : "Note created");
        focusNoteTitleField(content);
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Note creation failed");
      }
    },
    [
      activeNoteSection,
      createDraftNote,
      focusNoteTitleField,
      noteFacet,
      notes,
      notesRoot,
    ],
  );

  const moveNoteEntry = useCallback(
    async (sourcePath: string, targetFolderPath: string, sourceNoteId?: string) => {
      if (!notesRoot) return;
      const sourceNote = sourceNoteId ? notes.find((note) => note.id === sourceNoteId) : null;
      const dirtyInsideSource = notes.some((note) => {
        if (note.status !== "dirty" && note.status !== "saving") return false;
        const movablePath = noteMovablePath(note);
        return movablePath === sourcePath || Boolean(note.path?.startsWith(`${sourcePath}/`));
      });
      if (sourceNote?.status === "dirty" || sourceNote?.status === "saving" || dirtyInsideSource) {
        setStatus("Save notes before moving them");
        return;
      }
      const destination = joinPath(targetFolderPath, fileName(sourcePath));
      if (destination === sourcePath) return;
      setStatus("Moving note");
      const activeWasMoved =
        Boolean(activeNote?.path?.startsWith(`${sourcePath}/`)) ||
        Boolean(activeNote && noteMovablePath(activeNote) === sourcePath);
      try {
        const moved = await moveWorkspaceNotePath(sourcePath, destination);
        const payload = await listWorkspaceNotes();
        const serverNotes = (payload.notes ?? []).map(noteFromServerSummary);
        setNotes((previous) => [
          ...serverNotes,
          ...previous.filter((note) => note.source === "local"),
        ]);
        if (activeWasMoved && moved.note?.path) {
          setActiveNoteId(`server:${moved.note.path}`);
        }
        const targetRelative = relativeFileLabel(targetFolderPath, notesRoot);
        if (targetRelative) {
          setExpandedNoteFolders((previous) => ({
            ...previous,
            [`folder:${targetRelative}`]: true,
          }));
        }
        setStatus("Note moved");
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Note move failed");
      }
    },
    [activeNote, notes, notesRoot],
  );

  const createDevelopmentFolder = useCallback(async () => {
    const rawName = window.prompt("New workspace folder name", "");
    if (rawName === null) return;
    const folderName = safeRelativePath(rawName);
    if (!folderName || !developmentRoot) return;
    const basePath = activeFileEntry?.is_directory
      ? activeFileEntry.path
      : activeFileEntry?.path
        ? parentPath(activeFileEntry.path)
        : developmentRoot;
    const targetPath = rawName.trim().startsWith("/") ? normalizeRootPath(rawName) : joinPath(basePath, folderName);
    setStatus("Creating folder");
    try {
      await createWorkspaceFolder(targetPath, developmentRoot);
      setExpandedFileDirs((previous) => ({
        ...previous,
        [relativeFileLabel(basePath, developmentRoot)]: true,
      }));
      await refreshDevFiles();
      setStatus("Folder created");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Folder creation failed");
    }
  }, [activeFileEntry, developmentRoot, refreshDevFiles]);

  const moveDevelopmentEntry = useCallback(
    async (entry: WorkspaceFileEntry, targetDirectory: WorkspaceFileEntry) => {
      if (!developmentRoot || !targetDirectory.is_directory) return;
      const destination = joinPath(targetDirectory.path, entry.name);
      if (destination === entry.path || destination.startsWith(`${entry.path}/`)) return;
      setStatus("Moving workspace file");
      const activeMoved =
        activeFilePath === entry.path || Boolean(activeFilePath?.startsWith(`${entry.path}/`));
      try {
        const moved = await moveWorkspacePath(entry.path, destination, developmentRoot);
        await refreshDevFiles();
        if (activeMoved) setActiveFilePath(moved.file?.is_directory ? null : moved.file?.path ?? null);
        setExpandedFileDirs((previous) => ({
          ...previous,
          [targetDirectory.relative_path]: true,
        }));
        setStatus("Workspace file moved");
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Workspace move failed");
      }
    },
    [activeFilePath, developmentRoot, refreshDevFiles],
  );

  const applyDevelopmentRoot = useCallback((root: string) => {
    const nextRoot = normalizeRootPath(root);
    if (!nextRoot) return;
    let workspaceId = workspace?.id;
    if (!workspaceId) workspaceId = createWorkspace(fileName(nextRoot), "chat");
    setActiveWorkspace(workspaceId);
    updateWorkspace(workspaceId, {
      pinnedPaths: [nextRoot],
      name: fileName(nextRoot),
    });
    setActiveFilePath(null);
    setExpandedFileDirs({});
    setDevRoot(nextRoot);
    setRootEditing(false);
    setRootInput(nextRoot);
    setStoredRoots((previous) => rememberRoot(previous, nextRoot));
    setActivePane("work");
    setStatus("Workspace root changed");
  }, [createWorkspace, setActiveWorkspace, updateWorkspace, workspace?.id]);

  const workspaceIdForRoot = useCallback(
    (root: string) => {
      const normalizedRoot = normalizeRootPath(root);
      if (!normalizedRoot) return "";
      const existing = workspaces.find(
        (item) => normalizeRootPath(item.pinnedPaths[0] ?? "") === normalizedRoot,
      );
      if (existing) return existing.id;
      const id = createWorkspace(fileName(normalizedRoot), "chat");
      updateWorkspace(id, {
        pinnedPaths: [normalizedRoot],
        name: fileName(normalizedRoot),
      });
      return id;
    },
    [createWorkspace, updateWorkspace, workspaces],
  );

  const browseDevelopmentRoot = useCallback((root: string) => {
    const nextRoot = browsingRootPath(root);
    if (!nextRoot) return;
    setRootEditing(true);
    setRootInput(nextRoot);
    setStatus("Browsing workspace roots");
  }, []);

  const closeRootPicker = useCallback(() => {
    setRootEditing(false);
    setRootInput(developmentRoot);
    setRootSuggestions([]);
  }, [developmentRoot]);

  const openFolder = useCallback(async () => {
    const folder = await nativeDialog.openDirectory();
    if (!folder) {
      setRootEditing(true);
      setRootInput(developmentRoot || rootOptions[0]?.path || "");
      return;
    }
    applyDevelopmentRoot(folder);
  }, [applyDevelopmentRoot, developmentRoot, rootOptions]);

  const startNewSession = useCallback(async (workspaceIdOverride?: string | null) => {
    const targetWorkspaceId = workspaceIdOverride ?? workspace?.id ?? activeWorkspaceId;
    const activeThreadWorkspaceKey = activeThread
      ? threadWorkspaceKey(activeThread.workflowId, activeThread.id)
      : "";
    const activeThreadWorkspaceId = activeThreadWorkspaceKey
      ? threadWorkspaces[activeThreadWorkspaceKey]
      : "";
    const activeThreadInTargetWorkspace = Boolean(
      !targetWorkspaceId ||
        activeThreadWorkspaceId === targetWorkspaceId ||
        (workspace?.id === targetWorkspaceId && workspace.activeThreadId === activeThread?.id),
    );
    const activeBlankSession =
      activeThread?.title === "New Super DAN Session" &&
      messages.length === 0 &&
      tasks.length === 0 &&
      agentEvents.length === 0 &&
      activeThreadInTargetWorkspace;
    if (activeBlankSession) {
      if (targetWorkspaceId) {
        setActiveWorkspace(targetWorkspaceId);
        setCollapsedThreadGroups((previous) => ({
          ...previous,
          [`workspace:${targetWorkspaceId}`]: false,
        }));
      }
      setActivePane("work");
      setStatus("Ready");
      window.setTimeout(() => composerRef.current?.focus(), 0);
      return;
    }
    if (creatingSessionRef.current) {
      setStatus("Creating session");
      window.setTimeout(() => composerRef.current?.focus(), 0);
      return;
    }
    creatingSessionRef.current = true;
    const selectionSeq = ++sessionSelectionSeqRef.current;
    streamRef.current?.close();
    streamRef.current = null;
    agentStreamRef.current?.close();
    agentStreamRef.current = null;
    setStatus("Creating session");
    setActiveThread(null);
    setLoadingThreadId(null);
    setMessages([]);
    messagesRef.current = [];
    setPendingAssistantIds({});
    setTasks([]);
    setAgentEvents([]);
    setSelectedChunkId(null);
    setSelectedBlueprintNodeId(null);
    loadComposerDraftForThread(null);
    setActivePane("work");
    if (targetWorkspaceId) setActiveWorkspace(targetWorkspaceId);
    try {
      const workflowId = targetWorkspaceId || "_unassigned";
      const created = await createChatV2Thread(workflowId, {
        title: "New Super DAN Session",
        mode: "agent",
      });
      const next = {
        id: created.id,
        workflowId: created.workflow_id || workflowId,
        title: created.title || "New Super DAN Session",
      };
      if (sessionSelectionSeqRef.current !== selectionSeq) return;
      setActiveThread(next);
      loadComposerDraftForThread(next);
      bindThreadToWorkspace(next.workflowId, next.id, targetWorkspaceId);
      if (targetWorkspaceId) {
        setCollapsedThreadGroups((previous) => ({
          ...previous,
          [`workspace:${targetWorkspaceId}`]: false,
        }));
      }
      window.localStorage.setItem(
        LAST_THREAD_STORAGE_KEY,
        JSON.stringify({ threadId: next.id, workflowId: next.workflowId }),
      );
      await refreshThreads();
      if (sessionSelectionSeqRef.current === selectionSeq) setStatus("Ready");
    } catch {
      if (sessionSelectionSeqRef.current === selectionSeq) setStatus("Session create failed");
    } finally {
      creatingSessionRef.current = false;
    }
    if (sessionSelectionSeqRef.current === selectionSeq) {
      window.setTimeout(() => composerRef.current?.focus(), 0);
    }
  }, [
    activeThread?.title,
    activeThread?.id,
    activeThread?.workflowId,
    activeWorkspaceId,
    agentEvents.length,
    bindThreadToWorkspace,
    loadComposerDraftForThread,
    messages.length,
    refreshThreads,
    setActiveWorkspace,
    tasks.length,
    threadWorkspaces,
    workspace?.activeThreadId,
    workspace?.id,
  ]);

  const openSession = useCallback(
    async (summary: ChatV2ThreadSummary, workspaceId?: string | null, workspaceRoot?: string) => {
      if (summary.archived) {
        setStatus("Restore session to view it");
        return;
      }
      const optimisticWorkspaceId = workspaceId || (workspaceRoot ? workspaceIdForRoot(workspaceRoot) : "");
      if (optimisticWorkspaceId) {
        setActiveWorkspace(optimisticWorkspaceId);
        setActiveFilePath(null);
      }
      const selectionSeq = ++sessionSelectionSeqRef.current;
      streamRef.current?.close();
      streamRef.current = null;
      agentStreamRef.current?.close();
      agentStreamRef.current = null;
      const optimisticThread = {
        id: summary.id,
        workflowId: summary.workflow_id,
        title: summary.title,
      };
      setActiveThread(optimisticThread);
      loadComposerDraftForThread(optimisticThread);
      setMessages([]);
      messagesRef.current = [];
      setPendingAssistantIds({});
      setTasks([]);
      setAgentEvents([]);
      setSelectedChunkId(null);
      setSelectedBlueprintNodeId(null);
      setPromptLogPreview(null);
      setShowConversationChunks(true);
      setActivePane("work");
      setLoadingThreadId(summary.id);
      setStatus("Loading session");
      try {
        const thread = await getChatV2Thread(summary.workflow_id, summary.id);
        const history = await loadSuperDanThreadHistory(thread.id, thread.title, thread.updated_at).catch(() => ({
          tasks: [] as ChatV2TaskSnapshot[],
          events: [] as ChatV2AgentRunEvent[],
          messages: [] as ChatMessage[],
        }));
        if (sessionSelectionSeqRef.current !== selectionSeq) return;
        const sourceMessages = thread.messages.length > 0 ? thread.messages : history.messages;
        const loadedMessages = await restoreFullNativeMessages(sourceMessages);
        if (sessionSelectionSeqRef.current !== selectionSeq) return;
        const resolvedWorkspaceRoot = workspaceRoot || workspaceRootForTasks(history.tasks);
        const resolvedWorkspaceId =
          optimisticWorkspaceId ||
          workspaceIdForTasks(history.tasks, workspaces) ||
          (resolvedWorkspaceRoot ? workspaceIdForRoot(resolvedWorkspaceRoot) : "");
        if (resolvedWorkspaceId) {
          setActiveWorkspace(resolvedWorkspaceId);
          bindThreadToWorkspace(thread.workflow_id, thread.id, resolvedWorkspaceId);
        }
        markSessionResponseSeen(thread, history.tasks);
        setActivePane("work");
        setSelectedChunkId(null);
        setSelectedBlueprintNodeId(null);
        setPromptLogPreview(null);
        const loadedThread = {
          id: thread.id,
          workflowId: thread.workflow_id,
          title: thread.title,
        };
        setActiveThread(loadedThread);
        loadComposerDraftForThread(loadedThread);
        setMessages(loadedMessages);
        messagesRef.current = loadedMessages;
        setPendingAssistantIds({});
        setTasks(history.tasks);
        mergeBackgroundTasks(history.tasks);
        setAgentEvents(history.events);
        if (thread.messages.length === 0 && loadedMessages.length > 0) {
          void saveChatV2Thread(thread.workflow_id, thread.id, {
            messages: loadedMessages,
            mode: "agent",
          }).then(refreshThreads);
        }
        window.localStorage.setItem(
          LAST_THREAD_STORAGE_KEY,
          JSON.stringify({ threadId: thread.id, workflowId: thread.workflow_id }),
        );
        setStatus("Ready");
      } catch {
        if (sessionSelectionSeqRef.current !== selectionSeq) return;
        const failed = makeMessage(
          "assistant",
          `Could not load session "${summary.title || summary.id}".`,
        );
        setMessages([failed]);
        messagesRef.current = [failed];
        setStatus("Session load failed");
      } finally {
        if (sessionSelectionSeqRef.current === selectionSeq) {
          setLoadingThreadId((current) => (current === summary.id ? null : current));
        }
      }
    },
    [
      bindThreadToWorkspace,
      loadComposerDraftForThread,
      markSessionResponseSeen,
      mergeBackgroundTasks,
      refreshThreads,
      setActiveWorkspace,
      workspaceIdForRoot,
      workspaces,
    ],
  );

  const viewSessionProgress = useCallback(
    async (thread: ChatV2ThreadSummary, workspaceId?: string | null, workspaceRoot?: string) => {
      if (thread.archived) {
        setStatus("Restore session to view progress");
        return;
      }
      setActivePane("work");
      setPhonePage("chat");
      setShowConversationChunks(true);
      setSelectedChunkId(null);
      setSelectedBlueprintNodeId(null);
      if (activeThread?.id === thread.id) {
        return;
      }
      await openSession(thread, workspaceId, workspaceRoot);
    },
    [activeThread?.id, openSession],
  );

  const switchWorkbenchProject = useCallback((id: string) => {
    if (id === activeWorkspaceId) return;
    const target = workspaces.find((item) => item.id === id);
    if (!target) return;
    const thread = threads.find((item) => item.id === target.activeThreadId && !item.archived);
    setActiveWorkspace(id);
    setPhonePage("chat");
    if (thread) void openSession(thread, id, target.pinnedPaths[0]);
    else void startNewSession(id);
  }, [activeWorkspaceId, workspaces, threads, setActiveWorkspace, openSession, startNewSession]);

  const openSessionPromptLog = useCallback(async (thread: ChatV2ThreadSummary) => {
    if (thread.archived) {
      setStatus("Restore session to view its prompt log");
      return;
    }
    setActivePane("work");
    setPhonePage("preview");
    setShowSidecarPreview(true);
    setSelectedChunkId(null);
    setSelectedBlueprintNodeId(null);
    setActiveFilePath(null);
    setPromptLogPreview({
      threadId: thread.id,
      title: thread.title || "Session prompt log",
      body: "_Loading prompt log..._",
      path: "",
      entryCount: 0,
      status: "loading",
    });
    setStatus("Opening prompt log");
    try {
      const log = await getChatV2ThreadPromptLog(thread.id);
      setPromptLogPreview({
        threadId: thread.id,
        title: thread.title || "Session prompt log",
        body: log.content || "_No prompt log content was returned._",
        path: log.path || "",
        entryCount: log.entry_count ?? 0,
        status: "idle",
      });
      setStatus(log.entry_count > 0 ? "Prompt log ready" : "Prompt log has no model calls yet");
    } catch (error) {
      setPromptLogPreview({
        threadId: thread.id,
        title: thread.title || "Session prompt log",
        body: error instanceof Error ? error.message : "Prompt log failed to load.",
        path: "",
        entryCount: 0,
        status: "error",
      });
      setStatus("Prompt log failed");
    }
  }, []);

  const stopSessionRun = useCallback(
    async (thread: ChatV2ThreadSummary, task?: ChatV2TaskSnapshot | null) => {
      const targetTask =
        task ?? (activeThread?.id === thread.id ? activeRunningTask : null);
      const runId = targetTask ? taskRunId(targetTask) : "";
      if (!runId || !targetTask) {
        setStatus("Open the running session before stopping it");
        return;
      }
      setStatus("Requesting stop");
      try {
        const response = await postChatV2AgentRunCommand(runId, {
          command: "stop",
          task_id: targetTask.task_id,
          idempotency_key: nowId("stop"),
          payload: { reason: "user_requested_from_workspace_gui" },
        });
        if (response.task) {
          setTasks((previous) =>
            previous.map((item) =>
              item.task_id === response.task!.task_id ? response.task! : item,
            ),
          );
          mergeBackgroundTasks([response.task]);
        }
        setAgentEvents((previous) => [...previous, response.event].slice(-80));
        setStatus(response.event.summary || "Stop requested");
      } catch {
        setStatus("Stop request failed");
      }
    },
    [activeRunningTask, activeThread?.id, mergeBackgroundTasks],
  );

  const archiveSession = useCallback(
    async (thread: ChatV2ThreadSummary, archived = true) => {
      setThreads((previous) =>
        previous.map((item) =>
          item.id === thread.id && item.workflow_id === thread.workflow_id
            ? { ...item, archived }
            : item,
        ),
      );
      if (archived) {
        clearStoredThreadSelection(thread);
        removeThreadFromWorkspaceSlots(thread.id);
      }
      if (archived && activeThread?.id === thread.id) {
        sessionSelectionSeqRef.current += 1;
        clearActiveSessionView();
      }
      setStatus(archived ? "Archiving session" : "Restoring session");
      try {
        await archiveChatV2Thread(thread.workflow_id, thread.id, archived);
        await refreshThreads();
        setStatus(archived ? "Session archived" : "Session restored");
      } catch {
        await refreshThreads();
        setStatus(archived ? "Archive failed" : "Restore failed");
      }
    },
    [
      activeThread?.id,
      clearActiveSessionView,
      clearStoredThreadSelection,
      refreshThreads,
      removeThreadFromWorkspaceSlots,
    ],
  );

  const deleteArchivedSession = useCallback(
    async (thread: ChatV2ThreadSummary) => {
      if (!thread.archived) return;
      const sessionTitle = thread.title?.replace(/\s+/g, " ").trim() || "Untitled session";
      const title = sessionTitle.length > 80 ? `${sessionTitle.slice(0, 79).trimEnd()}…` : sessionTitle;
      const confirmed = window.confirm(`Permanently delete "${title}"? This cannot be undone.`);
      if (!confirmed) return;

      setStatus("Deleting session");
      setThreads((previous) =>
        previous.filter(
          (item) => !(item.id === thread.id && item.workflow_id === thread.workflow_id),
        ),
      );
      setBackgroundTasks((previous) => previous.filter((task) => task.thread_id !== thread.id));
      clearStoredThreadSelection(thread);
      removeThreadFromWorkspaceSlots(thread.id);
      if (activeThread?.id === thread.id) {
        sessionSelectionSeqRef.current += 1;
        clearActiveSessionView();
      }

      try {
        await deleteChatV2Thread(thread.workflow_id, thread.id);
        await refreshThreads();
        setStatus("Session deleted");
      } catch {
        await refreshThreads();
        setStatus("Delete failed");
      }
    },
    [
      activeThread?.id,
      clearActiveSessionView,
      clearStoredThreadSelection,
      refreshThreads,
      removeThreadFromWorkspaceSlots,
    ],
  );

  const deleteAllArchivedSessions = async () => {
    if (deletingArchived) return;
    const archived = threads.filter((thread) => thread.archived);
    if (!archived.length || !window.confirm(`Permanently delete all ${archived.length} archived DAN chats across all projects, including any hidden by search? Active chats, project files, and original Codex/Claude/Antigravity sessions will stay untouched. This cannot be undone.`)) return;
    setDeletingArchived(true);
    let deleted = 0;
    let failed = 0;
    try {
      for (const [index, thread] of archived.entries()) {
        setArchiveDeleteProgress(`Deleting ${index + 1} of ${archived.length}…`);
        try {
          await deleteChatV2Thread(thread.workflow_id, thread.id, true);
          deleted += 1;
          setThreads((previous) => previous.filter((item) => !(item.id === thread.id && item.workflow_id === thread.workflow_id)));
          setBackgroundTasks((previous) => previous.filter((task) => task.thread_id !== thread.id));
          clearStoredThreadSelection(thread);
          removeThreadFromWorkspaceSlots(thread.id);
          if (activeThread?.id === thread.id) {
            sessionSelectionSeqRef.current += 1;
            clearActiveSessionView();
          }
        } catch { failed += 1; }
      }
      await refreshThreads();
      const message = `${deleted} archived chats deleted${failed ? `; ${failed} could not be deleted. You can retry.` : ""}`;
      setArchiveDeleteProgress(message);
      setStatus(message);
    } finally { setDeletingArchived(false); }
  };

  const beginSessionSwipe = useCallback(
    (
      event: PointerEvent<HTMLDivElement>,
      thread: ChatV2ThreadSummary,
      archived: boolean,
    ) => {
      const actionTarget = (event.target as HTMLElement).closest("[data-session-action]");
      if (actionTarget || event.button !== 0 || event.pointerType === "mouse") return;
      const key = threadWorkspaceKey(thread.workflow_id, thread.id);
      sessionSwipeRef.current = {
        key,
        pointerId: event.pointerId,
        startX: event.clientX,
        thread,
        archived,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [],
  );

  const moveSessionSwipe = useCallback((event: PointerEvent<HTMLDivElement>) => {
    const swipe = sessionSwipeRef.current;
    if (!swipe || swipe.pointerId !== event.pointerId) return;
    const rawDelta = event.clientX - swipe.startX;
    const offset = swipe.archived
      ? Math.max(0, Math.min(88, rawDelta))
      : Math.min(0, Math.max(-88, rawDelta));
    if (Math.abs(offset) < 4) return;
    event.preventDefault();
    setSessionSwipeOffsets((previous) => ({ ...previous, [swipe.key]: offset }));
  }, []);

  const endSessionSwipe = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const swipe = sessionSwipeRef.current;
      if (!swipe || swipe.pointerId !== event.pointerId) return;
      const offset = sessionSwipeOffsets[swipe.key] ?? 0;
      sessionSwipeRef.current = null;
      setSessionSwipeOffsets((previous) => {
        const next = { ...previous };
        delete next[swipe.key];
        return next;
      });
      if (Math.abs(offset) >= 56) {
        suppressSessionClickRef.current = swipe.key;
        void archiveSession(swipe.thread, !swipe.archived);
      }
    },
    [archiveSession, sessionSwipeOffsets],
  );

  const ensureThread = useCallback(
    async (prompt: string) => {
      if (activeThread) return activeThread;
      const targetWorkspaceId = workspace?.id ?? activeWorkspaceId;
      const selectionSeq = ++sessionSelectionSeqRef.current;
      const workflowId = targetWorkspaceId || "_unassigned";
      const created = await createChatV2Thread(workflowId, {
        title: titleFromText(prompt),
        mode: "agent",
      });
      const next = {
        id: created.id,
        workflowId: created.workflow_id || workflowId,
        title: created.title,
      };
      if (sessionSelectionSeqRef.current === selectionSeq) {
        setActiveThread(next);
        bindThreadToWorkspace(next.workflowId, next.id, targetWorkspaceId);
        window.localStorage.setItem(
          LAST_THREAD_STORAGE_KEY,
          JSON.stringify({ threadId: next.id, workflowId: next.workflowId }),
        );
        await refreshThreads();
      }
      return next;
    },
    [activeThread, activeWorkspaceId, bindThreadToWorkspace, refreshThreads, workspace?.id],
  );

  const persistMessages = useCallback(
    async (
      thread: { id: string; workflowId: string },
      nextMessages: ChatMessage[],
      mode: "conversation" | "agent" = "conversation",
    ) => {
      try {
        await saveChatV2Thread(thread.workflowId, thread.id, {
          messages: nextMessages,
          mode,
        });
        await refreshThreads();
      } catch {
        setStatus("Thread save failed");
      }
    },
    [refreshThreads],
  );

  const applyMessages = useCallback((updater: (messages: ChatMessage[]) => ChatMessage[]) => {
    const next = updater(messagesRef.current);
    messagesRef.current = next;
    setMessages(next);
    return next;
  }, []);

  const attachRunEventToAssistant = useCallback(
    (assistantId: string, event: RunEventPayload) => {
      applyMessages((previous) =>
        previous.map((message) =>
          message.id === assistantId
            ? { ...message, runEvents: [...(message.runEvents ?? []), event].slice(-20) }
            : message,
        ),
      );
    },
    [applyMessages],
  );

  const connectAgentStream = useCallback(
    (
      runId: string,
      thread: { id: string; workflowId: string },
      assistantId: string,
      runWorkspaceRoot = developmentRoot,
      runWorkspaceId = workspace?.id ?? activeWorkspaceId ?? "",
    ) => {
      agentStreamRef.current?.close();
      attachedRunKeyRef.current = `${thread.id}:${runId}`;
      agentStreamRef.current = connectChatV2AgentRunEvents(
        runId,
        (event) => {
          const refreshThreadTasks = (delay = 0) => {
            if (delay <= 0) {
              void refreshTasks(thread.id);
              void refreshBackgroundTasks();
              return;
            }
            window.setTimeout(() => {
              void refreshTasks(thread.id);
              void refreshBackgroundTasks();
            }, delay);
          };
          if (event.task_id) refreshThreadTasks();
          if (agentRunEventIsTerminal(event)) {
            setBackgroundTasks((previous) => applyRunEventToTaskSnapshots(previous, event));
            if (activeThreadRef.current?.id === thread.id) {
              setTasks((previous) => applyRunEventToTaskSnapshots(previous, event));
            }
          }
          if (activeThreadRef.current?.id !== thread.id) return;
          setAgentEvents((previous) => [...previous, event].slice(-80));
          attachRunEventToAssistant(assistantId, runEventPayloadFromAgentEvent(event));
          const action = !agentRunEventIsTerminal(event) ? liveActionText(event.summary) : "";
          if (action) setLiveActions((previous) => previous[assistantId] === action ? previous : { ...previous, [assistantId]: action });
          if (event.type === "model_text_delta") {
            applyMessages((previous) => previous.map((message) => message.id === assistantId
              ? { ...message, content: streamedMessageContent(message.content, event) } : message));
          }

          if (agentRunEventIsTerminal(event)) {
            refreshThreadTasks(300);
            refreshThreadTasks(1200);
            const finalText =
              humanEventSummary(event) || (event.type === "completed" ? "Completed." : eventSummary(event));
            const next = applyMessages((previous) =>
              previous.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content: event.type === "completed" ? completedMessageContent(message.content, humanEventSummary(event), eventFinalResponseSource(event)) : message.content || finalText,
                      taskRunRef: {
                        taskId: event.task_id,
                        runId: event.run_id,
                        status: event.type,
                        workspaceRoot: runWorkspaceRoot,
                        workspaceId: runWorkspaceId,
                      },
                    }
                  : message,
              ),
            );
            void persistMessages(thread, next, "agent");
            setPendingAssistantIds((previous) => {
              const nextPending = { ...previous };
              delete nextPending[assistantId];
              return nextPending;
            });
            setLiveActions((previous) => {
              const next = { ...previous };
              delete next[assistantId];
              return next;
            });
            setStatus(event.type === "completed" ? "Ready" : event.type);
          }
        },
        undefined,
        () => {
          if (activeThreadRef.current?.id === thread.id) {
            setStatus("Super DAN event stream interrupted");
          }
        },
      );
    },
    [
      applyMessages,
      attachRunEventToAssistant,
      persistMessages,
      refreshBackgroundTasks,
      refreshTasks,
      activeWorkspaceId,
      workspace?.id,
      developmentRoot,
    ],
  );

  const sendAgent = useCallback(
    async (
      prompt: string,
      queueCommand: "append_followup" | "continue_after_current" = "append_followup",
      options: {
        selectedSkills?: string[];
        mentionedFiles?: WorkspaceFileEntry[];
        attachments?: ComposerAttachmentDraft[];
        displayText?: string;
        regenerate?: boolean;
      } = {},
    ) => {
      if (selectedAgentId !== "native") queueCommand = "continue_after_current";
      const attachments = options.attachments ?? [];
      const attachmentPayloads = workspaceAttachmentPayloads(attachments);
      const firstAttachmentPath =
        attachments.find((attachment) => typeof attachment.path === "string" && attachment.path.trim())
          ?.path ?? null;
      const displayText = options.displayText || prompt;
      const thread = await ensureThread(prompt);
      const taskRoot = activeThreadRef.current?.id === thread.id
        ? workspaceRootForTasks(tasks)
        : taskWorkspaceRootByThreadId.get(thread.id) ?? "";
      const taskWorkspaceId = activeThreadRef.current?.id === thread.id
        ? workspaceIdForTasks(tasks, workspaces)
        : taskWorkspaceByThreadId.get(thread.id) ?? "";
      const localWorkspaceId =
        threadWorkspaces[threadWorkspaceKey(thread.workflowId, thread.id)] ||
        workspace?.id ||
        activeWorkspaceId ||
        "";
      const localWorkspaceRoot = workspaceRootForWorkspaceId(workspaces, localWorkspaceId);
      const taskWorkspaceRootForId = workspaceRootForWorkspaceId(workspaces, taskWorkspaceId);
      const resolvedWorkspaceId =
        taskWorkspaceId && (!taskRoot || !taskWorkspaceRootForId || taskWorkspaceRootForId === taskRoot)
          ? taskWorkspaceId
          : localWorkspaceId && (!taskRoot || !localWorkspaceRoot || localWorkspaceRoot === taskRoot)
            ? localWorkspaceId
            : "";
      const resolvedWorkspaceRoot =
        taskRoot ||
        (resolvedWorkspaceId ? workspaceRootForWorkspaceId(workspaces, resolvedWorkspaceId) : "") ||
        developmentRoot;
      if (resolvedWorkspaceId) {
        bindThreadToWorkspace(thread.workflowId, thread.id, resolvedWorkspaceId);
      }
      const sendSelectionSeq = sessionSelectionSeqRef.current;
      const isStillSelectedThread = () =>
        sessionSelectionSeqRef.current === sendSelectionSeq &&
        (!activeThreadRef.current || activeThreadRef.current.id === thread.id);
      setSelectedBlueprintNodeId(null);
      const user = {
        ...makeMessage("user", displayText),
        attachments:
          attachments.length > 0 ? attachments.map(composerDraftToChatAttachment) : undefined,
      };
      const assistant = makeMessage(
        "assistant",
        activeRunId
          ? queueCommand === "continue_after_current"
            ? "Queued after the current Super DAN run."
            : "Steering note queued for the active Super DAN run."
          : "",
      );
      const nextMessages = applyMessages((previous) => [...previous, user, assistant]);
      if (!activeRunId) {
        setPendingAssistantIds((previous) => ({ ...previous, [assistant.id]: true }));
      }
      const initialPersist = persistMessages(thread, nextMessages, "agent");
      setStatus("");

      if (activeRunId && activeRunningTask) {
        const response = await postChatV2AgentRunCommand(activeRunId, {
          command: queueCommand,
          task_id: activeRunningTask.task_id,
          idempotency_key: nowId(queueCommand),
          payload: {
            text: prompt,
            attachments: attachmentPayloads,
            surface_context: buildSurfaceContext({
              note: activeNote,
              selectedChunk,
              selectedBlueprintNode,
              workspaceRoot: resolvedWorkspaceRoot,
              workspaceId: resolvedWorkspaceId || resolvedWorkspaceRoot,
              notesRoot,
              workspaceMode: activePane,
              agentSelection: selectedAgentOption,
              modelSelection: selectedModelOption,
              autonomyMode,
              activeFile: activePreviewFileEntry,
              activeFileContent,
              wireGuardStatus,
              selectedSkills: options.selectedSkills ?? [],
              mentionedFiles: options.mentionedFiles ?? [],
              attachments,
            }),
          },
        });
        if (response.task) {
          mergeBackgroundTasks([response.task]);
          if (isStillSelectedThread()) {
            setTasks((previous) => [response.task!, ...previous]);
          }
        }
        if (response.event && isStillSelectedThread()) {
          setAgentEvents((previous) => [...previous, response.event].slice(-80));
        }
        const updateAssistant = (previous: ChatMessage[]) =>
          previous.map((message) =>
            message.id === assistant.id
              ? { ...message, content: response.event.summary || assistant.content }
              : message,
          );
        const finalMessages = isStillSelectedThread()
          ? applyMessages(updateAssistant)
          : updateAssistant(nextMessages);
        await initialPersist;
        await persistMessages(thread, finalMessages, "agent");
        if (isStillSelectedThread()) {
          setStatus(
            queueCommand === "continue_after_current"
              ? "Queued after current Super DAN run"
              : "Steering Super DAN",
          );
        }
        return;
      }

      const created = await createChatV2AgentRun({
        workflow_id: thread.workflowId,
        message: prompt,
        history: normalizeChatV2History(nextMessages.slice(0, -1)),
        thread_id: thread.id,
        session_id: thread.id,
        mode: "agent",
        surface: WORKSPACE_SURFACE,
        surface_type: WORKSPACE_SURFACE_TYPE,
        surface_id: WORKSPACE_SURFACE_ID,
        attachment_path: firstAttachmentPath,
        surface_context: { ...(options.regenerate ? { regenerate: true } : {}), ...buildSurfaceContext({
          note: activeNote,
          selectedChunk,
          selectedBlueprintNode,
          workspaceRoot: resolvedWorkspaceRoot,
          workspaceId: resolvedWorkspaceId || resolvedWorkspaceRoot,
          notesRoot,
          workspaceMode: activePane,
          agentSelection: selectedAgentOption,
          modelSelection: selectedModelOption,
          autonomyMode,
          activeFile: activePreviewFileEntry,
          activeFileContent,
          wireGuardStatus,
          selectedSkills: options.selectedSkills ?? [],
          mentionedFiles: options.mentionedFiles ?? [],
          attachments,
        }) },
      });
      const createdTaskWorkspaceId =
        (created.task ? workspaceIdForTask(created.task, workspaces) : "") ||
        workspace?.id ||
        activeWorkspaceId ||
        "";
      if (createdTaskWorkspaceId) {
        bindThreadToWorkspace(thread.workflowId, thread.id, createdTaskWorkspaceId);
      }
      if (created.task) {
        mergeBackgroundTasks([created.task]);
        if (isStillSelectedThread()) {
          setTasks((previous) => [created.task!, ...previous]);
        }
      }
      if (created.event && isStillSelectedThread()) {
        setAgentEvents((previous) => [...previous, created.event!].slice(-80));
      }
      const runId = created.task_run_ref?.run_id || created.v2_control_plane.run_id || "";
      const linkAssistant = (previous: ChatMessage[]) =>
        previous.map((message) =>
          message.id === assistant.id
            ? {
                ...message,
                taskRunRef: {
                  taskId:
                    created.task?.task_id ??
                    created.task_run_ref?.task_id ??
                    created.v2_control_plane.task_run_ref?.task_id ??
                    null,
                  runId: runId || null,
                  status:
                    created.task?.status ??
                    created.task_run_ref?.status ??
                    created.v2_control_plane.task_run_ref?.status ??
                    "queued",
                  workspaceRoot:
                    textValue(created.task?.metadata?.workspace_root) ||
                    created.task_run_ref?.workspace_root ||
                    resolvedWorkspaceRoot,
                  workspaceId:
                    textValue(created.task?.metadata?.workspace_id) ||
                    created.task_run_ref?.workspace_id ||
                    resolvedWorkspaceId ||
                    resolvedWorkspaceRoot ||
                    "",
                },
              }
            : message,
        );
      const linkedMessages = isStillSelectedThread()
        ? applyMessages(linkAssistant)
        : linkAssistant(nextMessages);
      await initialPersist;
      if (!runId) {
        await persistMessages(thread, linkedMessages, "agent");
        if (isStillSelectedThread()) {
          setStatus(`${selectedAgentOption.shortLabel} · ${selectedModelOption.shortLabel} queued`);
        }
        return;
      }
      if (isStillSelectedThread()) {
        connectAgentStream(
          runId,
          thread,
          assistant.id,
          resolvedWorkspaceRoot,
          resolvedWorkspaceId || resolvedWorkspaceRoot,
        );
      }
      const executed = await executeChatV2AgentRun(
        runId,
        (() => {
          const payload = buildWorkspaceAgentExecutePayload(selectedAgentOption, selectedModelOption, autonomyMode);
          payload.profile_policy.native_workers = nativeWorkerProfiles;
          if (selectedAgentId !== "native") payload.profile_policy.lead_profile = leadProfiles[selectedAgentId] || {};
          return payload;
        })(),
      );
      if (executed.task) {
        mergeBackgroundTasks([executed.task]);
        if (isStillSelectedThread()) {
          setTasks((previous) => [executed.task!, ...previous]);
        }
      }
      if (isStillSelectedThread()) {
        setStatus(`${selectedAgentOption.shortLabel} · ${selectedModelOption.shortLabel} running`);
      }
      await persistMessages(thread, linkedMessages, "agent");
    },
    [
      activeNote,
      activeFileContent,
      activePreviewFileEntry,
      activePane,
      activeWorkspaceId,
      activeRunId,
      activeRunningTask,
      applyMessages,
      bindThreadToWorkspace,
      connectAgentStream,
      developmentRoot,
      ensureThread,
      mergeBackgroundTasks,
      notesRoot,
      persistMessages,
      selectedChunk,
      nativeWorkerProfiles,
      selectedAgentOption,
      selectedModelOption,
      selectedAgentId,
      leadProfiles,
      nativeWorkerProfiles,
      autonomyMode,
      selectedBlueprintNode,
      taskWorkspaceByThreadId,
      taskWorkspaceRootByThreadId,
      tasks,
      threadWorkspaces,
      wireGuardStatus,
      workspaces,
      workspace?.id,
    ],
  );

  const handleComposerInputChange = useCallback((event: ChangeEvent<HTMLTextAreaElement>) => {
    setComposerInputValue(event.target.value);
    setComposerCaret(event.target.selectionStart ?? event.target.value.length);
    setComposerSuggestionSuppressedFor(null);
  }, [setComposerInputValue]);

  const handleComposerSelectionChange = useCallback((event: SyntheticEvent<HTMLTextAreaElement>) => {
    const target = event.currentTarget;
    setComposerCaret(target.selectionStart ?? target.value.length);
  }, []);

  const handleComposerPaste = useCallback(
    (event: ClipboardEvent<HTMLTextAreaElement>) => {
      const clipboard = event.clipboardData;
      const imageFiles = Array.from(clipboard?.items ?? [])
        .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
        .map((item) => item.getAsFile())
        .filter((file): file is File => Boolean(file));
      if (imageFiles.length === 0) return;
      event.preventDefault();
      const remainingSlots = WORKSPACE_COMPOSER_MAX_SCREENSHOTS - composerAttachments.length;
      if (remainingSlots <= 0) {
        setStatus("Remove a screenshot before adding another");
        return;
      }
      const filesToAttach = imageFiles.slice(0, remainingSlots);
      setStatus("Adding screenshot");
      void (async () => {
        try {
          const drafts = await Promise.all(
            filesToAttach.map(async (file, index) => {
              const mimeType = file.type || "image/png";
              const fallbackName = `Screenshot ${index + 1}${extensionForImageMimeType(mimeType)}`;
              const dataUrl = await dataUrlFromFile(file);
              return {
                id: crypto.randomUUID(),
                kind: "figure" as const,
                name: resolveAttachmentName(file.name || fallbackName, mimeType),
                size: file.size,
                mimeType,
                source: "clipboard",
                dataUrl,
                file,
              };
            }),
          );
          const normalized = await normalizeAttachmentDrafts(drafts);
          setComposerAttachments((previous) =>
            [...previous, ...normalized].slice(0, WORKSPACE_COMPOSER_MAX_SCREENSHOTS),
          );
          setStatus(normalized.length === 1 ? "Screenshot attached" : "Screenshots attached");
          requestAnimationFrame(() => composerRef.current?.focus());
        } catch (error) {
          setStatus(error instanceof Error ? error.message : "Screenshot paste failed");
        }
      })();
    },
    [composerAttachments.length],
  );

  const removeComposerAttachment = useCallback((attachmentId: string) => {
    setComposerAttachments((previous) =>
      previous.filter((attachment) => attachment.id !== attachmentId),
    );
    requestAnimationFrame(() => composerRef.current?.focus());
  }, []);

  const applyComposerSuggestion = useCallback(
    (suggestion: WorkspaceComposerSuggestion) => {
      const token = workspaceComposerTokenAt(input, composerCaret);
      if (!token) return;
      const replacement = `${suggestion.insertText} `;
      const nextInput = `${input.slice(0, token.start)}${replacement}${input.slice(token.end)}`;
      const nextCaret = token.start + replacement.length;
      setComposerInputValue(nextInput);
      setComposerCaret(nextCaret);
      setComposerSuggestionIndex(0);
      setComposerSuggestionSuppressedFor(null);
      requestAnimationFrame(() => {
        composerRef.current?.focus();
        composerRef.current?.setSelectionRange(nextCaret, nextCaret);
      });
    },
    [composerCaret, input, setComposerInputValue],
  );

  useEffect(() => { attachedRunKeyRef.current = ""; }, [activeThread?.id]);
  useEffect(() => {
    if (!activeThread || loadingThreadId !== null || !activeRunId) return;
    const key = `${activeThread.id}:${activeRunId}`;
    if (attachedRunKeyRef.current === key) return;
    const current = messagesRef.current;
    const linked = current.find((message) => message.role === "assistant" && message.taskRunRef?.runId === activeRunId);
    const trailing = current.at(-1)?.role === "assistant" ? current.at(-1) : undefined;
    const target = linked ?? trailing ?? makeMessage("assistant", "");
    applyMessages((previous) => previous.some((message) => message.id === target.id)
      ? previous.map((message) => message.id === target.id ? { ...message, content: "", runEvents: [] } : message)
      : [...previous, target]);
    setPendingAssistantIds((previous) => ({ ...previous, [target.id]: true }));
    connectAgentStream(activeRunId, activeThread, target.id);
  }, [activeRunId, activeThread, applyMessages, connectAgentStream, loadingThreadId]);

  /** Re-run the latest request in place, discarding the answer that followed it. */
  const regenerateLastRequest = useCallback(async () => {
    const current = messagesRef.current;
    let index = current.length - 1;
    while (index >= 0 && current[index].role !== "user") index -= 1;
    if (index < 0 || !activeThread || activeRunId) return;
    const request = current[index];
    applyMessages(() => current.slice(0, index));
    try {
      await sendAgent(request.content, "append_followup", { displayText: request.content, regenerate: true });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Regenerate failed");
    }
  }, [activeRunId, activeThread, applyMessages, sendAgent]);

  /** Copy this conversation into a new chat in the same project; the original is untouched. */
  const forkConversation = useCallback(async () => {
    if (!activeThread || activeRunId) return;
    const source = messagesRef.current;
    const lastRequest = [...source].reverse().find((message) => message.role === "user");
    const workspaceId =
      threadWorkspaces[threadWorkspaceKey(activeThread.workflowId, activeThread.id)] || workspace?.id || activeWorkspaceId || "";
    try {
      setStatus("Forking conversation");
      const created = await createChatV2Thread(activeThread.workflowId, {
        title: `${activeThread.title || "Conversation"} (fork)`,
        mode: "agent",
        parent_thread_id: activeThread.id,
        branch_point_message_id: lastRequest?.id,
        branch_type: "explore",
      });
      const workflowId = created.workflow_id || activeThread.workflowId;
      await saveChatV2Thread(workflowId, created.id, { messages: source, mode: "agent" });
      if (workspaceId) bindThreadToWorkspace(workflowId, created.id, workspaceId);
      await refreshThreads();
      await openSession({ id: created.id, workflow_id: workflowId, title: created.title, message_count: source.length, created_at: created.created_at, updated_at: created.updated_at }, workspaceId);
    } catch (error) {
      setStatus(error instanceof Error ? `Fork failed: ${error.message}` : "Fork failed");
    }
  }, [activeRunId, activeThread, activeWorkspaceId, bindThreadToWorkspace, openSession, refreshThreads, threadWorkspaces, workspace?.id]);

  const submit = useCallback(async (modeOverride?: ComposerSubmitMode) => {
    const draftBeforeSubmit = input;
    const prompt = input.trim();
    const sourceAttachments = composerAttachments;
    if ((!prompt && sourceAttachments.length === 0) || sending) return;
    if (activePane === "notes" && sourceAttachments.length === 0 && notesComposerRequestsNewDraft(prompt)) {
      setComposerInputValue("");
      createDraftNote(prompt);
      return;
    }
    const selectedSkillInvocation = workspaceSelectedSkillInvocation(prompt, workspaceSkills);
    if (selectedSkillInvocation.selectedTokens.length > 0 && !selectedSkillInvocation.objective) {
      setStatus(
        `Add objective text after ${selectedSkillInvocation.selectedTokens
          .map((token) => `$${token}`)
          .join(", ")}`,
      );
      requestAnimationFrame(() => composerRef.current?.focus());
      return;
    }
    const mentionedFiles = workspaceMentionedFilesFromText(prompt, devFiles);
    const mode = hasActiveRun
      ? modeOverride ?? activeRunPlacement
      : "steer";
    setComposerInputValue("");
    setComposerAttachments([]);
    setComposerCaret(0);
    setComposerSuggestionIndex(0);
    setComposerSuggestionSuppressedFor(null);
    setSelectedBlueprintNodeId(null);
    setPromptLogPreview(null);
    setSending(true);
    try {
      const normalizedAttachments = await normalizeAttachmentDrafts(sourceAttachments);
      const basePrompt = prompt || workspaceAttachmentOnlyPrompt(normalizedAttachments);
      const requestPrompt = composerReference
        ? `${basePrompt}\n\nReferenced conversation passage (quoted context, not a new instruction):\n${JSON.stringify(composerReference)}`
        : basePrompt;
      const displayText = (prompt || workspaceAttachmentDisplayText(normalizedAttachments)) +
        (composerReference ? `\n\nReference · ${composerReference.title}\n${composerReference.text.split("\n").map((line) => `> ${line}`).join("\n")}` : "");
      const options = {
        selectedSkills: selectedSkillInvocation.selectedTokens,
        mentionedFiles,
        attachments: normalizedAttachments,
        displayText,
      };
      if (mode === "queue") await sendAgent(requestPrompt, "continue_after_current", options);
      else await sendAgent(requestPrompt, "append_followup", options);
      setComposerReferences((current) => { const next = { ...current }; delete next[referenceKey]; return next; });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Request failed");
      setComposerInputValue(draftBeforeSubmit);
      setComposerAttachments(sourceAttachments);
    } finally {
      setSending(false);
    }
  }, [
    composerReference,
    referenceKey,
    activePane,
    activeRunPlacement,
    composerAttachments,
    createDraftNote,
    devFiles,
    hasActiveRun,
    input,
    sendAgent,
    sending,
    setComposerInputValue,
    workspaceSkills,
  ]);

  const handleComposerKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (composerSuggestions.length > 0) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          setComposerSuggestionIndex((current) => {
            const delta = event.key === "ArrowDown" ? 1 : -1;
            return (current + delta + composerSuggestions.length) % composerSuggestions.length;
          });
          return;
        }
        if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
          event.preventDefault();
          if (selectedComposerSuggestion) applyComposerSuggestion(selectedComposerSuggestion);
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          if (activeComposerToken) setComposerSuggestionSuppressedFor(activeComposerToken.key);
          return;
        }
      }
      if (event.key !== "Enter") return;
      if (event.shiftKey) return;
      event.preventDefault();
      if (event.altKey && hasActiveRun) {
        void submit("queue");
        return;
      }
      void submit();
    },
    [
      activeComposerToken,
      applyComposerSuggestion,
      composerSuggestions.length,
      hasActiveRun,
      selectedComposerSuggestion,
      submit,
    ],
  );

  const renderWorkspaceComposer = () => (
    <div className="wb-composer shrink-0 border-t border-slate-200/80 bg-white/95 p-3 shadow-[0_-1px_0_rgba(15,23,42,0.02)] dark:border-slate-800 dark:bg-slate-950">
      <div className="flex flex-col gap-2">
        <WorkspaceComposerSuggestionPopup
          suggestions={composerSuggestions}
          activeIndex={composerSuggestionIndex}
          onActiveIndexChange={setComposerSuggestionIndex}
          onSelect={applyComposerSuggestion}
        />
        {composerAttachments.length > 0 && (
          <div className="flex min-h-16 flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/80 p-2 dark:border-slate-800 dark:bg-slate-900/70">
            {composerAttachments.map((attachment) => {
              const attachmentName = resolveAttachmentName(attachment.name, attachment.mimeType);
              const attachmentSize = formatAttachmentSize(attachment.size);
              return (
                <div
                  key={attachment.id}
                  className="relative h-14 w-20 shrink-0 overflow-hidden rounded-md border border-slate-300 bg-slate-100 shadow-sm dark:border-slate-700 dark:bg-slate-950"
                  title={attachmentSize ? `${attachmentName} · ${attachmentSize}` : attachmentName}
                >
                  {attachment.dataUrl ? (
                    <img
                      src={attachment.dataUrl}
                      alt={attachmentName}
                      className="h-full w-full object-cover"
                      draggable={false}
                    />
                  ) : (
                    <div className="grid h-full w-full place-items-center text-slate-500 dark:text-slate-400">
                      <ImageIcon size={18} />
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => removeComposerAttachment(attachment.id)}
                    className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-full border border-slate-200 bg-white/95 text-slate-600 shadow-sm transition hover:border-red-200 hover:text-red-600 dark:border-slate-700 dark:bg-slate-950/95 dark:text-slate-300 dark:hover:border-red-900 dark:hover:text-red-200"
                    title={`Remove ${attachmentName}`}
                    aria-label={`Remove ${attachmentName}`}
                  >
                    <X size={11} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
        {composerReference && <div className="wb-composer-reference" aria-label="Referenced passage">
          <div><strong>Reference · {composerReference.title}</strong><button type="button" aria-label="Remove reference" onClick={() => setComposerReferences((current) => { const next = { ...current }; delete next[referenceKey]; return next; })}><X size={14} /></button></div>
          <blockquote>{composerReference.text}</blockquote>
        </div>}
        <textarea
          wrap="soft"
          ref={composerRef}
          value={input}
          onChange={handleComposerInputChange}
          onPaste={handleComposerPaste}
          onKeyDown={handleComposerKeyDown}
          onKeyUp={handleComposerSelectionChange}
          onClick={handleComposerSelectionChange}
          onSelect={handleComposerSelectionChange}
          onFocus={handleComposerSelectionChange}
          placeholder={composerPlaceholder}
          rows={1}
          className="max-h-32 min-h-11 w-full resize-none rounded-lg border border-slate-200 bg-slate-50/90 px-3 py-2.5 text-sm leading-6 outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:bg-white focus:shadow-sm dark:border-slate-800 dark:bg-slate-900"
        />
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex h-7 shrink-0 items-center gap-0.5 rounded-md border border-slate-200 bg-slate-100/70 p-0.5 shadow-inner dark:border-slate-800 dark:bg-slate-900">
            {(["steer", "queue"] as const).map((mode) => {
              const queueUnavailable = mode === "queue" && !hasActiveRun;
              const active =
                mode === "steer"
                  ? activeRunPlacement === "steer" || !hasActiveRun
                  : hasActiveRun && activeRunPlacement === "queue";
              return (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setActiveRunPlacement(mode)}
                  disabled={queueUnavailable || (mode === "steer" && hasActiveRun && selectedAgentId !== "native")}
                  title={
                    mode === "steer" && hasActiveRun && selectedAgentId !== "native" ? "Native lead follow-ups run after the current turn" : mode === "queue"
                      ? hasActiveRun
                        ? "Queue this message after the current run (Option+Enter)"
                        : "Next is available while a run is active"
                      : hasActiveRun
                        ? "Steer the active run now (Enter)"
                        : "Start work with this message"
                  }
                  className={cx(
                    "inline-flex h-6 items-center gap-1 rounded px-1.5 text-[11px] font-semibold capitalize transition disabled:cursor-not-allowed disabled:opacity-40 sm:px-2",
                    active
                      ? "bg-white text-slate-950 shadow-sm dark:bg-slate-100 dark:text-slate-950"
                      : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200",
                  )}
                >
                  {mode === "queue" ? <Clock3 size={11} /> : <WandSparkles size={11} />}
                  {workspaceComposerPlacementLabel(mode, hasActiveRun)}
                </button>
              );
            })}
          </div>
          <div className="min-w-0 flex-1" />
          <div
            className="relative shrink-0"
            onBlur={(event) => {
              const nextFocus = event.relatedTarget;
              if (nextFocus instanceof Node && event.currentTarget.contains(nextFocus)) {
                return;
              }
              setAutonomyMenuOpen(false);
            }}
          >
            <button
              type="button"
              onClick={() => setAutonomyMenuOpen((open) => !open)}
              className="inline-flex h-7 min-w-[4.5rem] max-w-[6.5rem] items-center justify-center gap-1.5 rounded-md border border-slate-200 bg-slate-50/95 px-2 text-[11px] font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:bg-white dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-slate-700 sm:min-w-20 sm:max-w-[7rem]"
              title={selectedAutonomyOption.description}
              aria-expanded={autonomyMenuOpen}
            >
              <AutonomyIcon mode={selectedAutonomyOption.id} size={12} />
              <span className="truncate">{selectedAutonomyOption.shortLabel}</span>
              <ChevronDown size={12} className={cx("transition", autonomyMenuOpen && "rotate-180")} />
            </button>
            {autonomyMenuOpen && (
              <div className="absolute bottom-full right-0 z-50 mb-2 w-64 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-xl shadow-slate-950/10 dark:border-slate-800 dark:bg-slate-950">
                {WORKSPACE_AUTONOMY_OPTIONS.map((option) => {
                  const selected = option.id === selectedAutonomyOption.id;
                  return (
                    <button
                      key={option.id}
                      type="button"
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => {
                        setAutonomyMode(option.id);
                        setAutonomyMenuOpen(false);
                      }}
                      className={cx(
                        "flex w-full items-start gap-2 px-2.5 py-2 text-left transition",
                        selected
                          ? "bg-slate-100 text-slate-950 dark:bg-slate-800 dark:text-white"
                          : "text-slate-600 hover:bg-slate-50 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900 dark:hover:text-white",
                      )}
                    >
                      <AutonomyIcon mode={option.id} size={13} className="mt-0.5 shrink-0" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-semibold">{option.label}</span>
                        <span className="block text-[11px] font-medium leading-4 text-slate-500 dark:text-slate-400">
                          {option.description}
                        </span>
                      </span>
                      {selected && <Check size={13} className="mt-0.5 shrink-0" />}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <LeadAgentMenu selected={selectedAgentId} onChange={setSelectedAgentId} disabled={hasActiveRun || sending}
            profiles={leadProfiles} onProfilesChange={setLeadProfiles}
            modelId={selectedModelOption.id} modelOptions={workspaceModelOptionsForAgent("native")}
            onModelChange={(id) => setModelSelectionsByAgent((current) => ({ ...current, native: id }))} />
          <NativeWorkerSettings profiles={nativeWorkerProfiles} onChange={setNativeWorkerProfiles} />
          <button
            type="button"
            onClick={() => {
              if (composerActionIsStop && activeThread && activeRunningTask) {
                void stopSessionRun(
                  {
                    id: activeThread.id,
                    workflow_id: activeThread.workflowId,
                    title: activeThread.title || "Active session",
                    message_count: messages.length,
                    created_at: "",
                    updated_at: "",
                  },
                  activeRunningTask,
                );
                return;
              }
              void submit();
            }}
            disabled={sending || (!composerActionIsStop && !composerHasPayload)}
            className={cx(
              "inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2.5 text-[11px] font-semibold shadow-sm transition disabled:cursor-not-allowed disabled:opacity-40",
              composerActionIsStop
                ? "dan-composer-stop-button border border-red-200 bg-red-50 text-red-700 hover:border-red-300 hover:bg-white dark:border-red-900 dark:bg-red-950/35 dark:text-red-200 dark:hover:bg-red-950/55"
                : "bg-slate-950 text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-950 dark:hover:bg-white",
            )}
            title={composerActionIsStop ? "Stop running session" : undefined}
            aria-label={composerActionIsStop ? "Stop running session" : undefined}
          >
            {composerActionIsStop ? (
              <Square size={12} />
            ) : sending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Send size={12} />
            )}
            {workspaceComposerPrimaryActionLabel({
              hasActiveRun,
              placement: activeRunPlacement,
              isStop: composerActionIsStop,
            })}
          </button>
        </div>
      </div>
    </div>
  );

  const visiblePreviewFileEntry =
    !promptLogPreview && !selectedBlueprintNode && !selectedChunk ? activePreviewFileEntry : null;
  const activePreviewExternalUrl = useMemo(
    () =>
      visiblePreviewFileEntry
        ? absoluteWorkspacePreviewUrl(visiblePreviewFileEntry, developmentRoot)
        : "",
    [developmentRoot, visiblePreviewFileEntry],
  );
  const canOpenPreview = Boolean(promptLogPreview?.path || activePreviewExternalUrl);

  const openActiveFile = useCallback(() => {
    if (promptLogPreview?.path) {
      void nativeShell.openPath(promptLogPreview.path);
      return;
    }
    if (activePreviewExternalUrl) void nativeShell.openExternal(activePreviewExternalUrl);
  }, [activePreviewExternalUrl, promptLogPreview?.path]);

  const selectPreviewFile = useCallback(
    (entry: WorkspaceFileEntry) => {
      setActiveFilePath(entry.path);
      setSelectedBlueprintNodeId(null);
      setSelectedChunkId(null);
      setPromptLogPreview(null);
      setShowSidecarPreview(true);
      if (isPhoneViewport) setPhonePage("preview");
    },
    [isPhoneViewport],
  );

  useEffect(() => {
    const handler = (event: globalThis.KeyboardEvent) => {
      if (
        activePane === "notes" &&
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "s"
      ) {
        event.preventDefault();
        void saveNoteNow(activeNote, true);
        return;
      }

      const workspaceShortcut = event.altKey && event.shiftKey && !event.metaKey && !event.ctrlKey;
      if (!workspaceShortcut) return;
      const code = event.code;

      if (/^Digit[1-9]$/.test(code)) {
        const workspaceAtIndex = workspaceSlots[Number(code.replace("Digit", "")) - 1];
        if (!workspaceAtIndex) return;
        event.preventDefault();
        switchWorkbenchProject(workspaceAtIndex.id);
        setActivePane("work");
        return;
      }

      if (code === "Backquote") {
        event.preventDefault();
        setActivePane((pane) => (pane === "work" ? "notes" : "work"));
        return;
      }

      if (code === "KeyN") {
        event.preventDefault();
        setActivePane("notes");
        return;
      }

      if (code === "KeyW") {
        event.preventDefault();
        setActivePane("work");
        return;
      }

      if (code === "KeyF") {
        event.preventDefault();
        setActivePane("work");
        setShowFileExplorer((visible) => !visible);
        return;
      }

      if (code === "KeyS") {
        event.preventDefault();
        setActivePane("work");
        setShowSessionRail((visible) => !visible);
        return;
      }

      if (code === "KeyC") {
        event.preventDefault();
        setActivePane("work");
        setShowConversationChunks((visible) => !visible);
        return;
      }

      if (code === "KeyL") {
        event.preventDefault();
        setActivePane("work");
        setShowConversationChunks(true);
        window.setTimeout(() => composerRef.current?.focus(), 0);
        return;
      }

      if (code === "KeyP") {
        event.preventDefault();
        setActivePane("work");
        setShowSidecarPreview((visible) => !visible);
        return;
      }

      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        if (workspaceSlots.length < 2) return;
        const currentIndex = workspaceSlots.findIndex((item) => item.id === activeWorkspaceId);
        if (currentIndex < 0) return;
        const nextIndex =
          event.key === "ArrowRight"
            ? (currentIndex + 1) % workspaceSlots.length
            : (currentIndex - 1 + workspaceSlots.length) % workspaceSlots.length;
        switchWorkbenchProject(workspaceSlots[nextIndex].id);
        setActivePane("work");
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activeNote, activePane, activeWorkspaceId, saveNoteNow, setActiveWorkspace, switchWorkbenchProject, workspaceSlots]);

  useEffect(() => {
    return () => {
      streamRef.current?.close();
      agentStreamRef.current?.close();
    };
  }, []);

  const registerSessionGroup = (group: SessionGroup) => {
    if (group.workspaceId) return group.workspaceId;
    const id = createWorkspace(group.name, "chat", false);
    // A filtered sidebar may show only part of the recovered project's history.
    const complete = buildSessionGroups({ threads, workspaces: allWorkspaces, threadQuery: "", threadWorkspaces, taskWorkspaceByThreadId, taskWorkspaceRootByThreadId })
      .flatMap((item) => item.subgroups ?? [item]).find((item) => item.id === group.id) ?? group;
    updateWorkspace(id, { pinnedPaths: group.id.startsWith("project-root:") && group.root ? [group.root] : [], openThreadIds: complete.threads.map((thread) => thread.id) });
    setThreadWorkspaces((previous) => ({ ...previous, ...Object.fromEntries(complete.threads.map((thread) => [threadWorkspaceKey(thread.workflow_id, thread.id), id])) }));
    return id;
  };

  const renderSessionThreadRows = (group: SessionGroup) =>
    group.threads.map((thread) => {
      const threadRunningTask = runningTaskByThreadId.get(thread.id);
      const threadIsRunning = Boolean(threadRunningTask);
      const threadTasks = tasksByThreadId.get(thread.id) ?? [];
      const sessionDisplay = sessionCardDisplay(thread, threadTasks);
      const archived = Boolean(thread.archived || group.archived);
      const active = !archived && activeThread?.id === thread.id;
      const sessionKey = threadWorkspaceKey(thread.workflow_id, thread.id);
      const hasNewReadyResponse = Boolean(
        !archived &&
          !active &&
          sessionHasNewReadyResponse(threadTasks, sessionResponseSeen[sessionKey]),
      );
      const swipeOffset = sessionSwipeOffsets[sessionKey] ?? 0;
      return (
        <div
          key={sessionKey}
          className="relative overflow-hidden rounded-lg"
          onPointerDown={(event) => beginSessionSwipe(event, thread, archived)}
          onPointerMove={moveSessionSwipe}
          onPointerUp={endSessionSwipe}
          onPointerCancel={endSessionSwipe}
        >
          <div
            className={cx(
              "pointer-events-none absolute inset-y-0 flex items-center px-3 text-[10px] font-semibold uppercase tracking-[0.14em]",
              archived
                ? "left-0 text-slate-500 dark:text-slate-400"
                : "right-0 text-slate-500 dark:text-slate-400",
            )}
          >
            {archived ? "Restore" : "Archive"}
          </div>
          <div
            className={cx(
              "group/session relative flex w-full min-w-0 items-start gap-1 overflow-hidden rounded-lg border transition",
              threadIsRunning && "dan-session-live-card",
              active
                ? "border-slate-900 bg-white text-slate-950 shadow-sm dark:border-slate-100 dark:bg-slate-900 dark:text-slate-100"
                : "border-transparent bg-slate-50 text-slate-600 hover:border-slate-200 hover:bg-white hover:shadow-sm dark:bg-slate-950/40 dark:text-slate-300 dark:hover:border-slate-800 dark:hover:bg-slate-900",
            )}
            data-active={active || undefined}
            style={{
              transform: swipeOffset ? `translateX(${swipeOffset}px)` : undefined,
            }}
          >
            <button
              type="button"
              onClick={(event) => {
                if (suppressSessionClickRef.current === sessionKey) {
                  suppressSessionClickRef.current = null;
                  event.preventDefault();
                  return;
                }
                if (archived) {
                  setStatus("Restore session to view it");
                  return;
                }
                void openSession(thread, group.workspaceId, group.root);
                setPhonePage("chat");
              }}
              className="flex min-w-0 flex-1 items-start gap-2 px-2.5 py-2 text-left"
              title={sessionDisplay.detail}
            >
              {hasNewReadyResponse && (
                <span
                  aria-hidden="true"
                  className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500"
                />
              )}
              <span className="min-w-0 flex-1">
                <span className="dan-rail-card-title block truncate">{sessionDisplay.title}</span>
              </span>
            </button>
            <div className="flex shrink-0 items-center gap-0.5 py-1 pr-1">
              {!archived && (
                <button
                  type="button"
                  data-session-action
                  onClick={() => void openSessionPromptLog(thread)}
                  title="View prompt log"
                  aria-label="View prompt log"
                  className="grid h-6 w-6 place-items-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                >
                  <ScrollText size={12} />
                </button>
              )}
              {archived && (
                <button
                  type="button"
                  data-session-action
                  onClick={() => void deleteArchivedSession(thread)}
                  title="Delete permanently"
                  aria-label="Delete session permanently"
                  className="grid h-6 w-6 place-items-center rounded-md text-slate-400 transition hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-300"
                >
                  <Trash2 size={12} />
                </button>
              )}
              <button
                type="button"
                data-session-action
                onClick={() => void archiveSession(thread, !archived)}
                title={archived ? "Restore session" : "Archive session"}
                aria-label={archived ? "Restore session" : "Archive session"}
                className="grid h-6 w-6 place-items-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
              >
                {archived ? <ChevronRight size={12} /> : <Archive size={12} />}
              </button>
              {threadIsRunning && (
                <>
                  <button
                    type="button"
                    data-session-action
                    data-session-live
                    onClick={() => void viewSessionProgress(thread, group.workspaceId, group.root)}
                    title="View progress"
                    aria-label="View progress"
                    className="dan-session-live-button grid h-6 w-6 place-items-center rounded-md text-blue-500 transition hover:bg-blue-50 hover:text-blue-700 dark:text-blue-300 dark:hover:bg-blue-950/40 dark:hover:text-blue-100"
                  >
                    <Activity size={12} className="dan-session-live-icon" />
                  </button>
                  <button
                    type="button"
                    data-session-action
                    data-session-live
                    onClick={() => void stopSessionRun(thread, threadRunningTask)}
                    title="Stop running session"
                    aria-label="Stop running session"
                    className="grid h-6 w-6 place-items-center rounded-md text-slate-400 transition hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-300"
                  >
                    <Square size={11} />
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      );
    });

  return (
    <div
      className={cx(
        activePane === "notes" && workspaceSurfaceThemeClass,
        activePane === "work" && "wb-shell",
        isElectron() && "dan-native-window",
        "dan-phone-workspace flex h-screen flex-col overflow-hidden bg-[#f4f7fb] text-slate-950 antialiased dark:bg-slate-950 dark:text-slate-100",
      )}
    >
      {activePane === "work" && <header className="wb-header">
        <WorkbenchNavigation
          sidebarOpen={renderSessionRail}
          onToggleSidebar={() => { setShowSessionRail(!renderSessionRail); setShowFileExplorer(false); setPhonePage(renderSessionRail ? "chat" : "sessions"); }}
          projects={workbenchProjects}
          activeProjectId={activeWorkspaceId}
          activeSessionId={activeThread ? threadWorkspaceKey(activeThread.workflowId, activeThread.id) : null}
          onProject={switchWorkbenchProject}
          onSession={(id) => {
            const thread = threads.find((item) => threadWorkspaceKey(item.workflow_id, item.id) === id);
            if (thread) { void openSession(thread, activeWorkspaceId, workspace?.pinnedPaths[0]); setPhonePage("chat"); }
          }}
          onNewSession={() => { void startNewSession(); setPhonePage("chat"); }}
          onNewProject={() => { setCreatingProject(true); setWorkbenchSettings(true); }}
          onAllSessions={() => { setThreadQuery(""); setShowSessionRail(true); setShowFileExplorer(false); setPhonePage("sessions"); }}
        />
        <div className="wb-header-actions">
          <button aria-label="Sidecar chat" title="Sidecar chat" disabled={!activeThread} aria-pressed={sidecarChat} onClick={() => { setSidecarChat(!sidecarChat); setTeamPanel(false); setWorkbenchActivity(false); setShowSidecarPreview(false); setSidecarSelection({text:"",token:Date.now()}); }}><MessageSquareText size={17} /></button>
          <button aria-label="Files" title="Files" aria-pressed={showFileExplorer} onClick={() => { setShowFileExplorer(!showFileExplorer); setShowSessionRail(false); setPhonePage("files"); }}><Folder size={17} /></button>
          <button aria-label="Agent activity" title="Agents & tools" aria-pressed={workbenchActivity} onClick={() => { setWorkbenchActivity(!workbenchActivity); setTeamPanel(false); setShowSidecarPreview(false); setSidecarChat(false); }}><Activity size={17} /></button>
          <button aria-label="Preview" title="Preview" aria-pressed={showSidecarPreview} onClick={() => { setShowSidecarPreview(!showSidecarPreview); setTeamPanel(false); setWorkbenchActivity(false); setSidecarChat(false); setPhonePage("preview"); }}><PanelRight size={17} /></button>
          <button aria-label="Notes" title="Notes" onClick={() => { setActivePane("notes"); setPhonePage("note-preview"); }}><NotebookPen size={17} /></button>
          <button aria-label="Workspace settings" title="Workspace settings" aria-pressed={workbenchSettings} onClick={() => { setCreatingProject(false); setWorkbenchSettings(true); }}><MoreHorizontal size={18} /></button>
        </div>
      </header>}
      {danSettings && <DanSettings onClose={() => setDanSettings(false)} profiles={nativeWorkerProfiles} onProfilesChange={setNativeWorkerProfiles} />}
      {importNativeSessions && <ImportNativeSessions workspace={importNativeSessions.root} workspaceId={importNativeSessions.id} onClose={() => setImportNativeSessions(null)} onImport={async (thread) => { bindThreadToWorkspace(thread.workflow_id, thread.id, importNativeSessions.id); await refreshThreads(); }} />}
      {activePane === "work" && workbenchSettings && <ProjectSettings
        name={creatingProject ? "" : workspace?.name || "Project"}
        root={creatingProject ? "" : workspace?.pinnedPaths[0] || ""}
        creating={creatingProject}
        onClose={() => setWorkbenchSettings(false)}
        onBrowse={() => nativeDialog.openDirectory()}
        onSave={(name, root) => {
          const id = creatingProject ? createWorkspace(name, "chat") : activeWorkspaceId;
          if (id) {
            updateWorkspace(id, { name, pinnedPaths: root ? [root, ...(creatingProject ? [] : workspace?.pinnedPaths.slice(1) ?? [])] : [] });
            if (creatingProject) { void startNewSession(id); setActiveFilePath(null); }
          }
          setWorkbenchSettings(false); setShowSessionRail(true); setShowFileExplorer(false); setShowConversationChunks(true); setPhonePage("chat");
        }}
      />}
      {Boolean(activePane === "notes") && (
      <header className="dan-workspace-header relative z-40 flex h-14 shrink-0 items-center justify-between border-b border-slate-200/80 bg-white/95 px-4 shadow-[0_1px_0_rgba(15,23,42,0.03)] backdrop-blur dark:border-slate-800 dark:bg-slate-950/95">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="inline-flex shrink-0 rounded-lg border border-slate-200 bg-slate-100/70 p-0.5 shadow-inner dark:border-slate-800 dark:bg-slate-900">
            <button
              type="button"
              onClick={() => {
                setActivePane("work");
                setPhonePage("chat");
              }}
              title="Work workspace (⌥⇧W)"
              className={cx(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[13px] font-semibold transition",
                activePane === "work"
                  ? "bg-white text-slate-950 shadow-sm dark:bg-slate-100 dark:text-slate-950"
                  : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200",
              )}
            >
              <TerminalSquare size={14} />
              Work
            </button>
            <button
              type="button"
              onClick={() => {
                setActivePane("notes");
                setPhonePage("note-preview");
              }}
              title="Notes workspace (⌥⇧N)"
              className={cx(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[13px] font-semibold transition",
                activePane === "notes"
                  ? "bg-white text-slate-950 shadow-sm dark:bg-slate-100 dark:text-slate-950"
                  : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200",
              )}
            >
              <NotebookPen size={14} />
              Notes
            </button>
          </div>
          <div className="min-w-0 flex-1">
            {activePane === "work" ? (
              <>
                <div className="flex min-w-0 items-center gap-1.5">
                  <label htmlFor="dan-workspace-switcher" className="sr-only">
                    Workspace
                  </label>
                  <select
                    id="dan-workspace-switcher"
                    value={activeWorkspaceId ?? ""}
                    onChange={(event) => {
                      if (!event.target.value) return;
                      switchWorkbenchProject(event.target.value);
                      setActivePane("work");
                      setPhonePage("chat");
                    }}
                    className="min-w-0 max-w-[260px] rounded-md border border-transparent bg-transparent py-0 pr-7 text-[15px] font-semibold leading-5 text-slate-950 outline-none transition hover:border-slate-200 hover:bg-slate-50 focus:border-slate-300 focus:bg-white dark:text-slate-100 dark:hover:border-slate-800 dark:hover:bg-slate-900 dark:focus:bg-slate-950"
                    title="Switch workspace"
                  >
                    {workspaces.map((item) => (
                      <option key={item.id} value={item.id}>
                        {workspaceDisplayName(item)}
                      </option>
                    ))}
                  </select>
                </div>
                <div
                  className="relative mt-0.5 min-w-0"
                  style={{
                    width: rootPickerWidth,
                    maxWidth: "100%",
                  }}
                >
                  <label className="dan-workspace-root-inline flex h-5 min-w-0 items-center gap-1 rounded-md border border-transparent pr-1 text-[11px] leading-4 text-slate-500 transition">
                    <Folder size={10} className="shrink-0 text-slate-400/80" />
                    <span className="shrink-0 text-slate-400">Root</span>
                    <input
                      value={rootInput}
                      onFocus={() => {
                        setRootEditing(true);
                        setRootInput(developmentRoot);
                      }}
                      onBlur={() => {
                        window.setTimeout(() => setRootEditing(false), 120);
                      }}
                      onChange={(event) => setRootInput(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") applyDevelopmentRoot(rootInput);
                        if (event.key === "Escape") {
                          closeRootPicker();
                        }
                      }}
                      placeholder="/path/to/workspace"
                      className="dan-workspace-root-path-input min-w-0 flex-1 bg-transparent font-mono text-[11px] outline-none placeholder:text-slate-400"
                      title="Type workspace root and press Enter"
                    />
                    {loadingRoots && rootEditing && (
                      <Loader2 size={11} className="shrink-0 animate-spin text-slate-400" />
                    )}
                  </label>
                  {rootEditing && (
                    <div className="absolute left-0 top-6 z-[70] w-full overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg shadow-slate-950/10 dark:border-slate-800 dark:bg-slate-950">
                      <div className="flex items-center justify-between gap-1 border-b border-slate-200 px-2 py-1.5 dark:border-slate-800">
                        <div className="min-w-0">
                          <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
                            Workspace Roots
                          </div>
                          <div className="truncate font-mono text-[10px] leading-4 text-slate-400">
                            {rootBrowsePath || "Choose a folder"}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-1">
                          <button
                            type="button"
                            onMouseDown={(event) => {
                              event.preventDefault();
                              applyDevelopmentRoot(rootInput);
                            }}
                            disabled={!normalizeRootPath(rootInput)}
                            className="grid h-6 w-6 place-items-center rounded-md border border-slate-200 text-slate-500 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-800 dark:hover:bg-slate-900 dark:hover:text-slate-100"
                            title="Use this folder as workspace root"
                            aria-label="Use this folder as workspace root"
                          >
                            <Check size={12} />
                          </button>
                          <button
                            type="button"
                            onMouseDown={(event) => {
                              event.preventDefault();
                              browseDevelopmentRoot(rootParentPath);
                            }}
                            disabled={!rootParentPath}
                            className="grid h-6 w-6 place-items-center rounded-md border border-slate-200 text-slate-500 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-800 dark:hover:bg-slate-900 dark:hover:text-slate-100"
                            title="Go to parent folder"
                            aria-label="Go to parent folder"
                          >
                            <ArrowUp size={12} />
                          </button>
                          <button
                            type="button"
                            onMouseDown={(event) => {
                              event.preventDefault();
                              closeRootPicker();
                            }}
                            className="grid h-6 w-6 place-items-center rounded-md border border-slate-200 text-slate-500 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 dark:border-slate-800 dark:hover:bg-slate-900 dark:hover:text-slate-100"
                            title="Close workspace root picker"
                            aria-label="Close workspace root picker"
                          >
                            <X size={12} />
                          </button>
                        </div>
                      </div>
                      <div
                        className="overflow-auto p-1.5"
                        style={{ maxHeight: `min(${rootPickerHeight}px, calc(100vh - 150px))` }}
                      >
                        {rootParentPath && (
                          <button
                            type="button"
                            onMouseDown={(event) => {
                              event.preventDefault();
                              browseDevelopmentRoot(rootParentPath);
                            }}
                            className="flex w-full min-w-0 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-slate-600 transition hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900 dark:hover:text-slate-100"
                          >
                            <ArrowUp size={13} className="shrink-0 text-slate-400" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate font-semibold">Parent folder</span>
                              <span className="block truncate font-mono text-[10px] text-slate-400">
                                {rootParentPath}
                              </span>
                            </span>
                            <span className="shrink-0 rounded-full border border-slate-200 px-1.5 py-0.5 text-[10px] capitalize text-slate-400 dark:border-slate-800">
                              up
                            </span>
                          </button>
                        )}
                        {rootOptions.length > 0 ? (
                          rootOptions.slice(0, 10).map((option) => (
                            <div
                              key={option.path}
                              className="flex min-w-0 items-center gap-1 rounded-lg text-xs text-slate-600 transition hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-900"
                            >
                              <button
                                type="button"
                                onMouseDown={(event) => {
                                  event.preventDefault();
                                  applyDevelopmentRoot(option.path);
                                }}
                                className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-2 text-left transition hover:text-slate-950 dark:hover:text-slate-100"
                                title="Use as workspace root"
                              >
                                <Folder size={13} className="shrink-0 text-slate-400" />
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate font-semibold">{option.name}</span>
                                  <span className="block truncate font-mono text-[10px] text-slate-400">
                                    {option.path}
                                  </span>
                                </span>
                                <span className="shrink-0 rounded-full border border-slate-200 px-1.5 py-0.5 text-[10px] capitalize text-slate-400 dark:border-slate-800">
                                  {option.kind}
                                </span>
                              </button>
                              <button
                                type="button"
                                onMouseDown={(event) => {
                                  event.preventDefault();
                                  browseDevelopmentRoot(option.path);
                                }}
                                className="mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-md text-slate-400 transition hover:bg-white hover:text-slate-900 dark:hover:bg-slate-950 dark:hover:text-slate-100"
                                title="Browse inside this folder"
                                aria-label={`Browse inside ${option.name}`}
                              >
                                <ChevronRight size={13} />
                              </button>
                            </div>
                          ))
                        ) : (
                          <div className="px-2.5 py-3 text-xs text-slate-400">
                            {loadingRoots ? "Looking for folders..." : "No matching folders."}
                          </div>
                        )}
                      </div>
                      <div
                        role="separator"
                        aria-label="Resize workspace root picker"
                        title="Resize picker"
                        onPointerDown={startRootPickerResize}
                        className="absolute bottom-0 right-0 h-5 w-5 cursor-nwse-resize rounded-tl-md text-slate-300 transition hover:bg-slate-100 hover:text-slate-500 dark:hover:bg-slate-900"
                      >
                        <span className="absolute bottom-1 right-1 h-2.5 w-2.5 border-b border-r border-current" />
                        <span className="absolute bottom-1 right-1 h-1.5 w-1.5 border-b border-r border-current" />
                      </div>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <>
                <div className="truncate text-[15px] font-semibold leading-5">
                  Content Workspace
                </div>
                <div className="truncate text-[11px] leading-4 text-slate-500">
                  {notesRoot || "local content"} · {noteStatusText(activeNote)}
                </div>
              </>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {activePane === "work" && (
            <div className="hidden items-center gap-1 md:flex">
              <button
                type="button"
                onClick={() => setShowSessionRail((visible) => !visible)}
                title="Toggle sessions (⌥⇧S)"
                aria-label="Toggle sessions"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showSessionRail
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <PanelLeft size={14} />
              </button>
              <button
                type="button"
                onClick={() => setShowFileExplorer((visible) => !visible)}
                title="Toggle files (⌥⇧F)"
                aria-label="Toggle files"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showFileExplorer
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <Folder size={14} />
              </button>
              <button
                type="button"
                onClick={() => setShowConversationChunks((visible) => !visible)}
                title="Toggle work panel (⌥⇧C)"
                aria-label="Toggle work panel"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showConversationChunks
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <Cable size={14} />
              </button>
              <button
                type="button"
                onClick={() => setShowSidecarPreview((visible) => !visible)}
                title="Toggle preview panel (⌥⇧P)"
                aria-label="Toggle preview panel"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showSidecarPreview
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <PanelRight size={14} />
              </button>
            </div>
          )}
          {activePane === "notes" && (
            <div className="hidden items-center gap-1 md:flex">
              <button
                type="button"
                onClick={() => setShowNotesRail((visible) => !visible)}
                title="Toggle notes list"
                aria-label="Toggle notes list"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showNotesRail
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <PanelLeft size={14} />
              </button>
              <button
                type="button"
                onClick={() => {
                  if (showNoteEditor && !showNotesPreview) setShowNotesPreview(true);
                  setShowNoteEditor((visible) => !visible);
                }}
                title="Toggle note editor"
                aria-label="Toggle note editor"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showNoteEditor
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <FileText size={14} />
              </button>
              <button
                type="button"
                onClick={() => {
                  if (showNotesPreview && !showNoteEditor) setShowNoteEditor(true);
                  setShowNotesPreview((visible) => !visible);
                }}
                title="Toggle note preview"
                aria-label="Toggle note preview"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showNotesPreview
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <PanelRight size={14} />
              </button>
              <button
                type="button"
                onClick={() => setShowLearnPanel((visible) => !visible)}
                title="Toggle Learn"
                aria-label="Toggle Learn"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showLearnPanel
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <Lightbulb size={14} />
              </button>
            </div>
          )}
          <button
            type="button"
            onClick={() => void refreshWireGuardStatus()}
            className={cx(
              "dan-wireguard-button relative grid h-8 w-8 place-items-center rounded-lg border shadow-sm transition",
              wireGuardUi.tone === "ok"
                ? "border-emerald-300 bg-emerald-50 text-emerald-700 hover:border-emerald-400 hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-950/35 dark:text-emerald-200 dark:hover:border-emerald-700 dark:hover:bg-emerald-950/50"
                : wireGuardUi.tone === "warn"
                  ? "border-amber-300 bg-amber-50 text-amber-700 hover:border-amber-400 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/35 dark:text-amber-200 dark:hover:border-amber-700 dark:hover:bg-amber-950/50"
                  : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:border-slate-700 dark:hover:text-slate-200",
            )}
            title={
              wireGuardStatus
                ? `${wireGuardUi.label} ${wireGuardUi.detail}. ${wireGuardStatus.interface}: ${wireGuardStatus.conflict_policy}`
                : "WireGuard checking. Read-only status"
            }
            aria-label={`Refresh WireGuard status: ${wireGuardUi.detail}`}
          >
            {wireGuardLoading ? (
              <Loader2 size={14} className="shrink-0 animate-spin" />
            ) : (
              <Shield size={14} className="shrink-0" />
            )}
            <span
              aria-hidden="true"
              className={cx(
                "absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full ring-2",
                wireGuardUi.tone === "ok"
                  ? "bg-emerald-500 ring-emerald-50 dark:bg-emerald-400 dark:ring-emerald-950"
                  : wireGuardUi.tone === "warn"
                    ? "bg-amber-500 ring-amber-50 dark:bg-amber-400 dark:ring-amber-950"
                    : "bg-slate-400 ring-white dark:bg-slate-500 dark:ring-slate-950",
              )}
            />
            <span className="sr-only">
              {wireGuardUi.label} {wireGuardUi.detail}
            </span>
          </button>
          <span
            className={cx(
              "inline-flex max-w-[180px] items-center gap-1.5 truncate rounded-full border px-2.5 py-1 text-xs shadow-sm",
              activeRunningTask
                ? "border-amber-300 bg-amber-50 font-semibold text-amber-700 dark:border-amber-800 dark:bg-amber-950/35 dark:text-amber-200"
                : "border-slate-200 bg-white font-medium text-slate-500 dark:border-slate-800 dark:bg-slate-900",
            )}
            title={activeRunningTask ? "Agent running" : status}
          >
            {activeRunningTask ? (
              <>
                <Loader2 size={12} className="shrink-0 animate-spin" />
                <span className="truncate">Agent</span>
              </>
            ) : (
              <span className="truncate">{status}</span>
            )}
          </span>
        </div>
      </header>)}

      {activePane === "notes" ? (
        <section
          className="dan-notes-pages grid min-h-0 flex-1 bg-[#f4f7fb] dark:bg-slate-950"
          style={{ gridTemplateColumns: effectiveNotesGridTemplate }}
        >
          {!isPhoneViewport && !renderNotesRail && (
            <CollapsedPaneRail
              label="Content"
              title="Show content pane"
              onClick={() => setShowNotesRail(true)}
            >
              <NotebookPen size={14} />
            </CollapsedPaneRail>
          )}
          {renderNotesRail && (
          <aside className="dan-phone-page relative flex min-h-0 flex-col border-r border-slate-200/80 bg-white/85 dark:border-slate-800 dark:bg-slate-950">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-3 dark:border-slate-800">
              <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Content
              </div>
              <div className="flex items-center gap-1">
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setNoteCreateMenuOpen((open) => !open)}
                    onBlur={() => window.setTimeout(() => setNoteCreateMenuOpen(false), 120)}
                    className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                    title="Create note or folder"
                    aria-label="Create note or folder"
                  >
                    <Plus size={14} />
                  </button>
                  {noteCreateMenuOpen && (
                    <div className="absolute right-0 top-8 z-40 w-44 overflow-hidden rounded-xl border border-slate-200 bg-white p-1.5 text-xs shadow-xl shadow-slate-950/10 dark:border-slate-800 dark:bg-slate-950">
                      <button
                        type="button"
                        onMouseDown={(event) => {
                          event.preventDefault();
                          setNoteCreateMenuOpen(false);
                          void createNote("note");
                        }}
                        className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-slate-600 transition hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900"
                      >
                        <FileText size={13} />
                        <span>New note</span>
                      </button>
                      <button
                        type="button"
                        onMouseDown={(event) => {
                          event.preventDefault();
                          setNoteCreateMenuOpen(false);
                          void createNote("folder");
                        }}
                        className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-slate-600 transition hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900"
                      >
                        <FolderPlus size={13} />
                        <span>New folder</span>
                      </button>
                    </div>
                  )}
                </div>
                {!isPhoneViewport && (
                  <PaneHeaderButton
                    title="Collapse content pane"
                    onClick={() => setShowNotesRail(false)}
                  >
                    <ChevronRight size={13} className="rotate-180" />
                  </PaneHeaderButton>
                )}
              </div>
            </div>
            <div className="space-y-2 border-b border-slate-200/80 p-3 dark:border-slate-800">
              <input
                value={noteQuery}
                onChange={(event) => setNoteQuery(event.target.value)}
                placeholder="Search content"
                className="h-9 w-full rounded-lg border border-slate-200 bg-slate-50/90 px-2.5 text-[13px] outline-none shadow-inner transition placeholder:text-slate-400 focus:border-slate-400 focus:bg-white dark:border-slate-800 dark:bg-slate-900"
              />
              <div className="grid grid-cols-3 rounded-lg border border-slate-200 bg-slate-100/70 p-0.5 text-[12px] font-semibold dark:border-slate-800 dark:bg-slate-900">
                {(["pages", "tags", "sections"] as const).map((view) => (
                  <button
                    key={view}
                    type="button"
                    onClick={() => setNoteRailView(view)}
                    className={cx(
                      "h-7 rounded-md capitalize transition",
                      noteRailView === view
                        ? "bg-white text-slate-950 shadow-sm dark:bg-slate-100 dark:text-slate-950"
                      : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200",
                    )}
                  >
                    {view === "sections" ? "Categories" : view}
                  </button>
                ))}
              </div>
            </div>
            <div className="shrink-0 border-b border-slate-200/80 px-3 py-2.5 dark:border-slate-800">
              <div className="mb-2 flex items-center justify-between gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
                <span>Recent</span>
                <span className="rounded-full border border-slate-200 bg-white/70 px-1.5 py-0.5 font-mono text-[9px] tabular-nums dark:border-slate-800 dark:bg-slate-950">
                  {recentModifiedNoteItems.length}
                </span>
              </div>
              <div className="max-h-56 space-y-2 overflow-y-auto pr-1">
                <section className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2 text-[11px] font-semibold text-slate-500 dark:text-slate-300">
                    <span>Working Now</span>
                    <span className="font-mono text-[10px] tabular-nums text-slate-400">
                      {noteWorkingCards.length}
                    </span>
                  </div>
                  {noteWorkingCards.map((card) => (
                    <button
                      key={card.id}
                      type="button"
                      onClick={() => selectNote(card.note)}
                      className={cx(
                        "dan-rail-card-row dan-note-tree-row w-full rounded-lg border px-2 py-2 text-left transition",
                        railCardTone(card.note.id === activeNoteId),
                      )}
                      title={card.path}
                    >
                      <div className="flex min-w-0 items-center justify-between gap-2">
                        <span className="inline-flex min-w-0 items-center gap-1.5 text-[11px] font-semibold text-slate-600 dark:text-slate-300">
                          <span
                            className={cx(
                              "h-2 w-2 shrink-0 rounded-full",
                              card.status === "error"
                                ? "bg-red-500"
                                : card.status === "saving" || card.status === "agent"
                                  ? "bg-amber-500"
                                  : card.status === "loading"
                                    ? "bg-sky-500"
                                    : "bg-emerald-500",
                            )}
                          />
                          <span className="truncate">{card.state}</span>
                        </span>
                        <span className="shrink-0 rounded-full border border-slate-200 bg-white/60 px-1.5 py-0.5 text-[10px] text-slate-400 dark:border-slate-800 dark:bg-slate-950">
                          {card.target}
                        </span>
                      </div>
                      <div className="mt-1 truncate text-[12px] font-semibold text-slate-800 dark:text-slate-100">
                        {card.title}
                      </div>
                      <div className="mt-0.5 max-h-8 overflow-hidden text-[11px] leading-4 text-slate-500 [overflow-wrap:anywhere] dark:text-slate-400">
                        {card.snippet}
                      </div>
                      <div className="mt-1 truncate font-mono text-[10px] text-slate-400">
                        Target · {card.path}
                      </div>
                    </button>
                  ))}
                </section>
                <section className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2 text-[11px] font-semibold text-slate-500 dark:text-slate-300">
                    <span>Modified Pages</span>
                    <span className="font-mono text-[10px] tabular-nums text-slate-400">
                      {recentModifiedNoteItems.length}/{RECENT_MODIFIED_LIMIT}
                    </span>
                  </div>
                  {recentModifiedNoteItems.length > 0 ? (
                    <div className="space-y-1">
                      {recentModifiedNoteItems.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => selectNote(item.note)}
                          className={cx(
                            "dan-rail-card-row dan-note-tree-row flex w-full min-w-0 items-start gap-1.5 rounded-lg border px-1.5 py-2 text-left transition",
                            railCardTone(item.note.id === activeNoteId),
                          )}
                          title={item.path}
                        >
                          <span className="dan-rail-card-kind mt-0.5">
                            <Clock3 size={12} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="dan-rail-card-title block truncate">
                              {item.title}
                            </span>
                            <span className="dan-rail-card-meta">
                              {item.updatedLabel} · {item.section}
                            </span>
                            <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-400">
                              {item.path}
                            </span>
                          </span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-lg border border-dashed border-slate-200 px-2 py-2 text-[11px] text-slate-400 dark:border-slate-800">
                      No modified pages.
                    </div>
                  )}
                </section>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              {noteRailView === "pages" && (
                <>
                  {noteFacet !== "all" && (
                    <button
                      type="button"
                      onClick={() => selectNoteFacet("all")}
                      className="mb-2 flex h-8 w-full items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-2 text-left text-[12px] text-slate-600 transition hover:border-slate-300 hover:bg-white dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300"
                    >
                      <span className="min-w-0 truncate">{noteFacetTitle(noteFacet)}</span>
                      <span className="shrink-0 text-slate-400">Clear</span>
                    </button>
                  )}
                  {visibleNotes.length > 0 ? (
                    <NoteTree
                      nodes={noteTree}
                      activeId={activeNoteId}
                      query={noteQuery}
                      expanded={expandedNoteFolders}
                      root={notesRoot}
                      onToggle={(id) =>
                        setExpandedNoteFolders((previous) => ({
                          ...previous,
                          [id]: !previous[id],
                        }))
                      }
                      onSelect={selectNote}
                      onMove={moveNoteEntry}
                    />
                  ) : (
                    <div className="px-2 text-sm text-slate-400">No pages found.</div>
                  )}
                </>
              )}
              {noteRailView === "tags" && (
                noteRailViewForFacet(noteFacet) === "tags" ? (
                  <FacetArticlePanel
                    kind="tag"
                    facet={noteFacet}
                    notes={visibleNotes}
                    activeNoteId={activeNoteId}
                    root={notesRoot}
                    onBack={() => selectNoteFacet("all")}
                    onSelectNote={(note) => selectNote(note, noteFacet)}
                  />
                ) : (
                  <FacetIndex
                    kind="tag"
                    entries={visibleTagFacetOptions}
                    activeFacet={noteFacet}
                    total={catalogNoteItems.length}
                    onSelect={selectNoteFacet}
                  />
                )
              )}
              {noteRailView === "sections" && (
                noteRailViewForFacet(noteFacet) === "sections" ? (
                  <FacetArticlePanel
                    kind={noteFacet.startsWith("category:") ? "category" : "section"}
                    facet={noteFacet}
                    notes={visibleNotes}
                    activeNoteId={activeNoteId}
                    root={notesRoot}
                    onBack={() => selectNoteFacet("all")}
                    onSelectNote={(note) => selectNote(note, noteFacet)}
                  />
                ) : (
                  <FacetIndex
                    kind="section"
                    entries={visibleSectionFacetOptions}
                    activeFacet={noteFacet}
                    total={catalogNoteItems.length}
                    onSelect={selectNoteFacet}
                  />
                )
              )}
            </div>
            {!isPhoneViewport && (
              <div
                role="separator"
                aria-label="Resize content rail"
                onPointerDown={startLeftRailResize}
                className="absolute -right-1 top-0 z-20 h-full w-2 cursor-col-resize bg-transparent transition hover:bg-slate-300/40 dark:hover:bg-slate-700/45"
              />
            )}
          </aside>
          )}

          {!isPhoneViewport && !renderNoteEditor && (
            <CollapsedPaneRail
              label="Source"
              title="Show Markdown source"
              onClick={() => setShowNoteEditor(true)}
            >
              <FileText size={14} />
            </CollapsedPaneRail>
          )}
          {renderNoteEditor && (
          <div className="dan-phone-page relative flex min-h-0 flex-col border-r border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-950">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-4 dark:border-slate-800">
              <div className="min-w-0">
                <div className="truncate text-[14px] font-semibold leading-5 text-slate-900 dark:text-slate-100">
                  {activeNote?.title || "Markdown source"}
                </div>
                <div className="truncate text-[11px] leading-4 text-slate-500">
                  {activeNote ? noteDisplayPath(activeNote, notesRoot) : "local note"}
                </div>
              </div>
              {!isPhoneViewport && (
                <PaneHeaderButton
                  title="Collapse Markdown source"
                  onClick={() => {
                    if (!showNotesPreview) setShowNotesPreview(true);
                    setShowNoteEditor(false);
                  }}
                >
                  <ChevronRight size={13} className="rotate-180" />
                </PaneHeaderButton>
              )}
            </div>
            {activeNote?.status === "error" && (
              <div className="border-b border-rose-200 bg-rose-50 px-5 py-2 text-[12px] leading-5 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">
                {activeNote.error || "Note load failed."}
              </div>
            )}
            <textarea
              ref={noteEditorRef}
              value={activeNote?.content ?? ""}
              onChange={(event) => {
                updateActiveNote(event.target.value);
                updateNoteMentionState(event.currentTarget, event.target.value);
              }}
              onKeyDown={handleNoteEditorKeyDown}
              onKeyUp={(event) => updateNoteMentionState(event.currentTarget, event.currentTarget.value)}
              onClick={(event) => updateNoteMentionState(event.currentTarget, event.currentTarget.value)}
              spellCheck={false}
              placeholder={activeNote?.status === "loading" ? "Loading note..." : ""}
              className="min-h-0 flex-1 resize-none bg-transparent p-5 font-mono text-sm leading-7 text-slate-900 outline-none dark:text-slate-100"
            />
            {noteMention && activeMentionSuggestions.length > 0 && (
              <div className="absolute left-5 right-5 top-14 z-20 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl shadow-slate-950/10 dark:border-slate-800 dark:bg-slate-950">
                <div className="border-b border-slate-200 px-3 py-2 text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400 dark:border-slate-800">
                  Page ID
                </div>
                <div className="max-h-64 overflow-auto p-1.5">
                  {activeMentionSuggestions.map((suggestion, index) => (
                    <button
                      key={`${suggestion.pageID}:${suggestion.path}`}
                      type="button"
                      onMouseDown={(event) => {
                        event.preventDefault();
                        insertPageIdMention(suggestion);
                      }}
                      className={cx(
                        "flex w-full min-w-0 items-start gap-2 rounded-lg px-2.5 py-2 text-left transition",
                        index === noteMentionIndex
                          ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-950"
                          : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-900",
                      )}
                    >
                      <span className="mt-0.5 shrink-0 font-mono text-[12px] opacity-70">
                        @{suggestion.pageID}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12px] font-semibold">
                          {suggestion.title}
                        </span>
                        <span className="block truncate text-[10px] opacity-60">
                          {suggestion.section} · {suggestion.path}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {renderWorkspaceComposer()}
          </div>
          )}

          {!isPhoneViewport && !renderNotesPreview && (
            <CollapsedPaneRail
              label="Preview"
              title="Show note preview"
              onClick={() => setShowNotesPreview(true)}
              edge="left"
            >
              <PanelRight size={14} />
            </CollapsedPaneRail>
          )}
          {renderNotesPreview && (
          <div className="dan-phone-page flex min-h-0 flex-col bg-white dark:bg-slate-950">
            <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-200/80 px-4 dark:border-slate-800">
              <div className="min-w-0">
                <div className="truncate text-[14px] font-semibold leading-5">
                  {noteFacetPage ? "Collection" : "Preview"}
                </div>
                <div className="flex min-w-0 items-center gap-1.5 overflow-hidden text-[11px] leading-4 text-slate-500">
                  {noteFacetPage ? (
                    <>
                      <span className="truncate">{noteFacetTitle(noteFacetPage)}</span>
                      <span className="shrink-0 text-slate-300 dark:text-slate-700">·</span>
                      <span className="shrink-0 font-mono tabular-nums">
                        {visibleNotes.length} {visibleNotes.length === 1 ? "article" : "articles"}
                      </span>
                    </>
                  ) : activeNote ? (
                    <>
                    <button
                      type="button"
                      onClick={() =>
                        selectNoteFacet(noteFacetKey("section", activeNoteSection))
                      }
                      className="shrink-0 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 transition hover:border-slate-300 hover:bg-white hover:text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:hover:bg-slate-800"
                    >
                      {activeNoteSection}
                    </button>
                    {parsedActiveNote.meta.pageID && (
                      <span className="truncate">{parsedActiveNote.meta.pageID}</span>
                    )}
                    {parsedActiveNote.meta.tags.slice(0, 2).map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => selectNoteFacet(noteFacetKey("tag", tag))}
                        className="shrink-0 rounded px-1 py-0.5 text-sky-700 transition hover:bg-sky-50 hover:text-sky-900 dark:text-sky-300 dark:hover:bg-sky-950/40"
                      >
                        #{tag}
                      </button>
                    ))}
                    </>
                  ) : null}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {noteFacetPage ? (
                  <button
                    type="button"
                    onClick={() => setNoteFacetPage(null)}
                    className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 text-[11px] font-semibold text-slate-500 transition hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:border-slate-700 dark:hover:text-slate-200"
                    title="Return to the current article"
                  >
                    <FileText size={12} />
                    <span className="hidden sm:inline">Article</span>
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      disabled={!previousNote}
                      onClick={() =>
                        previousNote && selectNote(previousNote, noteArticleOriginFacet)
                      }
                      className="grid h-7 w-7 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition enabled:hover:border-slate-300 enabled:hover:text-slate-800 disabled:opacity-35 dark:border-slate-800 dark:bg-slate-950"
                      title="Previous page"
                      aria-label="Previous page"
                    >
                      <ChevronRight size={13} className="rotate-180" />
                    </button>
                    <button
                      type="button"
                      disabled={!upperCollectionFacet}
                      onClick={() =>
                        upperCollectionFacet && selectNoteFacet(upperCollectionFacet)
                      }
                      className="grid h-7 w-7 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition enabled:hover:border-slate-300 enabled:hover:text-slate-800 disabled:opacity-35 dark:border-slate-800 dark:bg-slate-950"
                      title={
                        upperCollectionFacet
                          ? `Open ${noteFacetTitle(upperCollectionFacet)} collection`
                          : "No parent collection"
                      }
                      aria-label={
                        upperCollectionFacet
                          ? `Open ${noteFacetTitle(upperCollectionFacet)} collection`
                          : "No parent collection"
                      }
                    >
                      <ChevronRight size={13} className="-rotate-90" />
                    </button>
                    <button
                      type="button"
                      disabled={!nextNote}
                      onClick={() =>
                        nextNote && selectNote(nextNote, noteArticleOriginFacet)
                      }
                      className="grid h-7 w-7 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition enabled:hover:border-slate-300 enabled:hover:text-slate-800 disabled:opacity-35 dark:border-slate-800 dark:bg-slate-950"
                      title="Next page"
                      aria-label="Next page"
                    >
                      <ChevronRight size={13} />
                    </button>
                  </>
                )}
                {!isPhoneViewport && (
                  <PaneHeaderButton
                    title="Collapse note preview"
                    onClick={() => {
                      if (!showNoteEditor) setShowNoteEditor(true);
                      setShowNotesPreview(false);
                    }}
                  >
                    <ChevronRight size={13} />
                  </PaneHeaderButton>
                )}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto px-[18px] py-5">
              {noteFacetPage ? (
                <NoteCollectionPage
                  facet={noteFacetPage}
                  notes={visibleNotes}
                  root={notesRoot}
                  query={noteQuery}
                  onSelectNote={(note) => selectNote(note, noteFacetPage)}
                  onSelectFacet={selectNoteFacet}
                />
              ) : activeNote?.status === "loading" || activeNote?.status === "error" ? (
                <MarkdownRenderer
                  content={
                    activeNote.status === "loading"
                      ? "_Loading note..._"
                      : `> ${activeNote.error || "Note load failed."}`
                  }
                />
              ) : isKnowledgeGraphPage ? (
                <KnowledgeGraphView notes={catalogNoteItems} root={notesRoot} onSelect={selectNote} />
              ) : (
                <article className="w-full min-w-0">
                  {parsedActiveNote.hasFrontmatter && (
                    <header className="mb-6 border-b border-slate-200 pb-5 dark:border-slate-800">
                      <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                        Hugo Page
                      </div>
                      <h1 className="mt-2 text-2xl font-semibold leading-tight text-slate-950 dark:text-slate-100">
                        {parsedActiveNote.meta.title}
                      </h1>
                      {parsedActiveNote.meta.subtitle && (
                        <p className="mt-1 text-sm text-slate-500">
                          {parsedActiveNote.meta.subtitle}
                        </p>
                      )}
                      {parsedActiveNote.meta.abstract && (
                        <p className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700 dark:border-slate-800 dark:bg-slate-900/60 dark:text-slate-300">
                          {parsedActiveNote.meta.abstract}
                        </p>
                      )}
                      {activeNoteMetaItems.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {activeNoteMetaItems.map(([label, value]) => (
                            <span
                              key={`${label}:${value}`}
                              className="inline-flex max-w-full items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-500 dark:border-slate-800 dark:bg-slate-950"
                            >
                              <span className="font-semibold uppercase tracking-[0.08em] text-slate-400">
                                {label}
                              </span>
                              <span className="truncate text-slate-700 dark:text-slate-300">
                                {value}
                              </span>
                            </span>
                          ))}
                        </div>
                      )}
                      {(activeNote || parsedActiveNote.meta.tags.length > 0) && (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {activeNote && (
                            <button
                              type="button"
                              onClick={() =>
                                selectNoteFacet(noteFacetKey("section", activeNoteSection))
                              }
                              className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-700 transition hover:border-slate-300 hover:bg-white dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
                            >
                              {activeNoteSection}
                            </button>
                          )}
                          {parsedActiveNote.meta.tags.map((tag) => (
                            <button
                              key={`tag:${tag}`}
                              type="button"
                              onClick={() => selectNoteFacet(noteFacetKey("tag", tag))}
                              className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-800 transition hover:border-sky-300 hover:bg-white dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-200"
                            >
                              #{tag}
                            </button>
                          ))}
                        </div>
                      )}
                    </header>
                  )}
                  <HugoNotePreviewBody
                    body={parsedActiveNote.body || "_No note content yet._"}
                    notesRoot={notesRoot}
                    renderMathCodeSpans={activeNoteMathEnabled}
                    onClick={handleNotePreviewClick}
                  />
                </article>
              )}
            </div>
          </div>
          )}

          {!isPhoneViewport && !renderLearnPanel && (
            <CollapsedPaneRail
              label="Learn"
              title="Show Learn panel"
              onClick={() => setShowLearnPanel(true)}
              edge="left"
            >
              <Lightbulb size={14} />
            </CollapsedPaneRail>
          )}
          {renderLearnPanel && (
            <NoteLearningPanel
              note={activeNote}
              root={notesRoot}
              course={learnCourse}
              activeSession={activeLearnSession}
              completedSessionIds={completedLearnSessionIds}
              status={learnStatus}
              error={learnError}
              onGenerate={generateLearnCourse}
              onRegenerate={generateLearnCourse}
              onSelectSession={selectLearnSession}
              onToggleSession={toggleLearnSession}
              onOpenManual={() => {
                setShowNotesPreview(true);
                setPhonePage("note-preview");
              }}
              onCollapse={() => {
                setShowLearnPanel(false);
                if (isPhoneViewport) setPhonePage("note-preview");
              }}
            />
          )}
        </section>
      ) : (
        <section className="dan-work-pages flex min-h-0 flex-1 bg-[#f4f7fb] dark:bg-slate-950">
          {!isPhoneViewport && !renderSessionRail && (
            <CollapsedPaneRail
              label="Sessions"
              title="Show sessions pane"
              onClick={() => setShowSessionRail(true)}
              className="md:order-1"
            >
              <PanelLeft size={14} />
            </CollapsedPaneRail>
          )}
          {renderSessionRail && <aside id="wb-project-sidebar" className="dan-phone-page dan-session-page wb-session-shelf wb-project-sidebar">
            <div className="wb-panel-heading"><span>DAN</span></div>
            <button className="wb-sidebar-new" onClick={() => { void startNewSession(); setShowConversationChunks(true); setPhonePage("chat"); }}><Plus size={16} />New chat</button>
            <label className="wb-shelf-search"><Search size={15} /><input aria-label="Search sessions" placeholder="Search chats" value={threadQuery} onChange={(event) => setThreadQuery(event.target.value)} /></label>
            <div className="wb-projects-heading"><span>{sessionShelfScope === "archived" ? "Archived chats" : "Projects"}</span><button title="New project" aria-label="New project" onClick={() => { setCreatingProject(true); setWorkbenchSettings(true); }}><Plus size={15} /></button></div>
            <div className="wb-shelf-list">
              {sessionGroups.flatMap((group) => group.subgroups ?? [group]).filter((group) => sessionShelfScope === "archived" ? group.archived : !group.archived).map((group) => {
                if (group.unassigned) return <section key={group.id} aria-label="Other chats">
                  <div className="wb-projects-heading"><span>Other chats</span></div>
                  {renderSessionThreadRows({ ...group, threads: [...group.threads].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || a.id.localeCompare(b.id)) })}
                </section>;
                const collapsed = !threadQuery.trim() && (collapsedThreadGroups[group.id] ?? (group.workspaceId !== activeWorkspaceId));
                return <div key={group.id} className="wb-sidebar-project">
                  <div className="wb-project-row" data-active={group.workspaceId === activeWorkspaceId || undefined}>
                    <button aria-expanded={!collapsed} onClick={() => setCollapsedThreadGroups((previous) => ({ ...previous, [group.id]: !collapsed }))}><ChevronRight size={13} className={collapsed ? "" : "rotate-90"} /><Folder size={15} /><span>{workspaces.find((item) => item.id === group.workspaceId)?.name || group.name}</span></button>
                    {!group.archived && <button className="wb-project-new-chat" aria-label={`New chat in ${group.name}`} title="New chat in project" onClick={() => { setCollapsedThreadGroups((previous) => ({ ...previous, [group.id]: false })); void startNewSession(registerSessionGroup(group)); setShowConversationChunks(true); setPhonePage("chat"); }}><Plus size={14} /></button>}
                    {!group.archived && <ProjectMenu name={workspaces.find((item) => item.id === group.workspaceId)?.name || group.name}
                      onNewChat={() => { void startNewSession(registerSessionGroup(group)); setShowConversationChunks(true); setPhonePage("chat"); }}
                      onEdit={() => {
                        const id = registerSessionGroup(group);
                        setActiveWorkspace(id);
                        if (group.threads[0]) void openSession(group.threads[0], id, group.root);
                        setCreatingProject(false); setWorkbenchSettings(true);
                      }}
                      onImport={() => {
                        const id = registerSessionGroup(group);
                        const project = useWorkspaceStore.getState().workspaces.find((item) => item.id === id);
                        const root = project?.pinnedPaths[0] || group.root || "";
                        setImportNativeSessions({ id, root });
                      }}
                      onRemove={() => {
                        const id = registerSessionGroup(group);
                        for (const thread of group.threads) clearStoredThreadSelection(thread);
                        if (id === activeWorkspaceId || group.threads.some((thread) => thread.id === activeThread?.id)) {
                          sessionSelectionSeqRef.current += 1;
                          clearActiveSessionView("Project removed from DAN");
                        }
                        hideWorkspace(id);
                      }} />}
                  </div>
                  {!collapsed && <div className="wb-project-chats">{renderSessionThreadRows({ ...group, threads: [...group.threads].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || a.id.localeCompare(b.id)) })}
                    {!group.threads.length && <p className="wb-shelf-empty">{threadQuery.trim() ? "No matching chats" : "No chats yet"}</p>}
                  </div>}
                </div>;
              })}
              {sessionGroups.every((group) => sessionShelfScope === "archived" ? !group.archived : group.archived) && <p className="wb-shelf-empty">{threadQuery.trim() ? "No matching chats" : sessionShelfScope === "archived" ? "No archived chats" : "Create a project to get started"}</p>}
            </div>
            <div className="wb-sidebar-footer">{sessionShelfScope === "archived" && <><button className="wb-delete-archived" disabled={deletingArchived || !threads.some((thread) => thread.archived)} onClick={() => void deleteAllArchivedSessions()}><Trash2 size={15} />{deletingArchived ? "Deleting archived chats…" : "Delete all archived chats"}</button>{archiveDeleteProgress && <p role="status" className="wb-archive-delete-progress">{archiveDeleteProgress}</p>}</>}<button onClick={() => { setSessionShelfScope(sessionShelfScope === "archived" ? "all" : "archived"); }}><Archive size={15} />{sessionShelfScope === "archived" ? "Back to projects" : "Archived chats"}</button><button onClick={() => setDanSettings(true)}><MoreHorizontal size={15} />DAN settings</button></div>
          </aside>}
          {!isPhoneViewport && !renderFileExplorer && (
            <CollapsedPaneRail
              label="Files"
              title="Show files pane"
              onClick={() => setShowFileExplorer(true)}
              className="md:order-2"
            >
              <Folder size={14} />
            </CollapsedPaneRail>
          )}
          {renderFileExplorer && (
            <aside className="dan-phone-page dan-files-page flex min-h-0 w-[270px] shrink-0 flex-col border-r border-slate-200/80 bg-white/85 dark:border-slate-800 dark:bg-slate-950 md:order-2">
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-3 dark:border-slate-800">
                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                  Files
                </div>
                <div className="flex items-center gap-1">
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setFileCreateMenuOpen((open) => !open)}
                      onBlur={() => window.setTimeout(() => setFileCreateMenuOpen(false), 120)}
                      className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                      title="Create workspace folder"
                      aria-label="Create workspace folder"
                    >
                      <Plus size={14} />
                    </button>
                    {fileCreateMenuOpen && (
                      <div className="absolute right-0 top-8 z-40 w-44 overflow-hidden rounded-xl border border-slate-200 bg-white p-1.5 text-xs shadow-xl shadow-slate-950/10 dark:border-slate-800 dark:bg-slate-950">
                        <button
                          type="button"
                          onMouseDown={(event) => {
                            event.preventDefault();
                            setFileCreateMenuOpen(false);
                            void createDevelopmentFolder();
                          }}
                          className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-slate-600 transition hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900"
                        >
                          <FolderPlus size={13} />
                          <span>New folder</span>
                        </button>
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={openFolder}
                    className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                    title="Open development workspace"
                  >
                    <FolderOpen size={14} />
                  </button>
                  {!isPhoneViewport && (
                    <PaneHeaderButton
                      title="Collapse files pane"
                      onClick={() => setShowFileExplorer(false)}
                    >
                      <ChevronRight size={13} className="rotate-180" />
                    </PaneHeaderButton>
                  )}
                </div>
              </div>
              <div className="border-b border-slate-200/80 p-3 dark:border-slate-800">
                {rootEditing ? (
                  <div className="space-y-2">
                    <input
                      autoFocus
                      list="dan-workspace-root-options"
                      value={rootInput}
                      onChange={(event) => setRootInput(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") applyDevelopmentRoot(rootInput);
                        if (event.key === "Escape") {
                          setRootEditing(false);
                          setRootInput(developmentRoot);
                        }
                      }}
                      placeholder="/path/to/workspace"
                      className="h-9 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 font-mono text-xs outline-none transition focus:border-slate-400 focus:bg-white dark:border-slate-800 dark:bg-slate-900"
                    />
                    <datalist id="dan-workspace-root-options">
                      {rootOptions.map((option) => (
                        <option key={option.path} value={option.path}>
                          {option.label}
                        </option>
                      ))}
                    </datalist>
                    {rootOptions.length > 0 && (
                      <label
                        title="Choose root"
                        className="flex h-8 w-full items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-1 text-slate-500 dark:border-slate-800 dark:bg-slate-900"
                      >
                        {loadingRoots ? (
                          <Loader2 size={13} className="shrink-0 animate-spin" />
                        ) : (
                          <ChevronDown size={13} className="shrink-0" />
                        )}
                        <select
                          value=""
                          onChange={(event) => {
                            if (event.target.value) applyDevelopmentRoot(event.target.value);
                          }}
                          className="min-w-0 flex-1 bg-transparent text-xs outline-none"
                        >
                          <option value="">Choose root</option>
                          {rootOptions.map((option) => (
                            <option key={option.path} value={option.path}>
                              {option.name} - {option.path}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    <div className="flex justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          setRootEditing(false);
                          setRootInput(developmentRoot);
                        }}
                        className="h-7 rounded-md border border-slate-200 px-2 text-xs text-slate-500 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={() => applyDevelopmentRoot(rootInput)}
                        disabled={!rootInput.trim()}
                        className="h-7 rounded-md bg-slate-950 px-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-slate-100 dark:text-slate-950"
                      >
                        Set
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setRootEditing(true);
                      setRootInput(developmentRoot);
                    }}
                    className="flex h-9 w-full items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/90 px-2.5 text-left text-xs shadow-inner transition hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900"
                    title="Change root path"
                  >
                    <Folder size={13} className="shrink-0 text-slate-400" />
                    <span className="shrink-0 font-medium text-slate-500">Root</span>
                    <span className="min-w-0 flex-1 truncate font-mono text-slate-500">
                      {developmentRoot || "Set root path"}
                    </span>
                  </button>
                )}
              </div>
              <div className="border-b border-slate-200/80 p-3 dark:border-slate-800">
                <div className="flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/90 px-2.5 shadow-inner dark:border-slate-800 dark:bg-slate-900">
                  <Search size={13} className="text-slate-400" />
                  <input
                    value={devFileQuery}
                    onChange={(event) => setDevFileQuery(event.target.value)}
                    placeholder="Find files"
                    className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-slate-400"
                  />
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-auto px-3 py-3">
                <div className="mb-2 flex items-center justify-between px-1 text-[11px] text-slate-400">
                  <span>{fileName(developmentRoot) || "workspace"}</span>
                  <span>{devFileCount}</span>
                </div>
                {fileTree.length > 0 ? (
                  <WorkspaceFileTree
                    nodes={fileTree}
                    activePath={activeFilePath}
                    query={devFileQuery}
                    expanded={expandedFileDirs}
                    onToggle={toggleFileDir}
                    onSelect={(entry) => {
                      selectPreviewFile(entry);
                    }}
                    onMove={moveDevelopmentEntry}
                  />
                ) : (
                  <div className="px-2 text-sm text-slate-400">No files loaded.</div>
                )}
              </div>
            </aside>
          )}

          {!isPhoneViewport && !renderWorkMain && (
            <CollapsedPaneRail
              label="Work"
              title="Show work panel"
              onClick={() => setShowConversationChunks(true)}
              className="md:order-3"
            >
              <Cable size={14} />
            </CollapsedPaneRail>
          )}
          {renderWorkMain && (
          <div
            className={cx(
              "dan-phone-page grid min-h-0 min-w-0 flex-1 md:order-3",
              renderSidecarPreview
                ? "grid-cols-[minmax(280px,0.95fr)_minmax(300px,1.05fr)]"
                : "grid-cols-[minmax(0,1fr)]",
            )}
          >
            <div className="flex min-h-0 min-w-0 flex-col bg-white/90 dark:bg-slate-950">
              <div className="wb-conversation-heading"><div className="wb-conversation-actions">
                {activeRunningTask && activeThread && <button className="wb-stop" onClick={() => void stopSessionRun({ id: activeThread.id, workflow_id: activeThread.workflowId, title: activeThread.title || "Active session", message_count: messages.length, created_at: "", updated_at: "" }, activeRunningTask)}><Square size={11} />Stop run</button>}
                <button aria-pressed={workbenchOutline} onClick={() => setWorkbenchOutline(!workbenchOutline)}>{workbenchOutline ? "Conversation" : "Task detail"}</button></div></div>

              <TeamStrip workers={team.workers} lead={selectedAgentOption.shortLabel} leadRunning={Boolean(activeRunningTask)} open={teamPanel} onToggle={() => { setTeamPanel(!teamPanel); setWorkbenchActivity(false); setSidecarChat(false); setShowSidecarPreview(false); }} />
              {workbenchOutline ? (              <div className="wb-outline min-h-0 flex-1 overflow-auto p-4">
                {isPhoneViewport || showConversationChunks ? (
                  <BlueprintView
                    nodes={blueprintNodes}
                    conversationChunks={userConversationChunks}
                    tasks={workPanelTasks}
                    agentEvents={agentEvents}
                    activeTask={activeRunningTask}
                    activeNodeId={activeBlueprintNode?.id ?? null}
                    selectedNodeId={selectedBlueprintNode?.id ?? null}
                    selectedChunkId={selectedChunk?.id ?? null}
                    elapsedCounter={elapsedCounter}
                    loading={loadingThreadId === activeThread?.id}
                    onSelect={(node) => {
                      setSelectedBlueprintNodeId(node.id);
                      setPromptLogPreview(null);
                      if (node.sourceChunkId) setSelectedChunkId(node.sourceChunkId);
                      else setSelectedChunkId(null);
                    }}
                    onSelectConversationChunk={(chunk) => {
                      setSelectedBlueprintNodeId(null);
                      setSelectedChunkId(chunk.id);
                      setPromptLogPreview(null);
                    }}
                  />
                ) : (
                  <div className="rounded-md border border-dashed border-slate-200 bg-white p-3 text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-950">
                    Work panel hidden.
                  </div>
                )}
                <WorkspacePreviewArtifactsCard
                  artifacts={previewArtifacts}
                  selectedPath={activeFilePath}
                  onSelect={selectPreviewFile}
                />
              </div>
) : (
                <WorkbenchConversation key={activeThread?.id ?? "new"} messages={messages} pending={pendingAssistantIds} loading={loadingThreadId !== null} status={status} liveActions={liveActions} onRegenerate={activeThread && !activeRunningTask && !sending ? () => void regenerateLastRequest() : undefined} onFork={activeThread && !activeRunningTask && !sending ? () => void forkConversation() : undefined} onSidecar={activeThread ? (text) => { setSidecarSelection({text, token:Date.now()}); setSidecarChat(true); setWorkbenchActivity(false); setTeamPanel(false); setShowSidecarPreview(false); } : undefined} onQuote={(text, messageIds) => {
                  if (!activeThread) return;
                  setComposerReferences((current) => ({ ...current, [referenceKey]: { text, messageIds, title: activeThread.title || "Conversation", threadId: activeThread.id, workflowId: activeThread.workflowId } }));
                  composerRef.current?.focus();
                }} />
              )}

              {showAgentQueuePanel && <details className="wb-followup-queue">
                <summary><Clock3 size={13} /><span>{visibleQueueRows.length} queued {visibleQueueRows.length === 1 ? "message" : "messages"}</span><ChevronDown size={13} /></summary>
                <ol>{visibleQueueRows.map((row) => <li key={row.id}><span>{row.rawDetail || row.detail}</span><small>{row.status.replaceAll("_", " ")}</small></li>)}</ol>
              </details>}

              {renderWorkspaceComposer()}
            </div>

            {renderSidecarPreview && (
              <aside className="flex min-h-0 min-w-0 flex-col border-l border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-950">
                <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-4 dark:border-slate-800">
                  <div className="min-w-0">
                    <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                      Preview
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={openActiveFile}
                      disabled={!canOpenPreview}
                      title="Open current preview"
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300 dark:hover:bg-slate-900"
                    >
                      Open
                    </button>
                    {!isPhoneViewport && (
                      <PaneHeaderButton
                        title="Collapse preview pane"
                        onClick={() => setShowSidecarPreview(false)}
                      >
                        <ChevronRight size={13} />
                      </PaneHeaderButton>
                    )}
                  </div>
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-4">
                  {promptLogPreview ? (
                    <div className="space-y-3">
                      <div className="rounded-md border border-slate-200 bg-slate-50/80 p-3 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-900/50 dark:text-slate-400">
                        <div className="font-semibold text-slate-700 dark:text-slate-200">
                          {promptLogPreview.title}
                        </div>
                        <div className="mt-1">
                          {promptLogPreview.status === "loading"
                            ? "Loading prompt log"
                            : `${promptLogPreview.entryCount} model ${promptLogPreview.entryCount === 1 ? "call" : "calls"} recorded`}
                        </div>
                        {promptLogPreview.path && (
                          <div className="mt-1 break-all font-mono">{promptLogPreview.path}</div>
                        )}
                      </div>
                      {promptLogPreview.status === "loading" ? (
                        <div className="flex items-center gap-2 text-sm text-slate-400">
                          <Loader2 size={14} className="animate-spin" />
                          Loading prompt log
                        </div>
                      ) : (
                        <MarkdownRenderer content={promptLogPreview.body || "_No prompt log content._"} />
                      )}
                    </div>
                  ) : selectedBlueprintNode ? (
                    <BlueprintNodePreview
                      node={selectedBlueprintNode}
                      tasks={workPanelTasks}
                      events={agentEvents}
                      activeTask={activeRunningTask}
                    />
                  ) : selectedChunk ? (
                    <MarkdownRenderer
                      content={previewMarkdownContent(selectedChunk.body)}
                      autoHighlightCode={false}
                    />
                  ) : activePreviewFileEntry ? (
                    <WorkspaceFilePreviewPanel
                      entry={activePreviewFileEntry}
                      root={developmentRoot}
                      content={activeFileContent}
                      status={activeFileStatus}
                    />
                  ) : (
                    <div className="text-sm text-slate-400">Select a step or development file.</div>
                  )}
                </div>
                {activePreviewFileEntry && !selectedBlueprintNode && !selectedChunk && (
                  <div className="border-t border-slate-200 px-3 py-2 text-[11px] text-slate-400 dark:border-slate-800">
                    {activePreviewFileEntry.size
                      ? compactFileSize(activePreviewFileEntry.size)
                      : workspacePreviewKindLabel(workspaceFilePreviewKind(activePreviewFileEntry) ?? "text")}
                    {workspaceFilePreviewNeedsText(activePreviewFileEntry) &&
                    activeFileStatus === "error"
                      ? " · preview unavailable"
                      : ""}
                  </div>
                )}
              </aside>
            )}
          </div>
          )}
          {!renderWorkMain && renderSidecarPreview && (
            <aside className="dan-phone-page flex min-h-0 min-w-0 flex-1 flex-col border-l border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-950 md:order-4">
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-4 dark:border-slate-800">
                <div className="min-w-0">
                  <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                    Preview
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={openActiveFile}
                      disabled={!canOpenPreview}
                      title="Open current preview"
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300 dark:hover:bg-slate-900"
                    >
                      Open
                  </button>
                  {!isPhoneViewport && (
                    <PaneHeaderButton
                      title="Collapse preview pane"
                      onClick={() => setShowSidecarPreview(false)}
                    >
                      <ChevronRight size={13} />
                    </PaneHeaderButton>
                  )}
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-4">
                {promptLogPreview ? (
                  <div className="space-y-3">
                    <div className="rounded-md border border-slate-200 bg-slate-50/80 p-3 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-900/50 dark:text-slate-400">
                      <div className="font-semibold text-slate-700 dark:text-slate-200">
                        {promptLogPreview.title}
                      </div>
                      <div className="mt-1">
                        {promptLogPreview.status === "loading"
                          ? "Loading prompt log"
                          : `${promptLogPreview.entryCount} model ${promptLogPreview.entryCount === 1 ? "call" : "calls"} recorded`}
                      </div>
                      {promptLogPreview.path && (
                        <div className="mt-1 break-all font-mono">{promptLogPreview.path}</div>
                      )}
                    </div>
                    {promptLogPreview.status === "loading" ? (
                      <div className="flex items-center gap-2 text-sm text-slate-400">
                        <Loader2 size={14} className="animate-spin" />
                        Loading prompt log
                      </div>
                    ) : (
                      <MarkdownRenderer content={promptLogPreview.body || "_No prompt log content._"} />
                    )}
                  </div>
                ) : selectedBlueprintNode ? (
                  <BlueprintNodePreview
                    node={selectedBlueprintNode}
                    tasks={workPanelTasks}
                    events={agentEvents}
                    activeTask={activeRunningTask}
                  />
                ) : selectedChunk ? (
                  <MarkdownRenderer
                    content={previewMarkdownContent(selectedChunk.body)}
                    autoHighlightCode={false}
                  />
                ) : activePreviewFileEntry ? (
                  <WorkspaceFilePreviewPanel
                    entry={activePreviewFileEntry}
                    root={developmentRoot}
                    content={activeFileContent}
                    status={activeFileStatus}
                  />
                ) : (
                  <div className="text-sm text-slate-400">Select a step or development file.</div>
                )}
              </div>
            </aside>
          )}
          {!isPhoneViewport && !renderSidecarPreview && (
            <CollapsedPaneRail
              label="Preview"
              title="Show preview pane"
              onClick={() => setShowSidecarPreview(true)}
              edge="left"
              className="md:order-4"
            >
              <PanelRight size={14} />
            </CollapsedPaneRail>
          )}
          {sidecarChat && activeThread && <SidecarChat key={`${activeThread.workflowId}:${activeThread.id}`} parentId={activeThread.id} workflowId={activeThread.workflowId}
            workspaceId={workspace?.id || ""} workspaceRoot={workspaceRootForTasks(tasks) || workspaceRoot || developmentRoot} context={messages} selection={sidecarSelection}
            leadLabel={selectedAgentOption.shortLabel} execution={{ ...buildWorkspaceAgentExecutePayload(selectedAgentOption, selectedModelOption, autonomyMode), profile_policy: { ...buildWorkspaceAgentExecutePayload(selectedAgentOption, selectedModelOption, autonomyMode).profile_policy, native_workers:nativeWorkerProfiles, ...(selectedAgentId !== "native" ? {lead_profile:leadProfiles[selectedAgentId] || {}} : {}) } }}
            onClose={() => setSidecarChat(false)} onCreated={() => { void refreshThreads(); }} />}
          {workbenchActivity && <aside className="wb-activity-panel"><div className="wb-panel-heading"><span>Activity</span><button onClick={() => setWorkbenchActivity(false)} aria-label="Close activity"><X size={17} /></button></div><WorkbenchActivity events={agentEvents} /></aside>}
          {teamPanel && <TeamPanel workers={team.workers} error={team.error} onStop={(worker) => void team.stop(worker)} onClose={() => setTeamPanel(false)} />}

        </section>
      )}
      <nav
        className={cx(
          "dan-phone-nav hidden shrink-0 border-t border-slate-200/80 bg-white/95 px-2 py-1.5 shadow-[0_-8px_24px_rgba(15,23,42,0.06)] backdrop-blur dark:border-slate-800 dark:bg-slate-950/95",
          activePane === "notes" && workspaceSurfaceTheme === "factory-worn" && "dan-phone-nav-factory-worn",
        )}
      >
        {activePane === "work"
          ? ([
              ["chat", MessageSquareText, "Chat"],
              ["sessions", Bot, "Sessions"],
              ["files", Folder, "Files"],
              ["preview", PanelRight, "Preview"],
            ] as const).map(([page, Icon, label]) => (
              <button
                key={page}
                type="button"
                onClick={() => setPhonePage(page)}
                className={cx(
                  "dan-phone-nav-button",
                  phonePage === page && "dan-phone-nav-button-active",
                )}
              >
                <Icon size={17} />
                <span>{label}</span>
              </button>
            ))
          : ([
              ["note-list", NotebookPen, "Pages"],
              ["note-edit", FileText, "Edit"],
              ["note-preview", PanelRight, "Read"],
              ["note-learn", Lightbulb, "Learn"],
              ["chat", TerminalSquare, "Work"],
            ] as const).map(([page, Icon, label]) => (
              <button
                key={page}
                type="button"
                onClick={() => {
                  if (page === "chat") {
                    setActivePane("work");
                    setPhonePage("chat");
                  } else {
                    setPhonePage(page);
                  }
                }}
                className={cx(
                  "dan-phone-nav-button",
                  phonePage === page && "dan-phone-nav-button-active",
                  phonePage === page &&
                    workspaceSurfaceTheme === "factory-worn" &&
                    "dan-phone-nav-button-active-factory-worn",
                )}
              >
                <Icon size={17} />
                <span>{label}</span>
              </button>
            ))}
        <button
          type="button"
          onClick={() => void refreshWireGuardStatus()}
          className="dan-phone-nav-button"
          title="Refresh read-only WireGuard status"
        >
          {wireGuardLoading ? <Loader2 size={17} className="animate-spin" /> : <Cable size={17} />}
          <span>{wireGuardUi.detail}</span>
        </button>
      </nav>
    </div>
  );
}
