import { useCodeStore } from "../store/useCodeStore";
import { nativeFs } from "./electronBridge";
import { pushLocalHistory } from "./localHistory";

export function startAutosave(): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  async function flushDirtyFiles() {
    const { openFiles, markFileSaved } = useCodeStore.getState();
    const dirty = openFiles.filter((f) => f.dirty);
    if (dirty.length === 0) return;

    for (const f of dirty) {
      pushLocalHistory(f.path, f.content, "Autosave");
    }

    const results = await Promise.allSettled(
      dirty.map((f) => nativeFs.writeFile(f.path, f.content)),
    );

    results.forEach((result, i) => {
      if (result.status === "fulfilled" && result.value) {
        markFileSaved(dirty[i].path);
        console.debug("[autosave] saved", dirty[i].path);
      } else {
        console.warn("[autosave] failed to save", dirty[i].path);
      }
    });
  }

  function scheduleSave() {
    if (stopped) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      if (typeof requestIdleCallback === "function") {
        requestIdleCallback(() => void flushDirtyFiles());
      } else {
        void flushDirtyFiles();
      }
    }, 1_000);
  }

  const unsubscribe = useCodeStore.subscribe((state) => {
    if (state.openFiles.some((f) => f.dirty)) scheduleSave();
  });

  function onBeforeUnload() {
    const { openFiles, markFileSaved } = useCodeStore.getState();
    const dirty = openFiles.filter((f) => f.dirty);
    for (const f of dirty) {
      try {
        nativeFs.writeFile(f.path, f.content);
        markFileSaved(f.path);
        console.debug("[autosave] beforeunload saved", f.path);
      } catch {
        // best-effort during unload
      }
    }
  }

  window.addEventListener("beforeunload", onBeforeUnload);

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    unsubscribe();
    window.removeEventListener("beforeunload", onBeforeUnload);
  };
}
