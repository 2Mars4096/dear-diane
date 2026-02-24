import { useState, useEffect, useRef, useMemo } from "react";
import { useReactFlow } from "@xyflow/react";
import { useGraphStore } from "../store/useGraphStore";
import { NodeIcon } from "../lib/nodeIcons";
import type { DanNode } from "../types/graph";

export default function CommandPalette() {
  const open = useGraphStore((s) => s.commandPaletteOpen);
  const setOpen = useGraphStore((s) => s.setCommandPaletteOpen);
  const nodes = useGraphStore((s) => s.nodes);
  const setSelectedNode = useGraphStore((s) => s.setSelectedNode);
  const reactFlow = useReactFlow();

  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIdx(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const results = useMemo(() => {
    const q = query.toLowerCase().trim();
    return nodes.filter((n) => {
      const d = n.data as unknown as DanNode;
      const name = (d.name || "").toLowerCase();
      const type = (d.node_type || "").toLowerCase();
      return !q || name.includes(q) || type.includes(q);
    });
  }, [nodes, query]);

  useEffect(() => {
    setActiveIdx(0);
  }, [query]);

  const selectNode = (nodeId: string) => {
    setOpen(false);
    setSelectedNode(nodeId);
    reactFlow.fitView({ nodes: [{ id: nodeId }], padding: 0.5, duration: 300 });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && results.length > 0) {
      e.preventDefault();
      selectNode(results[activeIdx].id);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/20 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && setOpen(false)}
    >
      <div
        className="bg-white rounded-lg shadow-2xl border border-gray-200 w-[420px] max-h-[400px] flex flex-col overflow-hidden"
        onKeyDown={handleKeyDown}
      >
        <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" className="text-gray-400 shrink-0">
            <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" />
            <path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search nodes by name or type…"
            className="flex-1 text-sm outline-none bg-transparent"
          />
          <kbd className="text-[10px] text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded border border-gray-200">
            esc
          </kbd>
        </div>

        <div className="overflow-y-auto flex-1">
          {results.length === 0 && (
            <div className="px-4 py-6 text-xs text-gray-400 text-center">No matching nodes</div>
          )}
          {results.map((n, i) => {
            const d = n.data as unknown as DanNode;
            return (
              <button
                key={n.id}
                onClick={() => selectNode(n.id)}
                onMouseEnter={() => setActiveIdx(i)}
                className={`w-full px-4 py-2 flex items-center gap-2 text-left text-xs transition-colors ${
                  i === activeIdx ? "bg-indigo-50 text-indigo-700" : "text-gray-700 hover:bg-gray-50"
                }`}
              >
                <NodeIcon type={d.node_type} className="shrink-0 text-gray-500" />
                <span className="font-medium truncate">{d.name || d.node_type}</span>
                <span className="ml-auto text-[10px] text-gray-400">{d.node_type}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
