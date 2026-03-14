import { useState, useEffect, useCallback, useRef } from "react";
import { Eye, EyeOff, RefreshCw, Loader2 } from "lucide-react";
import type { editor as monacoEditor } from "monaco-editor";
import { useCodeStore } from "../../store/useCodeStore";
import {
  loadCoverage,
  type CoverageData,
  type FileCoverage,
} from "../../lib/coverageLoader";

interface CoverageOverlayProps {
  visible: boolean;
}

let _coverageData: CoverageData | null = null;
let _coverageVisible = false;
const _listeners = new Set<() => void>();

export function getCoverageData(): CoverageData | null {
  return _coverageData;
}

export function isCoverageVisible(): boolean {
  return _coverageVisible;
}

export function setCoverageData(data: CoverageData | null) {
  _coverageData = data;
  _listeners.forEach((fn) => fn());
}

export function setCoverageVisible(v: boolean) {
  _coverageVisible = v;
  _listeners.forEach((fn) => fn());
}

function useCoverageState() {
  const [, tick] = useState(0);
  useEffect(() => {
    const listener = () => tick((n) => n + 1);
    _listeners.add(listener);
    return () => { _listeners.delete(listener); };
  }, []);
  return { data: _coverageData, visible: _coverageVisible };
}

export function CoverageSummaryBar() {
  const { data, visible } = useCoverageState();
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!pinnedRoots[0]) return;
    setLoading(true);
    try {
      const result = await loadCoverage(pinnedRoots[0]);
      setCoverageData(result);
    } catch {
      setCoverageData(null);
    }
    setLoading(false);
  }, [pinnedRoots]);

  if (!visible || !data) return null;

  const { summary } = data;
  const pct = summary.percentage;
  const barColor = pct >= 80 ? "bg-green-500" : pct >= 50 ? "bg-yellow-500" : "bg-red-500";

  return (
    <div className="flex items-center gap-2 px-3 py-1 bg-[#252526] border-b border-[#3c3c3c] text-[11px] shrink-0">
      <span className="text-gray-400">Coverage:</span>
      <div className="w-24 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full ${barColor} rounded-full transition-all`}
          style={{ width: `${Math.min(100, pct)}%` }}
        />
      </div>
      <span className={pct >= 80 ? "text-green-400" : pct >= 50 ? "text-yellow-400" : "text-red-400"}>
        {pct}%
      </span>
      <span className="text-gray-500">
        ({summary.covered}/{summary.lines} lines)
      </span>
      <button
        onClick={refresh}
        className="p-0.5 text-gray-500 hover:text-gray-300"
        title="Reload coverage"
      >
        {loading ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
      </button>
    </div>
  );
}

export function CoverageToggle({
  onToggle,
}: {
  onToggle?: (visible: boolean) => void;
}) {
  const { visible } = useCoverageState();
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const [loading, setLoading] = useState(false);

  const toggle = useCallback(async () => {
    const newVisible = !_coverageVisible;
    setCoverageVisible(newVisible);
    onToggle?.(newVisible);

    if (newVisible && !_coverageData && pinnedRoots[0]) {
      setLoading(true);
      try {
        const result = await loadCoverage(pinnedRoots[0]);
        setCoverageData(result);
      } catch {
        setCoverageData(null);
      }
      setLoading(false);
    }
  }, [pinnedRoots, onToggle]);

  return (
    <button
      onClick={toggle}
      className={`p-1 rounded transition-colors ${
        visible
          ? "text-green-400 bg-green-400/10"
          : "text-gray-500 hover:text-gray-300"
      }`}
      title={visible ? "Hide coverage" : "Show coverage"}
    >
      {loading ? (
        <Loader2 size={12} className="animate-spin" />
      ) : visible ? (
        <Eye size={12} />
      ) : (
        <EyeOff size={12} />
      )}
    </button>
  );
}

export function useCoverageDecorations(
  editorRef: React.RefObject<monacoEditor.IStandaloneCodeEditor | null>,
) {
  const decorationsRef = useRef<string[]>([]);
  const { data, visible } = useCoverageState();
  const activeFilePath = useCodeStore((s) => s.activeFilePath);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;

    if (!visible || !data || !activeFilePath) {
      if (decorationsRef.current.length > 0) {
        decorationsRef.current = editor.deltaDecorations(
          decorationsRef.current,
          [],
        );
      }
      return;
    }

    // Find coverage data for the active file (try both exact path and basename matching)
    let fileCov: FileCoverage | undefined;
    fileCov = data.files[activeFilePath];
    if (!fileCov) {
      for (const [key, val] of Object.entries(data.files)) {
        if (activeFilePath.endsWith(key) || key.endsWith(activeFilePath.split("/").slice(-2).join("/"))) {
          fileCov = val;
          break;
        }
      }
    }

    if (!fileCov) {
      decorationsRef.current = editor.deltaDecorations(
        decorationsRef.current,
        [],
      );
      return;
    }

    const decorations: monacoEditor.IModelDeltaDecoration[] = [];
    for (const [lineStr, hits] of Object.entries(fileCov.lines)) {
      const lineNum = parseInt(lineStr, 10);
      if (isNaN(lineNum)) continue;

      const isCovered = hits > 0;
      decorations.push({
        range: {
          startLineNumber: lineNum,
          startColumn: 1,
          endLineNumber: lineNum,
          endColumn: 1,
        },
        options: {
          isWholeLine: true,
          className: isCovered
            ? "coverage-line-covered"
            : "coverage-line-uncovered",
          glyphMarginClassName: isCovered
            ? "coverage-glyph-covered"
            : "coverage-glyph-uncovered",
          glyphMarginHoverMessage: {
            value: isCovered ? `Covered (${hits} hit${hits === 1 ? "" : "s"})` : "Not covered",
          },
        },
      });
    }

    decorationsRef.current = editor.deltaDecorations(
      decorationsRef.current,
      decorations,
    );

    return () => {
      if (editor && decorationsRef.current.length > 0) {
        decorationsRef.current = editor.deltaDecorations(
          decorationsRef.current,
          [],
        );
      }
    };
  }, [editorRef, data, visible, activeFilePath]);
}

export default function CoverageOverlay({ visible }: CoverageOverlayProps) {
  if (!visible) return null;
  return <CoverageSummaryBar />;
}
