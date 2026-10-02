import { execFile, type ChildProcess } from "node:child_process";
import { promisify } from "node:util";

const execute = promisify(execFile);
type ProcessIdentity = { pid: number; parent: number; started: string };
async function processes(): Promise<ProcessIdentity[]> {
  const { stdout } = await execute("ps", ["-axo", "pid=,ppid=,lstart="], { timeout: 2000, maxBuffer: 4 * 1024 * 1024 });
  return stdout.split("\n").flatMap(line => {
    const match = line.trim().match(/^(\d+)\s+(\d+)\s+(.+)$/);
    return match ? [{ pid: Number(match[1]), parent: Number(match[2]), started: match[3] }] : [];
  });
}

/** Capture descendants before SIGTERM can reparent them, including setsid workers. */
export async function stopBackendTree(child: ChildProcess, graceMs = 4000): Promise<void> {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "win32") {
    await execute("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
    return;
  }
  const owned = new Map<number, string>();
  const capture = (rows: ProcessIdentity[]) => {
    // Do not adopt descendants of a PID that has since been reused.
    const parents = new Set(rows.filter(row => owned.get(row.pid) === row.started).map(row => row.pid));
    let changed = true;
    while (changed) {
      changed = false;
      for (const row of rows) if (!parents.has(row.pid) && parents.has(row.parent)) {
        parents.add(row.pid); owned.set(row.pid, row.started); changed = true;
      }
    }
  };
  const initial = await processes();
  const root = initial.find(row => row.pid === child.pid);
  if (!root) return;
  owned.set(root.pid, root.started);
  capture(initial);
  child.kill("SIGTERM");
  const alive = (rows: ProcessIdentity[]) => rows.filter(row => owned.get(row.pid) === row.started);
  const signal = (rows: ProcessIdentity[], name: NodeJS.Signals) => {
    for (const row of alive(rows).reverse()) {
      try { process.kill(row.pid, name); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error; }
    }
  };
  const deadline = Date.now() + graceMs;
  let rows = initial;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 100));
    rows = await processes(); capture(rows);
    if (!alive(rows).length) return;
  }
  signal(rows, "SIGTERM");
  await new Promise(resolve => setTimeout(resolve, 300));
  rows = await processes(); capture(rows);
  signal(rows, "SIGKILL");
  // Reap the backend itself before a restart can bind its port.
  if (child.exitCode === null && child.signalCode === null) {
    await new Promise<void>(resolve => {
      const timer = setTimeout(resolve, 2000);
      child.once("exit", () => { clearTimeout(timer); resolve(); });
    });
  }
}
