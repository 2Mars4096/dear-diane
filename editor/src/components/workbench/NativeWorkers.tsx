import { useEffect, useState } from "react";
import { useDismissDetails } from "./useDismissDetails";
import { ChevronDown } from "lucide-react";

export type WorkerProfile = { enabled: boolean; account: string; model: string; effort: string; fast: boolean; base_url?: string };
export type WorkerProfiles = Record<string, WorkerProfile>;
export type Runtime = { id: string; label: string; available: boolean; accounts: { id: string; label: string }[]; models: string[]; efforts: string[]; fast: boolean; setup: string; version: string };
const empty: WorkerProfile = { enabled: false, account: "default", model: "", effort: "", fast: false };
export function loadWorkerProfiles(key = "dan.nativeWorkerProfiles.v1"): WorkerProfiles {
  try {
    const stored = JSON.parse(localStorage.getItem(key) || "{}");
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
    return Object.fromEntries(["dan", "codex", "claude", "antigravity", "cursor"].filter((id) => stored[id] && typeof stored[id] === "object").map((id) => [id, { ...empty, ...stored[id] }]));
  } catch { return {}; }
}
export function NativeWorkerSettings({ profiles, onChange, lead, inline = false, catalog }: { profiles: WorkerProfiles; onChange: (profiles: WorkerProfiles) => void; lead?: string; inline?: boolean; catalog?: Runtime[] }) {
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
            <label>Account<select value={profile.account} onChange={(event) => update({ account: event.target.value })}>{runtime.accounts.map((account) => <option key={account.id} value={account.id}>{account.label}</option>)}</select></label>
            {runtime.id === "dan" && <label>Provider<select value={profile.base_url || ""} onChange={(event) => update({ base_url: event.target.value })}><option value="">DAN configuration</option><option value="https://openrouter.ai/api/v1">OpenRouter</option></select></label>}
            <label>Model{runtime.models.length ? <select value={profile.model} onChange={(event) => update({ model: event.target.value, fast: runtime.id === "claude" && !["opus", "claude-opus-5", "claude-opus-4-8"].includes(event.target.value) ? false : profile.fast })}><option value="">Default</option>{[...new Set([...runtime.models, ...(profile.model ? [profile.model] : [])])].map((model) => <option key={model} value={model}>{model}</option>)}</select> : <input value={profile.model} placeholder="Default or model ID" onChange={(event) => update({ model: event.target.value })} />}</label>
            <label>Reasoning<select disabled={!runtime.efforts.length} value={profile.effort} onChange={(event) => update({ effort: event.target.value })}><option value="">CLI default</option>{runtime.efforts.map((effort) => <option key={effort}>{effort}</option>)}</select></label>
            <label className="wb-native-enabled"><input type="checkbox" checked={profile.fast} disabled={!runtime.fast || (runtime.id === "claude" && !["opus", "claude-opus-5", "claude-opus-4-8"].includes(profile.model))} onChange={(event) => update({ fast: event.target.checked })} />Fast mode</label>
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
