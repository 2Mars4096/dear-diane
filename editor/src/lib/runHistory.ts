import type { RunSummary } from "./api";

export interface CompareSelection {
  runA: RunSummary;
  runB: RunSummary;
}

export function resolveCompareSelection(
  runs: RunSummary[],
  runAId: string,
  runB: RunSummary,
): CompareSelection | null {
  if (!runAId || runAId === runB.run_id) {
    return null;
  }
  const runA = runs.find((r) => r.run_id === runAId);
  if (!runA) {
    return null;
  }
  return { runA, runB };
}
