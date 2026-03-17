import { useState, useEffect, useRef, useCallback } from "react";
import {
  Bug,
  Play,
  Pause,
  Square,
  RotateCcw,
  ArrowRight,
  ArrowDownRight,
  ArrowUpRight,
  ChevronRight,
  ChevronDown,
  X,
  Plus,
  Trash2,
  Link,
  Loader2,
} from "lucide-react";
import { useDebugStore, type DebugVariable } from "../../store/useDebugStore";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeDebug, nativeFs } from "../../lib/electronBridge";

/* ------------------------------------------------------------------ */
/*  Debug event listener                                              */
/* ------------------------------------------------------------------ */

export function useDebugEvents() {
  const setStatus = useDebugStore((s) => s.setStatus);
  const setThreads = useDebugStore((s) => s.setThreads);
  const setCallStack = useDebugStore((s) => s.setCallStack);
  const setActiveThreadId = useDebugStore((s) => s.setActiveThreadId);
  const setActiveFrameId = useDebugStore((s) => s.setActiveFrameId);
  const setVariables = useDebugStore((s) => s.setVariables);
  const setScopes = useDebugStore((s) => s.setScopes);
  const setPausedLocation = useDebugStore((s) => s.setPausedLocation);
  const appendConsoleOutput = useDebugStore((s) => s.appendConsoleOutput);
  const resetSession = useDebugStore((s) => s.resetSession);
  const updateWatchValue = useDebugStore((s) => s.updateWatchValue);

  useEffect(() => {
    const unsub = nativeDebug.onEvent(async (data) => {
      const { event, body } = data;

      switch (event) {
        case "stopped": {
          setStatus("paused");
          const threadId = body.threadId ?? 1;
          setActiveThreadId(threadId);

          try {
            const threadsResult = await nativeDebug.threads();
            if (threadsResult?.threads) setThreads(threadsResult.threads);

            const stResult = await nativeDebug.stackTrace(threadId);
            const frames = stResult?.stackFrames ?? [];
            setCallStack(frames);

            if (frames.length > 0) {
              const topFrame = frames[0];
              setActiveFrameId(topFrame.id);
              setPausedLocation(
                topFrame.source?.path ?? null,
                topFrame.line ?? null,
              );

              const scopesResult = await nativeDebug.scopes(topFrame.id);
              const scopesList = scopesResult?.scopes ?? [];
              setScopes(scopesList);

              if (scopesList.length > 0) {
                const varsResult = await nativeDebug.variables(scopesList[0].variablesReference);
                setVariables(varsResult?.variables ?? []);
              }

              if (topFrame.source?.path) {
                const { openFiles, openFile, setActiveFile } = useCodeStore.getState();
                const isOpen = openFiles.find((f) => f.path === topFrame.source!.path);
                if (isOpen) {
                  setActiveFile(topFrame.source.path!);
                } else {
                  const content = await nativeFs.readFile(topFrame.source.path!);
                  if (content !== null) openFile(topFrame.source.path!, content);
                }
                window.dispatchEvent(
                  new CustomEvent("editor:goToLine", {
                    detail: { lineNumber: topFrame.line, column: topFrame.column ?? 1 },
                  }),
                );
              }

              const watches = useDebugStore.getState().watchExpressions;
              for (const w of watches) {
                try {
                  const result = await nativeDebug.evaluate(w.expression, topFrame.id);
                  updateWatchValue(w.id, result?.result, undefined);
                } catch (err: any) {
                  updateWatchValue(w.id, undefined, err.message);
                }
              }
            }
          } catch {
            // Debug adapter may have disconnected
          }
          break;
        }

        case "continued":
          setStatus("running");
          setPausedLocation(null, null);
          break;

        case "thread":
          nativeDebug.threads().then((r) => {
            if (r?.threads) setThreads(r.threads);
          });
          break;

        case "terminated":
        case "exited":
          resetSession();
          break;

        case "output":
          if (body.output) {
            appendConsoleOutput(body.output);
          }
          break;

        case "breakpoint":
          // Breakpoint verified update handled elsewhere
          break;
      }
    });

    return unsub;
  }, [
    setStatus, setThreads, setCallStack, setActiveThreadId, setActiveFrameId,
    setVariables, setScopes, setPausedLocation, appendConsoleOutput, resetSession,
    updateWatchValue,
  ]);
}

/* ------------------------------------------------------------------ */
/*  Attach to Process modal                                           */
/* ------------------------------------------------------------------ */

interface DebuggableProcess {
  pid: number;
  name: string;
  port: number | null;
  type: string;
}

function AttachToProcessModal({ onClose }: { onClose: () => void }) {
  const [processes, setProcesses] = useState<DebuggableProcess[]>([]);
  const [loading, setLoading] = useState(true);
  const [attaching, setAttaching] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const setStatus = useDebugStore((s) => s.setStatus);
  const appendConsoleOutput = useDebugStore((s) => s.appendConsoleOutput);
  const overlayRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await nativeDebug.listProcesses();
      setProcesses(result ?? []);
    } catch (e: any) {
      setError(e.message ?? "Failed to list processes");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleAttach = async (proc: DebuggableProcess) => {
    setAttaching(proc.pid);
    setError(null);
    try {
      const config: any = {
        name: `Attach to ${proc.name.slice(0, 30)}`,
        type: proc.type,
        request: "attach",
      };
      if (proc.port) {
        config.port = proc.port;
        config.host = "localhost";
      }

      setStatus("running");
      const result = await nativeDebug.attach(config);
      if (result.success) {
        appendConsoleOutput(`Attached to process ${proc.pid} (${proc.name})\n`);
        onClose();
      } else {
        setStatus("idle");
        setError(result.error ?? "Attach failed");
      }
    } catch (e: any) {
      setStatus("idle");
      setError(e.message ?? "Attach failed");
    } finally {
      setAttaching(null);
    }
  };

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div className="w-[480px] max-h-[60vh] flex flex-col rounded-lg border border-gray-700 bg-[#1e1e1e] shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-700 px-4 py-3">
          <div className="flex items-center gap-2">
            <Link size={14} className="text-blue-400" />
            <h2 className="text-sm font-semibold text-white">Attach to Process</h2>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={refresh}
              className="rounded p-1 text-gray-400 hover:bg-gray-700 hover:text-white"
              title="Refresh"
            >
              <RotateCcw size={14} className={loading ? "animate-spin" : ""} />
            </button>
            <button
              onClick={onClose}
              className="rounded p-1 text-gray-400 hover:bg-gray-700 hover:text-white"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {error && (
          <div className="mx-4 mt-3 rounded bg-red-900/40 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-600 [&::-webkit-scrollbar]:w-1.5">
          {loading ? (
            <div className="flex items-center justify-center py-8 text-gray-500">
              <Loader2 size={18} className="animate-spin" />
              <span className="ml-2 text-xs">Scanning for debuggable processes...</span>
            </div>
          ) : processes.length === 0 ? (
            <div className="py-8 text-center text-xs text-gray-500">
              <Bug size={24} className="mx-auto mb-2 text-gray-600" />
              No debuggable processes found.
              <br />
              <span className="text-gray-600">
                Start a process with --inspect (Node) or debugpy (Python)
              </span>
            </div>
          ) : (
            processes.map((proc) => (
              <button
                key={proc.pid}
                onClick={() => handleAttach(proc)}
                disabled={attaching !== null}
                className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-xs hover:bg-gray-700/50 disabled:opacity-40"
              >
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold text-white ${
                  proc.type === "node" ? "bg-green-700" : "bg-blue-700"
                }`}>
                  {proc.type}
                </span>
                <span className="font-mono text-gray-400 shrink-0">PID {proc.pid}</span>
                <span className="min-w-0 truncate text-gray-300">{proc.name}</span>
                {proc.port && (
                  <span className="ml-auto shrink-0 text-[10px] text-gray-500">
                    :{proc.port}
                  </span>
                )}
                {attaching === proc.pid && (
                  <Loader2 size={12} className="ml-1 animate-spin text-blue-400" />
                )}
              </button>
            ))
          )}
        </div>

        <div className="border-t border-gray-700 px-4 py-2 text-[10px] text-gray-600">
          Node.js: listening on port 9229 &middot; Python: debugpy processes
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Control toolbar                                                   */
/* ------------------------------------------------------------------ */

function DebugControls() {
  const status = useDebugStore((s) => s.status);
  const activeThreadId = useDebugStore((s) => s.activeThreadId);
  const launchConfigs = useDebugStore((s) => s.launchConfigs);
  const activeLaunchConfigIndex = useDebugStore((s) => s.activeLaunchConfigIndex);
  const setActiveLaunchConfigIndex = useDebugStore((s) => s.setActiveLaunchConfigIndex);
  const addLaunchConfig = useDebugStore((s) => s.addLaunchConfig);
  const setStatus = useDebugStore((s) => s.setStatus);
  const [showAttachModal, setShowAttachModal] = useState(false);

  const isActive = status === "running" || status === "paused";
  const threadId = activeThreadId ?? 1;

  const handleStartContinue = async () => {
    if (status === "paused") {
      await nativeDebug.continue_(threadId);
    } else if (status === "idle" || status === "stopped") {
      const config = launchConfigs[activeLaunchConfigIndex];
      if (!config) return;
      setStatus("running");
      const result = await nativeDebug.start(config);
      if (!result.success) {
        setStatus("idle");
        useDebugStore.getState().appendConsoleOutput(`Error: ${result.error}\n`);
      }
    }
  };

  const handleStop = () => nativeDebug.stop();
  const handleRestart = () => nativeDebug.restart();
  const handlePause = () => nativeDebug.pause(threadId);
  const handleNext = () => nativeDebug.next(threadId);
  const handleStepIn = () => nativeDebug.stepIn(threadId);
  const handleStepOut = () => nativeDebug.stepOut(threadId);

  const handleAddConfig = () => {
    addLaunchConfig({
      name: `Launch ${launchConfigs.length + 1}`,
      type: "node",
      request: "launch",
      program: "${workspaceFolder}/index.js",
    });
  };

  const btnClass = (enabled: boolean) =>
    `p-1 rounded transition-colors ${
      enabled
        ? "text-gray-300 hover:text-white hover:bg-white/10"
        : "text-gray-600 cursor-not-allowed"
    }`;

  return (
    <div className="px-2 py-1.5 border-b border-[#3c3c3c]">
      <div className="flex items-center gap-1 mb-1.5">
        <select
          value={activeLaunchConfigIndex}
          onChange={(e) => setActiveLaunchConfigIndex(Number(e.target.value))}
          className="flex-1 bg-[#3c3c3c] text-gray-300 text-[11px] rounded px-1.5 py-0.5 border border-[#555] outline-none min-w-0"
          disabled={isActive}
        >
          {launchConfigs.length === 0 && (
            <option value={0}>No configurations</option>
          )}
          {launchConfigs.map((c, i) => (
            <option key={i} value={i}>{c.name}</option>
          ))}
        </select>
        <button
          onClick={handleAddConfig}
          className="p-0.5 text-gray-400 hover:text-white"
          title="Add Configuration"
        >
          <Plus size={14} />
        </button>
        <button
          onClick={() => setShowAttachModal(true)}
          disabled={isActive}
          className={`p-0.5 transition-colors ${isActive ? "text-gray-600 cursor-not-allowed" : "text-gray-400 hover:text-blue-400"}`}
          title="Attach to Process"
        >
          <Link size={14} />
        </button>
      </div>

      <div className="flex items-center gap-0.5">
        <button
          onClick={handleStartContinue}
          className={btnClass(status !== "running")}
          disabled={status === "running"}
          title={status === "paused" ? "Continue (F5)" : "Start Debugging (F5)"}
        >
          <Play size={16} />
        </button>
        <button onClick={handleNext} className={btnClass(status === "paused")} disabled={status !== "paused"} title="Step Over (F10)">
          <ArrowRight size={16} />
        </button>
        <button onClick={handleStepIn} className={btnClass(status === "paused")} disabled={status !== "paused"} title="Step Into (F11)">
          <ArrowDownRight size={16} />
        </button>
        <button onClick={handleStepOut} className={btnClass(status === "paused")} disabled={status !== "paused"} title="Step Out (Shift+F11)">
          <ArrowUpRight size={16} />
        </button>
        <button onClick={handleRestart} className={btnClass(isActive)} disabled={!isActive} title="Restart (Ctrl+Shift+F5)">
          <RotateCcw size={16} />
        </button>
        <button onClick={handlePause} className={btnClass(status === "running")} disabled={status !== "running"} title="Pause (F6)">
          <Pause size={16} />
        </button>
        <button onClick={handleStop} className={btnClass(isActive)} disabled={!isActive} title="Stop (Shift+F5)">
          <Square size={16} />
        </button>
      </div>

      {showAttachModal && (
        <AttachToProcessModal onClose={() => setShowAttachModal(false)} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Collapsible section                                               */
/* ------------------------------------------------------------------ */

function Section({
  title,
  defaultOpen = true,
  badge,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  badge?: number;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border-b border-[#3c3c3c]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1 px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400 hover:text-white hover:bg-white/5"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {title}
        {badge !== undefined && badge > 0 && (
          <span className="ml-auto text-[10px] bg-[#007acc] text-white px-1 rounded-full">
            {badge}
          </span>
        )}
      </button>
      {open && <div className="max-h-[200px] overflow-y-auto">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Variable tree                                                     */
/* ------------------------------------------------------------------ */

function VariableRow({ variable, depth = 0 }: { variable: DebugVariable; depth?: number }) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<DebugVariable[]>([]);
  const [loading, setLoading] = useState(false);
  const hasChildren = variable.variablesReference > 0;

  const handleExpand = async () => {
    if (!hasChildren) return;
    if (expanded) {
      setExpanded(false);
      return;
    }
    setLoading(true);
    try {
      const result = await nativeDebug.variables(variable.variablesReference);
      setChildren(result?.variables ?? []);
    } catch {
      setChildren([]);
    }
    setLoading(false);
    setExpanded(true);
  };

  return (
    <>
      <div
        className="flex items-center gap-1 px-2 py-0.5 hover:bg-white/5 cursor-default text-[12px] font-mono"
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={handleExpand}
      >
        {hasChildren ? (
          <span className="w-3 shrink-0">
            {loading ? (
              <span className="text-gray-500">…</span>
            ) : expanded ? (
              <ChevronDown size={12} className="text-gray-500" />
            ) : (
              <ChevronRight size={12} className="text-gray-500" />
            )}
          </span>
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <span className="text-blue-300 shrink-0">{variable.name}</span>
        <span className="text-gray-500 mx-0.5">=</span>
        <span className="text-gray-200 truncate">{variable.value}</span>
        {variable.type && (
          <span className="text-gray-600 text-[10px] ml-1 shrink-0">{variable.type}</span>
        )}
      </div>
      {expanded &&
        children.map((child) => (
          <VariableRow key={child.name} variable={child} depth={depth + 1} />
        ))}
    </>
  );
}

function VariablesSection() {
  const variables = useDebugStore((s) => s.variables);
  const status = useDebugStore((s) => s.status);

  if (status !== "paused" || variables.length === 0) {
    return (
      <Section title="Variables" badge={variables.length}>
        <div className="px-3 py-2 text-[11px] text-gray-500 italic">
          {status !== "paused" ? "Not paused" : "No variables"}
        </div>
      </Section>
    );
  }

  return (
    <Section title="Variables" badge={variables.length}>
      {variables.map((v) => (
        <VariableRow key={v.name} variable={v} />
      ))}
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/*  Watch section                                                     */
/* ------------------------------------------------------------------ */

function WatchSection() {
  const watches = useDebugStore((s) => s.watchExpressions);
  const addWatchExpression = useDebugStore((s) => s.addWatchExpression);
  const removeWatchExpression = useDebugStore((s) => s.removeWatchExpression);
  const status = useDebugStore((s) => s.status);
  const activeFrameId = useDebugStore((s) => s.activeFrameId);
  const updateWatchValue = useDebugStore((s) => s.updateWatchValue);
  const [input, setInput] = useState("");

  const handleAdd = async () => {
    const expr = input.trim();
    if (!expr) return;
    addWatchExpression(expr);
    setInput("");
    if (status === "paused" && activeFrameId !== null) {
      try {
        const result = await nativeDebug.evaluate(expr, activeFrameId);
        const ws = useDebugStore.getState().watchExpressions;
        const added = ws[ws.length - 1];
        if (added) updateWatchValue(added.id, result?.result, undefined);
      } catch (err: any) {
        const ws = useDebugStore.getState().watchExpressions;
        const added = ws[ws.length - 1];
        if (added) updateWatchValue(added.id, undefined, err.message);
      }
    }
  };

  return (
    <Section title="Watch" badge={watches.length}>
      {watches.map((w) => (
        <div
          key={w.id}
          className="flex items-center gap-1 px-2 py-0.5 hover:bg-white/5 text-[12px] font-mono group"
        >
          <span className="text-blue-300">{w.expression}</span>
          <span className="text-gray-500 mx-0.5">=</span>
          <span className={`truncate ${w.error ? "text-red-400" : "text-gray-200"}`}>
            {w.error ?? w.value ?? "not available"}
          </span>
          <button
            onClick={() => removeWatchExpression(w.id)}
            className="ml-auto opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400"
          >
            <X size={12} />
          </button>
        </div>
      ))}
      <div className="flex items-center px-2 py-1 gap-1">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="Add expression…"
          className="flex-1 bg-transparent text-[11px] text-gray-300 outline-none placeholder-gray-600 border-b border-transparent focus:border-[#007acc]"
        />
        <button onClick={handleAdd} className="text-gray-500 hover:text-white">
          <Plus size={12} />
        </button>
      </div>
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/*  Call stack section                                                 */
/* ------------------------------------------------------------------ */

function CallStackSection() {
  const callStack = useDebugStore((s) => s.callStack);
  const activeFrameId = useDebugStore((s) => s.activeFrameId);
  const setActiveFrameId = useDebugStore((s) => s.setActiveFrameId);
  const setVariables = useDebugStore((s) => s.setVariables);
  const setScopes = useDebugStore((s) => s.setScopes);
  const setPausedLocation = useDebugStore((s) => s.setPausedLocation);

  const handleFrameClick = async (frame: (typeof callStack)[0]) => {
    setActiveFrameId(frame.id);
    setPausedLocation(frame.source?.path ?? null, frame.line ?? null);

    try {
      const scopesResult = await nativeDebug.scopes(frame.id);
      const scopesList = scopesResult?.scopes ?? [];
      setScopes(scopesList);

      if (scopesList.length > 0) {
        const varsResult = await nativeDebug.variables(scopesList[0].variablesReference);
        setVariables(varsResult?.variables ?? []);
      }
    } catch { /* ignore */ }

    if (frame.source?.path) {
      const { openFiles, openFile, setActiveFile } = useCodeStore.getState();
      const isOpen = openFiles.find((f) => f.path === frame.source!.path);
      if (isOpen) {
        setActiveFile(frame.source.path!);
      } else {
        const content = await nativeFs.readFile(frame.source.path!);
        if (content !== null) openFile(frame.source.path!, content);
      }
      window.dispatchEvent(
        new CustomEvent("editor:goToLine", {
          detail: { lineNumber: frame.line, column: frame.column ?? 1 },
        }),
      );
    }
  };

  return (
    <Section title="Call Stack" badge={callStack.length}>
      {callStack.length === 0 ? (
        <div className="px-3 py-2 text-[11px] text-gray-500 italic">No call stack</div>
      ) : (
        callStack.map((frame) => (
          <button
            key={frame.id}
            onClick={() => handleFrameClick(frame)}
            className={`w-full text-left flex items-center gap-1.5 px-2 py-0.5 text-[11px] hover:bg-white/5 ${
              frame.id === activeFrameId ? "bg-[#007acc33] text-white" : "text-gray-300"
            }`}
          >
            <span className="truncate font-medium">{frame.name}</span>
            {frame.source?.name && (
              <span className="text-gray-500 shrink-0 text-[10px]">
                {frame.source.name}:{frame.line}
              </span>
            )}
          </button>
        ))
      )}
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/*  Breakpoints section                                               */
/* ------------------------------------------------------------------ */

function BreakpointConditionEditor({
  filePath,
  line,
  currentCondition,
  currentLogMessage,
  editField,
  onClose,
}: {
  filePath: string;
  line: number;
  currentCondition?: string;
  currentLogMessage?: string;
  editField: "condition" | "logMessage";
  onClose: () => void;
}) {
  const setBreakpointCondition = useDebugStore((s) => s.setBreakpointCondition);
  const setBreakpointLogMessage = useDebugStore((s) => s.setBreakpointLogMessage);
  const [value, setValue] = useState(
    editField === "condition" ? (currentCondition ?? "") : (currentLogMessage ?? ""),
  );
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = () => {
    if (editField === "condition") {
      setBreakpointCondition(filePath, line, value);
    } else {
      setBreakpointLogMessage(filePath, line, value);
    }
    onClose();
  };

  return (
    <div className="flex items-center gap-1 px-2 py-1 bg-[#2a2a2a] border border-[#007acc] rounded mx-1 my-0.5">
      <span className="text-[10px] text-gray-500 shrink-0 uppercase">
        {editField === "condition" ? "if" : "log"}
      </span>
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") handleSubmit();
          if (e.key === "Escape") onClose();
        }}
        onBlur={handleSubmit}
        placeholder={
          editField === "condition"
            ? "Expression (e.g. x > 5)"
            : "Log message (e.g. value is {x})"
        }
        className="flex-1 bg-transparent text-[11px] text-gray-200 outline-none placeholder-gray-600 font-mono min-w-0"
      />
    </div>
  );
}

function BreakpointsSection() {
  const breakpoints = useDebugStore((s) => s.breakpoints);
  const removeBreakpoint = useDebugStore((s) => s.removeBreakpoint);
  const allBps = Object.entries(breakpoints).flatMap(([filePath, bps]) =>
    bps.map((bp) => ({ filePath, ...bp })),
  );

  const [ctxMenu, setCtxMenu] = useState<{
    x: number;
    y: number;
    filePath: string;
    line: number;
  } | null>(null);
  const [editingBp, setEditingBp] = useState<{
    filePath: string;
    line: number;
    field: "condition" | "logMessage";
  } | null>(null);
  const ctxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ctxMenu) return;
    const handler = (e: MouseEvent) => {
      if (ctxRef.current && !ctxRef.current.contains(e.target as Node)) {
        setCtxMenu(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [ctxMenu]);

  const handleClick = (filePath: string, line: number) => {
    const { openFiles, setActiveFile } = useCodeStore.getState();
    const isOpen = openFiles.find((f) => f.path === filePath);
    if (isOpen) {
      setActiveFile(filePath);
    }
    window.dispatchEvent(
      new CustomEvent("editor:goToLine", { detail: { lineNumber: line, column: 1 } }),
    );
  };

  return (
    <Section title="Breakpoints" badge={allBps.length}>
      {allBps.length === 0 ? (
        <div className="px-3 py-2 text-[11px] text-gray-500 italic">No breakpoints</div>
      ) : (
        allBps.map((bp) => {
          const fileName = bp.filePath.split("/").pop();
          const isEditing =
            editingBp?.filePath === bp.filePath && editingBp?.line === bp.line;
          return (
            <div key={`${bp.filePath}:${bp.line}`}>
              <div
                className="flex items-center gap-1.5 px-2 py-0.5 text-[11px] hover:bg-white/5 cursor-pointer group"
                onClick={() => handleClick(bp.filePath, bp.line)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setCtxMenu({ x: e.clientX, y: e.clientY, filePath: bp.filePath, line: bp.line });
                }}
              >
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${
                    bp.logMessage
                      ? "bg-blue-400"
                      : bp.condition
                        ? "bg-yellow-400"
                        : "bg-red-500"
                  }`}
                />
                <span className="text-gray-300 truncate">{fileName}</span>
                <span className="text-gray-500">:{bp.line}</span>
                {bp.condition && (
                  <span className="text-yellow-500 text-[10px] truncate">({bp.condition})</span>
                )}
                {bp.logMessage && (
                  <span className="text-blue-400 text-[10px] truncate">[{bp.logMessage}]</span>
                )}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeBreakpoint(bp.filePath, bp.line);
                  }}
                  className="ml-auto opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400"
                >
                  <X size={12} />
                </button>
              </div>
              {isEditing && (
                <BreakpointConditionEditor
                  filePath={bp.filePath}
                  line={bp.line}
                  currentCondition={bp.condition}
                  currentLogMessage={bp.logMessage}
                  editField={editingBp.field}
                  onClose={() => setEditingBp(null)}
                />
              )}
            </div>
          );
        })
      )}

      {ctxMenu && (
        <div
          ref={ctxRef}
          className="fixed z-50 min-w-40 rounded-md border border-[#3c3c3c] bg-[#252526] py-1 shadow-lg text-xs text-gray-300"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
        >
          <button
            className="block w-full px-3 py-1.5 text-left hover:bg-[#094771] hover:text-white"
            onClick={() => {
              setEditingBp({ filePath: ctxMenu.filePath, line: ctxMenu.line, field: "condition" });
              setCtxMenu(null);
            }}
          >
            Edit Condition
          </button>
          <button
            className="block w-full px-3 py-1.5 text-left hover:bg-[#094771] hover:text-white"
            onClick={() => {
              setEditingBp({ filePath: ctxMenu.filePath, line: ctxMenu.line, field: "logMessage" });
              setCtxMenu(null);
            }}
          >
            Edit Log Message
          </button>
          <div className="my-1 border-t border-[#3c3c3c]" />
          <button
            className="block w-full px-3 py-1.5 text-left hover:bg-[#094771] hover:text-white text-red-400"
            onClick={() => {
              removeBreakpoint(ctxMenu.filePath, ctxMenu.line);
              setCtxMenu(null);
            }}
          >
            Remove Breakpoint
          </button>
        </div>
      )}
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/*  Debug console                                                     */
/* ------------------------------------------------------------------ */

export function DebugConsole() {
  const output = useDebugStore((s) => s.debugConsoleOutput);
  const status = useDebugStore((s) => s.status);
  const activeFrameId = useDebugStore((s) => s.activeFrameId);
  const appendConsoleOutput = useDebugStore((s) => s.appendConsoleOutput);
  const clearConsoleOutput = useDebugStore((s) => s.clearConsoleOutput);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [output.length]);

  const handleEval = async () => {
    const expr = input.trim();
    if (!expr) return;
    appendConsoleOutput(`> ${expr}\n`);
    setInput("");

    if (status !== "paused") {
      appendConsoleOutput("Cannot evaluate: not paused\n");
      return;
    }

    try {
      const result = await nativeDebug.evaluate(expr, activeFrameId ?? undefined);
      appendConsoleOutput(`${result?.result ?? "undefined"}\n`);
    } catch (err: any) {
      appendConsoleOutput(`Error: ${err.message}\n`);
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="flex items-center justify-between px-2 py-0.5 border-b border-[#3c3c3c] shrink-0">
        <span className="text-[11px] text-gray-400 font-medium">Debug Console</span>
        <button onClick={clearConsoleOutput} className="text-gray-500 hover:text-white" title="Clear">
          <Trash2 size={12} />
        </button>
      </div>
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-2 py-1 font-mono text-[12px]">
        {output.map((line, i) => (
          <div
            key={i}
            className={`whitespace-pre-wrap break-all ${
              line.startsWith("> ") ? "text-blue-300" : line.startsWith("Error") ? "text-red-400" : "text-gray-300"
            }`}
          >
            {line}
          </div>
        ))}
      </div>
      <div className="flex items-center px-2 py-1 border-t border-[#3c3c3c] shrink-0">
        <span className="text-[12px] text-gray-500 mr-1">{">"}</span>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleEval()}
          placeholder={status === "paused" ? "Evaluate expression…" : "Start debugging to evaluate"}
          disabled={status !== "paused"}
          className="flex-1 bg-transparent text-[12px] text-gray-300 outline-none placeholder-gray-600 font-mono disabled:opacity-50"
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main panel                                                        */
/* ------------------------------------------------------------------ */

export default function DebugPanel() {
  const status = useDebugStore((s) => s.status);

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e] text-gray-300 select-none">
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-[#3c3c3c] shrink-0">
        <Bug size={14} className="text-orange-400" />
        <span className="text-[12px] font-semibold uppercase tracking-wide">Debug</span>
        <span
          className={`ml-auto text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
            status === "running"
              ? "bg-green-500/20 text-green-400"
              : status === "paused"
                ? "bg-yellow-500/20 text-yellow-400"
                : "bg-gray-500/20 text-gray-500"
          }`}
        >
          {status}
        </span>
      </div>

      <DebugControls />

      <div className="flex-1 min-h-0 overflow-y-auto">
        <VariablesSection />
        <WatchSection />
        <CallStackSection />
        <BreakpointsSection />
      </div>
    </div>
  );
}
