import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useGraphStore, EVENT_CATEGORY, type LogEntry } from "../store/useGraphStore";

// ---------------------------------------------------------------------------
// Inline SVG icons (no external library dependency)
// ---------------------------------------------------------------------------

function BrainIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a5 5 0 0 1 5 5c0 1.5-.7 2.9-1.8 3.8" />
      <path d="M12 2a5 5 0 0 0-5 5c0 1.5.7 2.9 1.8 3.8" />
      <path d="M12 22v-8" />
      <path d="M7 10.8A7 7 0 0 0 5 17c0 2.2 1.8 4 4 4h6c2.2 0 4-1.8 4-4a7 7 0 0 0-2-6.2" />
    </svg>
  );
}

function WrenchIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  );
}

function TerminalIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="4 17 10 11 4 5" />
      <line x1="12" y1="19" x2="20" y2="19" />
    </svg>
  );
}

function XCircleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="15" y1="9" x2="9" y2="15" />
      <line x1="9" y1="9" x2="15" y2="15" />
    </svg>
  );
}

function CircleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="4" />
    </svg>
  );
}

function ChevronIcon({ open, className }: { open: boolean; className?: string }) {
  return (
    <svg className={`${className ?? ""} transition-transform ${open ? "rotate-90" : ""}`} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EVENT_ICON: Record<string, (cls: string) => JSX.Element> = {
  llm_thinking: (cls) => <BrainIcon className={cls} />,
  tool_call_started: (cls) => <WrenchIcon className={cls} />,
  tool_call_result: (cls) => <WrenchIcon className={cls} />,
  code_output: (cls) => <TerminalIcon className={cls} />,
  intermediate_text: (cls) => <TerminalIcon className={cls} />,
  node_failed: (cls) => <XCircleIcon className={cls} />,
  run_failed: (cls) => <XCircleIcon className={cls} />,
};

const EVENT_COLORS: Record<string, string> = {
  llm_thinking: "text-purple-500",
  tool_call_started: "text-blue-500",
  tool_call_result: "text-blue-400",
  code_output: "text-cyan-500",
  intermediate_text: "text-cyan-400",
  node_failed: "text-red-500",
  run_failed: "text-red-600",
  run_started: "text-blue-500",
  run_completed: "text-green-600",
  node_started: "text-yellow-600",
  node_completed: "text-green-500",
  node_skipped: "text-gray-400",
  node_output: "text-indigo-500",
  iteration_started: "text-orange-500",
  iteration_completed: "text-orange-400",
  human_input_needed: "text-cyan-600",
};

const CATEGORY_LABEL: Record<string, string> = {
  thinking: "Thinking",
  tool: "Tool Calls",
  output: "Output",
  error: "Errors",
  lifecycle: "Lifecycle",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour12: false, fractionalSecondDigits: 1 });
}

function getEventIcon(eventType: string): JSX.Element {
  const factory = EVENT_ICON[eventType];
  const color = EVENT_COLORS[eventType] ?? "text-gray-400";
  return factory ? factory(color) : <CircleIcon className={color} />;
}

function dataPreview(entry: LogEntry): string | null {
  const d = entry.data;
  if (!d) return null;
  switch (entry.event_type) {
    case "llm_thinking": {
      const model = d.model as string | undefined;
      const preview = d.prompt_preview as string | undefined;
      return [model && `model=${model}`, preview && `"${preview}"`].filter(Boolean).join("  ");
    }
    case "tool_call_started": {
      const toolId = d.tool_id as string | undefined;
      const args = d.args as Record<string, unknown> | undefined;
      const argStr = args ? Object.entries(args).map(([k, v]) => `${k}=${v}`).join(", ") : "";
      return [toolId, argStr].filter(Boolean).join("  ");
    }
    case "tool_call_result": {
      const result = d.result;
      return typeof result === "string" ? result.slice(0, 200) : result ? JSON.stringify(result).slice(0, 200) : null;
    }
    case "code_output": {
      const stdout = d.stdout as string | undefined;
      const stderr = d.stderr as string | undefined;
      const parts: string[] = [];
      if (stdout) parts.push(stdout.slice(0, 200));
      if (stderr) parts.push(`stderr: ${stderr.slice(0, 200)}`);
      return parts.join("\n") || null;
    }
    case "intermediate_text": {
      const text = d.text as string | undefined;
      return text ? text.slice(-200) : null;
    }
    case "node_failed":
    case "run_failed":
      return (d.error as string) ?? null;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function LogEntryRow({ entry, onClick }: { entry: LogEntry; onClick?: () => void }) {
  const color = EVENT_COLORS[entry.event_type] ?? "text-gray-500";
  const preview = dataPreview(entry);

  return (
    <div
      className={`flex items-start gap-1.5 py-0.5 text-[11px] font-mono leading-tight group ${onClick ? "cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50" : ""}`}
      onClick={onClick}
    >
      <span className="shrink-0 mt-px">{getEventIcon(entry.event_type)}</span>
      <span className="text-gray-400 shrink-0">{formatTime(entry.timestamp)}</span>
      <span className={`shrink-0 ${color}`}>{entry.event_type}</span>
      {preview && (
        <span className="text-gray-500 truncate ml-1" title={preview}>
          {preview}
        </span>
      )}
    </div>
  );
}

function NodeGroup({
  nodeId,
  entries,
  nodeName,
  defaultOpen,
  onSelectNode,
}: {
  nodeId: string;
  entries: LogEntry[];
  nodeName: string;
  defaultOpen: boolean;
  onSelectNode: (nodeId: string) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);

  const categorized = useMemo(() => {
    const groups: Record<string, LogEntry[]> = {};
    for (const e of entries) {
      const cat = EVENT_CATEGORY[e.event_type] ?? "lifecycle";
      (groups[cat] ??= []).push(e);
    }
    return groups;
  }, [entries]);

  const hasRichEvents = Boolean(categorized.thinking || categorized.tool || categorized.output || categorized.error);

  return (
    <div className="border-b border-gray-100 dark:border-gray-800 last:border-b-0">
      <button
        className="flex items-center gap-1.5 w-full px-2 py-1 text-[11px] font-semibold text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800/50 text-left"
        onClick={() => setOpen(!open)}
      >
        <ChevronIcon open={open} className="text-gray-400 shrink-0" />
        <span className="truncate">{nodeName}</span>
        <span className="text-gray-400 font-normal ml-auto shrink-0">{entries.length}</span>
      </button>

      {open && (
        <div className="pl-4 pr-2 pb-1">
          {hasRichEvents ? (
            Object.entries(categorized).map(([cat, items]) => (
              <CategorySection
                key={cat}
                category={cat}
                entries={items}
                onSelectNode={onSelectNode}
              />
            ))
          ) : (
            entries.map((e, i) => (
              <LogEntryRow
                key={i}
                entry={e}
                onClick={e.node_id ? () => onSelectNode(e.node_id!) : undefined}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function CategorySection({
  category,
  entries,
  onSelectNode,
}: {
  category: string;
  entries: LogEntry[];
  onSelectNode: (nodeId: string) => void;
}) {
  const [open, setOpen] = useState(category !== "lifecycle");

  return (
    <div className="mb-0.5">
      <button
        className="flex items-center gap-1 text-[10px] font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide py-0.5 hover:text-gray-700 dark:hover:text-gray-300"
        onClick={() => setOpen(!open)}
      >
        <ChevronIcon open={open} className="text-gray-300" />
        {CATEGORY_LABEL[category] ?? category}
        <span className="font-normal text-gray-400 ml-1">{entries.length}</span>
      </button>
      {open &&
        entries.map((e, i) => (
          <LogEntryRow
            key={i}
            entry={e}
            onClick={e.node_id ? () => onSelectNode(e.node_id!) : undefined}
          />
        ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function LogPanel() {
  const logs = useGraphStore((s) => s.logs);
  const nodes = useGraphStore((s) => s.nodes);
  const selectNodeFromLog = useGraphStore((s) => s.selectNodeFromLog);

  const [search, setSearch] = useState("");
  const [nodeFilter, setNodeFilter] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string>("");

  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
    userScrolledUp.current = !atBottom;
  }, []);

  useEffect(() => {
    if (!userScrolledUp.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs.length]);

  const nodeNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const n of nodes) {
      const d = n.data as Record<string, unknown>;
      const name = d?.name ?? d?.label;
      map[n.id] = typeof name === "string" ? name : n.id;
    }
    return map;
  }, [nodes]);

  const filteredLogs = useMemo(() => {
    let result = logs;
    if (nodeFilter) {
      result = result.filter((e) => e.node_id === nodeFilter);
    }
    if (typeFilter) {
      result = result.filter((e) => e.event_type === typeFilter);
    }
    if (search) {
      const lower = search.toLowerCase();
      result = result.filter(
        (e) =>
          e.message.toLowerCase().includes(lower) ||
          (e.data && JSON.stringify(e.data).toLowerCase().includes(lower)),
      );
    }
    return result;
  }, [logs, nodeFilter, typeFilter, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, LogEntry[]>();
    for (const entry of filteredLogs) {
      const key = entry.node_id ?? "__run__";
      const arr = map.get(key);
      if (arr) arr.push(entry);
      else map.set(key, [entry]);
    }
    return map;
  }, [filteredLogs]);

  const distinctNodeIds = useMemo(() => {
    const s = new Set<string>();
    for (const e of logs) if (e.node_id) s.add(e.node_id);
    return [...s].sort();
  }, [logs]);

  const distinctTypes = useMemo(() => {
    const s = new Set<string>();
    for (const e of logs) s.add(e.event_type);
    return [...s].sort();
  }, [logs]);

  if (logs.length === 0) {
    return (
      <div className="p-3 text-xs text-gray-400 text-center">
        No run events yet. Click <strong>Run</strong> to start.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* -- Header / Filters ------------------------------------------------ */}
      <div className="flex items-center gap-2 px-2 py-1 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 shrink-0 flex-wrap">
        <input
          type="text"
          placeholder="Search logs…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="text-[11px] px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 w-36 outline-none focus:ring-1 focus:ring-blue-400"
        />
        <select
          value={nodeFilter}
          onChange={(e) => setNodeFilter(e.target.value)}
          className="text-[11px] px-1 py-0.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 outline-none focus:ring-1 focus:ring-blue-400"
        >
          <option value="">All nodes</option>
          {distinctNodeIds.map((nid) => (
            <option key={nid} value={nid}>
              {nodeNameMap[nid] ?? nid}
            </option>
          ))}
        </select>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="text-[11px] px-1 py-0.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 outline-none focus:ring-1 focus:ring-blue-400"
        >
          <option value="">All types</option>
          {distinctTypes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="text-[10px] text-gray-400 ml-auto">
          {filteredLogs.length}/{logs.length}
        </span>
      </div>

      {/* -- Log body -------------------------------------------------------- */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="overflow-y-auto flex-1 min-h-0"
      >
        {[...grouped.entries()].map(([key, entries]) => (
          <NodeGroup
            key={key}
            nodeId={key}
            entries={entries}
            nodeName={key === "__run__" ? "Run" : (nodeNameMap[key] ?? key)}
            defaultOpen
            onSelectNode={selectNodeFromLog}
          />
        ))}
      </div>
    </div>
  );
}
