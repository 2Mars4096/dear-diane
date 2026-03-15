import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  GripVertical,
  Loader2,
  RotateCcw,
  X,
} from "lucide-react";
import { nativeGit } from "../../lib/electronBridge";

type RebaseAction = "pick" | "reword" | "edit" | "squash" | "fixup" | "drop";

interface RebaseCommit {
  hash: string;
  shortHash: string;
  message: string;
  author: string;
  action: RebaseAction;
  editedMessage?: string;
}

interface Props {
  cwd: string;
  onClose: () => void;
  onComplete: () => void;
}

const ACTION_COLORS: Record<RebaseAction, string> = {
  pick: "bg-green-600",
  reword: "bg-blue-600",
  edit: "bg-amber-600",
  squash: "bg-purple-600",
  fixup: "bg-teal-600",
  drop: "bg-red-600",
};

const ACTIONS: RebaseAction[] = ["pick", "reword", "edit", "squash", "fixup", "drop"];

export default function InteractiveRebase({ cwd, onClose, onComplete }: Props) {
  const [commits, setCommits] = useState<RebaseCommit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rebasing, setRebasing] = useState(false);
  const [rebaseStatus, setRebaseStatus] = useState<"idle" | "in-progress" | "conflict" | "done">("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [commitCount, setCommitCount] = useState(10);
  const dragIndexRef = useRef<number | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  const loadCommits = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await nativeGit.rebaseCommitList(cwd, commitCount);
      if (res.code !== 0) {
        setError(res.stderr || "Failed to load commit history");
        return;
      }
      const parsed = res.stdout
        .split("\n")
        .filter(Boolean)
        .map((line) => {
          const [hash, shortHash, message, author] = line.split("|");
          return {
            hash, shortHash,
            message: message ?? "",
            author: author ?? "",
            action: "pick" as RebaseAction,
          };
        })
        .reverse();
      setCommits(parsed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load commits");
    } finally {
      setLoading(false);
    }
  }, [cwd, commitCount]);

  useEffect(() => {
    loadCommits();
  }, [loadCommits]);

  useEffect(() => {
    const checkStatus = async () => {
      const res = await nativeGit.rebaseStatus(cwd);
      if (res.code === 0 && res.stdout.trim() === "true") {
        setRebaseStatus("in-progress");
      }
    };
    checkStatus();
  }, [cwd]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const setAction = (index: number, action: RebaseAction) => {
    setCommits((prev) => prev.map((c, i) => (i === index ? { ...c, action } : c)));
  };

  const setEditedMessage = (index: number, msg: string) => {
    setCommits((prev) => prev.map((c, i) => (i === index ? { ...c, editedMessage: msg } : c)));
  };

  const moveCommit = (from: number, to: number) => {
    if (to < 0 || to >= commits.length) return;
    setCommits((prev) => {
      const next = [...prev];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      return next;
    });
  };

  const handleDragStart = (index: number) => {
    dragIndexRef.current = index;
  };

  const handleDragOver = (e: React.DragEvent, _index: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const handleDrop = (index: number) => {
    if (dragIndexRef.current !== null && dragIndexRef.current !== index) {
      moveCommit(dragIndexRef.current, index);
    }
    dragIndexRef.current = null;
  };

  const handleStartRebase = async () => {
    if (commits.length === 0) return;
    setRebasing(true);
    setError(null);
    setStatusMessage("Starting interactive rebase...");

    const todoEntries = commits.map((c) => {
      const msg = c.action === "reword" && c.editedMessage ? c.editedMessage : c.message;
      return { hash: c.hash, action: c.action, message: msg };
    });

    try {
      const res = await nativeGit.rebaseStart(cwd, todoEntries);
      if (res.code === 0) {
        setRebaseStatus("done");
        setStatusMessage("Rebase completed successfully!");
        setTimeout(() => {
          onComplete();
          onClose();
        }, 1500);
      } else if (res.stderr.includes("CONFLICT") || res.stderr.includes("conflict")) {
        setRebaseStatus("conflict");
        setStatusMessage("Rebase paused due to conflicts. Resolve them, then continue or abort.");
        setError(res.stderr);
      } else {
        setError(res.stderr || "Rebase failed");
        setRebaseStatus("idle");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rebase failed");
      setRebaseStatus("idle");
    } finally {
      setRebasing(false);
    }
  };

  const handleAbort = async () => {
    setRebasing(true);
    try {
      await nativeGit.rebaseAbort(cwd);
      setRebaseStatus("idle");
      setStatusMessage("Rebase aborted.");
      setError(null);
      onComplete();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to abort rebase");
    } finally {
      setRebasing(false);
    }
  };

  const handleContinue = async () => {
    setRebasing(true);
    setError(null);
    try {
      const res = await nativeGit.rebaseContinue(cwd);
      if (res.code === 0) {
        setRebaseStatus("done");
        setStatusMessage("Rebase completed!");
        setTimeout(() => {
          onComplete();
          onClose();
        }, 1500);
      } else if (res.stderr.includes("CONFLICT") || res.stderr.includes("conflict")) {
        setRebaseStatus("conflict");
        setError(res.stderr);
      } else {
        setError(res.stderr || "Rebase continue failed");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to continue rebase");
    } finally {
      setRebasing(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
      ref={overlayRef}
    >
      <div className="flex max-h-[80vh] w-[700px] flex-col rounded-lg border border-gray-700 bg-[#1e1e1e] shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-700 px-4 py-3">
          <div className="flex items-center gap-2">
            <RotateCcw size={16} className="text-blue-400" />
            <h2 className="text-sm font-semibold text-white">Interactive Rebase</h2>
            {rebaseStatus === "in-progress" && (
              <span className="rounded-full bg-yellow-500/20 px-2 py-0.5 text-[10px] font-medium text-yellow-400">
                In Progress
              </span>
            )}
            {rebaseStatus === "conflict" && (
              <span className="rounded-full bg-red-500/20 px-2 py-0.5 text-[10px] font-medium text-red-400">
                Conflicts
              </span>
            )}
            {rebaseStatus === "done" && (
              <span className="rounded-full bg-green-500/20 px-2 py-0.5 text-[10px] font-medium text-green-400">
                Done
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-700 hover:text-white"
          >
            <X size={16} />
          </button>
        </div>

        {/* Count selector */}
        {rebaseStatus === "idle" && (
          <div className="flex items-center gap-2 border-b border-gray-800 px-4 py-2">
            <span className="text-xs text-gray-400">Show last</span>
            <select
              value={commitCount}
              onChange={(e) => setCommitCount(Number(e.target.value))}
              className="rounded border border-gray-600 bg-gray-800 px-2 py-0.5 text-xs text-white outline-none"
            >
              {[5, 10, 15, 20, 30, 50].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            <span className="text-xs text-gray-400">commits</span>
          </div>
        )}

        {/* Error banner */}
        {error && (
          <div className="mx-4 mt-3 flex items-start gap-2 rounded bg-red-900/40 px-3 py-2 text-xs text-red-300">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <pre className="max-h-24 flex-1 overflow-y-auto whitespace-pre-wrap">{error}</pre>
          </div>
        )}

        {/* Status message */}
        {statusMessage && !error && (
          <div className="mx-4 mt-3 rounded bg-blue-900/30 px-3 py-2 text-xs text-blue-300">
            {statusMessage}
          </div>
        )}

        {/* Commit list */}
        <div className="flex-1 overflow-y-auto px-4 py-3 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-600 [&::-webkit-scrollbar]:w-1.5">
          {loading ? (
            <div className="flex items-center justify-center py-12 text-gray-500">
              <Loader2 size={20} className="animate-spin" />
              <span className="ml-2 text-xs">Loading commits...</span>
            </div>
          ) : commits.length === 0 ? (
            <div className="py-12 text-center text-xs text-gray-500">No commits found</div>
          ) : (
            <div className="space-y-1">
              {commits.map((commit, index) => (
                <div
                  key={commit.hash}
                  draggable={rebaseStatus === "idle"}
                  onDragStart={() => handleDragStart(index)}
                  onDragOver={(e) => handleDragOver(e, index)}
                  onDrop={() => handleDrop(index)}
                  className={`group flex items-start gap-2 rounded px-2 py-1.5 transition-colors hover:bg-gray-800/50 ${
                    commit.action === "drop" ? "opacity-40" : ""
                  }`}
                >
                  {/* Drag handle */}
                  {rebaseStatus === "idle" && (
                    <GripVertical
                      size={14}
                      className="mt-0.5 shrink-0 cursor-grab text-gray-600 opacity-0 transition-opacity group-hover:opacity-100 active:cursor-grabbing"
                    />
                  )}

                  {/* Move buttons */}
                  {rebaseStatus === "idle" && (
                    <div className="mt-0.5 flex shrink-0 flex-col gap-px">
                      <button
                        onClick={() => moveCommit(index, index - 1)}
                        disabled={index === 0}
                        className="rounded p-px text-gray-500 transition-colors hover:text-white disabled:opacity-20"
                      >
                        <ArrowUp size={10} />
                      </button>
                      <button
                        onClick={() => moveCommit(index, index + 1)}
                        disabled={index === commits.length - 1}
                        className="rounded p-px text-gray-500 transition-colors hover:text-white disabled:opacity-20"
                      >
                        <ArrowDown size={10} />
                      </button>
                    </div>
                  )}

                  {/* Action selector */}
                  <select
                    value={commit.action}
                    onChange={(e) => setAction(index, e.target.value as RebaseAction)}
                    disabled={rebaseStatus !== "idle"}
                    className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold text-white outline-none ${ACTION_COLORS[commit.action]} disabled:opacity-60`}
                  >
                    {ACTIONS.map((a) => (
                      <option key={a} value={a}>{a}</option>
                    ))}
                  </select>

                  {/* Hash */}
                  <span className="shrink-0 font-mono text-[11px] text-blue-400">
                    {commit.shortHash}
                  </span>

                  {/* Message */}
                  <div className="min-w-0 flex-1">
                    {commit.action === "reword" && rebaseStatus === "idle" ? (
                      <input
                        className="w-full rounded border border-gray-600 bg-gray-800 px-1.5 py-0.5 text-xs text-white outline-none focus:border-blue-500"
                        defaultValue={commit.editedMessage ?? commit.message}
                        onChange={(e) => setEditedMessage(index, e.target.value)}
                        placeholder="New commit message..."
                      />
                    ) : (
                      <span className="truncate text-xs text-gray-300">{commit.message}</span>
                    )}
                  </div>

                  {/* Author */}
                  <span className="shrink-0 text-[10px] text-gray-500">{commit.author}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-gray-700 px-4 py-3">
          <div className="text-[10px] text-gray-500">
            {commits.length} commit{commits.length !== 1 ? "s" : ""} &middot;
            {" "}{commits.filter((c) => c.action === "drop").length} to drop
          </div>
          <div className="flex items-center gap-2">
            {(rebaseStatus === "in-progress" || rebaseStatus === "conflict") && (
              <>
                <button
                  onClick={handleAbort}
                  disabled={rebasing}
                  className="flex items-center gap-1.5 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-40"
                >
                  <X size={12} />
                  Abort Rebase
                </button>
                {rebaseStatus === "conflict" && (
                  <button
                    onClick={handleContinue}
                    disabled={rebasing}
                    className="flex items-center gap-1.5 rounded bg-green-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-green-500 disabled:opacity-40"
                  >
                    {rebasing ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                    Continue
                  </button>
                )}
              </>
            )}
            {rebaseStatus === "idle" && (
              <>
                <button
                  onClick={onClose}
                  className="rounded px-3 py-1.5 text-xs text-gray-400 transition-colors hover:bg-gray-700 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  onClick={handleStartRebase}
                  disabled={rebasing || commits.length === 0}
                  className="flex items-center gap-1.5 rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-500 disabled:opacity-40"
                >
                  {rebasing ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
                  Start Rebase
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
