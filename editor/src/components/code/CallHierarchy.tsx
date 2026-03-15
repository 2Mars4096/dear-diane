import { useState, useEffect, useCallback, useRef } from "react";
import {
  X,
  ChevronRight,
  ChevronDown,
  ArrowDownToLine,
  ArrowUpFromLine,
  Loader2,
  FunctionSquare,
  FileCode2,
  Box,
} from "lucide-react";
import { nativeLsp } from "../../lib/electronBridge";
import { useCodeStore } from "../../store/useCodeStore";

export interface CallHierarchyItem {
  name: string;
  kind: number;
  uri: string;
  range: { start: { line: number; character: number }; end: { line: number; character: number } };
  selectionRange: { start: { line: number; character: number }; end: { line: number; character: number } };
  detail?: string;
}

interface CallNode {
  item: CallHierarchyItem;
  fromRanges?: Array<{ start: { line: number; character: number }; end: { line: number; character: number } }>;
  children: CallNode[] | null;
  loading: boolean;
  expanded: boolean;
}

type Tab = "incoming" | "outgoing";

function kindIcon(kind: number) {
  if (kind === 12 || kind === 6) return <FunctionSquare size={12} className="text-purple-400 shrink-0" />;
  if (kind === 5) return <Box size={12} className="text-yellow-400 shrink-0" />;
  return <FileCode2 size={12} className="text-blue-400 shrink-0" />;
}

function basename(uri: string): string {
  const path = uri.replace(/^file:\/\//, "");
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function regexFallbackOutgoing(code: string, functionName: string): CallNode[] {
  const callPattern = /\b([a-zA-Z_$][\w$]*)\s*\(/g;
  const found = new Set<string>();
  let match: RegExpExecArray | null;
  while ((match = callPattern.exec(code)) !== null) {
    const name = match[1];
    if (name !== functionName && !["if", "for", "while", "switch", "catch", "return", "throw", "new", "typeof", "instanceof", "void", "delete"].includes(name)) {
      found.add(name);
    }
  }
  return Array.from(found).map((name) => ({
    item: {
      name,
      kind: 12,
      uri: "",
      range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
      selectionRange: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
      detail: "(regex match)",
    },
    fromRanges: [],
    children: null,
    loading: false,
    expanded: false,
  }));
}

function regexFallbackIncoming(openFiles: Array<{ path: string; content: string }>, functionName: string): CallNode[] {
  const results: CallNode[] = [];
  const pattern = new RegExp(`\\b${functionName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\(`, "g");

  for (const file of openFiles) {
    const lines = file.content.split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (pattern.test(lines[i])) {
        results.push({
          item: {
            name: `${basename(file.path)}:${i + 1}`,
            kind: 12,
            uri: `file://${file.path}`,
            range: { start: { line: i, character: 0 }, end: { line: i, character: lines[i].length } },
            selectionRange: { start: { line: i, character: 0 }, end: { line: i, character: lines[i].length } },
            detail: lines[i].trim().slice(0, 80),
          },
          fromRanges: [{ start: { line: i, character: 0 }, end: { line: i, character: lines[i].length } }],
          children: null,
          loading: false,
          expanded: false,
        });
        pattern.lastIndex = 0;
      }
      pattern.lastIndex = 0;
    }
  }
  return results;
}

interface Props {
  filePath: string;
  line: number;
  character: number;
  onClose: () => void;
  onNavigate: (uri: string, line: number, character: number) => void;
  style?: React.CSSProperties;
}

export default function CallHierarchy({
  filePath,
  line,
  character,
  onClose,
  onNavigate,
  style,
}: Props) {
  const [tab, setTab] = useState<Tab>("incoming");
  const [rootItem, setRootItem] = useState<CallHierarchyItem | null>(null);
  const [nodes, setNodes] = useState<CallNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const openFiles = useCodeStore((s) => s.openFiles);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onClose]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", handleKey, true);
    return () => window.removeEventListener("keydown", handleKey, true);
  }, [onClose]);

  const prepareHierarchy = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await nativeLsp.prepareCallHierarchy({
        filePath,
        line,
        character,
      });

      if (!result || (Array.isArray(result) && result.length === 0)) {
        // Fallback to regex
        const file = openFiles.find((f) => f.path === filePath);
        if (file) {
          const lines = file.content.split("\n");
          const targetLine = lines[line] ?? "";
          const wordMatch = /\b([a-zA-Z_$][\w$]*)\b/.exec(
            targetLine.slice(character),
          );
          const name = wordMatch?.[1] ?? "unknown";
          setRootItem({
            name,
            kind: 12,
            uri: `file://${filePath}`,
            range: { start: { line, character: 0 }, end: { line, character: targetLine.length } },
            selectionRange: { start: { line, character }, end: { line, character: character + name.length } },
            detail: "(regex fallback)",
          });
        }
        setLoading(false);
        return;
      }

      const items = Array.isArray(result) ? result : [result];
      setRootItem(items[0]);
    } catch {
      setError("Call hierarchy not supported by LSP");
      const file = openFiles.find((f) => f.path === filePath);
      if (file) {
        const lines = file.content.split("\n");
        const targetLine = lines[line] ?? "";
        const wordMatch = /\b([a-zA-Z_$][\w$]*)\b/.exec(targetLine.slice(character));
        const name = wordMatch?.[1] ?? "unknown";
        setRootItem({
          name,
          kind: 12,
          uri: `file://${filePath}`,
          range: { start: { line, character: 0 }, end: { line, character: targetLine.length } },
          selectionRange: { start: { line, character }, end: { line, character: character + name.length } },
          detail: "(regex fallback)",
        });
      }
    }
    setLoading(false);
  }, [filePath, line, character, openFiles]);

  useEffect(() => {
    prepareHierarchy();
  }, [prepareHierarchy]);

  const loadCalls = useCallback(
    async (item: CallHierarchyItem, direction: Tab): Promise<CallNode[]> => {
      try {
        const result =
          direction === "incoming"
            ? await nativeLsp.incomingCalls({ item })
            : await nativeLsp.outgoingCalls({ item });

        if (result && Array.isArray(result) && result.length > 0) {
          return result.map((entry: any) => ({
            item: direction === "incoming" ? entry.from : entry.to,
            fromRanges: entry.fromRanges,
            children: null,
            loading: false,
            expanded: false,
          }));
        }
      } catch {
        // LSP doesn't support call hierarchy
      }

      // Regex fallback
      if (direction === "outgoing") {
        const uri = item.uri.replace(/^file:\/\//, "");
        const file = openFiles.find((f) => f.path === uri);
        if (file) {
          const startLine = item.range.start.line;
          const endLine = item.range.end.line;
          const body = file.content
            .split("\n")
            .slice(startLine, endLine + 1)
            .join("\n");
          return regexFallbackOutgoing(body, item.name);
        }
      } else {
        return regexFallbackIncoming(
          openFiles.map((f) => ({ path: f.path, content: f.content })),
          item.name,
        );
      }
      return [];
    },
    [openFiles],
  );

  useEffect(() => {
    if (!rootItem) return;
    setNodes([]);
    (async () => {
      const children = await loadCalls(rootItem, tab);
      setNodes(children);
    })();
  }, [rootItem, tab, loadCalls]);

  const toggleExpand = useCallback(
    async (index: number[]) => {
      setNodes((prev) => {
        const next = [...prev];
        let target = next;
        for (let i = 0; i < index.length - 1; i++) {
          if (!target[index[i]]?.children) return prev;
          target = [...target[index[i]].children!];
          // Update parent reference
          let parentRef = next;
          for (let j = 0; j < i; j++) {
            parentRef = parentRef[index[j]].children!;
          }
          parentRef[index[i]] = { ...parentRef[index[i]], children: target };
        }
        const lastIdx = index[index.length - 1];
        const node = target[lastIdx];
        if (node.expanded) {
          target[lastIdx] = { ...node, expanded: false };
        } else if (node.children !== null) {
          target[lastIdx] = { ...node, expanded: true };
        } else {
          target[lastIdx] = { ...node, loading: true };
          // Load children async
          loadCalls(node.item, tab).then((children) => {
            setNodes((current) => {
              const updated = JSON.parse(JSON.stringify(current));
              let t = updated;
              for (let i = 0; i < index.length - 1; i++) {
                t = t[index[i]]?.children ?? t;
              }
              if (t[lastIdx]) {
                t[lastIdx].children = children;
                t[lastIdx].loading = false;
                t[lastIdx].expanded = true;
              }
              return updated;
            });
          });
        }
        return next;
      });
    },
    [loadCalls, tab],
  );

  const handleNavigate = useCallback(
    (item: CallHierarchyItem) => {
      const uri = item.uri.replace(/^file:\/\//, "");
      onNavigate(uri, item.selectionRange.start.line + 1, item.selectionRange.start.character + 1);
    },
    [onNavigate],
  );

  return (
    <div
      ref={panelRef}
      className="fixed z-50 w-[500px] max-h-[400px] bg-[#252526] border border-[#3c3c3c] rounded-lg shadow-2xl flex flex-col overflow-hidden"
      style={style}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[#3c3c3c] shrink-0">
        <span className="text-xs font-medium text-gray-300">Call Hierarchy</span>
        {rootItem && (
          <span className="text-xs text-gray-500 truncate flex-1">
            — {rootItem.name}
          </span>
        )}
        <button
          onClick={onClose}
          className="p-0.5 text-gray-500 hover:text-gray-300"
        >
          <X size={14} />
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[#3c3c3c] shrink-0">
        <button
          onClick={() => setTab("incoming")}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium border-b-2 transition-colors ${
            tab === "incoming"
              ? "text-white border-blue-500"
              : "text-gray-500 hover:text-gray-300 border-transparent"
          }`}
        >
          <ArrowDownToLine size={11} />
          Incoming Calls
        </button>
        <button
          onClick={() => setTab("outgoing")}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium border-b-2 transition-colors ${
            tab === "outgoing"
              ? "text-white border-blue-500"
              : "text-gray-500 hover:text-gray-300 border-transparent"
          }`}
        >
          <ArrowUpFromLine size={11} />
          Outgoing Calls
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {loading ? (
          <div className="flex items-center gap-2 p-4 text-xs text-gray-400">
            <Loader2 size={12} className="animate-spin" />
            Preparing call hierarchy...
          </div>
        ) : nodes.length === 0 ? (
          <div className="p-4 text-xs text-gray-500 text-center">
            No {tab === "incoming" ? "callers" : "callees"} found
          </div>
        ) : (
          nodes.map((node, i) => (
            <CallNodeRow
              key={`${node.item.name}-${i}`}
              node={node}
              depth={0}
              onToggle={() => toggleExpand([i])}
              onNavigate={handleNavigate}
              onChildToggle={(childPath) => toggleExpand([i, ...childPath])}
            />
          ))
        )}
      </div>

      {error && (
        <div className="px-3 py-1 border-t border-[#3c3c3c] text-[10px] text-yellow-500">
          {error}
        </div>
      )}
    </div>
  );
}

function CallNodeRow({
  node,
  depth,
  onToggle,
  onNavigate,
  onChildToggle,
}: {
  node: CallNode;
  depth: number;
  onToggle: () => void;
  onNavigate: (item: CallHierarchyItem) => void;
  onChildToggle: (path: number[]) => void;
}) {
  const hasChildren = node.children === null || (node.children && node.children.length > 0);
  const fileLoc = node.item.uri
    ? `${basename(node.item.uri)}:${node.item.selectionRange.start.line + 1}`
    : "";

  return (
    <div>
      <div
        className="flex items-center gap-1.5 px-2 py-1 hover:bg-[#2a2d2e] cursor-pointer group"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className="w-4 flex-shrink-0 flex items-center justify-center text-gray-600 hover:text-gray-400"
        >
          {node.loading ? (
            <Loader2 size={10} className="animate-spin" />
          ) : hasChildren ? (
            node.expanded ? (
              <ChevronDown size={10} />
            ) : (
              <ChevronRight size={10} />
            )
          ) : (
            <span className="w-2.5" />
          )}
        </button>
        {kindIcon(node.item.kind)}
        <button
          onClick={() => onNavigate(node.item)}
          className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
        >
          <span className="text-[11px] text-gray-200 truncate">
            {node.item.name}
          </span>
          {fileLoc && (
            <span className="text-[10px] text-gray-500 truncate shrink-0">
              {fileLoc}
            </span>
          )}
        </button>
        {node.item.detail && (
          <span className="text-[9px] text-gray-600 truncate max-w-[120px]">
            {node.item.detail}
          </span>
        )}
      </div>
      {node.expanded &&
        node.children?.map((child, i) => (
          <CallNodeRow
            key={`${child.item.name}-${i}`}
            node={child}
            depth={depth + 1}
            onToggle={() => onChildToggle([i])}
            onNavigate={onNavigate}
            onChildToggle={(path) => onChildToggle([i, ...path])}
          />
        ))}
    </div>
  );
}
