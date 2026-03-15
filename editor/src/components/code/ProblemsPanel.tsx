import { useState, useEffect, useCallback, useMemo } from "react";
import * as monaco from "monaco-editor";
import {
  XCircle,
  AlertTriangle,
  Info,
  Lightbulb,
  ChevronRight,
  ChevronDown,
  FileText,
  Filter,
  Sparkles,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { sendToChat } from "./ChatSidebar";

/* ------------------------------------------------------------------ */
/*  External diagnostics (from DAN tool output, linter results, etc.)  */
/* ------------------------------------------------------------------ */

export interface ExternalDiagnostic {
  filePath: string;
  line: number;
  column: number;
  message: string;
  severity: "error" | "warning" | "info";
  source: string;
}

let externalDiagnostics: ExternalDiagnostic[] = [];
const externalListeners: Set<() => void> = new Set();

export function pushExternalDiagnostic(diag: ExternalDiagnostic): void {
  externalDiagnostics = [...externalDiagnostics, diag];
  externalListeners.forEach((fn) => fn());
}

export function clearExternalDiagnostics(source?: string): void {
  if (source) {
    externalDiagnostics = externalDiagnostics.filter(
      (d) => d.source !== source,
    );
  } else {
    externalDiagnostics = [];
  }
  externalListeners.forEach((fn) => fn());
}

function useExternalDiagnostics(): ExternalDiagnostic[] {
  const [, forceUpdate] = useState(0);
  useEffect(() => {
    const listener = () => forceUpdate((v) => v + 1);
    externalListeners.add(listener);
    return () => {
      externalListeners.delete(listener);
    };
  }, []);
  return externalDiagnostics;
}

export function parseErrorsFromText(text: string): ExternalDiagnostic[] {
  const errors: ExternalDiagnostic[] = [];
  const pattern =
    /([\w/.]+\.\w+):(\d+)(?::(\d+))?\s*[-:]\s*(?:(error|warning|info)\s*\w*:\s*)?(.+)/gi;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    errors.push({
      filePath: match[1],
      line: parseInt(match[2]),
      column: parseInt(match[3] ?? "1"),
      message: match[5].trim(),
      severity: (match[4]?.toLowerCase() ?? "error") as
        | "error"
        | "warning"
        | "info",
      source: "DAN",
    });
  }
  return errors;
}

/* ------------------------------------------------------------------ */
/*  Types & constants                                                  */
/* ------------------------------------------------------------------ */

interface UnifiedDiagnostic {
  filePath: string;
  line: number;
  column: number;
  message: string;
  severity: "error" | "warning" | "info" | "hint";
  source: string;
}

interface GroupedDiagnostics {
  [resource: string]: UnifiedDiagnostic[];
}


/* ------------------------------------------------------------------ */
/*  Shared marker polling hook                                         */
/* ------------------------------------------------------------------ */

function useMarkerPolling(intervalMs = 2000) {
  const [markers, setMarkers] = useState<monaco.editor.IMarkerData[]>([]);

  useEffect(() => {
    const poll = () => {
      try {
        const all = monaco.editor.getModelMarkers({});
        setMarkers(all);
      } catch {
        // Monaco not ready yet
      }
    };

    poll();
    const id = setInterval(poll, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);

  return markers;
}

/* ------------------------------------------------------------------ */
/*  Exported hook for StatusBar integration                            */
/* ------------------------------------------------------------------ */

export function useProblemsCount() {
  const markers = useMarkerPolling();
  const extDiags = useExternalDiagnostics();

  return useMemo(() => {
    let errors = 0,
      warnings = 0,
      info = 0;
    for (const m of markers) {
      if (m.severity === 8) errors++;
      else if (m.severity === 4) warnings++;
      else if (m.severity === 2) info++;
    }
    for (const d of extDiags) {
      if (d.severity === "error") errors++;
      else if (d.severity === "warning") warnings++;
      else if (d.severity === "info") info++;
    }
    return { errors, warnings, info };
  }, [markers, extDiags]);
}

/* ------------------------------------------------------------------ */
/*  Panel header tabs                                                  */
/* ------------------------------------------------------------------ */

const PANEL_TABS = ["Problems", "Output", "Debug Console"] as const;

function PanelTabs({
  activeTab,
  onTabChange,
  counts,
}: {
  activeTab: string;
  onTabChange: (tab: string) => void;
  counts: { errors: number; warnings: number; info: number };
}) {
  return (
    <div className="flex items-center gap-0 px-2">
      {PANEL_TABS.map((tab) => (
        <button
          key={tab}
          onClick={() => onTabChange(tab)}
          className={`px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide border-b transition-colors ${
            activeTab === tab
              ? "text-white border-white"
              : "text-gray-500 hover:text-gray-300 border-transparent"
          }`}
        >
          {tab}
        </button>
      ))}

      <div className="ml-auto flex items-center gap-2 text-[11px]">
        {counts.errors > 0 && (
          <span className="flex items-center gap-1">
            <XCircle size={12} className="text-red-400" />
            <span className="text-red-400">{counts.errors}</span>
          </span>
        )}
        {counts.warnings > 0 && (
          <span className="flex items-center gap-1">
            <AlertTriangle size={12} className="text-yellow-400" />
            <span className="text-yellow-400">{counts.warnings}</span>
          </span>
        )}
        {counts.info > 0 && (
          <span className="flex items-center gap-1">
            <Info size={12} className="text-blue-400" />
            <span className="text-blue-400">{counts.info}</span>
          </span>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Filter bar                                                         */
/* ------------------------------------------------------------------ */

function FilterBar({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center px-2 py-1 border-b border-[#3c3c3c]">
      <Filter size={12} className="text-gray-500 mr-1.5 shrink-0" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Filter (e.g. text, error, warning)"
        className="flex-1 bg-transparent text-xs text-gray-300 placeholder:text-gray-600 outline-none"
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  File group                                                         */
/* ------------------------------------------------------------------ */

function shortenPath(resource: string): string {
  try {
    const uri = monaco.Uri.parse(resource);
    const raw = uri.path || resource;
    const parts = raw.split("/");
    return parts.slice(-3).join("/");
  } catch {
    return resource;
  }
}

function unifiedSeverityIcon(severity: string) {
  switch (severity) {
    case "error":
      return <XCircle size={14} className="text-red-400 shrink-0" />;
    case "warning":
      return <AlertTriangle size={14} className="text-yellow-400 shrink-0" />;
    case "info":
      return <Info size={14} className="text-blue-400 shrink-0" />;
    default:
      return <Lightbulb size={14} className="text-gray-400 shrink-0" />;
  }
}

function UnifiedFileGroup({
  resource,
  diagnostics,
  onNavigate,
}: {
  resource: string;
  diagnostics: UnifiedDiagnostic[];
  onNavigate: (resource: string, line: number, col: number) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const display = shortenPath(resource);

  const handleSendToAI = (d: UnifiedDiagnostic) => {
    const file = shortenPath(resource);
    sendToChat(
      `Fix this ${d.severity} in ${file} at line ${d.line}: ${d.message}`,
    );
  };

  return (
    <div>
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="w-full flex items-center gap-1.5 px-2 py-1 text-xs text-gray-400 font-medium hover:bg-[#2a2d2e] transition-colors"
      >
        {collapsed ? (
          <ChevronRight size={14} className="shrink-0" />
        ) : (
          <ChevronDown size={14} className="shrink-0" />
        )}
        <FileText size={13} className="shrink-0 text-gray-500" />
        <span className="truncate text-left">{display}</span>
        <span className="ml-auto text-[10px] text-gray-600 tabular-nums shrink-0">
          {diagnostics.length}
        </span>
      </button>

      {!collapsed &&
        diagnostics.map((d, i) => (
          <div
            key={`${d.line}-${d.column}-${i}`}
            className="w-full flex items-center gap-2 pl-7 pr-2 py-[3px] text-xs hover:bg-[#2a2d2e] transition-colors group"
          >
            <button
              onClick={() => onNavigate(resource, d.line, d.column)}
              className="flex items-center gap-2 flex-1 min-w-0 text-left"
            >
              {unifiedSeverityIcon(d.severity)}
              <span className="text-gray-300 truncate flex-1">
                {d.message}
              </span>
              {d.source === "DAN" ? (
                <span className="text-[9px] bg-purple-800/40 text-purple-300 rounded px-1 shrink-0">
                  DAN
                </span>
              ) : d.source ? (
                <span className="text-gray-600 shrink-0">{d.source}</span>
              ) : null}
              <span className="text-gray-600 tabular-nums shrink-0">
                [{d.line}, {d.column}]
              </span>
            </button>
            <button
              onClick={() => handleSendToAI(d)}
              title="Send to AI"
              className="opacity-0 group-hover:opacity-100 p-0.5 text-gray-500 hover:text-blue-400 transition-all shrink-0"
            >
              <Sparkles size={12} />
            </button>
          </div>
        ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

const UNIFIED_SEVERITY_ORDER: Record<string, number> = {
  error: 0,
  warning: 1,
  info: 2,
  hint: 3,
};

export default function ProblemsPanel() {
  const markers = useMarkerPolling();
  const extDiags = useExternalDiagnostics();
  const [filter, setFilter] = useState("");
  const [panelTab, setPanelTab] = useState("Problems");

  const setActiveFile = useCodeStore((s) => s.setActiveFile);
  const openFiles = useCodeStore((s) => s.openFiles);

  const { grouped, counts } = useMemo(() => {
    const combined: UnifiedDiagnostic[] = [
      ...markers.map((m) => ({
        filePath: m.source?.toString() ?? "unknown",
        line: m.startLineNumber,
        column: m.startColumn,
        message: m.message,
        severity: (m.severity === 8
          ? "error"
          : m.severity === 4
            ? "warning"
            : m.severity === 2
              ? "info"
              : "hint") as UnifiedDiagnostic["severity"],
        source: m.source ?? "Monaco",
      })),
      ...extDiags,
    ];

    const lower = filter.toLowerCase();
    const filtered = filter
      ? combined.filter(
          (d) =>
            d.message.toLowerCase().includes(lower) ||
            d.severity.includes(lower) ||
            d.source.toLowerCase().includes(lower) ||
            d.filePath.toLowerCase().includes(lower),
        )
      : combined;

    const g: GroupedDiagnostics = {};
    let errors = 0,
      warnings = 0,
      info = 0;
    for (const d of filtered) {
      (g[d.filePath] ??= []).push(d);
      if (d.severity === "error") errors++;
      else if (d.severity === "warning") warnings++;
      else if (d.severity === "info") info++;
    }
    for (const key of Object.keys(g)) {
      g[key].sort(
        (a, b) =>
          (UNIFIED_SEVERITY_ORDER[a.severity] ?? 9) -
            (UNIFIED_SEVERITY_ORDER[b.severity] ?? 9) || a.line - b.line,
      );
    }
    return { grouped: g, counts: { errors, warnings, info } };
  }, [markers, extDiags, filter]);

  const handleNavigate = useCallback(
    (resource: string, line: number, col: number) => {
      try {
        const uri = monaco.Uri.parse(resource);
        const filePath = uri.path;
        const isOpen = openFiles.some((f) => f.path === filePath);
        if (isOpen) {
          setActiveFile(filePath);
        }
        const editors = monaco.editor.getEditors();
        for (const ed of editors) {
          const model = ed.getModel();
          if (model && model.uri.toString() === resource) {
            ed.revealLineInCenter(line);
            ed.setPosition({ lineNumber: line, column: col });
            ed.focus();
            break;
          }
        }
      } catch {
        /* navigation failed silently */
      }
    },
    [openFiles, setActiveFile],
  );

  const fileKeys = Object.keys(grouped).sort();

  return (
    <div
      className="h-full w-full flex flex-col"
      style={{ background: "#1e1e1e" }}
    >
      <div className="bg-[#252526] border-b border-[#3c3c3c] shrink-0">
        <PanelTabs
          activeTab={panelTab}
          onTabChange={setPanelTab}
          counts={counts}
        />
      </div>

      {panelTab === "Problems" ? (
        <>
          <FilterBar value={filter} onChange={setFilter} />

          <div className="flex-1 min-h-0 overflow-y-auto">
            {fileKeys.length === 0 ? (
              <div className="flex items-center justify-center h-full text-gray-500 text-xs">
                No problems detected in the workspace
              </div>
            ) : (
              fileKeys.map((resource) => (
                <UnifiedFileGroup
                  key={resource}
                  resource={resource}
                  diagnostics={grouped[resource]}
                  onNavigate={handleNavigate}
                />
              ))
            )}
          </div>
        </>
      ) : (
        <div className="flex-1 flex items-center justify-center text-gray-500 text-xs">
          {panelTab} — coming soon
        </div>
      )}
    </div>
  );
}
