import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./styles/workspaceSurfaceThemes.css";
import App from "./App";
import { applyAppearanceTheme } from "./lib/appearanceTheme";
import { useSettingsStore } from "./store/useSettingsStore";

const initialSettings = useSettingsStore.getState();
applyAppearanceTheme(initialSettings.theme, initialSettings.workspaceSurfaceTone, initialSettings.workbenchColorScheme);

// Hydrate the remote project directory before the workbench chooses a session.
void import("./lib/remoteProjects").then(module => module.startRemoteProjects()).then(() => createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)).catch(error => createRoot(document.getElementById("root")!).render(<main style={{ padding: 32 }}><h1>Remote connection unavailable</h1><p role="alert">{String(error.message || error)}</p><button onClick={() => location.reload()}>Try again</button> <a href="/remote/login">Sign in</a></main>));
