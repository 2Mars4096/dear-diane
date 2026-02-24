import { useEffect, useRef } from "react";
import { useGraphStore, type LogEntry } from "../store/useGraphStore";

const EVENT_COLORS: Record<string, string> = {
  run_started: "text-blue-500",
  run_completed: "text-green-600",
  run_failed: "text-red-600",
  node_started: "text-yellow-600",
  node_completed: "text-green-500",
  node_failed: "text-red-500",
  node_skipped: "text-gray-400",
  node_output: "text-indigo-500",
};

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour12: false, fractionalSecondDigits: 1 });
}

function LogRow({ entry }: { entry: LogEntry }) {
  const color = EVENT_COLORS[entry.event_type] ?? "text-gray-500";
  return (
    <div className="flex gap-2 py-0.5 text-[11px] font-mono leading-tight">
      <span className="text-gray-400 shrink-0">{formatTime(entry.timestamp)}</span>
      <span className={`shrink-0 w-28 ${color}`}>{entry.event_type}</span>
      <span className="text-gray-600 truncate">{entry.node_id ?? ""}</span>
    </div>
  );
}

export default function LogPanel() {
  const logs = useGraphStore((s) => s.logs);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  if (logs.length === 0) {
    return (
      <div className="p-3 text-xs text-gray-400 text-center">
        No run events yet. Click Run to start.
      </div>
    );
  }

  return (
    <div className="overflow-y-auto max-h-full p-2">
      {logs.map((entry, i) => (
        <LogRow key={i} entry={entry} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
