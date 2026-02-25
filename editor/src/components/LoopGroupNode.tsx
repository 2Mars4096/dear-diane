import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useGraphStore } from "../store/useGraphStore";

interface LoopGroupData {
  groupId: string;
  label: string;
  collapsed: boolean;
  gateNodeId: string;
  memberCount: number;
  [key: string]: unknown;
}

function LoopGroupNodeComponent({ data }: NodeProps) {
  const { groupId, label, collapsed, memberCount } = data as unknown as LoopGroupData;
  const toggleGroup = useGraphStore((s) => s.toggleLoopGroup);

  if (collapsed) {
    return (
      <div className="border-2 border-dashed border-amber-400 rounded-lg px-3 py-2 bg-amber-50/80 shadow-sm min-w-[140px]">
        <Handle
          type="target"
          position={Position.Left}
          id="port:group-in"
          className="!w-2 !h-2 !bg-amber-400"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={(e) => { e.stopPropagation(); toggleGroup(groupId); }}
            className="text-xs hover:text-amber-600 transition-colors leading-none"
          >
            ▶
          </button>
          <span className="font-medium text-sm text-gray-800 truncate">{label}</span>
          <span className="text-xs text-gray-400 whitespace-nowrap">{memberCount} nodes</span>
        </div>
        <Handle
          type="source"
          position={Position.Right}
          id="port:group-out"
          className="!w-2 !h-2 !bg-amber-400"
        />
      </div>
    );
  }

  return (
    <div
      className="border-2 border-dashed border-amber-300 rounded-lg bg-amber-50/20 pointer-events-none"
      style={{ width: "100%", height: "100%" }}
    >
      <div className="flex items-center gap-2 px-2 py-1 pointer-events-auto">
        <button
          onClick={(e) => { e.stopPropagation(); toggleGroup(groupId); }}
          className="text-xs hover:text-amber-600 transition-colors leading-none"
        >
          ▼
        </button>
        <span className="font-medium text-xs text-amber-600 truncate">{label}</span>
      </div>
    </div>
  );
}

export default memo(LoopGroupNodeComponent);
