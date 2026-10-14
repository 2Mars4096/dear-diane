import { useSyncExternalStore } from 'react';
const key = 'dan.teamMaxParallelWorkers.v1';
const event = 'dan:team-capacity';
export function normalizeTeamCapacity(value: unknown): number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 4 ? value : 2;
}
function read(): number {
  try { return normalizeTeamCapacity(JSON.parse(localStorage.getItem(key) || '2')); } catch { return 2; }
}
function subscribe(callback: () => void) {
  window.addEventListener(event, callback);
  window.addEventListener('storage', callback);
  return () => { window.removeEventListener(event, callback); window.removeEventListener('storage', callback); };
}
export function useTeamCapacity(): [number, (value: number) => void] {
  const value = useSyncExternalStore(subscribe, read, () => 2);
  return [value, next => { localStorage.setItem(key, String(normalizeTeamCapacity(next))); window.dispatchEvent(new Event(event)); }];
}
