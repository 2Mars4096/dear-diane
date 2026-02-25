import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { FolderOpen, Layers } from "lucide-react";
import { useGraphStore } from "../store/useGraphStore";
import { NodeIcon } from "../lib/nodeIcons";
import { NODE_TYPE_CATALOG } from "../types/graph";

interface MentionItem {
  section: "node" | "workflow" | "subgraph";
  id: string;
  name: string;
  nodeType?: string;
}

interface MentionAutocompleteProps {
  query: string;
  anchorRect: { top: number; left: number } | null;
  onSelect: (mention: {
    type: "node" | "workflow" | "subgraph";
    id: string;
    name: string;
  }) => void;
  onDismiss: () => void;
}

const SUBGRAPH_NODE_TYPES = new Set(["composite", "while_loop", "for_each"]);

const typeLabel: Record<string, string> = Object.fromEntries(
  NODE_TYPE_CATALOG.map((c) => [c.type, c.label]),
);

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

export default function MentionAutocomplete({
  query,
  anchorRect,
  onSelect,
  onDismiss,
}: MentionAutocompleteProps) {
  const danGraph = useGraphStore((s) => s.danGraph);
  const graphList = useGraphStore((s) => s.graphList);

  const items = useMemo(() => {
    const all: MentionItem[] = [];
    const q = query.toLowerCase();

    if (danGraph) {
      for (const node of danGraph.nodes) {
        if (!q || node.name.toLowerCase().includes(q)) {
          all.push({
            section: SUBGRAPH_NODE_TYPES.has(node.node_type) ? "subgraph" : "node",
            id: node.id,
            name: node.name,
            nodeType: node.node_type,
          });
        }
      }
    }

    for (const g of graphList) {
      if (!q || g.name.toLowerCase().includes(q)) {
        all.push({ section: "workflow", id: g.graph_id, name: g.name });
      }
    }

    return all;
  }, [danGraph, graphList, query]);

  const nodeItems = items.filter((i) => i.section === "node");
  const workflowItems = items.filter((i) => i.section === "workflow");
  const subgraphItems = items.filter((i) => i.section === "subgraph");
  const flatItems = [...nodeItems, ...workflowItems, ...subgraphItems];

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

  const renderSection = (
    label: string,
    sectionItems: MentionItem[],
  ) => {
    if (sectionItems.length === 0) return null;
    const startIdx = runningIdx;
    runningIdx += sectionItems.length;
    return (
      <div key={label}>
        <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider px-3 py-1">
          {label}
        </div>
        {sectionItems.map((item, i) => {
          const idx = startIdx + i;
          const selected = idx === selectedIdx;
          return (
            <button
              key={item.id}
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
              {item.section === "node" && (
                <span className="text-gray-500 flex-shrink-0">
                  <NodeIcon type={item.nodeType ?? ""} />
                </span>
              )}
              {item.section === "workflow" && (
                <FolderOpen size={14} className="text-green-500 flex-shrink-0" />
              )}
              {item.section === "subgraph" && (
                <Layers size={14} className="text-amber-500 flex-shrink-0" />
              )}

              <span className="truncate flex-1">
                {highlightMatch(item.name, query)}
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
      className="bg-white border border-gray-200 rounded-lg shadow-lg max-h-[300px] overflow-y-auto w-64"
    >
      {flatItems.length === 0 ? (
        <div className="px-3 py-3 text-sm text-gray-400 text-center">
          No matches
        </div>
      ) : (
        <>
          {renderSection("Nodes", nodeItems)}
          {renderSection("Workflows", workflowItems)}
          {renderSection("Sub-graphs", subgraphItems)}
        </>
      )}
    </div>
  );
}
