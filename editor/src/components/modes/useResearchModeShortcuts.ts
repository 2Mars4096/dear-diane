import { useCallback } from "react";
import { useResearchStore } from "../../store/useResearchStore";
import { useCodeStore } from "../../store/useCodeStore";
import { useModeScopedWindowEvent } from "../../hooks/useModeScopedWindowEvent";

export function useResearchModeShortcuts() {
  const setPrimaryTab = useResearchStore((state) => state.setPrimaryTab);
  const primaryTab = useResearchStore((state) => state.primaryTab);
  const setContextTab = useResearchStore((state) => state.setContextTab);
  const contextTab = useResearchStore((state) => state.contextTab);
  const toggleContextPanel = useResearchStore(
    (state) => state.toggleContextPanel,
  );
  const toggleTerminal = useCodeStore((state) => state.toggleTerminal);

  const handler = useCallback(
    (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;

      if (meta && event.key === "e" && !event.shiftKey) {
        event.preventDefault();
        setPrimaryTab(primaryTab === "editor" ? "reader" : "editor");
        return;
      }
      if (meta && event.shiftKey && event.key === "r") {
        event.preventDefault();
        setContextTab(contextTab === "references" ? "notes" : "references");
        useResearchStore.getState().setShowContextPanel(true);
        return;
      }
      if (meta && event.shiftKey && event.key === "o") {
        event.preventDefault();
        setContextTab(contextTab === "outline" ? "notes" : "outline");
        useResearchStore.getState().setShowContextPanel(true);
        return;
      }
      if (meta && event.key === "i" && !event.shiftKey) {
        event.preventDefault();
        toggleContextPanel();
        return;
      }
      if (meta && event.key === "`") {
        event.preventDefault();
        toggleTerminal();
      }
    },
    [
      contextTab,
      primaryTab,
      setContextTab,
      setPrimaryTab,
      toggleContextPanel,
      toggleTerminal,
    ],
  );

  useModeScopedWindowEvent<KeyboardEvent>("research", "keydown", handler, true);
}
