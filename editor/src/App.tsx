import { lazy, Suspense, useEffect } from "react";
import {
  applyAppearanceTheme,
  subscribeToSystemAppearance,
} from "./lib/appearanceTheme";
import { useSettingsStore } from "./store/useSettingsStore";

const OpenRouterSetup = lazy(() => import("./components/workbench/OpenRouterSetup"));
const ChunkWorkspaceApp = lazy(() => import("./components/workspace/ChunkWorkspaceApp"));

const WORKSPACE_ROUTE = "workspace";
const WORKSPACE_ROUTE_ALIASES = new Set([WORKSPACE_ROUTE, "chunks", "work"]);

function useAppearanceTheme() {
  const theme = useSettingsStore((s) => s.theme);
  const colorScheme = useSettingsStore((s) => s.workbenchColorScheme);
  const workspaceSurfaceTone = useSettingsStore((s) => s.workspaceSurfaceTone);

  useEffect(() => {
    const apply = () => {
      const settings = useSettingsStore.getState();
      applyAppearanceTheme(settings.theme, settings.workspaceSurfaceTone, settings.workbenchColorScheme);
    };

    apply();
    if (theme !== "system" && workspaceSurfaceTone !== "system") return;

    return subscribeToSystemAppearance(apply);
  }, [theme, workspaceSurfaceTone, colorScheme]);
}

function useWorkspaceOnlyRoute() {
  useEffect(() => {
    const normalizeRoute = () => {
      const route = window.location.hash.replace(/^#/, "").trim().toLowerCase();
      if (route === WORKSPACE_ROUTE) return;
      if (WORKSPACE_ROUTE_ALIASES.has(route) || route) {
        window.history.replaceState(
          null,
          "",
          `${window.location.pathname}${window.location.search}#${WORKSPACE_ROUTE}`,
        );
        return;
      }
      window.location.hash = WORKSPACE_ROUTE;
    };

    normalizeRoute();
    window.addEventListener("hashchange", normalizeRoute);
    return () => window.removeEventListener("hashchange", normalizeRoute);
  }, []);
}

export default function App() {
  useAppearanceTheme();
  useWorkspaceOnlyRoute();

  return (
    <div className="h-screen w-screen overflow-hidden">
      <Suspense
        fallback={
          <div className="h-screen w-screen" role="status" aria-label="Restoring workspace">
          </div>
        }
      >
        <OpenRouterSetup><ChunkWorkspaceApp /></OpenRouterSetup>
      </Suspense>
    </div>
  );
}
