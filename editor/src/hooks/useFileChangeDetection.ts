import { useEffect } from "react";
import { useCodeStore } from "../store/useCodeStore";
import { useAppStore } from "../store/useAppStore";
import { nativeWatch, nativeFs } from "../lib/electronBridge";

const CONFIG_PATTERN =
  /\.(json|toml|yaml|yml|env|rc|config)$/i;
const HIGH_PRIORITY_FILES = [
  "package.json",
  "tsconfig.json",
  "pyproject.toml",
  ".env",
];

export function useFileChangeDetection() {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);

  useEffect(() => {
    if (!pinnedRoots[0]) return;

    const unsub = nativeWatch.onChange((filePath: string) => {
      const { openFiles } = useCodeStore.getState();
      const openFile = openFiles.find((f) => f.path === filePath);
      if (!openFile) return;

      if (openFile.dirty) return;

      const fileName = filePath.split("/").pop() ?? filePath;

      const isConfig =
        CONFIG_PATTERN.test(filePath) ||
        HIGH_PRIORITY_FILES.some((f) => filePath.endsWith(f));

      useAppStore.getState().addNotification({
        type: isConfig ? "warning" : "info",
        title: isConfig ? "Config Changed Externally" : "File Changed",
        message: `${fileName} was modified outside the editor`,
        source: "system",
        action: {
          label: "Reload",
          callback: () => {
            nativeFs.readFile(filePath).then((content) => {
              if (content !== null) {
                useCodeStore.getState().reloadFileContent(filePath, content);
              }
            });
          },
        },
      });
    });

    return unsub;
  }, [pinnedRoots]);
}
