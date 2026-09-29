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
  if (!api) return <section aria-label="Desktop updates"><h3>Updates</h3><p>{window.electronAPI ? "Install the first update-enabled build to manage future upgrades here." : "Desktop updates are available in the Dear Diane app."}</p></section>;
  const busy = pending || ["checking", "staging", "downloading", "installing"].includes(state?.phase || "");
  async function action(name: "check" | "choose" | "install-local" | "download" | "install" | "github-sign-in" | "github-cancel" | "github-status") {
    if (!api) return;
    setPending(true); setError("");
    try { setState(await api.action(name)); } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setPending(false); }
  }
  return <section aria-label="Desktop updates" className="wb-desktop-updates">
    <h3>Updates{state ? ` · ${state.currentVersion}` : ""}</h3>
    <p role="status" aria-live="polite">{state?.message || (state ? "Check for updates. Prepared local builds are found automatically." : "Loading update status…")}</p>
    {state && !state.releaseConfigured && <p>Published updates aren’t configured for this build. Local builds can still be installed here.</p>}
    {state?.github && <div aria-label="GitHub account">
      <p>{state.github.status === "connected" ? `GitHub: ${state.github.login}` : state.github.message}</p>
      {state.github.code && <p>One-time code: <strong>{state.github.code}</strong> · <a href="https://github.com/login/device" onClick={event => { event.preventDefault(); void nativeShell.openExternal("https://github.com/login/device"); }}>Open GitHub sign-in</a></p>}
      <div className="wb-update-actions">
        {state.github.status === "signing_in"
          ? <button disabled={pending} onClick={() => void action("github-cancel")}>Cancel sign-in</button>
          : <button disabled={busy} onClick={() => void action("github-sign-in")}>{state.github.status === "connected" ? "Switch GitHub account" : "Sign in to GitHub"}</button>}
        <button disabled={busy || state.github.status === "signing_in"} onClick={() => void action("github-status")}>Refresh connection</button>
      </div>
    </div>}
    {error && <p role="alert">{error}</p>}
    {state?.phase === "downloading" && <progress aria-label="Update download" max={100} value={state.percent}>{state.percent}%</progress>}
    <div className="wb-update-actions">
      <button disabled={!state || busy || (!state.releaseConfigured && !state.localSupported) || state.phase === "ready"} onClick={() => void action("check")}>Check for updates</button>
      {state?.localSupported && state.phase !== "ready" && <button disabled={busy || !state.localBuildAvailable} onClick={() => void action("install-local")}>Install local update</button>}
      {state?.phase === "available" && <button disabled={busy} onClick={() => void action("download")}>Download {state.version}</button>}
      {state?.phase === "ready" && <button disabled={busy} onClick={() => void action("install")}>Install and restart</button>}
    </div>
    {state?.localSupported && <details><summary>Other update options</summary><button disabled={busy} onClick={() => void action("choose")}>Choose a different build…</button></details>}
    {state?.localSupported && !state.localBuildAvailable && state.phase !== "ready" && <p>No prepared local update was found. Local updates do not require GitHub sign-in.</p>}
    {state?.localBuildAvailable && state.phase !== "ready" && <p>A prepared local update is available. Installing restarts Diane after checking for active work.</p>}
    {state?.phase === "ready" && <p>Finish active work first. Diane will close, install {state.version}, and reopen. Chats and settings stay on this Mac.</p>}
  </section>;
}
