import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

const EVENT_ICON: Record<string, (cls: string) => React.JSX.Element> = {
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

function getEventIcon(eventType: string): React.JSX.Element {
  const factory = EVENT_ICON[eventType];
  const color = EVENT_COLORS[eventType] ?? "text-gray-400";
  return factory ? factory(color) : <CircleIcon className={color} />;
}

function extractFailureText(data?: Record<string, unknown>): string | null {
  if (!data) return null;
  if (typeof data.error === "string" && data.error.trim()) {
    return data.error.trim();
  }
  const errors = data.errors;
  if (!errors || typeof errors !== "object") return null;
  for (const value of Object.values(errors as Record<string, unknown>)) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function dataContent(entry: LogEntry): string | null {
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
      return typeof result === "string" ? result : result ? JSON.stringify(result, null, 2) : null;
    }
    case "code_output": {
      const stdout = d.stdout as string | undefined;
      const stderr = d.stderr as string | undefined;
      const parts: string[] = [];
      if (stdout) parts.push(stdout);
      if (stderr) parts.push(`stderr: ${stderr}`);
      return parts.join("\n") || null;
    }
    case "intermediate_text": {
      const text = d.text as string | undefined;
      return text ?? null;
    }
    case "node_failed":
    case "run_failed":
      return extractFailureText(d);
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function LogEntryRow({ entry, onClick }: { entry: LogEntry; onClick?: () => void }) {
  const color = EVENT_COLORS[entry.event_type] ?? "text-gray-500";
  const content = dataContent(entry);
  const [expanded, setExpanded] = useState(false);
  const isLong = content ? content.length > 120 : false;

  return (
    <div
      className={`py-0.5 text-[11px] font-mono leading-tight group ${onClick ? "cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50" : ""}`}
    >
      <div className="flex items-start gap-1.5" onClick={onClick}>
        <span className="shrink-0 mt-px">{getEventIcon(entry.event_type)}</span>
        <span className="text-gray-400 shrink-0">{formatTime(entry.timestamp)}</span>
        <span className={`shrink-0 ${color}`}>{entry.event_type}</span>
        {content && !expanded && (
          <span className="text-gray-500 truncate ml-1">{content.slice(0, 120)}{isLong ? "…" : ""}</span>
        )}
        {isLong && (
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
            className="shrink-0 ml-auto text-[10px] text-indigo-500 hover:text-indigo-700 px-1"
          >
            {expanded ? "▾ less" : "▸ more"}
          </button>
        )}
      </div>
      {expanded && content && (
        <pre className="ml-8 mt-0.5 text-[10px] text-gray-600 bg-gray-50 dark:bg-gray-800/50 rounded px-2 py-1 whitespace-pre-wrap break-words max-h-80 overflow-auto border border-gray-100 dark:border-gray-700">{content}</pre>
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
  const nodeUsage = useGraphStore((s) => s.nodeUsage);
  const nodeCosts = useGraphStore((s) => s.nodeCosts);
  const usage = nodeUsage[nodeId];
  const cost = nodeCosts[nodeId];

  const categorized = useMemo(() => {
    const groups: Record<string, LogEntry[]> = {};
    for (const e of entries) {
      const cat = EVENT_CATEGORY[e.event_type] ?? "lifecycle";
      (groups[cat] ??= []).push(e);
    }
    return groups;
  }, [entries]);

  const hasRichEvents = Boolean(categorized.thinking || categorized.tool || categorized.output || categorized.error);

  const handleInspectInputs = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelectNode(nodeId);
  };

  const handleAddTestCase = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelectNode(nodeId);
    window.dispatchEvent(new CustomEvent("dan:open-test-case-modal", { detail: { nodeId, prefillFromRun: true } }));
  };

  const handleRerun = (e: React.MouseEvent) => {
    e.stopPropagation();
    useGraphStore.getState().rerunFromNode(nodeId, "downstream_of");
  };

  const handleFixThis = (e: React.MouseEvent) => {
    e.stopPropagation();
    const errorEntries = categorized.error ?? [];
    const firstError = errorEntries[0];
    const errorType = firstError?.event_type ?? "error";
    const errorMsg = firstError ? (dataContent(firstError) ?? firstError.message) : "unknown error";
    const firstLine = errorMsg.split("\n")[0].slice(0, 120);
    const summary = `Fix error in ${nodeName}: ${errorType} — ${firstLine}`;
    useGraphStore.getState().openDebugWithError(summary);
  };

  return (
    <div className="group/nodegroup border-b border-gray-100 dark:border-gray-800 last:border-b-0">
      <div className="flex items-center gap-1.5 w-full px-2 py-1 text-[11px] font-semibold text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800/50">
        <button className="flex items-center gap-1.5 flex-1 text-left min-w-0" onClick={() => setOpen(!open)}>
          <ChevronIcon open={open} className="text-gray-400 shrink-0" />
          <span className="truncate">{nodeName}</span>
          {usage && usage.total_tokens > 0 && (
            <span className="text-indigo-500 font-normal text-[10px] shrink-0">{usage.total_tokens.toLocaleString()} tok</span>
          )}
          {cost != null && cost > 0 && (
            <span className="text-emerald-600 font-normal text-[10px] shrink-0">{cost >= 0.01 ? `$${cost.toFixed(2)}` : `$${cost.toFixed(4)}`}</span>
          )}
          <span className="text-gray-400 font-normal ml-auto shrink-0">{entries.length}</span>
        </button>
        <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover/nodegroup:opacity-100 hover:opacity-100" style={{ opacity: open ? 1 : undefined }}>
          {categorized.error && categorized.error.length > 0 && (
            <button onClick={handleFixThis} title="Fix this error in Debug mode" className="p-0.5 rounded text-red-400 hover:text-red-600 hover:bg-red-50">
              <WrenchIcon className="text-current" />
            </button>
          )}
          <button onClick={handleInspectInputs} title="Inspect inputs" className="p-0.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          </button>
          <button onClick={handleAddTestCase} title="Add test case from this run" className="p-0.5 rounded text-gray-400 hover:text-emerald-600 hover:bg-emerald-50">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
          </button>
          <button onClick={handleRerun} title="Rerun from here" className="p-0.5 rounded text-gray-400 hover:text-amber-600 hover:bg-amber-50">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
          </button>
        </div>
      </div>

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

function RunSummaryBar() {
  const runSummary = useGraphStore((s) => s.runSummary);
  const runStatus = useGraphStore((s) => s.runStatus);
  const nodeCosts = useGraphStore((s) => s.nodeCosts);
  const nodeUsage = useGraphStore((s) => s.nodeUsage);
  const wasteFindings = useGraphStore((s) => s.wasteFindings);
  const nodes = useGraphStore((s) => s.nodes);
  const [expanded, setExpanded] = useState(false);
  if (!runSummary || (runStatus !== "completed" && runStatus !== "failed")) return null;
  const ok = runStatus === "completed";
  const elapsed = runSummary.elapsed_seconds != null ? `${runSummary.elapsed_seconds}s` : "—";
  const total = runSummary.total_tokens ?? 0;
  const prompt = runSummary.total_prompt_tokens ?? 0;
  const completion = runSummary.total_completion_tokens ?? 0;
  const tokenStr = total > 0
    ? `${total.toLocaleString()} tokens (${prompt.toLocaleString()} prompt + ${completion.toLocaleString()} completion)`
    : "no token data";
  const totalCost = Object.values(nodeCosts).reduce((a, b) => a + b, 0);
  const costStr = totalCost > 0 ? `~$${totalCost.toFixed(4)}` : "";

  // 18-4: Top 3 most expensive nodes
  const nodeNameMap: Record<string, string> = {};
  for (const n of nodes) {
    const d = n.data as Record<string, unknown>;
    nodeNameMap[n.id] = (d?.name ?? d?.label ?? n.id) as string;
  }
  const topNodes = Object.entries(nodeUsage)
    .sort(([, a], [, b]) => b.total_tokens - a.total_tokens)
    .slice(0, 3);

  // 18-4: Waste summary
  const totalWaste = wasteFindings.reduce((a, f) => a + f.estimated_saveable_tokens, 0);
  const wasteCategories = new Set(wasteFindings.map((f) => f.category));

  return (
    <div className={`border-t ${ok ? "border-green-200" : "border-red-200"}`}>
      <div
        className={`flex items-center gap-2 px-3 py-1.5 text-[11px] font-medium cursor-pointer ${ok ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"}`}
        onClick={() => setExpanded(!expanded)}
      >
        <span>{ok ? "Completed" : "Failed"} in {elapsed}</span>
        <span className="text-gray-400">|</span>
        <span>{tokenStr}</span>
        {costStr && (
          <>
            <span className="text-gray-400">|</span>
            <span className="text-emerald-700">{costStr}</span>
          </>
        )}
        {wasteFindings.length > 0 && (
          <>
            <span className="text-gray-400">|</span>
            <span className="text-amber-600">{wasteFindings.length} waste finding{wasteFindings.length > 1 ? "s" : ""}</span>
          </>
        )}
        <span className="ml-auto text-gray-400 text-[10px]">{expanded ? "collapse" : "details"}</span>
      </div>

      {expanded && (
        <div className={`px-3 py-2 text-[11px] space-y-2 ${ok ? "bg-green-50/60" : "bg-red-50/60"}`}>
          {/* Top expensive nodes */}
          {topNodes.length > 0 && (
            <div>
              <div className="font-semibold text-gray-600 mb-0.5">Top Token Consumers</div>
              {topNodes.map(([nid, u], i) => (
                <div key={nid} className="flex items-center gap-2 text-gray-500">
                  <span className="text-gray-400">{i + 1}.</span>
                  <span className="truncate max-w-[140px]">{nodeNameMap[nid] ?? nid}</span>
                  <span className="font-mono text-indigo-600">{u.total_tokens.toLocaleString()} tok</span>
                  {nodeCosts[nid] != null && nodeCosts[nid] > 0 && (
                    <span className="text-emerald-600">
                      {nodeCosts[nid] >= 0.01 ? `$${nodeCosts[nid].toFixed(2)}` : `$${nodeCosts[nid].toFixed(4)}`}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Waste summary */}
          {wasteFindings.length > 0 && (
            <div>
              <div className="font-semibold text-amber-700 mb-0.5">
                Waste Summary: ~{totalWaste.toLocaleString()} tokens saveable across {wasteFindings.length} finding{wasteFindings.length > 1 ? "s" : ""}
              </div>
              <div className="text-gray-500">
                Categories: {[...wasteCategories].join(", ")}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

type SortKey = "name" | "duration" | "tokens" | "cost";

function SortArrow({ active, dir }: { active: boolean; dir: "asc" | "desc" }) {
  if (!active) return <span className="text-gray-300 ml-0.5">⇅</span>;
  return <span className="text-indigo-500 ml-0.5">{dir === "asc" ? "▲" : "▼"}</span>;
}

export default function LogPanel() {
  const logs = useGraphStore((s) => s.logs);
  const nodes = useGraphStore((s) => s.nodes);
  const selectNodeFromLog = useGraphStore((s) => s.selectNodeFromLog);

  const [search, setSearch] = useState("");
  const [nodeFilter, setNodeFilter] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const prevLogsEmpty = useRef(true);

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

  const nodeTimings = useGraphStore((s) => s.nodeTimings);
  const nodeUsage = useGraphStore((s) => s.nodeUsage);
  const nodeCosts = useGraphStore((s) => s.nodeCosts);

  useEffect(() => {
    const wasEmpty = prevLogsEmpty.current;
    const isNonEmpty = logs.length > 0;
    if (wasEmpty && isNonEmpty) {
      setSortKey(null);
      setSortDir("asc");
    }
    prevLogsEmpty.current = logs.length === 0;
  }, [logs.length]);

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

  const sortedGroupEntries = useMemo(() => {
    const entries = [...grouped.entries()];
    if (!sortKey) return entries;

    return entries.sort(([keyA], [keyB]) => {
      let cmp = 0;
      switch (sortKey) {
        case "name": {
          const nameA = (nodeNameMap[keyA] ?? keyA).toLowerCase();
          const nameB = (nodeNameMap[keyB] ?? keyB).toLowerCase();
          cmp = nameA < nameB ? -1 : nameA > nameB ? 1 : 0;
          break;
        }
        case "duration": {
          const tA = nodeTimings[keyA];
          const tB = nodeTimings[keyB];
          const durA = tA?.end != null ? tA.end - tA.start : 0;
          const durB = tB?.end != null ? tB.end - tB.start : 0;
          cmp = durA - durB;
          break;
        }
        case "tokens": {
          const tokA = nodeUsage[keyA]?.total_tokens ?? 0;
          const tokB = nodeUsage[keyB]?.total_tokens ?? 0;
          cmp = tokA - tokB;
          break;
        }
        case "cost": {
          const costA = nodeCosts[keyA] ?? 0;
          const costB = nodeCosts[keyB] ?? 0;
          cmp = costA - costB;
          break;
        }
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [grouped, sortKey, sortDir, nodeNameMap, nodeTimings, nodeUsage, nodeCosts]);

  const toggleSort = useCallback((key: SortKey) => {
    setSortKey((prev) => {
      if (prev !== key) {
        setSortDir("asc");
        return key;
      }
      if (sortDir === "asc") {
        setSortDir("desc");
        return key;
      }
      setSortDir("asc");
      return null;
    });
  }, [sortDir]);

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

      {/* -- Sort header ------------------------------------------------------ */}
      <div className="flex items-center gap-0 px-2 py-0.5 border-b border-gray-100 dark:border-gray-800 bg-gray-50/80 dark:bg-gray-900/30 shrink-0 text-[10px] font-medium text-gray-500 uppercase tracking-wide select-none">
        {(["name", "duration", "tokens", "cost"] as const).map((key) => (
          <button
            key={key}
            onClick={() => toggleSort(key)}
            className={`px-1.5 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors ${
              key === "name" ? "flex-1 text-left" : "shrink-0"
            }`}
          >
            {key === "name" ? "Node" : key.charAt(0).toUpperCase() + key.slice(1)}
            <SortArrow active={sortKey === key} dir={sortDir} />
          </button>
        ))}
      </div>

      {/* -- Log body -------------------------------------------------------- */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="overflow-y-auto flex-1 min-h-0"
      >
        {sortedGroupEntries.map(([key, entries]) => (
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

      <RunSummaryBar />
    </div>
  );
}
