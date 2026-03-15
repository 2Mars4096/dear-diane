import { useEffect, useRef, useState } from "react";
import { useCodeStore } from "../store/useCodeStore";
import { startAutosave } from "../lib/autosave";
import {
  getCrashSnapshot,
  clearCrashSnapshot,
  startCrashRecovery,
} from "../lib/crashRecovery";

const MAX_SNAPSHOT_AGE = 24 * 60 * 60 * 1000; // 24 hours

export function useSessionRestore(): {
  recoveredFileCount: number;
  dismissRecovery: () => void;
} {
  const didRestore = useRef(false);
  const [recoveredFileCount, setRecoveredFileCount] = useState(0);

  useEffect(() => {
    if (didRestore.current) return;
    didRestore.current = true;

    const { openFile, updateFileContent } = useCodeStore.getState();

    const snapshot = getCrashSnapshot();
    if (snapshot && snapshot.files.length > 0) {
      const age = Date.now() - snapshot.timestamp;
      if (age < MAX_SNAPSHOT_AGE) {
        let restored = 0;
        for (const file of snapshot.files) {
          openFile(file.path, file.content, file.language);
          updateFileContent(file.path, file.content);
          restored++;
        }
        if (restored > 0) {
          console.debug("[crash-recovery] restored", restored, "files");
          setRecoveredFileCount(restored);
        }
      }
    }
    clearCrashSnapshot();

    const stopAutosave = startAutosave();
    const stopCrashRecovery = startCrashRecovery();

    return () => {
      stopAutosave();
      stopCrashRecovery();
    };
  }, []);

  return {
    recoveredFileCount,
    dismissRecovery: () => setRecoveredFileCount(0),
  };
}
