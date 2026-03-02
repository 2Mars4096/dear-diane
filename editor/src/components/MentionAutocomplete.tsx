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

function highlightMatch(name: string, query: string) {
  if (!query) return <>{name}</>;
  const lower = name.toLowerCase();
  const idx = lower.indexOf(query.toLowerCase());
  if (idx === -1) return <>{name}</>;
  return (
    <>
      {name.slice(0, idx)}
      <span className="font-bold">{name.slice(idx, idx + query.length)}</span>
      {name.slice(idx + query.length)}
    </>
  );
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
    const all: MentionItem[] = [];
    const q = subquery.toLowerCase();

    const shouldInclude = (sec: MentionSection) =>
      category === null || category === sec;

    if (shouldInclude("node") || shouldInclude("subgraph")) {
      if (danGraph) {
        for (const node of danGraph.nodes) {
          if (!q || node.name.toLowerCase().includes(q)) {
            const sec = SUBGRAPH_NODE_TYPES.has(node.node_type)
              ? "subgraph"
              : "node";
            if (shouldInclude(sec)) {
              all.push({
                section: sec,
                id: node.id,
                name: node.name,
                nodeType: node.node_type,
              });
            }
          }
        }
      }
    }

    if (shouldInclude("workflow")) {
      for (const g of graphList) {
        if (!q || g.name.toLowerCase().includes(q)) {
          all.push({ section: "workflow", id: g.graph_id, name: g.name });
        }
      }
    }

    if (shouldInclude("file")) {
      for (const f of fileList) {
        if (!q || f.toLowerCase().includes(q)) {
          all.push({ section: "file", id: f, name: f });
        }
      }
    }

    if (shouldInclude("code")) {
      for (const ref of codeRefs) {
        if (!q || ref.toLowerCase().includes(q)) {
          all.push({ section: "code", id: ref, name: ref });
        }
      }
    }

    if (shouldInclude("docs")) {
      for (const d of docsList) {
        if (!q || d.toLowerCase().includes(q)) {
          all.push({ section: "docs", id: d, name: d });
        }
      }
    }

    if (shouldInclude("chat")) {
      for (const t of chatThreads) {
        if (!q || t.title.toLowerCase().includes(q)) {
          all.push({ section: "chat", id: t.id, name: t.title });
        }
      }
    }

    return all;
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
