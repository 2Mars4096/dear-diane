import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import {
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  BookOpenText,
  ExternalLink,
  FileText,
  Folder,
  FolderSearch,
  Loader2,
  MessageSquareText,
  PencilLine,
  Play,
  RefreshCcw,
  Sparkles,
  Square,
} from "lucide-react";
import ModeChatSidebar from "../shared/ModeChatSidebar";
import { useModeScopedWindowEvent } from "../../hooks/useModeScopedWindowEvent";
import {
  nativeContentBootstrap,
  nativeContentPreview,
  nativeDialog,
  nativeFs,
  nativeShell,
  nativeWatch,
  isElectron,
} from "../../lib/electronBridge";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";
import { useSettingsStore } from "../../store/useSettingsStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import { useContentStore } from "../../store/useContentStore";
import { buildContentModeChatContext } from "./contentModeChatContext";
import {
  discoverContentProjects,
  inspectContentProjectRoot,
  listContentPages,
} from "./contentModeDiscovery";
import {
  buildManagedPreviewUrl,
  buildContentFolderTree,
  buildContentPageSummary,
  type ContentFolderTreeNode,
  type ContentPageSummary,
  type ContentProjectCandidate,
  extractFrontmatterSummary,
  getContentFolderAncestorPaths,
  resolvePreviewPath,
  summarizeHeadings,
} from "./contentModeModel";
import {
  validateContentDraft,
  type ContentValidationIssue,
} from "./contentModeValidation";
import {
  applyPreviewEditTarget,
  findPreviewEditTarget,
  type PreviewEditableSelection,
  type PreviewEditTarget,
} from "./contentModePreviewEditing";

const AUTOSAVE_DELAY_MS = 900;
const SELF_WRITE_GRACE_MS = 1_500;

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function relativeFileLabel(projectRoot: string | null, pagePath: string | null) {
  if (!projectRoot || !pagePath) return null;
  const normalizedRoot = projectRoot.replace(/\\/g, "/").replace(/\/+$/, "");
  const normalizedFile = pagePath.replace(/\\/g, "/");
  if (normalizedFile.startsWith(`${normalizedRoot}/`)) {
    return normalizedFile.slice(normalizedRoot.length + 1);
  }
  return normalizedFile;
}

function joinProjectPath(base: string, child: string) {
  const separator = base.includes("\\") ? "\\" : "/";
  return `${base.replace(/[\\/]+$/, "")}${separator}${child}`;
}

function buildDiagnosticTone(issue: ContentValidationIssue) {
  if (issue.severity === "error") {
    return "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-100";
  }
  return "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100";
}

function previewContentSnippet(content: string, maxLines = 12) {
  return content.split(/\r?\n/).slice(0, maxLines).join("\n").trim() || "—";
}

function previewEditTargetLabel(target: PreviewEditTarget | null) {
  if (!target) return "Rendered block";
  if (target.targetType === "frontmatter-field") return "Frontmatter title";

  switch (target.kind) {
    case "heading":
      return "Heading";
    case "list_item":
      return "List item";
    case "blockquote":
      return "Quote";
    case "paragraph":
    default:
      return "Paragraph";
  }
}

export default function ContentMode() {
  const monacoTheme = useSettingsStore((state) =>
    resolveMonacoTheme(state.theme),
  );
  const workspace = useWorkspaceStore((state) => state.getActiveWorkspace());
  const updateWorkspace = useWorkspaceStore((state) => state.updateWorkspace);

  const activeProjectRoot = useContentStore((state) => state.activeProjectRoot);
  const setActiveProjectRoot = useContentStore(
    (state) => state.setActiveProjectRoot,
  );
  const activePagePath = useContentStore((state) => state.activePagePath);
  const setActivePagePath = useContentStore((state) => state.setActivePagePath);
  const draftsByPath = useContentStore((state) => state.draftsByPath);
  const setDraftForPath = useContentStore((state) => state.setDraftForPath);
  const clearDraftForPath = useContentStore((state) => state.clearDraftForPath);

  const [showChatSidebar, setShowChatSidebar] = useState(false);
  const [projectCandidates, setProjectCandidates] = useState<
    ContentProjectCandidate[]
  >([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [pagesLoading, setPagesLoading] = useState(false);
  const [pagesError, setPagesError] = useState<string | null>(null);
  const [pages, setPages] = useState<ContentPageSummary[]>([]);
  const [pageQuery, setPageQuery] = useState("");
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>(
    {},
  );
  const [loadedContents, setLoadedContents] = useState<Record<string, string>>(
    {},
  );
  const [loadingPagePath, setLoadingPagePath] = useState<string | null>(null);
  const [previewFrameVersion, setPreviewFrameVersion] = useState(0);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewGuestIssue, setPreviewGuestIssue] = useState<string | null>(null);
  const [previewStatus, setPreviewStatus] = useState<Awaited<
    ReturnType<typeof nativeContentPreview.getStatus>
  > | null>(null);
  const [previewInspectMode, setPreviewInspectMode] = useState(false);
  const [previewEditTarget, setPreviewEditTarget] =
    useState<PreviewEditTarget | null>(null);
  const [previewEditValue, setPreviewEditValue] = useState("");
  const [previewEditError, setPreviewEditError] = useState<string | null>(null);
  const [previewEditSaving, setPreviewEditSaving] = useState(false);
  const [saveState, setSaveState] = useState<
    "idle" | "queued" | "saving" | "saved" | "error" | "conflict"
  >("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [externalConflict, setExternalConflict] = useState<{
    filePath: string;
    diskContent: string;
    lastKnownContent: string;
    detectedAt: number;
    showDiskSnapshot: boolean;
  } | null>(null);

  const activePagePathRef = useRef<string | null>(null);
  const activeDraftRef = useRef("");
  const activeLoadedContentRef = useRef("");
  const activeProjectRootRef = useRef<string | null>(null);
  const lastEditedDraftRef = useRef<string | null>(null);
  const attemptedWorkspaceBootstrapRef = useRef<Record<string, boolean>>({});
  const saveInFlightRef = useRef(false);
  const lastSelfWriteAtByPathRef = useRef<Record<string, number>>({});
  const previewSurfaceRef = useRef<HTMLDivElement | null>(null);
  const previewIframeRef = useRef<HTMLIFrameElement | null>(null);
  const previewGuestReadyRef = useRef(false);
  const previewSelectionSourceRef = useRef("");

  const electron = isElectron();
  const workspacePaths = workspace?.pinnedPaths ?? [];

  const syncPreviewSurfaceSize = useCallback(() => {
    const surface = previewSurfaceRef.current;
    if (!surface) return;

    const rect = surface.getBoundingClientRect();
    const width = Math.max(0, Math.round(rect.width));
    const height = Math.max(0, Math.round(rect.height));
    if (width === 0 || height === 0) return;

    const iframe = previewIframeRef.current;
    if (iframe) {
      iframe.style.width = `${width}px`;
      iframe.style.height = `${height}px`;
      iframe.width = `${width}`;
      iframe.height = `${height}`;
    }
  }, []);

  const tryBootstrapProject = useCallback(async () => {
    if (!electron || !workspace) return false;

    const roots = await nativeContentBootstrap.getRoots();
    if (roots.length === 0) return false;

    for (const root of roots) {
      const candidate = await inspectContentProjectRoot(root, "pinned");
      if (!candidate) continue;

      const nextPinnedPaths = workspace.pinnedPaths.includes(candidate.rootPath)
        ? workspace.pinnedPaths
        : [candidate.rootPath, ...workspace.pinnedPaths];
      const shouldRenameWorkspace =
        workspace.name === "Content Project" ||
        /^Workspace \d+$/.test(workspace.name);

      updateWorkspace(workspace.id, {
        pinnedPaths: nextPinnedPaths,
        ...(shouldRenameWorkspace ? { name: candidate.name } : {}),
      });
      setProjectCandidates((state) => {
        const withoutCandidate = state.filter(
          (entry) => entry.rootPath !== candidate.rootPath,
        );
        return [candidate, ...withoutCandidate];
      });
      setActiveProjectRoot(candidate.rootPath);
      setActivePagePath(null);
      setProjectError(null);
      return true;
    }

    return false;
  }, [
    electron,
    setActivePagePath,
    setActiveProjectRoot,
    updateWorkspace,
    workspace,
  ]);

  useEffect(() => {
    if (!electron || !workspace || workspacePaths.length > 0) return;
    if (attemptedWorkspaceBootstrapRef.current[workspace.id]) return;

    attemptedWorkspaceBootstrapRef.current[workspace.id] = true;
    let cancelled = false;

    void tryBootstrapProject().then(() => {
      if (cancelled) return;
    });

    return () => {
      cancelled = true;
    };
  }, [electron, tryBootstrapProject, workspace, workspacePaths.length]);

  const refreshProjects = useCallback(async () => {
    if (!electron) {
      setProjectCandidates([]);
      setProjectError(
        "Content project discovery requires the desktop app because local file access is not available in browser preview.",
      );
      return;
    }
    setProjectsLoading(true);
    setProjectError(null);
    try {
      const next = await discoverContentProjects(workspacePaths);
      setProjectCandidates(next);
      if (next.length === 0) {
        const bootstrapped = await tryBootstrapProject();
        if (bootstrapped) return;
        setProjectError(
          workspacePaths.length === 0
            ? "Pin or attach a local knowledge-base folder to start Content mode."
            : "No Hugo/content project was found under the current workspace paths.",
        );
      }
    } catch (error) {
      setProjectError(
        error instanceof Error
          ? error.message
          : "Failed to discover content projects.",
      );
    } finally {
      setProjectsLoading(false);
    }
  }, [electron, tryBootstrapProject, workspacePaths]);

  const refreshPages = useCallback(async () => {
    if (!activeProjectRoot) {
      setPages([]);
      setPagesError(null);
      return;
    }
    setPagesLoading(true);
    setPagesError(null);
    try {
      const next = await listContentPages(activeProjectRoot);
      setPages(next);
      if (next.length === 0) {
        setPagesError(
          "The selected project has no editable Markdown pages under content/ yet.",
        );
      }
    } catch (error) {
      setPages([]);
      setPagesError(
        error instanceof Error ? error.message : "Failed to load content pages.",
      );
    } finally {
      setPagesLoading(false);
    }
  }, [activeProjectRoot]);

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  useEffect(() => {
    if (!projectCandidates.length) return;
    if (
      activeProjectRoot &&
      projectCandidates.some((candidate) => candidate.rootPath === activeProjectRoot)
    ) {
      return;
    }
    setActiveProjectRoot(projectCandidates[0].rootPath);
  }, [activeProjectRoot, projectCandidates, setActiveProjectRoot]);

  useEffect(() => {
    void refreshPages();
  }, [refreshPages]);

  useEffect(() => {
    if (pages.length === 0) {
      if (activePagePath) setActivePagePath(null);
      return;
    }
    if (activePagePath && pages.some((page) => page.filePath === activePagePath)) {
      return;
    }
    setActivePagePath(pages[0].filePath);
  }, [activePagePath, pages, setActivePagePath]);

  useEffect(() => {
    if (!electron || !activePagePath || loadedContents[activePagePath] != null) return;
    let cancelled = false;
    setLoadingPagePath(activePagePath);
    void nativeFs.readFile(activePagePath).then((content) => {
      if (cancelled) return;
      setLoadingPagePath(null);
      if (content === null) return;
      setLoadedContents((state) => ({
        ...state,
        [activePagePath]: content,
      }));
    });
    return () => {
      cancelled = true;
    };
  }, [activePagePath, electron, loadedContents]);

  const toggleChatSidebar = useCallback(() => {
    setShowChatSidebar((value) => !value);
  }, []);
  const closeChatSidebar = useCallback(() => {
    setShowChatSidebar(false);
  }, []);

  useModeScopedWindowEvent("content", "app:toggleModeChatSidebar", toggleChatSidebar);

  const activePage = useMemo(
    () => pages.find((page) => page.filePath === activePagePath) ?? null,
    [activePagePath, pages],
  );

  const activeLoadedContent = activePagePath
    ? loadedContents[activePagePath] ?? ""
    : "";
  const activeDraft = activePagePath
    ? draftsByPath[activePagePath] ?? activeLoadedContent
    : "";
  const isDirty =
    !!activePagePath &&
    draftsByPath[activePagePath] != null &&
    draftsByPath[activePagePath] !== activeLoadedContent;

  const activeFrontmatter = useMemo(
    () => extractFrontmatterSummary(activeDraft),
    [activeDraft],
  );
  const activeHeadings = useMemo(
    () => summarizeHeadings(activeDraft).slice(0, 24),
    [activeDraft],
  );
  const activeConflict =
    activePagePath && externalConflict?.filePath === activePagePath
      ? externalConflict
      : null;
  const validationIssues = useMemo(
    () =>
      validateContentDraft({
        filePath: activePagePath,
        relPath: activePage?.relPath ?? null,
        content: activeDraft,
        knownPages: pages.map((page) => ({
          filePath: page.filePath,
          pageId: page.pageId,
        })),
      }),
    [activeDraft, activePage?.relPath, activePagePath, pages],
  );
  const blockingValidationIssues = useMemo(
    () => validationIssues.filter((issue) => issue.severity === "error"),
    [validationIssues],
  );
  const previewIsStale =
    isDirty ||
    saveState === "error" ||
    saveState === "conflict" ||
    (blockingValidationIssues.length > 0 && isDirty) ||
    !!activeConflict;

  const syncPageSummary = useCallback(
    (projectRoot: string | null, targetPath: string, content: string) => {
      if (!projectRoot) return;
      const contentRoot = joinProjectPath(projectRoot, "content");
      setPages((state) =>
        state.map((page) =>
          page.filePath === targetPath
            ? buildContentPageSummary(contentRoot, targetPath, content)
            : page,
        ),
      );
    },
    [],
  );
  const bumpPreviewFrame = useCallback(() => {
    window.setTimeout(() => {
      setPreviewFrameVersion((value) => value + 1);
    }, 250);
  }, []);

  useEffect(() => {
    activePagePathRef.current = activePagePath;
    activeDraftRef.current = activeDraft;
    activeLoadedContentRef.current = activeLoadedContent;
    activeProjectRootRef.current = activeProjectRoot;
  }, [activeDraft, activeLoadedContent, activePagePath, activeProjectRoot]);

  useEffect(() => {
    if (isDirty && saveState === "saved") {
      setSaveState("idle");
    }
  }, [isDirty, saveState]);

  useEffect(() => {
    if (lastEditedDraftRef.current === null) {
      lastEditedDraftRef.current = activeDraft;
      return;
    }
    if (lastEditedDraftRef.current !== activeDraft && saveError && !activeConflict) {
      setSaveError(null);
    }
    lastEditedDraftRef.current = activeDraft;
  }, [activeConflict, activeDraft, saveError]);

  useEffect(() => {
    setSaveState("idle");
    setSaveError(null);
  }, [activePagePath]);

  const filteredPages = useMemo(() => {
    const query = pageQuery.trim().toLowerCase();
    if (!query) return pages;
    return pages.filter((page) =>
      [
        page.title,
        page.section,
        page.slug,
        page.pageId ?? "",
        page.relPath,
      ].some((field) => field.toLowerCase().includes(query)),
    );
  }, [pageQuery, pages]);
  const pageTree = useMemo(
    () => buildContentFolderTree(filteredPages),
    [filteredPages],
  );
  const hasPageQuery = pageQuery.trim().length > 0;
  const activeChatContext = useCallback(
    () =>
      buildContentModeChatContext({
        workspacePaths,
        activeProjectRoot,
        activePage,
        activeDraft,
        isDirty,
      }),
    [workspacePaths, activeProjectRoot, activePage, activeDraft, isDirty],
  );

  const handleAttachProject = useCallback(async () => {
    if (!electron || !workspace) return;
    const selectedDir = await nativeDialog.openDirectory();
    if (!selectedDir) return;
    const inspected = await inspectContentProjectRoot(selectedDir, "manual");
    if (!inspected) {
      setProjectError(
        "The selected folder does not look like a Hugo content project yet. Expected a content/ directory.",
      );
      return;
    }
    const nextPinnedPaths = workspace.pinnedPaths.includes(selectedDir)
      ? workspace.pinnedPaths
      : [...workspace.pinnedPaths, selectedDir];
    updateWorkspace(workspace.id, { pinnedPaths: nextPinnedPaths });
    setProjectCandidates((state) => {
      const withoutSelected = state.filter(
        (candidate) => candidate.rootPath !== inspected.rootPath,
      );
      return [inspected, ...withoutSelected];
    });
    setActiveProjectRoot(selectedDir);
    setActivePagePath(null);
    setProjectError(null);
  }, [
    electron,
    setActivePagePath,
    setActiveProjectRoot,
    updateWorkspace,
    workspace,
  ]);

  useEffect(() => {
    if (!activePage?.relPath) return;
    const ancestors = getContentFolderAncestorPaths(activePage.relPath);
    if (ancestors.length === 0) return;
    setExpandedFolders((state) => {
      let changed = false;
      const next = { ...state };
      for (const path of ancestors) {
        if (next[path] !== true) {
          next[path] = true;
          changed = true;
        }
      }
      return changed ? next : state;
    });
  }, [activePage?.relPath]);

  const toggleFolderExpanded = useCallback(
    (folderPath: string, currentlyExpanded: boolean) => {
    setExpandedFolders((state) => ({
      ...state,
      [folderPath]: !currentlyExpanded,
    }));
    },
    [],
  );

  const handleEditorChange = useCallback(
    (value: string | undefined) => {
      if (!activePagePath) return;
      const nextValue = value ?? "";
      if (nextValue === activeLoadedContent) {
        clearDraftForPath(activePagePath);
        return;
      }
      setDraftForPath(activePagePath, nextValue);
    },
    [
      activeLoadedContent,
      activePagePath,
      clearDraftForPath,
      setDraftForPath,
    ],
  );

  const activeProjectCandidate = useMemo(
    () =>
      projectCandidates.find(
        (candidate) => candidate.rootPath === activeProjectRoot,
      ) ?? null,
    [activeProjectRoot, projectCandidates],
  );

  const activePreviewPath = useMemo(
    () =>
      activePage
        ? resolvePreviewPath(activePage.relPath, activeFrontmatter)
        : null,
    [activeFrontmatter, activePage],
  );

  const activeExternalPreviewUrl = useMemo(
    () =>
      buildManagedPreviewUrl(previewStatus?.baseUrl ?? null, activePreviewPath),
    [activePreviewPath, previewStatus?.baseUrl],
  );
  const activePreviewUrl = useMemo(
    () =>
      buildManagedPreviewUrl(
        previewStatus?.baseUrl ?? null,
        activePreviewPath,
        { embedded: true },
      ),
    [activePreviewPath, previewStatus?.baseUrl],
  );
  const previewCanEditOnPage =
    electron &&
    Boolean(activePreviewUrl) &&
    Boolean(previewStatus?.ready) &&
    !previewIsStale;

  const applyDraftContent = useCallback(
    (targetPath: string, nextContent: string) => {
      const loadedContent = loadedContents[targetPath] ?? "";
      if (nextContent === loadedContent) {
        clearDraftForPath(targetPath);
        return;
      }
      setDraftForPath(targetPath, nextContent);
    },
    [clearDraftForPath, loadedContents, setDraftForPath],
  );

  const sendPreviewGuestMessage = useCallback(
    (channel: string, ...args: unknown[]) => {
      if (!electron) return false;
      const iframe = previewIframeRef.current;
      if (!iframe?.contentWindow || !previewGuestReadyRef.current) return false;
      try {
        iframe.contentWindow.postMessage(
          {
            source: "dan-preview-host",
            channel,
            args,
          },
          "*",
        );
        return true;
      } catch (error) {
        previewGuestReadyRef.current = false;
        throw error;
      }
    },
    [electron],
  );

  const clearPreviewEditSelection = useCallback(() => {
    setPreviewEditTarget(null);
    setPreviewEditValue("");
    setPreviewEditError(null);
    previewSelectionSourceRef.current = "";
    sendPreviewGuestMessage("dan-preview:clear-selection");
  }, [sendPreviewGuestMessage]);

  const handlePreviewInspectToggle = useCallback(() => {
    if (previewInspectMode) {
      clearPreviewEditSelection();
      setPreviewInspectMode(false);
      return;
    }
    if (!previewCanEditOnPage) return;
    setPreviewEditError(null);
    setPreviewInspectMode(true);
  }, [
    clearPreviewEditSelection,
    previewCanEditOnPage,
    previewInspectMode,
  ]);

  const handlePreviewGuestSelection = useCallback(
    (selection: PreviewEditableSelection) => {
      const sourceContent = activeLoadedContentRef.current;
      if (!sourceContent) {
        setPreviewEditError("Load the page from disk before editing it on the preview surface.");
        return;
      }

      const target = findPreviewEditTarget(sourceContent, selection);
      if (!target) {
        setPreviewEditTarget(null);
        setPreviewEditValue("");
        setPreviewEditError(
          "DAN could not map that rendered block back to Markdown yet. Use the source editor for this one.",
        );
        return;
      }

      previewSelectionSourceRef.current = sourceContent;
      setPreviewEditTarget(target);
      setPreviewEditValue(target.text);
      setPreviewEditError(null);
    },
    [],
  );

  useEffect(() => {
    if (!electron || !activeProjectRoot) {
      setPreviewStatus(null);
      setPreviewError(null);
      return;
    }

    let cancelled = false;
    const syncStatus = async () => {
      const status = await nativeContentPreview.getStatus(activeProjectRoot);
      if (cancelled) return;
      setPreviewStatus(status);
      setPreviewError(status.lastError);
    };

    void syncStatus();
    const interval = window.setInterval(() => {
      void syncStatus();
    }, 2_500);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeProjectRoot, electron]);

  useEffect(() => {
    if (
      !electron ||
      !activeProjectRoot ||
      !activeProjectCandidate?.hasHugoConfig
    ) {
      return;
    }

    let cancelled = false;
    setPreviewBusy(true);
    void nativeContentPreview
      .start(activeProjectRoot)
      .then((status) => {
        if (cancelled) return;
        setPreviewStatus(status);
        setPreviewError(status.lastError);
      })
      .finally(() => {
        if (!cancelled) setPreviewBusy(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeProjectCandidate?.hasHugoConfig, activeProjectRoot, electron]);

  useEffect(() => {
    clearPreviewEditSelection();
    setPreviewInspectMode(false);
    setPreviewGuestIssue(null);
    previewSelectionSourceRef.current = "";
    previewGuestReadyRef.current = false;
  }, [activePagePath, activePreviewUrl, clearPreviewEditSelection]);

  useEffect(() => {
    if (!previewInspectMode || !previewIsStale) return;
    clearPreviewEditSelection();
    setPreviewInspectMode(false);
  }, [clearPreviewEditSelection, previewInspectMode, previewIsStale]);

  useEffect(() => {
    if (!activePreviewUrl) return;

    const surface = previewSurfaceRef.current;
    if (!surface) return;

    let frameId = 0;
    const scheduleSync = () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = 0;
        syncPreviewSurfaceSize();
      });
    };

    scheduleSync();
    window.setTimeout(scheduleSync, 0);

    const observer =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => scheduleSync())
        : null;
    observer?.observe(surface);
    window.addEventListener("resize", scheduleSync);

    return () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      observer?.disconnect();
      window.removeEventListener("resize", scheduleSync);
    };
  }, [activePreviewUrl, previewFrameVersion, syncPreviewSurfaceSize]);

  useEffect(() => {
    previewGuestReadyRef.current = false;
  }, [activePreviewUrl, previewFrameVersion]);

  useEffect(() => {
    const iframe = previewIframeRef.current;
    if (!iframe || !activePreviewUrl) {
      return;
    }

    const handleLoad = () => {
      previewGuestReadyRef.current = true;
      setPreviewGuestIssue(null);
      syncPreviewSurfaceSize();
      sendPreviewGuestMessage("dan-preview:set-mode", {
        inspectMode: previewInspectMode && previewCanEditOnPage,
      });
      if (!previewInspectMode) {
        sendPreviewGuestMessage("dan-preview:clear-selection");
      }
    };

    iframe.addEventListener("load", handleLoad);
    return () => {
      if (previewIframeRef.current === iframe) {
        previewGuestReadyRef.current = false;
      }
      iframe.removeEventListener("load", handleLoad);
    };
  }, [
    activePreviewUrl,
    previewCanEditOnPage,
    previewFrameVersion,
    previewInspectMode,
    sendPreviewGuestMessage,
    syncPreviewSurfaceSize,
  ]);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      const frameWindow = previewIframeRef.current?.contentWindow;
      if (!frameWindow || event.source !== frameWindow) {
        return;
      }

      const payload = event.data;
      if (
        !payload ||
        typeof payload !== "object" ||
        !("source" in payload) ||
        (payload as { source?: unknown }).source !== "dan-preview-guest"
      ) {
        return;
      }

      const channel =
        "channel" in payload && typeof payload.channel === "string"
          ? payload.channel
          : null;
      if (!channel) return;

      if (channel === "dan-preview-selection") {
        const selection =
          "payload" in payload &&
          payload.payload &&
          typeof payload.payload === "object"
            ? (payload.payload as PreviewEditableSelection)
            : null;
        if (selection) {
          handlePreviewGuestSelection(selection);
        }
        return;
      }

      if (channel === "dan-preview-selection-cleared") {
        setPreviewEditTarget(null);
        setPreviewEditValue("");
        setPreviewEditError(null);
        return;
      }

      if (channel === "dan-preview-runtime-issue") {
        const message =
          "payload" in payload &&
          payload.payload &&
          typeof payload.payload === "object" &&
          "message" in payload.payload &&
          typeof payload.payload.message === "string"
            ? payload.payload.message
            : null;
        if (message) {
          setPreviewGuestIssue(message);
        }
      }
    };

    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [handlePreviewGuestSelection]);

  useEffect(() => {
    if (!sendPreviewGuestMessage(
      "dan-preview:set-mode",
      {
      inspectMode: previewInspectMode && previewCanEditOnPage,
      },
    )) {
      return;
    }
    if (!previewInspectMode) {
      sendPreviewGuestMessage("dan-preview:clear-selection");
    }
  }, [
    previewCanEditOnPage,
    previewFrameVersion,
    previewInspectMode,
    sendPreviewGuestMessage,
  ]);

  useEffect(() => {
    if (!electron || !activePagePath) return;
    void nativeWatch.start(activePagePath);
    return () => {
      void nativeWatch.stop(activePagePath);
    };
  }, [activePagePath, electron]);

  useEffect(() => {
    if (!electron) return;
    return nativeWatch.onChange((filePath: string) => {
      const currentPath = activePagePathRef.current;
      if (!currentPath || filePath !== currentPath) return;

      const lastSelfWriteAt = lastSelfWriteAtByPathRef.current[filePath] ?? 0;
      if (Date.now() - lastSelfWriteAt < SELF_WRITE_GRACE_MS) return;

      void nativeFs.readFile(filePath).then((diskContent) => {
        if (diskContent == null) return;
        if (activePagePathRef.current !== filePath) return;

        const loadedContent = activeLoadedContentRef.current;
        const draftContent = activeDraftRef.current;
        const projectRoot = activeProjectRootRef.current;

        if (diskContent === loadedContent) return;

        if (draftContent !== loadedContent && diskContent !== draftContent) {
          setExternalConflict({
            filePath,
            diskContent,
            lastKnownContent: loadedContent,
            detectedAt: Date.now(),
            showDiskSnapshot: false,
          });
          setSaveState("conflict");
          setSaveError(
            "This page changed on disk while you still had local edits. Reload the disk version or keep your draft before saving again.",
          );
          return;
        }

        setLoadedContents((state) => ({
          ...state,
          [filePath]: diskContent,
        }));
        syncPageSummary(projectRoot, filePath, diskContent);

        if (diskContent === draftContent) {
          clearDraftForPath(filePath);
        }

        setExternalConflict((state) =>
          state?.filePath === filePath ? null : state,
        );
        setSaveState("saved");
        setSaveError(null);

        if (previewStatus?.ready) {
          bumpPreviewFrame();
        }
      });
    });
  }, [bumpPreviewFrame, clearDraftForPath, electron, previewStatus?.ready, syncPageSummary]);

  const handleSaveToDisk = useCallback(
    async (
      source: "manual" | "autosave" = "manual",
      contentOverride?: string,
    ) => {
      if (!electron || !activePagePath) return false;
      if (saveInFlightRef.current) {
        if (source === "autosave") setSaveState("queued");
        return false;
      }
      if (activeConflict) {
        setSaveState("conflict");
        setSaveError(
          "Resolve the external file change before saving this page again.",
        );
        return false;
      }
      const nextContent = contentOverride ?? activeDraft;
      const nextValidationIssues = validateContentDraft({
        filePath: activePagePath,
        relPath: activePage?.relPath ?? null,
        content: nextContent,
        knownPages: pages.map((page) => ({
          filePath: page.filePath,
          pageId: page.pageId,
        })),
      }).filter((issue) => issue.severity === "error");

      if (nextValidationIssues.length > 0) {
        setSaveState("error");
        setSaveError(
          nextValidationIssues[0]?.message ??
            "Resolve validation errors before saving.",
        );
        return false;
      }

      const targetPath = activePagePath;
      saveInFlightRef.current = true;
      setSaveState("saving");
      setSaveError(null);
      lastSelfWriteAtByPathRef.current[targetPath] = Date.now();

      try {
        const saved = await nativeFs.writeFile(targetPath, nextContent);
        if (!saved) {
          throw new Error("Saving requires the Electron desktop runtime.");
        }

        lastSelfWriteAtByPathRef.current[targetPath] = Date.now();
        setLoadedContents((state) => ({
          ...state,
          [targetPath]: nextContent,
        }));
        clearDraftForPath(targetPath);
        syncPageSummary(activeProjectRoot, targetPath, nextContent);
        setExternalConflict((state) =>
          state?.filePath === targetPath ? null : state,
        );

        let nextPreviewStatus = previewStatus;
        if (activeProjectRoot && activeProjectCandidate?.hasHugoConfig) {
          nextPreviewStatus = await nativeContentPreview.start(activeProjectRoot);
          setPreviewStatus(nextPreviewStatus);
          setPreviewError(nextPreviewStatus.lastError);
        }

        setSaveState("saved");
        if (nextPreviewStatus?.ready) {
          bumpPreviewFrame();
        }
        return true;
      } catch (error) {
        setSaveState("error");
        setSaveError(
          error instanceof Error
            ? error.message
            : "Failed to save the current page.",
        );
        return false;
      } finally {
        saveInFlightRef.current = false;
      }
    },
    [
      activeConflict,
      activeDraft,
      activePage?.relPath,
      activePagePath,
      activeProjectCandidate?.hasHugoConfig,
      activeProjectRoot,
      bumpPreviewFrame,
      clearDraftForPath,
      electron,
      pages,
      previewStatus,
      syncPageSummary,
    ],
  );

  const handleApplyPreviewEdit = useCallback(async () => {
    if (!activePagePath || !previewEditTarget) return;

    const sourceSnapshot = previewSelectionSourceRef.current;
    const currentSource = activeLoadedContentRef.current;
    if (!sourceSnapshot || currentSource !== sourceSnapshot) {
      setPreviewEditError(
        "The rendered page changed since you picked this block. Click it again before applying the edit.",
      );
      return;
    }

    const nextContent = applyPreviewEditTarget(
      currentSource,
      previewEditTarget,
      previewEditValue,
    );

    setPreviewEditSaving(true);
    setPreviewEditError(null);
    applyDraftContent(activePagePath, nextContent);
    const saved = await handleSaveToDisk("manual", nextContent);
    setPreviewEditSaving(false);

    if (saved) {
      clearPreviewEditSelection();
      previewSelectionSourceRef.current = nextContent;
    } else {
      setPreviewInspectMode(false);
    }
  }, [
    activePagePath,
    applyDraftContent,
    clearPreviewEditSelection,
    handleSaveToDisk,
    previewEditTarget,
    previewEditValue,
  ]);

  useEffect(() => {
    if (!electron || !activePagePath || !isDirty) return;
    if (blockingValidationIssues.length > 0 || activeConflict) return;

    setSaveState((state) => (state === "saving" ? state : "queued"));
    const timer = window.setTimeout(() => {
      void handleSaveToDisk("autosave");
    }, AUTOSAVE_DELAY_MS);

    return () => {
      window.clearTimeout(timer);
    };
  }, [
    activeConflict,
    activeDraft,
    activePagePath,
    blockingValidationIssues.length,
    electron,
    handleSaveToDisk,
    isDirty,
  ]);

  const handleReloadDiskVersion = useCallback(() => {
    if (!activeConflict) return;
    const { filePath, diskContent } = activeConflict;

    setLoadedContents((state) => ({
      ...state,
      [filePath]: diskContent,
    }));
    clearDraftForPath(filePath);
    syncPageSummary(activeProjectRootRef.current, filePath, diskContent);
    setExternalConflict(null);
    setSaveState("saved");
    setSaveError(null);

    if (previewStatus?.ready) {
      bumpPreviewFrame();
    }
  }, [activeConflict, bumpPreviewFrame, clearDraftForPath, previewStatus?.ready, syncPageSummary]);

  const handleKeepDraft = useCallback(() => {
    if (!activeConflict) return;
    const { filePath, diskContent } = activeConflict;
    const draftContent = activeDraftRef.current;

    setLoadedContents((state) => ({
      ...state,
      [filePath]: diskContent,
    }));
    syncPageSummary(activeProjectRootRef.current, filePath, diskContent);

    if (draftContent === diskContent) {
      clearDraftForPath(filePath);
      setSaveState("saved");
    } else {
      setDraftForPath(filePath, draftContent);
      setSaveState("queued");
    }

    setExternalConflict(null);
    setSaveError(null);
  }, [activeConflict, clearDraftForPath, setDraftForPath, syncPageSummary]);

  const handleToggleConflictSnapshot = useCallback(() => {
    if (!activeConflict) return;
    setExternalConflict((state) =>
      state && state.filePath === activeConflict.filePath
        ? {
            ...state,
            showDiskSnapshot: !state.showDiskSnapshot,
          }
        : state,
    );
  }, [activeConflict]);

  const handlePreviewStart = useCallback(async () => {
    if (!electron || !activeProjectRoot) return;
    setPreviewBusy(true);
    const status = await nativeContentPreview.start(activeProjectRoot);
    setPreviewBusy(false);
    setPreviewStatus(status);
    setPreviewError(status.lastError);
    if (status.ready) {
      setPreviewFrameVersion((value) => value + 1);
    }
  }, [activeProjectRoot, electron]);

  const handlePreviewRestart = useCallback(async () => {
    if (!electron || !activeProjectRoot) return;
    setPreviewBusy(true);
    const status = await nativeContentPreview.restart(activeProjectRoot);
    setPreviewBusy(false);
    setPreviewStatus(status);
    setPreviewError(status.lastError);
    if (status.ready) {
      setPreviewFrameVersion((value) => value + 1);
    }
  }, [activeProjectRoot, electron]);

  const handlePreviewStop = useCallback(async () => {
    if (!electron || !activeProjectRoot) return;
    const status = await nativeContentPreview.stop(activeProjectRoot);
    setPreviewStatus(status);
    setPreviewError(status.lastError);
  }, [activeProjectRoot, electron]);

  const handlePreviewOpenExternal = useCallback(async () => {
    if (!activeExternalPreviewUrl) return;
    await nativeShell.openExternal(activeExternalPreviewUrl);
  }, [activeExternalPreviewUrl]);

  const handleSaveShortcut = useCallback(
    (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "s" && !event.shiftKey) {
        event.preventDefault();
        void handleSaveToDisk();
      }
    },
    [handleSaveToDisk],
  );

  useModeScopedWindowEvent<KeyboardEvent>(
    "content",
    "keydown",
    handleSaveShortcut,
    true,
  );

  const saveIndicatorLabel =
    activeConflict
      ? "External change detected"
      : saveState === "saving"
      ? "Saving to disk"
      : saveState === "queued"
        ? "Autosave queued"
        : blockingValidationIssues.length > 0 && isDirty
          ? "Fix errors to save"
      : saveState === "error"
        ? "Save failed"
        : isDirty
          ? "Local draft only"
          : saveState === "saved"
            ? "Saved to disk"
            : "Loaded from disk";

  const saveIndicatorTone =
    activeConflict
      ? "bg-rose-100 text-rose-900 dark:bg-rose-500/20 dark:text-rose-200"
      : saveState === "saving"
      ? "bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200"
      : saveState === "queued"
        ? "bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200"
        : blockingValidationIssues.length > 0 && isDirty
          ? "bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-200"
      : saveState === "error"
        ? "bg-rose-100 text-rose-900 dark:bg-rose-500/20 dark:text-rose-200"
        : isDirty
          ? "bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-200"
          : "bg-emerald-100 text-emerald-900 dark:bg-emerald-500/20 dark:text-emerald-200";

  const previewEditButtonLabel = previewInspectMode
    ? "Exit Page Edit"
    : "Edit on Page";
  const showPreviewActions =
    electron && (!!activeProjectRoot || !!activeProjectCandidate);
  const previewActionButtonClass =
    "flex h-8 w-8 items-center justify-center rounded-full border border-stone-200/80 bg-white/76 text-stone-600 shadow-sm backdrop-blur transition-colors hover:bg-white hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-40 dark:border-stone-700/80 dark:bg-stone-950/70 dark:text-stone-300 dark:hover:bg-stone-900 dark:hover:text-white";

  const hasInspectorContent =
    !!activeConflict ||
    validationIssues.length > 0 ||
    !!saveError ||
    !!activeFrontmatter.raw ||
    activeHeadings.length > 0;

  const diagnosticsCount =
    validationIssues.length +
    (activeConflict ? 1 : 0) +
    (saveError && !activeConflict ? 1 : 0);

  const metaChips = activePage
    ? [
        activePage.pageId ? `pageID ${activePage.pageId}` : null,
        `${activePage.wordCount} words`,
        activePreviewPath ? `route ${activePreviewPath}` : null,
      ].filter((value): value is string => Boolean(value))
    : [];

  return (
    <div className="flex h-full bg-[#f6f1e8] text-stone-900 dark:bg-[#131313] dark:text-stone-100">
      <aside className="flex w-[298px] shrink-0 flex-col border-r border-stone-200 bg-[#fbf8f2] dark:border-stone-800 dark:bg-[#171717]">
        <div className="border-b border-stone-200 px-4 py-3 dark:border-stone-800">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-stone-300 bg-white text-stone-700 shadow-sm dark:border-stone-700 dark:bg-stone-900 dark:text-stone-200">
              <BookOpenText size={16} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold tracking-tight">
                {workspace?.name ?? "Content Workspace"}
              </div>
              <div className="text-[10px] uppercase tracking-[0.24em] text-stone-500 dark:text-stone-400">
                Editorial Desk
              </div>
            </div>
          </div>
        </div>

        <div className="border-b border-stone-200 px-4 py-3 dark:border-stone-800">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-500 dark:text-stone-400">
              Projects
            </span>
            {electron ? (
              <div className="flex items-center gap-1">
                <button
                  onClick={() => void refreshProjects()}
                  className="rounded-md border border-stone-300 px-2 py-1 text-xs text-stone-600 transition-colors hover:bg-white hover:text-stone-900 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-900 dark:hover:text-white"
                  title="Refresh projects"
                >
                  <RefreshCcw size={12} />
                </button>
                <button
                  onClick={() => void handleAttachProject()}
                  className="rounded-md border border-stone-300 px-2 py-1 text-xs font-medium text-stone-700 transition-colors hover:bg-white hover:text-stone-950 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-900 dark:hover:text-white"
                >
                  Attach
                </button>
              </div>
            ) : (
              <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.18em] text-stone-500 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-400">
                Desktop only
              </span>
            )}
          </div>
          {projectsLoading ? (
            <div className="flex items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-500 dark:border-stone-800 dark:bg-stone-900/60 dark:text-stone-400">
              <Loader2 size={14} className="animate-spin" />
              Scanning workspace paths for Hugo projects...
            </div>
          ) : projectCandidates.length > 0 ? (
            <div className="space-y-2">
              {projectCandidates.map((candidate) => (
                <button
                  key={candidate.rootPath}
                  onClick={() => {
                    setActiveProjectRoot(candidate.rootPath);
                    setActivePagePath(null);
                  }}
                  className={cx(
                    "w-full rounded-2xl border px-3 py-3 text-left transition-colors",
                    activeProjectRoot === candidate.rootPath
                      ? "border-stone-900 bg-stone-900 text-white dark:border-stone-100 dark:bg-stone-100 dark:text-stone-950"
                      : "border-stone-200 bg-white hover:border-stone-400 hover:bg-stone-50 dark:border-stone-800 dark:bg-stone-900/60 dark:hover:border-stone-600 dark:hover:bg-stone-900",
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate font-medium">{candidate.name}</div>
                      <div
                        className={cx(
                          "truncate text-xs",
                          activeProjectRoot === candidate.rootPath
                            ? "text-white/70 dark:text-stone-600"
                            : "text-stone-500 dark:text-stone-400",
                        )}
                      >
                        {candidate.rootPath}
                      </div>
                    </div>
                    <span
                      className={cx(
                        "rounded-full px-2 py-0.5 text-[10px] uppercase tracking-[0.2em]",
                        activeProjectRoot === candidate.rootPath
                          ? "bg-white/15 text-white dark:bg-stone-300 dark:text-stone-900"
                          : "bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-300",
                      )}
                    >
                      {candidate.source}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-stone-300 bg-white px-3 py-3 text-sm text-stone-600 dark:border-stone-700 dark:bg-stone-900/50 dark:text-stone-400">
              {projectError ?? "No content projects detected yet."}
            </div>
          )}
        </div>

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="border-b border-stone-200 px-4 py-3 dark:border-stone-800">
            <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-500 dark:text-stone-400">
              <FolderSearch size={12} />
              Pages
            </div>
            <input
              value={pageQuery}
              onChange={(event) => setPageQuery(event.target.value)}
              placeholder="Filter pages, folders, or pageID..."
              className="w-full rounded-xl border border-stone-300 bg-white px-3 py-2 text-sm outline-none transition-colors placeholder:text-stone-400 focus:border-stone-500 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100 dark:placeholder:text-stone-500 dark:focus:border-stone-500"
            />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
            {pagesLoading ? (
              <div className="flex items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-500 dark:border-stone-800 dark:bg-stone-900/60 dark:text-stone-400">
                <Loader2 size={14} className="animate-spin" />
                Loading content pages...
              </div>
            ) : pagesError ? (
              <div className="rounded-xl border border-dashed border-stone-300 bg-white px-3 py-3 text-sm text-stone-600 dark:border-stone-700 dark:bg-stone-900/50 dark:text-stone-400">
                {pagesError}
              </div>
            ) : filteredPages.length === 0 ? (
              <div className="rounded-xl border border-dashed border-stone-300 bg-white px-3 py-3 text-sm text-stone-600 dark:border-stone-700 dark:bg-stone-900/50 dark:text-stone-400">
                No matching pages.
              </div>
            ) : (
              (() => {
                const renderPageButton = (
                  page: ContentPageSummary,
                  depth: number,
                ) => (
                  <button
                    key={page.filePath}
                    onClick={() => setActivePagePath(page.filePath)}
                    className={cx(
                      "w-full rounded-2xl border px-3 py-2 text-left transition-colors",
                      page.filePath === activePagePath
                        ? "border-stone-900 bg-stone-900 text-white dark:border-stone-100 dark:bg-stone-100 dark:text-stone-950"
                        : "border-transparent bg-transparent hover:border-stone-200 hover:bg-white dark:hover:border-stone-800 dark:hover:bg-stone-900/60",
                    )}
                  >
                    <div
                      className="flex items-start gap-3"
                      style={{ paddingLeft: `${depth * 14}px` }}
                    >
                      <div className="mt-0.5 rounded-lg border border-current/15 p-1">
                        <FileText size={13} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">
                          {page.title}
                        </div>
                        <div
                          className={cx(
                            "truncate text-xs",
                            page.filePath === activePagePath
                              ? "text-white/70 dark:text-stone-600"
                              : "text-stone-500 dark:text-stone-400",
                          )}
                        >
                          {page.relPath}
                        </div>
                      </div>
                    </div>
                  </button>
                );

                const renderFolderNode = (
                  folder: ContentFolderTreeNode,
                  depth = 0,
                ) => {
                  const explicitExpanded = expandedFolders[folder.relPath];
                  const isExpanded =
                    hasPageQuery ? true : (explicitExpanded ?? depth === 0);
                  const folderContainsActive =
                    !!activePage?.relPath &&
                    activePage.relPath.startsWith(`${folder.relPath}/`);

                  return (
                    <div key={folder.relPath} className="space-y-1">
                      <button
                        type="button"
                        aria-expanded={isExpanded}
                        onClick={() =>
                          toggleFolderExpanded(folder.relPath, isExpanded)
                        }
                        className={cx(
                          "flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-left transition-colors",
                          folderContainsActive
                            ? "bg-stone-200/80 text-stone-900 dark:bg-stone-800 dark:text-stone-100"
                            : "text-stone-700 hover:bg-white dark:text-stone-300 dark:hover:bg-stone-900/60",
                        )}
                        style={{ paddingLeft: `${depth * 14 + 8}px` }}
                      >
                        {isExpanded ? (
                          <ChevronDown size={14} className="shrink-0" />
                        ) : (
                          <ChevronRight size={14} className="shrink-0" />
                        )}
                        <Folder size={14} className="shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-sm font-medium">
                          {folder.label}
                        </span>
                        <span className="rounded-full bg-stone-200 px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] text-stone-600 dark:bg-stone-700 dark:text-stone-300">
                          {folder.pageCount}
                        </span>
                      </button>
                      {isExpanded ? (
                        <div className="space-y-1">
                          {folder.folders.map((child) =>
                            renderFolderNode(child, depth + 1),
                          )}
                          {folder.pages.map((page) =>
                            renderPageButton(page, depth + 1),
                          )}
                        </div>
                      ) : null}
                    </div>
                  );
                };

                return (
                  <div className="space-y-1">
                    {pageTree.folders.map((folder) => renderFolderNode(folder))}
                    {pageTree.pages.length > 0 ? (
                      <div className="space-y-1 pt-2">
                        {pageTree.folders.length > 0 ? (
                          <div className="px-2 text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-500 dark:text-stone-400">
                            Root Pages
                          </div>
                        ) : null}
                        {pageTree.pages.map((page) => renderPageButton(page, 0))}
                      </div>
                    ) : null}
                  </div>
                );
              })()
            )}
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="border-b border-stone-200 bg-white/80 px-5 py-4 backdrop-blur dark:border-stone-800 dark:bg-stone-950/70">
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-500 dark:text-stone-400">
                  Content Desk
                </div>
                <div className="truncate text-base font-semibold tracking-tight">
                  {activePage?.title ?? "Content Mode"}
                </div>
                <div className="truncate text-xs text-stone-500 dark:text-stone-400">
                  {relativeFileLabel(activeProjectRoot, activePagePath) ??
                    "Choose a project and page to start."}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={cx(
                    "rounded-full px-2.5 py-1 text-[11px] font-medium",
                    saveIndicatorTone,
                  )}
                >
                  {saveIndicatorLabel}
                </span>
                <button
                  onClick={() => void handleSaveToDisk()}
                  disabled={!electron || !activePagePath || saveState === "saving"}
                  className="inline-flex items-center rounded-xl border border-stone-300 px-3 py-2 text-sm font-medium text-stone-700 transition-colors hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-900"
                >
                  Save
                </button>
                <button
                  onClick={toggleChatSidebar}
                  className={cx(
                    "inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-sm font-medium transition-colors",
                    showChatSidebar
                      ? "border-stone-900 bg-stone-900 text-white dark:border-stone-100 dark:bg-stone-100 dark:text-stone-950"
                      : "border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-900",
                  )}
                >
                  <MessageSquareText size={14} />
                  Chat
                </button>
              </div>
            </div>
            {activePage ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-stone-600 dark:text-stone-400">
                {metaChips.map((chip) => (
                  <span
                    key={chip}
                    className="rounded-full bg-stone-100 px-2.5 py-1 dark:bg-stone-800"
                  >
                    {chip}
                  </span>
                ))}
                <span className="rounded-full bg-stone-100 px-2.5 py-1 dark:bg-stone-800">
                  autosave {AUTOSAVE_DELAY_MS}ms
                </span>
                {diagnosticsCount > 0 ? (
                  <span className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-900 dark:bg-amber-500/20 dark:text-amber-200">
                    {diagnosticsCount} issue{diagnosticsCount === 1 ? "" : "s"}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="grid min-h-0 flex-1 xl:grid-cols-[minmax(380px,0.92fr)_minmax(520px,1.08fr)]">
            <section className="flex min-h-0 min-w-0 flex-col border-b border-stone-200 bg-[#fcfaf6] xl:border-b-0 xl:border-r dark:border-stone-800 dark:bg-[#151515]">
              <div className="relative min-h-0 flex-1">
                {loadingPagePath === activePagePath && activePagePath ? (
                  <div className="pointer-events-none absolute right-4 top-4 z-10 inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white/92 px-2.5 py-1 text-xs text-stone-500 shadow-sm backdrop-blur dark:border-stone-700 dark:bg-stone-900/90 dark:text-stone-400">
                    <Loader2 size={12} className="animate-spin" />
                    Loading
                  </div>
                ) : null}
                {activePagePath ? (
                  <Editor
                    height="100%"
                    language="markdown"
                    theme={monacoTheme}
                    value={activeDraft}
                    onChange={handleEditorChange}
                    options={{
                      automaticLayout: true,
                      minimap: { enabled: false },
                      wordWrap: "on",
                      fontSize: 14,
                      lineNumbersMinChars: 3,
                      scrollBeyondLastLine: false,
                      smoothScrolling: true,
                    }}
                  />
                ) : (
                  <div className="flex h-full items-center justify-center px-8 text-center text-stone-500 dark:text-stone-400">
                    <div className="max-w-md space-y-3">
                      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-dashed border-stone-300 dark:border-stone-700">
                        <Sparkles size={18} />
                      </div>
                      <div className="text-lg font-semibold text-stone-900 dark:text-stone-100">
                        Select a page to start the content desk
                      </div>
                      <p className="text-sm leading-6">
                        This desk now prioritizes the writing view and the real
                        rendered page. The extra metadata stays available below,
                        but it no longer crowds the preview.
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {activePagePath && hasInspectorContent ? (
                <div className="border-t border-stone-200 bg-white/70 px-3 py-3 dark:border-stone-800 dark:bg-stone-950/40">
                  <div className="grid gap-3 xl:grid-cols-1 2xl:grid-cols-2">
                    <details
                      open={diagnosticsCount > 0}
                      className="group rounded-2xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900/60"
                    >
                      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 text-sm font-medium text-stone-800 marker:hidden dark:text-stone-100">
                        <span className="flex items-center gap-2">
                          <AlertTriangle size={14} />
                          Diagnostics
                        </span>
                        <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] text-stone-600 dark:bg-stone-800 dark:text-stone-300">
                          {diagnosticsCount === 0 ? "clean" : diagnosticsCount}
                        </span>
                      </summary>
                      <div className="space-y-2 border-t border-stone-200 px-3 py-3 text-sm dark:border-stone-800">
                        {activeConflict ? (
                          <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-3 dark:border-rose-900/60 dark:bg-rose-950/40">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <div className="font-medium text-rose-900 dark:text-rose-100">
                                  Disk changed outside DAN
                                </div>
                                <p className="mt-1 text-xs leading-5 text-rose-800 dark:text-rose-200">
                                  Detected at{" "}
                                  {new Date(activeConflict.detectedAt).toLocaleTimeString()}
                                  . Reload the newer disk version, or keep the
                                  current draft and reapply it over disk.
                                </p>
                              </div>
                              <button
                                onClick={handleToggleConflictSnapshot}
                                className="rounded-lg border border-rose-200 px-2 py-1 text-[11px] font-medium text-rose-800 transition-colors hover:bg-white dark:border-rose-800 dark:text-rose-100 dark:hover:bg-rose-950/60"
                              >
                                {activeConflict.showDiskSnapshot
                                  ? "Hide Compare"
                                  : "Compare"}
                              </button>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                              <button
                                onClick={handleReloadDiskVersion}
                                className="rounded-xl border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-900 transition-colors hover:bg-white dark:border-rose-800 dark:text-rose-100 dark:hover:bg-rose-950/60"
                              >
                                Reload Disk
                              </button>
                              <button
                                onClick={handleKeepDraft}
                                className="rounded-xl border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-900 transition-colors hover:bg-white dark:border-rose-800 dark:text-rose-100 dark:hover:bg-rose-950/60"
                              >
                                Keep Draft
                              </button>
                            </div>
                            {activeConflict.showDiskSnapshot ? (
                              <div className="mt-3 grid gap-3">
                                <div>
                                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.2em] text-rose-800 dark:text-rose-200">
                                    Last Known Disk
                                  </div>
                                  <pre className="overflow-x-auto rounded-xl bg-white/80 px-3 py-2 text-xs leading-5 text-rose-900 dark:bg-stone-950 dark:text-rose-100">
                                    {previewContentSnippet(
                                      activeConflict.lastKnownContent,
                                    )}
                                  </pre>
                                </div>
                                <div>
                                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.2em] text-rose-800 dark:text-rose-200">
                                    Current Disk
                                  </div>
                                  <pre className="overflow-x-auto rounded-xl bg-white/80 px-3 py-2 text-xs leading-5 text-rose-900 dark:bg-stone-950 dark:text-rose-100">
                                    {previewContentSnippet(activeConflict.diskContent)}
                                  </pre>
                                </div>
                              </div>
                            ) : null}
                          </div>
                        ) : null}
                        {validationIssues.map((issue) => (
                          <div
                            key={issue.id}
                            className={cx(
                              "rounded-xl border px-3 py-2",
                              buildDiagnosticTone(issue),
                            )}
                          >
                            <div className="flex items-center gap-2">
                              <AlertTriangle size={13} />
                              <span className="text-[10px] font-semibold uppercase tracking-[0.22em]">
                                {issue.category}
                              </span>
                            </div>
                            <div className="mt-1 text-sm leading-5">
                              {issue.message}
                            </div>
                            {issue.detail ? (
                              <div className="mt-1 text-xs opacity-80">
                                {issue.detail}
                              </div>
                            ) : null}
                          </div>
                        ))}
                        {saveError && !activeConflict ? (
                          <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-100">
                            {saveError}
                          </div>
                        ) : null}
                        {!activeConflict &&
                        validationIssues.length === 0 &&
                        !saveError ? (
                          <div className="text-sm leading-6 text-stone-600 dark:text-stone-400">
                            No active blockers. This panel stays quiet until a
                            draft needs attention.
                          </div>
                        ) : null}
                      </div>
                    </details>

                    <details className="group rounded-2xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900/60">
                      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 text-sm font-medium text-stone-800 marker:hidden dark:text-stone-100">
                        <span>Frontmatter</span>
                        <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] text-stone-600 dark:bg-stone-800 dark:text-stone-300">
                          {activeFrontmatter.raw ? "loaded" : "none"}
                        </span>
                      </summary>
                      <div className="space-y-1 border-t border-stone-200 px-3 py-3 text-sm text-stone-600 dark:border-stone-800 dark:text-stone-400">
                        {activeFrontmatter.raw ? (
                          <>
                            <div>
                              <span className="font-medium text-stone-900 dark:text-stone-100">
                                title:
                              </span>{" "}
                              {activeFrontmatter.title ?? "—"}
                            </div>
                            <div>
                              <span className="font-medium text-stone-900 dark:text-stone-100">
                                pageID:
                              </span>{" "}
                              {activeFrontmatter.pageId ?? "—"}
                            </div>
                            <div>
                              <span className="font-medium text-stone-900 dark:text-stone-100">
                                date:
                              </span>{" "}
                              {activeFrontmatter.date ?? "—"}
                            </div>
                            <div>
                              <span className="font-medium text-stone-900 dark:text-stone-100">
                                slug:
                              </span>{" "}
                              {activeFrontmatter.slug ?? "—"}
                            </div>
                            <div>
                              <span className="font-medium text-stone-900 dark:text-stone-100">
                                url:
                              </span>{" "}
                              {activeFrontmatter.url ?? "—"}
                            </div>
                            <div>
                              <span className="font-medium text-stone-900 dark:text-stone-100">
                                draft:
                              </span>{" "}
                              {activeFrontmatter.draft == null
                                ? "—"
                                : activeFrontmatter.draft
                                  ? "true"
                                  : "false"}
                            </div>
                          </>
                        ) : (
                          <div>No frontmatter block detected.</div>
                        )}
                      </div>
                    </details>

                    <details className="group rounded-2xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900/60 2xl:col-span-2">
                      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 text-sm font-medium text-stone-800 marker:hidden dark:text-stone-100">
                        <span>Heading Outline</span>
                        <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] text-stone-600 dark:bg-stone-800 dark:text-stone-300">
                          {activeHeadings.length === 0
                            ? "empty"
                            : activeHeadings.length}
                        </span>
                      </summary>
                      <div className="border-t border-stone-200 px-3 py-3 dark:border-stone-800">
                        {activeHeadings.length > 0 ? (
                          <div className="space-y-2">
                            {activeHeadings.map((heading, index) => (
                              <div
                                key={`${heading.depth}-${heading.text}-${index}`}
                                className="text-sm text-stone-700 dark:text-stone-300"
                                style={{ paddingLeft: `${(heading.depth - 2) * 12}px` }}
                              >
                                {heading.text}
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-sm text-stone-500 dark:text-stone-400">
                            No `##`-or-deeper headings detected in the current
                            draft.
                          </div>
                        )}
                      </div>
                    </details>
                  </div>
                </div>
              ) : null}
            </section>

            <section className="flex min-h-0 min-w-0 flex-col bg-[#efe7dc] dark:bg-[#191613]">
              <div className="min-h-0 flex-1 p-4">
                {electron ? (
                  !activeProjectCandidate ? (
                    <div className="flex h-full items-center justify-center rounded-[28px] border border-dashed border-stone-300 bg-white/70 px-8 text-center text-sm text-stone-600 dark:border-stone-700 dark:bg-stone-950/40 dark:text-stone-400">
                      Select a content project to start the live preview desk.
                    </div>
                  ) : !activeProjectCandidate.hasHugoConfig ? (
                    <div className="flex h-full items-center justify-center rounded-[28px] border border-dashed border-stone-300 bg-white/70 px-8 text-center text-sm leading-6 text-stone-600 dark:border-stone-700 dark:bg-stone-950/40 dark:text-stone-400">
                      No Hugo config detected yet. Route inspection still works,
                      but DAN will not boot a managed preview for this root.
                    </div>
                  ) : previewBusy || previewStatus?.lifecycle === "starting" ? (
                    <div className="flex h-full items-center justify-center rounded-[28px] border border-stone-200 bg-white/80 px-8 dark:border-stone-800 dark:bg-stone-950/50">
                      <div className="space-y-3 text-center">
                        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-stone-200 bg-stone-50 dark:border-stone-700 dark:bg-stone-900">
                          <Loader2 size={18} className="animate-spin" />
                        </div>
                        <div className="text-sm font-medium text-stone-900 dark:text-stone-100">
                          Booting the Hugo preview server...
                        </div>
                        <div className="text-xs text-stone-500 dark:text-stone-400">
                          Large knowledge bases may take a moment on cold start.
                        </div>
                      </div>
                    </div>
                  ) : activePreviewUrl && previewStatus?.ready ? (
                    <div
                      ref={previewSurfaceRef}
                      className="group relative h-full overflow-hidden rounded-[28px] border border-stone-200 bg-white shadow-[0_28px_80px_-48px_rgba(20,17,12,0.55)] dark:border-stone-800 dark:bg-stone-950"
                    >
                      {showPreviewActions ? (
                        <div className="pointer-events-none absolute right-3 top-3 z-10 flex gap-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100">
                          <div className="pointer-events-auto flex gap-1 rounded-full border border-stone-200/70 bg-white/44 p-1 shadow-sm backdrop-blur-xl dark:border-stone-700/70 dark:bg-stone-950/44">
                            <button
                              onClick={handlePreviewInspectToggle}
                              disabled={!previewCanEditOnPage}
                              title={previewEditButtonLabel}
                              className={cx(
                                previewActionButtonClass,
                                previewInspectMode
                                  ? "border-emerald-700 bg-emerald-700 text-white hover:bg-emerald-700 hover:text-white dark:border-emerald-400 dark:bg-emerald-400 dark:text-stone-950 dark:hover:bg-emerald-400 dark:hover:text-stone-950"
                                  : null,
                              )}
                            >
                              <PencilLine size={14} />
                            </button>
                            <button
                              onClick={() => void handlePreviewRestart()}
                              disabled={
                                !activeProjectRoot ||
                                !activeProjectCandidate?.hasHugoConfig ||
                                previewBusy
                              }
                              title="Restart preview"
                              className={previewActionButtonClass}
                            >
                              <RefreshCcw size={14} />
                            </button>
                            <button
                              onClick={() => void handlePreviewStop()}
                              disabled={!activeProjectRoot || previewBusy}
                              title="Stop preview"
                              className={previewActionButtonClass}
                            >
                              <Square size={13} />
                            </button>
                            <button
                              onClick={() => void handlePreviewOpenExternal()}
                              disabled={!activePreviewUrl}
                              title="Open in browser"
                              className={previewActionButtonClass}
                            >
                              <ExternalLink size={14} />
                            </button>
                          </div>
                        </div>
                      ) : null}

                      {previewGuestIssue ? (
                        <div className="pointer-events-none absolute inset-x-0 top-16 z-10 flex justify-center px-4">
                          <div className="pointer-events-auto max-w-2xl rounded-2xl border border-amber-200 bg-white/96 px-4 py-3 text-sm text-amber-900 shadow-sm backdrop-blur dark:border-amber-500/30 dark:bg-stone-950/92 dark:text-amber-200">
                            <div className="font-medium">
                              This page loaded, but its client script failed.
                            </div>
                            <div className="mt-1 break-all text-xs leading-5 opacity-90">
                              {previewGuestIssue}
                            </div>
                          </div>
                        </div>
                      ) : null}

                      <iframe
                        ref={previewIframeRef}
                        key={`${activePreviewUrl}:${previewFrameVersion}`}
                        src={activePreviewUrl}
                        title="Managed Hugo Preview"
                        className="absolute inset-0 h-full w-full bg-white dark:bg-stone-950"
                      />

                      {previewCanEditOnPage &&
                      (previewInspectMode || previewEditTarget || previewEditError) ? (
                        <div className="pointer-events-none absolute inset-0 flex items-start justify-end p-4">
                          <div className="pointer-events-auto w-full max-w-[420px] rounded-[26px] border border-stone-200 bg-white/96 p-4 shadow-[0_24px_60px_-36px_rgba(20,17,12,0.6)] backdrop-blur dark:border-stone-700 dark:bg-stone-950/92">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-500 dark:text-stone-400">
                                  On-Page Edit
                                </div>
                                <div className="mt-1 text-sm font-medium text-stone-900 dark:text-stone-100">
                                  {previewEditTarget
                                    ? previewEditTargetLabel(previewEditTarget)
                                    : "Pick a rendered block"}
                                </div>
                              </div>
                              <button
                                onClick={() => {
                                  clearPreviewEditSelection();
                                  setPreviewInspectMode(false);
                                }}
                                className="rounded-full border border-stone-300 px-2.5 py-1 text-[11px] font-medium text-stone-600 transition-colors hover:bg-stone-100 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-900"
                              >
                                Close
                              </button>
                            </div>

                            {previewEditTarget ? (
                              <>
                                <textarea
                                  value={previewEditValue}
                                  onChange={(event) =>
                                    setPreviewEditValue(event.target.value)
                                  }
                                  rows={
                                    previewEditTarget.targetType ===
                                      "markdown-block" &&
                                    previewEditTarget.kind === "blockquote"
                                      ? 5
                                      : 4
                                  }
                                  className="mt-3 w-full rounded-2xl border border-stone-300 bg-stone-50 px-3 py-3 text-sm leading-6 text-stone-900 outline-none transition-colors focus:border-stone-500 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100 dark:focus:border-stone-500"
                                />
                                {previewEditError ? (
                                  <div className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-100">
                                    {previewEditError}
                                  </div>
                                ) : (
                                  <div className="mt-3 text-xs leading-5 text-stone-500 dark:text-stone-400">
                                    DAN writes this change back to Markdown, saves
                                    the file, and then lets Hugo refresh the real
                                    page.
                                  </div>
                                )}
                                <div className="mt-3 flex justify-end gap-2">
                                  <button
                                    onClick={clearPreviewEditSelection}
                                    className="rounded-xl border border-stone-300 px-3 py-2 text-xs font-medium text-stone-700 transition-colors hover:bg-stone-100 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-900"
                                  >
                                    Clear
                                  </button>
                                  <button
                                    onClick={() => void handleApplyPreviewEdit()}
                                    disabled={previewEditSaving}
                                    className="rounded-xl bg-stone-900 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-stone-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-stone-100 dark:text-stone-950 dark:hover:bg-stone-300"
                                  >
                                    {previewEditSaving ? "Applying…" : "Apply to Source"}
                                  </button>
                                </div>
                              </>
                            ) : (
                              <div className="mt-3 text-sm leading-6 text-stone-600 dark:text-stone-400">
                                Click a heading, paragraph, list item, or quote in
                                the rendered page. The preview stays authoritative;
                                DAN only writes the matched Markdown block behind it.
                              </div>
                            )}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <div className="relative flex h-full flex-col justify-between rounded-[28px] border border-dashed border-stone-300 bg-white/70 p-6 dark:border-stone-700 dark:bg-stone-950/40">
                      {showPreviewActions ? (
                        <div className="mb-4 flex justify-end">
                          <div className="flex gap-1 rounded-full border border-stone-200/70 bg-white/70 p-1 shadow-sm backdrop-blur dark:border-stone-700/70 dark:bg-stone-950/60">
                            <button
                              onClick={handlePreviewInspectToggle}
                              disabled={!previewCanEditOnPage}
                              title={previewEditButtonLabel}
                              className={cx(
                                previewActionButtonClass,
                                previewInspectMode
                                  ? "border-emerald-700 bg-emerald-700 text-white hover:bg-emerald-700 hover:text-white dark:border-emerald-400 dark:bg-emerald-400 dark:text-stone-950 dark:hover:bg-emerald-400 dark:hover:text-stone-950"
                                  : null,
                              )}
                            >
                              <PencilLine size={14} />
                            </button>
                            <button
                              onClick={() => void handlePreviewStart()}
                              disabled={
                                !activeProjectRoot ||
                                !activeProjectCandidate?.hasHugoConfig ||
                                previewBusy
                              }
                              title="Start preview"
                              className={previewActionButtonClass}
                            >
                              <Play size={14} />
                            </button>
                            <button
                              onClick={() => void handlePreviewRestart()}
                              disabled={
                                !activeProjectRoot ||
                                !activeProjectCandidate?.hasHugoConfig ||
                                previewBusy
                              }
                              title="Restart preview"
                              className={previewActionButtonClass}
                            >
                              <RefreshCcw size={14} />
                            </button>
                            <button
                              onClick={() => void handlePreviewStop()}
                              disabled={!activeProjectRoot || previewBusy}
                              title="Stop preview"
                              className={previewActionButtonClass}
                            >
                              <Square size={13} />
                            </button>
                            <button
                              onClick={() => void handlePreviewOpenExternal()}
                              disabled={!activePreviewUrl}
                              title="Open in browser"
                              className={previewActionButtonClass}
                            >
                              <ExternalLink size={14} />
                            </button>
                          </div>
                        </div>
                      ) : null}

                      <div>
                        <div className="text-base font-semibold text-stone-900 dark:text-stone-100">
                          {previewError ??
                            previewStatus?.lastError ??
                            "The managed preview server is not running yet."}
                        </div>
                        <p className="mt-2 max-w-lg text-sm leading-6 text-stone-600 dark:text-stone-400">
                          This pane now reserves the room for the real rendered
                          page. When preview is down, the controls stay here and the
                          logs move to the bottom instead of shrinking the iframe.
                        </p>
                      </div>
                      {previewStatus?.recentLogs.length ? (
                        <div className="rounded-2xl bg-stone-100 px-4 py-3 text-xs text-stone-700 dark:bg-stone-900 dark:text-stone-300">
                          {previewStatus.recentLogs.slice(-4).map((line, index) => (
                            <div key={`${index}-${line}`}>{line}</div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  )
                ) : (
                  <div className="relative h-full overflow-hidden rounded-[28px] border border-stone-200 bg-white/78 shadow-[0_28px_80px_-48px_rgba(20,17,12,0.35)] dark:border-stone-800 dark:bg-stone-950/40">
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>

        {showChatSidebar && (
          <div className="w-[360px] min-w-[280px] max-w-[460px] shrink-0 border-l border-stone-200 dark:border-stone-800">
            <ModeChatSidebar
              mode="content"
              onClose={closeChatSidebar}
              contextProvider={activeChatContext}
            />
          </div>
        )}
      </div>
    </div>
  );
}
