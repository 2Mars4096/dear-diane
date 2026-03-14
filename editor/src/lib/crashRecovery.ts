import { useCodeStore } from "../store/useCodeStore";

const CRASH_RECOVERY_KEY = "dan-crash-recovery";
const SNAPSHOT_INTERVAL = 30_000;

export interface CrashSnapshot {
  timestamp: number;
  files: Array<{
    path: string;
    content: string;
    language: string;
  }>;
}

export function startCrashRecovery(): () => void {
  const interval = setInterval(() => {
    const { openFiles } = useCodeStore.getState();
    const dirtyFiles = openFiles.filter((f) => f.dirty);

    if (dirtyFiles.length === 0) {
      localStorage.removeItem(CRASH_RECOVERY_KEY);
      return;
    }

    const snapshot: CrashSnapshot = {
      timestamp: Date.now(),
      files: dirtyFiles.map((f) => ({
        path: f.path,
        content: f.content,
        language: f.language,
      })),
    };

    try {
      localStorage.setItem(CRASH_RECOVERY_KEY, JSON.stringify(snapshot));
    } catch {
      // localStorage full — skip
    }
  }, SNAPSHOT_INTERVAL);

  return () => clearInterval(interval);
}

export function getCrashSnapshot(): CrashSnapshot | null {
  try {
    const raw = localStorage.getItem(CRASH_RECOVERY_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as CrashSnapshot;
  } catch {
    return null;
  }
}

export function clearCrashSnapshot(): void {
  localStorage.removeItem(CRASH_RECOVERY_KEY);
}
