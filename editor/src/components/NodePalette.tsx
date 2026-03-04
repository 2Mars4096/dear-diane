// -- 5-4: Build palette — searchable categorized sidebar ---------------------

import { useState, useEffect } from "react";
import {
  NODE_TYPE_CATALOG,
  NODE_DESCRIPTIONS,
  type NodeTypeString,
} from "../types/graph";
import { createDefaultNode, EDGE_COLORS } from "../lib/graphAdapter";
import { useGraphStore } from "../store/useGraphStore";
import {
  PREDEFINED_AGENT_TEMPLATES,
  type PaletteTemplate,
} from "../lib/paletteTemplates";
import type { GraphListItem, Block } from "../lib/api";
import { fetchBlocks } from "../lib/api";

type NodeCatalogItem = (typeof NODE_TYPE_CATALOG)[number];

const CATEGORY_ORDER = [
  "io",
  "operator",
  "control",
  "template",
  "mcp",
  "composite",
  "workflow",
] as const;

const CATEGORY_LABELS: Record<string, string> = {
  io: "Input / Output",
  operator: "Operators",
  control: "Control Flow",
  template: "Pre-defined Agents",
  mcp: "MCP / Wrapped Agents",
  composite: "Composite",
  workflow: "Saved Workflows",
};

const MCP_PLACEHOLDERS = [
  { id: "mcp_tool", label: "MCP Tool" },
  { id: "custom_wrapper", label: "Custom Wrapper" },
  { id: "api_adapter", label: "API Adapter" },
];

const EDGE_TYPES = ["data", "control", "context"] as const;

function tooltipText(desc: { description: string; inputs: string[]; outputs: string[] }): string {
  return `${desc.description}\nIn: ${desc.inputs.join(", ")}  →  Out: ${desc.outputs.join(", ")}`;
}

export default function NodePalette() {
  const addNode = useGraphStore((s) => s.addNode);
  const addTemplateNode = useGraphStore((s) => s.addTemplateNode);
  const selectedEdgeType = useGraphStore((s) => s.selectedEdgeType);
  const setSelectedEdgeType = useGraphStore((s) => s.setSelectedEdgeType);
  const graphList = useGraphStore((s) => s.graphList);
  const currentGraphId = useGraphStore((s) => s.graphId);

  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [blocksLoading, setBlocksLoading] = useState(false);

  useEffect(() => {
    setBlocksLoading(true);
    fetchBlocks()
      .then((res) => setBlocks(Array.isArray(res) ? res : (res as { blocks?: Block[] }).blocks ?? []))
      .catch(() => {})
      .finally(() => setBlocksLoading(false));
  }, []);

  const toggleCategory = (cat: string) =>
    setCollapsed((prev) => ({ ...prev, [cat]: !prev[cat] }));

  const handleDragStart = (e: React.DragEvent, nodeType: NodeTypeString) => {
    e.dataTransfer.setData("application/dan-node-type", nodeType);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleTemplateDragStart = (e: React.DragEvent, templateId: string) => {
    e.dataTransfer.setData("application/dan-node-type", `template:${templateId}`);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleWorkflowDragStart = (e: React.DragEvent, graphId: string) => {
    e.dataTransfer.setData("application/dan-node-type", `workflow:${graphId}`);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleBlockDragStart = (e: React.DragEvent, block: Block) => {
    e.dataTransfer.setData("application/dan-block", JSON.stringify(block));
    e.dataTransfer.effectAllowed = "move";
  };

  const categorized = NODE_TYPE_CATALOG.reduce<Record<string, NodeCatalogItem[]>>(
    (acc, item) => {
      if (!acc[item.category]) acc[item.category] = [];
      acc[item.category].push(item);
      return acc;
    },
    {},
  );

  const q = search.toLowerCase().trim();

  type CategoryEntry =
    | { key: string; type: "catalog"; items: NodeCatalogItem[] }
    | { key: string; type: "template"; items: PaletteTemplate[] }
    | { key: string; type: "mcp"; items: typeof MCP_PLACEHOLDERS }
    | { key: string; type: "workflow"; items: GraphListItem[] };

  const filteredCategories: CategoryEntry[] = CATEGORY_ORDER.map(
    (cat): CategoryEntry | null => {
      if (cat === "template") {
        const items = PREDEFINED_AGENT_TEMPLATES.filter(
          (t) =>
            !q ||
            t.label.toLowerCase().includes(q) ||
            t.description.toLowerCase().includes(q),
        );
        return items.length ? { key: cat, type: "template", items } : null;
      }
      if (cat === "mcp") {
        const items = MCP_PLACEHOLDERS.filter(
          (m) => !q || m.label.toLowerCase().includes(q),
        );
        return items.length ? { key: cat, type: "mcp", items } : null;
      }
      if (cat === "workflow") {
        const items = graphList
          .filter((g) => g.graph_id !== currentGraphId)
          .filter(
            (g) =>
              !q ||
              g.name.toLowerCase().includes(q) ||
              g.graph_id.toLowerCase().includes(q),
          );
        return items.length ? { key: cat, type: "workflow", items } : null;
      }
      const items = (categorized[cat] ?? []).filter(
        (item) =>
          !q ||
          item.label.toLowerCase().includes(q) ||
          item.type.toLowerCase().includes(q),
      );
      return items.length ? { key: cat, type: "catalog", items } : null;
    },
  ).filter((c): c is CategoryEntry => c !== null);

  return (
    <div className="w-52 bg-gray-50 border-r border-gray-200 flex flex-col min-h-0">
      {/* Header: title + edge selector + search */}
      <div className="p-3 space-y-2 flex-shrink-0">
        <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider">
          Node Palette
        </h2>

        {/* Edge type selector */}
        <div className="flex gap-1">
          {EDGE_TYPES.map((et) => (
            <button
              key={et}
              onClick={() => setSelectedEdgeType(et)}
              className={`flex-1 px-1.5 py-1 text-[10px] font-medium rounded border transition-all capitalize ${
                selectedEdgeType === et
                  ? "text-white border-transparent"
                  : "bg-white text-gray-500 border-gray-200 hover:border-gray-300"
              }`}
              style={
                selectedEdgeType === et
                  ? { backgroundColor: EDGE_COLORS[et] }
                  : undefined
              }
            >
              {et}
            </button>
          ))}
        </div>

        {/* Search input */}
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search nodes…"
          className="w-full px-2.5 py-1.5 text-xs bg-white border border-gray-200 rounded focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-200 transition-all"
        />
      </div>

      {/* Scrollable categories */}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {filteredCategories.map(({ key, type, items }) => {
          const isCollapsed = !q && (collapsed[key] ?? false);
          return (
            <div key={key} className="mb-3">
              <button
                onClick={() => toggleCategory(key)}
                className="flex items-center gap-1 w-full text-[11px] font-semibold text-gray-400 uppercase mb-1.5 hover:text-gray-600 transition-colors"
              >
                <span className="text-[9px]">{isCollapsed ? "▸" : "▾"}</span>
                {CATEGORY_LABELS[key] ?? key}
                {type === "mcp" && (
                  <span className="ml-auto text-[9px] font-normal normal-case text-amber-500 bg-amber-50 px-1 rounded">
                    soon
                  </span>
                )}
              </button>

              {!isCollapsed && (
                <div className="flex flex-col gap-1">
                  {/* Catalog node types */}
                  {type === "catalog" &&
                    (items as NodeCatalogItem[]).map((item) => {
                      const desc = NODE_DESCRIPTIONS[item.type];
                      return (
                        <div
                          key={item.type}
                          draggable
                          onDragStart={(e) => handleDragStart(e, item.type)}
                          onClick={() =>
                            addNode(
                              createDefaultNode(item.type, { x: 200, y: 200 }),
                            )
                          }
                          title={desc ? tooltipText(desc) : item.label}
                          className="px-2.5 py-1.5 bg-white rounded border border-gray-200 text-xs cursor-grab hover:border-indigo-400 hover:shadow-sm transition-all select-none"
                        >
                          {item.label}
                        </div>
                      );
                    })}

                  {/* Pre-defined agent templates */}
                  {type === "template" &&
                    (items as PaletteTemplate[]).map((tpl) => (
                      <div
                        key={tpl.id}
                        draggable
                        onDragStart={(e) =>
                          handleTemplateDragStart(e, tpl.id)
                        }
                        onClick={() =>
                          addTemplateNode(tpl.id, { x: 200, y: 200 })
                        }
                        title={`${tpl.description}\nPre-built sub-graph template`}
                        className="px-2.5 py-1.5 bg-indigo-50 rounded border border-indigo-200 text-xs cursor-grab hover:border-indigo-400 hover:shadow-sm transition-all select-none text-indigo-700"
                      >
                        {tpl.label}
                      </div>
                    ))}

                  {/* MCP / Wrapped Agents placeholders */}
                  {type === "mcp" &&
                    (items as typeof MCP_PLACEHOLDERS).map((m) => (
                      <div
                        key={m.id}
                        className="px-2.5 py-1.5 bg-gray-100 rounded border border-gray-200 text-xs text-gray-400 cursor-not-allowed opacity-60 select-none"
                      >
                        {m.label}
                      </div>
                    ))}

                  {/* Saved Workflows */}
                  {type === "workflow" &&
                    (items as GraphListItem[]).map((g) => (
                      <div
                        key={g.graph_id}
                        draggable
                        onDragStart={(e) =>
                          handleWorkflowDragStart(e, g.graph_id)
                        }
                        onClick={() => {
                          const store = useGraphStore.getState();
                          store.addGraphAsNode(g.graph_id, { x: 200, y: 200 });
                        }}
                        title={`Import "${g.name}" as a reusable node`}
                        className="px-2.5 py-1.5 bg-emerald-50 rounded border border-emerald-200 text-xs cursor-grab hover:border-emerald-400 hover:shadow-sm transition-all select-none text-emerald-700"
                      >
                        {g.name || g.graph_id}
                      </div>
                    ))}
                </div>
              )}
            </div>
          );
        })}

        {/* Installed Blocks section */}
        <div className="mb-3 mt-1 pt-2 border-t border-gray-200">
          <button
            onClick={() => toggleCategory("blocks")}
            className="flex items-center gap-1 w-full text-[11px] font-semibold text-gray-400 uppercase mb-1.5 hover:text-gray-600 transition-colors"
          >
            <span className="text-[9px]">{!q && collapsed["blocks"] ? "▸" : "▾"}</span>
            Installed Blocks
            {blocks.length > 0 && (
              <span className="ml-auto text-[9px] font-normal normal-case text-purple-500 bg-purple-50 px-1 rounded">
                {blocks.length}
              </span>
            )}
          </button>

          {!((!q && collapsed["blocks"]) ?? false) && (
            <div className="flex flex-col gap-1">
              {blocksLoading && (
                <div className="text-[10px] text-gray-400 italic px-1">Loading…</div>
              )}
              {!blocksLoading && blocks.length === 0 && !q && (
                <div className="text-[10px] text-gray-400 px-1 leading-tight">
                  No blocks installed — use <code className="text-[9px] bg-gray-100 px-0.5 rounded">dan-blocks install</code> to add
                </div>
              )}
              {!blocksLoading &&
                blocks
                  .filter(
                    (b) =>
                      !q ||
                      b.name.toLowerCase().includes(q) ||
                      b.description.toLowerCase().includes(q) ||
                      b.block_type.toLowerCase().includes(q),
                  )
                  .map((block) => {
                    const BLOCK_TYPE_ICONS: Record<string, string> = {
                      composite: "📦",
                      single_node: "🔧",
                      template: "📋",
                    };
                    return (
                      <div
                        key={`${block.name}@${block.version}`}
                        draggable
                        onDragStart={(e) => handleBlockDragStart(e, block)}
                        title={`${block.description || block.name}\n${block.block_type} • v${block.version}${block.author ? ` • by ${block.author}` : ""}`}
                        className="px-2.5 py-1.5 bg-purple-50 rounded border border-purple-200 text-xs cursor-grab hover:border-purple-400 hover:shadow-sm transition-all select-none text-purple-700"
                      >
                        <div className="flex items-center gap-1">
                          <span className="text-[10px] shrink-0">{BLOCK_TYPE_ICONS[block.block_type] ?? "📦"}</span>
                          <span className="truncate flex-1">{block.name}</span>
                          <span className="text-[9px] font-mono bg-purple-100 text-purple-600 px-1 rounded shrink-0">
                            {block.version}
                          </span>
                        </div>
                        {block.description && (
                          <div className="text-[10px] text-purple-500 truncate mt-0.5">
                            {block.description}
                          </div>
                        )}
                      </div>
                    );
                  })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
