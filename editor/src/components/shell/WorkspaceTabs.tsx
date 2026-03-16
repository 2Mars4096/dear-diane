import { useState, useRef, useCallback, useEffect } from "react";
import { Plus, X, Palette, PenLine, XCircle } from "lucide-react";
import {
  useWorkspaceStore,
  WORKSPACE_COLORS,
  type Workspace,
} from "../../store/useWorkspaceStore";

function ColorDot({ color, size = 8 }: { color?: string; size?: number }) {
  const hex =
    WORKSPACE_COLORS.find((c) => c.id === color)?.hex ??
    color ??
    "#6b7280";
  return (
    <span
      className="inline-block rounded-full flex-shrink-0"
      style={{ width: size, height: size, backgroundColor: hex }}
    />
  );
}

function WorkspaceTab({
  ws,
  isActive,
  onActivate,
  onClose,
  onRename,
  onChangeColor,
  onCloseOthers,
  onDragStart,
  onDragOver,
  onDrop,
}: {
  ws: Workspace;
  isActive: boolean;
  onActivate: () => void;
  onClose: () => void;
  onRename: (name: string) => void;
  onChangeColor: (color: string) => void;
  onCloseOthers: () => void;
  onDragStart: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(ws.name);
  const [showCtx, setShowCtx] = useState(false);
  const [ctxPos, setCtxPos] = useState({ x: 0, y: 0 });
  const inputRef = useRef<HTMLInputElement>(null);
  const ctxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  useEffect(() => {
    if (!showCtx) return;
    const dismiss = (e: MouseEvent) => {
      if (ctxRef.current && !ctxRef.current.contains(e.target as Node))
        setShowCtx(false);
    };
    document.addEventListener("mousedown", dismiss);
    return () => document.removeEventListener("mousedown", dismiss);
  }, [showCtx]);

  const commitRename = () => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== ws.name) onRename(trimmed);
    setEditing(false);
  };

  const handleContext = (e: React.MouseEvent) => {
    e.preventDefault();
    setCtxPos({ x: e.clientX, y: e.clientY });
    setShowCtx(true);
  };

  return (
    <>
      <div
        draggable
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onClick={onActivate}
        onDoubleClick={() => { setEditValue(ws.name); setEditing(true); }}
        onContextMenu={handleContext}
        onMouseDown={(e) => { if (e.button === 1) { e.preventDefault(); onClose(); } }}
        className={`
          app-no-drag group flex items-center gap-1.5 pl-2.5 pr-1 h-7 rounded-t text-[11px] font-medium
          cursor-pointer select-none transition-colors whitespace-nowrap max-w-[180px]
          ${isActive
            ? "bg-white text-gray-900 dark:bg-gray-800 dark:text-white"
            : "bg-transparent text-gray-500 hover:text-gray-800 hover:bg-gray-200/70 dark:text-gray-400 dark:hover:text-gray-200 dark:hover:bg-gray-800/50"
          }
        `}
      >
        <ColorDot color={ws.color} />
        {editing ? (
          <input
            ref={inputRef}
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename();
              if (e.key === "Escape") setEditing(false);
            }}
            className="bg-transparent border-b border-gray-400 dark:border-gray-500 text-gray-900 dark:text-white text-[11px] w-24 outline-none"
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span className="truncate">{ws.name}</span>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); onClose(); }}
          className="ml-0.5 p-0.5 rounded opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-gray-200 dark:hover:bg-white/10 transition-opacity"
        >
          <X size={12} />
        </button>
      </div>

      {showCtx && (
        <div
          ref={ctxRef}
          className="fixed z-[9999] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded shadow-xl py-1 min-w-[160px] text-[11px] text-gray-700 dark:text-gray-200"
          style={{ left: ctxPos.x, top: ctxPos.y }}
        >
          <button
            className="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-white/10 flex items-center gap-2"
            onClick={() => { setShowCtx(false); setEditValue(ws.name); setEditing(true); }}
          >
            <PenLine size={12} /> Rename
          </button>

          <div className="px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-white/10">
            <div className="flex items-center gap-2 mb-1">
              <Palette size={12} /> Color
            </div>
            <div className="flex gap-1 pl-5">
              {WORKSPACE_COLORS.map((c) => (
                <button
                  key={c.id}
                  onClick={() => { onChangeColor(c.id); setShowCtx(false); }}
                  className={`w-4 h-4 rounded-full border-2 transition-transform hover:scale-125 ${ws.color === c.id ? "border-gray-700 dark:border-white" : "border-transparent"}`}
                  style={{ backgroundColor: c.hex }}
                />
              ))}
            </div>
          </div>

          <button
            className="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-white/10 flex items-center gap-2"
            onClick={() => { setShowCtx(false); onCloseOthers(); }}
          >
            <XCircle size={12} /> Close Others
          </button>
          <div className="border-t border-gray-200 dark:border-gray-700 my-1" />
          <button
            className="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-white/10 flex items-center gap-2 text-red-500 dark:text-red-400"
            onClick={() => { setShowCtx(false); onClose(); }}
          >
            <X size={12} /> Close
          </button>
        </div>
      )}
    </>
  );
}

export default function WorkspaceTabs() {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const createWorkspace = useWorkspaceStore((s) => s.createWorkspace);
  const removeWorkspace = useWorkspaceStore((s) => s.removeWorkspace);
  const setActiveWorkspace = useWorkspaceStore((s) => s.setActiveWorkspace);
  const updateWorkspace = useWorkspaceStore((s) => s.updateWorkspace);
  const reorderWorkspace = useWorkspaceStore((s) => s.reorderWorkspace);

  const dragIdx = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const handleDragStart = useCallback((idx: number) => (e: React.DragEvent) => {
    dragIdx.current = idx;
    e.dataTransfer.effectAllowed = "move";
  }, []);

  const handleDragOver = useCallback((_idx: number) => (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const handleDrop = useCallback((toIdx: number) => (e: React.DragEvent) => {
    e.preventDefault();
    const from = dragIdx.current;
    if (from !== null && from !== toIdx) reorderWorkspace(from, toIdx);
    dragIdx.current = null;
  }, [reorderWorkspace]);

  const handleCloseOthers = useCallback(
    (keepId: string) => {
      workspaces.forEach((w) => {
        if (w.id !== keepId) removeWorkspace(w.id);
      });
    },
    [workspaces, removeWorkspace],
  );

  if (workspaces.length === 0) return null;

  const isElectron = typeof window !== "undefined" && "electronAPI" in window;
  const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.platform);

  return (
    <div
      className={`flex items-end bg-gray-100 dark:bg-[#1a1a1a] border-b border-gray-200 dark:border-gray-800 h-[30px] flex-shrink-0 app-drag-region ${isElectron && isMac ? "pl-[72px]" : ""}`}
    >
      <div
        ref={scrollRef}
        className="flex items-end gap-0.5 overflow-x-auto px-1 scrollbar-none flex-1 min-w-0"
      >
        {workspaces.map((ws, idx) => (
          <WorkspaceTab
            key={ws.id}
            ws={ws}
            isActive={ws.id === activeId}
            onActivate={() => setActiveWorkspace(ws.id)}
            onClose={() => removeWorkspace(ws.id)}
            onRename={(name) => updateWorkspace(ws.id, { name })}
            onChangeColor={(color) => updateWorkspace(ws.id, { color })}
            onCloseOthers={() => handleCloseOthers(ws.id)}
            onDragStart={handleDragStart(idx)}
            onDragOver={handleDragOver(idx)}
            onDrop={handleDrop(idx)}
          />
        ))}
      </div>
      <button
        onClick={() => createWorkspace()}
        title="New Workspace (⌘⇧N)"
        className="app-no-drag flex items-center justify-center w-7 h-7 text-gray-500 hover:text-gray-800 hover:bg-gray-200 rounded transition-colors dark:hover:text-gray-300 dark:hover:bg-white/5 flex-shrink-0"
      >
        <Plus size={14} />
      </button>
    </div>
  );
}

export function useWorkspaceShortcuts() {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const setActiveWorkspace = useWorkspaceStore((s) => s.setActiveWorkspace);
  const createWorkspace = useWorkspaceStore((s) => s.createWorkspace);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const isMeta = e.metaKey || e.ctrlKey;

      if (isMeta && e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
        e.preventDefault();
        if (workspaces.length < 2) return;
        const curIdx = workspaces.findIndex((w) => w.id === activeId);
        if (curIdx === -1) return;
        const next =
          e.key === "ArrowRight"
            ? (curIdx + 1) % workspaces.length
            : (curIdx - 1 + workspaces.length) % workspaces.length;
        setActiveWorkspace(workspaces[next].id);
        return;
      }

      if (isMeta && e.shiftKey && e.key === "N") {
        e.preventDefault();
        createWorkspace();
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [workspaces, activeId, setActiveWorkspace, createWorkspace]);
}
