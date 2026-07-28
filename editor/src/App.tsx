import { lazy, Suspense, useEffect } from "react";
import {
  applyAppearanceTheme,
  subscribeToSystemAppearance,
} from "./lib/appearanceTheme";
import { workspaceSurfaceThemeClassName } from "./lib/workspaceSurfaceTheme";
import { useSettingsStore } from "./store/useSettingsStore";

const ChunkWorkspaceApp = lazy(() => import("./components/workspace/ChunkWorkspaceApp"));

const WORKSPACE_ROUTE = "workspace";
const WORKSPACE_ROUTE_ALIASES = new Set([WORKSPACE_ROUTE, "chunks", "work"]);

function useAppearanceTheme() {
  const theme = useSettingsStore((s) => s.theme);
  const workspaceSurfaceTone = useSettingsStore((s) => s.workspaceSurfaceTone);

  useEffect(() => {
    const apply = () => {
      const settings = useSettingsStore.getState();
      applyAppearanceTheme(settings.theme, settings.workspaceSurfaceTone);
    };

    apply();
    if (theme !== "system" && workspaceSurfaceTone !== "system") return;

    return subscribeToSystemAppearance(apply);
  }, [theme, workspaceSurfaceTone]);
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
  const workspaceSurfaceTheme = useSettingsStore((s) => s.workspaceSurfaceTheme);
  const workspaceSurfaceTone = useSettingsStore((s) => s.workspaceSurfaceTone);
  const desktopSurfaceClassName = workspaceSurfaceThemeClassName(
    workspaceSurfaceTheme,
    workspaceSurfaceTone,
  );

  return (
    <div className={`h-screen w-screen overflow-hidden ${desktopSurfaceClassName}`}>
      <Suspense
        fallback={
          <div className="grid h-screen w-screen place-items-center bg-white text-sm text-gray-500 dark:bg-gray-950 dark:text-gray-400">
            Loading DAN
          </div>
        }
      >
        <ChunkWorkspaceApp />
      </Suspense>
    </div>
  );
}
