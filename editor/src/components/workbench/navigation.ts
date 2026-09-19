export interface NavigationSession {
  id: string;
  title: string;
  createdAt: string;
}

export function newestSessionFirst(a: NavigationSession, b: NavigationSession): number {
  return ((Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0)) || b.id.localeCompare(a.id);
}

/** Eight physical slots: surviving sessions never move, even when a ninth arrives. */
export function reconcileSessionSlots(previous: (string | null)[], sessions: NavigationSession[]): (string | null)[] {
  const newest = [...sessions].sort(newestSessionFirst).slice(0, 8);
  const eligible = new Set(newest.map((session) => session.id));
  const used = new Set<string>();
  const slots = Array.from({ length: 8 }, (_, index) => {
    const id = previous[index];
    if (!id || !eligible.has(id) || used.has(id)) return null;
    used.add(id);
    return id;
  });
  for (const session of [...newest].reverse()) {
    if (used.has(session.id)) continue;
    slots[slots.indexOf(null)] = session.id;
    used.add(session.id);
  }
  return slots;
}

export const directionKeys: Record<string, number> = {
  ArrowUp: 0, w: 0, e: 1, ArrowRight: 2, d: 2, c: 3,
  ArrowDown: 4, s: 4, z: 5, ArrowLeft: 6, a: 6, q: 7,
};

export function heldDirectionSlot(keys: Iterable<string>): number | null {
  const held = new Set(Array.from(keys, (key) => key.toLowerCase()));
  const x = Number(held.has("arrowright") || held.has("d")) - Number(held.has("arrowleft") || held.has("a"));
  const y = Number(held.has("arrowdown") || held.has("s")) - Number(held.has("arrowup") || held.has("w"));
  if (!x && !y) return null;
  return (Math.round(Math.atan2(x, -y) / (Math.PI / 4)) + 8) % 8;
}
