import { useState, useEffect, useRef } from "react";
import {
  Download,
  Eye,
  CheckCircle2,
  Loader2,
  ChevronRight,
  X,
} from "lucide-react";
import { nativeShell } from "../../lib/electronBridge";

// ---------------------------------------------------------------------------
// 1. ChartBlock — renders Chart.js charts from parsed spec
// ---------------------------------------------------------------------------

const DARK_LABEL = "#9ca3af";
const DARK_GRID = "#374151";

export interface ChartSpec {
  type: string;
  data: Record<string, unknown>;
  options?: Record<string, unknown>;
}

export function ChartBlock({ spec }: { spec: ChartSpec }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<{ destroy(): void } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!canvasRef.current) return;
    let cancelled = false;

    import("chart.js/auto")
      .then(({ default: Chart }) => {
        if (cancelled || !canvasRef.current) return;
        chartRef.current?.destroy();

        const opts = (spec.options ?? {}) as Record<string, any>;
        chartRef.current = new Chart(canvasRef.current, {
          type: spec.type as any,
          data: spec.data as any,
          options: {
            responsive: true,
            maintainAspectRatio: true,
            ...opts,
            plugins: {
              ...opts.plugins,
              legend: {
                labels: { color: DARK_LABEL },
                ...opts.plugins?.legend,
              },
            },
            scales: {
              x: {
                ticks: { color: DARK_LABEL },
                grid: { color: DARK_GRID },
              },
              y: {
                ticks: { color: DARK_LABEL },
                grid: { color: DARK_GRID },
              },
              ...opts.scales,
            },
          },
        });
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load chart library");
      });

    return () => {
      cancelled = true;
      chartRef.current?.destroy();
    };
  }, [spec]);

  if (error) {
    return (
      <div className="my-3 p-3 bg-red-900/20 rounded-lg border border-red-700/50 text-xs text-red-400">
        {error}
      </div>
    );
  }

  return (
    <div className="my-3 p-3 bg-gray-800/50 rounded-lg border border-gray-700/50">
      <canvas ref={canvasRef} className="max-h-[300px]" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. FileCard — downloadable file / attachment card
// ---------------------------------------------------------------------------

const FILE_ICONS: Record<string, string> = {
  pdf: "📄", doc: "📝", docx: "📝", xlsx: "📊", csv: "📊",
  png: "🖼️", jpg: "🖼️", jpeg: "🖼️", svg: "🖼️", gif: "🖼️",
  zip: "📦", tar: "📦", gz: "📦",
  py: "🐍", ts: "📘", js: "📒", json: "📋",
};

export interface FileCardProps {
  filename: string;
  size?: string;
  type?: string;
  path?: string;
  onDownload?: () => void;
  onPreview?: () => void;
}

export function FileCard({
  filename,
  size,
  type,
  path,
  onDownload,
  onPreview,
}: FileCardProps) {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  const icon = FILE_ICONS[ext] ?? "📎";

  const handleDownload = () => {
    if (onDownload) return onDownload();
    if (path) nativeShell.openPath(path);
  };

  return (
    <div className="inline-flex items-center gap-2 my-1 px-3 py-2 bg-gray-800/50 border border-gray-700/50 rounded-lg hover:border-gray-600/50 transition-colors">
      <span className="text-lg">{icon}</span>
      <div className="min-w-0">
        <p className="text-sm text-gray-200 truncate">{filename}</p>
        {(size || type) && (
          <p className="text-[10px] text-gray-500">
            {[type, size].filter(Boolean).join(" · ")}
          </p>
        )}
      </div>
      {path && (
        <button
          onClick={handleDownload}
          className="ml-2 p-1 text-gray-400 hover:text-blue-400 rounded transition-colors"
          title="Open file"
        >
          <Download size={14} />
        </button>
      )}
      {onPreview && (
        <button
          onClick={onPreview}
          className="p-1 text-gray-400 hover:text-blue-400 rounded transition-colors"
          title="Preview"
        >
          <Eye size={14} />
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3. InlineDiff — side-by-side diff with accept / reject
// ---------------------------------------------------------------------------

interface DiffLine {
  type: "added" | "removed" | "context";
  content: string;
}

function computeDiffLines(original: string, modified: string): DiffLine[] {
  const origLines = original.split("\n");
  const modLines = modified.split("\n");
  const result: DiffLine[] = [];

  // LCS-based diff for ordered line matching
  const m = origLines.length;
  const n = modLines.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array(n + 1).fill(0),
  );
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] =
        origLines[i - 1] === modLines[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);

  let i = m,
    j = n;
  const ops: DiffLine[] = [];
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && origLines[i - 1] === modLines[j - 1]) {
      ops.push({ type: "context", content: origLines[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ type: "added", content: modLines[j - 1] });
      j--;
    } else {
      ops.push({ type: "removed", content: origLines[i - 1] });
      i--;
    }
  }
  ops.reverse();
  return ops.length ? ops : result;
}

export interface InlineDiffProps {
  original: string;
  modified: string;
  filename: string;
  onAccept?: () => void;
  onReject?: () => void;
}

export function InlineDiff({
  original,
  modified,
  filename,
  onAccept,
  onReject,
}: InlineDiffProps) {
  const lines = computeDiffLines(original, modified);

  return (
    <div className="my-3 rounded-lg border border-gray-700/50 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-800 border-b border-gray-700/50">
        <span className="text-xs text-gray-400">{filename}</span>
        <div className="flex gap-1.5">
          {onAccept && (
            <button
              onClick={onAccept}
              className="text-[10px] px-2 py-0.5 bg-green-600/20 text-green-400 rounded hover:bg-green-600/30 transition-colors"
            >
              Accept
            </button>
          )}
          {onReject && (
            <button
              onClick={onReject}
              className="text-[10px] px-2 py-0.5 bg-red-600/20 text-red-400 rounded hover:bg-red-600/30 transition-colors"
            >
              Reject
            </button>
          )}
        </div>
      </div>
      <div className="text-xs font-mono overflow-x-auto max-h-[400px] overflow-y-auto">
        {lines.map((line, idx) => (
          <div
            key={idx}
            className={`px-3 py-0.5 ${
              line.type === "added"
                ? "bg-green-900/20 text-green-300"
                : line.type === "removed"
                  ? "bg-red-900/20 text-red-300"
                  : "text-gray-400"
            }`}
          >
            <span className="inline-block w-4 text-gray-600 select-none">
              {line.type === "added" ? "+" : line.type === "removed" ? "-" : " "}
            </span>
            {line.content}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 4. ProgressCard — multi-step task progress
// ---------------------------------------------------------------------------

export interface ProgressStep {
  label: string;
  status: "done" | "active" | "pending";
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

export function ProgressCard({
  steps,
  elapsed,
}: {
  steps: ProgressStep[];
  elapsed?: number;
}) {
  return (
    <div className="my-3 p-3 bg-gray-800/30 rounded-lg border border-gray-700/50">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-gray-300">
          Working on task…
        </span>
        {elapsed != null && (
          <span className="text-[10px] text-gray-500">
            {formatElapsed(elapsed)}
          </span>
        )}
      </div>
      <div className="space-y-1.5">
        {steps.map((step, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            {step.status === "done" ? (
              <CheckCircle2
                size={14}
                className="text-green-500 shrink-0"
              />
            ) : step.status === "active" ? (
              <Loader2
                size={14}
                className="text-blue-400 animate-spin shrink-0"
              />
            ) : (
              <div className="w-3.5 h-3.5 rounded-full border border-gray-600 shrink-0" />
            )}
            <span
              className={
                step.status === "done"
                  ? "text-gray-500"
                  : step.status === "active"
                    ? "text-blue-300"
                    : "text-gray-500"
              }
            >
              {step.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 5. CollapsibleSection + TabbedOutput — structured responses
// ---------------------------------------------------------------------------

export function CollapsibleSection({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="my-2 border border-gray-700/50 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-gray-800/50 hover:bg-gray-800 text-xs font-medium text-gray-300 transition-colors"
      >
        <ChevronRight
          size={12}
          className={`transition-transform ${open ? "rotate-90" : ""}`}
        />
        {title}
      </button>
      {open && <div className="px-3 py-2 text-sm">{children}</div>}
    </div>
  );
}

export function TabbedOutput({
  tabs,
}: {
  tabs: Array<{ label: string; content: React.ReactNode }>;
}) {
  const [active, setActive] = useState(0);
  return (
    <div className="my-2 border border-gray-700/50 rounded-lg overflow-hidden">
      <div className="flex border-b border-gray-700/50 bg-gray-800/30">
        {tabs.map((tab, i) => (
          <button
            key={i}
            onClick={() => setActive(i)}
            className={`px-3 py-1.5 text-xs transition-colors ${
              active === i
                ? "text-blue-400 border-b border-blue-400"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="p-3">{tabs[active]?.content}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 6. InlineImage — image display with lightbox
// ---------------------------------------------------------------------------

export function InlineImage({
  src,
  alt,
}: {
  src: string;
  alt?: string;
}) {
  const [lightbox, setLightbox] = useState(false);
  const [loadError, setLoadError] = useState(false);

  if (loadError) return null;

  return (
    <>
      <img
        src={src}
        alt={alt ?? ""}
        className="my-2 max-w-full max-h-[400px] rounded-lg border border-gray-700/50 cursor-pointer hover:opacity-90 transition-opacity"
        onClick={() => setLightbox(true)}
        onError={() => setLoadError(true)}
      />
      {lightbox && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/80"
          onClick={() => setLightbox(false)}
        >
          <button
            className="absolute top-4 right-4 text-white/60 hover:text-white transition-colors"
            onClick={() => setLightbox(false)}
          >
            <X size={24} />
          </button>
          <img
            src={src}
            alt={alt ?? ""}
            className="max-w-[90vw] max-h-[90vh] rounded-lg"
          />
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// 7. CitationCard — academic reference card
// ---------------------------------------------------------------------------

export interface CitationData {
  author: string;
  year: string;
  title: string;
  url?: string;
  abstract?: string;
}

export function CitationCard({
  author,
  year,
  title,
  url,
  abstract,
}: CitationData) {
  const [showAbstract, setShowAbstract] = useState(false);

  return (
    <div className="my-1.5 px-3 py-2 bg-gray-800/30 border-l-2 border-blue-500/50 rounded-r-lg hover:bg-gray-800/50 transition-colors">
      <p className="text-sm text-gray-200 font-medium">
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-blue-400 transition-colors"
          >
            {title}
          </a>
        ) : (
          title
        )}
      </p>
      <p className="text-[11px] text-gray-500 mt-0.5">
        {author} ({year})
      </p>
      {abstract && (
        <button
          onClick={() => setShowAbstract((v) => !v)}
          className="text-[10px] text-blue-500 hover:text-blue-400 mt-1 transition-colors"
        >
          {showAbstract ? "Hide abstract" : "Show abstract"}
        </button>
      )}
      {showAbstract && abstract && (
        <p className="text-xs text-gray-400 mt-1 leading-relaxed">
          {abstract}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Parsing helpers — extract rich blocks from raw markdown
// ---------------------------------------------------------------------------

export interface RichBlock {
  type:
    | "markdown"
    | "chart"
    | "file"
    | "diff"
    | "progress"
    | "image"
    | "citation";
  raw: string;
  data?: any;
}

const CHART_FENCE_RE =
  /```(?:chart|json:chart)\s*\n([\s\S]*?)```/g;

const FILE_REF_RE =
  /\[file:([^\]]+?)(?:\|([^\]]*))?\]/g;

const DIFF_FENCE_RE =
  /```diff:(\S+)\s*\n([\s\S]*?)```/g;

const PROGRESS_RE =
  /\[progress:([\s\S]*?)\]/g;

const CITATION_RE =
  /\[@cite\s+([\s\S]*?)\]/g;

const IMAGE_DATA_RE =
  /!\[([^\]]*)\]\((data:image\/[^)]+)\)/g;

export function parseRichBlocks(raw: string): RichBlock[] {
  const blocks: RichBlock[] = [];
  let source = raw;

  interface Match {
    index: number;
    length: number;
    block: RichBlock;
  }

  const matches: Match[] = [];

  // Chart blocks
  let m: RegExpExecArray | null;
  CHART_FENCE_RE.lastIndex = 0;
  while ((m = CHART_FENCE_RE.exec(source)) !== null) {
    try {
      const spec = JSON.parse(m[1]);
      matches.push({
        index: m.index,
        length: m[0].length,
        block: { type: "chart", raw: m[0], data: spec },
      });
    } catch {
      // invalid JSON — leave as regular code block
    }
  }

  // File references
  FILE_REF_RE.lastIndex = 0;
  while ((m = FILE_REF_RE.exec(source)) !== null) {
    const path = m[1].trim();
    const filename = path.split("/").pop() ?? path;
    const meta = m[2]?.trim();
    matches.push({
      index: m.index,
      length: m[0].length,
      block: {
        type: "file",
        raw: m[0],
        data: { filename, path, size: meta },
      },
    });
  }

  // Diff blocks: ```diff:filename\n...\n```
  DIFF_FENCE_RE.lastIndex = 0;
  while ((m = DIFF_FENCE_RE.exec(source)) !== null) {
    const filename = m[1];
    const content = m[2];
    const parts = content.split("\n---\n");
    if (parts.length === 2) {
      matches.push({
        index: m.index,
        length: m[0].length,
        block: {
          type: "diff",
          raw: m[0],
          data: {
            filename,
            original: parts[0],
            modified: parts[1],
          },
        },
      });
    }
  }

  // Progress blocks
  PROGRESS_RE.lastIndex = 0;
  while ((m = PROGRESS_RE.exec(source)) !== null) {
    try {
      const data = JSON.parse(m[1]);
      matches.push({
        index: m.index,
        length: m[0].length,
        block: { type: "progress", raw: m[0], data },
      });
    } catch {
      // not valid JSON
    }
  }

  // Citations
  CITATION_RE.lastIndex = 0;
  while ((m = CITATION_RE.exec(source)) !== null) {
    try {
      const data = JSON.parse(m[1]);
      matches.push({
        index: m.index,
        length: m[0].length,
        block: { type: "citation", raw: m[0], data },
      });
    } catch {
      // not valid JSON
    }
  }

  // Base64 images (non-URL images handled separately since URL images go through markdown)
  IMAGE_DATA_RE.lastIndex = 0;
  while ((m = IMAGE_DATA_RE.exec(source)) !== null) {
    matches.push({
      index: m.index,
      length: m[0].length,
      block: {
        type: "image",
        raw: m[0],
        data: { src: m[2], alt: m[1] },
      },
    });
  }

  // Sort matches by position, then build interleaved block list
  matches.sort((a, b) => a.index - b.index);

  let cursor = 0;
  for (const match of matches) {
    if (match.index < cursor) continue; // overlapping match, skip
    if (match.index > cursor) {
      blocks.push({
        type: "markdown",
        raw: source.slice(cursor, match.index),
      });
    }
    blocks.push(match.block);
    cursor = match.index + match.length;
  }
  if (cursor < source.length) {
    blocks.push({ type: "markdown", raw: source.slice(cursor) });
  }

  return blocks.length ? blocks : [{ type: "markdown", raw: source }];
}
