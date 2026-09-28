export type MainTab = { id: string; kind: "chat" | "pdf" | "file" | "settings" | "papers"; label: string; title?: string };

/** Closing the active tab activates its neighbour; the last tab cannot be closed. */
export function closeMainTab(tabs: MainTab[], active: string, id: string): { tabs: MainTab[]; active: string } {
  if (tabs.length <= 1) return { tabs, active };
  const index = tabs.findIndex((tab) => tab.id === id);
  if (index < 0) return { tabs, active };
  const next = tabs.filter((tab) => tab.id !== id);
  return { tabs: next, active: active === id ? next[Math.min(index, next.length - 1)].id : active };
}
