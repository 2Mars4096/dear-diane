import { useState } from "react";
import {
  Loader2,
  Check,
  X,
  ChevronDown,
  ChevronRight,
  Wrench,
  Clock,
} from "lucide-react";
import type { ToolCallInfo } from "../types/chat";

const STATUS_CONFIG = {
  running: {
    icon: <Loader2 size={12} className="animate-spin text-blue-500" />,
    border: "border-blue-100",
    bg: "bg-blue-50/50",
  },
  success: {
    icon: <Check size={12} className="text-green-500" />,
    border: "border-green-100",
    bg: "bg-green-50/30",
  },
  error: {
    icon: <X size={12} className="text-red-500" />,
    border: "border-red-200",
    bg: "bg-red-50/30",
  },
} as const;

interface ToolCallCardProps {
  toolCall: ToolCallInfo;
  mutationStatus?: "proposed" | "applied" | "partial" | "rejected" | "reverted" | null;
  onPreviewChanges?: () => void;
  operations?: Array<{ op: string; name?: string; node_id?: string; node_type?: string }>;
}

export default function ToolCallCard({
  toolCall,
  mutationStatus,
  onPreviewChanges,
  operations,
}: ToolCallCardProps) {
  const [argsExpanded, setArgsExpanded] = useState(false);
  const [outputExpanded, setOutputExpanded] = useState(false);

  const cfg = STATUS_CONFIG[toolCall.status];

  const appliedLabel =
    mutationStatus === "applied"
      ? "Applied"
      : mutationStatus === "rejected"
        ? "Rejected"
        : null;

  return (
    <div
      className={`my-2 rounded-lg border ${cfg.border} ${cfg.bg} overflow-hidden`}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2">
        <Wrench size={12} className="text-gray-400 flex-shrink-0" />
        <span className="text-xs font-medium text-gray-700 flex-1 min-w-0 truncate">
          {toolCall.toolName}
        </span>
        {cfg.icon}
        {toolCall.durationMs != null && toolCall.status !== "running" && (
          <span className="flex items-center gap-0.5 text-[10px] text-gray-400 tabular-nums flex-shrink-0">
            <Clock size={9} />
            {toolCall.durationMs < 1000
              ? `${toolCall.durationMs}ms`
              : `${(toolCall.durationMs / 1000).toFixed(1)}s`}
          </span>
        )}
        {appliedLabel && (
          <span
            className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
              mutationStatus === "applied"
                ? "text-green-600 bg-green-50"
                : "text-gray-500 bg-gray-100"
            }`}
          >
            {appliedLabel}
          </span>
        )}
      </div>

      {/* Args preview */}
      {toolCall.argsPreview && (
        <div className="border-t border-gray-100">
          <button
            onClick={() => setArgsExpanded(!argsExpanded)}
            className="flex items-center gap-1 w-full px-3 py-1.5 text-[10px] text-gray-500 hover:bg-gray-50/50 transition-colors"
          >
            {argsExpanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
            <span>Arguments</span>
          </button>
          {argsExpanded && (
            <div className="px-3 pb-2">
              <pre className="text-[10px] text-gray-600 bg-gray-50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono leading-relaxed">
                {toolCall.argsPreview}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Operations sub-items (for plan_graph_mutations) */}
      {operations && operations.length > 0 && (
        <div className="border-t border-gray-100 px-3 py-1.5">
          <div className="space-y-0.5">
            {operations.slice(0, 8).map((op, i) => (
              <div key={i} className="flex items-center gap-1.5 text-[10px] text-gray-500">
                <span className="w-1 h-1 rounded-full bg-gray-300 flex-shrink-0" />
                <span className="font-mono">{op.op}</span>
                {op.name && <span className="text-gray-400 truncate">{op.name}</span>}
                {!op.name && op.node_id && (
                  <span className="text-gray-400 truncate">{op.node_id}</span>
                )}
                {op.node_type && (
                  <span className="text-gray-300">[{op.node_type}]</span>
                )}
              </div>
            ))}
            {operations.length > 8 && (
              <div className="text-[10px] text-gray-400 pl-2.5">
                +{operations.length - 8} more
              </div>
            )}
          </div>
        </div>
      )}

      {/* Output preview */}
      {toolCall.outputPreview && toolCall.status !== "running" && (
        <div className="border-t border-gray-100">
          <button
            onClick={() => setOutputExpanded(!outputExpanded)}
            className="flex items-center gap-1 w-full px-3 py-1.5 text-[10px] text-gray-500 hover:bg-gray-50/50 transition-colors"
          >
            {outputExpanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
            <span>Output</span>
          </button>
          {outputExpanded && (
            <div className="px-3 pb-2">
              <pre
                className={`text-[10px] rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono leading-relaxed ${
                  toolCall.status === "error"
                    ? "text-red-600 bg-red-50"
                    : "text-gray-600 bg-gray-50"
                }`}
              >
                {toolCall.outputPreview}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Preview Changes button (for mutation tool calls) */}
      {onPreviewChanges && mutationStatus === "proposed" && (
        <div className="border-t border-gray-100 px-3 py-2">
          <button
            onClick={onPreviewChanges}
            className="text-[11px] font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
          >
            Preview Changes &rarr;
          </button>
        </div>
      )}
    </div>
  );
}
