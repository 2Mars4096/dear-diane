const SAFE_MODE_KEY = "dan-safe-mode";
const FAILED_EXTENSIONS_KEY = "dan-failed-extensions";

export function isSafeMode(): boolean {
  return localStorage.getItem(SAFE_MODE_KEY) === "true";
}

export function enableSafeMode() {
  localStorage.setItem(SAFE_MODE_KEY, "true");
}

export function disableSafeMode() {
  localStorage.removeItem(SAFE_MODE_KEY);
}

interface FailureRecord {
  error: string;
  count: number;
  lastFailure: number;
}

export function recordExtensionFailure(extensionId: string, error: string) {
  const failures = getFailedExtensions();
  failures[extensionId] = {
    error,
    count: (failures[extensionId]?.count ?? 0) + 1,
    lastFailure: Date.now(),
  };
  localStorage.setItem(FAILED_EXTENSIONS_KEY, JSON.stringify(failures));

  if (failures[extensionId].count >= 3) {
    console.warn(`Extension ${extensionId} disabled after 3 failures`);
  }
}

export function getFailedExtensions(): Record<string, FailureRecord> {
  try {
    return JSON.parse(localStorage.getItem(FAILED_EXTENSIONS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function isExtensionFailed(extensionId: string): boolean {
  const failures = getFailedExtensions();
  return (failures[extensionId]?.count ?? 0) >= 3;
}

export function clearFailures() {
  localStorage.removeItem(FAILED_EXTENSIONS_KEY);
}
