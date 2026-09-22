import { useEffect, useState } from "react";
import { useDismissDetails } from "./useDismissDetails";
import { ChevronDown } from "lucide-react";
import { ModelFields } from "./ModelFields";
import { EMPTY_PROFILE as empty, modelSource, type WorkerProfile, type WorkerProfiles, type Runtime } from "./modelSelection";
export type { WorkerProfile, WorkerProfiles, Runtime } from "./modelSelection";

// Older profiles saved Claude aliases; the menu now lists full IDs only.
const CLAUDE_ALIASES: Record<string, string> = { opus: "claude-opus-5", sonnet: "claude-sonnet-5", haiku: "claude-haiku-4-5-20251001", fable: "claude-fable-5-1" };
export function loadWorkerProfiles(key = "dan.nativeWorkerProfiles.v1"): WorkerProfiles {
  try {
    const stored = JSON.parse(localStorage.getItem(key) || "{}");
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
    return Object.fromEntries(["dan", "codex", "claude", "antigravity", "cursor"].filter((id) => stored[id] && typeof stored[id] === "object").map((id) => [id, { ...empty, ...stored[id], model: id === "claude" && modelSource(stored[id]) === "native" ? CLAUDE_ALIASES[stored[id].model] ?? stored[id].model : stored[id].model }]));
  } catch { return {}; }
}
export function NativeWorkerSettings({ profiles, onChange, lead, inline = false, catalog, disabled = false }: { profiles: WorkerProfiles; onChange: (profiles: WorkerProfiles) => void; lead?: string; inline?: boolean; catalog?: Runtime[]; disabled?: boolean }) {
  const details = useDismissDetails();
  const [viewedRuntime, setViewedRuntime] = useState("codex");
  const [runtimes, setRuntimes] = useState<Runtime[]>([]);
  const [error, setError] = useState("");
  async function refresh() {
    try {
      const response = await fetch("/api/native-workers/catalog");
      if (!response.ok) throw new Error("Unable to load worker accounts. Check the DAN server.");
      const data = await response.json();
      if (!Array.isArray(data.runtimes)) throw new Error("Restart the DAN server to load worker settings.");
      setRuntimes(data.runtimes); setError("");
    } catch (error) { setError(String(error)); }
  }
  useEffect(() => { if (inline && !catalog) void refresh(); }, [inline, catalog]);
  const panel = <div className={inline ? "wb-agent-fields" : "wb-native-settings-panel"}>
      {!inline && <><h3>Team</h3><p>Choose an agent and configure its role in the team.</p></>}
      {!lead && <label>Agent<select value={viewedRuntime} onChange={(event) => setViewedRuntime(event.target.value)}>{runtimes.map((runtime) => <option key={runtime.id} value={runtime.id}>{runtime.label}{profiles[runtime.id]?.enabled ? " · included" : ""}</option>)}</select></label>}
      {error && <p role="alert">{error}</p>}
      {(catalog ?? runtimes).filter((runtime) => runtime.id === (lead || viewedRuntime)).map((runtime) => {
        const profile = profiles[runtime.id] || empty;
        const update = (patch: Partial<WorkerProfile>) => onChange({ ...profiles, [runtime.id]: { ...profile, ...patch } });
        return <fieldset key={runtime.id}>
          {!inline && <legend>{runtime.label}</legend>}
          {!lead && <label className="wb-native-enabled"><input type="checkbox" checked={profile.enabled} disabled={!runtime.available} onChange={(event) => update({ enabled: event.target.checked })} />Include in team</label>}
          {!runtime.available ? <p>{runtime.setup || "Install this CLI to enable it."}</p> : <>
            <ModelFields runtime={runtime} profile={profile} onChange={update} disabled={disabled} />
          </>}
        </fieldset>;
      })}
      {!inline && <p>Settings apply to the next run.</p>}
    </div>;
  if (inline) return panel;
  return <details ref={details} name="dan-agent-controls" className="wb-native-settings" onToggle={(event) => { if (event.currentTarget.open) void refresh(); }} onKeyDown={(event) => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
    <summary title="Choose agents the lead can delegate to">Team{Object.values(profiles).some((profile) => profile.enabled) && <span className="wb-role-label">{Object.values(profiles).filter((profile) => profile.enabled).length}</span>}<ChevronDown size={12} /></summary>
    {panel}
  </details>;
}
