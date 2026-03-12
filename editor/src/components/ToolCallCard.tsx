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

const STATUS_ICON = {
  running: <Loader2 size={11} className="animate-spin text-blue-500" />,
  success: <Check size={11} className="text-emerald-500" />,
  error: <X size={11} className="text-red-500" />,
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
  const [expanded, setExpanded] = useState(false);

  const icon = STATUS_ICON[toolCall.status];
  const duration =
    toolCall.durationMs != null && toolCall.status !== "running"
      ? toolCall.durationMs < 1000
        ? `${toolCall.durationMs}ms`
        : `${(toolCall.durationMs / 1000).toFixed(1)}s`
      : null;

  const appliedLabel =
    mutationStatus === "applied" ? "Applied"
    : mutationStatus === "rejected" ? "Rejected"
    : null;

  const hasDetails = !!(toolCall.argsPreview || toolCall.outputPreview || (operations && operations.length > 0));

  return (
    <div className="my-1">
      <button
        onClick={() => hasDetails && setExpanded(!expanded)}
        className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] transition-colors ${
          hasDetails ? "cursor-pointer hover:bg-gray-100" : "cursor-default"
        } ${toolCall.status === "error" ? "bg-red-50/50" : "bg-gray-50/70"}`}
      >
        {hasDetails && (expanded ? <ChevronDown size={10} className="text-gray-400" /> : <ChevronRight size={10} className="text-gray-400" />)}
        <Wrench size={10} className="text-gray-400" />
        <span className="font-medium text-gray-600">{toolCall.toolName}</span>
        {icon}
        {duration && (
          <span className="flex items-center gap-0.5 text-[10px] text-gray-400 tabular-nums">
            <Clock size={8} />
            {duration}
          </span>
        )}
        {appliedLabel && (
          <span className={`text-[9px] font-medium px-1 py-0.5 rounded ${
            mutationStatus === "applied" ? "text-green-600 bg-green-50" : "text-gray-500 bg-gray-100"
          }`}>
            {appliedLabel}
          </span>
        )}
        {onPreviewChanges && mutationStatus === "proposed" && (
          <span
            onClick={(e) => { e.stopPropagation(); onPreviewChanges(); }}
            className="text-indigo-600 hover:text-indigo-800 font-medium underline"
          >
            Preview
          </span>
        )}
      </button>

      {expanded && (
        <div className="ml-4 mt-1 mb-1 pl-3 border-l-2 border-gray-100 space-y-1">
          {toolCall.argsPreview && (
            <div>
              <span className="text-[10px] text-gray-400">Args</span>
              <pre className="text-[10px] text-gray-600 bg-gray-50 rounded p-1.5 overflow-x-auto whitespace-pre-wrap font-mono leading-relaxed mt-0.5">
                {toolCall.argsPreview}
              </pre>
            </div>
          )}
          {operations && operations.length > 0 && (
            <div className="space-y-0.5">
              {operations.slice(0, 8).map((op, i) => (
                <div key={i} className="flex items-center gap-1.5 text-[10px] text-gray-500">
                  <span className="w-1 h-1 rounded-full bg-gray-300 flex-shrink-0" />
                  <span className="font-mono">{op.op}</span>
                  {(op.name || op.node_id) && <span className="text-gray-400 truncate">{op.name || op.node_id}</span>}
                </div>
              ))}
              {operations.length > 8 && (
                <div className="text-[10px] text-gray-400 pl-2.5">+{operations.length - 8} more</div>
              )}
            </div>
          )}
          {toolCall.outputPreview && toolCall.status !== "running" && (
            <div>
              <span className="text-[10px] text-gray-400">Output</span>
              <pre className={`text-[10px] rounded p-1.5 overflow-x-auto whitespace-pre-wrap font-mono leading-relaxed mt-0.5 ${
                toolCall.status === "error" ? "text-red-600 bg-red-50" : "text-gray-600 bg-gray-50"
              }`}>
                {toolCall.outputPreview}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
