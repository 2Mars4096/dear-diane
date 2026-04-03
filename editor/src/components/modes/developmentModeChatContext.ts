interface DevelopmentChatContextState {
  activeFilePath: string | null;
  currentBranch: string;
  openFiles: Array<{
    path: string;
    language: string;
    content: string;
  }>;
  pinnedRoots: string[];
}

export function buildDevelopmentModeChatContext(
  state: DevelopmentChatContextState,
) {
  const activeFile = state.openFiles.find(
    (file) => file.path === state.activeFilePath,
  );
  const lines: string[] = ["[Workspace Context]"];

  if (state.currentBranch) {
    lines.push(`Git branch: ${state.currentBranch}`);
  }
  if (activeFile) {
    const lineCount = activeFile.content.split("\n").length;
    lines.push(
      `Active file: ${activeFile.path} (${activeFile.language}, ${lineCount} lines)`,
    );
  }
  if (state.openFiles.length > 0) {
    lines.push(
      `Open files: ${state.openFiles
        .map((file) => file.path.split("/").pop())
        .join(", ")}`,
    );
  }
  if (state.pinnedRoots.length > 0) {
    lines.push(`Workspace roots: ${state.pinnedRoots.join(", ")}`);
  }
  if (activeFile) {
    lines.push(
      "",
      "[Active File Content (first 200 lines)]",
      activeFile.content.split("\n").slice(0, 200).join("\n"),
    );
  }

  return lines.join("\n");
}
