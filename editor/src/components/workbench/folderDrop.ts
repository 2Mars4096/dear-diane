import { nativeFs } from "../../lib/electronBridge";

export async function droppedFolderPath(
  transfer: DataTransfer,
  resolve: (file: File) => Promise<string | null> = nativeFs.droppedDirectory,
): Promise<{ path: string; error?: never } | { error: string; path?: never }> {
  // Read drag data synchronously: browsers clear the transfer after the drop event.
  const files = Array.from(transfer.files);
  const item = Array.from(transfer.items).find((entry) => entry.kind === "file");
  const entry = item?.webkitGetAsEntry?.();
  if (files.length !== 1) return { error: "Drop one folder at a time." };
  if (entry && !entry.isDirectory) return { error: "Drop a folder, not a file." };
  try {
    const path = await resolve(files[0]);
    if (path) return { path };
  } catch { /* Preserve the existing field and explain the fallback. */ }
  return { error: "The folder path is unavailable here. Paste its full path, or drop it in the Dear Diane desktop app." };
}
