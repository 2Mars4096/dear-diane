import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
} from "react";
import {
  Activity,
  Archive,
  Bot,
  Cable,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  File,
  FileText,
  Folder,
  FolderPlus,
  FolderOpen,
  Loader2,
  MessageSquareText,
  NotebookPen,
  PanelLeft,
  PanelRight,
  Plus,
  Search,
  Send,
  Shield,
  Square,
  TerminalSquare,
  WandSparkles,
  X,
} from "lucide-react";
import {
  listWorkspaceFileTree,
  listWorkspaceNotes,
  listWorkspaceRootSuggestions,
  getWorkspaceWireGuardStatus,
  readWorkspaceFile,
  readWorkspaceNote,
  writeWorkspaceNote,
  type WorkspaceFileEntry,
  type WorkspaceNoteSummary,
  type WorkspaceRootSuggestion,
  type WorkspaceWireGuardStatus,
} from "../../lib/api";
import {
  archiveChatV2Thread,
  connectChatV2AgentRunEvents,
  createChatV2AgentRun,
  createChatV2Thread,
  executeChatV2AgentRun,
  getChatV2AgentRun,
  getChatV2AgentRunEvents,
  getChatV2Thread,
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
  isElectron,
  nativeDialog,
  nativeFs,
  nativeShell,
  nativeWatch,
} from "../../lib/electronBridge";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import type { ChatMessage, RunEventPayload } from "../../types/chat";
import MarkdownRenderer from "../shared/MarkdownRenderer";

const DEFAULT_WORKFLOW_ID = "_scratch";
const SUPER_DAN_BACKEND = "super_dan";
const SUPER_TUI_PROFILE = "super_tui";
const NOTES_STORAGE_KEY = "dan.chunkWorkspace.notes.v1";
const NOTE_CONTENT_CACHE_STORAGE_KEY = "dan.chunkWorkspace.noteContentCache.v1";
const LAST_THREAD_STORAGE_KEY = "dan.chunkWorkspace.lastThread.v1";
const ROOT_SUGGESTION_STORAGE_KEY = "dan.chunkWorkspace.roots.v1";
const THREAD_WORKSPACE_STORAGE_KEY = "dan.chunkWorkspace.threadWorkspaces.v1";
const LAYOUT_STORAGE_KEY = "dan.chunkWorkspace.layout.v1";
const UI_STATE_STORAGE_KEY = "dan.chunkWorkspace.uiState.v1";
const WORKSPACE_SURFACE_TYPE = "frontend";
const WORKSPACE_SURFACE_ID = "chunk-workspace";
const WORKSPACE_SURFACE = `${WORKSPACE_SURFACE_TYPE}:${WORKSPACE_SURFACE_ID}`;
const LEFT_RAIL_DEFAULT_WIDTH = 292;
const LEFT_RAIL_MIN_WIDTH = 220;
const LEFT_RAIL_MAX_WIDTH = 420;

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
type WorkspacePane = "work" | "notes";
type PhonePage = "chat" | "sessions" | "files" | "preview" | "note-list" | "note-edit" | "note-preview";
type NoteRailView = "pages" | "tags" | "sections";
type ActiveRunPlacement = "steer" | "queue";
type ComposerSubmitMode = ActiveRunPlacement;

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

interface SessionGroup {
  id: string;
  name: string;
  workspaceId: string | null;
  root: string;
  shortcut: string;
  threads: ChatV2ThreadSummary[];
  defaultCollapsed?: boolean;
}

interface QueueRow {
  id: string;
  label: string;
  detail: string;
  status: string;
  active: boolean;
}

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
  leftRailWidth: number;
}

interface WorkspaceUiState {
  activePane: WorkspacePane;
  selectedChunkId: string | null;
  activeFilePath: string | null;
  threadQuery: string;
  noteQuery: string;
  noteFacet: string;
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

const terminalTaskStatuses = new Set(["completed", "failed", "blocked", "stopped"]);
const terminalQueueStatuses = new Set(["completed", "done", "failed", "cancelled", "canceled", "stopped"]);

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function clampLeftRailWidth(value: number) {
  if (!Number.isFinite(value)) return LEFT_RAIL_DEFAULT_WIDTH;
  return Math.min(LEFT_RAIL_MAX_WIDTH, Math.max(LEFT_RAIL_MIN_WIDTH, Math.round(value)));
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
  if (status.config_present) return { label, detail: "ready", tone: "warn" };
  return { label, detail: "setup", tone: "unknown" };
}

function nowId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function makeMessage(role: ChatMessage["role"], content: string): ChatMessage {
  return {
    id: nowId(role),
    role,
    content,
    timestamp: Date.now(),
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

function relativeFileLabel(path: string, root: string) {
  if (!root || !path.startsWith(root)) return path;
  return path.slice(root.length).replace(/^[\\/]+/, "") || fileName(path);
}

function noteRelativePath(note: WorkspaceNote, root: string) {
  if (note.relativePath) return note.relativePath;
  if (note.path) return relativeFileLabel(note.path, root);
  return note.title;
}

function noteSection(note: WorkspaceNote, root: string) {
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

function workspaceDisplayName(workspace: { name?: string; pinnedPaths?: string[] } | null | undefined) {
  const rootName = fileName(workspace?.pinnedPaths?.[0] ?? "");
  return rootName || workspace?.name || "Workspace";
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
      draft: metaString(data, "draft"),
      tags: metaList(data, "tags"),
      categories: metaList(data, "categories"),
    },
  };
}

function formatHugoPreviewBody(body: string) {
  return body
    .replace(/{{<\s*summary\s+"([^"]+)"\s*>}}/g, (_, pageId) =>
      `> Summary transclusion: @${pageId}`,
    )
    .replace(/{{<\s*([^>\s]+)([\s\S]*?)>}}/g, (_, shortcode, args) =>
      `\`${shortcode}${String(args || "").trim() ? ` ${String(args).trim()}` : ""}\``,
    )
    .replace(/(^|[\s(])@([A-Za-z0-9][A-Za-z0-9_-]+)/g, "$1[@$2](#$2)");
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
    ["Updated", formatHugoDate(meta.lastmod)],
    ["Author", meta.author],
    ["PageID", meta.pageID],
    ["Link", meta.link],
    ["Figure", meta.figure],
    ["Draft", meta.draft === "true" ? "draft" : ""],
    ["Path", note ? noteRelativePath(note, "") : ""],
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

function parentNoteFor(note: WorkspaceNote | null, candidates: WorkspaceNote[], root: string) {
  if (!note) return null;
  const parent = noteParentPathLabel(note, root);
  if (!parent) return null;
  const parentIndexPath = `${parent}/index.md`.toLowerCase();
  return (
    candidates.find((candidate) =>
      noteRelativePath(candidate, root).toLowerCase() === parentIndexPath,
    ) ?? null
  );
}

function normalizeRootPath(path: string) {
  const trimmed = path.trim();
  if (trimmed === "/" || /^[a-z]:[\\/]$/i.test(trimmed)) return trimmed;
  return trimmed.replace(/[\\/]+$/, "");
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

function persistThreadWorkspaces(bindings: Record<string, string>) {
  try {
    window.localStorage.setItem(THREAD_WORKSPACE_STORAGE_KEY, JSON.stringify(bindings));
  } catch {
    // Workspace/session grouping stays local and best effort.
  }
}

function readStoredLayout(): LayoutPreferences {
  const defaults: LayoutPreferences = {
    showSessionRail: true,
    showFileExplorer: true,
    showConversationChunks: true,
    showSidecarPreview: true,
    showNotesRail: true,
    showNoteEditor: true,
    showNotesPreview: true,
    leftRailWidth: LEFT_RAIL_DEFAULT_WIDTH,
  };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LAYOUT_STORAGE_KEY) || "{}") as
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
      leftRailWidth:
        typeof parsed.leftRailWidth === "number"
          ? clampLeftRailWidth(parsed.leftRailWidth)
          : defaults.leftRailWidth,
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
    return {
      activePane: parsed.activePane === "notes" ? "notes" : defaults.activePane,
      selectedChunkId:
        typeof parsed.selectedChunkId === "string" ? parsed.selectedChunkId : null,
      activeFilePath:
        typeof parsed.activeFilePath === "string" ? parsed.activeFilePath : null,
      threadQuery: typeof parsed.threadQuery === "string" ? parsed.threadQuery : "",
      noteQuery: typeof parsed.noteQuery === "string" ? parsed.noteQuery : "",
      noteFacet: typeof parsed.noteFacet === "string" ? parsed.noteFacet : "all",
      noteRailView:
        storedNoteRailView === "tags" || storedNoteRailView === "sections"
          ? storedNoteRailView
          : storedNoteRailView === "categories"
            ? "sections"
            : "pages",
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
  if (normalized === DEFAULT_WORKFLOW_ID) return "Scratch";
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

function compactFileSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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

function taskRunId(task: ChatV2TaskSnapshot) {
  const runId = task.metadata?.active_run_id;
  return typeof runId === "string" ? runId : "";
}

function isTaskTerminal(task?: ChatV2TaskSnapshot | null) {
  return Boolean(task && terminalTaskStatuses.has(task.status));
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
  const trimmed = text.trim();
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

function addSection(lines: string[], label: string, items: string[]) {
  if (items.length === 0) return;
  if (lines.length > 0) lines.push("");
  lines.push(`### ${label}`, "");
  for (const item of items) lines.push(`- ${item}`);
}

function normalizeStructuredMarkdown(content: string) {
  if (!/\*\*(Files|Risks|Checks):\*\*/.test(content)) return content;
  return content
    .replace(/\s*\*\*(Files|Risks|Checks):\*\*\s*/g, "\n\n### $1\n\n")
    .replace(/\s+-\s+/g, "\n- ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function pathBullets(paths: string[], verb: string) {
  return paths.map((path) => `${verb}: \`${path}\``);
}

function formatStructuredAgentSummary(content: string) {
  const data = parseJsonObject(content);
  if (!data) return "";

  const lines: string[] = [];
  const primary = [
    ...stringList(data.answer),
    ...stringList(data.final_answer),
    ...stringList(data.summary),
    ...stringList(data.change_summary),
    ...stringList(data.message),
  ];
  const primaryItems = primary.map(normalizeSummaryLine).filter(Boolean);
  if (primaryItems.length === 1) lines.push(primaryItems[0]);
  if (primaryItems.length > 1) addSection(lines, "Summary", primaryItems);

  const created = [
    ...stringList(data.files_created),
    ...stringList(data.created_files),
    ...stringList(data.artifacts_created),
  ];
  const changed = [
    ...stringList(data.files_changed),
    ...stringList(data.changed_files),
    ...stringList(data.files_modified),
    ...stringList(data.modified_files),
  ].filter((path) => !created.includes(path));
  const artifacts = stringList(data.artifacts).filter(
    (path) => !created.includes(path) && !changed.includes(path),
  );
  addSection(lines, "Files", [
    ...pathBullets(created, "Created"),
    ...pathBullets(changed, "Changed"),
    ...pathBullets(artifacts, "Artifact"),
  ]);
  addSection(lines, "Risks", stringList(data.risks).map(normalizeSummaryLine));
  addSection(lines, "Checks", [
    ...stringList(data.validation),
    ...stringList(data.checks),
    ...stringList(data.tests),
  ].map(normalizeSummaryLine));

  if (lines.length === 0) return "";
  return lines.join("\n");
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
  return (
    textValue(payload.text) ||
    textValue(payload.message) ||
    textValue(payload.objective) ||
    textValue(metadata.text) ||
    textValue(metadata.message) ||
    textValue(metadata.objective)
  );
}

function answerEventFromAgentEvents(events: ChatV2AgentRunEvent[]) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (terminalTaskStatuses.has(event.type) && humanEventSummary(event)) return event;
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

function isMachineSummary(summary: string, event: ChatV2AgentRunEvent) {
  const source = eventSource(event);
  const text = summary.trim();
  if (!text) return true;
  if (text === source || text === event.type) return true;
  if (/^Token usage\b/i.test(text)) return true;
  if (["completed", "acknowledged", "released", "model.requested", "tool.started"].includes(text)) {
    return true;
  }
  return /^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$/i.test(text) && !/\s/.test(text);
}

function humanEventSummary(event: ChatV2AgentRunEvent) {
  const summary = eventSummary(event).trim();
  const structured = formatStructuredAgentSummary(summary);
  if (structured) return structured;
  return isMachineSummary(summary, event) ? "" : summary;
}

function toolLabel(toolId: string) {
  return toolId.replace(/_/g, " ");
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

function eventActivityLine(event: ChatV2AgentRunEvent) {
  const source = eventSource(event);
  const payload = eventPayload(event);
  const human = humanEventSummary(event);
  if (human && event.type !== "token_usage_recorded") return human;

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
  if (source === "tool.started") {
    const toolId = eventPayloadText(event, "tool_id");
    return toolId ? `Using ${toolLabel(toolId)}.` : "Using a workspace tool.";
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
  if (source === "super.heartbeat") {
    const detail = eventPayloadText(event, "detail");
    return detail ? `Working: ${detail}.` : "Working.";
  }
  if (source.includes("validation")) return "Checking changes.";
  if (source === "worker.started" || source === "live.generic_build.started") {
    return "Working in this workspace.";
  }
  if (event.type === "failed" || event.type === "blocked") return human || "Super DAN needs attention.";
  if (event.type === "completed") return human || "Super DAN completed.";
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
    const outcome = [...changedPathLines(group), ...artifactLines(group)];
    if (terminal) {
      const answer = humanEventSummary(terminal);
      if (answer) {
        chunks.push({
          id: `agent-answer:${key}`,
          kind: "agent",
          title: terminal.type === "completed" ? "DAN · Answer" : `DAN · ${terminal.type}`,
          body: answer,
          status: eventChunkStatus(terminal),
          meta: eventSource(terminal),
          taskId: terminal.task_id,
          runId: terminal.run_id,
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

function taskProgressLabel(task: ChatV2TaskSnapshot) {
  const text = textValue(task.latest_progress);
  if (
    !text ||
    ["model.requested", "tool.started", "completed"].includes(text) ||
    (/^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$/i.test(text) && !/\s/.test(text)) ||
    /^Token usage\b/i.test(text)
  ) {
    if (task.status === "running") return "Super DAN is working";
    if (task.status === "queued") return "Super DAN is queued";
    if (task.status === "completed") return "Super DAN completed";
    if (task.status === "failed" || task.status === "blocked") return "Super DAN needs attention";
    return task.phase || "Super DAN task";
  }
  return text;
}

function taskMessageLabel(task: ChatV2TaskSnapshot) {
  const metadata = task.metadata ?? {};
  return (
    textValue(metadata.message) ||
    textValue(metadata.objective) ||
    textValue(metadata.text) ||
    taskProgressLabel(task)
  );
}

function queueRowsFromTasks(tasks: ChatV2TaskSnapshot[]) {
  const rows: QueueRow[] = [];
  for (const task of tasks) {
    if (!isTaskTerminal(task)) {
      rows.push({
        id: `task:${task.task_id}`,
        label: task.status === "queued" ? "Queued run" : "Active run",
        detail: taskMessageLabel(task),
        status: task.status,
        active: task.status !== "queued",
      });
    }

    for (const item of task.metadata?.queue_items ?? []) {
      const status = item.status || "queued";
      if (terminalQueueStatuses.has(status.toLowerCase())) continue;
      rows.push({
        id: `queue:${item.id}`,
        label: item.lane === "continue_after_current" ? "Next message" : "Steering message",
        detail: item.text.trim() || "Queued message",
        status,
        active: false,
      });
    }
  }
  return rows.slice(0, 8);
}

async function loadSuperDanThreadHistory(threadId: string, title: string) {
  const tasks = await listChatV2ThreadTasks(threadId);
  const items = await Promise.all(
    [...tasks].reverse().map(async (task) => {
      const runId = taskRunId(task);
      if (!runId) return { task, run: null, events: [] as ChatV2AgentRunEvent[], runId: "" };
      const [run, events] = await Promise.all([
        getChatV2AgentRun(runId).catch(() => null),
        getChatV2AgentRunEvents(runId).catch(() => [] as ChatV2AgentRunEvent[]),
      ]);
      return { task, run, events, runId };
    }),
  );
  const events = items.flatMap((item) => item.events);
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
  return { tasks, events, messages };
}

function buildSurfaceContext(args: {
  note: WorkspaceNote | null;
  selectedChunk: WorkspaceChunk | null;
  workspaceRoot: string;
  notesRoot: string;
  activeFile: WorkspaceFileEntry | null;
  activeFileContent: string;
  wireGuardStatus: WorkspaceWireGuardStatus | null;
}) {
  const {
    note,
    selectedChunk,
    workspaceRoot,
    notesRoot,
    activeFile,
    activeFileContent,
    wireGuardStatus,
  } = args;
  return {
    identity: { name: "DAN Workspace", role: "chunk_workspace" },
    workspace_root: workspaceRoot,
    notes_root: notesRoot,
    workspace_source: "chunk_workspace",
    ui_surface: "chunk_workspace",
    surface_profile: SUPER_TUI_PROFILE,
    agent_profile: SUPER_TUI_PROFILE,
    agent_backend: SUPER_DAN_BACKEND,
    gui_for: "dan super-tui",
    capabilities: [
      "notes",
      "markdown_preview",
      "chunk_selection",
      "background_agent_runs",
      "checkpoint_commands",
      "read_only_wireguard_status",
    ],
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
    active_note: note
      ? {
          id: note.id,
          title: note.title,
          path: note.path ?? null,
          source: note.source,
          dirty: note.status === "dirty",
        }
      : null,
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
    active_file: activeFile
      ? {
          path: activeFile.path,
          relativePath: activeFile.relative_path,
          name: activeFile.name,
          preview: activeFileContent.slice(0, 1800),
        }
      : null,
  };
}

function statusTone(status: ChunkStatus) {
  if (status === "running") return "border-blue-200 bg-blue-50 text-blue-700";
  if (status === "dirty" || status === "queued") return "border-amber-200 bg-amber-50 text-amber-700";
  if (status === "error") return "border-rose-200 bg-rose-50 text-rose-700";
  return "border-slate-200 bg-slate-50 text-slate-500";
}

function chunkTone(chunk: WorkspaceChunk) {
  const title = chunk.title.toLowerCase();
  if (chunk.status === "error") {
    return {
      card: "border-rose-200 border-l-4 border-l-rose-500 bg-rose-50/35 dark:border-rose-900/70 dark:border-l-rose-400 dark:bg-rose-950/20",
      icon: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-200",
    };
  }
  if (chunk.status === "running") {
    return {
      card: "border-blue-200 border-l-4 border-l-blue-500 bg-blue-50/35 dark:border-blue-900/70 dark:border-l-blue-400 dark:bg-blue-950/20",
      icon: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-200",
    };
  }
  if (title.includes("outcome")) {
    return {
      card: "border-emerald-200 border-l-4 border-l-emerald-500 bg-emerald-50/30 dark:border-emerald-900/70 dark:border-l-emerald-400 dark:bg-emerald-950/15",
      icon: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-200",
    };
  }
  if (title.includes("answer")) {
    return {
      card: "border-sky-200 border-l-4 border-l-sky-500 bg-sky-50/30 dark:border-sky-900/70 dark:border-l-sky-400 dark:bg-sky-950/15",
      icon: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-200",
    };
  }
  return {
    card: "border-slate-200 border-l-4 border-l-slate-300 bg-white dark:border-slate-800 dark:border-l-slate-700 dark:bg-slate-950",
    icon: "bg-slate-100 text-slate-500 dark:bg-slate-900 dark:text-slate-300",
  };
}

function noteStatusText(note: WorkspaceNote | null) {
  if (!note) return "No note";
  if (note.status === "dirty") return "Unsaved";
  if (note.status === "saving") return "Saving";
  if (note.status === "loading") return "Loading";
  if (note.status === "error") return "Save issue";
  return "Saved";
}

function QueueList({ rows }: { rows: QueueRow[] }) {
  if (rows.length === 0) {
    return <div className="px-2 text-xs text-slate-400">No queued messages</div>;
  }
  return (
    <div className="space-y-2">
      {rows.map((row) => {
        return (
          <div
            key={row.id}
            className={cx(
              "rounded-md border bg-white px-2.5 py-2 text-xs dark:bg-slate-950",
              row.active
                ? "border-blue-200 dark:border-blue-900/70"
                : "border-slate-200 dark:border-slate-800",
            )}
          >
            <div className="flex items-center gap-2">
              {row.active ? (
                <Loader2 size={13} className="animate-spin text-blue-600" />
              ) : (
                <Clock3 size={13} className="text-amber-600" />
              )}
              <div className="min-w-0 flex-1 truncate font-medium text-slate-800 dark:text-slate-200">
                {row.label}
              </div>
              <span className="shrink-0 rounded-full border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500 dark:border-slate-700">
                {row.status}
              </span>
            </div>
            <div className="mt-1.5 line-clamp-2 break-words text-[11px] leading-4 text-slate-500 dark:text-slate-400">
              {row.detail}
            </div>
          </div>
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

const KNOWLEDGE_GRAPH_COLORS: Record<string, string> = {
  papers: "#7c3aed",
  notes: "#0891b2",
  log: "#ea580c",
  "to-do": "#16a34a",
  blogs: "#2563eb",
  research: "#e11d48",
  root: "#64748b",
};

function buildKnowledgeGraph(
  notes: WorkspaceNote[],
  root: string,
): { nodes: KnowledgeGraphNode[]; links: KnowledgeGraphLink[]; sections: string[] } {
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
  const viewWidth = 920;
  const viewHeight = 560;
  const centerX = viewWidth / 2;
  const centerY = viewHeight / 2;
  const sectionRadius = Math.min(viewWidth, viewHeight) * 0.31;
  const nodes = pageNodes.map((note, index) => {
    const id = pageIdFromNote(note);
    const section = noteSection(note, root);
    const groupIndex = sectionIndex.get(section) ?? 0;
    const groupCount = Math.max(1, sectionCounts.get(section) ?? 0);
    sectionCounts.set(section, groupCount + 1);
    const groupAngle = (Math.PI * 2 * groupIndex) / Math.max(1, sections.length) - Math.PI / 2;
    const localAngle = (index * 2.399963229728653) % (Math.PI * 2);
    const localRadius = 24 + (groupCount % 9) * 16;
    const groupX = centerX + Math.cos(groupAngle) * sectionRadius;
    const groupY = centerY + Math.sin(groupAngle) * sectionRadius * 0.72;
    const nodeDegree = degree[id] ?? 0;
    return {
      id,
      title: note.title,
      section,
      degree: nodeDegree,
      note,
      x: Math.max(28, Math.min(viewWidth - 28, groupX + Math.cos(localAngle) * localRadius)),
      y: Math.max(28, Math.min(viewHeight - 28, groupY + Math.sin(localAngle) * localRadius)),
      radius: Math.max(4.5, Math.min(15, 5 + nodeDegree * 1.4)),
    };
  });

  return { nodes, links, sections };
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
  const graph = useMemo(() => buildKnowledgeGraph(notes, root), [notes, root]);
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

  return (
    <div className="mx-auto flex h-full min-h-[520px] max-w-6xl flex-col">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
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
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={section}
            onChange={(event) => setSection(event.target.value)}
            className="h-9 rounded-lg border border-slate-200 bg-white px-2.5 text-xs outline-none transition focus:border-slate-400 dark:border-slate-800 dark:bg-slate-950"
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
            className="h-9 w-44 rounded-lg border border-slate-200 bg-white px-2.5 text-xs outline-none transition placeholder:text-slate-400 focus:border-slate-400 dark:border-slate-800 dark:bg-slate-950"
          />
          <label className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
            <input
              type="checkbox"
              checked={showLabels}
              onChange={(event) => setShowLabels(event.target.checked)}
            />
            Labels
          </label>
          <label className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
            <input
              type="checkbox"
              checked={showOrphans}
              onChange={(event) => setShowOrphans(event.target.checked)}
            />
            Orphans
          </label>
        </div>
      </div>
      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
        <div className="min-h-[420px] overflow-hidden rounded-xl border border-slate-200 bg-[#fbfaf7] shadow-inner dark:border-slate-800 dark:bg-slate-950">
          {visible.nodes.length === 0 ? (
            <div className="grid h-full place-items-center text-sm text-slate-400">
              No matching nodes
            </div>
          ) : (
            <svg viewBox="0 0 920 560" role="img" className="h-full min-h-[420px] w-full">
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
                      transform={`translate(${node.x} ${node.y})`}
                      opacity={active ? 1 : 0.14}
                      className="cursor-pointer"
                      onMouseEnter={() => setHoveredId(node.id)}
                      onMouseLeave={() => setHoveredId(null)}
                      onClick={() => onSelect(node.note)}
                    >
                      <circle
                        r={node.radius}
                        fill={color}
                        stroke="#ffffff"
                        strokeWidth={1.6}
                      />
                      {showLabels && (
                        <text
                          x={node.radius + 5}
                          y={4}
                          fontSize="9"
                          fill="#44403c"
                          className="select-none"
                        >
                          {node.title.length > 35 ? `${node.title.slice(0, 32)}...` : node.title}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            </svg>
          )}
        </div>
        <aside className="rounded-xl border border-slate-200 bg-white p-3 text-xs dark:border-slate-800 dark:bg-slate-950">
          {selectedNode ? (
            <div>
              <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
                Selected
              </div>
              <div className="mt-2 font-semibold text-slate-900 dark:text-slate-100">
                {selectedNode.title}
              </div>
              <div className="mt-1 text-slate-500">
                {selectedNode.section} · {selectedNode.degree} connections
              </div>
              <button
                type="button"
                onClick={() => onSelect(selectedNode.note)}
                className="mt-3 h-8 rounded-lg bg-slate-950 px-3 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-950"
              >
                Open page
              </button>
            </div>
          ) : (
            <div className="text-slate-500">
              Hover a node to inspect its category and connection count. Click a node to open the page.
            </div>
          )}
          <div className="mt-4 space-y-1.5">
            {graph.sections.map((item) => (
              <div key={item} className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2 truncate">
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ background: KNOWLEDGE_GRAPH_COLORS[item] || "#78716c" }}
                  />
                  <span className="truncate">{item}</span>
                </span>
                <span className="text-slate-400">
                  {graph.nodes.filter((node) => node.section === item).length}
                </span>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

function ChunkCard({
  chunk,
  active,
  onSelect,
}: {
  chunk: WorkspaceChunk;
  active: boolean;
  onSelect: () => void;
}) {
  const Icon =
    chunk.kind === "agent"
      ? Bot
      : chunk.kind === "chat"
        ? MessageSquareText
        : chunk.kind === "code"
          ? TerminalSquare
          : FileText;
  const tone = chunkTone(chunk);
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onSelect();
  };

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={handleKeyDown}
      aria-pressed={active}
      className={cx(
        "w-full cursor-pointer rounded-xl border p-4 text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] outline-none transition focus-visible:ring-2 focus-visible:ring-slate-300 dark:focus-visible:ring-slate-600",
        tone.card,
        active
          ? "ring-2 ring-slate-300 dark:ring-slate-600"
          : "hover:border-slate-300 hover:shadow-md dark:hover:border-slate-700",
      )}
    >
      <div className="flex gap-3.5">
        <div
          className={cx(
            "mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg",
            tone.icon,
          )}
        >
          <Icon size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-[14px] font-semibold leading-5 text-slate-950 dark:text-slate-100">
                {chunk.title}
              </div>
              <div className="mt-0.5 truncate text-[11px] leading-4 text-slate-500">
                {chunk.filePath || chunk.meta}
              </div>
            </div>
            <span
              className={cx(
                "shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold",
                statusTone(chunk.status),
              )}
            >
              {chunk.status}
            </span>
          </div>
          <div className="mt-3 min-w-0 whitespace-normal break-words text-sm leading-6 text-slate-700 dark:text-slate-300">
            <MarkdownRenderer
              content={chunk.body || "Waiting for output..."}
              className="[&_code]:break-words [&_h3]:mb-1 [&_h3]:mt-3 [&_h3]:text-[11px] [&_h3]:uppercase [&_h3]:tracking-[0.16em] [&_li]:break-words [&_li]:leading-6 [&_ol]:my-1 [&_p]:my-0 [&_p]:break-words [&_p]:leading-6 [&_ul]:my-1.5"
            />
          </div>
        </div>
      </div>
    </article>
  );
}

function WorkspaceFileTree({
  nodes,
  activePath,
  query,
  expanded,
  onToggle,
  onSelect,
}: {
  nodes: FileTreeNode[];
  activePath: string | null;
  query: string;
  expanded: Record<string, boolean>;
  onToggle: (path: string) => void;
  onSelect: (entry: WorkspaceFileEntry) => void;
}) {
  const normalizedQuery = query.trim().toLowerCase();

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
    const visibleChildren = node.children.filter(nodeMatches);
    return (
      <div key={node.path}>
        <button
          type="button"
          onClick={() => (node.is_directory ? onToggle(node.relative_path) : onSelect(node))}
          className={cx(
            "flex h-7 w-full items-center gap-1.5 rounded px-1.5 text-left text-[13px]",
            isActive
              ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-950"
              : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-900",
          )}
          style={{ paddingLeft: 6 + depth * 14 }}
        >
          {node.is_directory ? (
            isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />
          ) : (
            <span className="w-[13px] shrink-0" />
          )}
          {node.is_directory ? (
            <Folder size={14} className="shrink-0 text-slate-500" />
          ) : (
            <File size={14} className="shrink-0 text-slate-400" />
          )}
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
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
  onToggle,
  onSelect,
}: {
  nodes: NoteTreeNode[];
  activeId: string | null;
  query: string;
  expanded: Record<string, boolean>;
  onToggle: (id: string) => void;
  onSelect: (note: WorkspaceNote) => void;
}) {
  const normalizedQuery = query.trim().toLowerCase();

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
    return (
      <div key={node.id}>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => (hasChildren ? onToggle(node.id) : node.note && onSelect(node.note))}
            className={cx(
              "grid h-7 w-5 shrink-0 place-items-center rounded text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-900",
              !hasChildren && "pointer-events-none opacity-0",
            )}
            style={{ marginLeft: depth * 14 }}
            aria-label={isOpen ? "Collapse note folder" : "Expand note folder"}
          >
            {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </button>
          <button
            type="button"
            onClick={() => (node.note ? onSelect(node.note) : onToggle(node.id))}
            className={cx(
              "flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-left text-[13px] transition",
              isActive
                ? "bg-slate-900 text-white shadow-sm dark:bg-slate-100 dark:text-slate-950"
                : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-900",
            )}
            title={node.pathLabel}
          >
            {node.isFolder ? (
              isOpen ? (
                <FolderOpen size={14} className="shrink-0 opacity-70" />
              ) : (
                <Folder size={14} className="shrink-0 opacity-70" />
              )
            ) : (
              <FileText size={14} className="shrink-0 opacity-70" />
            )}
            <span className="min-w-0 flex-1 truncate">{node.label}</span>
            {canSelect && node.note?.status === "dirty" && (
              <Circle size={8} className="shrink-0" fill="currentColor" />
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

export default function ChunkWorkspaceApp() {
  const isPhoneViewport = usePhoneViewport();
  const initialNotes = useMemo(() => readStoredNotes(), []);
  const initialLayout = useMemo(() => readStoredLayout(), []);
  const initialUiState = useMemo(() => readStoredUiState(), []);
  const [notes, setNotes] = useState<WorkspaceNote[]>(initialNotes.notes);
  const [activeNoteId, setActiveNoteId] = useState<string | null>(initialNotes.activeId);
  const [activePane, setActivePane] = useState<WorkspacePane>(initialUiState.activePane);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(
    initialUiState.selectedChunkId,
  );
  const [threads, setThreads] = useState<ChatV2ThreadSummary[]>([]);
  const [threadQuery, setThreadQuery] = useState(initialUiState.threadQuery);
  const [threadWorkspaces, setThreadWorkspaces] = useState<Record<string, string>>(
    () => readStoredThreadWorkspaces(),
  );
  const [collapsedThreadGroups, setCollapsedThreadGroups] = useState<Record<string, boolean>>(
    initialUiState.collapsedThreadGroups,
  );
  const [activeThread, setActiveThread] = useState<{
    id: string;
    workflowId: string;
    title?: string;
  } | null>(null);
  const [loadingThreadId, setLoadingThreadId] = useState<string | null>(null);
  const [pendingAssistantIds, setPendingAssistantIds] = useState<Record<string, boolean>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tasks, setTasks] = useState<ChatV2TaskSnapshot[]>([]);
  const [agentEvents, setAgentEvents] = useState<ChatV2AgentRunEvent[]>([]);
  const [input, setInput] = useState("");
  const [activeRunPlacement, setActiveRunPlacement] = useState<ActiveRunPlacement>("steer");
  const [status, setStatus] = useState("Ready");
  const [sending, setSending] = useState(false);
  const [noteQuery, setNoteQuery] = useState(initialUiState.noteQuery);
  const [noteFacet, setNoteFacet] = useState(initialUiState.noteFacet);
  const [noteRailView, setNoteRailView] = useState<NoteRailView>(
    initialUiState.noteRailView,
  );
  const [noteMention, setNoteMention] = useState<NoteMentionState | null>(null);
  const [noteMentionIndex, setNoteMentionIndex] = useState(0);
  const [notesRoot, setNotesRoot] = useState("");
  const [devRoot, setDevRoot] = useState("");
  const [devFiles, setDevFiles] = useState<WorkspaceFileEntry[]>([]);
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
  const [rootEditing, setRootEditing] = useState(false);
  const [rootInput, setRootInput] = useState("");
  const [rootSuggestions, setRootSuggestions] = useState<WorkspaceRootSuggestion[]>([]);
  const [storedRoots, setStoredRoots] = useState<string[]>(() => readStoredRoots());
  const [loadingRoots, setLoadingRoots] = useState(false);
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
  const [leftRailWidth, setLeftRailWidth] = useState(initialLayout.leftRailWidth);
  const [phonePage, setPhonePage] = useState<PhonePage>(
    initialUiState.activePane === "notes" ? "note-preview" : "chat",
  );
  const [wireGuardStatus, setWireGuardStatus] = useState<WorkspaceWireGuardStatus | null>(null);
  const [wireGuardLoading, setWireGuardLoading] = useState(false);
  const [sessionSwipeOffsets, setSessionSwipeOffsets] = useState<Record<string, number>>({});

  const workspaces = useWorkspaceStore((state) => state.workspaces);
  const activeWorkspaceId = useWorkspaceStore((state) => state.activeWorkspaceId);
  const createWorkspace = useWorkspaceStore((state) => state.createWorkspace);
  const removeWorkspace = useWorkspaceStore((state) => state.removeWorkspace);
  const setActiveWorkspace = useWorkspaceStore((state) => state.setActiveWorkspace);
  const updateWorkspace = useWorkspaceStore((state) => state.updateWorkspace);
  const workspace = useMemo(
    () => workspaces.find((item) => item.id === activeWorkspaceId),
    [activeWorkspaceId, workspaces],
  );

  const messagesRef = useRef<ChatMessage[]>([]);
  const streamRef = useRef<WebSocket | null>(null);
  const agentStreamRef = useRef<WebSocket | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const noteEditorRef = useRef<HTMLTextAreaElement | null>(null);
  const sessionSwipeRef = useRef<SessionSwipeState | null>(null);
  const suppressSessionClickRef = useRef<string | null>(null);
  const noteContentCacheRef = useRef<Record<string, NoteCacheEntry>>(
    readStoredNoteContentCache(),
  );
  const selfWriteAtRef = useRef<Record<string, number>>({});
  const serverNotesLoadedRef = useRef(false);
  const activeNote = notes.find((note) => note.id === activeNoteId) ?? notes[0] ?? null;
  const activeNoteSection = activeNote ? noteSection(activeNote, notesRoot) : "";
  const deferredNoteContent = useDeferredValue(activeNote?.content ?? "");
  const parsedActiveNote = useMemo(
    () => extractHugoPage(deferredNoteContent, activeNote?.title || "Note"),
    [activeNote?.title, deferredNoteContent],
  );
  const renderedNoteBody = useMemo(
    () => formatHugoPreviewBody(parsedActiveNote.body),
    [parsedActiveNote.body],
  );
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
      notes
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
    [notes, notesRoot],
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
  const selectNote = useCallback((note: WorkspaceNote) => {
    setActiveNoteId(note.id);
    setSelectedChunkId(null);
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
    setNoteRailView("pages");
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
  const workspaceRoot = workspace?.pinnedPaths[0] ?? "";
  const developmentRoot = workspaceRoot || devRoot;
  const activeFileEntry = useMemo(
    () => devFiles.find((entry) => entry.path === activeFilePath) ?? null,
    [activeFilePath, devFiles],
  );

  const saveNoteNow = useCallback(async (noteToSave: WorkspaceNote | null, force = false) => {
    if (!noteToSave) return;
    if (!force && noteToSave.status !== "dirty") return;
    if (noteToSave.status === "saving") return;
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
      if (ok) {
        selfWriteAtRef.current[noteToSave.path] = Date.now();
        noteContentCacheRef.current[noteToSave.path] = {
          content: noteToSave.content,
          updatedAt: summary ? Math.floor(summary.mtime * 1000) : Date.now(),
          size: summary?.size ?? noteToSave.content.length,
        };
        persistNoteContentCache(noteContentCacheRef.current);
      }
      setNotes((previous) =>
        previous.map((note) =>
          note.id === noteToSave.id
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
                status: ok ? "clean" : "error",
                updatedAt: summary ? Math.floor(summary.mtime * 1000) : Date.now(),
                size: summary?.size ?? noteToSave.content.length,
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
  }, []);

  useEffect(() => {
    if (!rootEditing) setRootInput(developmentRoot);
  }, [developmentRoot, rootEditing]);

  useEffect(() => {
    if (workspaces.length === 0) createWorkspace("DAN Workspace", "chat");
  }, [createWorkspace, workspaces.length]);

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
      leftRailWidth,
    });
  }, [
    showSessionRail,
    showFileExplorer,
    showConversationChunks,
    showSidecarPreview,
    showNotesRail,
    showNoteEditor,
    showNotesPreview,
    leftRailWidth,
  ]);

  useEffect(() => {
    persistUiState({
      activePane,
      selectedChunkId,
      activeFilePath,
      threadQuery,
      noteQuery,
      noteFacet,
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

  useEffect(() => {
    void refreshThreads();
  }, [refreshThreads]);

  const refreshTasks = useCallback(async (threadId: string) => {
    try {
      const next = await listChatV2ThreadTasks(threadId);
      setTasks(next);
      return next;
    } catch {
      setTasks([]);
      return [];
    }
  }, []);

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
    if (!targetThreadId || activeThread || threads.length === 0) return;
    const match = threads.find(
      (thread) =>
        thread.id === targetThreadId &&
        (!targetWorkflowId || thread.workflow_id === targetWorkflowId),
    );
    if (!match) return;
    void getChatV2Thread(match.workflow_id, match.id)
      .then(async (thread) => {
        const history = await loadSuperDanThreadHistory(thread.id, thread.title).catch(() => ({
          tasks: [] as ChatV2TaskSnapshot[],
          events: [] as ChatV2AgentRunEvent[],
          messages: [] as ChatMessage[],
        }));
        const restoredMessages = thread.messages.length > 0 ? thread.messages : history.messages;
        setActiveThread({ id: thread.id, workflowId: thread.workflow_id, title: thread.title });
        setMessages(restoredMessages);
        messagesRef.current = restoredMessages;
        setTasks(history.tasks);
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
      .catch(() => setStatus("Session restore failed"));
  }, [activeThread, refreshThreads, threads, workspace?.activeThreadId]);

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
          return currentNote?.source === "server" || currentNote?.source === "disk"
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

  useEffect(() => {
    if (!activeFilePath) {
      setActiveFileContent("");
      setActiveFileStatus("idle");
      return;
    }
    let cancelled = false;
    setActiveFileStatus("loading");
    void readWorkspaceFile(activeFilePath, developmentRoot || undefined)
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
  }, [activeFilePath, developmentRoot]);

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

  const messageChunks = useMemo<WorkspaceChunk[]>(
    () =>
      messages.slice(-16).map((message) => ({
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
        taskId: message.taskRunRef?.taskId,
        runId: message.taskRunRef?.runId,
      })),
    [messages, pendingAssistantIds],
  );

  const eventChunks = useMemo<WorkspaceChunk[]>(
    () => compactAgentRunChunks(agentEvents),
    [agentEvents],
  );

  const chunks = useMemo(
    () => messageChunks.concat(eventChunks),
    [eventChunks, messageChunks],
  );

  const selectedChunk = chunks.find((chunk) => chunk.id === selectedChunkId) ?? null;

  useEffect(() => {
    if (!selectedChunkId || chunks.length === 0) return;
    if (!chunks.some((chunk) => chunk.id === selectedChunkId)) setSelectedChunkId(null);
  }, [chunks, selectedChunkId]);

  const activeRunningTask = tasks.find((task) => !isTaskTerminal(task)) ?? null;
  const activeRunId = activeRunningTask ? taskRunId(activeRunningTask) : "";
  const runningTaskByThreadId = useMemo(() => {
    const entries = tasks
      .filter((task) => !isTaskTerminal(task))
      .map((task) => [task.thread_id, task] as const);
    return new Map(entries);
  }, [tasks]);

  const noteFacetOptions = useMemo(() => {
    const sections = new Map<string, number>();
    const tags = new Map<string, number>();
    for (const note of notes) {
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
  }, [notes, notesRoot]);
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
    return notes.filter((note) => {
      if (!noteMatchesFacet(note, notesRoot, noteFacet)) return false;
      if (!query) return true;
      return `${note.title} ${note.path ?? ""} ${note.relativePath ?? ""} ${note.pageID ?? ""} ${noteSection(note, notesRoot)} ${note.tags.join(" ")} ${note.categories.join(" ")}`
        .toLowerCase()
        .includes(query);
    });
  }, [noteFacet, noteQuery, notes, notesRoot]);
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
  const upperNote = parentNoteFor(activeNote, notes, notesRoot);

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
  const sessionGroups = useMemo(() => {
    const query = threadQuery.trim().toLowerCase();
    const groups: SessionGroup[] = workspaces.map((item, index) => ({
      id: `workspace:${item.id}`,
      name: workspaceDisplayName(item),
      workspaceId: item.id,
      root: item.pinnedPaths[0] ?? "",
      shortcut: index < 9 ? String(index + 1) : "",
      threads: [],
    }));
    const groupByWorkspaceId = new Map(groups.map((group) => [group.workspaceId, group]));
    const storedThreadGroupById = new Map<string, string>();
    for (const item of workspaces) {
      for (const threadId of [item.activeThreadId, ...item.openThreadIds]) {
        if (threadId) storedThreadGroupById.set(threadId, item.id);
      }
    }
    const projectGroups = new Map<string, SessionGroup>();
    const archivedThreads: ChatV2ThreadSummary[] = [];
    for (const thread of threads) {
      const haystack = `${thread.title} ${thread.workflow_id} ${thread.id}`.toLowerCase();
      if (query && !haystack.includes(query)) continue;
      if (thread.archived) {
        archivedThreads.push(thread);
        continue;
      }
      const group = groupByWorkspaceId.get(
        threadWorkspaces[threadWorkspaceKey(thread.workflow_id, thread.id)] ??
          storedThreadGroupById.get(thread.id) ??
          "",
      );
      if (group) {
        group.threads.push(thread);
        continue;
      }
      const workflowId = thread.workflow_id || "unknown";
      const projectId = `project:${workflowId}`;
      const projectGroup =
        projectGroups.get(projectId) ??
        ({
          id: projectId,
          name: `Project: ${projectLabelFromWorkflowId(workflowId)}`,
          workspaceId: null,
          root: workflowId === DEFAULT_WORKFLOW_ID ? "" : workflowId,
          shortcut: "",
          threads: [],
          defaultCollapsed: true,
        } satisfies SessionGroup);
      projectGroup.threads.push(thread);
      projectGroups.set(projectId, projectGroup);
    }
    const visibleGroups = groups.filter((group) => !query || group.threads.length > 0);
    const visibleProjectGroups = [...projectGroups.values()]
      .filter((group) => group.threads.length > 0)
      .sort((a, b) => b.threads.length - a.threads.length || a.name.localeCompare(b.name));
    const archivedGroup =
      archivedThreads.length > 0
        ? [
            {
              id: "archived",
              name: "Archived",
              workspaceId: null,
              root: "",
              shortcut: "",
              threads: archivedThreads,
              defaultCollapsed: !query,
            } satisfies SessionGroup,
          ]
        : [];
    return visibleGroups.concat(visibleProjectGroups, archivedGroup);
  }, [threadQuery, threadWorkspaces, threads, workspaces]);
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
  const hasActiveRun = Boolean(activeRunId && activeRunningTask);
  const queueRows = useMemo(() => queueRowsFromTasks(tasks), [tasks]);
  const showAgentQueuePanel = queueRows.length > 0;
  const notesGridTemplate = [
    showNotesRail ? `${leftRailWidth}px` : "",
    showNoteEditor ? "minmax(0,1fr)" : "",
    showNotesPreview ? "minmax(0,1fr)" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const renderSessionRail = isPhoneViewport ? phonePage === "sessions" : showSessionRail;
  const renderFileExplorer = isPhoneViewport ? phonePage === "files" : showFileExplorer;
  const renderWorkMain = !isPhoneViewport || phonePage === "chat";
  const renderSidecarPreview = isPhoneViewport ? phonePage === "preview" : showSidecarPreview;
  const renderNotesRail = isPhoneViewport ? phonePage === "note-list" : showNotesRail;
  const renderNoteEditor = isPhoneViewport ? phonePage === "note-edit" : showNoteEditor;
  const renderNotesPreview = isPhoneViewport ? phonePage === "note-preview" : showNotesPreview;
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
  }, [hasActiveRun]);

  useEffect(() => {
    void refreshWireGuardStatus();
  }, [refreshWireGuardStatus]);

  useEffect(() => {
    if (activePane === "work" && !["chat", "sessions", "files", "preview"].includes(phonePage)) {
      setPhonePage("chat");
    }
    if (
      activePane === "notes" &&
      !["note-list", "note-edit", "note-preview"].includes(phonePage)
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

  const toggleThreadGroup = useCallback((groupId: string) => {
    setCollapsedThreadGroups((previous) => ({
      ...previous,
      [groupId]: !previous[groupId],
    }));
  }, []);

  const bindThreadToWorkspace = useCallback(
    (workflowId: string, threadId: string, workspaceId?: string | null) => {
      const targetWorkspaceId = workspaceId || activeWorkspaceId;
      if (!targetWorkspaceId) return;
      setThreadWorkspaces((previous) => ({
        ...previous,
        [threadWorkspaceKey(workflowId, threadId)]: targetWorkspaceId,
      }));
      updateWorkspace(targetWorkspaceId, { activeThreadId: threadId });
    },
    [activeWorkspaceId, updateWorkspace],
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

  const createNote = useCallback(() => {
    const title = `Note ${notes.length + 1}`;
    const stamp = Date.now();
    const createdAt = new Date(stamp).toISOString();
    const relativePath = `notes/note-${stamp}/index.md`;
    const serverPath = notesRoot ? joinPath(notesRoot, relativePath) : "";
    const content = `---\ntitle: "${title}"\nsubtitle: ""\ndate: ${createdAt}\ndraft: false\nabstract: ""\npageID: "notes-note-${stamp}"\ntags: []\ncategories: []\n---\n\n`;
    const note = notesRoot
      ? {
          id: `server:${serverPath}`,
          title,
          path: serverPath,
          relativePath,
          section: pathParts(relativePath)[0] ?? "root",
          source: "server" as const,
          content,
          loaded: true,
          status: "dirty" as const,
          updatedAt: Date.now(),
          size: content.length,
          date: createdAt,
          draft: false,
          tags: [],
          categories: [],
          citations: [],
        }
      : createLocalNote(title);
    setNotes((previous) => [note, ...previous]);
    setActiveNoteId(note.id);
    setSelectedChunkId(null);
  }, [notes.length, notesRoot]);

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

  const createDevelopmentWorkspace = useCallback(() => {
    const id = createWorkspace(undefined, "chat");
    setActiveWorkspace(id);
    setActivePane("work");
    setActiveFilePath(null);
    setExpandedFileDirs({});
    setRootEditing(true);
    setShowSessionRail(true);
    setShowFileExplorer(true);
    setRootInput(developmentRoot || rootOptions[0]?.path || "");
  }, [createWorkspace, developmentRoot, rootOptions, setActiveWorkspace]);

  const openFolder = useCallback(async () => {
    const folder = await nativeDialog.openDirectory();
    if (!folder) {
      setRootEditing(true);
      setRootInput(developmentRoot || rootOptions[0]?.path || "");
      return;
    }
    applyDevelopmentRoot(folder);
  }, [applyDevelopmentRoot, developmentRoot, rootOptions]);

  const startNewSession = useCallback(async () => {
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
    setInput("");
    setActivePane("work");
    try {
      const created = await createChatV2Thread(DEFAULT_WORKFLOW_ID, {
        title: "New Super DAN Session",
        mode: "agent",
      });
      const next = {
        id: created.id,
        workflowId: created.workflow_id || DEFAULT_WORKFLOW_ID,
        title: created.title || "New Super DAN Session",
      };
      setActiveThread(next);
      bindThreadToWorkspace(next.workflowId, next.id);
      if (activeWorkspaceId) {
        setCollapsedThreadGroups((previous) => ({
          ...previous,
          [`workspace:${activeWorkspaceId}`]: false,
        }));
      }
      window.localStorage.setItem(
        LAST_THREAD_STORAGE_KEY,
        JSON.stringify({ threadId: next.id, workflowId: next.workflowId }),
      );
      await refreshThreads();
      setStatus("Ready");
    } catch {
      setStatus("Session create failed");
    }
    window.setTimeout(() => composerRef.current?.focus(), 0);
  }, [activeWorkspaceId, bindThreadToWorkspace, refreshThreads]);

  const openSession = useCallback(
    async (summary: ChatV2ThreadSummary, workspaceId?: string | null) => {
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
      setMessages([]);
      messagesRef.current = [];
      setPendingAssistantIds({});
      setTasks([]);
      setAgentEvents([]);
      setSelectedChunkId(null);
      setShowConversationChunks(true);
      setActivePane("work");
      setLoadingThreadId(summary.id);
      setStatus("Loading session");
      try {
        const thread = await getChatV2Thread(summary.workflow_id, summary.id);
        const history = await loadSuperDanThreadHistory(thread.id, thread.title).catch(() => ({
          tasks: [] as ChatV2TaskSnapshot[],
          events: [] as ChatV2AgentRunEvent[],
          messages: [] as ChatMessage[],
        }));
        const loadedMessages = thread.messages.length > 0 ? thread.messages : history.messages;
        if (workspaceId) {
          setActiveWorkspace(workspaceId);
          bindThreadToWorkspace(thread.workflow_id, thread.id, workspaceId);
        }
        setActivePane("work");
        setSelectedChunkId(null);
        setActiveThread({
          id: thread.id,
          workflowId: thread.workflow_id,
          title: thread.title,
        });
        setMessages(loadedMessages);
        messagesRef.current = loadedMessages;
        setPendingAssistantIds({});
        setTasks(history.tasks);
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
        setStatus(loadedMessages.length === 0 ? "Session has no saved Super DAN history" : "Ready");
      } catch {
        const failed = makeMessage(
          "assistant",
          `Could not load session "${summary.title || summary.id}".`,
        );
        setMessages([failed]);
        messagesRef.current = [failed];
        setStatus("Session load failed");
      } finally {
        setLoadingThreadId((current) => (current === summary.id ? null : current));
      }
    },
    [bindThreadToWorkspace, refreshThreads, setActiveWorkspace],
  );

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
        }
        setAgentEvents((previous) => [...previous, response.event].slice(-80));
        setStatus(response.event.summary || "Stop requested");
      } catch {
        setStatus("Stop request failed");
      }
    },
    [activeRunningTask, activeThread?.id],
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
      if (archived && activeThread?.id === thread.id) {
        streamRef.current?.close();
        streamRef.current = null;
        agentStreamRef.current?.close();
        agentStreamRef.current = null;
        setActiveThread(null);
        setMessages([]);
        messagesRef.current = [];
        setTasks([]);
        setAgentEvents([]);
        setSelectedChunkId(null);
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
    [activeThread?.id, refreshThreads],
  );

  const beginSessionSwipe = useCallback(
    (
      event: PointerEvent<HTMLDivElement>,
      thread: ChatV2ThreadSummary,
      archived: boolean,
    ) => {
      const actionTarget = (event.target as HTMLElement).closest("[data-session-action]");
      if (actionTarget || event.button !== 0) return;
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
      const created = await createChatV2Thread(DEFAULT_WORKFLOW_ID, {
        title: titleFromText(prompt),
        mode: "agent",
      });
      const next = {
        id: created.id,
        workflowId: created.workflow_id || DEFAULT_WORKFLOW_ID,
        title: created.title,
      };
      setActiveThread(next);
      bindThreadToWorkspace(next.workflowId, next.id);
      window.localStorage.setItem(
        LAST_THREAD_STORAGE_KEY,
        JSON.stringify({ threadId: next.id, workflowId: next.workflowId }),
      );
      await refreshThreads();
      return next;
    },
    [activeThread, bindThreadToWorkspace, refreshThreads],
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
    let next: ChatMessage[] = [];
    setMessages((previous) => {
      next = updater(previous);
      messagesRef.current = next;
      return next;
    });
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
    ) => {
      agentStreamRef.current?.close();
      agentStreamRef.current = connectChatV2AgentRunEvents(
        runId,
        (event) => {
          setAgentEvents((previous) => [...previous, event].slice(-80));
          attachRunEventToAssistant(assistantId, runEventPayloadFromAgentEvent(event));
          if (event.task_id) void refreshTasks(thread.id);
          if (event.type === "completed" || event.type === "failed" || event.type === "blocked") {
            const finalText =
              humanEventSummary(event) || (event.type === "completed" ? "Completed." : eventSummary(event));
            const next = applyMessages((previous) =>
              previous.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content: message.content || finalText,
                      taskRunRef: {
                        taskId: event.task_id,
                        runId: event.run_id,
                        status: event.type,
                        workspaceRoot: developmentRoot,
                        workspaceId: workspace?.id ?? "",
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
            setStatus(event.type === "completed" ? "Ready" : event.type);
          }
        },
        undefined,
        () => setStatus("Super DAN event stream interrupted"),
      );
    },
    [
      applyMessages,
      attachRunEventToAssistant,
      persistMessages,
      refreshTasks,
      workspace?.id,
      developmentRoot,
    ],
  );

  const sendAgent = useCallback(
    async (
      prompt: string,
      queueCommand: "append_followup" | "continue_after_current" = "append_followup",
    ) => {
      const thread = await ensureThread(prompt);
      const user = makeMessage("user", prompt);
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
      setStatus("Starting Super DAN");

      if (activeRunId && activeRunningTask) {
        const response = await postChatV2AgentRunCommand(activeRunId, {
          command: queueCommand,
          task_id: activeRunningTask.task_id,
          idempotency_key: nowId(queueCommand),
          payload: {
            text: prompt,
            surface_context: buildSurfaceContext({
              note: activeNote,
              selectedChunk,
              workspaceRoot: developmentRoot,
              notesRoot,
              activeFile: activeFileEntry,
              activeFileContent,
              wireGuardStatus,
            }),
          },
        });
        if (response.task) setTasks((previous) => [response.task!, ...previous]);
        setAgentEvents((previous) => [...previous, response.event].slice(-80));
        const finalMessages = applyMessages((previous) =>
          previous.map((message) =>
            message.id === assistant.id
              ? { ...message, content: response.event.summary || assistant.content }
              : message,
          ),
        );
        await initialPersist;
        await persistMessages(thread, finalMessages, "agent");
        setStatus(
          queueCommand === "continue_after_current"
            ? "Queued after current Super DAN run"
            : "Steering Super DAN",
        );
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
        surface_context: buildSurfaceContext({
          note: activeNote,
          selectedChunk,
          workspaceRoot: developmentRoot,
          notesRoot,
          activeFile: activeFileEntry,
          activeFileContent,
          wireGuardStatus,
        }),
      });
      if (created.task) setTasks((previous) => [created.task!, ...previous]);
      if (created.event) setAgentEvents((previous) => [...previous, created.event!].slice(-80));
      const runId = created.task_run_ref?.run_id || created.v2_control_plane.run_id || "";
      const linkedMessages = applyMessages((previous) =>
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
                    developmentRoot,
                  workspaceId:
                    textValue(created.task?.metadata?.workspace_id) ||
                    created.task_run_ref?.workspace_id ||
                    workspace?.id ||
                    "",
                },
              }
            : message,
        ),
      );
      await initialPersist;
      if (!runId) {
        await persistMessages(thread, linkedMessages, "agent");
        setStatus("Super DAN queued");
        return;
      }
      connectAgentStream(runId, thread, assistant.id);
      const executed = await executeChatV2AgentRun(runId, {
        backend: SUPER_DAN_BACKEND,
        surface_profile: SUPER_TUI_PROFILE,
        background: true,
        profile_policy: {
          backend: SUPER_DAN_BACKEND,
          surface_profile: SUPER_TUI_PROFILE,
        },
        metadata: {
          backend: SUPER_DAN_BACKEND,
          surface_profile: SUPER_TUI_PROFILE,
          compatibility_profile: SUPER_TUI_PROFILE,
          surface: "gui:chunk-workspace",
          requested_from: "chunk_workspace",
          gui_for: "dan super-tui",
          selected_backend: SUPER_DAN_BACKEND,
        },
      });
      if (executed.task) setTasks((previous) => [executed.task!, ...previous]);
      setStatus("Super DAN running");
      await persistMessages(thread, linkedMessages, "agent");
    },
    [
      activeNote,
      activeFileContent,
      activeFileEntry,
      activeRunId,
      activeRunningTask,
      applyMessages,
      connectAgentStream,
      developmentRoot,
      ensureThread,
      notesRoot,
      persistMessages,
      selectedChunk,
      wireGuardStatus,
      workspace?.id,
    ],
  );

  const submit = useCallback(async (modeOverride?: ComposerSubmitMode) => {
    const prompt = input.trim();
    if (!prompt || sending) return;
    const mode = modeOverride ?? (hasActiveRun ? activeRunPlacement : "steer");
    setInput("");
    setSending(true);
    try {
      if (mode === "queue") await sendAgent(prompt, "continue_after_current");
      else await sendAgent(prompt, "append_followup");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Request failed");
    } finally {
      setSending(false);
    }
  }, [activeRunPlacement, hasActiveRun, input, sendAgent, sending]);

  const handleComposerKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== "Enter") return;
      if (event.shiftKey) return;
      event.preventDefault();
      if (event.altKey && hasActiveRun) {
        void submit("queue");
        return;
      }
      void submit();
    },
    [hasActiveRun, submit],
  );

  const openActiveFile = useCallback(() => {
    if (activeFileEntry?.path) void nativeShell.openPath(activeFileEntry.path);
  }, [activeFileEntry?.path]);

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
        setActiveWorkspace(workspaceAtIndex.id);
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
        setActiveWorkspace(workspaceSlots[nextIndex].id);
        setActivePane("work");
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activeNote, activePane, activeWorkspaceId, saveNoteNow, setActiveWorkspace, workspaceSlots]);

  useEffect(() => {
    return () => {
      streamRef.current?.close();
      agentStreamRef.current?.close();
    };
  }, []);

  return (
    <div className="dan-phone-workspace flex h-screen flex-col overflow-hidden bg-[#f4f7fb] text-slate-950 antialiased dark:bg-slate-950 dark:text-slate-100">
      <header className="dan-workspace-header flex h-14 shrink-0 items-center justify-between border-b border-slate-200/80 bg-white/95 px-4 shadow-[0_1px_0_rgba(15,23,42,0.03)] backdrop-blur dark:border-slate-800 dark:bg-slate-950/95">
        <div className="flex min-w-0 items-center gap-3">
          <div className="inline-flex rounded-lg border border-slate-200 bg-slate-100/70 p-0.5 shadow-inner dark:border-slate-800 dark:bg-slate-900">
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
                      setActiveWorkspace(event.target.value);
                      setActivePane("work");
                      setPhonePage("chat");
                    }}
                    className="min-w-0 max-w-[260px] rounded-md border border-transparent bg-transparent py-0 pr-7 text-[15px] font-semibold leading-5 text-slate-950 outline-none transition hover:border-slate-200 hover:bg-slate-50 focus:border-slate-300 focus:bg-white dark:text-slate-100 dark:hover:border-slate-800 dark:hover:bg-slate-900 dark:focus:bg-slate-950"
                    title="Switch workspace"
                  >
                    {workspaces.map((item, index) => (
                      <option key={item.id} value={item.id}>
                        {index + 1} {workspaceDisplayName(item)}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={createDevelopmentWorkspace}
                    className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-800 dark:bg-slate-950 dark:hover:text-slate-100"
                    title="New workspace"
                    aria-label="New workspace"
                  >
                    <Plus size={13} />
                  </button>
                  <button
                    type="button"
                    onClick={openFolder}
                    className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-800 dark:bg-slate-950 dark:hover:text-slate-100"
                    title="Open root folder"
                    aria-label="Open root folder"
                  >
                    <FolderOpen size={13} />
                  </button>
                  {workspaces.length > 1 && activeWorkspaceId && (
                    <button
                      type="button"
                      onClick={() => removeWorkspace(activeWorkspaceId)}
                      className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-slate-200 bg-white text-slate-400 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-800 dark:bg-slate-950 dark:hover:text-slate-100"
                      title="Close workspace"
                      aria-label="Close workspace"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>
                <div className="relative mt-0.5 max-w-[560px]">
                  <label className="flex h-5 min-w-0 items-center gap-1.5 rounded-md border border-transparent pr-1 text-[11px] leading-4 text-slate-500 transition hover:border-slate-200 hover:bg-slate-50 focus-within:border-slate-300 focus-within:bg-white dark:hover:border-slate-800 dark:hover:bg-slate-900 dark:focus-within:bg-slate-950">
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
                          setRootEditing(false);
                          setRootInput(developmentRoot);
                        }
                      }}
                      placeholder="/path/to/workspace"
                      className="min-w-0 flex-1 bg-transparent font-mono text-[11px] outline-none placeholder:text-slate-400"
                      title="Type workspace root and press Enter"
                    />
                    {loadingRoots && rootEditing && (
                      <Loader2 size={11} className="shrink-0 animate-spin text-slate-400" />
                    )}
                  </label>
                  {rootEditing && rootOptions.length > 0 && (
                    <div className="absolute left-0 top-6 z-50 w-[560px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl shadow-slate-950/10 dark:border-slate-800 dark:bg-slate-950">
                      <div className="border-b border-slate-200 px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400 dark:border-slate-800">
                        Workspace Roots
                      </div>
                      <div className="max-h-56 overflow-auto p-1.5">
                        {rootOptions.slice(0, 8).map((option) => (
                          <button
                            key={option.path}
                            type="button"
                            onMouseDown={(event) => {
                              event.preventDefault();
                              applyDevelopmentRoot(option.path);
                            }}
                            className="flex w-full min-w-0 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-slate-600 transition hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900 dark:hover:text-slate-100"
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
                        ))}
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
        <div className="flex items-center gap-2">
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
                title="Toggle conversation chunks (⌥⇧C)"
                aria-label="Toggle conversation chunks"
                className={cx(
                  "grid h-8 w-8 place-items-center rounded-lg border transition",
                  showConversationChunks
                    ? "border-slate-300 bg-slate-100 text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950",
                )}
              >
                <MessageSquareText size={14} />
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
            </div>
          )}
          {activeRunningTask && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
              <Loader2 size={12} className="animate-spin" />
              Agent
            </span>
          )}
          <button
            type="button"
            onClick={() => void refreshWireGuardStatus()}
            className={cx(
              "dan-wireguard-pill inline-flex max-w-[170px] items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold shadow-sm transition",
              wireGuardUi.tone === "ok"
                ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/35 dark:text-emerald-200"
                : wireGuardUi.tone === "warn"
                  ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/35 dark:text-amber-200"
                  : "border-slate-200 bg-white text-slate-500 dark:border-slate-800 dark:bg-slate-900",
            )}
            title={
              wireGuardStatus
                ? `${wireGuardStatus.interface}: ${wireGuardStatus.conflict_policy}`
                : "Read-only WireGuard status"
            }
          >
            {wireGuardLoading ? (
              <Loader2 size={12} className="shrink-0 animate-spin" />
            ) : (
              <Shield size={12} className="shrink-0" />
            )}
            <span className="truncate">
              {wireGuardUi.label} · {wireGuardUi.detail}
            </span>
          </button>
          <span className="max-w-[180px] truncate rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-500 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            {status}
          </span>
        </div>
      </header>

      {activePane === "notes" ? (
        <section
          className="dan-notes-pages grid min-h-0 flex-1 bg-[#f4f7fb] dark:bg-slate-950"
          style={{ gridTemplateColumns: effectiveNotesGridTemplate }}
        >
          {renderNotesRail && (
          <aside className="dan-phone-page relative flex min-h-0 flex-col border-r border-slate-200/80 bg-white/85 dark:border-slate-800 dark:bg-slate-950">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-3 dark:border-slate-800">
              <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                Content
              </div>
              <button
                type="button"
                onClick={createNote}
                className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                title="New page"
              >
                <Plus size={14} />
              </button>
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
            <div className="min-h-0 flex-1 overflow-auto p-3">
              {noteRailView === "pages" && (
                <>
                  {noteFacet !== "all" && (
                    <button
                      type="button"
                      onClick={() => setNoteFacet("all")}
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
                      onToggle={(id) =>
                        setExpandedNoteFolders((previous) => ({
                          ...previous,
                          [id]: !previous[id],
                        }))
                      }
                      onSelect={selectNote}
                    />
                  ) : (
                    <div className="px-2 text-sm text-slate-400">No pages found.</div>
                  )}
                </>
              )}
              {noteRailView === "tags" && (
                <FacetIndex
                  kind="tag"
                  entries={visibleTagFacetOptions}
                  activeFacet={noteFacet}
                  total={notes.length}
                  onSelect={selectNoteFacet}
                />
              )}
              {noteRailView === "sections" && (
                <FacetIndex
                  kind="section"
                  entries={visibleSectionFacetOptions}
                  activeFacet={noteFacet}
                  total={notes.length}
                  onSelect={selectNoteFacet}
                />
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

          {renderNoteEditor && (
          <div className="dan-phone-page relative flex min-h-0 flex-col border-r border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-950">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-4 dark:border-slate-800">
              <div className="min-w-0">
                <div className="truncate text-[14px] font-semibold leading-5 text-slate-900 dark:text-slate-100">
                  {activeNote?.title || "Markdown source"}
                </div>
                <div className="truncate text-[11px] leading-4 text-slate-500">
                  {activeNote ? noteRelativePath(activeNote, notesRoot) : "local note"}
                </div>
              </div>
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
          </div>
          )}

          {renderNotesPreview && (
          <div className="dan-phone-page flex min-h-0 flex-col bg-white dark:bg-slate-950">
            <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-200/80 px-4 dark:border-slate-800">
              <div className="min-w-0">
                <div className="truncate text-[14px] font-semibold leading-5">
                  Preview
                </div>
                <div className="flex min-w-0 items-center gap-1.5 overflow-hidden text-[11px] leading-4 text-slate-500">
                  {activeNote && (
                    <button
                      type="button"
                      onClick={() =>
                        selectNoteFacet(noteFacetKey("section", activeNoteSection))
                      }
                      className="shrink-0 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 transition hover:border-slate-300 hover:bg-white hover:text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:hover:bg-slate-800"
                    >
                      {activeNoteSection}
                    </button>
                  )}
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
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  disabled={!previousNote}
                  onClick={() => previousNote && selectNote(previousNote)}
                  className="grid h-7 w-7 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition enabled:hover:border-slate-300 enabled:hover:text-slate-800 disabled:opacity-35 dark:border-slate-800 dark:bg-slate-950"
                  title="Previous page"
                  aria-label="Previous page"
                >
                  <ChevronRight size={13} className="rotate-180" />
                </button>
                <button
                  type="button"
                  disabled={!upperNote}
                  onClick={() => upperNote && selectNote(upperNote)}
                  className="grid h-7 w-7 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition enabled:hover:border-slate-300 enabled:hover:text-slate-800 disabled:opacity-35 dark:border-slate-800 dark:bg-slate-950"
                  title="Parent page"
                  aria-label="Parent page"
                >
                  <ChevronRight size={13} className="-rotate-90" />
                </button>
                <button
                  type="button"
                  disabled={!nextNote}
                  onClick={() => nextNote && selectNote(nextNote)}
                  className="grid h-7 w-7 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition enabled:hover:border-slate-300 enabled:hover:text-slate-800 disabled:opacity-35 dark:border-slate-800 dark:bg-slate-950"
                  title="Next page"
                  aria-label="Next page"
                >
                  <ChevronRight size={13} />
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto px-7 py-6">
              {activeNote?.status === "loading" || activeNote?.status === "error" ? (
                <MarkdownRenderer
                  content={
                    activeNote.status === "loading"
                      ? "_Loading note..._"
                      : `> ${activeNote.error || "Note load failed."}`
                  }
                />
              ) : isKnowledgeGraphPage ? (
                <KnowledgeGraphView notes={notes} root={notesRoot} onSelect={selectNote} />
              ) : (
                <article className="mx-auto max-w-3xl">
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
                  <MarkdownRenderer
                    content={renderedNoteBody || "_No note content yet._"}
                    onClick={handleNotePreviewClick}
                  />
                </article>
              )}
            </div>
          </div>
          )}
        </section>
      ) : (
        <section className="dan-work-pages flex min-h-0 flex-1 bg-[#f4f7fb] dark:bg-slate-950">
          {renderSessionRail && (
            <aside
              className="dan-phone-page dan-session-page relative flex min-h-0 shrink-0 flex-col border-r border-slate-200/80 bg-white/85 shadow-[1px_0_0_rgba(15,23,42,0.02)] dark:border-slate-800 dark:bg-slate-950"
              style={isPhoneViewport ? undefined : { width: leftRailWidth }}
            >
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-3 dark:border-slate-800">
                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                  Sessions
                </div>
                <button
                  type="button"
                  onClick={() => void startNewSession()}
                  title="New session in active workspace"
                  aria-label="New session in active workspace"
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                >
                  <Plus size={14} />
                </button>
              </div>
              <div className="shrink-0 border-b border-slate-200/80 p-3 dark:border-slate-800">
                <label className="flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/90 px-2.5 shadow-inner dark:border-slate-800 dark:bg-slate-900">
                  <Search size={13} className="text-slate-400" />
                  <input
                    value={threadQuery}
                    onChange={(event) => setThreadQuery(event.target.value)}
                    placeholder="Search sessions"
                    className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-slate-400"
                  />
                </label>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-3">
                <div className="space-y-1.5">
                  {sessionGroups.map((group) => {
                    const collapsed = threadQuery.trim()
                      ? false
                      : (collapsedThreadGroups[group.id] ?? group.defaultCollapsed ?? false);
                    const workspaceGroupActive = group.workspaceId === activeWorkspaceId;
                    return (
                      <div key={group.id} className="group/workspace">
                        <div
                          className={cx(
                            "flex items-start gap-1 rounded-lg transition",
                            workspaceGroupActive
                              ? "bg-slate-100/90 shadow-sm dark:bg-slate-900"
                              : "hover:bg-slate-50 dark:hover:bg-slate-900/70",
                          )}
                        >
                          <button
                            type="button"
                            onClick={() => {
                              toggleThreadGroup(group.id);
                              if (group.workspaceId) {
                                setActiveWorkspace(group.workspaceId);
                                setActivePane("work");
                              }
                            }}
                            className="flex min-w-0 flex-1 items-start gap-1.5 px-1.5 py-2 text-left"
                          >
                            {collapsed ? (
                              <ChevronRight size={14} className="mt-0.5 shrink-0 text-slate-400" />
                            ) : (
                              <ChevronDown size={14} className="mt-0.5 shrink-0 text-slate-400" />
                            )}
                            <span className="min-w-0 flex-1">
                              <span className="flex min-w-0 items-center gap-1.5">
                                {group.shortcut && (
                                  <span className="shrink-0 text-[10px] font-semibold text-slate-400">
                                    {group.shortcut}
                                  </span>
                                )}
                                <span className="block truncate text-[12px] font-semibold leading-4 text-slate-700 dark:text-slate-200">
                                  {group.name}
                                </span>
                                <span className="ml-auto shrink-0 text-[11px] text-slate-400">
                                  {group.threads.length}
                                </span>
                              </span>
                              {group.root && (
                                <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
                                  {fileName(group.root)}
                                </span>
                              )}
                            </span>
                          </button>
                          {group.workspaceId && (
                            <button
                              type="button"
                              onClick={() => removeWorkspace(group.workspaceId!)}
                              title={`Close ${group.name}`}
                              aria-label={`Close ${group.name}`}
                              className="mr-1 mt-1 grid h-5 w-5 shrink-0 place-items-center rounded text-slate-400 opacity-0 hover:bg-white hover:text-slate-700 hover:opacity-100 group-hover/workspace:opacity-100 dark:hover:bg-slate-950 dark:hover:text-slate-200"
                            >
                              <X size={12} />
                            </button>
                          )}
                        </div>
                        {!collapsed && (
                          <div className="ml-4 mt-1 space-y-1 border-l border-slate-200 pl-2 dark:border-slate-800">
                            {group.threads.map((thread) => {
                              const active = activeThread?.id === thread.id;
                              const threadRunningTask = runningTaskByThreadId.get(thread.id);
                              const threadIsRunning = Boolean(threadRunningTask);
                              const archived = Boolean(thread.archived || group.id === "archived");
                              const sessionKey = threadWorkspaceKey(thread.workflow_id, thread.id);
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
                                      "group/session relative flex w-full min-w-0 items-start gap-1 rounded-lg border transition",
                                      active
                                        ? "border-slate-900 bg-white text-slate-950 shadow-sm dark:border-slate-100 dark:bg-slate-900 dark:text-slate-100"
                                        : "border-transparent bg-slate-50 text-slate-600 hover:border-slate-200 hover:bg-white hover:shadow-sm dark:bg-slate-950/40 dark:text-slate-300 dark:hover:border-slate-800 dark:hover:bg-slate-900",
                                    )}
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
                                        void openSession(thread, group.workspaceId);
                                      }}
                                      className="flex min-w-0 flex-1 items-start gap-2 px-2.5 py-2 text-left"
                                    >
                                      <span
                                        className={cx(
                                          "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                                          threadIsRunning
                                            ? "bg-blue-500"
                                            : active
                                              ? "bg-slate-900 dark:bg-slate-100"
                                              : "bg-slate-400",
                                        )}
                                      />
                                      <span className="min-w-0 flex-1">
                                        <span className="block truncate text-[12px] font-semibold leading-4">
                                          {thread.title || "Untitled"}
                                        </span>
                                        <span className="mt-0.5 block truncate text-[10px] text-slate-400">
                                          {thread.mode === "agent" ? "Super DAN" : "Chat"} ·{" "}
                                          {thread.message_count} messages
                                          {compactThreadTime(thread.updated_at)
                                            ? ` · ${compactThreadTime(thread.updated_at)}`
                                            : ""}
                                          {threadIsRunning ? " · running" : ""}
                                        </span>
                                      </span>
                                    </button>
                                    <div className="flex shrink-0 items-center gap-0.5 py-1 pr-1">
                                      {threadIsRunning && (
                                        <button
                                          type="button"
                                          data-session-action
                                          onClick={() => void stopSessionRun(thread, threadRunningTask)}
                                          title="Stop running session"
                                          aria-label="Stop running session"
                                          className="grid h-6 w-6 place-items-center rounded-md text-slate-400 transition hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-300"
                                        >
                                          <Square size={11} />
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
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                            {group.threads.length === 0 && (
                              <div className="px-2 py-1 text-xs text-slate-400">No sessions</div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {sessionGroups.length === 0 && (
                    <div className="px-2 py-2 text-sm text-slate-400">No sessions found.</div>
                  )}
                </div>
              </div>
              <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-t border-slate-200/80 px-3 text-[11px] text-slate-400 dark:border-slate-800">
                <button
                  type="button"
                  onClick={createDevelopmentWorkspace}
                  title="New workspace"
                  className="inline-flex h-7 min-w-0 items-center gap-1 rounded-md border border-slate-200 bg-white px-2 text-slate-500 shadow-sm hover:border-slate-300 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                >
                  <FolderPlus size={12} />
                  <span className="truncate">New Workspace</span>
                </button>
                <span className="shrink-0">
                  {workspaces.length} workspaces · {threads.length} sessions
                </span>
              </div>
              {!isPhoneViewport && (
                <div
                  role="separator"
                  aria-label="Resize sessions rail"
                  onPointerDown={startLeftRailResize}
                  className="absolute -right-1 top-0 z-20 h-full w-2 cursor-col-resize bg-transparent transition hover:bg-slate-300/40 dark:hover:bg-slate-700/45"
                />
              )}
            </aside>
          )}
          {renderFileExplorer && (
            <aside className="dan-phone-page dan-files-page flex min-h-0 w-[270px] shrink-0 flex-col border-r border-slate-200/80 bg-white/85 dark:border-slate-800 dark:bg-slate-950">
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-3 dark:border-slate-800">
                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
                  Files
                </div>
                <button
                  type="button"
                  onClick={openFolder}
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                  title="Open development workspace"
                >
                  <FolderOpen size={14} />
                </button>
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
                    onSelect={(entry) => setActiveFilePath(entry.path)}
                  />
                ) : (
                  <div className="px-2 text-sm text-slate-400">No files loaded.</div>
                )}
              </div>
            </aside>
          )}

          {renderWorkMain && (
          <div
            className={cx(
              "dan-phone-page grid min-h-0 min-w-0 flex-1",
              renderSidecarPreview
                ? "grid-cols-[minmax(280px,0.95fr)_minmax(300px,1.05fr)]"
                : "grid-cols-[minmax(0,1fr)]",
            )}
          >
            <div className="flex min-h-0 min-w-0 flex-col bg-white/90 dark:bg-slate-950">
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 bg-white/80 px-4 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-semibold leading-5">Conversation Chunks</div>
                  <div className="truncate text-[11px] leading-4 text-slate-500">
                    {selectedChunk?.title || activeThread?.title || "Super DAN and narrator history"}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {activeThread && activeRunningTask && (
                    <button
                      type="button"
                      onClick={() =>
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
                        )
                      }
                      className="inline-flex h-8 items-center gap-1 rounded-lg border border-red-200 bg-red-50 px-2 text-xs font-semibold text-red-700 shadow-sm transition hover:border-red-300 hover:bg-white dark:border-red-900 dark:bg-red-950/35 dark:text-red-200"
                      title="Stop running session"
                    >
                      <Square size={11} />
                      Stop
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setShowConversationChunks((visible) => !visible)}
                    className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-slate-300 hover:text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                    title="Toggle conversation chunks"
                  >
                    <MessageSquareText size={14} />
                  </button>
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-auto p-4">
                {showConversationChunks ? (
                  chunks.length > 0 ? (
                    <div className="space-y-3">
                      {chunks.slice(-14).map((chunk) => (
                        <ChunkCard
                          key={chunk.id}
                          chunk={chunk}
                          active={selectedChunk?.id === chunk.id}
                          onSelect={() => setSelectedChunkId(chunk.id)}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-md border border-dashed border-slate-200 bg-white p-3 text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-950">
                      {loadingThreadId === activeThread?.id
                        ? "Loading session..."
                        : activeThread
                          ? "This session has no saved Super DAN history."
                          : "No Super DAN history yet."}
                    </div>
                  )
                ) : (
                  <div className="rounded-md border border-dashed border-slate-200 bg-white p-3 text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-950">
                    Conversation chunks hidden.
                  </div>
                )}
              </div>

              {showAgentQueuePanel && (
                <div className="max-h-44 shrink-0 overflow-auto border-t border-slate-200/80 bg-slate-50/90 p-3 dark:border-slate-800 dark:bg-slate-900/40">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">
                      <Activity size={13} />
                      Message Queue
                    </div>
                    <span className="text-[11px] text-slate-400">
                      {queueRows.length} {queueRows.length === 1 ? "item" : "items"}
                    </span>
                  </div>
                  <QueueList rows={queueRows} />
                </div>
              )}

              <div className="shrink-0 border-t border-slate-200/80 bg-white/95 p-3 shadow-[0_-1px_0_rgba(15,23,42,0.02)] dark:border-slate-800 dark:bg-slate-950">
                <div className="flex gap-2">
                  <div className="hidden h-11 shrink-0 rounded-lg border border-slate-200 bg-slate-100/70 p-0.5 shadow-inner dark:border-slate-800 dark:bg-slate-900 sm:flex">
                    {hasActiveRun ? (
                      (["steer", "queue"] as const).map((mode) => (
                        <button
                          key={mode}
                          type="button"
                          onClick={() => setActiveRunPlacement(mode)}
                          title={
                            mode === "queue"
                              ? "Queue this message after the current Super DAN run (Option+Enter)"
                              : "Steer the active Super DAN run now (Enter)"
                          }
                          className={cx(
                            "inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 text-xs font-semibold capitalize transition",
                            activeRunPlacement === mode
                              ? "bg-white text-slate-950 shadow-sm dark:bg-slate-100 dark:text-slate-950"
                              : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200",
                          )}
                        >
                          {mode === "queue" ? <Activity size={13} /> : <WandSparkles size={13} />}
                          {mode === "queue" ? "Next" : "Steer"}
                        </button>
                      ))
                    ) : (
                      <div
                        title="Start a Super DAN run"
                        className="inline-flex h-9 items-center gap-1.5 rounded-md bg-white px-2.5 text-xs font-semibold text-slate-950 shadow-sm dark:bg-slate-100 dark:text-slate-950"
                      >
                        <WandSparkles size={13} />
                        Super DAN
                      </div>
                    )}
                  </div>
                  <textarea
                    ref={composerRef}
                    value={input}
                    onChange={(event) => setInput(event.target.value)}
                    onKeyDown={handleComposerKeyDown}
                    placeholder={
                      selectedChunk
                        ? `Ask Super DAN about ${selectedChunk.title}`
                        : activeFileEntry
                          ? `Ask Super DAN about ${activeFileEntry.relative_path}`
                          : "Ask Super DAN to work in this workspace"
                    }
                    rows={1}
                    className="max-h-32 min-h-11 flex-1 resize-none rounded-lg border border-slate-200 bg-slate-50/90 px-3 py-2.5 text-sm leading-6 outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:bg-white focus:shadow-sm dark:border-slate-800 dark:bg-slate-900"
                  />
                  <button
                    type="button"
                    onClick={() => void submit()}
                    disabled={sending || !input.trim()}
                    className="inline-flex h-11 items-center gap-1.5 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-slate-100 dark:text-slate-950 dark:hover:bg-white"
                  >
                    {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                    {hasActiveRun
                      ? activeRunPlacement === "queue"
                        ? "Next"
                        : "Steer"
                      : "Run"}
                  </button>
                </div>
                {(activeFileEntry || selectedChunk || hasActiveRun) && (
                  <div className="mt-1 flex items-center gap-3 text-[11px] text-slate-400">
                    {activeFileEntry && (
                      <span className="truncate">File: {activeFileEntry.relative_path}</span>
                    )}
                    {selectedChunk && <span className="truncate">Chunk: {selectedChunk.title}</span>}
                    {hasActiveRun && <span className="truncate">Run: {activeRunId}</span>}
                  </div>
                )}
              </div>
            </div>

            {renderSidecarPreview && (
              <aside className="flex min-h-0 min-w-0 flex-col border-l border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-950">
                <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-4 dark:border-slate-800">
                  <div className="min-w-0">
                    <div className="truncate text-[15px] font-semibold leading-5">
                      {selectedChunk ? selectedChunk.title : activeFileEntry?.name || "Preview"}
                    </div>
                    <div className="truncate text-[11px] leading-4 text-slate-500">
                      {selectedChunk?.meta ||
                        activeFileEntry?.relative_path ||
                        "Select a chunk or file"}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={openActiveFile}
                    disabled={!activeFileEntry}
                    className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300 dark:hover:bg-slate-900"
                  >
                    Open
                  </button>
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-4">
                  {selectedChunk ? (
                    <MarkdownRenderer content={selectedChunk.body || "_Waiting for output._"} />
                  ) : activeFileStatus === "loading" ? (
                    <div className="flex items-center gap-2 text-sm text-slate-400">
                      <Loader2 size={14} className="animate-spin" />
                      Loading file
                    </div>
                  ) : activeFileEntry ? (
                    <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5 text-slate-700 dark:text-slate-300">
                      {activeFileContent || "_empty file_"}
                    </pre>
                  ) : (
                    <div className="text-sm text-slate-400">Select a development file.</div>
                  )}
                </div>
                {activeFileEntry && !selectedChunk && (
                  <div className="border-t border-slate-200 px-3 py-2 text-[11px] text-slate-400 dark:border-slate-800">
                    {compactFileSize(activeFileEntry.size)}
                    {activeFileStatus === "error" ? " · preview unavailable" : ""}
                  </div>
                )}
              </aside>
            )}
          </div>
          )}
          {!renderWorkMain && renderSidecarPreview && (
            <aside className="dan-phone-page flex min-h-0 min-w-0 flex-1 flex-col border-l border-slate-200/80 bg-white dark:border-slate-800 dark:bg-slate-950">
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200/80 px-4 dark:border-slate-800">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-semibold leading-5">
                    {selectedChunk ? selectedChunk.title : activeFileEntry?.name || "Preview"}
                  </div>
                  <div className="truncate text-[11px] leading-4 text-slate-500">
                    {selectedChunk?.meta || activeFileEntry?.relative_path || "Select a chunk or file"}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={openActiveFile}
                  disabled={!activeFileEntry}
                  className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300 dark:hover:bg-slate-900"
                >
                  Open
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-4">
                {selectedChunk ? (
                  <MarkdownRenderer content={selectedChunk.body || "_Waiting for output._"} />
                ) : activeFileStatus === "loading" ? (
                  <div className="flex items-center gap-2 text-sm text-slate-400">
                    <Loader2 size={14} className="animate-spin" />
                    Loading file
                  </div>
                ) : activeFileEntry ? (
                  <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5 text-slate-700 dark:text-slate-300">
                    {activeFileContent || "_empty file_"}
                  </pre>
                ) : (
                  <div className="text-sm text-slate-400">Select a chunk or development file.</div>
                )}
              </div>
            </aside>
          )}
        </section>
      )}
      <nav className="dan-phone-nav hidden shrink-0 border-t border-slate-200/80 bg-white/95 px-2 py-1.5 shadow-[0_-8px_24px_rgba(15,23,42,0.06)] backdrop-blur dark:border-slate-800 dark:bg-slate-950/95">
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
