import { useState, useMemo } from "react";
import { useGraphStore } from "../store/useGraphStore";
import type { DanNode } from "../types/graph";

export default function PortMappingOverlay() {
  const layerStack = useGraphStore((s) => s.layerStack);
  const danGraph = useGraphStore((s) => s.danGraph);
  const [collapsed, setCollapsed] = useState(true);

  const mappings = useMemo(() => {
    if (!danGraph || layerStack.length === 0) return null;
    const parentEntry = layerStack[layerStack.length - 1];
    const parentNode = danGraph.nodes.find((n) => n.id === parentEntry.nodeId);
    if (!parentNode) return null;
    const d = parentNode as unknown as DanNode;
    if (d.node_type !== "composite") return null;

    const inputMappings = (d as unknown as Record<string, unknown>).input_mappings as Record<string, string> | undefined;
    const outputMappings = (d as unknown as Record<string, unknown>).output_mappings as Record<string, string> | undefined;

    if (!inputMappings && !outputMappings) return null;
    const inputs = Object.entries(inputMappings ?? {});
    const outputs = Object.entries(outputMappings ?? {});
    if (inputs.length === 0 && outputs.length === 0) return null;

    return { inputs, outputs };
  }, [danGraph, layerStack]);

  if (!mappings) return null;

  return (
    <div className="px-3 py-1 bg-gray-50 border-b border-gray-100 text-[10px] text-gray-500">
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="hover:text-gray-700 font-medium"
      >
        Port Mappings {collapsed ? "+" : "−"}
      </button>

      {!collapsed && (
        <div className="mt-1 flex gap-6">
          {mappings.inputs.length > 0 && (
            <div>
              <span className="font-medium text-gray-600">Inputs:</span>
              {mappings.inputs.map(([outer, inner]) => (
                <div key={outer} className="ml-2">
                  <span className="text-indigo-500">{outer}</span>
                  {" → "}
                  <span className="text-emerald-600">{inner}</span>
                </div>
              ))}
            </div>
          )}
          {mappings.outputs.length > 0 && (
            <div>
              <span className="font-medium text-gray-600">Outputs:</span>
              {mappings.outputs.map(([inner, outer]) => (
                <div key={inner} className="ml-2">
                  <span className="text-emerald-600">{inner}</span>
                  {" → "}
                  <span className="text-indigo-500">{outer}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
