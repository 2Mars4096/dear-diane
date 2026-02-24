import { useEffect } from "react";
import { useGraphStore } from "../store/useGraphStore";

export function useKeyboardShortcuts() {
  const saveGraph = useGraphStore((s) => s.saveGraph);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        saveGraph();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [saveGraph]);
}
