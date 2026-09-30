import { useEffect, useState } from "react";
import { nativeShell, type DesktopUpdateState } from "../../lib/electronBridge";

export function DesktopUpdates() {
  const api = window.electronAPI?.updates;
  const [state, setState] = useState<DesktopUpdateState | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!api) return;
    let disposed = false;
    const refresh = () => api.status().then(value => { if (!disposed) setState(value); }).catch(error => { if (!disposed) setError(String(error)); });
    void refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [api]);
  if (!api) return <section aria-label="Desktop updates"><p>{window.electronAPI ? "Install the first update-enabled build to manage future upgrades here." : "Desktop updates are available in the Dear Diane app."}</p></section>;
  const busy = pending || ["checking", "staging", "downloading", "installing"].includes(state?.phase || "");
  async function action(name: "check" | "choose" | "install-local" | "download" | "install" | "github-sign-in" | "github-cancel" | "github-status") {
    if (!api) return;
    setPending(true); setError("");
    try { setState(await api.action(name)); } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setPending(false); }
  }
  const signingIn = state?.github?.status === "signing_in";
  const connected = state?.github?.status === "connected";
  return <section aria-label="Desktop updates" className="wb-desktop-updates">
    <div className="wb-update-overview">
      <div className="wb-update-summary">
        <span className="wb-update-version">{state ? `Version ${state.currentVersion}` : "Updates"}</span>
        <p role="status" aria-live="polite">{state?.message || (state ? "Check for a newer version." : "Loading update status…")}</p>
      </div>
      <div className="wb-update-actions">
        {state?.github && !connected && !signingIn && state.releaseConfigured && <button disabled={busy} onClick={() => void action("github-sign-in")}>Sign in to GitHub</button>}
        <button disabled={!state || busy || (!state.releaseConfigured && !state.localSupported) || state.phase === "ready"} onClick={() => void action("check")}>{state?.phase === "checking" ? "Checking…" : "Check for updates"}</button>
        {state?.localSupported && state.localBuildAvailable && state.phase !== "ready" && <button disabled={busy} onClick={() => void action("install-local")}>Install local update</button>}
        {state?.phase === "available" && <button disabled={busy} onClick={() => void action("download")}>Download {state.version}</button>}
        {state?.phase === "ready" && <button disabled={busy} onClick={() => void action("install")}>Install and restart</button>}
      </div>
    </div>
    {error && <p role="alert">{error}</p>}
    {state?.phase === "downloading" && <progress aria-label="Update download" max={100} value={state.percent}>{state.percent}%</progress>}
    {signingIn && <div className="wb-update-signin" aria-label="GitHub sign-in">
      <p>{state?.github?.message}{state?.github?.code && <> · <strong>{state.github.code}</strong></>}</p>
      <div className="wb-update-actions">
        <button onClick={() => void nativeShell.openExternal("https://github.com/login/device")}>Open GitHub sign-in</button>
        <button disabled={pending} onClick={() => void action("github-cancel")}>Cancel sign-in</button>
      </div>
    </div>}
    {(state?.localSupported || state?.github) && <details className="wb-update-options"><summary>Update options</summary>
      {state?.github && <div className="wb-update-option-row" aria-label="GitHub account">
        <p>{connected ? `GitHub: ${state.github.login}` : "Sign in to access private releases."}</p>
        <div className="wb-update-actions">
          {connected && <button disabled={busy} onClick={() => void action("github-sign-in")}>Switch GitHub account</button>}
          <button disabled={busy || signingIn} onClick={() => void action("github-status")}>Refresh connection</button>
        </div>
      </div>}
      {state && !state.releaseConfigured && <p>Published updates aren’t configured for this build.</p>}
      {state?.localSupported && <div className="wb-update-option-row">
        <p>{state.localBuildAvailable ? "A local build is ready." : "No prepared local update."} Local updates do not require GitHub sign-in.</p>
        <div className="wb-update-actions"><button disabled={busy} onClick={() => void action("choose")}>Choose a different build…</button></div>
      </div>}
    </details>}
    {state?.phase === "ready" && <p>Finish active work first. Diane will restart to install {state.version}. Chats and settings stay on this Mac.</p>}
  </section>;
}
