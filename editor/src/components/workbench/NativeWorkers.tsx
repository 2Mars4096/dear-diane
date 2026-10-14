import { useTeamCapacity } from "./teamCapacity";
import { useEffect, useState } from "react";
import { useDismissDetails } from "./useDismissDetails";
import { ChevronDown, Plus, X } from "lucide-react";
import { ModelFields } from "./ModelFields";
import { EMPTY_PROFILE as empty, modelSource, type WorkerProfile, type WorkerProfiles, type Runtime } from "./modelSelection";
export type { WorkerProfile, WorkerProfiles, Runtime } from "./modelSelection";

// Older profiles saved Claude aliases; the menu now lists full IDs only.
const CLAUDE_ALIASES: Record<string, string> = { opus: "claude-opus-5-5", sonnet: "claude-sonnet-5", haiku: "claude-haiku-4-5-20251001", fable: "claude-fable-5-1" };
export function loadWorkerProfiles(key = "dan.nativeWorkerProfiles.v1"): WorkerProfiles {
  try {
    const stored = JSON.parse(localStorage.getItem(key) || "{}");
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
    return Object.fromEntries(Object.keys(stored).filter(id => stored[id] && typeof stored[id] === "object" && ["dan", "codex", "claude", "antigravity", "cursor"].includes(stored[id].backend || id)).map((id) => [id, { ...empty, ...stored[id], enabled: !!(stored[id].backend || stored[id].enabled), model: (stored[id].backend || id) === "claude" && modelSource(stored[id]) === "native" ? CLAUDE_ALIASES[stored[id].model] ?? stored[id].model : stored[id].model }]));
  } catch { return {}; }
}
export function NativeWorkerSettings({ profiles, onChange, lead, inline = false, catalog, disabled = false }: { profiles: WorkerProfiles; onChange: (profiles: WorkerProfiles) => void; lead?: string; inline?: boolean; catalog?: Runtime[]; disabled?: boolean }) {
  const [capacity, setCapacity] = useTeamCapacity();
  const details = useDismissDetails();
  const [selectedWorker, setSelectedWorker] = useState("");
  const roster = Object.entries(profiles).filter(([, profile]) => profile.enabled || profile.backend);
  const addWorker = () => {
    const id = `worker-${crypto.randomUUID()}`;
    onChange({ ...profiles, [id]: { ...empty, enabled: true, backend: "codex", name: `Worker ${roster.length + 1}` } });
    setSelectedWorker(id);
  };
  const [runtimes, setRuntimes] = useState<Runtime[]>([]);
  const [error, setError] = useState("");
  async function refresh() {
    try {
      const response = await fetch("/api/native-workers/catalog");
      if (!response.ok) throw new Error("Unable to load worker accounts. Check the Dear Diane server.");
      const data = await response.json();
      if (!Array.isArray(data.runtimes)) throw new Error("Restart the Dear Diane server to load worker settings.");
      setRuntimes(data.runtimes); setError("");
    } catch (error) { setError(String(error)); }
  }
  useEffect(() => { if (inline && !catalog) void refresh(); }, [inline, catalog]);
  const panel = <div className={inline ? "wb-agent-fields" : "wb-native-settings-panel"}>
      {!inline && <><h3>Team</h3><p>Add workers and configure each one. The lead assigns tasks to these workers.</p></>}
      {!lead && <><label>Max parallel workers<select value={capacity} disabled={disabled} onChange={event => setCapacity(Number(event.target.value))}>{[1, 2, 3, 4].map(value => <option key={value} value={value}>{value}</option>)}</select></label><p>The lead may use fewer. Does not include the lead.</p></>}
      {!lead && <div className="wb-worker-roster">
        <p>{roster.length} configured · up to {capacity} working at once</p>
        {roster.map(([id, profile]) => {
          const runtimeId = profile.backend || id;
          const runtime = (catalog ?? runtimes).find(item => item.id === runtimeId);
          const update = (patch: Partial<WorkerProfile>) => onChange({ ...profiles, [id]: { ...profile, backend: runtimeId, ...patch, enabled: true } });
          return <div className="wb-worker-config" key={id}>
            <div className="wb-worker-config-heading"><button type="button" aria-expanded={selectedWorker === id} onClick={() => setSelectedWorker(selectedWorker === id ? "" : id)}><ChevronDown size={13} /><strong>{profile.name || runtime?.label || runtimeId}</strong><span>{runtime?.label || runtimeId} · {runtime?.model_labels?.[profile.model] || profile.model || "Default model"}</span></button><button type="button" aria-label={`Remove ${profile.name || runtimeId}`} disabled={disabled} onClick={() => { const next = { ...profiles }; delete next[id]; onChange(next); }}><X size={14} /></button></div>
            {selectedWorker === id && <fieldset disabled={disabled}>
              <label>Name<input value={profile.name || ""} placeholder="Worker name" onChange={event => update({ name: event.target.value })} /></label>
              <label>Agent<select value={runtimeId} onChange={event => onChange({ ...profiles, [id]: { ...empty, enabled: true, name: profile.name, backend: event.target.value } })}>{(catalog ?? runtimes).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
              <label><input type="checkbox" checked={!!profile.allow_children} disabled={disabled || !["dan", "codex", "claude"].includes(runtimeId)} onChange={event => update({ allow_children: event.target.checked })} /> Allow child sessions</label>
              <p>{["dan", "codex", "claude"].includes(runtimeId) ? "Children use this worker’s settings and share the team’s parallel limit." : "Child sessions are supported for Diane, Codex and Claude Code."}</p>
              {runtime?.available ? <ModelFields runtime={runtime} profile={profile} onChange={update} disabled={disabled} /> : <p>{runtime?.setup || "Loading agent settings…"}</p>}
            </fieldset>}
          </div>;
        })}
        <button type="button" className="wb-add-worker" onClick={addWorker} disabled={disabled}><Plus size={14} /> Add worker</button>
      </div>}
      {error && <p role="alert">{error}</p>}
      {lead && (catalog ?? runtimes).filter(runtime => runtime.id === lead).map(runtime => <fieldset key={runtime.id}>
        {runtime.available ? <ModelFields runtime={runtime} profile={profiles[runtime.id] || empty} onChange={patch => onChange({ ...profiles, [runtime.id]: { ...(profiles[runtime.id] || empty), ...patch } })} disabled={disabled} /> : <p>{runtime.setup || "Install this CLI to enable it."}</p>}
      </fieldset>)}
      {!inline && <p>Team changes apply when you send your next message. Existing workers keep their settings.</p>}
    </div>;
  if (inline) return panel;
  return <details ref={details} name="dan-agent-controls" className="wb-native-settings" onToggle={(event) => { if (event.currentTarget.open) void refresh(); }} onKeyDown={(event) => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
    <summary title="Choose agents the lead can delegate to">Team{Object.values(profiles).some((profile) => profile.enabled) && <span className="wb-role-label">{Object.values(profiles).filter((profile) => profile.enabled).length}</span>}<ChevronDown size={12} /></summary>
    {panel}
  </details>;
}
