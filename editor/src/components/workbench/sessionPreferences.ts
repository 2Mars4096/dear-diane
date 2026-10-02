import { useCallback, useEffect, useState } from 'react';
export type SessionPreferences = Record<string, { pinned?: boolean; unread?: boolean; workspaceId?: string }>;
const KEY = 'dan.sessionPreferences.v1';
function read(): SessionPreferences {
  try { const value = JSON.parse(localStorage.getItem(KEY) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch { return {}; }
}
export function useSessionPreferences() {
  const [preferences, setPreferences] = useState(read);
  useEffect(() => {
    const sync = (event: StorageEvent) => { if (event.key === KEY) setPreferences(read()); };
    window.addEventListener('storage', sync); return () => window.removeEventListener('storage', sync);
  }, []);
  const update = useCallback((key: string, patch: { pinned?: boolean; unread?: boolean; workspaceId?: string }) => {
    const latest = read();
    const next = { ...latest, [key]: { ...latest[key], ...patch } };
    localStorage.setItem(KEY, JSON.stringify(next)); setPreferences(next);
  }, []);
  return { preferences, update };
}
