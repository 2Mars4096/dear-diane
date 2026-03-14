import { useEffect, useRef } from "react";
import { useCodeStore } from "../store/useCodeStore";
import { nativeWatch, nativeFs } from "../lib/electronBridge";

const DEBOUNCE_MS = 100;
const TYPING_GRACE_MS = 500;

/**
 * Watches all open files for external changes and reloads them.
 * - Non-dirty files: silently reload from disk.
 * - Dirty files: skip reload (user has unsaved edits).
 * - Debounces rapid fs events and avoids reloading while the user types.
 */
export function useFileWatcher(): void {
  const openFiles = useCodeStore((s) => s.openFiles);
  const reloadFileContent = useCodeStore((s) => s.reloadFileContent);

  const watchedRef = useRef<Set<string>>(new Set());
  const debounceTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const lastEditTime = useRef<Map<string, number>>(new Map());

  const prevFilesRef = useRef<typeof openFiles>([]);

  useEffect(() => {
    const unsub = useCodeStore.subscribe((state) => {
      const prev = prevFilesRef.current;
      const curr = state.openFiles;
      for (const f of curr) {
        const old = prev.find((p) => p.path === f.path);
        if (old && old.content !== f.content) {
          lastEditTime.current.set(f.path, Date.now());
        }
      }
      prevFilesRef.current = curr;
    });
    return unsub;
  }, []);

  useEffect(() => {
    const currentPaths = new Set(openFiles.map((f) => f.path));

    for (const p of currentPaths) {
      if (!watchedRef.current.has(p)) {
        nativeWatch.start(p);
        watchedRef.current.add(p);
      }
    }

    for (const p of watchedRef.current) {
      if (!currentPaths.has(p)) {
        nativeWatch.stop(p);
        watchedRef.current.delete(p);
        debounceTimers.current.delete(p);
        lastEditTime.current.delete(p);
      }
    }
  }, [openFiles]);

  useEffect(() => {
    const cleanup = nativeWatch.onChange((filePath: string) => {
      const prev = debounceTimers.current.get(filePath);
      if (prev) clearTimeout(prev);

      debounceTimers.current.set(
        filePath,
        setTimeout(async () => {
          debounceTimers.current.delete(filePath);

          const lastEdit = lastEditTime.current.get(filePath) ?? 0;
          if (Date.now() - lastEdit < TYPING_GRACE_MS) return;

          const file = useCodeStore.getState().openFiles.find((f) => f.path === filePath);
          if (!file || file.dirty) return;

          const content = await nativeFs.readFile(filePath);
          if (content == null) return;

          if (content !== file.content) {
            reloadFileContent(filePath, content);
          }
        }, DEBOUNCE_MS),
      );
    });

    return () => {
      cleanup();
      for (const [, timer] of debounceTimers.current) clearTimeout(timer);
      debounceTimers.current.clear();
    };
  }, [reloadFileContent]);

  useEffect(() => {
    return () => {
      for (const p of watchedRef.current) {
        nativeWatch.stop(p);
      }
      watchedRef.current.clear();
    };
  }, []);
}
