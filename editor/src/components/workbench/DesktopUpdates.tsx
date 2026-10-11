import { useState } from "react";

import { useDesktopUpdateStatus, setDesktopUpdateStatus, updateNeedsAttention } from "./desktopUpdateStatus";

export function DesktopUpdates() {
  const api = window.electronAPI?.updates;
  const state = useDesktopUpdateStatus();
  const setState = setDesktopUpdateStatus;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  if (!api) return <section aria-label="Desktop updates"><p>{window.electronAPI ? "Install the first update-enabled build to manage future upgrades here." : "Desktop updates are available in the Dear Diane app."}</p></section>;
  const busy = pending || ["checking", "staging", "downloading", "installing"].includes(state?.phase || "");
  async function action(name: "check" | "choose" | "install-local" | "download" | "download-install" | "install") {
    if (!api) return;
    setPending(true); setError("");
    try { setState(await api.action(name)); } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setPending(false); }
  }
  const attention = updateNeedsAttention(state);
  const primaryAction = state?.phase === "ready" ? "install" : state?.phase === "available" ? "download-install" : "check";
  const primaryLabel = state?.phase === "checking" ? "Checking…"
    : state?.phase === "downloading" ? "Downloading…"
    : state?.phase === "staging" ? "Preparing…"
    : state?.phase === "installing" ? "Restarting…"
    : primaryAction === "install" ? "Install and restart"
    : primaryAction === "download-install" ? "Download and install" : "Check for updates";
  return <section aria-label="Desktop updates" className="wb-desktop-updates">
    <div className="wb-update-overview">
      <div className="wb-update-summary">
        <span className="wb-update-version">{state ? `Version ${state.currentVersion}` : "Updates"}</span>
        <p role="status" aria-live="polite">{state?.message || (state ? "Check for a newer version." : "Loading update status…")}</p>
      </div>
      <div className="wb-update-actions" aria-label={attention ? "Update available" : "Update actions"}>
        <button disabled={!state || busy || (primaryAction === "check" && !state.releaseConfigured)} onClick={() => void action(primaryAction)}>
          {primaryLabel}{attention && <span className="wb-update-dot" aria-hidden="true" />}
        </button>
      </div>
    </div>
    {state?.phase === "available" && <p>Diane will restart automatically after downloading and installing the update.</p>}
    {error && <p role="alert">{error}</p>}
    {state?.phase === "downloading" && <progress aria-label="Update download" max={100} value={state.percent}>{state.percent}%</progress>}
    {state?.localSupported && <details className="wb-update-options"><summary>Update options</summary>
      {state && !state.releaseConfigured && <p>Published updates aren’t configured for this build.</p>}
      {state?.localSupported && <div className="wb-update-option-row">
        <p>{state.localBuildAvailable ? "A local build is ready." : "No prepared local update."}</p>
        <div className="wb-update-actions">
          {state.localBuildAvailable && state.phase !== "ready" && <button disabled={busy} onClick={() => void action("install-local")}>Install local update</button>}
          <button disabled={busy} onClick={() => void action("choose")}>Choose a different build…</button>
        </div>
      </div>}
    </details>}
    {state?.phase === "ready" && <p>Finish active work first. Diane will restart to install {state.version}. Chats and settings stay on this Mac.</p>}
  </section>;
}
