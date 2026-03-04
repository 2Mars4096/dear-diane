import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  FolderOpen,
  Layers,
  FileText,
  Code,
  BookOpen,
  MessageSquare,
} from "lucide-react";
import { useGraphStore } from "../store/useGraphStore";
import { NodeIcon } from "../lib/nodeIcons";
import { NODE_TYPE_CATALOG } from "../types/graph";
import type { MentionType } from "../lib/mentionParser";
import * as api from "../lib/api";

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
  const parts: JSX.Element[] = [];
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

const SECTION_LABELS: Record<MentionSection, string> = {
  node: "Nodes",
  workflow: "Workflows",
  subgraph: "Sub-graphs",
  file: "Files",
  code: "Code",
  docs: "Docs",
  chat: "Past Chats",
};

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

  const sectionOrder: MentionSection[] = [
    "node",
    "workflow",
    "subgraph",
    "file",
    "code",
    "docs",
    "chat",
  ];
  const grouped = useMemo(() => {
    const map: Partial<Record<MentionSection, MentionItem[]>> = {};
    for (const sec of sectionOrder) {
      const secItems = items.filter((i) => i.section === sec);
      if (secItems.length > 0) map[sec] = secItems;
    }
    return map;
  }, [items]);

  const flatItems = useMemo(
    () => sectionOrder.flatMap((sec) => grouped[sec] ?? []),
    [grouped],
  );

  const [selectedIdx, setSelectedIdx] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => setSelectedIdx(0), [query]);

  const selectItem = useCallback(
    (item: MentionItem) => {
      onSelect({ type: item.section, id: item.id, name: item.name });
    },
    [onSelect],
  );

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

  const renderSection = (sec: MentionSection, sectionItems: MentionItem[]) => {
    if (sectionItems.length === 0) return null;
    const startIdx = runningIdx;
    runningIdx += sectionItems.length;
    return (
      <div key={sec}>
        <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider px-3 py-1">
          {SECTION_LABELS[sec]}
        </div>
        {sectionItems.map((item, i) => {
          const idx = startIdx + i;
          const selected = idx === selectedIdx;
          return (
            <button
              key={`${item.section}-${item.id}`}
              data-selected={selected ? "" : undefined}
              onMouseDown={(e) => {
                e.preventDefault();
                selectItem(item);
              }}
              onMouseEnter={() => setSelectedIdx(idx)}
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
        })}
      </div>
    );
  };

  return (
    <div
      ref={listRef}
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
          {sectionOrder.map((sec) =>
            grouped[sec] ? renderSection(sec, grouped[sec]) : null,
          )}
        </>
      )}
    </div>
  );
}
