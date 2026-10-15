import { execFile } from 'node:child_process';

/** Validate the interpreter before starting a long-lived server. A hung site import is bounded. */
export async function selectBackendPython(candidates: string[], options: { cwd: string; env: NodeJS.ProcessEnv },
  probe = (executable: string) => new Promise<void>((resolve, reject) => {
    execFile(executable, ['-c', 'import diane.server, uvicorn'], { ...options, timeout: 4000, killSignal: 'SIGKILL', maxBuffer: 16384 }, error => error ? reject(error) : resolve());
  })) {
  for (const executable of [...new Set(candidates.filter(Boolean))]) {
    try { await probe(executable); return executable; } catch { /* Try the next configured installation. */ }
  }
  throw new Error('No working Diane Python environment was found. Set DIANE_PYTHON to an interpreter with dear-diane installed.');
}
