import { useCallback, useRef } from "react";
import { useCodeStore } from "../../store/useCodeStore";
import { useDebugStore } from "../../store/useDebugStore";
import { nativeDebug, nativeFs } from "../../lib/electronBridge";
import { goBack, goForward } from "../../hooks/useCursorHistory";
import { useModeScopedWindowEvent } from "../../hooks/useModeScopedWindowEvent";

export function useDevelopmentModeShortcuts() {
  const toggleTerminal = useCodeStore((state) => state.toggleTerminal);
  const activeFilePath = useCodeStore((state) => state.activeFilePath);
  const markFileSaved = useCodeStore((state) => state.markFileSaved);
  const closeFile = useCodeStore((state) => state.closeFile);
  const setActiveSidebarPanel = useCodeStore(
    (state) => state.setActiveSidebarPanel,
  );
  const setQuickOpenVisible = useCodeStore(
    (state) => state.setQuickOpenVisible,
  );
  const setCommandPaletteVisible = useCodeStore(
    (state) => state.setCommandPaletteVisible,
  );
  const toggleSettings = useCodeStore((state) => state.toggleSettings);

  const zenPendingRef = useRef(false);
  const zenTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  const handleSaveAll = useCallback(async () => {
    const state = useCodeStore.getState();
    for (const file of state.openFiles) {
      if (!file.dirty) continue;
      const isUnderRoot = state.pinnedRoots.some(
        (root) => file.path.startsWith(root + "/") || file.path === root,
      );
      if (!isUnderRoot && !state.allowedExternalPaths.has(file.path)) {
        state.setPendingWriteConfirmation({
          filePath: file.path,
          content: file.content,
        });
        return;
      }
      const ok = await nativeFs.writeFile(file.path, file.content);
      if (ok) state.markFileSaved(file.path);
    }
  }, []);

  const navigateCursorHistory = useCallback(
    async (direction: "back" | "forward") => {
      const entry = await (direction === "back" ? goBack() : goForward());
      if (!entry) return;
      const { openFiles, openFile, setActiveFile } = useCodeStore.getState();
      const isOpen = openFiles.find((file) => file.path === entry.filePath);
      if (isOpen) {
        setActiveFile(entry.filePath);
      } else {
        const content = await nativeFs.readFile(entry.filePath);
        if (content !== null) openFile(entry.filePath, content);
      }
      window.dispatchEvent(
        new CustomEvent("editor:goToLine", {
          detail: {
            lineNumber: entry.lineNumber,
            column: entry.column,
          },
        }),
      );
    },
    [],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;

      if (!meta && zenPendingRef.current && event.key === "z") {
        event.preventDefault();
        zenPendingRef.current = false;
        clearTimeout(zenTimeoutRef.current);
        const state = useCodeStore.getState();
        if (state.zenModeFilePath) {
          state.setZenModeFilePath(null);
        } else if (state.activeFilePath) {
          state.setZenModeFilePath(state.activeFilePath);
        }
        return;
      }

      if (meta && event.key === "k") {
        zenPendingRef.current = true;
        clearTimeout(zenTimeoutRef.current);
        zenTimeoutRef.current = setTimeout(() => {
          zenPendingRef.current = false;
        }, 1500);
      } else if (!meta || event.key !== "k") {
        if (zenPendingRef.current && event.key !== "z") {
          zenPendingRef.current = false;
        }
      }

      if (event.altKey && !meta && event.key === "ArrowLeft") {
        event.preventDefault();
        void navigateCursorHistory("back");
        return;
      }
      if (event.altKey && !meta && event.key === "ArrowRight") {
        event.preventDefault();
        void navigateCursorHistory("forward");
        return;
      }

      if (
        event.shiftKey &&
        event.altKey &&
        (event.key === "h" || event.key === "H") &&
        !meta
      ) {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent("codemode:showCallHierarchy"));
        return;
      }

      if (event.key === "F5" && !meta) {
        event.preventDefault();
        const debugState = useDebugStore.getState();
        if (event.shiftKey) {
          void nativeDebug.stop();
        } else if (debugState.status === "paused") {
          void nativeDebug.continue_(debugState.activeThreadId ?? 1);
        } else if (
          debugState.status === "idle" ||
          debugState.status === "stopped"
        ) {
          const config =
            debugState.launchConfigs[debugState.activeLaunchConfigIndex];
          if (config) {
            debugState.setStatus("running");
            void nativeDebug.start(config).then((result) => {
              if (!result.success) {
                debugState.setStatus("idle");
                debugState.appendConsoleOutput(`Error: ${result.error}\n`);
              }
            });
          }
        }
        return;
      }
      if (event.key === "F10" && !meta && !event.shiftKey) {
        event.preventDefault();
        const debugState = useDebugStore.getState();
        if (debugState.status === "paused") {
          void nativeDebug.next(debugState.activeThreadId ?? 1);
        }
        return;
      }
      if (event.key === "F11" && !meta) {
        event.preventDefault();
        const debugState = useDebugStore.getState();
        if (debugState.status === "paused") {
          if (event.shiftKey) {
            void nativeDebug.stepOut(debugState.activeThreadId ?? 1);
          } else {
            void nativeDebug.stepIn(debugState.activeThreadId ?? 1);
          }
        }
        return;
      }

      if (!meta) return;

      if (event.key === "b" && event.shiftKey) {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent("taskRunner:runBuild"));
      } else if (event.key === "`") {
        event.preventDefault();
        toggleTerminal();
      } else if (event.key === "s" && event.altKey) {
        event.preventDefault();
        void handleSaveAll();
      } else if (event.key === "s") {
        event.preventDefault();
        if (activeFilePath) {
          const state = useCodeStore.getState();
          const file = state.openFiles.find(
            (openFile) => openFile.path === activeFilePath,
          );
          if (file?.dirty) {
            const isUnderRoot = state.pinnedRoots.some(
              (root) =>
                activeFilePath.startsWith(root + "/") || activeFilePath === root,
            );
            if (
              !isUnderRoot &&
              !state.allowedExternalPaths.has(activeFilePath)
            ) {
              state.setPendingWriteConfirmation({
                filePath: activeFilePath,
                content: file.content,
              });
              return;
            }
            void nativeFs.writeFile(activeFilePath, file.content).then((ok) => {
              if (ok) markFileSaved(activeFilePath);
            });
          }
        }
      } else if (event.key === "w") {
        event.preventDefault();
        if (activeFilePath) closeFile(activeFilePath);
      } else if (event.key === "t" && event.shiftKey) {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent("taskRunner:runTest"));
      } else if (event.key === "t" && !event.shiftKey) {
        event.preventDefault();
        useCodeStore.getState().setSymbolSearchVisible(true);
      } else if (event.key === "p" && event.shiftKey) {
        event.preventDefault();
        setCommandPaletteVisible(true);
      } else if (event.key === "p") {
        event.preventDefault();
        setQuickOpenVisible(true);
      } else if (event.key === "f" && event.shiftKey) {
        event.preventDefault();
        setActiveSidebarPanel("search");
      } else if (event.key === "x" && event.shiftKey) {
        event.preventDefault();
        setActiveSidebarPanel("extensions");
      } else if (event.key === ",") {
        event.preventDefault();
        toggleSettings();
      } else if (event.key === "i" && event.shiftKey) {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent("codemode:toggleQA"));
      } else if (event.key === "\\") {
        event.preventDefault();
        const state = useCodeStore.getState();
        if (state.splitFilePath) {
          state.setSplitFilePath(null);
        } else if (state.activeFilePath) {
          state.setSplitFilePath(state.activeFilePath);
        }
      }
    },
    [
      activeFilePath,
      closeFile,
      handleSaveAll,
      markFileSaved,
      navigateCursorHistory,
      setActiveSidebarPanel,
      setCommandPaletteVisible,
      setQuickOpenVisible,
      toggleSettings,
      toggleTerminal,
    ],
  );

  useModeScopedWindowEvent<KeyboardEvent>(
    "development",
    "keydown",
    handleKeyDown,
  );
}
