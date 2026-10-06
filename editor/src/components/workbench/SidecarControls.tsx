import { useState } from 'react';
import { ChevronDown, WandSparkles } from 'lucide-react';
import { LeadAgentMenu, type LeadAgentId } from './LeadAgentMenu';
import { NativeWorkerSettings, loadWorkerProfiles, type WorkerProfiles } from './NativeWorkers';
import { withLeadSelection, type WorkerProfile } from './modelSelection';
import type { executeChatV2AgentRun } from '../../lib/chatV2Api';

type Execution = Parameters<typeof executeChatV2AgentRun>[1];
const backends = { native: 'super_dan', codex: 'native_codex', claude: 'claude', cursor: 'cursor', antigravity: 'antigravity' };
export function useSidecarControls(initial: Execution = {}) {
  const [base] = useState(initial);
  const [changed, setChanged] = useState(false);
  const [agent, setAgent] = useState<LeadAgentId>(() => (Object.keys(backends) as LeadAgentId[]).find(id => backends[id] === initial.backend) || 'native');
  const [mode, setMode] = useState(String(initial.profile_policy?.permission_mode || 'auto'));
  const [profiles, setProfiles] = useState<WorkerProfiles>(() => ({ ...loadWorkerProfiles('dan.leadProfiles.v1'), ...(initial.profile_policy?.lead_profile ? { [agent === 'native' ? 'dan' : agent]: initial.profile_policy.lead_profile as WorkerProfile } : {}) }));
  const [team, setTeam] = useState<WorkerProfiles>(() => initial.profile_policy?.native_workers as WorkerProfiles || loadWorkerProfiles());
  const autonomy = mode === 'plan' ? 'review' : 'auto';
  const profilePolicy = { ...base.profile_policy };
  if (base.backend !== backends[agent]) {
    for (const key of ['model', 'base_url', 'codex_model', 'codex_reasoning_effort']) delete profilePolicy[key];
    profilePolicy.auto_backend_continuation = agent === 'native';
  }
  const execution: Execution = changed ? withLeadSelection({
    ...base, backend: backends[agent],
    profile_policy: { ...profilePolicy, backend: backends[agent], permission_mode: mode, autonomy_mode: autonomy, attention_resolution_mode: autonomy, native_workers: team },
    approval_policy: { ...base.approval_policy, mode: mode === 'plan' ? 'ask_on_attention' : 'auto_within_workspace', attention_resolution: autonomy },
    metadata: { ...base.metadata, backend: backends[agent], selected_backend: backends[agent], selected_agent: agent, permission_mode: mode, autonomy_mode: autonomy, attention_resolution_mode: autonomy },
  }, agent, profiles) : base;
  return { execution, label: agent === 'native' ? 'Diane' : agent === 'codex' ? 'Codex' : agent === 'claude' ? 'Claude' : agent,
    render: (disabled: boolean) => <>
      <label className="wb-sidecar-mode"><WandSparkles size={12}/><select aria-label="Mode" disabled={disabled} value={mode} onChange={event => { setMode(event.target.value); setChanged(true); }}><option value="plan">Plan</option><option value="auto">Auto</option><option value="full">Full access</option></select><ChevronDown size={12}/></label>
      <LeadAgentMenu selected={agent} onChange={value => { setAgent(value); setChanged(true); }} disabled={disabled} profiles={profiles} onProfilesChange={value => { setProfiles(value); setChanged(true); }}/>
      <NativeWorkerSettings profiles={team} onChange={value => { setTeam(value); setChanged(true); }}/>
    </> };
}
