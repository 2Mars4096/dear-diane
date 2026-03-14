import { useEffect, useRef, useState } from "react";
import { useCodeStore } from "../store/useCodeStore";
import { nativeFs } from "../lib/electronBridge";
import { saveSession, loadSession } from "../lib/sessionPersistence";
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

    const { openFile, setActiveFile, restoreSession, updateFileContent } =
      useCodeStore.getState();

    const session = loadSession();
    if (session) {
      restoreSession({
        pinnedRoots: session.pinnedRoots,
        showTerminal: session.showTerminal,
        showSidebar: session.showSidebar,
        activeSidebarPanel: session.activeSidebarPanel as
          | "explorer"
          | "search"
          | "git"
          | "extensions",
        recentFiles: session.recentFiles,
      });

      void (async () => {
        for (const filePath of session.openFilePaths) {
          try {
            const content = await nativeFs.readFile(filePath);
            if (content !== null) openFile(filePath, content);
          } catch {
            // file deleted or unreadable — skip
          }
        }
        if (
          session.activeFilePath &&
          useCodeStore
            .getState()
            .openFiles.some((f) => f.path === session.activeFilePath)
        ) {
          setActiveFile(session.activeFilePath);
        }
        console.debug(
          "[session] restored",
          session.openFilePaths.length,
          "files",
        );

        // After session files are loaded, apply crash recovery on top
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
      })();
    } else {
      // No session — still check for crash recovery
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
    }

    const stopAutosave = startAutosave();
    const stopCrashRecovery = startCrashRecovery();

    const sessionInterval = setInterval(() => {
      if (typeof requestIdleCallback === "function") {
        requestIdleCallback(() => saveSession());
      } else {
        saveSession();
      }
    }, 10_000);

    function onBeforeUnload() {
      saveSession();
    }
    window.addEventListener("beforeunload", onBeforeUnload);

    return () => {
      stopAutosave();
      stopCrashRecovery();
      clearInterval(sessionInterval);
      window.removeEventListener("beforeunload", onBeforeUnload);
      saveSession();
    };
  }, []);

  return {
    recoveredFileCount,
    dismissRecovery: () => setRecoveredFileCount(0),
  };
}
