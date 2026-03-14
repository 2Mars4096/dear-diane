import { useState, useEffect, useMemo, useCallback } from "react";
import {
  Play,
  Loader2,
  RefreshCw,
  Circle,
  CheckCircle2,
  XCircle,
  MinusCircle,
  ChevronRight,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import {
  detectTestFramework,
  discoverTests,
  updateTestResults,
  type TestFramework,
  type TestItem,
  type TestResult,
} from "../../lib/testDetector";
import { nativeShell, nativeFs } from "../../lib/electronBridge";
import {
  CoverageToggle,
  setCoverageData,
  setCoverageVisible,
} from "./CoverageOverlay";
import { loadCoverage } from "../../lib/coverageLoader";

export default function TestExplorer() {
  const [framework, setFramework] = useState<TestFramework | null>(null);
  const [tests, setTests] = useState<TestItem[]>([]);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<TestResult[]>([]);
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const openFile = useCodeStore((s) => s.openFile);
  const setActiveFile = useCodeStore((s) => s.setActiveFile);

  useEffect(() => {
    if (pinnedRoots[0]) {
      detectTestFramework(pinnedRoots[0]).then(setFramework);
    }
  }, [pinnedRoots]);

  const refreshTests = useCallback(() => {
    if (framework && pinnedRoots[0]) {
      discoverTests(pinnedRoots[0], framework).then(setTests);
    }
  }, [framework, pinnedRoots]);

  useEffect(() => {
    refreshTests();
  }, [refreshTests]);

  const runTests = useCallback(
    async (scope?: string) => {
      if (!framework || !pinnedRoots[0]) return;
      setRunning(true);

      const args = [...framework.args];
      if (scope) args.push(scope);

      try {
        const result = await nativeShell.run({
          command: framework.command,
          args,
          cwd: pinnedRoots[0],
        });

        const output = result.stdout + result.stderr;
        const parsed = framework.parseOutput(output);
        setResults(parsed);
        setTests((prev) => updateTestResults(prev, parsed));

        // Auto-load coverage data after tests complete
        if (pinnedRoots[0]) {
          loadCoverage(pinnedRoots[0]).then((cov) => {
            if (cov) {
              setCoverageData(cov);
              setCoverageVisible(true);
            }
          }).catch(() => {});
        }
      } catch {
        // parse errors gracefully
      }
      setRunning(false);
    },
    [framework, pinnedRoots],
  );

  const openFileAtLine = useCallback(
    async (filePath: string, line?: number) => {
      const existingFiles = useCodeStore.getState().openFiles;
      const isOpen = existingFiles.some((f) => f.path === filePath);
      if (isOpen) {
        setActiveFile(filePath);
      } else {
        const content = await nativeFs.readFile(filePath);
        if (content !== null) {
          openFile(filePath, content);
        }
      }
      if (line) {
        window.dispatchEvent(
          new CustomEvent("editor:goToLine", {
            detail: { lineNumber: line, column: 1 },
          }),
        );
      }
    },
    [openFile, setActiveFile],
  );

  const stats = useMemo(() => {
    const passed = results.filter((r) => r.status === "passed").length;
    const failed = results.filter((r) => r.status === "failed").length;
    const skipped = results.filter((r) => r.status === "skipped").length;
    return { passed, failed, skipped, total: results.length };
  }, [results]);

  // Listen for external "run all tests" event (Cmd+Shift+T)
  useEffect(() => {
    const handler = () => runTests();
    window.addEventListener("taskRunner:runTest", handler);
    return () => window.removeEventListener("taskRunner:runTest", handler);
  }, [runTests]);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-800">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs font-semibold text-gray-300">
            Test Explorer{framework ? ` (${framework.name})` : ""}
          </span>
          <div className="flex gap-1">
            <button
              onClick={() => runTests()}
              disabled={running || !framework}
              className="p-1 text-green-400 hover:text-green-300 disabled:opacity-50"
              title="Run All Tests"
            >
              {running ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Play size={12} />
              )}
            </button>
            <CoverageToggle />
            <button
              onClick={refreshTests}
              className="p-1 text-gray-400 hover:text-gray-200"
              title="Refresh"
            >
              <RefreshCw size={12} />
            </button>
          </div>
        </div>

        {results.length > 0 && (
          <div className="flex items-center gap-2 text-[10px]">
            <span className="text-green-400">&#x2713; {stats.passed}</span>
            <span className="text-red-400">&#x2717; {stats.failed}</span>
            <span className="text-yellow-400">&#x25CB; {stats.skipped}</span>
            <span className="text-gray-500">{stats.total} total</span>
          </div>
        )}
      </div>

      {/* Test tree */}
      <div className="flex-1 overflow-y-auto">
        {!framework ? (
          <div className="p-4 text-xs text-gray-600 text-center">
            No test framework detected.
            <br />
            <span className="text-gray-700">
              Supports Jest, Vitest, pytest, Go
            </span>
          </div>
        ) : tests.length === 0 ? (
          <div className="p-4 text-xs text-gray-600 text-center">
            No test files found.
          </div>
        ) : (
          tests.map((item) => (
            <TestTreeItem
              key={item.id}
              item={item}
              depth={0}
              onRun={runTests}
              onNavigate={openFileAtLine}
            />
          ))
        )}
      </div>

      {/* Failed test details */}
      {results.filter((r) => r.status === "failed").length > 0 && (
        <div className="border-t border-gray-800 max-h-[200px] overflow-y-auto">
          <div className="px-3 py-1 text-[10px] font-semibold text-red-400">
            Failures
          </div>
          {results
            .filter((r) => r.status === "failed")
            .map((r, i) => (
              <div
                key={i}
                className="px-3 py-1 border-b border-gray-800/30"
              >
                <p className="text-[11px] text-gray-300">{r.name}</p>
                {r.error && (
                  <pre className="text-[10px] text-red-400/70 mt-0.5 whitespace-pre-wrap overflow-x-auto">
                    {r.error.slice(0, 500)}
                  </pre>
                )}
                {r.filePath && (
                  <button
                    onClick={() => openFileAtLine(r.filePath!, r.line)}
                    className="text-[9px] text-blue-400 hover:underline mt-0.5"
                  >
                    {r.filePath}
                    {r.line ? `:${r.line}` : ""}
                  </button>
                )}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

function TestTreeItem({
  item,
  depth,
  onRun,
  onNavigate,
}: {
  item: TestItem;
  depth: number;
  onRun: (scope: string) => void;
  onNavigate: (filePath: string, line?: number) => void;
}) {
  const [expanded, setExpanded] = useState(true);

  const statusIcon: Record<TestItem["status"], React.ReactNode> = {
    unknown: <Circle size={10} className="text-gray-600 shrink-0" />,
    running: (
      <Loader2 size={10} className="text-blue-400 animate-spin shrink-0" />
    ),
    passed: <CheckCircle2 size={10} className="text-green-500 shrink-0" />,
    failed: <XCircle size={10} className="text-red-500 shrink-0" />,
    skipped: <MinusCircle size={10} className="text-yellow-500 shrink-0" />,
  };

  return (
    <div>
      <div
        className="flex items-center gap-1.5 px-2 py-1 hover:bg-gray-800/50 cursor-pointer group"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {item.children.length > 0 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-gray-600 hover:text-gray-400"
          >
            <ChevronRight
              size={10}
              className={`transition-transform ${expanded ? "rotate-90" : ""}`}
            />
          </button>
        )}
        {statusIcon[item.status]}
        <span
          className="text-[11px] text-gray-300 flex-1 truncate"
          onClick={() => onNavigate(item.filePath, item.line)}
        >
          {item.label}
        </span>
        {item.duration !== undefined && (
          <span className="text-[9px] text-gray-600 tabular-nums">
            {item.duration}ms
          </span>
        )}
        <button
          onClick={(e) => {
            e.stopPropagation();
            onRun(item.filePath ?? item.id);
          }}
          className="opacity-0 group-hover:opacity-100 p-0.5 text-gray-500 hover:text-green-400"
          title="Run this test"
        >
          <Play size={10} />
        </button>
      </div>
      {expanded &&
        item.children.map((child) => (
          <TestTreeItem
            key={child.id}
            item={child}
            depth={depth + 1}
            onRun={onRun}
            onNavigate={onNavigate}
          />
        ))}
    </div>
  );
}
