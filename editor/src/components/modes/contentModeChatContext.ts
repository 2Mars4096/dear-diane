import type { ContentPageSummary } from "./contentModeModel";

interface ContentModeChatContextState {
  workspacePaths: string[];
  activeProjectRoot: string | null;
  activePage: ContentPageSummary | null;
  activeDraft: string;
  isDirty: boolean;
}

export function buildContentModeChatContext(
  state: ContentModeChatContextState,
) {
  const lines: string[] = ["[Content Workspace Context]"];

  if (state.workspacePaths.length > 0) {
    lines.push(`Workspace paths: ${state.workspacePaths.join(", ")}`);
  }
  if (state.activeProjectRoot) {
    lines.push(`Active content project: ${state.activeProjectRoot}`);
  }
  if (state.activePage) {
    lines.push(
      `Active page: ${state.activePage.filePath}`,
      `Section: ${state.activePage.section}`,
      `Preview route: ${state.activePage.previewPath}`,
      `Word count: ${state.activePage.wordCount}`,
      `Draft state: ${state.isDirty ? "dirty local draft" : "matches loaded file"}`,
    );
    if (state.activePage.pageId) {
      lines.push(`pageID: ${state.activePage.pageId}`);
    }
    lines.push(
      "",
      "[Active Draft (first 200 lines)]",
      state.activeDraft.split("\n").slice(0, 200).join("\n"),
    );
  }

  return lines.join("\n");
}
