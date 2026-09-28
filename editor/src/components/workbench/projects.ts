import { useWorkspaceStore } from '../../store/useWorkspaceStore';
import { fileName, normalizeRootPath } from '../../lib/workspacePaths';
import type { ChatV2ThreadSummary } from '../../lib/chatV2Api';

/** Creating/editing a project changes its registry entry; UI selection remains with the caller. */
export function saveWorkbenchProject(name: string, root: string, creating: boolean) {
  const store = useWorkspaceStore.getState();
  const id = creating ? store.createWorkspace(name, 'chat') : store.activeWorkspaceId;
  if (!id) return null;
  const previous = store.workspaces.find(project => project.id === id);
  store.updateWorkspace(id, { name, pinnedPaths: root ? [root, ...(creating ? [] : previous?.pinnedPaths.slice(1) ?? [])] : [] });
  return id;
}
export function ensureProjectForRoot(root: string) {
  const path = normalizeRootPath(root);
  if (!path) return null;
  const existing = useWorkspaceStore.getState().workspaces.find(project => !project.removedFromDan && normalizeRootPath(project.pinnedPaths[0] ?? '') === path);
  if (existing) return { id: existing.id, created: false };
  return { id: saveWorkbenchProject(fileName(path), path, true)!, created: true };
}
export function selectWorkbenchProject(id: string, threads: ChatV2ThreadSummary[]) {
  const store = useWorkspaceStore.getState();
  if (id === store.activeWorkspaceId) return null;
  const target = store.workspaces.find(project => project.id === id && !project.removedFromDan);
  if (!target) return null;
  store.setActiveWorkspace(id);
  return { root: target.pinnedPaths[0], thread: threads.find(thread => thread.id === target.activeThreadId && !thread.archived) };
}
