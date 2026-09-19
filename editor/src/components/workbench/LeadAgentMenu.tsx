import { useEffect, useState } from "react";
import { useDismissDetails } from "./useDismissDetails";
import { ChevronDown } from "lucide-react";

import { NativeWorkerSettings, type WorkerProfiles, type Runtime } from "./NativeWorkers";

const choices = [{ id: "native", label: "DAN" }, { id: "codex", label: "Codex" }, { id: "claude", label: "Claude Code" }, { id: "antigravity", label: "Antigravity" }, { id: "cursor", label: "Cursor" }] as const;
export type LeadAgentId = typeof choices[number]["id"];
export function LeadAgentMenu({ selected, onChange, disabled = false, profiles, onProfilesChange, modelId, modelOptions, onModelChange }: {
  selected: LeadAgentId; onChange: (id: LeadAgentId) => void; disabled?: boolean;
  profiles: WorkerProfiles; onProfilesChange: (profiles: WorkerProfiles) => void;
  modelId: string; modelOptions: { id: string; label: string }[]; onModelChange: (id: string) => void;
}) {
  const details = useDismissDetails();
  const [catalog, setCatalog] = useState<Runtime[]>([]);
  const [available, setAvailable] = useState<string[]>(["native"]);
  const [error, setError] = useState("");
  async function refresh() {
    try {
      const response = await fetch("/api/native-workers/catalog");
      if (!response.ok) throw new Error("Agent availability could not be checked.");
      const data = await response.json();
      setCatalog(data.runtimes);
      setAvailable(["native", ...data.runtimes.filter((item: { available: boolean }) => item.available).map((item: { id: string }) => item.id)]);
      setError("");
    } catch (error) { setError(String(error)); }
  }
  useEffect(() => { void refresh(); }, []);
  return <details name="dan-agent-controls" ref={details} className="wb-native-settings wb-lead-menu" onToggle={(event) => { if (event.currentTarget.open) void refresh(); }} onKeyDown={(event) => {
    if (event.key === "Escape") { event.preventDefault(); details.current!.open = false; details.current?.querySelector("summary")?.focus(); }
  }}>
    <summary aria-label={`Lead agent: ${choices.find((item) => item.id === selected)?.label}`}>Lead <span className="wb-role-label">{choices.find((item) => item.id === selected)?.label}</span> <ChevronDown size={12} /></summary>
    <div className="wb-native-settings-panel">
      <h3>Lead agent</h3><p>Runs this conversation and coordinates the team.</p>
      <label>Agent<select aria-label="Lead agent" value={selected} disabled={disabled} onChange={(event) => onChange(event.target.value as LeadAgentId)}>{choices.map((choice) => <option key={choice.id} value={choice.id} disabled={!available.includes(choice.id)}>{choice.label}{!available.includes(choice.id) ? " · CLI unavailable" : ""}</option>)}</select></label>
      {selected === "native" ? <div className="wb-agent-fields">
        <label>Model<select aria-label="Lead model" value={modelId} onChange={(event) => onModelChange(event.target.value)}>{modelOptions.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}</select></label>
        <label>Reasoning<select disabled><option>Managed by DAN</option></select></label>
        <label className="wb-native-enabled"><input type="checkbox" disabled checked={false} readOnly />Fast mode</label>
        <p>DAN manages reasoning internally. Fast mode is available on supported native models.</p>
      </div> : <NativeWorkerSettings catalog={catalog} inline lead={selected} profiles={profiles} onChange={onProfilesChange} />}
      {disabled && <p>Change the lead after the current run finishes.</p>}
      {error && <p role="alert">{error}</p>}
      <p className="wb-lead-note">Changes apply to the next run. Native agents use their CLI login.</p>
    </div>
  </details>;
}
