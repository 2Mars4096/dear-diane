const LOCAL_HISTORY_KEY = "dan-local-history";
const MAX_ENTRIES_PER_FILE = 50;
const MAX_TOTAL_FILES = 100;
const MIN_CHANGE_INTERVAL = 60_000;

export interface HistoryEntry {
  timestamp: number;
  content: string;
  label?: string;
}

interface FileHistory {
  [filePath: string]: HistoryEntry[];
}

export function getLocalHistory(): FileHistory {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_HISTORY_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function getFileHistory(filePath: string): HistoryEntry[] {
  return getLocalHistory()[filePath] ?? [];
}

export function pushLocalHistory(
  filePath: string,
  content: string,
  label?: string,
): void {
  try {
    const history = getLocalHistory();
    if (!history[filePath]) history[filePath] = [];

    const entries = history[filePath];
    const lastEntry = entries[entries.length - 1];

    if (lastEntry) {
      if (Date.now() - lastEntry.timestamp < MIN_CHANGE_INTERVAL) return;
      if (lastEntry.content === content) return;
    }

    entries.push({ timestamp: Date.now(), content, label });

    if (entries.length > MAX_ENTRIES_PER_FILE) {
      entries.splice(0, entries.length - MAX_ENTRIES_PER_FILE);
    }

    const fileKeys = Object.keys(history);
    if (fileKeys.length > MAX_TOTAL_FILES) {
      const sortedKeys = fileKeys.sort((a, b) => {
        const aLast = history[a][history[a].length - 1]?.timestamp ?? 0;
        const bLast = history[b][history[b].length - 1]?.timestamp ?? 0;
        return aLast - bLast;
      });
      for (let i = 0; i < fileKeys.length - MAX_TOTAL_FILES; i++) {
        delete history[sortedKeys[i]];
      }
    }

    localStorage.setItem(LOCAL_HISTORY_KEY, JSON.stringify(history));
  } catch {
    // localStorage full — ignore
  }
}

export function clearFileHistory(filePath: string): void {
  const history = getLocalHistory();
  delete history[filePath];
  localStorage.setItem(LOCAL_HISTORY_KEY, JSON.stringify(history));
}
