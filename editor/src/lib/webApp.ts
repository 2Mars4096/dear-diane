import { isElectron } from './electronBridge';

export interface InstallPrompt extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}
let pendingInstall: InstallPrompt | null = null;
export const getInstallPrompt = () => pendingInstall;
export const clearInstallPrompt = () => { pendingInstall = null; };
// Capture the one-shot browser event at startup, before lazy Settings mounts.
window.addEventListener('beforeinstallprompt', event => {
  if (isElectron()) return;
  event.preventDefault();
  pendingInstall = event as InstallPrompt;
});
window.addEventListener('appinstalled', clearInstallPrompt);

/** Install the browser shell only; desktop lifecycle belongs to Electron. */
export async function registerWebApp(): Promise<void> {
  if (!import.meta.env.PROD || isElectron() || !window.isSecureContext
      || !['http:', 'https:'].includes(location.protocol) || !('serviceWorker' in navigator)) return;
  try {
    await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' });
  } catch {
    // An unavailable service worker must not prevent ordinary online work.
  }
}
