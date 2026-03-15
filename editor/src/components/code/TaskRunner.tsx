import { useState, useEffect, useMemo, useCallback } from "react";
import {
  Play,
  Loader2,
  RefreshCw,
  Plus,
  ChevronRight,
  ChevronDown,
  X,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { detectTasks, type DetectedTask } from "../../lib/taskDetector";
import { nativeShell } from "../../lib/electronBridge";
import { parseTaskOutput } from "../../lib/problemMatcher";
import {
  pushExternalDiagnostic,
  clearExternalDiagnostics,
} from "./ProblemsPanel";

interface TaskRun {
  termId: string;
  output: string;
  exitCode: number | null;
}

export default function TaskRunner() {
  const [tasks, setTasks] = useState<DetectedTask[]>([]);
  const [customTasks, setCustomTasks] = useState<DetectedTask[]>([]);
  const [runningTasks, setRunningTasks] = useState<Map<string, TaskRun>>(
    new Map(),
  );
  const [showAddTask, setShowAddTask] = useState(false);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    new Set(),
  );
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);

  const refreshTasks = useCallback(() => {
    if (pinnedRoots.length > 0) {
      detectTasks(pinnedRoots[0]).then(setTasks);
    }
  }, [pinnedRoots]);

  useEffect(() => {
    refreshTasks();
  }, [refreshTasks]);

  const runTask = useCallback(
    async (task: DetectedTask) => {
      if (runningTasks.has(task.id)) return;

      const cmd = `${task.command} ${task.args.join(" ")}`;

      window.dispatchEvent(
        new CustomEvent("chat:shellCommand", {
          detail: { command: cmd, cwd: task.cwd },
        }),
      );

      setRunningTasks((prev) => {
        const next = new Map(prev);
        next.set(task.id, { termId: "", output: "", exitCode: null });
        return next;
      });

      try {
        const result = await nativeShell.run({
          command: task.command,
          args: task.args,
          cwd: task.cwd,
        });
        const output = result.stdout + result.stderr;
        setRunningTasks((prev) => {
          const next = new Map(prev);
          next.set(task.id, {
            termId: "",
            output,
            exitCode: result.code,
          });
          return next;
        });

        clearExternalDiagnostics(`task:${task.id}`);
        const problems = parseTaskOutput(output, `task:${task.label}`);
        for (const p of problems) {
          pushExternalDiagnostic({
            filePath: p.file,
            line: p.line,
            column: p.column ?? 1,
            message: p.message,
            severity: p.severity,
            source: `task:${task.label}`,
          });
        }
      } catch {
        setRunningTasks((prev) => {
          const next = new Map(prev);
          next.delete(task.id);
          return next;
        });
      }
    },
    [runningTasks],
  );

  const stopTask = useCallback((taskId: string) => {
    setRunningTasks((prev) => {
      const next = new Map(prev);
      next.delete(taskId);
      return next;
    });
  }, []);

  const toggleGroup = (group: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  };

  const groupedTasks = useMemo(() => {
    const all = [...tasks, ...customTasks];
    const groups: Record<string, DetectedTask[]> = {};
    for (const task of all) {
      const group = task.group ?? "other";
      if (!groups[group]) groups[group] = [];
      groups[group].push(task);
    }
    return groups;
  }, [tasks, customTasks]);

  const GROUP_ORDER = ["build", "test", "lint", "start", "clean", "other"];

  const sortedGroups = useMemo(() => {
    return Object.entries(groupedTasks).sort(
      ([a], [b]) =>
        (GROUP_ORDER.indexOf(a) === -1 ? 99 : GROUP_ORDER.indexOf(a)) -
        (GROUP_ORDER.indexOf(b) === -1 ? 99 : GROUP_ORDER.indexOf(b)),
    );
  }, [groupedTasks]);

  const addCustomTask = (label: string, command: string) => {
    const parts = command.trim().split(/\s+/);
    const cmd = parts[0];
    const args = parts.slice(1);
    const task: DetectedTask = {
      id: `custom:${Date.now()}`,
      label,
      command: cmd,
      args,
      cwd: pinnedRoots[0] ?? ".",
      source: "custom",
      group: "other",
    };
    setCustomTasks((prev) => [...prev, task]);
    setShowAddTask(false);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-300">Tasks</span>
        <div className="flex gap-1">
          <button
            onClick={refreshTasks}
            className="p-1 text-gray-400 hover:text-gray-200"
            title="Refresh tasks"
          >
            <RefreshCw size={12} />
          </button>
          <button
            onClick={() => setShowAddTask(true)}
            className="p-1 text-gray-400 hover:text-green-400"
            title="Add custom task"
          >
            <Plus size={12} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {sortedGroups.map(([group, groupTasks]) => (
          <div key={group}>
            <button
              onClick={() => toggleGroup(group)}
              className="w-full flex items-center gap-1 px-3 py-1 text-[9px] font-semibold text-gray-500 uppercase tracking-wider hover:bg-gray-800/30"
            >
              {collapsedGroups.has(group) ? (
                <ChevronRight size={10} />
              ) : (
                <ChevronDown size={10} />
              )}
              {group}
              <span className="ml-auto text-gray-600 tabular-nums">
                {groupTasks.length}
              </span>
            </button>
            {!collapsedGroups.has(group) &&
              groupTasks.map((task) => (
                <TaskItem
                  key={task.id}
                  task={task}
                  running={
                    runningTasks.has(task.id) &&
                    runningTasks.get(task.id)!.exitCode === null
                  }
                  selected={selectedTask === task.id}
                  exitCode={runningTasks.get(task.id)?.exitCode ?? null}
                  onRun={() => runTask(task)}
                  onStop={() => stopTask(task.id)}
                  onSelect={() => setSelectedTask(task.id)}
                />
              ))}
          </div>
        ))}
        {tasks.length === 0 && customTasks.length === 0 && (
          <div className="p-4 text-xs text-gray-600 text-center">
            No tasks detected.
            <br />
            Open a project or add a custom task.
          </div>
        )}
      </div>

      {/* Output preview for selected task */}
      {selectedTask && runningTasks.has(selectedTask) && (
        <div className="border-t border-gray-800 max-h-[150px] overflow-y-auto">
          <div className="px-3 py-1 flex items-center justify-between">
            <span className="text-[10px] font-semibold text-gray-400">
              Output
            </span>
            <button
              onClick={() => setSelectedTask(null)}
              className="p-0.5 text-gray-500 hover:text-gray-300"
            >
              <X size={10} />
            </button>
          </div>
          <pre className="px-3 pb-2 text-[10px] text-gray-400 whitespace-pre-wrap font-mono">
            {runningTasks.get(selectedTask)?.output.slice(-2000) ||
              "Running..."}
          </pre>
        </div>
      )}

      {showAddTask && (
        <AddTaskForm
          onAdd={addCustomTask}
          onCancel={() => setShowAddTask(false)}
        />
      )}

      <div className="px-3 py-1 border-t border-gray-800 text-[9px] text-gray-600">
        ⌘⇧B: Build &bull; ⌘⇧T: Test
      </div>
    </div>
  );
}

function TaskItem({
  task,
  running,
  selected,
  exitCode,
  onRun,
  onStop,
  onSelect,
}: {
  task: DetectedTask;
  running: boolean;
  selected: boolean;
  exitCode: number | null;
  onRun: () => void;
  onStop: () => void;
  onSelect: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      className={`flex items-center gap-2 px-3 py-1.5 hover:bg-gray-800/50 group cursor-pointer ${selected ? "bg-gray-800/40" : ""}`}
    >
      <button
        onClick={(e) => {
          e.stopPropagation();
          if (running) onStop();
          else onRun();
        }}
        className="p-0.5 text-gray-500 hover:text-green-400 disabled:opacity-50"
      >
        {running ? (
          <Loader2 size={12} className="animate-spin text-blue-400" />
        ) : (
          <Play size={12} />
        )}
      </button>
      <div className="flex-1 min-w-0">
        <span className="text-[11px] text-gray-300 truncate block">
          {task.label}
        </span>
      </div>
      {exitCode !== null && !running && (
        <span
          className={`text-[9px] tabular-nums ${exitCode === 0 ? "text-green-500" : "text-red-400"}`}
        >
          {exitCode === 0 ? "✓" : `✗ ${exitCode}`}
        </span>
      )}
      <span className="text-[9px] text-gray-600 opacity-0 group-hover:opacity-100">
        {task.source}
      </span>
    </div>
  );
}

function AddTaskForm({
  onAdd,
  onCancel,
}: {
  onAdd: (label: string, command: string) => void;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState("");
  const [command, setCommand] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (label.trim() && command.trim()) {
      onAdd(label.trim(), command.trim());
    }
  };

  return (
    <div className="border-t border-gray-800 p-3">
      <form onSubmit={handleSubmit} className="space-y-2">
        <div className="text-[10px] font-semibold text-gray-400 mb-1">
          Custom Task
        </div>
        <input
          autoFocus
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Task name"
          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 placeholder:text-gray-600 outline-none focus:border-blue-500"
        />
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="Command (e.g. npm run build)"
          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 placeholder:text-gray-600 outline-none focus:border-blue-500"
        />
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="px-2 py-1 text-[10px] text-gray-400 hover:text-gray-200"
          >
            Cancel
          </button>
          <button
            type="submit"
            className="px-2 py-1 text-[10px] bg-blue-600 hover:bg-blue-500 text-white rounded"
          >
            Add
          </button>
        </div>
      </form>
    </div>
  );
}

/** Dispatch a custom event to run the default build task (Cmd+Shift+B) */
export function runDefaultBuildTask() {
  window.dispatchEvent(new CustomEvent("taskRunner:runBuild"));
}

/** Dispatch a custom event to run the default test task (Cmd+Shift+T) */
export function runDefaultTestTask() {
  window.dispatchEvent(new CustomEvent("taskRunner:runTest"));
}
