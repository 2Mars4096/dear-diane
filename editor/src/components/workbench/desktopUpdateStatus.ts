import { useSyncExternalStore } from 'react';
import type { DesktopUpdateState } from '../../lib/electronBridge';

let state: DesktopUpdateState | null = null;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let refreshing: Promise<void> | null = null;
let revision = 0;
export function setDesktopUpdateStatus(value: DesktopUpdateState) {
  revision++;
  state = value;
  listeners.forEach(listener => listener());
}
export function refreshDesktopUpdateStatus() {
  if (refreshing) return refreshing;
  const api = window.electronAPI?.updates;
  if (!api) return Promise.resolve();
  const requestedRevision = revision;
  refreshing = api.status().then(value => { if (revision === requestedRevision) setDesktopUpdateStatus(value); }).catch(() => { /* Keep last known availability. */ })
    .finally(() => { refreshing = null; });
  return refreshing;
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1 && window.electronAPI?.updates) {
    void refreshDesktopUpdateStatus();
    timer = setInterval(() => void refreshDesktopUpdateStatus(), 2000);
    window.addEventListener('focus', refreshDesktopUpdateStatus);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      if (timer) clearInterval(timer);
      timer = null;
      window.removeEventListener('focus', refreshDesktopUpdateStatus);
    }
  };
}
export function updateNeedsAttention(value: DesktopUpdateState | null) {
  return value?.phase === 'available' || value?.phase === 'ready';
}
export function useDesktopUpdateStatus() {
  return useSyncExternalStore(subscribe, () => state, () => null);
}
