import { lazy, Suspense, useEffect, useState } from "react";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import {
  applyAppearanceTheme,
  subscribeToSystemAppearance,
} from "./lib/appearanceTheme";
import { useSettingsStore } from "./store/useSettingsStore";

const AppShell = lazy(() => import("./components/shell/AppShell"));
const ChatV2App = lazy(() => import("./components/v2/ChatV2App"));
const ChunkWorkspaceApp = lazy(() => import("./components/workspace/ChunkWorkspaceApp"));

function isV2Route() {
  const route = window.location.hash.replace(/^#/, "").trim().toLowerCase();
  return route === "v2" || route === "chat-v2";
}

function isWorkspaceRoute() {
  const route = window.location.hash.replace(/^#/, "").trim().toLowerCase();
  return route === "workspace" || route === "chunks" || route === "work";
}

function useAppearanceTheme() {
  const theme = useSettingsStore((s) => s.theme);

  useEffect(() => {
    const apply = () =>
      applyAppearanceTheme(useSettingsStore.getState().theme);

    apply();
    if (theme !== "system") return;

    return subscribeToSystemAppearance(apply);
  }, [theme]);
}

function ClassicApp() {
  useKeyboardShortcuts();
  return <AppShell />;
}

export default function App() {
  useAppearanceTheme();
  const [v2Route, setV2Route] = useState(() => isV2Route());
  const [workspaceRoute, setWorkspaceRoute] = useState(() => isWorkspaceRoute());

  useEffect(() => {
    const syncRoute = () => {
      setV2Route(isV2Route());
      setWorkspaceRoute(isWorkspaceRoute());
    };
    window.addEventListener("hashchange", syncRoute);
    return () => window.removeEventListener("hashchange", syncRoute);
  }, []);

  return (
    <Suspense
      fallback={
        <div className="grid h-screen w-screen place-items-center bg-white text-sm text-gray-500 dark:bg-gray-950 dark:text-gray-400">
          Loading DAN
        </div>
      }
    >
      {workspaceRoute ? <ChunkWorkspaceApp /> : v2Route ? <ChatV2App /> : <ClassicApp />}
    </Suspense>
  );
}
