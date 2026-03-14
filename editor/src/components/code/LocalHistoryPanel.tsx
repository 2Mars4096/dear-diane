import { useCallback, useEffect, useMemo, useState } from "react";
import { Clock, Trash2, RotateCcw, Eye } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import {
  getFileHistory,
  clearFileHistory,
  type HistoryEntry,
} from "../../lib/localHistory";

/* ------------------------------------------------------------------ */
/*  Relative time formatter                                            */
/* ------------------------------------------------------------------ */

function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(ts).toLocaleDateString();
}

function formatTimestamp(ts: number): string {
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function sizeDelta(current: string, historical: string): string {
  const delta = new Blob([current]).size - new Blob([historical]).size;
  if (delta === 0) return "";
  const sign = delta > 0 ? "+" : "";
  if (Math.abs(delta) < 1024) return `${sign}${delta}B`;
  return `${sign}${(delta / 1024).toFixed(1)}KB`;
}

/* ------------------------------------------------------------------ */
/*  LocalHistoryPanel component                                        */
/* ------------------------------------------------------------------ */

export default function LocalHistoryPanel() {
  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const openFiles = useCodeStore((s) => s.openFiles);
  const openDiff = useCodeStore((s) => s.openDiff);
  const updateFileContent = useCodeStore((s) => s.updateFileContent);

  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  const activeFile = openFiles.find((f) => f.path === activeFilePath);
  const currentContent = activeFile?.content ?? "";

  const refresh = useCallback(() => {
    if (!activeFilePath) {
      setEntries([]);
      return;
    }
    const hist = getFileHistory(activeFilePath);
    setEntries([...hist].reverse());
    setSelectedIdx(null);
  }, [activeFilePath]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleViewDiff = useCallback(
    (entry: HistoryEntry) => {
      if (!activeFilePath) return;
      openDiff(
        entry.content,
        currentContent,
        `${activeFilePath} (${formatTimestamp(entry.timestamp)})`,
        activeFilePath,
      );
    },
    [activeFilePath, currentContent, openDiff],
  );

  const handleRestore = useCallback(
    (entry: HistoryEntry) => {
      if (!activeFilePath) return;
      updateFileContent(activeFilePath, entry.content);
    },
    [activeFilePath, updateFileContent],
  );

  const handleClear = useCallback(() => {
    if (!activeFilePath) return;
    clearFileHistory(activeFilePath);
    refresh();
  }, [activeFilePath, refresh]);

  const fileName = useMemo(() => {
    if (!activeFilePath) return "";
    return activeFilePath.split("/").pop() ?? activeFilePath;
  }, [activeFilePath]);

  if (!activeFilePath) {
    return (
      <div className="h-full flex items-center justify-center text-gray-500 text-xs px-4 text-center">
        Open a file to view its local history
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col text-xs">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#3c3c3c] bg-[#252526] shrink-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <Clock size={13} className="text-gray-500 shrink-0" />
          <span className="text-gray-300 font-medium truncate">
            Timeline
          </span>
          <span className="text-gray-600 truncate">{fileName}</span>
        </div>
        {entries.length > 0 && (
          <button
            onClick={handleClear}
            className="shrink-0 p-1 rounded hover:bg-[#3c3c3c] text-gray-500 hover:text-red-400 transition-colors"
            title="Clear history for this file"
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>

      {/* Entry list */}
      <div className="flex-1 overflow-y-auto">
        {entries.length === 0 ? (
          <div className="px-3 py-6 text-center text-gray-600">
            No history entries yet
          </div>
        ) : (
          entries.map((entry, i) => {
            const isSelected = selectedIdx === i;
            const delta = sizeDelta(currentContent, entry.content);

            return (
              <div
                key={entry.timestamp}
                className={`group px-3 py-2 border-b border-[#2d2d2d] cursor-pointer transition-colors ${
                  isSelected ? "bg-[#094771]" : "hover:bg-[#2a2d2e]"
                }`}
                onClick={() => setSelectedIdx(isSelected ? null : i)}
              >
                <div className="flex items-center justify-between">
                  <span className="text-gray-300">
                    {relativeTime(entry.timestamp)}
                  </span>
                  <div className="flex items-center gap-1">
                    {delta && (
                      <span
                        className={`text-[10px] ${delta.startsWith("+") ? "text-green-500" : "text-red-500"}`}
                      >
                        {delta}
                      </span>
                    )}
                    {entry.label && (
                      <span className="px-1 py-0.5 rounded bg-[#3c3c3c] text-gray-500 text-[10px]">
                        {entry.label}
                      </span>
                    )}
                  </div>
                </div>
                <div className="text-[10px] text-gray-600 mt-0.5">
                  {formatTimestamp(entry.timestamp)}
                </div>

                {/* Actions row */}
                {isSelected && (
                  <div className="flex items-center gap-2 mt-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleViewDiff(entry);
                      }}
                      className="flex items-center gap-1 px-2 py-1 rounded bg-[#3c3c3c] hover:bg-[#4c4c4c] text-gray-300 transition-colors"
                    >
                      <Eye size={11} />
                      Compare
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRestore(entry);
                      }}
                      className="flex items-center gap-1 px-2 py-1 rounded bg-[#3c3c3c] hover:bg-[#4c4c4c] text-gray-300 transition-colors"
                    >
                      <RotateCcw size={11} />
                      Restore
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
