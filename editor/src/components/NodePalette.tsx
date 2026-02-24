import { NODE_TYPE_CATALOG, type NodeTypeString } from "../types/graph";
import { createDefaultNode } from "../lib/graphAdapter";
import { useGraphStore } from "../store/useGraphStore";

const CATEGORY_LABELS: Record<string, string> = {
  operator: "Operators",
  control: "Control Flow",
  composite: "Composite",
};

export default function NodePalette() {
  const addNode = useGraphStore((s) => s.addNode);

  const handleDragStart = (e: React.DragEvent, nodeType: NodeTypeString) => {
    e.dataTransfer.setData("application/dan-node-type", nodeType);
    e.dataTransfer.effectAllowed = "move";
  };

  const categories = Object.groupBy(NODE_TYPE_CATALOG, (n) => n.category);

  return (
    <div className="w-52 bg-gray-50 border-r border-gray-200 p-3 overflow-y-auto">
      <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">
        Node Palette
      </h2>
      {Object.entries(categories).map(([cat, items]) => (
        <div key={cat} className="mb-4">
          <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1.5">
            {CATEGORY_LABELS[cat] ?? cat}
          </h3>
          <div className="flex flex-col gap-1">
            {(items ?? []).map((item) => (
              <div
                key={item.type}
                draggable
                onDragStart={(e) => handleDragStart(e, item.type)}
                onClick={() => addNode(createDefaultNode(item.type, { x: 200, y: 200 }))}
                className="px-2.5 py-1.5 bg-white rounded border border-gray-200 text-xs cursor-grab hover:border-indigo-400 hover:shadow-sm transition-all select-none"
              >
                {item.label}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
