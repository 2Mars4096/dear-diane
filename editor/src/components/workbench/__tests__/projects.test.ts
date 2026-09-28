// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest';
import { useWorkspaceStore } from '../../../store/useWorkspaceStore';
import { ensureProjectForRoot, saveWorkbenchProject, selectWorkbenchProject } from '../projects';
import type { ChatV2ThreadSummary } from '../../../lib/chatV2Api';
beforeEach(() => { useWorkspaceStore.setState({ workspaces: [], activeWorkspaceId: null }); });
it('reuses visible normalized roots and gives new projects folder names', () => {
  const first = ensureProjectForRoot('/home/me/Project/')!;
  expect(first.created).toBe(true);
  expect(ensureProjectForRoot(' /home/me/Project ')).toEqual({ id: first.id, created: false });
  expect(useWorkspaceStore.getState().workspaces[0].name).toBe('Project');
  useWorkspaceStore.getState().hideWorkspace(first.id);
  expect(ensureProjectForRoot('/home/me/Project')?.created).toBe(true);
  expect(ensureProjectForRoot('')).toBeNull();
});
it('retains secondary roots when editing, without resetting project identity', () => {
  const id = saveWorkbenchProject('First', '/first', true)!;
  useWorkspaceStore.getState().updateWorkspace(id, { pinnedPaths: ['/first', '/extra'] });
  expect(saveWorkbenchProject('Renamed', '/second', false)).toBe(id);
  expect(useWorkspaceStore.getState().workspaces[0]).toMatchObject({ name: 'Renamed', pinnedPaths: ['/second', '/extra'] });
});
it('selects the saved non-archived thread and rejects hidden or already active projects', () => {
  const first = ensureProjectForRoot('/first')!, second = ensureProjectForRoot('/second')!;
  useWorkspaceStore.getState().updateWorkspace(first.id, { activeThreadId: 'thread' });
  const thread = { id: 'thread', archived: false } as ChatV2ThreadSummary;
  expect(selectWorkbenchProject(first.id, [thread])).toEqual({ root: '/first', thread });
  expect(selectWorkbenchProject(first.id, [thread])).toBeNull();
  expect(selectWorkbenchProject(second.id, [thread])).toEqual({ root: '/second', thread: undefined });
  useWorkspaceStore.getState().hideWorkspace(first.id);
  expect(selectWorkbenchProject(first.id, [thread])).toBeNull();
});
