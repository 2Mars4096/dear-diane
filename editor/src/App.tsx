import { useEffect } from "react";
import AppShell from "./components/shell/AppShell";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import {
  applyAppearanceTheme,
  subscribeToSystemAppearance,
} from "./lib/appearanceTheme";
import { useSettingsStore } from "./store/useSettingsStore";

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

export default function App() {
  useKeyboardShortcuts();
  useAppearanceTheme();
  return <AppShell />;
}
