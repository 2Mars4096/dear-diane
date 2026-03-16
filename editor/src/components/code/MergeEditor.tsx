import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor as monacoEditor } from "monaco-editor";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  Eye,
  EyeOff,
  GitMerge,
  Loader2,
  X,
} from "lucide-react";
import { Allotment } from "allotment";
import { nativeGit } from "../../lib/electronBridge";
import { useSettingsStore } from "../../store/useSettingsStore";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";

interface ConflictRegion {
  id: number;
  startLine: number;
  endLine: number;
  currentStart: number;
  currentEnd: number;
  separatorLine: number;
  incomingStart: number;
  incomingEnd: number;
  currentContent: string;
  incomingContent: string;
  baseContent?: string;
  resolved: boolean;
}

interface ConflictFile {
  path: string;
  base: string;
  current: string;
  incoming: string;
  merged: string;
}

interface Props {
  cwd: string;
  filePath: string;
  onClose: () => void;
  onResolved: () => void;
}

function parseConflicts(content: string): ConflictRegion[] {
  const lines = content.split("\n");
  const regions: ConflictRegion[] = [];
  let id = 0;
  let i = 0;

  while (i < lines.length) {
    if (lines[i].startsWith("<<<<<<<")) {
      const startLine = i;
      const currentStart = i + 1;
      let separatorLine = -1;
      let incomingEnd = -1;

      for (let j = i + 1; j < lines.length; j++) {
        if (lines[j].startsWith("=======")) {
          separatorLine = j;
        } else if (lines[j].startsWith(">>>>>>>") && separatorLine >= 0) {
          incomingEnd = j;
          break;
        }
      }

      if (separatorLine >= 0 && incomingEnd >= 0) {
        const currentContent = lines.slice(currentStart, separatorLine).join("\n");
        const incomingContent = lines.slice(separatorLine + 1, incomingEnd).join("\n");

        regions.push({
          id: id++,
          startLine,
          endLine: incomingEnd,
          currentStart,
          currentEnd: separatorLine - 1,
          separatorLine,
          incomingStart: separatorLine + 1,
          incomingEnd: incomingEnd - 1,
          currentContent,
          incomingContent,
          resolved: false,
        });

        i = incomingEnd + 1;
        continue;
      }
    }
    i++;
  }

  return regions;
}

function resolveConflict(
  content: string,
  region: ConflictRegion,
  choice: "current" | "incoming" | "both" | "base",
  baseContent?: string,
): string {
  const lines = content.split("\n");
  let replacement: string;

  switch (choice) {
    case "current":
      replacement = region.currentContent;
      break;
    case "incoming":
      replacement = region.incomingContent;
      break;
    case "both":
      replacement = region.currentContent + "\n" + region.incomingContent;
      break;
    case "base":
      replacement = baseContent ?? "";
      break;
    default:
      replacement = region.currentContent;
  }

  const before = lines.slice(0, region.startLine);
  const after = lines.slice(region.endLine + 1);
  return [...before, replacement, ...after].join("\n");
}

function ReadOnlyPane({
  title,
  content,
  language,
  highlightClass,
  label,
  theme,
}: {
  title: string;
  content: string;
  language: string;
  highlightClass: string;
  label: string;
  theme: string;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className={`flex items-center gap-1.5 px-3 py-1 ${highlightClass} shrink-0`}>
        <span className="text-[11px] font-semibold text-white/90">{title}</span>
        <span className="ml-auto text-[10px] text-white/50">{label}</span>
      </div>
      <div className="flex-1 min-h-0">
        <Editor
          value={content}
          language={language}
          theme={theme}
          options={{
            readOnly: true,
            minimap: { enabled: false },
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            automaticLayout: true,
            fontSize: 12,
            padding: { top: 4 },
            renderWhitespace: "none",
          }}
        />
      </div>
    </div>
  );
}

export default function MergeEditor({ cwd, filePath, onClose, onResolved }: Props) {
  const theme = useSettingsStore((s) => resolveMonacoTheme(s.theme));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [conflictFile, setConflictFile] = useState<ConflictFile | null>(null);
  const [_conflicts, setConflicts] = useState<ConflictRegion[]>([]);
  const [activeConflictIndex, setActiveConflictIndex] = useState(0);
  const [mergedContent, setMergedContent] = useState("");
  const [showBase, setShowBase] = useState(false);
  const [resolving, setResolving] = useState(false);
  const mergedEditorRef = useRef<monacoEditor.IStandaloneCodeEditor | null>(null);

  const language = useMemo(() => {
    const ext = filePath.split(".").pop()?.toLowerCase() ?? "";
    const map: Record<string, string> = {
      ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
      py: "python", rs: "rust", go: "go", json: "json", md: "markdown",
      css: "css", scss: "scss", html: "html", yml: "yaml", yaml: "yaml",
      toml: "toml", sh: "shell", sql: "sql", xml: "xml",
    };
    return map[ext] ?? "plaintext";
  }, [filePath]);

  const loadConflict = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [contentRes, baseRes, oursRes, theirsRes] = await Promise.all([
        nativeGit.conflictContent(cwd, filePath),
        nativeGit.showBase(cwd, filePath),
        nativeGit.showOurs(cwd, filePath),
        nativeGit.showTheirs(cwd, filePath),
      ]);

      if (contentRes.code !== 0) {
        setError(contentRes.stderr || "Failed to read conflicted file");
        return;
      }

      const merged = contentRes.stdout;
      const file: ConflictFile = {
        path: filePath,
        base: baseRes.code === 0 ? baseRes.stdout : "",
        current: oursRes.code === 0 ? oursRes.stdout : "",
        incoming: theirsRes.code === 0 ? theirsRes.stdout : "",
        merged,
      };

      setConflictFile(file);
      setMergedContent(merged);
      setConflicts(parseConflicts(merged));
      setActiveConflictIndex(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load conflict");
    } finally {
      setLoading(false);
    }
  }, [cwd, filePath]);

  useEffect(() => {
    loadConflict();
  }, [loadConflict]);

  const remainingConflicts = useMemo(
    () => parseConflicts(mergedContent).length,
    [mergedContent],
  );

  const handleAccept = useCallback(
    (choice: "current" | "incoming" | "both" | "base") => {
      const currentConflicts = parseConflicts(mergedContent);
      if (activeConflictIndex >= currentConflicts.length) return;

      const region = currentConflicts[activeConflictIndex];
      const newContent = resolveConflict(mergedContent, region, choice, conflictFile?.base);
      setMergedContent(newContent);

      const newConflicts = parseConflicts(newContent);
      setConflicts(newConflicts);
      if (activeConflictIndex >= newConflicts.length && newConflicts.length > 0) {
        setActiveConflictIndex(newConflicts.length - 1);
      }
    },
    [mergedContent, activeConflictIndex, conflictFile],
  );

  const navigateConflict = useCallback(
    (direction: "prev" | "next") => {
      const currentConflicts = parseConflicts(mergedContent);
      if (currentConflicts.length === 0) return;

      if (direction === "prev") {
        setActiveConflictIndex((i) => Math.max(0, i - 1));
      } else {
        setActiveConflictIndex((i) => Math.min(currentConflicts.length - 1, i + 1));
      }
    },
    [mergedContent],
  );

  useEffect(() => {
    if (!mergedEditorRef.current) return;
    const currentConflicts = parseConflicts(mergedContent);
    if (activeConflictIndex < currentConflicts.length) {
      const region = currentConflicts[activeConflictIndex];
      mergedEditorRef.current.revealLineInCenter(region.startLine + 1);
    }
  }, [activeConflictIndex, mergedContent]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey && e.key === "ArrowUp") {
        e.preventDefault();
        navigateConflict("prev");
      } else if (e.altKey && e.key === "ArrowDown") {
        e.preventDefault();
        navigateConflict("next");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigateConflict]);

  const handleMarkResolved = useCallback(async () => {
    if (remainingConflicts > 0) return;
    setResolving(true);
    try {
      const res = await nativeGit.markResolved(cwd, filePath, mergedContent);
      if (res.code === 0) {
        onResolved();
        onClose();
      } else {
        setError(res.stderr || "Failed to mark as resolved");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to resolve");
    } finally {
      setResolving(false);
    }
  }, [cwd, filePath, mergedContent, remainingConflicts, onResolved, onClose]);

  const handleEditorMount: OnMount = useCallback((editor) => {
    mergedEditorRef.current = editor;
  }, []);

  const handleEditorChange = useCallback((value: string | undefined) => {
    if (value !== undefined) {
      setMergedContent(value);
    }
  }, []);

  const shortPath = filePath.split("/").slice(-2).join("/");

  if (loading) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-[#1e1e1e]">
        <Loader2 size={24} className="animate-spin text-gray-500" />
        <span className="ml-2 text-sm text-gray-400">Loading merge editor...</span>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col bg-[#1e1e1e]">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-700 px-3 py-1.5 shrink-0">
        <div className="flex items-center gap-2">
          <GitMerge size={15} className="text-orange-400" />
          <span className="text-xs font-semibold text-white">Merge Editor</span>
          <span className="text-[11px] text-gray-500">{shortPath}</span>
        </div>

        <div className="flex items-center gap-2">
          {/* Conflict navigation */}
          <div className="flex items-center gap-1 text-[11px]">
            <button
              onClick={() => navigateConflict("prev")}
              disabled={activeConflictIndex === 0 || remainingConflicts === 0}
              className="rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-700 hover:text-white disabled:opacity-30"
              title="Previous Conflict (Alt+Up)"
            >
              <ArrowUp size={14} />
            </button>
            <span className={`px-1 ${remainingConflicts > 0 ? "text-yellow-400" : "text-green-400"}`}>
              {remainingConflicts > 0
                ? `${remainingConflicts} conflict${remainingConflicts !== 1 ? "s" : ""}`
                : "All resolved"}
            </span>
            <button
              onClick={() => navigateConflict("next")}
              disabled={activeConflictIndex >= remainingConflicts - 1 || remainingConflicts === 0}
              className="rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-700 hover:text-white disabled:opacity-30"
              title="Next Conflict (Alt+Down)"
            >
              <ArrowDown size={14} />
            </button>
          </div>

          {/* Action buttons */}
          {remainingConflicts > 0 && (
            <div className="flex items-center gap-1">
              <button
                onClick={() => handleAccept("current")}
                className="rounded bg-green-700/40 px-2 py-0.5 text-[10px] font-medium text-green-300 transition-colors hover:bg-green-700/60"
              >
                Accept Current
              </button>
              <button
                onClick={() => handleAccept("incoming")}
                className="rounded bg-blue-700/40 px-2 py-0.5 text-[10px] font-medium text-blue-300 transition-colors hover:bg-blue-700/60"
              >
                Accept Incoming
              </button>
              <button
                onClick={() => handleAccept("both")}
                className="rounded bg-purple-700/40 px-2 py-0.5 text-[10px] font-medium text-purple-300 transition-colors hover:bg-purple-700/60"
              >
                Accept Both
              </button>
              {conflictFile?.base && (
                <button
                  onClick={() => handleAccept("base")}
                  className="rounded bg-gray-700/40 px-2 py-0.5 text-[10px] font-medium text-gray-300 transition-colors hover:bg-gray-700/60"
                >
                  Accept Base
                </button>
              )}
            </div>
          )}

          {/* Toggle base view */}
          <button
            onClick={() => setShowBase((v) => !v)}
            className={`rounded p-1 text-gray-400 transition-colors hover:bg-gray-700 ${showBase ? "text-white bg-gray-700" : ""}`}
            title={showBase ? "Hide Base" : "Show Base (Common Ancestor)"}
          >
            {showBase ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>

          {/* Mark resolved */}
          <button
            onClick={handleMarkResolved}
            disabled={remainingConflicts > 0 || resolving}
            className="flex items-center gap-1 rounded bg-green-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-green-500 disabled:opacity-40"
          >
            {resolving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
            Mark Resolved
          </button>

          <button
            onClick={onClose}
            className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-700 hover:text-white"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mx-3 mt-2 flex items-start gap-2 rounded bg-red-900/40 px-3 py-2 text-xs text-red-300">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span className="flex-1">{error}</span>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 min-h-0">
        <Allotment vertical>
          {/* Top: side-by-side view of ours vs theirs */}
          <Allotment.Pane>
            <Allotment>
              <Allotment.Pane>
                <ReadOnlyPane
                  title="Current (Ours)"
                  content={conflictFile?.current ?? ""}
                  language={language}
                  highlightClass="bg-green-900/40 border-b border-green-800/50"
                  label="HEAD"
                  theme={theme}
                />
              </Allotment.Pane>

              {showBase && (
                <Allotment.Pane>
                  <ReadOnlyPane
                    title="Base"
                    content={conflictFile?.base ?? ""}
                    language={language}
                    highlightClass="bg-gray-800 border-b border-gray-700"
                    label="Common Ancestor"
                    theme={theme}
                  />
                </Allotment.Pane>
              )}

              <Allotment.Pane>
                <ReadOnlyPane
                  title="Incoming (Theirs)"
                  content={conflictFile?.incoming ?? ""}
                  language={language}
                  highlightClass="bg-blue-900/40 border-b border-blue-800/50"
                  label="Theirs"
                  theme={theme}
                />
              </Allotment.Pane>
            </Allotment>
          </Allotment.Pane>

          {/* Bottom: merged result (editable) */}
          <Allotment.Pane>
            <div className="flex h-full flex-col">
              <div className="flex items-center gap-1.5 border-b border-gray-700 bg-[#252526] px-3 py-1 shrink-0">
                <span className="text-[11px] font-semibold text-white/90">Result</span>
                <span className="ml-auto text-[10px] text-gray-500">Editable</span>
              </div>
              <div className="flex-1 min-h-0">
                <Editor
                  value={mergedContent}
                  language={language}
                  theme={theme}
                  onChange={handleEditorChange}
                  onMount={handleEditorMount}
                  options={{
                    minimap: { enabled: false },
                    lineNumbers: "on",
                    scrollBeyondLastLine: false,
                    automaticLayout: true,
                    fontSize: 12,
                    padding: { top: 4 },
                  }}
                />
              </div>
            </div>
          </Allotment.Pane>
        </Allotment>
      </div>
    </div>
  );
}
