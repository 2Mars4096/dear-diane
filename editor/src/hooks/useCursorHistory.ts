interface CursorHistoryEntry {
  filePath: string;
  lineNumber: number;
  column: number;
  timestamp: number;
}

const MAX_HISTORY = 100;

let history: CursorHistoryEntry[] = [];
let currentIndex = -1;
let isNavigating = false;

export function pushCursorPosition(filePath: string, lineNumber: number, column: number) {
  if (isNavigating) return;

  const last = history[currentIndex];
  if (last && last.filePath === filePath && last.lineNumber === lineNumber) return;

  history = history.slice(0, currentIndex + 1);
  history.push({ filePath, lineNumber, column, timestamp: Date.now() });
  if (history.length > MAX_HISTORY) history.shift();
  currentIndex = history.length - 1;
}

export function canGoBack(): boolean {
  return currentIndex > 0;
}

export function canGoForward(): boolean {
  return currentIndex < history.length - 1;
}

export async function goBack(): Promise<CursorHistoryEntry | null> {
  if (!canGoBack()) return null;
  isNavigating = true;
  currentIndex--;
  const entry = history[currentIndex];
  setTimeout(() => { isNavigating = false; }, 100);
  return entry;
}

export async function goForward(): Promise<CursorHistoryEntry | null> {
  if (!canGoForward()) return null;
  isNavigating = true;
  currentIndex++;
  const entry = history[currentIndex];
  setTimeout(() => { isNavigating = false; }, 100);
  return entry;
}
