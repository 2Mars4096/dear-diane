import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./styles/workspaceSurfaceThemes.css";
import App from "./App";
import { applyAppearanceTheme } from "./lib/appearanceTheme";
import { useSettingsStore } from "./store/useSettingsStore";

const initialSettings = useSettingsStore.getState();
applyAppearanceTheme(initialSettings.theme, initialSettings.workspaceSurfaceTone, initialSettings.workbenchColorScheme);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
