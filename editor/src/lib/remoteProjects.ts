import { useWorkspaceStore, type Workspace } from "../store/useWorkspaceStore";

const fields = ["id", "name", "removedFromDan", "icon", "color", "pinnedPaths", "researchConfig", "createdAt"] as const;
type Project = Partial<Workspace> & { id: string };
type Projects = Record<string, Project>;
function metadata(workspaces: Workspace[]): Projects {
  return Object.fromEntries(workspaces.map(workspace => [workspace.id, Object.fromEntries(fields.filter(key => workspace[key] !== undefined).map(key => [key, workspace[key]])) as Project]));
}

export async function startRemoteProjects() {
  const machine = document.querySelector<HTMLMetaElement>('meta[name="dan-remote-machine"]')?.content;
  if (!machine) return;
  document.title = `DAN · ${machine}`;
  document.documentElement.style.setProperty("--dan-remote-label", JSON.stringify(machine));
  let initial;
  try {
    const response = await fetch("/api/remote/registry", { signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error("Sign in again to connect to this machine.");
    initial = await response.json();
  } catch { throw new Error(`Could not load projects from ${machine}. Reconnect and reload this page.`); }
  if (!initial.enabled) throw new Error("This address is no longer a remote DAN service.");
  let applying = false;
  let pending: Projects = {};
  let sending = false;
  let writing = false;
  let baseline: Projects = {};
  const notice = document.createElement("div");
  notice.className = "dan-remote-status";
  notice.setAttribute("role", "status");
  notice.hidden = true;
  document.body.append(notice);
  function apply(projects: Projects) {
    applying = true;
    const state = useWorkspaceStore.getState();
    const workspaces = Object.values(projects).map(project => {
      const existing = state.workspaces.find(item => item.id === project.id);
      return { name: "Remote project", pinnedPaths: [], createdAt: Date.now(), lastAccessedAt: Date.now(), lastActiveMode: "chat", openThreadIds: [], activeThreadId: null, ...existing, ...project } as Workspace;
    });
    useWorkspaceStore.setState({ workspaces, activeWorkspaceId: workspaces.some(item => item.id === state.activeWorkspaceId && !item.removedFromDan) ? state.activeWorkspaceId : workspaces.find(item => !item.removedFromDan)?.id ?? null });
    baseline = metadata(workspaces);
    applying = false;
  }
  async function sync() {
    if (sending) return;
    sending = true;
    const batch = pending;
    pending = {};
    try {
      const hasChanges = Object.keys(batch).length > 0;
      writing = hasChanges;
      const response = await fetch("/api/remote/registry", hasChanges ? { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ projects: batch }), signal: AbortSignal.timeout(10000) } : { signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error("Project sync unavailable");
      const result = await response.json();
      const merged = result.projects as Projects;
      // Preserve edits made while this request was in flight.
      for (const [id, patch] of Object.entries(pending)) merged[id] = { ...merged[id], ...patch };
      apply(merged);
      notice.hidden = true;
    } catch {
      for (const [id, patch] of Object.entries(batch)) pending[id] = { ...patch, ...pending[id] };
      notice.textContent = `Reconnecting to ${machine}. ${Object.keys(pending).length ? "Project changes have not synced yet." : "If your session expired, reload to sign in."}`;
      notice.hidden = false;
    } finally { sending = false; writing = false; }
  }
  apply(initial.projects);
  useWorkspaceStore.subscribe(state => {
    if (applying) return;
    const next = metadata(state.workspaces);
    for (const [id, project] of Object.entries(next)) {
      const changed = Object.fromEntries(fields.filter(key => JSON.stringify(project[key]) !== JSON.stringify(baseline[id]?.[key])).map(key => [key, project[key] ?? null]));
      if (Object.keys(changed).length) pending[id] = { ...pending[id], ...changed, id };
    }
    for (const id of Object.keys(baseline)) if (!next[id]) pending[id] = { id, removedFromDan: true };
    baseline = next;
    if (Object.keys(pending).length) void sync();
  });
  window.setInterval(() => void sync(), 5000);
  window.addEventListener("beforeunload", event => {
    if (writing || Object.keys(pending).length) { event.preventDefault(); event.returnValue = ""; }
  });
}
