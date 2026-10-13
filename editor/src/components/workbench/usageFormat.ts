/** Colour by what is left: under 25% warns, under 10% is critical. */
export function usageLevel(usedPercent: number): "mid" | "high" | undefined {
  const left = 100 - usedPercent;
  return left < 10 ? "high" : left < 25 ? "mid" : undefined;
}
export function resetsIn(at: number | null, now = Date.now() / 1000): string {
  if (!at) return "";
  if (at <= now) return "awaiting refresh";
  const seconds = at - now;
  if (seconds < 3600) return `resets in ${Math.max(1, Math.round(seconds / 60))}m`;
  if (seconds < 86400) return `resets in ${Math.round(seconds / 3600)}h`;
  return `resets in ${Math.round(seconds / 86400)}d`;
}
export function agoLabel(at?: number, now = Date.now() / 1000): string {
  if (!at) return "";
  const seconds = Math.max(0, now - at);
  return seconds < 60 ? "just now" : seconds < 3600 ? `${Math.round(seconds / 60)}m ago` : `${Math.round(seconds / 3600)}h ago`;
}
