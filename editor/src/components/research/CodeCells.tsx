import {
  useState,
  useRef,
  useCallback,
  useEffect,
} from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor as monacoEditor } from "monaco-editor";
import {
  Plus,
  Play,
  Trash2,
  ChevronDown,
  Check,
  X,
  Loader2,
  FileDown,
  Globe,
  FileText,
  Code2,
  Terminal,
  BarChart3,
} from "lucide-react";
import {
  useResearchStore,
  type CellTool,
} from "../../store/useResearchStore";
import { requestEditorChatText } from "../../lib/editorChat";
import { useSettingsStore } from "../../store/useSettingsStore";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface CellOutput {
  type: "text" | "table" | "chart" | "image" | "error";
  data: any;
}

interface CodeCell {
  id: string;
  code: string;
  language: "python" | "r" | "javascript";
  output: CellOutput | null;
  status: "idle" | "running" | "completed" | "error";
  runDuration?: number;
  tool: CellTool;
  toolParams?: Record<string, string>;
}

type CellLanguage = CodeCell["language"];

const LANGUAGES: { value: CellLanguage; label: string }[] = [
  { value: "python", label: "Python" },
  { value: "r", label: "R" },
  { value: "javascript", label: "JavaScript" },
];

const TOOLS: { value: CellTool; label: string; icon: typeof Code2; description: string }[] = [
  { value: "python_eval", label: "Python", icon: Code2, description: "Evaluate Python code" },
  { value: "web_search", label: "Web Search", icon: Globe, description: "Search the web" },
  { value: "pdf_read", label: "PDF Read", icon: FileText, description: "Extract text from PDF page" },
  { value: "shell_exec", label: "Shell", icon: Terminal, description: "Run a shell command" },
  { value: "data_analyze", label: "Data Analyze", icon: BarChart3, description: "Analyze a dataset" },
];

const TOOL_COLORS: Record<CellTool, string> = {
  python_eval: "text-green-400",
  web_search: "text-blue-400",
  pdf_read: "text-orange-400",
  shell_exec: "text-yellow-400",
  data_analyze: "text-purple-400",
};

const LINE_HEIGHT = 19;
const MIN_LINES = 2;
const MAX_LINES = 15;

let cellCounter = 1;
function nextId() {
  return `cell-${Date.now()}-${cellCounter++}`;
}

/* ------------------------------------------------------------------ */
/*  Mock execution (for python_eval)                                   */
/* ------------------------------------------------------------------ */

function mockExecute(code: string, language: CellLanguage): CellOutput {
  const trimmed = code.trim();

  const printMatch = trimmed.match(/print\s*\(\s*(['"`])(.*?)\1\s*\)/s);
  if (printMatch) return { type: "text", data: printMatch[2] };

  if (/console\.log\s*\(/.test(trimmed)) {
    const m = trimmed.match(/console\.log\s*\(\s*(['"`])(.*?)\1\s*\)/s);
    return { type: "text", data: m ? m[2] : "[console output]" };
  }

  if (/import\s+matplotlib|plt\.|ggplot|plot\(/.test(trimmed)) {
    return {
      type: "chart",
      data: {
        type: "bar",
        labels: ["A", "B", "C", "D", "E"],
        values: [12, 19, 3, 5, 8],
        title: "Sample Chart",
      },
    };
  }

  if (/pd\.DataFrame|data\.frame|tibble/.test(trimmed)) {
    return {
      type: "table",
      data: {
        headers: ["id", "name", "value", "category"],
        rows: [
          ["1", "Alpha", "0.85", "A"],
          ["2", "Beta", "0.42", "B"],
          ["3", "Gamma", "0.97", "A"],
          ["4", "Delta", "0.31", "C"],
          ["5", "Epsilon", "0.66", "B"],
        ],
      },
    };
  }

  if (/raise |Error|Exception|stop\(|throw /.test(trimmed)) {
    return {
      type: "error",
      data: {
        name: "RuntimeError",
        message: "Mock error raised by cell",
        traceback:
          `Traceback (most recent call last):\n` +
          `  File "<cell>", line 1, in <module>\n` +
          `RuntimeError: Mock error raised by cell`,
      },
    };
  }

  if (!trimmed) return { type: "text", data: "" };
  return { type: "text", data: `Cell executed (${language})` };
}

/* ------------------------------------------------------------------ */
/*  Tool execution via API                                             */
/* ------------------------------------------------------------------ */

async function executeToolCall(
  tool: CellTool,
  content: string,
  params: Record<string, string>,
): Promise<CellOutput> {
  const messageMap: Record<CellTool, string> = {
    python_eval: content,
    web_search: `Search the web for: ${content}`,
    pdf_read: `Extract and summarize text from page ${params.page || "1"} of paper "${params.paper || "current"}"`,
    shell_exec: `Execute shell command: ${content}`,
    data_analyze: `Analyze dataset "${params.dataset || "data"}" with analysis type: ${params.analysisType || "summary"}. Additional instructions: ${content}`,
  };

  try {
    const result = await requestEditorChatText({
      message: messageMap[tool] || content,
      mode: "ask",
      scope: `research-code-cell:${tool}`,
      surfaceContext: { tool, params },
    });
    return { type: "text", data: result };
  } catch (err: any) {
    return { type: "error", data: { message: err.message || "Tool execution failed" } };
  }
}

/* ------------------------------------------------------------------ */
/*  Status badge                                                       */
/* ------------------------------------------------------------------ */

function StatusBadge({ status }: { status: CodeCell["status"] }) {
  switch (status) {
    case "running":
      return <Loader2 size={12} className="text-blue-400 animate-spin" />;
    case "completed":
      return <Check size={12} className="text-green-500" />;
    case "error":
      return <X size={12} className="text-red-500" />;
    default:
      return <span className="w-3 h-3 rounded-full border border-gray-600 block" />;
  }
}

/* ------------------------------------------------------------------ */
/*  Language selector                                                  */
/* ------------------------------------------------------------------ */

function LanguageSelector({
  value,
  onChange,
}: {
  value: CellLanguage;
  onChange: (lang: CellLanguage) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-gray-400 bg-gray-800 rounded hover:bg-gray-700 transition-colors"
      >
        {LANGUAGES.find((l) => l.value === value)?.label}
        <ChevronDown size={10} />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-0.5 z-50 bg-[#2d2d2d] border border-gray-700 rounded shadow-lg min-w-[90px]">
          {LANGUAGES.map((l) => (
            <button
              key={l.value}
              onClick={() => {
                onChange(l.value);
                setOpen(false);
              }}
              className={`w-full text-left px-2 py-1 text-[10px] hover:bg-gray-700 transition-colors ${
                l.value === value ? "text-purple-400" : "text-gray-300"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tool selector                                                      */
/* ------------------------------------------------------------------ */

function ToolSelector({
  value,
  onChange,
}: {
  value: CellTool;
  onChange: (tool: CellTool) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = TOOLS.find((t) => t.value === value)!;
  const Icon = current.icon;

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1 px-1.5 py-0.5 text-[10px] bg-gray-800 rounded hover:bg-gray-700 transition-colors ${TOOL_COLORS[value]}`}
        title={current.description}
      >
        <Icon size={10} />
        {current.label}
        <ChevronDown size={10} />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-0.5 z-50 bg-[#2d2d2d] border border-gray-700 rounded shadow-lg min-w-[160px]">
          {TOOLS.map((t) => {
            const TIcon = t.icon;
            return (
              <button
                key={t.value}
                onClick={() => {
                  onChange(t.value);
                  setOpen(false);
                }}
                className={`w-full text-left px-2 py-1.5 text-[10px] hover:bg-gray-700 transition-colors flex items-center gap-2 ${
                  t.value === value ? TOOL_COLORS[t.value] : "text-gray-300"
                }`}
              >
                <TIcon size={11} />
                <div>
                  <div>{t.label}</div>
                  <div className="text-[9px] text-gray-600">{t.description}</div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tool-specific input forms                                          */
/* ------------------------------------------------------------------ */

function WebSearchForm({
  code,
  onChange,
}: {
  code: string;
  onChange: (code: string) => void;
}) {
  return (
    <div className="px-3 py-2 bg-[#1a1a2e] border-y border-gray-800/30">
      <label className="text-[9px] text-gray-500 uppercase tracking-wider mb-1 block">
        Search Query
      </label>
      <input
        value={code}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Enter search query..."
        className="w-full bg-[#1e1e1e] border border-gray-700 rounded px-2.5 py-1.5 text-xs text-gray-300 placeholder-gray-600 outline-none focus:border-blue-500/50"
      />
    </div>
  );
}

function PdfReadForm({
  code: _code,
  params,
  onChange: _onChange,
  onParamsChange,
}: {
  code: string;
  params: Record<string, string>;
  onChange: (code: string) => void;
  onParamsChange: (params: Record<string, string>) => void;
}) {
  const papers = useResearchStore((s) => s.papers);

  return (
    <div className="px-3 py-2 bg-[#1a1a2e] border-y border-gray-800/30 space-y-2">
      <div>
        <label className="text-[9px] text-gray-500 uppercase tracking-wider mb-1 block">
          Paper
        </label>
        <select
          value={params.paper ?? ""}
          onChange={(e) => onParamsChange({ ...params, paper: e.target.value })}
          className="w-full bg-[#1e1e1e] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 outline-none focus:border-blue-500/50"
        >
          <option value="">Select paper...</option>
          {papers
            .filter((p) => p.filePath)
            .map((p) => (
              <option key={p.id} value={p.title}>
                {p.title}
              </option>
            ))}
        </select>
      </div>
      <div>
        <label className="text-[9px] text-gray-500 uppercase tracking-wider mb-1 block">
          Page Number
        </label>
        <input
          type="number"
          min={1}
          value={params.page ?? "1"}
          onChange={(e) => onParamsChange({ ...params, page: e.target.value })}
          className="w-20 bg-[#1e1e1e] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 outline-none focus:border-blue-500/50"
        />
      </div>
    </div>
  );
}

function ShellExecForm({
  code,
  onChange,
}: {
  code: string;
  onChange: (code: string) => void;
}) {
  return (
    <div className="px-3 py-2 bg-[#1a1a2e] border-y border-gray-800/30">
      <label className="text-[9px] text-gray-500 uppercase tracking-wider mb-1 block">
        Command
      </label>
      <input
        value={code}
        onChange={(e) => onChange(e.target.value)}
        placeholder="$ command --args"
        className="w-full bg-[#1e1e1e] border border-gray-700 rounded px-2.5 py-1.5 text-xs text-gray-300 placeholder-gray-600 outline-none focus:border-blue-500/50 font-mono"
      />
    </div>
  );
}

function DataAnalyzeForm({
  code,
  params,
  onChange,
  onParamsChange,
}: {
  code: string;
  params: Record<string, string>;
  onChange: (code: string) => void;
  onParamsChange: (params: Record<string, string>) => void;
}) {
  return (
    <div className="px-3 py-2 bg-[#1a1a2e] border-y border-gray-800/30 space-y-2">
      <div>
        <label className="text-[9px] text-gray-500 uppercase tracking-wider mb-1 block">
          Dataset
        </label>
        <input
          value={params.dataset ?? ""}
          onChange={(e) => onParamsChange({ ...params, dataset: e.target.value })}
          placeholder="Dataset name or path..."
          className="w-full bg-[#1e1e1e] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 placeholder-gray-600 outline-none focus:border-blue-500/50"
        />
      </div>
      <div>
        <label className="text-[9px] text-gray-500 uppercase tracking-wider mb-1 block">
          Analysis Type
        </label>
        <select
          value={params.analysisType ?? "summary"}
          onChange={(e) => onParamsChange({ ...params, analysisType: e.target.value })}
          className="w-full bg-[#1e1e1e] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 outline-none focus:border-blue-500/50"
        >
          <option value="summary">Summary Statistics</option>
          <option value="correlation">Correlation Analysis</option>
          <option value="regression">Regression</option>
          <option value="clustering">Clustering</option>
          <option value="visualization">Visualization</option>
        </select>
      </div>
      <div>
        <label className="text-[9px] text-gray-500 uppercase tracking-wider mb-1 block">
          Instructions
        </label>
        <textarea
          value={code}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Additional analysis instructions..."
          className="w-full h-14 bg-[#1e1e1e] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 placeholder-gray-600 outline-none focus:border-blue-500/50 resize-none"
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Output renderers                                                   */
/* ------------------------------------------------------------------ */

function ChartOutput({ data }: { data: { type: string; labels: string[]; values: number[]; title: string } }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<any>(null);

  useEffect(() => {
    let cancelled = false;
    import("chart.js/auto").then((mod) => {
      if (cancelled || !canvasRef.current) return;
      chartRef.current?.destroy();
      chartRef.current = new mod.default(canvasRef.current, {
        type: (data.type as any) || "bar",
        data: {
          labels: data.labels,
          datasets: [
            {
              label: data.title,
              data: data.values,
              backgroundColor: [
                "rgba(168,85,247,0.6)",
                "rgba(59,130,246,0.6)",
                "rgba(16,185,129,0.6)",
                "rgba(251,191,36,0.6)",
                "rgba(239,68,68,0.6)",
              ],
              borderColor: [
                "rgb(168,85,247)",
                "rgb(59,130,246)",
                "rgb(16,185,129)",
                "rgb(251,191,36)",
                "rgb(239,68,68)",
              ],
              borderWidth: 1,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color: "#9ca3af", font: { size: 10 } } },
            title: {
              display: !!data.title,
              text: data.title,
              color: "#d1d5db",
              font: { size: 11 },
            },
          },
          scales: {
            x: { ticks: { color: "#6b7280", font: { size: 9 } }, grid: { color: "#374151" } },
            y: { ticks: { color: "#6b7280", font: { size: 9 } }, grid: { color: "#374151" } },
          },
        },
      });
    });
    return () => {
      cancelled = true;
      chartRef.current?.destroy();
    };
  }, [data]);

  return (
    <div className="h-48 p-2">
      <canvas ref={canvasRef} />
    </div>
  );
}

function TableOutput({ data }: { data: { headers: string[]; rows: string[][] } }) {
  return (
    <div className="overflow-auto max-h-52">
      <table className="text-[10px] w-full">
        <thead>
          <tr className="bg-gray-800/50">
            {data.headers.map((h, i) => (
              <th key={i} className="px-2 py-1 text-left text-gray-400 font-medium border-b border-gray-700 whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, i) => (
            <tr key={i} className="hover:bg-gray-800/20">
              {row.map((cell, j) => (
                <td key={j} className="px-2 py-0.5 text-gray-400 border-b border-gray-800/30 whitespace-nowrap">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CellOutputView({ output }: { output: CellOutput }) {
  switch (output.type) {
    case "text":
      return (
        <pre className="px-3 py-2 text-[11px] text-gray-300 font-mono whitespace-pre-wrap bg-gray-900/50">
          {output.data || "\u200b"}
        </pre>
      );
    case "table":
      return <TableOutput data={output.data} />;
    case "chart":
      return <ChartOutput data={output.data} />;
    case "image":
      return (
        <div className="p-2">
          <img src={output.data} alt="cell output" className="max-h-52 object-contain rounded" />
        </div>
      );
    case "error":
      return (
        <pre className="px-3 py-2 text-[11px] text-red-400 font-mono whitespace-pre-wrap bg-red-950/20 border-l-2 border-red-500">
          {output.data?.traceback ?? output.data?.message ?? String(output.data)}
        </pre>
      );
    default:
      return null;
  }
}

/* ------------------------------------------------------------------ */
/*  Tool status badge                                                  */
/* ------------------------------------------------------------------ */

function ToolBadge({ tool }: { tool: CellTool }) {
  if (tool === "python_eval") return null;
  const info = TOOLS.find((t) => t.value === tool);
  if (!info) return null;
  const Icon = info.icon;
  return (
    <span className={`flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-gray-800/60 ${TOOL_COLORS[tool]}`}>
      <Icon size={9} />
      {info.label}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Single cell                                                        */
/* ------------------------------------------------------------------ */

function CellItem({
  cell,
  index,
  onUpdate,
  onRun,
  onDelete,
  onInsertBelow,
}: {
  cell: CodeCell;
  index: number;
  onUpdate: (updates: Partial<CodeCell>) => void;
  onRun: () => void;
  onDelete: () => void;
  onInsertBelow: () => void;
}) {
  const [editorHeight, setEditorHeight] = useState(MIN_LINES * LINE_HEIGHT);
  const editorRef = useRef<monacoEditor.IStandaloneCodeEditor | null>(null);
  const theme = useSettingsStore((s) => resolveMonacoTheme(s.theme));
  const isCodeTool = cell.tool === "python_eval";

  const handleMount: OnMount = (editor) => {
    editorRef.current = editor;
    const updateHeight = () => {
      const h = editor.getContentHeight();
      const clamped = Math.min(Math.max(h, MIN_LINES * LINE_HEIGHT), MAX_LINES * LINE_HEIGHT);
      setEditorHeight(clamped);
    };
    editor.onDidContentSizeChange(updateHeight);
    updateHeight();
  };

  const hasInsertableOutput =
    cell.output &&
    (cell.output.type === "chart" || cell.output.type === "table" || cell.output.type === "image");

  return (
    <div className="border-b border-gray-800/50 group/cell">
      {/* Cell header */}
      <div className="flex items-center gap-1.5 px-2 py-1 bg-[#1e1e1e]">
        <span className="text-[10px] text-gray-600 font-mono w-6 text-right">[{index + 1}]</span>
        <StatusBadge status={cell.status} />

        <ToolSelector
          value={cell.tool}
          onChange={(tool) => onUpdate({ tool })}
        />

        {isCodeTool && (
          <LanguageSelector
            value={cell.language}
            onChange={(lang) => onUpdate({ language: lang })}
          />
        )}

        <ToolBadge tool={cell.tool} />

        <div className="flex-1" />
        {cell.runDuration !== undefined && cell.status !== "running" && (
          <span className="text-[9px] text-gray-600 mr-1">
            {(cell.runDuration / 1000).toFixed(1)}s
          </span>
        )}
        <button
          onClick={onRun}
          disabled={cell.status === "running"}
          className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] text-green-400 hover:bg-green-900/20 rounded transition-colors disabled:opacity-40"
          title="Run cell"
        >
          <Play size={11} />
        </button>
        <button
          onClick={onInsertBelow}
          className="opacity-0 group-hover/cell:opacity-100 text-gray-500 hover:text-gray-300 transition-all"
          title="Insert cell below"
        >
          <Plus size={12} />
        </button>
        <button
          onClick={onDelete}
          className="opacity-0 group-hover/cell:opacity-100 text-gray-500 hover:text-red-400 transition-all"
          title="Delete cell"
        >
          <Trash2 size={12} />
        </button>
      </div>

      {/* Tool-specific input */}
      {cell.tool === "web_search" && (
        <WebSearchForm
          code={cell.code}
          onChange={(code) => onUpdate({ code })}
        />
      )}
      {cell.tool === "pdf_read" && (
        <PdfReadForm
          code={cell.code}
          params={cell.toolParams ?? {}}
          onChange={(code) => onUpdate({ code })}
          onParamsChange={(toolParams) => onUpdate({ toolParams })}
        />
      )}
      {cell.tool === "shell_exec" && (
        <ShellExecForm
          code={cell.code}
          onChange={(code) => onUpdate({ code })}
        />
      )}
      {cell.tool === "data_analyze" && (
        <DataAnalyzeForm
          code={cell.code}
          params={cell.toolParams ?? {}}
          onChange={(code) => onUpdate({ code })}
          onParamsChange={(toolParams) => onUpdate({ toolParams })}
        />
      )}

      {/* Monaco editor — only for code tools */}
      {isCodeTool && (
        <div
          className="border-y border-gray-800/30"
          style={{ height: editorHeight }}
        >
          <Editor
            height="100%"
            language={cell.language}
            value={cell.code}
            onChange={(v) => onUpdate({ code: v ?? "" })}
            onMount={handleMount}
            theme={theme}
            options={{
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              lineNumbers: "off",
              glyphMargin: false,
              folding: false,
              lineDecorationsWidth: 4,
              lineNumbersMinChars: 0,
              renderLineHighlight: "none",
              overviewRulerLanes: 0,
              scrollbar: { vertical: "hidden", horizontal: "auto" },
              fontSize: 12,
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              padding: { top: 4, bottom: 4 },
              wordWrap: "on",
              automaticLayout: true,
            }}
          />
        </div>
      )}

      {/* Output */}
      {cell.output && (
        <div className="relative">
          <CellOutputView output={cell.output} />
          {hasInsertableOutput && (
            <button
              className="absolute top-1 right-1 flex items-center gap-1 px-1.5 py-0.5 text-[9px] text-purple-400 bg-gray-800/80 rounded opacity-0 group-hover/cell:opacity-100 hover:bg-purple-900/30 transition-all"
              onClick={() => {
                window.dispatchEvent(
                  new CustomEvent("research:insert-figure", {
                    detail: { cellId: cell.id, output: cell.output },
                  }),
                );
              }}
            >
              <FileDown size={10} /> Insert into paper
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function CodeCells() {
  const [cells, setCells] = useState<CodeCell[]>([
    { id: nextId(), code: "", language: "python", output: null, status: "idle", tool: "python_eval" },
  ]);

  const updateCell = useCallback((id: string, updates: Partial<CodeCell>) => {
    setCells((prev) => prev.map((c) => (c.id === id ? { ...c, ...updates } : c)));
  }, []);

  const addCell = useCallback(() => {
    setCells((prev) => [
      ...prev,
      { id: nextId(), code: "", language: "python", output: null, status: "idle", tool: "python_eval" },
    ]);
  }, []);

  const insertCellBelow = useCallback((index: number) => {
    setCells((prev) => {
      const next = [...prev];
      next.splice(index + 1, 0, {
        id: nextId(),
        code: "",
        language: "python",
        output: null,
        status: "idle",
        tool: "python_eval",
      });
      return next;
    });
  }, []);

  const deleteCell = useCallback((id: string) => {
    setCells((prev) => {
      if (prev.length <= 1) return prev;
      return prev.filter((c) => c.id !== id);
    });
  }, []);

  const runCell = useCallback(
    async (id: string) => {
      const cell = cells.find((c) => c.id === id);
      if (!cell) return;

      setCells((prev) =>
        prev.map((c) =>
          c.id === id ? { ...c, status: "running" as const, output: null, runDuration: undefined } : c,
        ),
      );

      const start = Date.now();

      if (cell.tool === "python_eval") {
        setTimeout(() => {
          const result = mockExecute(cell.code, cell.language);
          const dur = Date.now() - start;
          setCells((prev) =>
            prev.map((c) =>
              c.id === id
                ? {
                    ...c,
                    status: result.type === "error" ? ("error" as const) : ("completed" as const),
                    output: result,
                    runDuration: dur,
                  }
                : c,
            ),
          );
        }, 800 + Math.random() * 400);
      } else {
        const result = await executeToolCall(cell.tool, cell.code, cell.toolParams ?? {});
        const dur = Date.now() - start;
        setCells((prev) =>
          prev.map((c) =>
            c.id === id
              ? {
                  ...c,
                  status: result.type === "error" ? ("error" as const) : ("completed" as const),
                  output: result,
                  runDuration: dur,
                }
              : c,
          ),
        );
      }
    },
    [cells],
  );

  const runAll = useCallback(() => {
    cells.forEach((c, i) => {
      setTimeout(() => runCell(c.id), i * 300);
    });
  }, [cells, runCell]);

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-800 bg-[#252526]">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-gray-400">Code Cells</span>
          <span className="text-[9px] text-gray-600 bg-gray-800 px-1.5 py-0.5 rounded">
            {cells.length} cell{cells.length !== 1 ? "s" : ""}
          </span>
        </div>
        <div className="flex gap-1">
          <button
            onClick={addCell}
            className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-gray-400 bg-gray-800 rounded hover:bg-gray-700 transition-colors"
          >
            <Plus size={12} /> Add Cell
          </button>
          <button
            onClick={runAll}
            className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-green-400 bg-gray-800 rounded hover:bg-gray-700 transition-colors"
          >
            <Play size={12} /> Run All
          </button>
        </div>
      </div>

      {/* Cell list */}
      <div className="flex-1 overflow-y-auto">
        {cells.map((cell, i) => (
          <CellItem
            key={cell.id}
            cell={cell}
            index={i}
            onUpdate={(updates) => updateCell(cell.id, updates)}
            onRun={() => runCell(cell.id)}
            onDelete={() => deleteCell(cell.id)}
            onInsertBelow={() => insertCellBelow(i)}
          />
        ))}
      </div>
    </div>
  );
}
