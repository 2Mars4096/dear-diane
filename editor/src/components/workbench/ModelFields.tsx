import { useState } from "react";
import { modelSource, reasoningOptions, supportsFast, switchModelSource, withModel, type ModelSourceId, type Runtime, type WorkerProfile } from "./modelSelection";

export function ModelFields({ runtime, profile, onChange, disabled = false }: {
  runtime: Runtime; profile: WorkerProfile; onChange: (profile: WorkerProfile) => void; disabled?: boolean;
}) {
  const [custom, setCustom] = useState(false);
  const sourceId = modelSource(profile);
  const sources = runtime.sources ?? [{ id: "native" as const, label: runtime.id === "dan" ? "Default configuration" : "Native / CLI configuration", supported: true }];
  const apiSources = sources.filter(item => item.id !== "native");
  const source = sources.find(item => item.id === sourceId);
  const models = sourceId === "native" ? runtime.models : source?.models ?? [];
  const labels = sourceId === "native" ? runtime.model_labels : source?.model_labels;
  const efforts = reasoningOptions(runtime, profile);
  const editModel = custom || !models.length || Boolean(profile.model && !models.includes(profile.model));
  return <>
    <label>Model source<select aria-label="Model source" disabled={disabled} value={sourceId === "native" ? "native" : "api"} onChange={event => {
      setCustom(false);
      const provider = event.target.value === "native" ? "native" : apiSources.find(item => item.id === profile.last_api_provider && item.supported)?.id ?? apiSources.find(item => item.supported)?.id;
      if (!provider) return;
      const next = switchModelSource(profile, provider);
      const available = sources.find(item => item.id === provider)?.models;
      if (provider !== "native" && !profile.selections?.[provider] && available?.length) next.model = available[0];
      onChange(withModel(runtime, next, next.model));
    }}><option value="native">{runtime.id === "dan" ? "Default configuration" : "Native"}</option><option value="api" disabled={!apiSources.some(item => item.supported)}>API</option></select></label>
    {sourceId !== "native" && <label>API provider<select aria-label="API provider" disabled={disabled} value={sourceId} onChange={event => {
      setCustom(false);
      const provider = event.target.value as ModelSourceId;
      const next = switchModelSource(profile, provider);
      const available = sources.find(item => item.id === provider)?.models;
      if (!profile.selections?.[provider] && available?.length) next.model = available[0];
      onChange(withModel(runtime, next, next.model));
    }}>{apiSources.map(item => <option key={item.id} value={item.id} disabled={!item.supported}>{item.label}{!item.supported ? " · unavailable" : ""}</option>)}</select></label>}
    {sourceId === "native" && runtime.id !== "dan" && <label>Account<select disabled={disabled} value={profile.account} onChange={event => onChange({ ...profile, account: event.target.value })}>{runtime.accounts.map(account => <option key={account.id} value={account.id}>{account.label}</option>)}</select></label>}
    <label>Model{editModel ? <input aria-label="Model" disabled={disabled} value={profile.model} placeholder={sourceId === "openrouter" ? "provider/model-id" : "Default or model ID"} onChange={event => onChange(withModel(runtime, profile, event.target.value))} /> :
      <select aria-label="Model" disabled={disabled} value={profile.model} onChange={event => {
        if (event.target.value === "__custom__") { setCustom(true); return; }
        onChange(withModel(runtime, profile, event.target.value));
      }}>{sourceId === "native" && <option value="">Default</option>}{models.map(model => <option key={model} value={model}>{labels?.[model] ?? model}</option>)}<option value="__custom__">Custom model…</option></select>}</label>
    {editModel && models.length > 0 && <button type="button" disabled={disabled} onClick={() => { setCustom(false); onChange(withModel(runtime, profile, sourceId === "native" ? "" : models[0])); }}>Choose a listed model</button>}
    <label>Reasoning<select aria-label="Reasoning" disabled={disabled || !efforts.length} value={profile.effort} onChange={event => onChange({ ...profile, effort: event.target.value })}><option value="">{efforts.length ? "Default" : "Managed by agent / model"}</option>{efforts.map(effort => <option key={effort} value={effort}>{effort[0].toUpperCase() + effort.slice(1)}</option>)}</select></label>
    <label className="wb-native-enabled"><input type="checkbox" disabled={disabled || !supportsFast(runtime, profile)} checked={profile.fast && supportsFast(runtime, profile)} onChange={event => onChange({ ...profile, fast: event.target.checked })} />Fast mode</label>
    {sourceId !== "native" && <p>{source?.configured === false ? `Add a ${source?.label ?? sourceId} key in Settings → Agents & skills → API providers.` : `Uses your ${source?.label ?? sourceId} API key and billing.`}</p>}
    {sourceId !== "native" && runtime.id === "dan" && sourceId === "openai" && <p>Default supports GPT-4.1 models here. Choose Codex for GPT-6 tool calling.</p>}
    {sourceId === "openrouter" && runtime.id === "claude" && !profile.model.startsWith("anthropic/") && <p>Experimental with Claude Code. This model controls its own reasoning; Anthropic models have the strongest compatibility.</p>}
    {sourceId !== "native" && apiSources.some(item => !item.supported) && <p>{apiSources.find(item => !item.supported)?.reason}</p>}
    {sourceId === "native" && !apiSources.some(item => item.supported) && apiSources.length > 0 && <p>{apiSources.find(item => !item.supported)?.reason}</p>}
  </>;
}
