import { useState } from "react";
import { modelSource, reasoningOptions, supportsFast, switchModelSource, withModel, type Runtime, type WorkerProfile } from "./modelSelection";

export function ModelFields({ runtime, profile, onChange, disabled = false }: {
  runtime: Runtime; profile: WorkerProfile; onChange: (profile: WorkerProfile) => void; disabled?: boolean;
}) {
  const [custom, setCustom] = useState(false);
  const sourceId = modelSource(profile);
  const sources = runtime.sources ?? [{ id: "native" as const, label: runtime.id === "dan" ? "DAN configuration" : "Native / CLI configuration", supported: true }];
  const source = sources.find(item => item.id === sourceId);
  const models = sourceId === "native" ? runtime.models : source?.models ?? [];
  const labels = sourceId === "native" ? runtime.model_labels : source?.model_labels;
  const efforts = reasoningOptions(runtime, profile);
  const editModel = custom || !models.length || Boolean(profile.model && !models.includes(profile.model));
  return <>
    <label>Model source<select aria-label="Model source" disabled={disabled} value={sourceId} onChange={event => {
      setCustom(false);
      const next = switchModelSource(profile, event.target.value as "native" | "openrouter");
      onChange(withModel(runtime, next, next.model));
    }}>{sources.map(item => <option key={item.id} value={item.id} disabled={!item.supported}>{item.label}{!item.supported ? " · unavailable" : ""}</option>)}</select></label>
    {sourceId === "native" && runtime.id !== "dan" && <label>Account<select disabled={disabled} value={profile.account} onChange={event => onChange({ ...profile, account: event.target.value })}>{runtime.accounts.map(account => <option key={account.id} value={account.id}>{account.label}</option>)}</select></label>}
    <label>Model{editModel ? <input aria-label="Model" disabled={disabled} value={profile.model} placeholder={sourceId === "openrouter" ? "provider/model-id" : "Default or model ID"} onChange={event => onChange(withModel(runtime, profile, event.target.value))} /> :
      <select aria-label="Model" disabled={disabled} value={profile.model} onChange={event => {
        if (event.target.value === "__custom__") { setCustom(true); return; }
        onChange(withModel(runtime, profile, event.target.value));
      }}>{sourceId === "native" && <option value="">Default</option>}{models.map(model => <option key={model} value={model}>{labels?.[model] ?? model}</option>)}<option value="__custom__">Custom model…</option></select>}</label>
    {editModel && models.length > 0 && <button type="button" disabled={disabled} onClick={() => { setCustom(false); onChange(withModel(runtime, profile, sourceId === "native" ? "" : models[0])); }}>Choose a listed model</button>}
    <label>Reasoning<select aria-label="Reasoning" disabled={disabled || !efforts.length} value={profile.effort} onChange={event => onChange({ ...profile, effort: event.target.value })}><option value="">{efforts.length ? "Default" : "Managed by agent / model"}</option>{efforts.map(effort => <option key={effort} value={effort}>{effort[0].toUpperCase() + effort.slice(1)}</option>)}</select></label>
    <label className="wb-native-enabled"><input type="checkbox" disabled={disabled || !supportsFast(runtime, profile)} checked={profile.fast && supportsFast(runtime, profile)} onChange={event => onChange({ ...profile, fast: event.target.checked })} />Fast mode</label>
    {sourceId === "openrouter" && <p>{source?.configured === false ? "Set OPENROUTER_API_KEY on the DAN server to run this model." : "Uses the server’s OpenRouter credentials and billing."}</p>}
    {sourceId === "openrouter" && runtime.id === "claude" && !profile.model.startsWith("anthropic/") && <p>Experimental with Claude Code. This model controls its own reasoning; Anthropic models have the strongest compatibility.</p>}
    {sourceId === "native" && sources.some(item => !item.supported) && <p>{sources.find(item => !item.supported)?.reason}</p>}
  </>;
}
