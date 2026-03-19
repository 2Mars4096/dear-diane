import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  FolderOpen,
  Layers,
  FileText,
  Code,
  BookOpen,
  MessageSquare,
  Clock,
} from "lucide-react";
import hljs from "../lib/hljs";
import { useGraphStore } from "../store/useGraphStore";
import { NodeIcon } from "../lib/nodeIcons";
import { NODE_TYPE_CATALOG, NODE_DESCRIPTIONS } from "../types/graph";
import type { MentionType } from "../lib/mentionParser";
import * as api from "../lib/api";
import { sanitizeHtml } from "../lib/sanitizeHtml";

type MentionSection = MentionType;

interface MentionItem {
  section: MentionSection;
  id: string;
  name: string;
  nodeType?: string;
}

interface MentionAutocompleteProps {
  query: string;
  anchorRect: { top: number; left: number } | null;
  onSelect: (mention: {
    type: MentionSection;
    id: string;
    name: string;
  }) => void;
  onDismiss: () => void;
}

// ---------------------------------------------------------------------------
// Recent mentions (localStorage)
// ---------------------------------------------------------------------------

const RECENT_MENTIONS_KEY = "dan_recent_mentions";
const MAX_RECENT = 10;

interface RecentMention {
  type: MentionSection;
  identifier: string;
  label: string;
  nodeType?: string;
}

function getRecentMentions(): RecentMention[] {
  try {
    const raw = localStorage.getItem(RECENT_MENTIONS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function pushRecentMention(mention: RecentMention): void {
  const list = getRecentMentions().filter(
    (m) => !(m.type === mention.type && m.identifier === mention.identifier),
  );
  list.unshift(mention);
  localStorage.setItem(
    RECENT_MENTIONS_KEY,
    JSON.stringify(list.slice(0, MAX_RECENT)),
  );
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SUBGRAPH_NODE_TYPES = new Set([
  "composite",
  "while_loop",
  "for_each",
  "parallel_subagents",
]);

const CATEGORY_PREFIXES: Record<string, MentionSection> = {
  "file:": "file",
  "code:": "code",
  "docs:": "docs",
  "chat:": "chat",
};

const typeLabel: Record<string, string> = {
  ...Object.fromEntries(NODE_TYPE_CATALOG.map((c) => [c.type, c.label])),
  gate: "Gate",
};

const FUZZY_THRESHOLD = 0.3;

const SECTION_ORDER: MentionSection[] = [
  "node",
  "workflow",
  "subgraph",
  "file",
  "symbol",
  "folder",
  "code",
  "docs",
  "chat",
];

const SECTION_LABELS: Record<MentionSection, string> = {
  node: "Nodes",
  workflow: "Workflows",
  subgraph: "Sub-graphs",
  file: "Files",
  symbol: "Symbols",
  folder: "Folders",
  code: "Code",
  docs: "Docs",
  chat: "Past Chats",
};

// ---------------------------------------------------------------------------
// Fuzzy matching
// ---------------------------------------------------------------------------

function fuzzyScore(query: string, target: string): number {
  if (!query) return 1;
  const q = query.toLowerCase();
  const t = target.toLowerCase();

  if (t.includes(q)) {
    return 1.0 - q.length / (t.length + 1) * 0.1;
  }

  let qi = 0;
  let gaps = 0;
  let lastMatch = -1;
  const matchedIndices: number[] = [];

  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      if (lastMatch >= 0) gaps += ti - lastMatch - 1;
      lastMatch = ti;
      matchedIndices.push(ti);
      qi++;
    }
  }

  if (qi < q.length) return 0;

  const matchRatio = q.length / t.length;
  const gapPenalty = gaps / (t.length + 1);
  const startBonus = matchedIndices[0] === 0 ? 0.1 : 0;

  return Math.max(0, matchRatio - gapPenalty * 0.5 + startBonus);
}

function getFuzzyMatchIndices(query: string, target: string): number[] {
  if (!query) return [];
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  const indices: number[] = [];
  let qi = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      indices.push(ti);
      qi++;
    }
  }
  return qi === q.length ? indices : [];
}

function highlightMatch(name: string, query: string) {
  if (!query) return <>{name}</>;

  const lower = name.toLowerCase();
  const idx = lower.indexOf(query.toLowerCase());
  if (idx !== -1) {
    return (
      <>
        {name.slice(0, idx)}
        <span className="font-bold">{name.slice(idx, idx + query.length)}</span>
        {name.slice(idx + query.length)}
      </>
    );
  }

  const matched = getFuzzyMatchIndices(query, name);
  if (matched.length === 0) return <>{name}</>;

  const matchSet = new Set(matched);
  const parts: React.ReactElement[] = [];
  let i = 0;
  while (i < name.length) {
    if (matchSet.has(i)) {
      let end = i;
      while (end < name.length && matchSet.has(end)) end++;
      parts.push(<span key={i} className="font-bold">{name.slice(i, end)}</span>);
      i = end;
    } else {
      let end = i;
      while (end < name.length && !matchSet.has(end)) end++;
      parts.push(<span key={i}>{name.slice(i, end)}</span>);
      i = end;
    }
  }
  return <>{parts}</>;
}

function parsePrefixQuery(query: string): {
  category: MentionSection | null;
  subquery: string;
} {
  for (const [prefix, cat] of Object.entries(CATEGORY_PREFIXES)) {
    if (query.startsWith(prefix)) {
      return { category: cat, subquery: query.slice(prefix.length) };
    }
  }
  return { category: null, subquery: query };
}

// ---------------------------------------------------------------------------
// Section icon
// ---------------------------------------------------------------------------

function SectionIcon({
  section,
  nodeType,
}: {
  section: MentionSection;
  nodeType?: string;
}) {
  switch (section) {
    case "node":
      return (
        <span className="text-gray-500 flex-shrink-0">
          <NodeIcon type={nodeType ?? ""} />
        </span>
      );
    case "workflow":
      return <FolderOpen size={14} className="text-green-500 flex-shrink-0" />;
    case "subgraph":
      return <Layers size={14} className="text-amber-500 flex-shrink-0" />;
    case "file":
      return <FileText size={14} className="text-purple-500 flex-shrink-0" />;
    case "symbol":
      return <Code size={14} className="text-cyan-500 flex-shrink-0" />;
    case "folder":
      return <FolderOpen size={14} className="text-orange-500 flex-shrink-0" />;
    case "code":
      return <Code size={14} className="text-cyan-500 flex-shrink-0" />;
    case "docs":
      return <BookOpen size={14} className="text-emerald-500 flex-shrink-0" />;
    case "chat":
      return (
        <MessageSquare size={14} className="text-rose-500 flex-shrink-0" />
      );
  }
}

// ---------------------------------------------------------------------------
// Preview tooltip (shown on hover with debounce)
// ---------------------------------------------------------------------------

function escapeHtmlSimple(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

type NodeInfo = {
  id: string;
  node_type: string;
  name: string;
  description?: string;
  input_ports: Array<{ name: string }>;
  output_ports: Array<{ name: string }>;
};

function MentionPreviewTooltip({
  item,
  anchorEl,
  danGraph,
}: {
  item: MentionItem;
  anchorEl: HTMLElement;
  danGraph: { nodes: NodeInfo[] } | null;
}) {
  const rect = anchorEl.getBoundingClientRect();
  const parentRect = anchorEl
    .closest("[data-mention-dropdown]")
    ?.getBoundingClientRect();
  const dropdownRight = parentRect?.right ?? rect.right;
  const dropdownLeft = parentRect?.left ?? rect.left;
  const spaceRight = window.innerWidth - dropdownRight;

  const style: React.CSSProperties = {
    position: "fixed",
    top: Math.min(rect.top, window.innerHeight - 200),
    ...(spaceRight >= 240
      ? { left: dropdownRight + 6 }
      : { right: window.innerWidth - dropdownLeft + 6 }),
    zIndex: 51,
  };

  let content: React.ReactNode;

  if (item.section === "node" || item.section === "subgraph") {
    const node = danGraph?.nodes.find((n) => n.id === item.id);
    const desc =
      node?.description ||
      NODE_DESCRIPTIONS[node?.node_type ?? ""]?.description ||
      "";
    content = (
      <>
        <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">
          {typeLabel[item.nodeType ?? ""] ?? item.nodeType ?? "Node"}
        </div>
        {desc && <p className="text-xs text-gray-600 mb-1.5">{desc}</p>}
        {node && (
          <div className="text-[10px] text-gray-500 space-y-0.5">
            {node.input_ports.length > 0 && (
              <div>
                <span className="text-gray-400">In:</span>{" "}
                {node.input_ports.map((p) => p.name).join(", ")}
              </div>
            )}
            {node.output_ports.length > 0 && (
              <div>
                <span className="text-gray-400">Out:</span>{" "}
                {node.output_ports.map((p) => p.name).join(", ")}
              </div>
            )}
          </div>
        )}
      </>
    );
  } else if (item.section === "code") {
    const ext = item.name.includes(".")
      ? (item.name.split(".").pop() ?? "")
      : "";
    let highlighted = escapeHtmlSimple(item.name);
    try {
      if (ext && hljs.getLanguage(ext)) {
        highlighted = hljs.highlight(item.name, { language: ext }).value;
      }
    } catch {
      /* ignore */
    }
    const safeHighlighted = sanitizeHtml(highlighted);
    content = (
      <>
        <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">
          Code Reference
        </div>
        <pre className="text-[11px] bg-gray-900 text-gray-100 rounded p-2 overflow-x-auto font-mono leading-relaxed">
          <code dangerouslySetInnerHTML={{ __html: safeHighlighted }} />
        </pre>
      </>
    );
  } else if (item.section === "file") {
    const parts = item.name.split("/");
    content = (
      <>
        <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">
          File
        </div>
        <p className="text-xs text-gray-600 font-mono break-all">
          {parts.length > 2
            ? `…/${parts.slice(-2).join("/")}`
            : item.name}
        </p>
      </>
    );
  } else if (item.section === "docs") {
    content = (
      <>
        <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">
          Documentation
        </div>
        <p className="text-xs text-gray-600">{item.name}</p>
      </>
    );
  } else if (item.section === "chat") {
    content = (
      <>
        <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">
          Chat Thread
        </div>
        <p className="text-xs text-gray-600">{item.name}</p>
      </>
    );
  } else {
    content = (
      <>
        <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">
          {SECTION_LABELS[item.section] ?? "Reference"}
        </div>
        <p className="text-xs text-gray-600">{item.name}</p>
      </>
    );
  }

  return (
    <div
      style={style}
      className="bg-white border border-gray-200 rounded-lg shadow-lg p-3 w-56 pointer-events-none"
    >
      {content}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function MentionAutocomplete({
  query,
  anchorRect,
  onSelect,
  onDismiss,
}: MentionAutocompleteProps) {
  const danGraph = useGraphStore((s) => s.danGraph);
  const graphList = useGraphStore((s) => s.graphList);
  const graphId = useGraphStore((s) => s.graphId);

  const [fileList, setFileList] = useState<string[]>([]);
  const [docsList, setDocsList] = useState<string[]>([]);
  const [codeRefs, setCodeRefs] = useState<string[]>([]);
  const [chatThreads, setChatThreads] = useState<
    Array<{ id: string; title: string }>
  >([]);

  const [hoveredItem, setHoveredItem] = useState<MentionItem | null>(null);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const filesFetched = useRef(false);
  const docsFetched = useRef(false);
  const codeRefsFetched = useRef<string | null>(null);
  const chatsFetched = useRef<string | null>(null);

  const { category, subquery } = useMemo(
    () => parsePrefixQuery(query),
    [query],
  );

  useEffect(() => {
    if (category === "file" && !filesFetched.current) {
      filesFetched.current = true;
      api.listWorkspaceFiles().then((r) => setFileList(r.files)).catch(() => {});
    }
  }, [category]);

  useEffect(() => {
    if (category === "docs" && !docsFetched.current) {
      docsFetched.current = true;
      api.listDocs().then((r) => setDocsList(r.docs)).catch(() => {});
    }
  }, [category]);

  useEffect(() => {
    if (category === "code" && graphId && codeRefsFetched.current !== graphId) {
      codeRefsFetched.current = graphId;
      api.listCodeRefs(graphId).then((r) => setCodeRefs(r.refs)).catch(() => {});
    }
  }, [category, graphId]);

  useEffect(() => {
    if (category === "chat" && graphId && chatsFetched.current !== graphId) {
      chatsFetched.current = graphId;
      api
        .listChatThreads(graphId)
        .then((r) =>
          setChatThreads(
            r.threads.map((t) => ({ id: t.id, title: t.title || "Untitled" })),
          ),
        )
        .catch(() => {});
    }
  }, [category, graphId]);

  useEffect(() => {
    return () => {
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    };
  }, []);

  // Recent mentions filtered by current query
  const recentFiltered = useMemo(() => {
    const stored = getRecentMentions();
    if (stored.length === 0) return [];
    return stored
      .filter((r) => {
        if (category && r.type !== category) return false;
        if (!subquery) return true;
        return fuzzyScore(subquery, r.label) >= FUZZY_THRESHOLD;
      })
      .map(
        (r): MentionItem => ({
          section: r.type,
          id: r.identifier,
          name: r.label,
          nodeType: r.nodeType,
        }),
      );
  }, [category, subquery]);

  const recentKeys = useMemo(
    () => new Set(recentFiltered.map((r) => `${r.section}:${r.id}`)),
    [recentFiltered],
  );

  const items = useMemo(() => {
    const scored: Array<MentionItem & { score: number }> = [];
    const q = subquery;

    const shouldInclude = (sec: MentionSection) =>
      category === null || category === sec;

    const tryAdd = (sec: MentionSection, id: string, name: string, nodeType?: string) => {
      if (!q) {
        scored.push({ section: sec, id, name, nodeType, score: 1 });
        return;
      }
      const s = fuzzyScore(q, name);
      if (s >= FUZZY_THRESHOLD) {
        scored.push({ section: sec, id, name, nodeType, score: s });
      }
    };

    if (shouldInclude("node") || shouldInclude("subgraph")) {
      if (danGraph) {
        for (const node of danGraph.nodes) {
          const sec = SUBGRAPH_NODE_TYPES.has(node.node_type)
            ? "subgraph"
            : "node";
          if (shouldInclude(sec)) tryAdd(sec, node.id, node.name, node.node_type);
        }
      }
    }

    if (shouldInclude("workflow")) {
      for (const g of graphList) tryAdd("workflow", g.graph_id, g.name);
    }

    if (shouldInclude("file")) {
      for (const f of fileList) tryAdd("file", f, f);
    }

    if (shouldInclude("code")) {
      for (const ref of codeRefs) tryAdd("code", ref, ref);
    }

    if (shouldInclude("docs")) {
      for (const d of docsList) tryAdd("docs", d, d);
    }

    if (shouldInclude("chat")) {
      for (const t of chatThreads) tryAdd("chat", t.id, t.title);
    }

    if (q) scored.sort((a, b) => b.score - a.score);
    return scored as MentionItem[];
  }, [
    danGraph,
    graphList,
    fileList,
    codeRefs,
    docsList,
    chatThreads,
    subquery,
    category,
  ]);

  const grouped = useMemo(() => {
    const map: Partial<Record<MentionSection, MentionItem[]>> = {};
    for (const sec of SECTION_ORDER) {
      const secItems = items.filter(
        (i) => i.section === sec && !recentKeys.has(`${i.section}:${i.id}`),
      );
      if (secItems.length > 0) map[sec] = secItems;
    }
    return map;
  }, [items, recentKeys]);

  const flatItems = useMemo(
    () => [
      ...recentFiltered,
      ...SECTION_ORDER.flatMap((sec) => grouped[sec] ?? []),
    ],
    [grouped, recentFiltered],
  );

  const [selectedIdx, setSelectedIdx] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => setSelectedIdx(0), [query]);

  const selectItem = useCallback(
    (item: MentionItem) => {
      pushRecentMention({
        type: item.section,
        identifier: item.id,
        label: item.name,
        nodeType: item.nodeType,
      });
      onSelect({ type: item.section, id: item.id, name: item.name });
    },
    [onSelect],
  );

  const handleItemHover = useCallback(
    (item: MentionItem, el: HTMLElement) => {
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = setTimeout(() => {
        setHoveredItem(item);
        setTooltipAnchor(el);
      }, 300);
    },
    [],
  );

  const handleItemLeave = useCallback(() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = null;
    setHoveredItem(null);
    setTooltipAnchor(null);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onDismiss();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIdx((i) => Math.min(i + 1, flatItems.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIdx((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" && flatItems.length > 0) {
        e.preventDefault();
        e.stopPropagation();
        selectItem(flatItems[selectedIdx]);
      }
    };

    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [flatItems, selectedIdx, selectItem, onDismiss]);

  useEffect(() => {
    const el = listRef.current?.querySelector("[data-selected]");
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedIdx]);

  if (!anchorRect) return null;

  const flipUp = anchorRect.top > window.innerHeight - 340;
  const style: React.CSSProperties = {
    position: "fixed",
    left: anchorRect.left,
    zIndex: 50,
    ...(flipUp
      ? { bottom: window.innerHeight - anchorRect.top + 4 }
      : { top: anchorRect.top + 4 }),
  };

  let runningIdx = 0;

  const renderItemButton = (item: MentionItem, idx: number) => {
    const selected = idx === selectedIdx;
    return (
      <button
        key={`${item.section}-${item.id}-${idx}`}
        data-selected={selected ? "" : undefined}
        onMouseDown={(e) => {
          e.preventDefault();
          selectItem(item);
        }}
        onMouseEnter={(e) => {
          setSelectedIdx(idx);
          handleItemHover(item, e.currentTarget);
        }}
        onMouseLeave={handleItemLeave}
        className={`flex items-center gap-2 w-full px-3 py-1.5 text-sm cursor-pointer text-left ${
          selected ? "bg-indigo-50" : "hover:bg-gray-50"
        }`}
      >
        <SectionIcon section={item.section} nodeType={item.nodeType} />

        <span className="truncate flex-1">
          {highlightMatch(item.name, subquery)}
        </span>

        {item.nodeType && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 flex-shrink-0">
            {typeLabel[item.nodeType] ?? item.nodeType}
          </span>
        )}
      </button>
    );
  };

  const renderRecentSection = () => {
    if (recentFiltered.length === 0) return null;
    const startIdx = runningIdx;
    runningIdx += recentFiltered.length;
    return (
      <div key="__recent">
        <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider px-3 py-1 flex items-center gap-1">
          <Clock size={9} />
          Recent
        </div>
        {recentFiltered.map((item, i) => renderItemButton(item, startIdx + i))}
      </div>
    );
  };

  const renderSection = (sec: MentionSection, sectionItems: MentionItem[]) => {
    if (sectionItems.length === 0) return null;
    const startIdx = runningIdx;
    runningIdx += sectionItems.length;
    return (
      <div key={sec}>
        <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider px-3 py-1">
          {SECTION_LABELS[sec]}
        </div>
        {sectionItems.map((item, i) => renderItemButton(item, startIdx + i))}
      </div>
    );
  };

  return (
    <div
      ref={listRef}
      data-mention-dropdown
      style={style}
      className="bg-white border border-gray-200 rounded-lg shadow-lg max-h-[300px] overflow-y-auto w-72"
    >
      {flatItems.length === 0 ? (
        <div className="px-3 py-3 text-sm text-gray-400 text-center">
          {category
            ? `No ${SECTION_LABELS[category].toLowerCase()} matches`
            : "No matches"}
        </div>
      ) : (
        <>
          {renderRecentSection()}
          {SECTION_ORDER.map((sec) =>
            grouped[sec] ? renderSection(sec, grouped[sec]) : null,
          )}
        </>
      )}
      {hoveredItem && tooltipAnchor && (
        <MentionPreviewTooltip
          item={hoveredItem}
          anchorEl={tooltipAnchor}
          danGraph={danGraph as { nodes: NodeInfo[] } | null}
        />
      )}
    </div>
  );
}
