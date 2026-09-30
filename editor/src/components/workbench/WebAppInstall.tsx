import { useEffect, useState } from 'react';
import { isElectron } from '../../lib/electronBridge';
import { clearInstallPrompt, getInstallPrompt, type InstallPrompt } from '../../lib/webApp';

export function WebAppInstall() {
  const [prompt, setPrompt] = useState<InstallPrompt | null>(getInstallPrompt);
  const [installed, setInstalled] = useState(() => window.matchMedia('(display-mode: standalone)').matches);
  const [error, setError] = useState('');
  useEffect(() => {
    const available = (event: Event) => { event.preventDefault(); setPrompt(event as InstallPrompt); };
    const done = () => { setInstalled(true); setPrompt(null); };
    window.addEventListener('beforeinstallprompt', available);
    window.addEventListener('appinstalled', done);
    return () => {
      window.removeEventListener('beforeinstallprompt', available);
      window.removeEventListener('appinstalled', done);
    };
  }, []);
  if (isElectron()) return null;
  return <section aria-labelledby="web-app-heading">
    <h3 id="web-app-heading">Phone app</h3>
    {installed ? <p>Dear Diane is open as an installed web app.</p> : <>
      <p>Add Dear Diane to your Home Screen to open it as an app.</p>
      {!window.isSecureContext && <p>Open your Diane host through its trusted HTTPS address to enable app installation.</p>}
      {prompt ? <button onClick={async () => {
        setError('');
        try { await prompt.prompt(); await prompt.userChoice; }
        catch { setError('Use your browser menu to add Dear Diane to your Home Screen.'); }
        finally { clearInstallPrompt(); setPrompt(null); }
      }}>Install Dear Diane</button> : <p>iPhone: open the browser’s Share menu → Add to Home Screen. Android: open the browser menu → Install app or Add to Home screen.</p>}
    </>}
    <p>Reconnect to your Diane host to use the workspace. Phone notifications are not enabled yet.</p>
    {error && <p role="alert">{error}</p>}
  </section>;
}
