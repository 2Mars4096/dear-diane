import { useEffect, useState } from "react";
import { useDismissDetails } from "./useDismissDetails";
import { ChevronDown } from "lucide-react";

import { NativeWorkerSettings, type WorkerProfiles, type Runtime } from "./NativeWorkers";

const choices = [{ id: "native", label: "Diane" }, { id: "codex", label: "Codex" }, { id: "claude", label: "Claude Code" }, { id: "antigravity", label: "Antigravity" }, { id: "cursor", label: "Cursor" }] as const;
export type LeadAgentId = typeof choices[number]["id"];
export function LeadAgentMenu({ selected, onChange, disabled = false, profiles, onProfilesChange }: {
  selected: LeadAgentId; onChange: (id: LeadAgentId) => void; disabled?: boolean;
  profiles: WorkerProfiles; onProfilesChange: (profiles: WorkerProfiles) => void;
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
      if (!Array.isArray(data.runtimes)) throw new Error("Restart the Dear Diane server to load agent settings.");
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
      <h3>Lead agent</h3><p>Choose the agent that runs the conversation, then the model that powers it.</p>
      <label>Agent<select aria-label="Lead agent" value={selected} disabled={disabled} onChange={(event) => onChange(event.target.value as LeadAgentId)}>{choices.map((choice) => <option key={choice.id} value={choice.id} disabled={!available.includes(choice.id)}>{choice.label}{!available.includes(choice.id) ? " · CLI unavailable" : ""}</option>)}</select></label>
      <NativeWorkerSettings catalog={catalog} inline lead={selected === "native" ? "dan" : selected} profiles={profiles} onChange={onProfilesChange} disabled={disabled} />
      {disabled && <p>Change the lead after the current run finishes.</p>}
      {error && <p role="alert">{error}</p>}
      <p className="wb-lead-note">Changes apply to the next run.</p>
    </div>
  </details>;
}
