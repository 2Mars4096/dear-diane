import { useEffect, useState } from "react";
import type { ManagedProcess } from "./ProcessesPanel";

/** Poll DAN-owned processes for this project (slow when the tab is closed; drives the tab badge). */
export function useProcesses(cwd: string, active: boolean) {
  const [processes, setProcesses] = useState<ManagedProcess[]>([]);
  const [error, setError] = useState("");
  const refresh = async () => {
    if (!cwd) { setProcesses([]); return; }
    try {
      const response = await fetch(`/api/processes?cwd=${encodeURIComponent(cwd)}`);
      if (!response.ok) throw new Error("Processes are unavailable.");
      setProcesses((await response.json()).processes ?? []); setError("");
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  };
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), active ? 3000 : 15000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh closes over cwd only
  }, [cwd, active]);
  return { processes, error, refresh };
}
