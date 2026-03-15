import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Archive,
  Check,
  CherryIcon,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  FolderGit2,
  GitBranch,
  GitCommitHorizontal,
  GitGraph as GitGraphIcon,
  GitMerge,
  GitPullRequest,
  List,
  Loader2,
  Minus,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeGit, nativeFs } from "../../lib/electronBridge";
import { generateCommitMessage } from "../../lib/aiCodeActions";
import GitGraph from "./GitGraph";
import GitHubPanel from "./GitHubPanel";

// ─── Types ──────────────────────────────────────────────────────────────

interface GitFileStatus {
  path: string;
  oldPath?: string;
  indexStatus: string;
  workTreeStatus: string;
  isStaged: boolean;
  isUntracked: boolean;
}

interface GitCommit {
  hash: string;
  author: string;
  email: string;
  timestamp: number;
  subject: string;
}

interface BranchInfo {
  name: string;
  isCurrent: boolean;
  upstream: string;
  isRemote: boolean;
}

// ─── Parsers ────────────────────────────────────────────────────────────

function parseStatus(porcelain: string): GitFileStatus[] {
  if (!porcelain.trim()) return [];
  return porcelain
    .split("\n")
    .filter((l) => l.length >= 3)
    .map((line) => {
      const ix = line[0];
      const wt = line[1];
      let rawPath = line.slice(3);
      let oldPath: string | undefined;

      const arrow = rawPath.indexOf(" -> ");
      if (arrow !== -1) {
        oldPath = rawPath.slice(0, arrow);
        rawPath = rawPath.slice(arrow + 4);
      }
      if (rawPath.startsWith('"') && rawPath.endsWith('"')) {
        rawPath = rawPath.slice(1, -1);
      }

      const isUntracked = ix === "?" && wt === "?";
      const isStaged = !isUntracked && ix !== " " && ix !== "?";

      return { path: rawPath, oldPath, indexStatus: ix, workTreeStatus: wt, isStaged, isUntracked };
    });
}

function parseLog(output: string): GitCommit[] {
  if (!output.trim()) return [];
  return output
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [hash, author, email, ts, ...rest] = line.split("|");
      return { hash, author, email, timestamp: parseInt(ts, 10), subject: rest.join("|") };
    })
    .filter((c) => c.hash);
}

function parseBranch(output: string): string {
  for (const line of output.split("\n")) {
    if (line.startsWith("* ")) return line.slice(2).trim();
  }
  return "";
}

function parseBranchList(output: string): BranchInfo[] {
  if (!output.trim()) return [];
  return output
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [name, head, upstream] = line.split("|");
      const trimmedName = name.trim();
      return {
        name: trimmedName,
        isCurrent: head.trim() === "*",
        upstream: upstream?.trim() ?? "",
        isRemote: trimmedName.startsWith("origin/"),
      };
    })
    .filter((b) => b.name && b.name !== "origin/HEAD");
}

interface StashEntry {
  index: number;
  ref: string;
  message: string;
  date: string;
}

function parseStashList(output: string): StashEntry[] {
  if (!output.trim()) return [];
  return output
    .split("\n")
    .filter(Boolean)
    .map((line, i) => {
      const [ref, message, date] = line.split("|");
      return {
        index: i,
        ref: ref?.trim() ?? `stash@{${i}}`,
        message: message?.trim() ?? "",
        date: date?.trim() ?? "",
      };
    });
}

function parseAheadBehind(output: string): { ahead: number; behind: number } {
  const parts = output.trim().split(/\s+/);
  if (parts.length >= 2) {
    return { ahead: parseInt(parts[0], 10) || 0, behind: parseInt(parts[1], 10) || 0 };
  }
  return { ahead: 0, behind: 0 };
}

// ─── Helpers ────────────────────────────────────────────────────────────

function basename(p: string): string {
  return p.split("/").filter(Boolean).pop() ?? p;
}

function parentDir(p: string): string {
  const parts = p.split("/");
  parts.pop();
  return parts.join("/");
}

function relativeTime(ts: number): string {
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  if (diff < 2592000) return `${Math.floor(diff / 604800)}w ago`;
  return `${Math.floor(diff / 2592000)}mo ago`;
}

const BADGE_BG: Record<string, string> = {
  M: "bg-amber-500",
  A: "bg-green-500",
  D: "bg-red-500",
  R: "bg-teal-500",
  C: "bg-purple-500",
  U: "bg-blue-500",
  "?": "bg-gray-500",
};

// ─── Status Badge ───────────────────────────────────────────────────────

function StatusBadge({ code }: { code: string }) {
  return (
    <span
      className={`inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded text-[10px] font-bold leading-none text-white ${BADGE_BG[code] ?? "bg-gray-500"}`}
    >
      {code}
    </span>
  );
}

// ─── File Change Item ───────────────────────────────────────────────────

interface FileItemProps {
  file: GitFileStatus;
  cwd: string;
  staged: boolean;
  onStage: (path: string) => void;
  onUnstage: (path: string) => void;
  onOpenDiff: (file: GitFileStatus) => void;
}

const FileChangeItem = React.memo(function FileChangeItem({
  file,
  cwd,
  staged,
  onStage,
  onUnstage,
  onOpenDiff,
}: FileItemProps) {
  const openFile = useCodeStore((s) => s.openFile);
  const code = staged ? file.indexStatus : file.workTreeStatus;
  const name = basename(file.path);
  const dir = parentDir(file.path);

  const handleOpen = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const content = await nativeFs.readFile(`${cwd}/${file.path}`);
    openFile(`${cwd}/${file.path}`, content ?? "");
  };

  const hoverBtn =
    "rounded p-0.5 text-gray-400 hover:bg-gray-600 hover:text-white transition-colors";

  return (
    <div
      className="group flex cursor-pointer items-center gap-1.5 px-3 py-[3px] text-xs hover:bg-gray-700/50"
      onClick={() => onOpenDiff(file)}
    >
      <StatusBadge code={code} />
      <span className="truncate text-gray-200">{name}</span>
      {dir && <span className="min-w-0 truncate text-[11px] text-gray-500">{dir}</span>}

      <div className="ml-auto flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        <button className={hoverBtn} title="Open File" onClick={handleOpen}>
          <Eye size={13} />
        </button>
        {!staged && !file.isUntracked && (
          <button
            className={hoverBtn}
            title="Discard Changes"
            onClick={(e) => e.stopPropagation()}
          >
            <Undo2 size={13} />
          </button>
        )}
        {staged ? (
          <button
            className={hoverBtn}
            title="Unstage"
            onClick={(e) => {
              e.stopPropagation();
              onUnstage(file.path);
            }}
          >
            <Minus size={13} />
          </button>
        ) : (
          <button
            className={hoverBtn}
            title="Stage"
            onClick={(e) => {
              e.stopPropagation();
              onStage(file.path);
            }}
          >
            <Plus size={13} />
          </button>
        )}
      </div>
    </div>
  );
});

// ─── Collapsible Section ────────────────────────────────────────────────

function Section({
  title,
  count,
  defaultOpen = true,
  actions,
  children,
}: {
  title: string;
  count: number;
  defaultOpen?: boolean;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div>
      <div
        className="flex cursor-pointer select-none items-center gap-1 px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400 hover:text-gray-300"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="truncate">{title}</span>
        <span className="ml-1 rounded-full bg-gray-700 px-1.5 text-[10px] font-medium text-gray-300">
          {count}
        </span>
        {actions && (
          <div className="ml-auto flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
            {actions}
          </div>
        )}
      </div>
      {open && children}
    </div>
  );
}

// ─── Commit Item ────────────────────────────────────────────────────────

function CommitItem({
  commit,
  cwd: _cwd,
  onCherryPick,
}: {
  commit: GitCommit;
  cwd: string;
  onCherryPick: (hash: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className="group cursor-pointer px-3 py-1.5 text-xs hover:bg-gray-700/50"
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="flex items-center gap-1.5">
        <GitCommitHorizontal size={12} className="shrink-0 text-gray-500" />
        <span className="font-mono text-blue-400">{commit.hash.slice(0, 7)}</span>
        <span className="truncate text-gray-300">{commit.subject}</span>
        <div className="ml-auto flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
          <button
            className="rounded p-0.5 text-gray-400 hover:bg-gray-600 hover:text-white transition-colors"
            title="Cherry-pick this commit"
            onClick={(e) => {
              e.stopPropagation();
              onCherryPick(commit.hash);
            }}
          >
            <CherryIcon size={12} />
          </button>
          <button
            className="rounded p-0.5 text-gray-400 hover:bg-gray-600 hover:text-white transition-colors"
            title="Copy commit hash"
            onClick={(e) => {
              e.stopPropagation();
              navigator.clipboard.writeText(commit.hash);
            }}
          >
            <Copy size={12} />
          </button>
        </div>
      </div>
      <div className="mt-0.5 flex items-center gap-2 pl-[18px] text-[10px] text-gray-500">
        <span>{commit.author}</span>
        <span>&middot;</span>
        <span>{relativeTime(commit.timestamp)}</span>
      </div>
      {expanded && (
        <div className="mt-1 select-all pl-[18px] font-mono text-[10px] text-gray-500">
          {commit.hash}
        </div>
      )}
    </div>
  );
}

// ─── Stash Item ─────────────────────────────────────────────────────────

function StashItem({
  stash,
  onApply,
  onPop,
  onDrop,
  onView,
}: {
  stash: StashEntry;
  onApply: (index: number) => void;
  onPop: (index: number) => void;
  onDrop: (index: number) => void;
  onView: (index: number) => void;
}) {
  const hoverBtn = "rounded p-0.5 text-gray-400 hover:bg-gray-600 hover:text-white transition-colors";

  return (
    <div className="group flex items-center gap-1.5 px-3 py-[5px] text-xs hover:bg-gray-700/50">
      <Archive size={12} className="shrink-0 text-gray-500" />
      <span className="font-mono text-[10px] text-purple-400">{stash.ref}</span>
      <span className="min-w-0 truncate text-gray-300">{stash.message}</span>
      <div className="ml-auto flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        <button className={hoverBtn} title="View stash diff" onClick={() => onView(stash.index)}>
          <Eye size={12} />
        </button>
        <button className={hoverBtn} title="Apply (keep stash)" onClick={() => onApply(stash.index)}>
          <Play size={12} />
        </button>
        <button className={hoverBtn} title="Pop (apply & remove)" onClick={() => onPop(stash.index)}>
          <Check size={12} />
        </button>
        <button className={hoverBtn} title="Drop stash" onClick={() => onDrop(stash.index)}>
          <Trash2 size={12} />
        </button>
      </div>
      {stash.date && (
        <span className="shrink-0 text-[10px] text-gray-600">{stash.date.split(" ").slice(0, 1).join("")}</span>
      )}
    </div>
  );
}

// ─── Branch Dropdown ────────────────────────────────────────────────────

function BranchDropdown({
  branches,
  currentBranch: _currentBranch,
  onCheckout,
  onCreate,
  onClose,
}: {
  branches: BranchInfo[];
  currentBranch: string;
  onCheckout: (name: string) => void;
  onCreate: (name: string) => void;
  onClose: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const keyHandler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", keyHandler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", keyHandler);
    };
  }, [onClose]);

  const lowerFilter = filter.toLowerCase();
  const local = branches.filter((b) => !b.isRemote && b.name.toLowerCase().includes(lowerFilter));
  const remote = branches.filter((b) => b.isRemote && b.name.toLowerCase().includes(lowerFilter));

  const handleCreate = () => {
    const trimmed = newName.trim();
    if (trimmed) {
      onCreate(trimmed);
      onClose();
    }
  };

  return (
    <div
      ref={dropdownRef}
      className="absolute left-0 top-full z-50 mt-1 max-h-[320px] w-64 overflow-hidden rounded-md border border-gray-700 bg-gray-800 shadow-xl"
    >
      <div className="border-b border-gray-700 p-1.5">
        <div className="flex items-center gap-1.5 rounded border border-gray-600 bg-gray-900 px-2 py-1">
          <Search size={12} className="shrink-0 text-gray-500" />
          <input
            ref={inputRef}
            className="flex-1 bg-transparent text-xs text-white placeholder-gray-500 outline-none"
            placeholder="Filter branches…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      </div>

      <div className="max-h-[220px] overflow-y-auto [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-600 [&::-webkit-scrollbar]:w-1">
        {local.length > 0 && (
          <div>
            <div className="px-2.5 pt-2 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-gray-500">
              Local
            </div>
            {local.map((b) => (
              <button
                key={b.name}
                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-xs hover:bg-gray-700/60"
                onClick={() => {
                  if (!b.isCurrent) onCheckout(b.name);
                  onClose();
                }}
              >
                {b.isCurrent ? (
                  <Check size={12} className="shrink-0 text-green-400" />
                ) : (
                  <span className="w-3 shrink-0" />
                )}
                <span className={b.isCurrent ? "text-green-300" : "text-gray-200"}>{b.name}</span>
              </button>
            ))}
          </div>
        )}

        {remote.length > 0 && (
          <div>
            <div className="px-2.5 pt-2 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-gray-500">
              Remote
            </div>
            {remote.map((b) => (
              <button
                key={b.name}
                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-xs text-gray-500 hover:bg-gray-700/60 hover:text-gray-300"
                onClick={() => {
                  onCheckout(b.name);
                  onClose();
                }}
              >
                <span className="w-3 shrink-0" />
                <span>{b.name}</span>
              </button>
            ))}
          </div>
        )}

        {local.length === 0 && remote.length === 0 && (
          <div className="px-3 py-3 text-center text-xs text-gray-500">No branches found</div>
        )}
      </div>

      <div className="border-t border-gray-700 p-1.5">
        {showCreate ? (
          <div className="flex items-center gap-1">
            <input
              className="flex-1 rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-white placeholder-gray-500 outline-none focus:border-blue-500"
              placeholder="New branch name…"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreate();
                if (e.key === "Escape") setShowCreate(false);
              }}
              autoFocus
            />
            <button
              className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-500 disabled:opacity-40"
              onClick={handleCreate}
              disabled={!newName.trim()}
            >
              Create
            </button>
            <button
              className="rounded p-1 text-gray-400 hover:bg-gray-700 hover:text-white"
              onClick={() => setShowCreate(false)}
            >
              <X size={12} />
            </button>
          </div>
        ) : (
          <button
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-gray-400 hover:bg-gray-700/60 hover:text-gray-200"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={12} />
            Create new branch…
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Main Component ─────────────────────────────────────────────────────

export default function GitPanel() {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const openDiff = useCodeStore((s) => s.openDiff);
  const cwd = pinnedRoots[0] ?? "";

  const [loading, setLoading] = useState(false);
  const [files, setFiles] = useState<GitFileStatus[]>([]);
  const [branch, setBranch] = useState("");
  const [commits, setCommits] = useState<GitCommit[]>([]);
  const [commitMsg, setCommitMsg] = useState("");
  const [committing, setCommitting] = useState(false);
  const [isRepo, setIsRepo] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [pushing, setPushing] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [ahead, setAhead] = useState(0);
  const [behind, setBehind] = useState(0);
  const [syncFeedback, setSyncFeedback] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [branchDropdownOpen, setBranchDropdownOpen] = useState(false);
  const [checkingOut, setCheckingOut] = useState(false);
  const [generatingMsg, setGeneratingMsg] = useState(false);

  const [stashes, setStashes] = useState<StashEntry[]>([]);
  const [stashMsg, setStashMsg] = useState("");
  const [stashing, setStashing] = useState(false);
  const [showGraphView, setShowGraphView] = useState(false);

  const [conflictedFiles, setConflictedFiles] = useState<string[]>([]);
  const [_showRebase, _setShowRebase] = useState(false);
  const [rebaseInProgress, setRebaseInProgress] = useState(false);
  const [showGitHub, setShowGitHub] = useState(false);

  const feedbackTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const showFeedback = useCallback((type: "success" | "error", msg: string) => {
    setSyncFeedback({ type, msg });
    clearTimeout(feedbackTimer.current);
    feedbackTimer.current = setTimeout(() => setSyncFeedback(null), 4000);
  }, []);

  const refreshAheadBehind = useCallback(async () => {
    if (!cwd) return;
    try {
      const res = await nativeGit.aheadBehind(cwd);
      if (res.code === 0) {
        const { ahead: a, behind: b } = parseAheadBehind(res.stdout);
        setAhead(a);
        setBehind(b);
      } else {
        setAhead(0);
        setBehind(0);
      }
    } catch {
      setAhead(0);
      setBehind(0);
    }
  }, [cwd]);

  const refreshConflicts = useCallback(async () => {
    if (!cwd) return;
    try {
      const res = await nativeGit.conflictFiles(cwd);
      if (res.code === 0) {
        setConflictedFiles(res.stdout.split("\n").filter(Boolean));
      } else {
        setConflictedFiles([]);
      }
    } catch {
      setConflictedFiles([]);
    }
  }, [cwd]);

  const refreshRebaseStatus = useCallback(async () => {
    if (!cwd) return;
    try {
      const res = await nativeGit.rebaseStatus(cwd);
      setRebaseInProgress(res.code === 0 && res.stdout.trim() === "true");
    } catch {
      setRebaseInProgress(false);
    }
  }, [cwd]);

  const handleOpenMergeEditor = useCallback((conflictPath: string) => {
    window.dispatchEvent(
      new CustomEvent("codemode:openMergeEditor", {
        detail: { cwd, filePath: conflictPath },
      }),
    );
  }, [cwd]);

  const handleOpenInteractiveRebase = useCallback(() => {
    window.dispatchEvent(
      new CustomEvent("codemode:openInteractiveRebase", {
        detail: { cwd },
      }),
    );
  }, [cwd]);

  const refresh = useCallback(async () => {
    if (!cwd) return;
    setLoading(true);
    setError(null);
    try {
      const [statusRes, branchRes, logRes] = await Promise.all([
        nativeGit.status(cwd),
        nativeGit.branch(cwd),
        nativeGit.log(cwd, 20),
      ]);

      if (statusRes.code !== 0 && statusRes.stderr.includes("not a git repository")) {
        setIsRepo(false);
        setFiles([]);
        setBranch("");
        setCommits([]);
        return;
      }

      setIsRepo(true);
      setFiles(parseStatus(statusRes.stdout));
      setBranch(parseBranch(branchRes.stdout));
      setCommits(parseLog(logRes.stdout));

      refreshAheadBehind();
      refreshConflicts();
      refreshRebaseStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to refresh");
    } finally {
      setLoading(false);
    }
  }, [cwd, refreshAheadBehind, refreshConflicts, refreshRebaseStatus]);

  const loadBranches = useCallback(async () => {
    if (!cwd) return;
    try {
      const res = await nativeGit.branchList(cwd);
      if (res.code === 0) {
        setBranches(parseBranchList(res.stdout));
      }
    } catch {
      /* non-critical */
    }
  }, [cwd]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, [refresh]);

  const { staged, unstaged, untracked } = useMemo(() => {
    const staged: GitFileStatus[] = [];
    const unstaged: GitFileStatus[] = [];
    const untracked: GitFileStatus[] = [];
    for (const f of files) {
      if (f.isUntracked) {
        untracked.push(f);
      } else {
        if (f.indexStatus !== " " && f.indexStatus !== "?") staged.push(f);
        if (f.workTreeStatus !== " " && f.workTreeStatus !== "?") unstaged.push(f);
      }
    }
    return { staged, unstaged, untracked };
  }, [files]);

  const handleStage = useCallback(
    async (path: string) => {
      await nativeGit.stage(cwd, path);
      refresh();
    },
    [cwd, refresh],
  );

  const handleUnstage = useCallback(
    async (path: string) => {
      await nativeGit.unstage(cwd, path);
      refresh();
    },
    [cwd, refresh],
  );

  const stageMany = useCallback(
    async (list: GitFileStatus[]) => {
      for (const f of list) await nativeGit.stage(cwd, f.path);
      refresh();
    },
    [cwd, refresh],
  );

  const unstageAll = useCallback(async () => {
    for (const f of staged) await nativeGit.unstage(cwd, f.path);
    refresh();
  }, [cwd, staged, refresh]);

  const handleCommit = useCallback(async () => {
    if (!commitMsg.trim() || staged.length === 0) return;
    setCommitting(true);
    try {
      const res = await nativeGit.commit(cwd, commitMsg.trim());
      if (res.code === 0) {
        setCommitMsg("");
        refresh();
      } else {
        setError(res.stderr || "Commit failed");
      }
    } finally {
      setCommitting(false);
    }
  }, [cwd, commitMsg, staged.length, refresh]);

  const handlePush = useCallback(async () => {
    if (pushing) return;
    setPushing(true);
    setSyncFeedback(null);
    try {
      const res = await nativeGit.push(cwd);
      if (res.code === 0) {
        showFeedback("success", "Pushed successfully");
        refresh();
      } else {
        showFeedback("error", res.stderr?.split("\n")[0] || "Push failed");
      }
    } catch (e) {
      showFeedback("error", e instanceof Error ? e.message : "Push failed");
    } finally {
      setPushing(false);
    }
  }, [cwd, pushing, refresh, showFeedback]);

  const handlePull = useCallback(async () => {
    if (pulling) return;
    setPulling(true);
    setSyncFeedback(null);
    try {
      const res = await nativeGit.pull(cwd);
      if (res.code === 0) {
        showFeedback("success", "Pulled successfully");
        refresh();
      } else {
        showFeedback("error", res.stderr?.split("\n")[0] || "Pull failed");
      }
    } catch (e) {
      showFeedback("error", e instanceof Error ? e.message : "Pull failed");
    } finally {
      setPulling(false);
    }
  }, [cwd, pulling, refresh, showFeedback]);

  const handleCheckout = useCallback(
    async (branchName: string) => {
      setCheckingOut(true);
      try {
        const res = await nativeGit.checkout(cwd, branchName);
        if (res.code === 0) {
          refresh();
        } else {
          setError(res.stderr?.split("\n")[0] || "Checkout failed");
        }
      } finally {
        setCheckingOut(false);
      }
    },
    [cwd, refresh],
  );

  const handleCreateBranch = useCallback(
    async (branchName: string) => {
      setCheckingOut(true);
      try {
        const res = await nativeGit.createBranch(cwd, branchName);
        if (res.code === 0) {
          refresh();
        } else {
          setError(res.stderr?.split("\n")[0] || "Failed to create branch");
        }
      } finally {
        setCheckingOut(false);
      }
    },
    [cwd, refresh],
  );

  const toggleBranchDropdown = useCallback(() => {
    setBranchDropdownOpen((v) => {
      if (!v) loadBranches();
      return !v;
    });
  }, [loadBranches]);

  const handleOpenDiff = useCallback(
    async (file: GitFileStatus) => {
      const fullPath = `${cwd}/${file.path}`;

      let original = "";
      if (!file.isUntracked) {
        try {
          const r = await nativeGit.fileShow(cwd, "HEAD", file.path);
          if (r.code === 0) original = r.stdout;
        } catch {
          /* new file in index */
        }
      }

      let modified = "";
      try {
        const content = await nativeFs.readFile(fullPath);
        if (content !== null) modified = content;
      } catch {
        /* deleted file */
      }

      openDiff(original, modified, `${file.path} (HEAD)`, file.path);
    },
    [cwd, openDiff],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        handleCommit();
      }
    },
    [handleCommit],
  );

  const handleGenerateCommitMsg = useCallback(async () => {
    if (generatingMsg || !cwd) return;
    setGeneratingMsg(true);
    try {
      const diffRes = await nativeGit.diffStaged(cwd);
      let diff = diffRes.stdout;
      if (!diff.trim()) {
        const unstaged = await nativeGit.diff(cwd);
        diff = unstaged.stdout;
      }
      if (!diff.trim()) {
        setGeneratingMsg(false);
        return;
      }
      const msg = await generateCommitMessage(diff.slice(0, 8000));
      if (msg) setCommitMsg(msg);
    } catch {
      /* non-critical */
    } finally {
      setGeneratingMsg(false);
    }
  }, [cwd, generatingMsg]);

  // ── Stash operations ──

  const refreshStashes = useCallback(async () => {
    if (!cwd) return;
    try {
      const res = await nativeGit.stashList(cwd);
      if (res.code === 0) {
        setStashes(parseStashList(res.stdout));
      }
    } catch { /* non-critical */ }
  }, [cwd]);

  useEffect(() => {
    refreshStashes();
  }, [refreshStashes]);

  const handleStashPush = useCallback(async () => {
    if (stashing || !cwd) return;
    setStashing(true);
    try {
      const res = await nativeGit.stash(cwd, stashMsg.trim() || undefined);
      if (res.code === 0) {
        setStashMsg("");
        showFeedback("success", "Changes stashed");
        refresh();
        refreshStashes();
      } else {
        showFeedback("error", res.stderr?.split("\n")[0] || "Stash failed");
      }
    } catch (e) {
      showFeedback("error", e instanceof Error ? e.message : "Stash failed");
    } finally {
      setStashing(false);
    }
  }, [cwd, stashMsg, stashing, refresh, refreshStashes, showFeedback]);

  const handleStashApply = useCallback(async (index: number) => {
    try {
      const res = await nativeGit.stashApply(cwd, index);
      if (res.code === 0) {
        showFeedback("success", "Stash applied");
        refresh();
      } else {
        showFeedback("error", res.stderr?.split("\n")[0] || "Apply failed");
      }
    } catch (e) {
      showFeedback("error", e instanceof Error ? e.message : "Apply failed");
    }
  }, [cwd, refresh, showFeedback]);

  const handleStashPop = useCallback(async (index: number) => {
    try {
      const res = await nativeGit.stashPop(cwd, index);
      if (res.code === 0) {
        showFeedback("success", "Stash popped");
        refresh();
        refreshStashes();
      } else {
        showFeedback("error", res.stderr?.split("\n")[0] || "Pop failed");
      }
    } catch (e) {
      showFeedback("error", e instanceof Error ? e.message : "Pop failed");
    }
  }, [cwd, refresh, refreshStashes, showFeedback]);

  const handleStashDrop = useCallback(async (index: number) => {
    try {
      const res = await nativeGit.stashDrop(cwd, index);
      if (res.code === 0) {
        showFeedback("success", "Stash dropped");
        refreshStashes();
      } else {
        showFeedback("error", res.stderr?.split("\n")[0] || "Drop failed");
      }
    } catch (e) {
      showFeedback("error", e instanceof Error ? e.message : "Drop failed");
    }
  }, [cwd, refreshStashes, showFeedback]);

  const handleStashView = useCallback(async (index: number) => {
    try {
      const res = await nativeGit.stashShow(cwd, index);
      if (res.code === 0) {
        openDiff("", res.stdout, `stash@{${index}} diff`, `stash@{${index}}`);
      } else {
        showFeedback("error", res.stderr?.split("\n")[0] || "Could not show stash");
      }
    } catch (e) {
      showFeedback("error", e instanceof Error ? e.message : "Show failed");
    }
  }, [cwd, openDiff, showFeedback]);

  // ── Cherry-pick ──

  const handleCherryPick = useCallback(async (hash: string) => {
    try {
      const res = await nativeGit.cherryPick(cwd, hash);
      if (res.code === 0) {
        showFeedback("success", `Cherry-picked ${hash.slice(0, 7)}`);
        refresh();
      } else {
        showFeedback("error", res.stderr?.split("\n")[0] || "Cherry-pick failed (conflict?)");
      }
    } catch (e) {
      showFeedback("error", e instanceof Error ? e.message : "Cherry-pick failed");
    }
  }, [cwd, refresh, showFeedback]);

  // ── Graph view commit click ──

  const handleGraphViewCommit = useCallback(async (hash: string) => {
    try {
      const res = await nativeGit.fileShow(cwd, hash, "");
      if (res.code === 0) {
        openDiff("", res.stdout, `${hash.slice(0, 7)} (parent)`, hash.slice(0, 7));
      }
    } catch { /* non-critical */ }
  }, [cwd, openDiff]);

  // ── Empty / not-a-repo states ──

  if (!cwd) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-3 bg-gray-900 px-4 text-center">
        <FolderGit2 size={32} className="text-gray-600" />
        <p className="text-sm text-gray-400">Open a folder to view source control</p>
      </div>
    );
  }

  const headerBtn = "rounded p-1 text-gray-500 hover:bg-gray-700/60 hover:text-gray-300 transition-colors";
  const sectionAction = "rounded p-0.5 text-gray-400 hover:bg-gray-600 hover:text-white transition-colors";

  const commitEnabled = commitMsg.trim().length > 0 && staged.length > 0 && !committing;

  const noChanges = !loading && staged.length === 0 && unstaged.length === 0 && untracked.length === 0;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-gray-900 text-gray-300">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-800 px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">
          Source Control
        </span>
        <div className="flex items-center gap-1">
          {/* Branch switcher */}
          {branch && (
            <div className="relative">
              <button
                className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-gray-400 hover:bg-gray-700/60 hover:text-gray-300 transition-colors"
                onClick={toggleBranchDropdown}
                disabled={checkingOut}
                title="Switch branch"
              >
                {checkingOut ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <GitBranch size={12} />
                )}
                <span className="max-w-[80px] truncate">{branch}</span>
                <ChevronDown size={10} />
              </button>

              {/* Ahead/behind indicators */}
              {(ahead > 0 || behind > 0) && (
                <span className="ml-0.5 inline-flex items-center gap-1 text-[10px]">
                  {ahead > 0 && <span className="text-green-400">↑{ahead}</span>}
                  {behind > 0 && <span className="text-yellow-400">↓{behind}</span>}
                </span>
              )}

              {branchDropdownOpen && (
                <BranchDropdown
                  branches={branches}
                  currentBranch={branch}
                  onCheckout={handleCheckout}
                  onCreate={handleCreateBranch}
                  onClose={() => setBranchDropdownOpen(false)}
                />
              )}
            </div>
          )}

          {/* Push */}
          <button
            className={`${headerBtn} ${ahead > 0 ? "text-green-400 hover:text-green-300" : ""}`}
            title={`Push${ahead > 0 ? ` (${ahead} ahead)` : ""}`}
            onClick={handlePush}
            disabled={pushing || pulling}
          >
            {pushing ? <Loader2 size={14} className="animate-spin" /> : <ArrowUp size={14} />}
          </button>

          {/* Pull */}
          <button
            className={`${headerBtn} ${behind > 0 ? "text-yellow-400 hover:text-yellow-300" : ""}`}
            title={`Pull${behind > 0 ? ` (${behind} behind)` : ""}`}
            onClick={handlePull}
            disabled={pulling || pushing}
          >
            {pulling ? <Loader2 size={14} className="animate-spin" /> : <ArrowDown size={14} />}
          </button>

          {/* Interactive Rebase */}
          <button
            className={headerBtn}
            title="Interactive Rebase..."
            onClick={handleOpenInteractiveRebase}
          >
            <RotateCcw size={14} />
          </button>

          {/* GitHub toggle */}
          <button
            className={`${headerBtn} ${showGitHub ? "text-purple-400 hover:text-purple-300" : ""}`}
            title={showGitHub ? "Local Git" : "GitHub PRs & Issues"}
            onClick={() => setShowGitHub((v) => !v)}
          >
            <GitPullRequest size={14} />
          </button>

          {/* Graph / List toggle */}
          <button
            className={`${headerBtn} ${showGraphView ? "text-blue-400 hover:text-blue-300" : ""}`}
            title={showGraphView ? "List view" : "Graph view"}
            onClick={() => setShowGraphView((v) => !v)}
          >
            {showGraphView ? <List size={14} /> : <GitGraphIcon size={14} />}
          </button>

          {/* Refresh */}
          <button className={headerBtn} title="Refresh" onClick={refresh} disabled={loading}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Push/Pull feedback */}
      {syncFeedback && (
        <div
          className={`mx-2 mt-1.5 rounded px-2.5 py-1 text-xs ${
            syncFeedback.type === "success"
              ? "bg-green-900/40 text-green-300"
              : "bg-red-900/40 text-red-300"
          }`}
        >
          {syncFeedback.msg}
        </div>
      )}

      {showGitHub ? (
        <GitHubPanel />
      ) : !isRepo ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
          <FolderGit2 size={32} className="text-gray-600" />
          <p className="text-sm text-gray-400">Not a git repository</p>
          <p className="text-xs text-gray-500">Initialize a repository to use source control.</p>
        </div>
      ) : showGraphView ? (
        <GitGraph onViewCommit={handleGraphViewCommit} />
      ) : (
        <div className="flex flex-1 flex-col overflow-y-auto [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-700 [&::-webkit-scrollbar]:w-1.5">
          {/* Commit input */}
          <div className="border-b border-gray-800 p-2">
            <div className="relative">
              <textarea
                className="w-full resize-none rounded border border-gray-700 bg-gray-800 px-2.5 py-1.5 pr-8 text-xs text-white placeholder-gray-500 focus:border-blue-500 focus:outline-none"
                placeholder="Message (Ctrl+Enter to commit)"
                rows={3}
                value={commitMsg}
                onChange={(e) => setCommitMsg(e.target.value)}
                onKeyDown={handleKeyDown}
              />
              <button
                className="absolute top-1.5 right-1.5 p-1 rounded text-gray-500 hover:text-purple-400 hover:bg-purple-500/10 transition-colors disabled:opacity-30"
                onClick={handleGenerateCommitMsg}
                disabled={generatingMsg || (staged.length === 0 && unstaged.length === 0 && untracked.length === 0)}
                title="Generate commit message with AI"
              >
                {generatingMsg ? (
                  <Loader2 size={13} className="animate-spin text-purple-400" />
                ) : (
                  <Sparkles size={13} />
                )}
              </button>
            </div>
            <button
              className={`mt-1.5 flex w-full items-center justify-center gap-1.5 rounded py-1.5 text-xs font-medium transition ${
                commitEnabled
                  ? "bg-blue-600 text-white hover:bg-blue-500"
                  : "cursor-not-allowed bg-blue-600/40 text-white/50"
              }`}
              onClick={handleCommit}
              disabled={!commitEnabled}
            >
              {committing ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
              Commit
            </button>
          </div>

          {error && (
            <div className="mx-2 mt-2 rounded bg-red-900/40 px-2.5 py-1.5 text-xs text-red-300">
              {error}
            </div>
          )}

          {/* Rebase in progress */}
          {rebaseInProgress && (
            <div className="mx-2 mt-2 flex items-center gap-2 rounded bg-yellow-900/40 px-2.5 py-1.5 text-xs text-yellow-300">
              <RotateCcw size={12} className="shrink-0" />
              <span className="flex-1">Rebase in progress</span>
              <button
                className="rounded bg-yellow-600 px-2 py-0.5 text-[10px] font-medium text-white hover:bg-yellow-500"
                onClick={handleOpenInteractiveRebase}
              >
                Manage
              </button>
            </div>
          )}

          {/* Merge Conflicts */}
          {conflictedFiles.length > 0 && (
            <Section
              title="Merge Conflicts"
              count={conflictedFiles.length}
              actions={
                <AlertTriangle size={14} className="text-red-400" />
              }
            >
              {conflictedFiles.map((fp) => (
                <div
                  key={fp}
                  className="group flex items-center gap-1.5 px-3 py-[5px] text-xs hover:bg-gray-700/50"
                >
                  <GitMerge size={12} className="shrink-0 text-red-400" />
                  <span className="min-w-0 truncate text-gray-200">{fp.split("/").pop()}</span>
                  {fp.includes("/") && (
                    <span className="min-w-0 truncate text-[10px] text-gray-500">
                      {fp.split("/").slice(0, -1).join("/")}
                    </span>
                  )}
                  <button
                    className="ml-auto shrink-0 rounded bg-orange-600 px-2 py-0.5 text-[10px] font-medium text-white opacity-0 transition-opacity hover:bg-orange-500 group-hover:opacity-100"
                    onClick={() => handleOpenMergeEditor(fp)}
                  >
                    Resolve
                  </button>
                </div>
              ))}
            </Section>
          )}

          {loading && files.length === 0 && (
            <div className="flex items-center gap-2 px-3 py-4 text-xs text-gray-500">
              <Loader2 size={14} className="animate-spin" />
              Loading changes…
            </div>
          )}

          {/* Staged Changes */}
          {staged.length > 0 && (
            <Section
              title="Staged Changes"
              count={staged.length}
              actions={
                <button className={sectionAction} title="Unstage All" onClick={unstageAll}>
                  <Minus size={14} />
                </button>
              }
            >
              {staged.map((f) => (
                <FileChangeItem
                  key={`s-${f.path}`}
                  file={f}
                  cwd={cwd}
                  staged
                  onStage={handleStage}
                  onUnstage={handleUnstage}
                  onOpenDiff={handleOpenDiff}
                />
              ))}
            </Section>
          )}

          {/* Changes (unstaged) */}
          {unstaged.length > 0 && (
            <Section
              title="Changes"
              count={unstaged.length}
              actions={
                <button
                  className={sectionAction}
                  title="Stage All"
                  onClick={() => stageMany(unstaged)}
                >
                  <Plus size={14} />
                </button>
              }
            >
              {unstaged.map((f) => (
                <FileChangeItem
                  key={`u-${f.path}`}
                  file={f}
                  cwd={cwd}
                  staged={false}
                  onStage={handleStage}
                  onUnstage={handleUnstage}
                  onOpenDiff={handleOpenDiff}
                />
              ))}
            </Section>
          )}

          {/* Untracked */}
          {untracked.length > 0 && (
            <Section
              title="Untracked"
              count={untracked.length}
              actions={
                <button
                  className={sectionAction}
                  title="Stage All Untracked"
                  onClick={() => stageMany(untracked)}
                >
                  <Plus size={14} />
                </button>
              }
            >
              {untracked.map((f) => (
                <FileChangeItem
                  key={`t-${f.path}`}
                  file={f}
                  cwd={cwd}
                  staged={false}
                  onStage={handleStage}
                  onUnstage={handleUnstage}
                  onOpenDiff={handleOpenDiff}
                />
              ))}
            </Section>
          )}

          {noChanges && (
            <div className="px-3 py-4 text-center text-xs text-gray-500">No changes detected</div>
          )}

          {/* Commit History */}
          {commits.length > 0 && (
            <Section title="Commit History" count={commits.length} defaultOpen={false}>
              {commits.map((c) => (
                <CommitItem key={c.hash} commit={c} cwd={cwd} onCherryPick={handleCherryPick} />
              ))}
            </Section>
          )}

          {/* Stashes */}
          <Section title="Stashes" count={stashes.length} defaultOpen={false}>
            <div className="border-b border-gray-800/50 px-3 py-1.5">
              <div className="flex items-center gap-1">
                <input
                  className="flex-1 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white placeholder-gray-500 outline-none focus:border-blue-500"
                  placeholder="Stash message (optional)…"
                  value={stashMsg}
                  onChange={(e) => setStashMsg(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleStashPush(); }}
                />
                <button
                  className="flex items-center gap-1 rounded bg-purple-600 px-2 py-1 text-xs text-white hover:bg-purple-500 disabled:opacity-40"
                  onClick={handleStashPush}
                  disabled={stashing || (staged.length === 0 && unstaged.length === 0 && untracked.length === 0)}
                >
                  {stashing ? <Loader2 size={11} className="animate-spin" /> : <Archive size={11} />}
                  Stash
                </button>
              </div>
            </div>
            {stashes.length === 0 ? (
              <div className="px-3 py-2 text-center text-[11px] text-gray-500">No stashes</div>
            ) : (
              stashes.map((s) => (
                <StashItem
                  key={s.ref}
                  stash={s}
                  onApply={handleStashApply}
                  onPop={handleStashPop}
                  onDrop={handleStashDrop}
                  onView={handleStashView}
                />
              ))
            )}
          </Section>
        </div>
      )}
    </div>
  );
}
