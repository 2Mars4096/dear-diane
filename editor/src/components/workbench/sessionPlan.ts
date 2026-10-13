import { useCallback, useEffect, useRef, useState } from 'react';
import { requestJson } from '../../lib/http';

export type PlanDocument = { title: string; outcome: string; scope: string; specifications: string; steps: string; assumptions: string; blockers: string };
export type PlanQuestion = { question: string; reason: string; recommended: string; alternatives: string[]; selection_mode?: 'single' | 'multiple' };
export type PlanState = {
  selected_skills?: string[];
  last_run_id?: string;
  version: number; revision: number; mode: 'plan' | 'work'; document: PlanDocument | null;
  history: { revision: number; document: PlanDocument; summary: string }[];
  questions?: PlanQuestion[];
  question: PlanQuestion | null; questions_asked: number; summary: string;
  proposal: { document: PlanDocument; summary: string; question: PlanQuestion | null } | null;
  execution: { revision: number; run_id: string; document: PlanDocument; status?: string; progress?: string } | null;
};
export type PlanThread = { workflowId: string; id: string };
export type PlanReview = { action: 'refine' | 'start'; workflow_id: string; revision: number };
export const emptyPlan = (): PlanState => ({ version: 0, revision: 0, mode: 'work', document: null, history: [], question: null, questions_asked: 0, summary: '', proposal: null, execution: null });
const path = (thread: PlanThread) => `/api/chats/${encodeURIComponent(thread.workflowId)}/${encodeURIComponent(thread.id)}/plan`;
export const getSessionPlan = (thread: PlanThread) => requestJson<PlanState>(path(thread));
export const saveSessionPlan = (thread: PlanThread, update: { version: number; action: 'mode' | 'edit' | 'accept_proposal' | 'dismiss_proposal'; mode?: 'plan' | 'work'; document?: PlanDocument }) =>
  requestJson<PlanState>(path(thread), { method: 'PUT', body: JSON.stringify(update) });

export function useSessionPlan(thread: PlanThread | null) {
  const workflowId = thread?.workflowId || '';
  const threadId = thread?.id || '';
  const key = threadId ? `${workflowId}:${threadId}` : '';
  const [result, setResult] = useState({ key: '', state: emptyPlan(), loaded: false });
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const selected = useRef(key); selected.current = key;
  const busy = useRef(false);
  const state = result.key === key ? result.state : emptyPlan();
  const loaded = result.key === key && result.loaded;
  const reload = useCallback(async () => {
    if (!threadId) return;
    const next = await getSessionPlan({ workflowId, id: threadId });
    if (selected.current === key) setResult(previous => previous.key === key && previous.state.version > next.version ? previous : { key, state: next, loaded: true });
    return next;
  }, [key, threadId, workflowId]); // session identity, not title
  useEffect(() => {
    let disposed = false;
    setError('');
    if (!key) return;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { if (!busy.current) { await reload(); if (!disposed) setError(''); } }
      catch (reason) { if (!disposed) setError(reason instanceof Error ? reason.message : 'Could not load plan'); }
      if (!disposed) timer = setTimeout(poll, 2500);
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [key, reload]);
  const update = async (change: Omit<Parameters<typeof saveSessionPlan>[1], 'version'>) => {
    if (!thread || busy.current || !loaded) throw new Error('Wait for the plan to finish loading or saving.');
    busy.current = true; setSaving(true); setError('');
    try {
      const next = await saveSessionPlan(thread, { ...change, version: state.version });
      if (selected.current === key) setResult(previous => previous.key === key && previous.state.version > next.version ? previous : { key, state: next, loaded: true });
      return next;
    } catch (reason) {
      if (selected.current === key) setError(reason instanceof Error ? reason.message : 'Could not save plan');
      throw reason;
    } finally { busy.current = false; setSaving(false); }
  };
  return { state, loaded, error, saving, reload, update };
}
