import type { ChatV2ThreadSummary } from '../../lib/chatV2Api';
import type { DocumentFile } from '../documents/documents';
import type { MainTab } from './mainTabState';
export type ItemGroup = { name: string; workspaceId: string | null; root: string; threads: ChatV2ThreadSummary[]; subgroups?: ItemGroup[] };
export type WorkspaceItem = {
  key: string; title: string; kind: 'session' | 'pdf' | 'file' | 'papers' | 'settings'; projectId: string; project: string;
  root: string; timestamp: number; archived?: boolean; thread?: ChatV2ThreadSummary; tabId?: string; dirty?: boolean; path?: string;
};
export const itemSessionKey = (thread: { id: string; workflow_id: string }) => `${thread.workflow_id}:${thread.id}`;
export function workspaceItems(groups: ItemGroup[], tabs: MainTab[], documents: Record<string, DocumentFile>, dirty: Record<string, boolean>, projects: { id: string; name: string }[] = []): WorkspaceItem[] {
  const flat = groups.flatMap(group => group.subgroups || [group]);
  const sessions = flat.flatMap(group => group.threads.map(thread => ({
    key: itemSessionKey(thread), title: thread.title || 'New session', kind: 'session' as const,
    projectId: group.workspaceId || '', project: projects.find(project => project.id === group.workspaceId)?.name || (group.workspaceId || group.root ? group.name : 'No project'), root: group.root,
    timestamp: Date.parse(thread.updated_at) || Date.parse(thread.created_at) || 0, archived: Boolean(thread.archived), thread,
  })));
  const files = tabs.filter(tab => tab.kind !== 'chat').map(tab => {
    const file = documents[tab.id];
    const owner = flat.filter(group => group.root && file?.path.startsWith(group.root.replace(/\/$/, '') + '/')).sort((a,b) => b.root.length - a.root.length)[0] || flat.find(group => group.workspaceId && group.workspaceId === file?.workspaceId);
    return { key: `tab:${tab.id}`, title: tab.label, kind: tab.kind as WorkspaceItem['kind'], tabId: tab.id, path: file?.path,
      projectId: owner?.workspaceId || file?.workspaceId || '', project: projects.find(project => project.id === (owner?.workspaceId || file?.workspaceId))?.name || owner?.name || file?.projectName || (tab.kind === 'papers' ? 'Library' : tab.kind === 'settings' ? 'App' : 'No project'), root: owner?.root || '',
      timestamp: 0, dirty: Boolean(file && dirty[file.path]),
    };
  });
  return [...new Map([...sessions, ...files].map(item => [item.key, item])).values()];
}
export function orderedWorkspaceItems(items: WorkspaceItem[], visits: string[], pinned: Record<string, { pinned?: boolean }>, baseline: Map<string, number>) {
  return [...items].sort((a,b) => Number(Boolean(pinned[b.key]?.pinned)) - Number(Boolean(pinned[a.key]?.pinned))
    || (visits.indexOf(a.key) < 0 ? Infinity : visits.indexOf(a.key)) - (visits.indexOf(b.key) < 0 ? Infinity : visits.indexOf(b.key))
    || (baseline.get(b.key) || 0) - (baseline.get(a.key) || 0) || a.key.localeCompare(b.key));
}
