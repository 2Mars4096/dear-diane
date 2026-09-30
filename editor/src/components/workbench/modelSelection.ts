export type ModelSourceId = "native" | "openrouter" | "openai" | "deepseek" | "moonshot";
export type ModelChoice = { model: string; effort: string; fast: boolean };
export type WorkerProfile = ModelChoice & { enabled: boolean; account: string; provider?: ModelSourceId; base_url?: string; last_api_provider?: ModelSourceId; selections?: Partial<Record<ModelSourceId, ModelChoice>> };
export type WorkerProfiles = Record<string, WorkerProfile>;
export type ModelSource = { id: ModelSourceId; label: string; supported: boolean; configured?: boolean; models?: string[]; model_labels?: Record<string, string>; efforts?: string[]; fast?: boolean; model_efforts?: Record<string, string[]>; key_env?: string; reason?: string };
export type Runtime = { id: string; label: string; available: boolean; accounts: { id: string; label: string }[]; models: string[]; model_efforts?: Record<string, string[]>; model_labels?: Record<string, string>; efforts: string[]; fast: boolean; setup: string; version: string; sources?: ModelSource[] };
export const EMPTY_PROFILE: WorkerProfile = { enabled: false, account: "default", model: "", effort: "", fast: false };
export const OPENROUTER_URL = "https://openrouter.ai/api/v1";

export const API_URLS: Record<Exclude<ModelSourceId, "native">, string> = { openrouter: OPENROUTER_URL, openai: "https://api.openai.com/v1", deepseek: "https://api.deepseek.com", moonshot: "https://api.moonshot.ai/v1" };
export const API_DEFAULTS = { openrouter: "deepseek/deepseek-v4.1-flash", openai: "gpt-6-astra", deepseek: "deepseek-flash", moonshot: "kimi-k3" };

export function modelSource(profile: WorkerProfile): ModelSourceId {
  return profile.provider ?? (Object.entries(API_URLS).find(([, url]) => profile.base_url?.replace(/\/$/, "") === url)?.[0] as ModelSourceId | undefined) ?? "native";
}

export function switchModelSource(profile: WorkerProfile, provider: ModelSourceId): WorkerProfile {
  const selections = { ...profile.selections, [modelSource(profile)]: { model: profile.model, effort: profile.effort, fast: profile.fast } };
  const next = selections[provider] ?? { model: provider === "native" ? "" : API_DEFAULTS[provider], effort: "", fast: false };
  return { ...profile, ...next, provider, base_url: provider === "native" ? undefined : API_URLS[provider], last_api_provider: provider === "native" ? profile.last_api_provider ?? (modelSource(profile) !== "native" ? modelSource(profile) : undefined) : provider, selections };
}

export function reasoningOptions(runtime: Runtime, profile: WorkerProfile): string[] {
  if (modelSource(profile) !== "native") {
    if (modelSource(profile) === "openrouter" && runtime.id === "claude" && !profile.model.startsWith("anthropic/")) return [];
    const source = runtime.sources?.find(source => source.id === modelSource(profile));
    return source?.model_efforts?.[profile.model] ?? source?.efforts ?? [];
  }
  return runtime.model_efforts?.[profile.model] ?? runtime.efforts;
}

export function supportsFast(runtime: Runtime, profile: WorkerProfile): boolean {
  return modelSource(profile) === "native" && runtime.fast && (runtime.id !== "claude" || ["opus", "claude-opus-5", "claude-opus-4-8", "claude-fable-5-1"].includes(profile.model));
}

export function withModel(runtime: Runtime, profile: WorkerProfile, model: string): WorkerProfile {
  const next = { ...profile, model };
  return { ...next, effort: reasoningOptions(runtime, next).includes(next.effort) ? next.effort : "", fast: supportsFast(runtime, next) && next.fast };
}

// One contract for conversation and reader/sidecar execution. No credentials belong here.
export function leadExecutionProfile(agentId: string, profiles: WorkerProfiles): WorkerProfile {
  return profiles[agentId === "native" ? "dan" : agentId] ?? EMPTY_PROFILE;
}

export function withLeadSelection<T extends { profile_policy: Record<string, unknown>; metadata: Record<string, unknown> }>(payload: T, agentId: string, profiles: WorkerProfiles): T {
  const profile = leadExecutionProfile(agentId, profiles);
  return { ...payload, profile_policy: { ...payload.profile_policy, lead_profile: profile }, metadata: { ...payload.metadata,
    selected_model_provider: modelSource(profile), selected_model: profile.model,
    selected_reasoning_effort: profile.effort, selected_fast: profile.fast } };
}
