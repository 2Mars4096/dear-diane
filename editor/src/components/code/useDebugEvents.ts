import { useEffect } from "react";
import { useDebugStore } from "../../store/useDebugStore";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeDebug, nativeFs } from "../../lib/electronBridge";

export function useDebugEvents() {
  const setStatus = useDebugStore((s) => s.setStatus);
  const setThreads = useDebugStore((s) => s.setThreads);
  const setCallStack = useDebugStore((s) => s.setCallStack);
  const setActiveThreadId = useDebugStore((s) => s.setActiveThreadId);
  const setActiveFrameId = useDebugStore((s) => s.setActiveFrameId);
  const setVariables = useDebugStore((s) => s.setVariables);
  const setScopes = useDebugStore((s) => s.setScopes);
  const setPausedLocation = useDebugStore((s) => s.setPausedLocation);
  const appendConsoleOutput = useDebugStore((s) => s.appendConsoleOutput);
  const resetSession = useDebugStore((s) => s.resetSession);
  const updateWatchValue = useDebugStore((s) => s.updateWatchValue);

  useEffect(() => {
    const unsub = nativeDebug.onEvent(async (data) => {
      const { event, body } = data;

      switch (event) {
        case "stopped": {
          setStatus("paused");
          const threadId = body.threadId ?? 1;
          setActiveThreadId(threadId);

          try {
            const threadsResult = await nativeDebug.threads();
            if (threadsResult?.threads) setThreads(threadsResult.threads);

            const stackTraceResult = await nativeDebug.stackTrace(threadId);
            const frames = stackTraceResult?.stackFrames ?? [];
            setCallStack(frames);

            if (frames.length > 0) {
              const topFrame = frames[0];
              setActiveFrameId(topFrame.id);
              setPausedLocation(
                topFrame.source?.path ?? null,
                topFrame.line ?? null,
              );

              const scopesResult = await nativeDebug.scopes(topFrame.id);
              const scopes = scopesResult?.scopes ?? [];
              setScopes(scopes);

              if (scopes.length > 0) {
                const variablesResult = await nativeDebug.variables(
                  scopes[0].variablesReference,
                );
                setVariables(variablesResult?.variables ?? []);
              }

              if (topFrame.source?.path) {
                const { openFiles, openFile, setActiveFile } = useCodeStore.getState();
                const isOpen = openFiles.find(
                  (file) => file.path === topFrame.source!.path,
                );
                if (isOpen) {
                  setActiveFile(topFrame.source.path!);
                } else {
                  const content = await nativeFs.readFile(topFrame.source.path!);
                  if (content !== null) openFile(topFrame.source.path!, content);
                }
                window.dispatchEvent(
                  new CustomEvent("editor:goToLine", {
                    detail: { lineNumber: topFrame.line, column: topFrame.column ?? 1 },
                  }),
                );
              }

              const watches = useDebugStore.getState().watchExpressions;
              for (const watch of watches) {
                try {
                  const result = await nativeDebug.evaluate(watch.expression, topFrame.id);
                  updateWatchValue(watch.id, result?.result, undefined);
                } catch (error: any) {
                  updateWatchValue(watch.id, undefined, error.message);
                }
              }
            }
          } catch {
            // Debug adapter may have disconnected.
          }
          break;
        }

        case "continued":
          setStatus("running");
          setPausedLocation(null, null);
          break;

        case "thread":
          nativeDebug.threads().then((result) => {
            if (result?.threads) setThreads(result.threads);
          });
          break;

        case "terminated":
        case "exited":
          resetSession();
          break;

        case "output":
          if (body.output) {
            appendConsoleOutput(body.output);
          }
          break;

        case "breakpoint":
          break;
      }
    });

    return unsub;
  }, [
    appendConsoleOutput,
    resetSession,
    setActiveFrameId,
    setActiveThreadId,
    setCallStack,
    setPausedLocation,
    setScopes,
    setStatus,
    setThreads,
    setVariables,
    updateWatchValue,
  ]);
}
