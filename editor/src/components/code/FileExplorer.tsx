import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock,
  Eye,
  EyeOff,
  File,
  FileText,
  Filter,
  Folder,
  FolderPlus,
  FilePlus,
  FolderPlusIcon,
  ChevronsDownUp,
  Loader2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { useCodeStore, type FileTreeEntry } from "../../store/useCodeStore";
import { isElectron, nativeDialog, nativeFs, nativeShell } from "../../lib/electronBridge";
import { parseGitignore } from "../../lib/gitignoreFilter";
import { FileIcon } from "./FileIcon";

// ─── Inline Input (for new file/folder/rename) ────────────────────────

interface InlineInputProps {
  defaultValue?: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
  placeholder?: string;
  depth: number;
  icon?: React.ReactNode;
}

function InlineInput({ defaultValue = "", onSubmit, onCancel, placeholder, depth, icon }: InlineInputProps) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    if (defaultValue) {
      const dotIdx = defaultValue.lastIndexOf(".");
      el.setSelectionRange(0, dotIdx > 0 ? dotIdx : defaultValue.length);
    }
  }, [defaultValue]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const val = ref.current?.value.trim();
      if (val) onSubmit(val);
      else onCancel();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  };

  return (
    <div
      className="flex items-center gap-1 py-[2px] pr-2"
      style={{ paddingLeft: depth * 16 + 4 }}
    >
      <span className="w-3.5 shrink-0" />
      {icon}
      <input
        ref={ref}
        type="text"
        defaultValue={defaultValue}
        placeholder={placeholder}
        onKeyDown={handleKeyDown}
        onBlur={onCancel}
        className="min-w-0 flex-1 rounded-sm border border-blue-500 bg-gray-800 px-1 py-0.5 text-xs text-gray-200 outline-none"
      />
    </div>
  );
}

// State for create / rename operations, shared via context
interface FileOpState {
  creating: { parentPath: string; type: "file" | "folder" } | null;
  renaming: string | null;
}

const FileOpContext = React.createContext<{
  ops: FileOpState;
  setOps: React.Dispatch<React.SetStateAction<FileOpState>>;
  refreshDir: (dirPath: string) => Promise<void>;
}>({
  ops: { creating: null, renaming: null },
  setOps: () => {},
  refreshDir: async () => {},
});

const HIDDEN_ALWAYS = new Set(["node_modules", ".DS_Store", "__pycache__", ".git"]);
const SHOWN_DOTFILES = new Set([".env", ".gitignore", ".eslintrc", ".prettierrc", ".editorconfig", ".npmrc", ".nvmrc"]);

function shouldShow(name: string): boolean {
  if (HIDDEN_ALWAYS.has(name)) return false;
  if (name.startsWith(".") && !SHOWN_DOTFILES.has(name)) return false;
  return true;
}

function sortEntries(entries: FileTreeEntry[]): FileTreeEntry[] {
  return [...entries].sort((a, b) => {
    if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  });
}

function basename(p: string): string {
  return p.split("/").filter(Boolean).pop() ?? p;
}

// ─── Filter helpers ────────────────────────────────────────────────────

function matchesFilter(entry: FileTreeEntry, filter: string, fileTree: Record<string, FileTreeEntry[]>): boolean {
  const lf = filter.toLowerCase();
  if (entry.name.toLowerCase().includes(lf)) return true;
  if (entry.isDirectory) {
    const children = fileTree[entry.path] ?? entry.children;
    if (children) {
      return children.some((child) => matchesFilter(child, filter, fileTree));
    }
  }
  return false;
}

function HighlightName({ name, filter }: { name: string; filter: string }) {
  if (!filter) return <span className="truncate">{name}</span>;
  const lName = name.toLowerCase();
  const lFilter = filter.toLowerCase();
  const idx = lName.indexOf(lFilter);
  if (idx === -1) return <span className="truncate">{name}</span>;
  return (
    <span className="truncate">
      {name.slice(0, idx)}
      <span className="bg-yellow-500/30 text-yellow-200">{name.slice(idx, idx + filter.length)}</span>
      {name.slice(idx + filter.length)}
    </span>
  );
}

function ExplorerFilter({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="px-2 py-1.5 border-b border-gray-800">
      <div className="relative">
        <Filter size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Filter files..."
          className="w-full h-[24px] bg-gray-800 border border-gray-700 rounded pl-7 pr-7 text-[11px] text-white placeholder:text-gray-500 focus:border-blue-500/60 focus:outline-none"
          spellCheck={false}
        />
        {value && (
          <button
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
            onClick={() => onChange("")}
          >
            <X size={11} />
          </button>
        )}
      </div>
    </div>
  );
}

function getFileIcon(name: string, isDir: boolean, isOpen: boolean) {
  return <FileIcon name={name} isDirectory={isDir} isExpanded={isOpen} size={16} className="shrink-0" />;
}

// ─── Context Menu ──────────────────────────────────────────────────────

interface ContextMenuState {
  x: number;
  y: number;
  entry: FileTreeEntry;
  rootPath?: string;
}

interface MenuAction {
  label: string;
  action: () => void;
  separator?: boolean;
}

function parentDir(p: string): string {
  const parts = p.split("/");
  parts.pop();
  return parts.join("/") || "/";
}

function ContextMenuOverlay({
  menu,
  onClose,
}: {
  menu: ContextMenuState;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { setOps, refreshDir } = React.useContext(FileOpContext);
  const openFile = useCodeStore((s) => s.openFile);
  const closeFile = useCodeStore((s) => s.closeFile);
  const setDirExpanded = useCodeStore((s) => s.setDirExpanded);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler, true);
    return () => document.removeEventListener("mousedown", handler, true);
  }, [onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler, true);
    return () => document.removeEventListener("keydown", handler, true);
  }, [onClose]);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).catch(() => {});
    onClose();
  };

  const handleNewFile = () => {
    const dir = menu.entry.isDirectory ? menu.entry.path : parentDir(menu.entry.path);
    setDirExpanded(dir, true);
    setOps((s) => ({ ...s, creating: { parentPath: dir, type: "file" } }));
    onClose();
  };

  const handleNewFolder = () => {
    const dir = menu.entry.isDirectory ? menu.entry.path : parentDir(menu.entry.path);
    setDirExpanded(dir, true);
    setOps((s) => ({ ...s, creating: { parentPath: dir, type: "folder" } }));
    onClose();
  };

  const handleRename = () => {
    setOps((s) => ({ ...s, renaming: menu.entry.path }));
    onClose();
  };

  const handleDelete = async () => {
    onClose();
    const name = menu.entry.name;
    const confirmed = window.confirm(`Are you sure you want to delete "${name}"?`);
    if (!confirmed) return;
    try {
      await nativeFs.delete(menu.entry.path);
      closeFile(menu.entry.path);
      await refreshDir(parentDir(menu.entry.path));
    } catch (err) {
      console.error("Delete failed:", err);
    }
  };

  const handleOpen = async () => {
    if (!menu.entry.isDirectory) {
      const content = await nativeFs.readFile(menu.entry.path);
      openFile(menu.entry.path, content ?? "");
    }
    onClose();
  };

  const relPath = menu.rootPath
    ? menu.entry.path.replace(menu.rootPath, "").replace(/^\//, "")
    : menu.entry.name;

  const items: MenuAction[] = menu.entry.isDirectory
    ? [
        { label: "New File…", action: handleNewFile },
        { label: "New Folder…", action: handleNewFolder },
        { label: "Rename…", action: handleRename, separator: true },
        { label: "Delete", action: handleDelete },
        { label: "Copy Path", action: () => copyToClipboard(menu.entry.path), separator: true },
        { label: "Copy Relative Path", action: () => copyToClipboard(relPath) },
        {
          label: "Reveal in Finder",
          action: () => { nativeShell.openPath(menu.entry.path); onClose(); },
          separator: true,
        },
      ]
    : [
        { label: "Open", action: handleOpen },
        { label: "New File…", action: handleNewFile, separator: true },
        { label: "New Folder…", action: handleNewFolder },
        { label: "Rename…", action: handleRename, separator: true },
        { label: "Delete", action: handleDelete },
        { label: "Copy Path", action: () => copyToClipboard(menu.entry.path), separator: true },
        { label: "Copy Relative Path", action: () => copyToClipboard(relPath) },
        {
          label: "Reveal in Finder",
          action: () => { nativeShell.openPath(parentDir(menu.entry.path)); onClose(); },
          separator: true,
        },
      ];

  return (
    <div
      ref={ref}
      className="fixed z-[999] min-w-[180px] rounded-md border border-gray-700 bg-gray-800 py-1 shadow-xl"
      style={{ left: menu.x, top: menu.y }}
    >
      {items.map((item, i) => (
        <React.Fragment key={item.label}>
          {item.separator && i > 0 && (
            <div className="my-1 border-t border-gray-700" />
          )}
          <button
            className="flex w-full items-center px-3 py-1.5 text-left text-xs text-gray-300 hover:bg-blue-600/40 hover:text-white"
            onClick={item.action}
          >
            {item.label}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
}

// ─── Root Header Context Menu ──────────────────────────────────────────

function RootContextMenu({
  x,
  y,
  rootPath,
  onClose,
}: {
  x: number;
  y: number;
  rootPath: string;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const removePinnedRoot = useCodeStore((s) => s.removePinnedRoot);
  const setDirExpanded = useCodeStore((s) => s.setDirExpanded);
  const { setOps } = React.useContext(FileOpContext);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler, true);
    return () => document.removeEventListener("mousedown", handler, true);
  }, [onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler, true);
    return () => document.removeEventListener("keydown", handler, true);
  }, [onClose]);

  const items: MenuAction[] = [
    {
      label: "New File…",
      action: () => {
        setDirExpanded(rootPath, true);
        setOps((s) => ({ ...s, creating: { parentPath: rootPath, type: "file" } }));
        onClose();
      },
    },
    {
      label: "New Folder…",
      action: () => {
        setDirExpanded(rootPath, true);
        setOps((s) => ({ ...s, creating: { parentPath: rootPath, type: "folder" } }));
        onClose();
      },
    },
    {
      label: "Remove from Workspace",
      action: () => { removePinnedRoot(rootPath); onClose(); },
      separator: true,
    },
    {
      label: "Open in Finder",
      action: () => { nativeShell.openPath(rootPath); onClose(); },
      separator: true,
    },
    {
      label: "Copy Path",
      action: () => { navigator.clipboard.writeText(rootPath).catch(() => {}); onClose(); },
    },
  ];

  return (
    <div
      ref={ref}
      className="fixed z-[999] min-w-[180px] rounded-md border border-gray-700 bg-gray-800 py-1 shadow-xl"
      style={{ left: x, top: y }}
    >
      {items.map((item, i) => (
        <React.Fragment key={item.label}>
          {item.separator && i > 0 && (
            <div className="my-1 border-t border-gray-700" />
          )}
          <button
            className="flex w-full items-center px-3 py-1.5 text-left text-xs text-gray-300 hover:bg-blue-600/40 hover:text-white"
            onClick={item.action}
          >
            {item.label}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
}

// ─── Tree Node ─────────────────────────────────────────────────────────

interface TreeNodeProps {
  entry: FileTreeEntry;
  depth: number;
  rootPath: string;
  onContextMenu: (e: React.MouseEvent, entry: FileTreeEntry) => void;
  filter?: string;
  gitignoreFilter?: ((path: string) => boolean) | null;
  showIgnored?: boolean;
}

const TreeNode = React.memo(function TreeNode({
  entry,
  depth,
  rootPath,
  onContextMenu,
  filter = "",
  gitignoreFilter: gitFilter = null,
  showIgnored = true,
}: TreeNodeProps) {
  const [loading, setLoading] = useState(false);
  const expanded = useCodeStore((s) => s.expandedDirs[entry.path] ?? false);
  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const isOpen = useCodeStore((s) =>
    entry.isDirectory ? false : s.openFiles.some((f) => f.path === entry.path),
  );
  const fileTree = useCodeStore((s) => s.fileTree);
  const toggleDir = useCodeStore((s) => s.toggleDir);
  const setFileTree = useCodeStore((s) => s.setFileTree);
  const openFile = useCodeStore((s) => s.openFile);
  const closeFile = useCodeStore((s) => s.closeFile);
  const renameOpenFile = useCodeStore((s) => s.renameOpenFile);

  const { ops, setOps, refreshDir } = React.useContext(FileOpContext);

  const isActive = !entry.isDirectory && entry.path === activeFilePath;
  const isRenaming = ops.renaming === entry.path;
  const isCreatingHere = entry.isDirectory && ops.creating?.parentPath === entry.path;

  const handleClick = useCallback(async () => {
    if (entry.isDirectory) {
      const wasExpanded = useCodeStore.getState().expandedDirs[entry.path] ?? false;
      toggleDir(entry.path);

      if (!wasExpanded && !fileTree[entry.path]) {
        setLoading(true);
        try {
          const raw = await nativeFs.readDir(entry.path);
          if (raw) {
            const children = raw
              .filter((e) => shouldShow(e.name))
              .map((e) => ({
                name: e.name,
                path: `${entry.path}/${e.name}`,
                isDirectory: e.isDirectory,
              }));
            setFileTree(entry.path, sortEntries(children));
          }
        } finally {
          setLoading(false);
        }
      }
    } else {
      const content = await nativeFs.readFile(entry.path);
      openFile(entry.path, content ?? "");
    }
  }, [entry, toggleDir, fileTree, setFileTree, openFile]);

  const handleDoubleClick = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!entry.isDirectory) {
        const content = await nativeFs.readFile(entry.path);
        openFile(entry.path, content ?? "");
      }
    },
    [entry, openFile],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "F2") {
        e.preventDefault();
        setOps((s) => ({ ...s, renaming: entry.path }));
      } else if (e.key === "Delete" || (e.key === "Backspace" && e.metaKey)) {
        e.preventDefault();
        const confirmed = window.confirm(`Delete "${entry.name}"?`);
        if (!confirmed) return;
        nativeFs.delete(entry.path).then(() => {
          closeFile(entry.path);
          refreshDir(parentDir(entry.path));
        }).catch((err) => console.error("Delete failed:", err));
      }
    },
    [entry, setOps, closeFile, refreshDir],
  );

  const handleRenameSubmit = useCallback(async (newName: string) => {
    const dir = parentDir(entry.path);
    const newPath = `${dir}/${newName}`;
    if (newPath === entry.path) {
      setOps((s) => ({ ...s, renaming: null }));
      return;
    }
    try {
      const exists = await nativeFs.exists(newPath);
      if (exists) {
        console.error(`"${newName}" already exists in this directory`);
        setOps((s) => ({ ...s, renaming: null }));
        return;
      }
      await nativeFs.rename(entry.path, newPath);
      renameOpenFile(entry.path, newPath);
      await refreshDir(dir);
    } catch (err) {
      console.error("Rename failed:", err);
    }
    setOps((s) => ({ ...s, renaming: null }));
  }, [entry, setOps, renameOpenFile, refreshDir]);

  const handleCreateSubmit = useCallback(async (name: string) => {
    const creating = ops.creating;
    if (!creating) return;
    const newPath = `${creating.parentPath}/${name}`;
    try {
      const exists = await nativeFs.exists(newPath);
      if (exists) {
        console.error(`"${name}" already exists`);
        setOps((s) => ({ ...s, creating: null }));
        return;
      }
      if (creating.type === "folder") {
        await nativeFs.mkdir(newPath);
      } else {
        await nativeFs.writeFile(newPath, "");
      }
      await refreshDir(creating.parentPath);
      if (creating.type === "file") {
        openFile(newPath, "");
      }
    } catch (err) {
      console.error("Create failed:", err);
    }
    setOps((s) => ({ ...s, creating: null }));
  }, [ops.creating, setOps, refreshDir, openFile]);

  const children = entry.isDirectory ? (fileTree[entry.path] ?? entry.children) : undefined;
  const sortedChildren = children ? sortEntries(children) : undefined;

  const filteredChildren = useMemo(() => {
    if (!sortedChildren) return undefined;
    let result = sortedChildren;
    if (!showIgnored && gitFilter) {
      result = result.filter((e) => !gitFilter(e.path));
    }
    if (filter) {
      result = result.filter((e) => matchesFilter(e, filter, fileTree));
    }
    return result;
  }, [sortedChildren, showIgnored, gitFilter, filter, fileTree]);

  const forceExpand = !!filter && !!filteredChildren && filteredChildren.length > 0;

  if (isRenaming) {
    return (
      <InlineInput
        defaultValue={entry.name}
        onSubmit={handleRenameSubmit}
        onCancel={() => setOps((s) => ({ ...s, renaming: null }))}
        depth={depth}
        icon={getFileIcon(entry.name, entry.isDirectory, false)}
      />
    );
  }

  const isExpanded = expanded || forceExpand;

  return (
    <>
      <div
        role="treeitem"
        tabIndex={0}
        aria-expanded={entry.isDirectory ? isExpanded : undefined}
        draggable={!entry.isDirectory}
        onDragStart={(e) => {
          if (entry.isDirectory) return;
          e.dataTransfer.setData("text/uri-list", entry.path);
          e.dataTransfer.setData("text/plain", entry.path);
          e.dataTransfer.effectAllowed = "copy";
        }}
        className={`group flex cursor-pointer select-none items-center gap-1 py-[2px] pr-2 text-xs outline-none focus-visible:ring-1 focus-visible:ring-blue-500/50 ${
          isActive
            ? "bg-blue-600/30 text-white"
            : "text-gray-300 hover:bg-gray-700/50"
        }`}
        style={{ paddingLeft: depth * 16 + 4 }}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        onKeyDown={handleKeyDown}
        onContextMenu={(e) => onContextMenu(e, entry)}
      >
        {entry.isDirectory ? (
          isExpanded ? (
            <ChevronDown size={14} className="shrink-0 text-gray-500" />
          ) : (
            <ChevronRight size={14} className="shrink-0 text-gray-500" />
          )
        ) : (
          <span className="w-3.5 shrink-0" />
        )}
        {getFileIcon(entry.name, entry.isDirectory, isExpanded)}
        <HighlightName name={entry.name} filter={filter} />
        {isOpen && !isActive && (
          <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-blue-400/60" />
        )}
      </div>

      {entry.isDirectory && isExpanded && (
        <>
          {isCreatingHere && (
            <InlineInput
              onSubmit={handleCreateSubmit}
              onCancel={() => setOps((s) => ({ ...s, creating: null }))}
              placeholder={ops.creating?.type === "folder" ? "Folder name" : "File name"}
              depth={depth + 1}
              icon={
                ops.creating?.type === "folder"
                  ? <Folder size={16} className="shrink-0 text-amber-400" />
                  : <File size={16} className="shrink-0 text-gray-500" />
              }
            />
          )}
          {loading && (
            <div
              className="flex items-center gap-1.5 py-1 text-xs text-gray-500"
              style={{ paddingLeft: (depth + 1) * 16 + 4 }}
            >
              <Loader2 size={12} className="animate-spin" />
              Loading…
            </div>
          )}
          {filteredChildren?.map((child) => (
            <TreeNode
              key={child.path}
              entry={child}
              depth={depth + 1}
              rootPath={rootPath}
              onContextMenu={onContextMenu}
              filter={filter}
              gitignoreFilter={gitFilter}
              showIgnored={showIgnored}
            />
          ))}
        </>
      )}
    </>
  );
});

// ─── Pinned Root Section ───────────────────────────────────────────────

interface PinnedRootSectionProps {
  rootPath: string;
  filter: string;
  showIgnored: boolean;
  gitignoreFilter: ((path: string) => boolean) | null;
}

function PinnedRootSection({ rootPath, filter, showIgnored, gitignoreFilter: gitFilter }: PinnedRootSectionProps) {
  const expanded = useCodeStore((s) => s.expandedDirs[rootPath] ?? true);
  const fileTree = useCodeStore((s) => s.fileTree);
  const toggleDir = useCodeStore((s) => s.toggleDir);
  const setFileTree = useCodeStore((s) => s.setFileTree);
  const setDirExpanded = useCodeStore((s) => s.setDirExpanded);
  const openFile = useCodeStore((s) => s.openFile);
  const [loading, setLoading] = useState(false);
  const [rootCtx, setRootCtx] = useState<{ x: number; y: number } | null>(null);
  const [itemCtx, setItemCtx] = useState<ContextMenuState | null>(null);
  const [fileOps, setFileOps] = useState<FileOpState>({ creating: null, renaming: null });

  const refreshDir = useCallback(async (dirPath: string) => {
    try {
      const raw = await nativeFs.readDir(dirPath);
      if (raw) {
        const entries = raw
          .filter((e) => shouldShow(e.name))
          .map((e) => ({
            name: e.name,
            path: `${dirPath}/${e.name}`,
            isDirectory: e.isDirectory,
          }));
        setFileTree(dirPath, sortEntries(entries));
      }
    } catch (err) {
      console.error("refreshDir failed:", err);
    }
  }, [setFileTree]);

  const loadRoot = useCallback(async () => {
    if (fileTree[rootPath]) return;
    setLoading(true);
    try {
      await refreshDir(rootPath);
    } finally {
      setLoading(false);
    }
  }, [rootPath, fileTree, refreshDir]);

  useEffect(() => {
    if (expanded) loadRoot();
  }, [expanded, loadRoot]);

  const handleToggle = () => {
    if (!expanded) setDirExpanded(rootPath, true);
    else toggleDir(rootPath);
  };

  const handleRootContext = (e: React.MouseEvent) => {
    e.preventDefault();
    setRootCtx({ x: e.clientX, y: e.clientY });
  };

  const handleItemContext = (e: React.MouseEvent, entry: FileTreeEntry) => {
    e.preventDefault();
    setItemCtx({ x: e.clientX, y: e.clientY, entry, rootPath });
  };

  const handleRootCreateSubmit = useCallback(async (name: string) => {
    const creating = fileOps.creating;
    if (!creating || creating.parentPath !== rootPath) return;
    const newPath = `${rootPath}/${name}`;
    try {
      const exists = await nativeFs.exists(newPath);
      if (exists) {
        console.error(`"${name}" already exists`);
        setFileOps((s) => ({ ...s, creating: null }));
        return;
      }
      if (creating.type === "folder") {
        await nativeFs.mkdir(newPath);
      } else {
        await nativeFs.writeFile(newPath, "");
      }
      await refreshDir(rootPath);
      if (creating.type === "file") {
        openFile(newPath, "");
      }
    } catch (err) {
      console.error("Create failed:", err);
    }
    setFileOps((s) => ({ ...s, creating: null }));
  }, [fileOps.creating, rootPath, refreshDir, openFile]);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { parentPath: string; type: "file" | "folder" };
      if (detail.parentPath === rootPath) {
        setFileOps((s) => ({ ...s, creating: { parentPath: rootPath, type: detail.type } }));
      }
    };
    window.addEventListener("explorer:create", handler);
    return () => window.removeEventListener("explorer:create", handler);
  }, [rootPath]);

  const isCreatingAtRoot = fileOps.creating?.parentPath === rootPath;

  const entries = fileTree[rootPath];
  const sorted = entries ? sortEntries(entries) : undefined;

  const filteredEntries = useMemo(() => {
    if (!sorted) return undefined;
    let result = sorted;
    if (!showIgnored && gitFilter) {
      result = result.filter((e) => !gitFilter(e.path));
    }
    if (filter) {
      result = result.filter((e) => matchesFilter(e, filter, fileTree));
    }
    return result;
  }, [sorted, showIgnored, gitFilter, filter, fileTree]);

  return (
    <FileOpContext.Provider value={{ ops: fileOps, setOps: setFileOps, refreshDir }}>
      <div className="mb-0.5">
        <div
          className="sticky top-0 z-10 flex cursor-pointer select-none items-center gap-1 bg-gray-900 px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400 hover:text-gray-200"
          onClick={handleToggle}
          onContextMenu={handleRootContext}
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span className="truncate">{basename(rootPath)}</span>
        </div>

        {expanded && (
          <div role="tree">
            {isCreatingAtRoot && (
              <InlineInput
                onSubmit={handleRootCreateSubmit}
                onCancel={() => setFileOps((s) => ({ ...s, creating: null }))}
                placeholder={fileOps.creating?.type === "folder" ? "Folder name" : "File name"}
                depth={1}
                icon={
                  fileOps.creating?.type === "folder"
                    ? <Folder size={16} className="shrink-0 text-amber-400" />
                    : <File size={16} className="shrink-0 text-gray-500" />
                }
              />
            )}
            {loading && !sorted && (
              <div className="flex items-center gap-1.5 px-4 py-2 text-xs text-gray-500">
                <Loader2 size={12} className="animate-spin" />
                Loading…
              </div>
            )}
            {filteredEntries?.map((entry) => (
              <TreeNode
                key={entry.path}
                entry={entry}
                depth={1}
                rootPath={rootPath}
                onContextMenu={handleItemContext}
                filter={filter}
                gitignoreFilter={gitFilter}
                showIgnored={showIgnored}
              />
            ))}
          </div>
        )}

        {rootCtx && (
          <RootContextMenu
            x={rootCtx.x}
            y={rootCtx.y}
            rootPath={rootPath}
            onClose={() => setRootCtx(null)}
          />
        )}
        {itemCtx && (
          <ContextMenuOverlay menu={itemCtx} onClose={() => setItemCtx(null)} />
        )}
      </div>
    </FileOpContext.Provider>
  );
}

// ─── Agent Suggested Files Section ─────────────────────────────────────

function getRelativeTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  return `${Math.floor(diff / 3600000)}h ago`;
}

function AgentSuggestedSection() {
  const suggestedFiles = useCodeStore((s) => s.agentSuggestedFiles);
  const clearAgentSuggestedFiles = useCodeStore((s) => s.clearAgentSuggestedFiles);
  const openFile = useCodeStore((s) => s.openFile);
  const [collapsed, setCollapsed] = useState(false);

  if (suggestedFiles.length === 0) return null;

  const handleOpenFile = async (filePath: string) => {
    const content = await nativeFs.readFile(filePath);
    if (content !== null) openFile(filePath, content);
  };

  return (
    <div className="border-b border-gray-800">
      <div
        className="flex items-center justify-between px-3 py-1.5 cursor-pointer hover:bg-gray-800/50"
        onClick={() => setCollapsed((v) => !v)}
      >
        <div className="flex items-center gap-1.5 text-[11px] font-semibold text-purple-300 uppercase tracking-wider">
          <ChevronRight
            size={12}
            className={`transition-transform ${collapsed ? "" : "rotate-90"}`}
          />
          <Sparkles size={11} />
          DAN Suggested
          <span className="text-gray-500 font-normal normal-case">
            ({suggestedFiles.length})
          </span>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            clearAgentSuggestedFiles();
          }}
          className="text-gray-600 hover:text-gray-400"
        >
          <X size={11} />
        </button>
      </div>
      {!collapsed && (
        <div className="pb-1">
          {suggestedFiles.map((file) => (
            <div
              key={file.path}
              className="flex items-center gap-1.5 px-5 py-0.5 text-[11px] text-gray-300 hover:bg-gray-800/60 cursor-pointer"
              onClick={() => handleOpenFile(file.path)}
            >
              <FileText size={12} className="text-gray-500 shrink-0" />
              <span className="truncate">
                {file.path.split("/").pop()}
              </span>
              {file.isNew && (
                <span className="text-[9px] bg-green-800/40 text-green-300 rounded px-1">
                  new
                </span>
              )}
              <span className="text-gray-600 ml-auto text-[10px]">
                {getRelativeTime(file.timestamp)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Recent Files Section ──────────────────────────────────────────────

function RecentFilesSection() {
  const recentFiles = useCodeStore((s) => s.recentFiles);
  const openFile = useCodeStore((s) => s.openFile);
  const openFiles = useCodeStore((s) => s.openFiles);
  const clearRecentFiles = useCodeStore((s) => s.clearRecentFiles);
  const [collapsed, setCollapsed] = useState(false);

  if (recentFiles.length === 0) return null;

  return (
    <div className="border-b border-gray-800">
      <button
        className="flex items-center gap-1.5 w-full px-3 py-1.5 text-[11px] font-semibold uppercase tracking-widest text-gray-400 hover:text-gray-300"
        onClick={() => setCollapsed(!collapsed)}
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
        <Clock size={12} />
        Recent Files
        <span className="ml-auto flex items-center gap-1.5">
          <span className="text-[10px] text-gray-600 font-normal">{recentFiles.length}</span>
          <span
            role="button"
            tabIndex={0}
            className="rounded p-0.5 text-gray-600 hover:text-gray-300 hover:bg-gray-700/60 transition"
            title="Clear Recent Files"
            onClick={(e) => {
              e.stopPropagation();
              clearRecentFiles();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.stopPropagation();
                clearRecentFiles();
              }
            }}
          >
            <Trash2 size={12} />
          </span>
        </span>
      </button>

      {!collapsed &&
        recentFiles.slice(0, 15).map((filePath) => {
          const isOpen = openFiles.some((f) => f.path === filePath);
          const fileName = filePath.split("/").pop() ?? filePath;

          return (
            <button
              key={filePath}
              className="flex items-center gap-2 w-full px-4 py-1 text-xs hover:bg-gray-800/60 text-left group"
              title={filePath}
              onClick={async () => {
                const content = await nativeFs.readFile(filePath);
                if (content !== null) openFile(filePath, content);
              }}
            >
              {getFileIcon(fileName, false, false)}
              <span className="truncate text-gray-300">{fileName}</span>
              {isOpen && (
                <span className="ml-auto w-1.5 h-1.5 rounded-full bg-blue-500 shrink-0" />
              )}
            </button>
          );
        })}
    </div>
  );
}

// ─── Empty State ───────────────────────────────────────────────────────

function EmptyState() {
  const electron = isElectron();
  const addPinnedRoot = useCodeStore((s) => s.addPinnedRoot);

  const handleOpen = async () => {
    if (!electron) return;
    const dir = await nativeDialog.openDirectory();
    if (dir) addPinnedRoot(dir);
  };

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
      <Folder size={32} className="text-gray-600" />
      <p className="text-sm text-gray-400">No folder opened</p>
      <button
        className="rounded-md bg-blue-600 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-gray-700 disabled:text-gray-400"
        onClick={handleOpen}
        disabled={!electron}
      >
        Open Folder
      </button>
      {!electron && (
        <p className="max-w-[220px] text-[10px] text-gray-500">
          Opening folders requires the desktop app. Browser preview stays browsable for demos only.
        </p>
      )}
      <p className="text-[10px] text-gray-600">
        <kbd className="rounded border border-gray-700 bg-gray-800 px-1 py-0.5 font-mono text-[10px]">
          ⌘O
        </kbd>
      </p>
    </div>
  );
}

// ─── Header ────────────────────────────────────────────────────────────

function ExplorerHeader({
  onNewFile,
  onNewFolder,
  showIgnored,
  onToggleIgnored,
}: {
  onNewFile: () => void;
  onNewFolder: () => void;
  showIgnored: boolean;
  onToggleIgnored: () => void;
}) {
  const electron = isElectron();
  const addPinnedRoot = useCodeStore((s) => s.addPinnedRoot);
  const expandedDirs = useCodeStore((s) => s.expandedDirs);
  const setDirExpanded = useCodeStore((s) => s.setDirExpanded);

  const handleAddFolder = async () => {
    if (!electron) return;
    const dir = await nativeDialog.openDirectory();
    if (dir) addPinnedRoot(dir);
  };

  const handleCollapseAll = () => {
    for (const key of Object.keys(expandedDirs)) {
      if (expandedDirs[key]) setDirExpanded(key, false);
    }
  };

  const btnClass =
    "rounded p-1 text-gray-500 hover:bg-gray-700/60 hover:text-gray-300 transition";

  return (
    <div className="flex items-center justify-between border-b border-gray-800 px-3 py-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">
        Explorer
      </span>
      <div className="flex items-center gap-0.5">
        <button className={btnClass} title="New File…" onClick={onNewFile}>
          <FilePlus size={14} />
        </button>
        <button className={btnClass} title="New Folder…" onClick={onNewFolder}>
          <FolderPlus size={14} />
        </button>
        <button
          className={`${btnClass} ${!showIgnored ? "text-blue-400" : ""}`}
          title={showIgnored ? "Hide gitignored files" : "Show gitignored files"}
          onClick={onToggleIgnored}
        >
          {showIgnored ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
        <button className={btnClass} title="Collapse All" onClick={handleCollapseAll}>
          <ChevronsDownUp size={14} />
        </button>
        <button
          className={btnClass}
          title={electron ? "Add Folder to Workspace" : "Desktop app required"}
          onClick={handleAddFolder}
          disabled={!electron}
        >
          <FolderPlusIcon size={14} />
        </button>
      </div>
    </div>
  );
}

// ─── Main Component ────────────────────────────────────────────────────

export default function FileExplorer() {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const setDirExpanded = useCodeStore((s) => s.setDirExpanded);
  const [filter, setFilter] = useState("");
  const [showIgnored, setShowIgnored] = useState(false);
  const [gitignoreFilter, setGitignoreFilter] = useState<((path: string) => boolean) | null>(null);

  useEffect(() => {
    if (pinnedRoots.length === 0) {
      setGitignoreFilter(null);
      return;
    }
    nativeFs.readGitignore(pinnedRoots[0]).then((content) => {
      if (content) {
        setGitignoreFilter(() => parseGitignore(content));
      } else {
        setGitignoreFilter(null);
      }
    });
  }, [pinnedRoots]);

  const triggerCreate = useCallback((type: "file" | "folder") => {
    if (pinnedRoots.length === 0) return;
    const root = pinnedRoots[0];
    setDirExpanded(root, true);
    window.dispatchEvent(new CustomEvent("explorer:create", { detail: { parentPath: root, type } }));
  }, [pinnedRoots, setDirExpanded]);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-gray-900 text-gray-300 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-700 [&::-webkit-scrollbar]:w-1.5">
      <ExplorerHeader
        onNewFile={() => triggerCreate("file")}
        onNewFolder={() => triggerCreate("folder")}
        showIgnored={showIgnored}
        onToggleIgnored={() => setShowIgnored((v) => !v)}
      />
      {pinnedRoots.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          <ExplorerFilter value={filter} onChange={setFilter} />
          <div className="flex-1 overflow-y-auto [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-700 [&::-webkit-scrollbar]:w-1.5">
            <RecentFilesSection />
            <AgentSuggestedSection />
            {pinnedRoots.map((root) => (
              <PinnedRootSection
                key={root}
                rootPath={root}
                filter={filter}
                showIgnored={showIgnored}
                gitignoreFilter={gitignoreFilter}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
